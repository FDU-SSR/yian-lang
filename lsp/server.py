"""The pygls protocol process for the YIAN language server."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, TypeAlias, TypeVar, cast
from urllib.parse import unquote, urlparse

from lsprotocol import types
from lsprotocol.converters import get_converter
from pygls.exceptions import JsonRpcException
from pygls.lsp.server import LanguageServer

from lsp.coordinator import AnalysisCoordinator
from lsp.ipc.types import JsonObject, JsonValue
from lsp.supervisor import WorkerOperationError, WorkerSupervisor

__all__ = ["SERVER_NAME", "SERVER_VERSION", "YianLanguageServer", "create_server"]

SERVER_NAME = "yian-lsp"
SERVER_VERSION = "0.7.0"
REQUEST_FAILED = -32803

_LOGGER = logging.getLogger(__name__)
_CONVERTER = get_converter()
_T = TypeVar("_T")
_ProtocolParams: TypeAlias = (
    types.DefinitionParams
    | types.HoverParams
    | types.DocumentSymbolParams
    | types.SemanticTokensParams
    | types.CompletionParams
    | types.SignatureHelpParams
    | types.ReferenceParams
    | types.DocumentHighlightParams
    | types.PrepareRenameParams
    | types.RenameParams
    | types.CodeActionParams
)

if TYPE_CHECKING:
    from pygls.server import ServerErrors


class YianLanguageServer(LanguageServer):
    """Keep protocol state in the parent and compiler state in one child."""

    def __init__(
        self,
        *,
        compiler_root: Path | None = None,
        raw_pointers: bool = False,
        max_workers: int = 4,
        event_sink: Callable[[str, JsonObject], None] | None = None,
    ) -> None:
        super().__init__(  # pyright: ignore[reportUnknownMemberType]
            name=SERVER_NAME,
            version=SERVER_VERSION,
            text_document_sync_kind=types.TextDocumentSyncKind.Full,
            max_workers=max_workers,
        )
        self.worker = WorkerSupervisor(
            compiler_root=compiler_root,
            raw_pointers=raw_pointers,
            event_sink=event_sink,
        )
        self.analysis = AnalysisCoordinator(self.worker, self.__analysis_completed)
        self.__published: set[str] = set()
        self.__project_published: set[str] = set()
        self.__publish_revision = -1
        self.__publish_full = False

    @property
    def revision(self) -> int:
        return self.worker.revision

    def schedule_analysis(self, reason: str, uri: str | None = None) -> None:
        self.analysis.invalidate()
        self.analysis.schedule_edit(reason, uri)

    def analyze_now(self, reason: str) -> None:
        self.analysis.invalidate()
        self.analysis.schedule_full(reason)

    async def start_worker(self) -> None:
        result = await self.worker.start()
        self.__publish_project_diagnostics(result)
        if self.worker.project_root is not None:
            self.analyze_now("startup")

    def cancel_analysis(self) -> None:
        self.analysis.close()

    async def close_worker(self) -> None:
        await self.analysis.aclose()

    async def reload_project(self, reason: str) -> None:
        """Reload disk topology in the worker, then analyze the new inputs."""
        try:
            result = await self.analysis.reload_project()
        except Exception:
            _LOGGER.exception("project reload failed (%s)", reason)
            return
        self.__publish_project_diagnostics(result)
        self.analyze_now(reason)

    def __analysis_completed(
        self, result: JsonObject, reason: str, elapsed_ms: float
    ) -> None:
        revision = result.get("revision")
        full = result.get("full") is True
        diagnostic_count = result.get("diagnostic_count", 0)
        files = result.get("files", [])
        _LOGGER.info(
            "analysis rev=%s (%s, %s): %s files, %s diagnostics in %.0f ms",
            revision,
            reason,
            "full" if full else "syntax",
            len(files) if isinstance(files, list) else "?",
            diagnostic_count,
            elapsed_ms,
        )
        if not isinstance(revision, int) or isinstance(revision, bool):
            return
        if revision != self.worker.revision:
            return
        if revision == self.__publish_revision and (self.__publish_full or not full):
            return
        self.__publish_revision = revision
        self.__publish_full = full
        self.worker.trace_event("diagnostics_published", {
            "revision": revision,
            "full": full,
            "diagnostic_count": diagnostic_count,
        })
        self.__publish_analysis(result)
        if full:
            self.__refresh_semantic_tokens()

    def __publish_analysis(self, result: JsonObject) -> None:
        raw_diagnostics = result.get("diagnostics")
        diagnostics = raw_diagnostics if isinstance(raw_diagnostics, dict) else {}
        syntax_uri = result.get("syntax_document")
        if isinstance(syntax_uri, str):
            raw_items = diagnostics.get(syntax_uri, [])
            items = raw_items if isinstance(raw_items, list) else []
            rendered = [
                self.restore_protocol(cast(JsonValue, item), types.Diagnostic)
                for item in items
                if isinstance(item, dict)
            ]
            version = next(
                (
                    version for uri, _text, version in self.worker.open_documents
                    if uri == syntax_uri
                ),
                None,
            )
            self.text_document_publish_diagnostics(
                types.PublishDiagnosticsParams(
                    uri=syntax_uri,
                    diagnostics=rendered,
                    version=version,
                )
            )
            return
        published: set[str] = set()
        for uri, _text, version in self.worker.open_documents:
            raw_items = diagnostics.get(uri, [])
            items = raw_items if isinstance(raw_items, list) else []
            rendered = [
                self.restore_protocol(cast(JsonValue, item), types.Diagnostic)
                for item in items
                if isinstance(item, dict)
            ]
            self.text_document_publish_diagnostics(
                types.PublishDiagnosticsParams(
                    uri=uri,
                    diagnostics=rendered,
                    version=version,
                )
            )
            published.add(uri)
        for uri in self.__published - published:
            self.text_document_publish_diagnostics(
                types.PublishDiagnosticsParams(uri=uri, diagnostics=[])
            )
        self.__published = published

    def __publish_project_diagnostics(self, result: JsonObject) -> None:
        raw_items = result.get("project_diagnostics")
        items = raw_items if isinstance(raw_items, list) else []
        by_uri: dict[str, list[types.Diagnostic]] = {}
        for raw_item in items:
            if not isinstance(raw_item, dict):
                continue
            uri_value = raw_item.get("uri")
            diagnostic_value = raw_item.get("diagnostic")
            if not isinstance(uri_value, str) or not isinstance(diagnostic_value, dict):
                continue
            by_uri.setdefault(uri_value, []).append(
                self.restore_protocol(diagnostic_value, types.Diagnostic)
            )
        for uri in self.__project_published | set(by_uri):
            self.text_document_publish_diagnostics(
                types.PublishDiagnosticsParams(uri=uri, diagnostics=by_uri.get(uri, []))
            )
        self.__project_published = set(by_uri)

    def __refresh_semantic_tokens(self) -> None:
        workspace = self.client_capabilities.workspace
        tokens = None if workspace is None else workspace.semantic_tokens
        if tokens is None or not tokens.refresh_support:
            return
        self.workspace_semantic_tokens_refresh(None)

    def report_server_error(self, error: Exception, source: ServerErrors) -> None:
        """Keep protocol failures in logs rather than mixing them into stdout."""
        _LOGGER.error("unhandled protocol error from %s: %s", source, error, exc_info=error)

    async def query(self, method: str, params: _ProtocolParams) -> JsonValue:
        started = asyncio.get_running_loop().time()
        self.worker.trace_event("request_received", {
            "method": method,
            "revision": self.worker.revision,
        })
        revision = self.worker.revision
        encoded = self.__unstructure(params)
        if not isinstance(encoded, dict):
            raise JsonRpcException("invalid LSP query parameters", code=-32602)
        try:
            result = await self.analysis.query(method, cast(JsonObject, encoded))
        except WorkerOperationError as error:
            raise JsonRpcException(str(error), code=error.code) from error
        if revision != self.worker.revision:
            raise JsonRpcException("document changed during query", code=-32801)
        self.worker.trace_event("request_completed", {
            "method": method,
            "revision": revision,
            "elapsed_ms": (asyncio.get_running_loop().time() - started) * 1000,
        })
        return result

    async def format_document(self, uri: str, text: str | None) -> JsonValue:
        started = asyncio.get_running_loop().time()
        self.worker.trace_event("request_received", {
            "method": types.TEXT_DOCUMENT_FORMATTING,
            "revision": self.worker.revision,
        })
        try:
            result = await self.analysis.format(uri, text)
        except WorkerOperationError as error:
            raise JsonRpcException(str(error), code=error.code) from error
        self.worker.trace_event("request_completed", {
            "method": types.TEXT_DOCUMENT_FORMATTING,
            "revision": self.worker.revision,
            "elapsed_ms": (asyncio.get_running_loop().time() - started) * 1000,
        })
        return result

    @staticmethod
    def restore_protocol(value: JsonValue, target: type[_T]) -> _T:
        return _CONVERTER.structure(value, target)

    @staticmethod
    def __unstructure(value: _ProtocolParams) -> JsonValue:
        return cast(JsonValue, _CONVERTER.unstructure(value))

    def document_closed(self, uri: str) -> None:
        self.__published.discard(uri)


def create_server(
    *,
    compiler_root: Path | None = None,
    raw_pointers: bool = False,
    max_workers: int = 4,
    event_sink: Callable[[str, JsonObject], None] | None = None,
) -> YianLanguageServer:
    server = YianLanguageServer(
        compiler_root=compiler_root,
        raw_pointers=raw_pointers,
        max_workers=max_workers,
        event_sink=event_sink,
    )
    __register_features(server)
    return server


def __register_features(server: YianLanguageServer) -> None:
    async def initialize(ls: YianLanguageServer, params: types.InitializeParams) -> None:
        root = __workspace_root(params)
        ls.worker.set_workspace_root(root)
        if root is None:
            _LOGGER.info("no workspace folder; analyzing opened files only")

    server.feature(types.INITIALIZE)(initialize)

    async def initialized(ls: YianLanguageServer, params: types.InitializedParams) -> None:
        __register_watchers(ls)
        await ls.start_worker()
        if ls.worker.workspace_root is None:
            _LOGGER.info("standalone mode: waiting for a document")

    server.feature(types.INITIALIZED)(initialized)

    async def did_open(ls: YianLanguageServer, params: types.DidOpenTextDocumentParams) -> None:
        document = params.text_document
        ls.worker.open_document(document.uri, document.text, document.version)
        _LOGGER.info("textDocument/didOpen %s v%s", document.uri, document.version)
        ls.analyze_now("didOpen")

    server.feature(types.TEXT_DOCUMENT_DID_OPEN)(did_open)

    def did_change(ls: YianLanguageServer, params: types.DidChangeTextDocumentParams) -> None:
        if not params.content_changes:
            return
        document = params.text_document
        ls.worker.change_document(
            document.uri,
            params.content_changes[-1].text,
            document.version,
        )
        __document_changed(ls, "didChange", document.uri, document.version)

    server.feature(types.TEXT_DOCUMENT_DID_CHANGE)(did_change)

    async def definition(
        ls: YianLanguageServer, params: types.DefinitionParams
    ) -> types.Location | None:
        value = await ls.query(types.TEXT_DOCUMENT_DEFINITION, params)
        return None if value is None else ls.restore_protocol(value, types.Location)

    server.feature(types.TEXT_DOCUMENT_DEFINITION)(definition)

    async def hover_at(ls: YianLanguageServer, params: types.HoverParams) -> types.Hover | None:
        value = await ls.query(types.TEXT_DOCUMENT_HOVER, params)
        return None if value is None else ls.restore_protocol(value, types.Hover)

    server.feature(types.TEXT_DOCUMENT_HOVER)(hover_at)

    async def symbols(
        ls: YianLanguageServer, params: types.DocumentSymbolParams
    ) -> list[types.DocumentSymbol] | None:
        value = await ls.query(types.TEXT_DOCUMENT_DOCUMENT_SYMBOL, params)
        if not isinstance(value, list):
            return None
        return [ls.restore_protocol(item, types.DocumentSymbol) for item in value]

    server.feature(types.TEXT_DOCUMENT_DOCUMENT_SYMBOL)(symbols)

    async def semantic_tokens(
        ls: YianLanguageServer, params: types.SemanticTokensParams
    ) -> types.SemanticTokens:
        if ls.analysis.current_full_revision != ls.worker.revision:
            return types.SemanticTokens(data=[])
        value = await ls.query(types.TEXT_DOCUMENT_SEMANTIC_TOKENS_FULL, params)
        return ls.restore_protocol(value, types.SemanticTokens)

    server.feature(types.TEXT_DOCUMENT_SEMANTIC_TOKENS_FULL, __legend())(semantic_tokens)

    async def completions(
        ls: YianLanguageServer, params: types.CompletionParams
    ) -> types.CompletionList | None:
        value = await ls.query(types.TEXT_DOCUMENT_COMPLETION, params)
        return None if value is None else ls.restore_protocol(value, types.CompletionList)

    server.feature(
        types.TEXT_DOCUMENT_COMPLETION,
        types.CompletionOptions(trigger_characters=[".", ":", "<"]),
    )(completions)

    async def signature(
        ls: YianLanguageServer, params: types.SignatureHelpParams
    ) -> types.SignatureHelp | None:
        value = await ls.query(types.TEXT_DOCUMENT_SIGNATURE_HELP, params)
        return None if value is None else ls.restore_protocol(value, types.SignatureHelp)

    server.feature(
        types.TEXT_DOCUMENT_SIGNATURE_HELP,
        types.SignatureHelpOptions(trigger_characters=["(", ","]),
    )(signature)

    async def formatting(
        ls: YianLanguageServer, params: types.DocumentFormattingParams
    ) -> list[types.TextEdit] | None:
        text = next(
            (value for uri, value, _version in ls.worker.open_documents
             if uri == params.text_document.uri),
            None,
        )
        value = await ls.format_document(params.text_document.uri, text)
        if not isinstance(value, list):
            return None
        return [ls.restore_protocol(item, types.TextEdit) for item in value]

    server.feature(types.TEXT_DOCUMENT_FORMATTING)(formatting)

    async def references(
        ls: YianLanguageServer, params: types.ReferenceParams
    ) -> list[types.Location] | None:
        value = await ls.query(types.TEXT_DOCUMENT_REFERENCES, params)
        if not isinstance(value, list):
            return None
        return [ls.restore_protocol(item, types.Location) for item in value]

    server.feature(types.TEXT_DOCUMENT_REFERENCES)(references)

    async def highlights(
        ls: YianLanguageServer, params: types.DocumentHighlightParams
    ) -> list[types.DocumentHighlight] | None:
        value = await ls.query(types.TEXT_DOCUMENT_DOCUMENT_HIGHLIGHT, params)
        if value is None:
            return None
        if not isinstance(value, list):
            return []
        return [ls.restore_protocol(item, types.DocumentHighlight) for item in value]

    server.feature(types.TEXT_DOCUMENT_DOCUMENT_HIGHLIGHT)(highlights)

    async def prepare_rename(
        ls: YianLanguageServer, params: types.PrepareRenameParams
    ) -> types.PrepareRenamePlaceholder | None:
        value = await ls.query(types.TEXT_DOCUMENT_PREPARE_RENAME, params)
        return None if value is None else ls.restore_protocol(value, types.PrepareRenamePlaceholder)

    server.feature(types.TEXT_DOCUMENT_PREPARE_RENAME)(prepare_rename)

    async def rename(
        ls: YianLanguageServer, params: types.RenameParams
    ) -> types.WorkspaceEdit | None:
        value = await ls.query(types.TEXT_DOCUMENT_RENAME, params)
        return None if value is None else ls.restore_protocol(value, types.WorkspaceEdit)

    server.feature(types.TEXT_DOCUMENT_RENAME)(rename)

    async def code_action(
        ls: YianLanguageServer, params: types.CodeActionParams
    ) -> list[types.CodeAction]:
        value = await ls.query(types.TEXT_DOCUMENT_CODE_ACTION, params)
        if not isinstance(value, list):
            return []
        return [ls.restore_protocol(item, types.CodeAction) for item in value]

    server.feature(
        types.TEXT_DOCUMENT_CODE_ACTION,
        types.CodeActionOptions(code_action_kinds=[types.CodeActionKind.QuickFix]),
    )(code_action)

    def did_save(ls: YianLanguageServer, params: types.DidSaveTextDocumentParams) -> None:
        _LOGGER.info("textDocument/didSave %s", params.text_document.uri)
        if ls.worker.workspace_root is not None:
            asyncio.create_task(ls.reload_project("didSave"))
        else:
            ls.worker.invalidate()
            ls.analyze_now("didSave")

    server.feature(types.TEXT_DOCUMENT_DID_SAVE)(did_save)

    def did_close(ls: YianLanguageServer, params: types.DidCloseTextDocumentParams) -> None:
        uri = params.text_document.uri
        ls.worker.close_document(uri)
        ls.text_document_publish_diagnostics(
            types.PublishDiagnosticsParams(uri=uri, diagnostics=[])
        )
        ls.document_closed(uri)
        __document_changed(ls, "didClose", uri, None)

    server.feature(types.TEXT_DOCUMENT_DID_CLOSE)(did_close)

    def shutdown(ls: YianLanguageServer, params: None) -> None:
        del params
        ls.cancel_analysis()

    server.feature(types.SHUTDOWN)(shutdown)

    async def exit_server(ls: YianLanguageServer, params: None) -> None:
        del params
        await ls.close_worker()

    server.feature(types.EXIT)(exit_server)

    def watched_files(ls: YianLanguageServer, params: types.DidChangeWatchedFilesParams) -> None:
        if not params.changes:
            return
        topology = False
        for event in params.changes:
            path = __uri_to_path(event.uri)
            _LOGGER.info(
                "workspace file %s %s",
                types.FileChangeType(event.type).name.lower(),
                path,
            )
            if path.name == "package.anx" or (
                path.suffix == ".an" and event.type != types.FileChangeType.Changed
            ):
                topology = True
        if topology:
            asyncio.create_task(ls.reload_project("project file change"))
        else:
            ls.worker.invalidate()
            ls.schedule_analysis("watched files")

    server.feature(types.WORKSPACE_DID_CHANGE_WATCHED_FILES)(watched_files)


def __document_changed(
    server: YianLanguageServer, event: str, uri: str, version: int | None
) -> None:
    _LOGGER.info("textDocument/%s %s v%s", event, uri, version if version is not None else "-")
    server.schedule_analysis(event, uri if event != "didClose" else None)


def __register_watchers(server: YianLanguageServer) -> None:
    workspace = server.client_capabilities.workspace
    watched = None if workspace is None else workspace.did_change_watched_files
    if watched is None or not watched.dynamic_registration:
        _LOGGER.info("client does not support dynamic watcher registration")
        return
    try:
        server.client_register_capability(
            types.RegistrationParams(
                registrations=[
                    types.Registration(
                        id="yian-watched-files",
                        method=types.WORKSPACE_DID_CHANGE_WATCHED_FILES,
                        register_options=types.DidChangeWatchedFilesRegistrationOptions(
                            watchers=[
                                types.FileSystemWatcher(glob_pattern="**/*.an"),
                                types.FileSystemWatcher(glob_pattern="**/package.anx"),
                            ]
                        ),
                    )
                ]
            )
        )
    except Exception as error:  # pragma: no cover
        _LOGGER.warning("could not register file watchers: %s", error)


def __workspace_root(params: types.InitializeParams) -> Path | None:
    for folder in params.workspace_folders or []:
        try:
            return __uri_to_path(folder.uri)
        except ValueError:
            continue
    if params.root_uri is not None:
        try:
            return __uri_to_path(params.root_uri)
        except ValueError:
            return None
    if params.root_path:
        return Path(params.root_path)
    return None


def __uri_to_path(uri: str) -> Path:
    parsed = urlparse(uri)
    if parsed.scheme != "file":
        raise ValueError(f"unsupported document URI scheme: {parsed.scheme}")
    if parsed.netloc not in ("", "localhost"):
        raise ValueError("file URI has a non-local authority")
    return Path(unquote(parsed.path))


def __legend() -> types.SemanticTokensLegend:
    return types.SemanticTokensLegend(
        token_types=[
            "type", "struct", "enum", "interface", "typeParameter", "function",
            "method", "property", "enumMember", "variable", "parameter",
        ],
        token_modifiers=["declaration", "defaultLibrary"],
    )


if __name__ == "__main__":
    raise SystemExit("run the protocol server through lsp.main")
