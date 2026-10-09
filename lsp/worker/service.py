"""Compiler and semantic-query state owned by the worker process."""

from __future__ import annotations

import logging
import copy
import hashlib
import json
import time
from collections import OrderedDict
from pathlib import Path
from typing import TypeVar, cast

from lsprotocol import types
from lsprotocol.converters import get_converter

from compiler.analysis.documents import Document, DocumentStore
from anx.project import LoadResult
from compiler.analysis.queries.completion import complete, signature_help as signature_info
from compiler.analysis.queries.context import QueryContext
from compiler.analysis.queries.navigation import Navigator
from compiler.analysis.queries.refactor import (
    ReferenceResult,
    find_references,
    rename as rename_symbol,
)
from compiler.frontend.lex.position import SrcPosition, SrcSpan
from compiler.interop.positions import to_compiler_column, uri_to_path
from lsp.completion import completion_list, signature_help
from lsp.diagnostics import diagnostics_by_document
from lsp.formatting import document_edits
from lsp.navigation import document_symbols, hover, location
from lsp.refactor import (
    code_actions,
    document_highlights,
    import_removals,
    locations,
    to_range,
    workspace_edit,
)
from lsp.semantic_tokens import classify, encode
from lsp.workspace import AnalysisPayload, Snapshot, Workspace
from lsp.ipc.types import JsonObject, JsonValue, require_object, require_string

__all__ = ["OperationFailure", "WorkerService"]

_LOGGER = logging.getLogger(__name__)
_CONVERTER = get_converter()
_T = TypeVar("_T")
_MAX_SYNTAX_CACHE_ENTRIES = 128
_MAX_SYNTAX_CACHE_BYTES = 8 * 1024 * 1024
_MAX_QUERY_CACHE_ENTRIES = 512
_MAX_QUERY_CACHE_BYTES = 32 * 1024 * 1024
_ProtocolValue = (
    types.Diagnostic
    | types.Location
    | types.Hover
    | types.DocumentSymbol
    | types.SemanticTokens
    | types.CompletionList
    | types.SignatureHelp
    | types.DocumentHighlight
    | types.PrepareRenamePlaceholder
    | types.WorkspaceEdit
    | types.CodeAction
    | types.TextEdit
    | list[types.Diagnostic]
    | list[types.Location]
    | list[types.DocumentSymbol]
    | list[types.DocumentHighlight]
    | list[types.CodeAction]
    | list[types.TextEdit]
    | None
)


class OperationFailure(RuntimeError):
    """A structured failure returned to the protocol process."""

    def __init__(self, code: int, message: str) -> None:
        super().__init__(message)
        self.code = code


class WorkerService:
    """Own one workspace, its current analysis snapshot, and semantic indexes."""

    def __init__(self, *, epoch: int) -> None:
        self.__epoch = epoch
        self.__workspace: Workspace | None = None
        self.__compiler_root: Path | None = None
        self.__raw_pointers = False
        self.__workspace_root: Path | None = None
        self.__main_revision = -1
        self.__topology_revision = -1
        self.__open_documents: dict[Path, Document] = {}
        self.__snapshot_revision = -1
        self.__navigator: Navigator | None = None
        self.__navigator_generation = -1
        self.__syntax_cache: OrderedDict[str, tuple[JsonValue, int]] = OrderedDict()
        self.__syntax_cache_bytes = 0
        self.__query_cache: OrderedDict[str, tuple[JsonValue, int]] = OrderedDict()
        self.__query_cache_bytes = 0

    def handle(
        self,
        operation: str,
        payload: JsonObject,
        *,
        revision: int,
        topology_revision: int,
    ) -> JsonObject:
        started = time.perf_counter()
        if operation == "initialize":
            result = self.__initialize(payload, topology_revision)
        elif operation == "sync":
            result = self.__sync(payload, revision, topology_revision)
        elif operation == "analyze":
            result = self.__analyze(payload, revision)
        elif operation == "syntax":
            result = self.__syntax(payload, revision)
        elif operation == "query":
            result = self.__query(payload, revision)
        elif operation == "format":
            result = self.__format(payload)
        elif operation == "release":
            self.__release()
            result = {}
        elif operation == "shutdown":
            self.__release()
            result = {}
        else:
            raise OperationFailure(-32601, f"unknown worker operation: {operation}")
        result["worker_elapsed_ms"] = (time.perf_counter() - started) * 1000
        return result

    def __initialize(
        self, payload: JsonObject, topology_revision: int
    ) -> JsonObject:
        compiler_root_value = payload.get("compiler_root")
        if compiler_root_value is not None and not isinstance(compiler_root_value, str):
            raise OperationFailure(-32602, "compiler_root must be a path or null")
        root_value = payload.get("workspace_root")
        if root_value is not None and not isinstance(root_value, str):
            raise OperationFailure(-32602, "workspace_root must be a path or null")
        raw_pointers = payload.get("raw_pointers")
        if not isinstance(raw_pointers, bool):
            raise OperationFailure(-32602, "raw_pointers must be a boolean")

        self.__compiler_root = None if compiler_root_value is None else Path(compiler_root_value)
        self.__raw_pointers = raw_pointers
        self.__workspace_root = None if root_value is None else Path(root_value).resolve()
        self.__workspace = Workspace(
            compiler_root=self.__compiler_root,
            raw_pointers=self.__raw_pointers,
        )
        self.__topology_revision = topology_revision
        if self.__workspace_root is not None:
            result = self.__workspace.use_directory(self.__workspace_root)
            return self.__project_result(result)
        return {"project_diagnostics": [], "project_loaded": False}

    def __sync(
        self, payload: JsonObject, revision: int, topology_revision: int
    ) -> JsonObject:
        workspace = self.__require_workspace()
        raw_documents = payload.get("documents")
        if not isinstance(raw_documents, list):
            raise OperationFailure(-32602, "documents must be an array")
        new_documents: dict[Path, Document] = {}
        for raw_document in raw_documents:
            item = require_object(raw_document, field="document")
            uri = require_string(item.get("uri"), field="document.uri")
            text = require_string(item.get("text"), field="document.text")
            version_value = item.get("version")
            if version_value is not None and (
                not isinstance(version_value, int) or isinstance(version_value, bool)
            ):
                raise OperationFailure(-32602, "document.version must be an integer or null")
            path = uri_to_path(uri).resolve()
            new_documents[path] = Document(path=path, text=text, version=version_value)

        root_value = payload.get("workspace_root")
        if root_value is not None and not isinstance(root_value, str):
            raise OperationFailure(-32602, "workspace_root must be a path or null")
        workspace_root = None if root_value is None else Path(root_value).resolve()
        topology_changed = topology_revision != self.__topology_revision
        previous_root = self.__workspace_root
        root_changed = workspace_root != previous_root
        project_result = None
        if root_changed or topology_changed:
            self.__workspace_root = workspace_root
            if workspace_root is None:
                if previous_root is not None:
                    workspace.apply_directory(Path.cwd(), None, None)
                else:
                    workspace.invalidate()
            else:
                project_result = workspace.use_directory(workspace_root)
            self.__topology_revision = topology_revision

        changed = False
        revision_changed = revision != self.__main_revision
        for path in self.__open_documents.keys() - new_documents.keys():
            workspace.close(path)
            changed = True
        for path, document in new_documents.items():
            previous = self.__open_documents.get(path)
            if previous != document:
                workspace.open(path, document.text, document.version)
                changed = True
        if revision_changed:
            self.__clear_query_cache()
        if revision_changed and not changed and not root_changed and not topology_changed:
            workspace.invalidate()
            changed = True
        self.__open_documents = new_documents
        self.__main_revision = revision
        self.__snapshot_revision = -1
        self.__navigator = None
        self.__navigator_generation = -1
        if project_result is None and (root_changed or topology_changed) and workspace_root is not None:
            project_result = workspace.use_directory(workspace_root)
        if project_result is not None:
            return self.__project_result(project_result)
        return {
            "project_diagnostics": self.__project_diagnostic_json(),
            "project_loaded": workspace.project is not None,
            "inputs_changed": changed,
        }

    def __analyze(self, payload: JsonObject, revision: int) -> JsonObject:
        full_value = payload.get("full")
        if not isinstance(full_value, bool):
            raise OperationFailure(-32602, "full must be a boolean")
        full = full_value
        workspace = self.__require_workspace()
        if revision != self.__main_revision:
            raise OperationFailure(-32801, "analysis input revision is stale")
        cached = workspace.fresh_snapshot if full else workspace.fresh_syntax_snapshot
        if cached is not None and self.__snapshot_revision == revision:
            return self.__analysis_result(cached, cached=True)

        freeze_started = time.perf_counter()
        files, documents, session = workspace.capture()
        overlay = DocumentStore(documents)
        frozen = DocumentStore(
            Document(path=path, text=overlay.text(path), version=overlay.version(path))
            for path in files
        )
        freeze_elapsed_ms = (time.perf_counter() - freeze_started) * 1000
        analysis_started = time.perf_counter()
        try:
            result = session.analyze(files, documents=frozen, syntax_only=not full, recover=True)
        except Exception as error:
            _LOGGER.exception("analysis failed for revision %d", revision)
            raise OperationFailure(-32603, f"analysis failed: {error}") from error
        analysis_elapsed_ms = (time.perf_counter() - analysis_started) * 1000
        queries = QueryContext(result)
        index_started = time.perf_counter()
        if full and queries.index is not None:
            _ = queries.index.declarations
        index_elapsed_ms = (time.perf_counter() - index_started) * 1000
        versions = {document.path.resolve(): document.version for document in documents}
        snapshot = workspace.accept(
            workspace.revision,
            files,
            AnalysisPayload(result, queries, versions),
        )
        if snapshot is None:
            raise OperationFailure(-32801, "analysis result became stale")
        self.__snapshot_revision = revision
        self.__navigator = None
        self.__navigator_generation = -1
        serialization_started = time.perf_counter()
        response = self.__analysis_result(snapshot, cached=False)
        serialization_elapsed_ms = (time.perf_counter() - serialization_started) * 1000
        timings: JsonObject = {
            key: value for key, value in result.timings.items()
        }
        timings.update({
            "input_freeze_ms": freeze_elapsed_ms,
            "analysis_total_ms": analysis_elapsed_ms,
            "declaration_index_ms": index_elapsed_ms,
            "diagnostic_serialization_ms": serialization_elapsed_ms,
        })
        response["analysis_elapsed_ms"] = analysis_elapsed_ms
        response["analysis_timings"] = timings
        return response

    def __analysis_result(self, snapshot: Snapshot, *, cached: bool) -> JsonObject:
        texts = {path.resolve(): text for path, text in snapshot.result.sources.items()}
        by_document = diagnostics_by_document(snapshot.result.diagnostics, texts)
        diagnostics: dict[str, JsonValue] = {}
        for path, items in by_document.items():
            diagnostics[path_to_uri(path)] = self.__unstructure(items)
        return {
            "revision": self.__snapshot_revision,
            "full": not snapshot.result.syntax_only,
            "cached": cached,
            "files": [path_to_uri(path) for path in snapshot.files],
            "diagnostics": diagnostics,
            "diagnostic_count": len(snapshot.result.diagnostics),
            "project_diagnostics": self.__project_diagnostic_json(),
        }

    def __project_result(self, result: LoadResult | None) -> JsonObject:
        del result
        workspace = self.__require_workspace()
        return {
            "project_diagnostics": self.__project_diagnostic_json(),
            "project_loaded": workspace.project is not None,
            "project_root": None if workspace.project_root is None else str(workspace.project_root),
        }

    def __project_diagnostic_json(self) -> list[JsonValue]:
        workspace = self.__require_workspace()
        rendered: list[JsonValue] = []
        for diagnostic in workspace.project_diagnostics:
            path = diagnostic.path or (
                workspace.project_root / "package.anx"
                if workspace.project_root is not None else None
            )
            if path is None:
                continue
            position = types.Position(line=0, character=0)
            item = types.Diagnostic(
                range=types.Range(start=position, end=position),
                severity=types.DiagnosticSeverity.Error,
                source="anx",
                code=diagnostic.code,
                message=diagnostic.message,
            )
            rendered.append({
                "uri": path_to_uri(path.resolve()),
                "diagnostic": self.__unstructure(item),
            })
        return rendered

    def __query(self, payload: JsonObject, revision: int) -> JsonObject:
        method = require_string(payload.get("method"), field="method")
        raw_params = require_object(payload.get("params"), field="params")
        snapshot = self.__current_snapshot(revision)
        key = f"{self.__epoch}:{revision}:{method}:{json.dumps(raw_params, sort_keys=True, separators=(',', ':'))}"
        cached = self.__cache_get(self.__query_cache, key)
        if cached is not None:
            return {"value": cached, "cached": True}
        query_started = time.perf_counter()
        value = self.__dispatch_query(method, raw_params, snapshot)
        query_elapsed_ms = (time.perf_counter() - query_started) * 1000
        self.__cache_put(
            self.__query_cache,
            key,
            value,
            _MAX_QUERY_CACHE_ENTRIES,
            _MAX_QUERY_CACHE_BYTES,
            syntax=False,
        )
        return {"value": value, "cached": False, "query_elapsed_ms": query_elapsed_ms}

    def __dispatch_query(
        self, method: str, params: JsonObject, snapshot: Snapshot
    ) -> JsonValue:
        navigator = self.__navigator_for(snapshot)
        result = snapshot.result
        context = snapshot.queries

        if method == types.TEXT_DOCUMENT_DEFINITION:
            request = self.__structure(params, types.DefinitionParams)
            located = self.__located(navigator, request.text_document.uri, request.position)
            if located is None:
                return None
            path, row, column = located
            resolution = navigator.resolve(path, row, column)
            return None if resolution is None or resolution.target is None else self.__unstructure(
                location(resolution.target, navigator)
            )

        if method == types.TEXT_DOCUMENT_HOVER:
            request = self.__structure(params, types.HoverParams)
            located = self.__located(navigator, request.text_document.uri, request.position)
            if located is None:
                return None
            path, row, column = located
            span = self.__span_of(navigator, path, request.position)
            if span is None:
                return None
            resolution = navigator.resolve(path, row, column)
            return None if resolution is None else self.__unstructure(
                hover(resolution, navigator, span)
            )

        if method == types.TEXT_DOCUMENT_DOCUMENT_SYMBOL:
            request = self.__structure(params, types.DocumentSymbolParams)
            path = uri_to_path(request.text_document.uri)
            return self.__unstructure(document_symbols(navigator.declarations_in(path), navigator))

        if method == types.TEXT_DOCUMENT_SEMANTIC_TOKENS_FULL:
            request = self.__structure(params, types.SemanticTokensParams)
            path = uri_to_path(request.text_document.uri)
            return self.__unstructure(types.SemanticTokens(
                data=encode(classify(navigator, path), navigator.text_of(path))
            ))

        if method == types.TEXT_DOCUMENT_COMPLETION:
            request = self.__structure(params, types.CompletionParams)
            located = self.__located(navigator, request.text_document.uri, request.position)
            if located is None:
                return None
            path, row, column = located
            return self.__unstructure(completion_list(
                complete(context, path, row, column, std_root=self.__require_workspace().std_root),
                navigator,
            ))

        if method == types.TEXT_DOCUMENT_SIGNATURE_HELP:
            request = self.__structure(params, types.SignatureHelpParams)
            located = self.__located(navigator, request.text_document.uri, request.position)
            if located is None:
                return None
            path, row, column = located
            info = signature_info(
                context, path, row, column, std_root=self.__require_workspace().std_root
            )
            return None if info is None else self.__unstructure(signature_help(info))

        if method == types.TEXT_DOCUMENT_REFERENCES:
            request = self.__structure(params, types.ReferenceParams)
            located = self.__located(navigator, request.text_document.uri, request.position)
            if located is None:
                return None
            path, row, column = located
            found = find_references(
                context,
                path,
                row,
                column,
                include_declaration=request.context.include_declaration,
                std_root=self.__require_workspace().std_root,
            )
            return None if found is None else self.__unstructure(locations(found, navigator))

        if method == types.TEXT_DOCUMENT_DOCUMENT_HIGHLIGHT:
            request = self.__structure(params, types.DocumentHighlightParams)
            located = self.__located(navigator, request.text_document.uri, request.position)
            if located is None:
                return None
            path, row, column = located
            found = find_references(
                context, path, row, column, std_root=self.__require_workspace().std_root
            )
            if found is None:
                return None
            same_file = ReferenceResult(
                target=found.target,
                sites=tuple(site for site in found.sites if site.span.path.resolve() == path.resolve()),
            )
            return self.__unstructure(document_highlights(same_file, navigator))

        if method == types.TEXT_DOCUMENT_PREPARE_RENAME:
            request = self.__structure(params, types.PrepareRenameParams)
            located = self.__located(navigator, request.text_document.uri, request.position)
            if located is None:
                return None
            path, row, column = located
            found = find_references(
                context, path, row, column, std_root=self.__require_workspace().std_root
            )
            if found is None:
                return None
            return self.__unstructure(types.PrepareRenamePlaceholder(
                range=to_range(found.target.span, navigator), placeholder=found.target.name
            ))

        if method == types.TEXT_DOCUMENT_RENAME:
            request = self.__structure(params, types.RenameParams)
            located = self.__located(navigator, request.text_document.uri, request.position)
            if located is None:
                return None
            path, row, column = located
            outcome = rename_symbol(
                context,
                path,
                row,
                column,
                request.new_name,
                std_root=self.__require_workspace().std_root,
            )
            if not outcome.ok:
                raise OperationFailure(-32803, outcome.refusal or "rename was refused")
            return self.__unstructure(workspace_edit(outcome, navigator))

        if method == types.TEXT_DOCUMENT_CODE_ACTION:
            request = self.__structure(params, types.CodeActionParams)
            path = uri_to_path(request.text_document.uri)
            return self.__unstructure(
                code_actions(import_removals(result, navigator, path), navigator, path)
            )

        raise OperationFailure(-32601, f"unsupported LSP query: {method}")

    def __format(self, payload: JsonObject) -> JsonObject:
        uri = require_string(payload.get("uri"), field="uri")
        path = uri_to_path(uri)
        raw_text = payload.get("text")
        if raw_text is not None and not isinstance(raw_text, str):
            raise OperationFailure(-32602, "text must be a string or null")
        text = (
            raw_text
            if isinstance(raw_text, str)
            else self.__require_workspace().documents.text(path)
        )
        key = f"format:{uri}:{hashlib.sha256(text.encode('utf-8')).hexdigest()}"
        cached = self.__cache_get(self.__query_cache, key)
        if cached is not None:
            return {"value": cached, "cached": True}
        value = self.__unstructure(document_edits(text, path))
        self.__cache_put(
            self.__query_cache,
            key,
            value,
            _MAX_QUERY_CACHE_ENTRIES,
            _MAX_QUERY_CACHE_BYTES,
            syntax=False,
        )
        return {"value": value, "cached": False}

    def __syntax(self, payload: JsonObject, revision: int) -> JsonObject:
        uri = require_string(payload.get("uri"), field="uri")
        text = require_string(payload.get("text"), field="text")
        if revision != self.__main_revision:
            raise OperationFailure(-32801, "syntax input revision is stale")
        path = uri_to_path(uri).resolve()
        key = f"{path}:{hashlib.sha256(text.encode('utf-8')).hexdigest()}"
        cached = self.__cache_get(self.__syntax_cache, key)
        cache_hit = cached is not None
        timings: JsonObject
        if cached is None:
            document = Document(path=path, text=text, version=None)
            overlay = DocumentStore((document,))
            result = self.__require_workspace().syntax_session().analyze(
                [path],
                documents=overlay,
                syntax_only=True,
                recover=True,
            )
            by_document = diagnostics_by_document(result.diagnostics, result.sources)
            items = self.__unstructure(by_document.get(path, []))
            self.__cache_put(
                self.__syntax_cache,
                key,
                items,
                _MAX_SYNTAX_CACHE_ENTRIES,
                _MAX_SYNTAX_CACHE_BYTES,
                syntax=True,
            )
            cached = items
            timings = {}
            for name, value in result.timings.items():
                timings[name] = value
        else:
            timings = {}
        return {
            "revision": revision,
            "full": False,
            "cached": cache_hit,
            "syntax_document": uri,
            "diagnostics": {uri: cached},
            "diagnostic_count": len(cached) if isinstance(cached, list) else 0,
            "analysis_timings": timings,
        }

    def __cache_get(
        self, cache: OrderedDict[str, tuple[JsonValue, int]], key: str
    ) -> JsonValue | None:
        entry = cache.get(key)
        if entry is None:
            return None
        cache.move_to_end(key)
        return copy.deepcopy(entry[0])

    def __cache_put(
        self,
        cache: OrderedDict[str, tuple[JsonValue, int]],
        key: str,
        value: JsonValue,
        max_entries: int,
        max_bytes: int,
        *,
        syntax: bool,
    ) -> None:
        size = len(key.encode("utf-8")) + len(
            json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        )
        if size > max_bytes:
            return
        previous = cache.pop(key, None)
        if previous is not None:
            self.__set_cache_bytes(syntax, self.__cache_bytes(syntax) - previous[1])
        cache[key] = (copy.deepcopy(value), size)
        self.__set_cache_bytes(syntax, self.__cache_bytes(syntax) + size)
        while len(cache) > max_entries or self.__cache_bytes(syntax) > max_bytes:
            _old_key, (_old_value, old_size) = cache.popitem(last=False)
            self.__set_cache_bytes(syntax, self.__cache_bytes(syntax) - old_size)

    def __cache_bytes(self, syntax: bool) -> int:
        return self.__syntax_cache_bytes if syntax else self.__query_cache_bytes

    def __set_cache_bytes(self, syntax: bool, value: int) -> None:
        if syntax:
            self.__syntax_cache_bytes = max(0, value)
        else:
            self.__query_cache_bytes = max(0, value)

    def __clear_query_cache(self) -> None:
        self.__query_cache.clear()
        self.__query_cache_bytes = 0

    def __current_snapshot(self, revision: int) -> Snapshot:
        if revision != self.__main_revision or revision != self.__snapshot_revision:
            raise OperationFailure(-32801, "document changed before query")
        snapshot = self.__require_workspace().fresh_snapshot
        if snapshot is None:
            raise OperationFailure(-32801, "current full analysis is unavailable")
        return snapshot

    def __navigator_for(self, snapshot: Snapshot) -> Navigator:
        if self.__navigator_generation != snapshot.generation or self.__navigator is None:
            self.__navigator = Navigator(
                snapshot.queries,
                std_root=self.__require_workspace().std_root,
            )
            self.__navigator_generation = snapshot.generation
        return self.__navigator

    @staticmethod
    def __located(
        navigator: Navigator,
        uri: str,
        position: types.Position,
    ) -> tuple[Path, int, int] | None:
        path = uri_to_path(uri)
        lines = navigator.text_of(path).splitlines()
        if not 0 <= position.line < len(lines):
            return None
        return path, position.line, to_compiler_column(lines[position.line], position.character)

    def __span_of(
        self, navigator: Navigator, path: Path, position: types.Position
    ) -> SrcSpan | None:
        located = self.__located(navigator, path.as_uri(), position)
        if located is None:
            return None
        _, row, column = located
        start = SrcPosition(row, column, path)
        return SrcSpan(start, SrcPosition(row, column + 1, path))

    def __release(self) -> None:
        self.__navigator = None
        self.__navigator_generation = -1
        self.__snapshot_revision = -1
        self.__workspace = None
        self.__open_documents.clear()

    def __require_workspace(self) -> Workspace:
        if self.__workspace is None:
            raise OperationFailure(-32600, "worker has not been initialized")
        return self.__workspace

    @staticmethod
    def __structure(value: JsonValue, target: type[_T]) -> _T:
        return _CONVERTER.structure(value, target)

    @staticmethod
    def __unstructure(value: _ProtocolValue) -> JsonValue:
        return cast(JsonValue, _CONVERTER.unstructure(value))


def path_to_uri(path: Path) -> str:
    return path.resolve().as_uri()
