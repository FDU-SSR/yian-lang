"""The YIAN language server: protocol adapter over the analysis session.

Scope: process startup, document synchronisation, workspace snapshots,
published diagnostics, and dispatch to analysis queries.

Stdout is the JSON-RPC channel, so human-readable logs go to stderr. Handlers
translate protocol values and ask the compiler's analysis queries for facts.

Analysis and semantic queries run on one background worker. The protocol loop
owns document versions and publishes only results from the current revision.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from collections.abc import Callable
from typing import TYPE_CHECKING, TypeVar

from lsprotocol import types
from pygls.exceptions import JsonRpcException
from pygls.lsp.server import LanguageServer

from compiler.analysis.queries.navigation import Navigator
from compiler.analysis.session import AnalysisResult
from compiler.analysis.queries.completion import complete, signature_help as signature_info
from compiler.analysis.queries.context import QueryContext
from compiler.analysis.queries.refactor import (
    ReferenceResult,
    find_references,
    rename as rename_symbol,
)
from compiler.frontend.lex.position import SrcPosition, SrcSpan
from compiler.interop.positions import path_to_uri, to_compiler_column, uri_to_path
from lsp.completion import completion_list, signature_help
from lsp.coordinator import AnalysisCoordinator
from lsp.diagnostics import diagnostics_by_document
from lsp.formatting import document_edits
from lsp.refactor import (
    code_actions,
    document_highlights,
    import_removals,
    locations,
    to_range,
    workspace_edit,
)
from lsp.navigation import document_symbols, hover, location
from lsp.semantic_tokens import classify, encode, legend
from lsp.workspace import Snapshot, Workspace

if TYPE_CHECKING:
    from pygls.server import ServerErrors

__all__ = ["SERVER_NAME", "SERVER_VERSION", "YianLanguageServer", "create_server"]

SERVER_NAME = "yian-lsp"
#: Reported to the client as ``serverInfo``.  It tracks the VS Code extension's
#: version on purpose: the two are released together from this repository, and
# the extension warns when the pair it started does not match.
SERVER_VERSION = "0.7.0"

#: LSP's ``RequestFailed``: the request was understood, but cannot be fulfilled.
#: A refused rename (a standard library symbol, an unusable name, an unchecked
#: use) is exactly that.
REQUEST_FAILED = -32803

# Single underscore on purpose: `YianLanguageServer` mentions this module-level
# private, and a double underscore inside that class body would be mangled to
# `_YianLanguageServer__LOGGER` and fail at run time (AGENTS.md).
_LOGGER = logging.getLogger(__name__)
_QueryResult = TypeVar("_QueryResult")


class YianLanguageServer(LanguageServer):
    """One server process with event-loop-owned state and serialized compiler work."""

    def __init__(
        self,
        *,
        compiler_root: Path | None = None,
        raw_pointers: bool = False,
        max_workers: int = 1,
    ) -> None:
        # pygls types its constructor with `*args, **kwargs`, which strict mode
        # reads as a partially unknown signature even though the keywords below
        # are the ones it forwards to `JsonRPCServer.__init__`.
        super().__init__(  # pyright: ignore[reportUnknownMemberType]
            name=SERVER_NAME,
            version=SERVER_VERSION,
            # Whole documents per change: analysis always restarts from text
            # so applying incremental diffs buys nothing.
            text_document_sync_kind=types.TextDocumentSyncKind.Full,
            max_workers=max_workers,
        )
        self.model = Workspace(compiler_root=compiler_root, raw_pointers=raw_pointers)
        self.analysis = AnalysisCoordinator(self.model, self.__analysis_completed)
        #: Documents the last analysis published diagnostics for.  Anything that
        #: drops out of the next round has to be cleared explicitly, or the
        #: Problems panel keeps entries for a file nobody analyzes any more.
        self.__published: set[Path] = set()
        self.__project_published: set[Path] = set()
        #: Generation of the snapshot those diagnostics came from, so a full
        #: analysis triggered by a semantic request is published exactly once.
        self.__publish_generation = -1
        self.__navigator_generation = -1
        self.__navigator_cache: Navigator | None = None

    def navigator(self, snapshot: Snapshot) -> Navigator:
        if self.__navigator_generation != snapshot.generation or self.__navigator_cache is None:
            self.__navigator_cache = Navigator(snapshot.queries, std_root=self.model.std_root)
            self.__navigator_generation = snapshot.generation
        return self.__navigator_cache

    # ── analysis scheduling ────────────────────────

    def schedule_analysis(self, reason: str) -> None:
        self.analysis.schedule_edit(reason)

    def analyze_now(self, reason: str) -> None:
        self.analysis.schedule_full(reason)

    def cancel_analysis(self) -> None:
        self.analysis.close()

    def __analysis_completed(self, snapshot: Snapshot, reason: str, elapsed_ms: float) -> None:
        self.notify_analysis(
            snapshot, reason, "syntax" if snapshot.result.syntax_only else "full", elapsed_ms
        )

    def reload_project(self, reason: str) -> None:
        """Refresh project topology without blocking editor notifications."""
        self.model.invalidate()
        asyncio.create_task(self.__reload_project(reason))

    async def __reload_project(self, reason: str) -> None:
        start = self.model.workspace_root
        if start is None:
            self.analyze_now(reason)
            return
        while True:
            revision = self.model.revision
            try:
                root, result = await asyncio.to_thread(self.model.load_directory, start)
            except Exception:
                _LOGGER.exception("project reload failed (%s)", reason)
                return
            if revision == self.model.revision:
                break
        self.model.apply_directory(start, root, result)
        self.publish_project_diagnostics()
        self.analyze_now(reason)

    def publish_project_diagnostics(self) -> None:
        """Report project-loader failures on their manifest, including unopened files."""
        by_path: dict[Path, list[types.Diagnostic]] = {}
        for diagnostic in self.model.project_diagnostics:
            path = diagnostic.path or (
                self.model.project_root / "package.anx" if self.model.project_root is not None else None
            )
            if path is None:
                continue
            path = path.resolve()
            position = types.Position(line=0, character=0)
            by_path.setdefault(path, []).append(types.Diagnostic(
                range=types.Range(start=position, end=position),
                severity=types.DiagnosticSeverity.Error,
                source="anx", code=diagnostic.code, message=diagnostic.message,
            ))
        for path in self.__project_published | set(by_path):
            self.text_document_publish_diagnostics(types.PublishDiagnosticsParams(
                uri=path_to_uri(path), diagnostics=by_path.get(path, []),
            ))
        self.__project_published = set(by_path)

    def notify_analysis(
        self, snapshot: Snapshot, reason: str, mode: str, elapsed_ms: float | None = None
    ) -> None:
        """Log and publish one completed, current analysis snapshot.

        The declaration count is only logged when the index already exists: an
        analysis must not build it just to write a log line (see ``LazyIndex``).
        """
        if snapshot.generation == self.__publish_generation:
            return
        parts = [f"{len(snapshot.files)} files"]
        index = snapshot.index
        if index is not None and index.built:
            parts.append(f"{len(index.declarations)} declarations")
        parts.append(f"{len(snapshot.result.diagnostics)} diagnostics")
        if elapsed_ms is not None:
            parts.append(f"in {elapsed_ms:.0f} ms")
        _LOGGER.info(
            "analysis #%d (%s, %s): %s",
            snapshot.generation,
            reason,
            mode,
            ", ".join(parts),
        )
        self.__publish_generation = snapshot.generation
        self.__publish(snapshot)
        if mode == "full":
            self.refresh_semantic_tokens()

    def refresh_semantic_tokens(self) -> None:
        """Ask a supporting client to re-request semantic tokens.

        Colours fall back to TextMate while the text is ahead of the analysis
        (see :func:`semantic_tokens`), so when a full run finally lands the
        client has to be told that there is something new to fetch.  Clients
        without the capability just keep what they have.
        """
        workspace = self.client_capabilities.workspace
        tokens = None if workspace is None else workspace.semantic_tokens
        if tokens is None or not tokens.refresh_support:
            return
        self.workspace_semantic_tokens_refresh(None)

    def __publish(self, snapshot: Snapshot) -> None:
        """Send the snapshot's diagnostics for every open document.

        Diagnostics for files the client never opened are dropped: an editor
        shows what the user is looking at, and a project-wide publish would fill
        the Problems panel with standard-library noise.  Documents that were
        published to before and are no longer covered get an empty list, which is
        what removes an entry after the file is closed or the error is fixed.
        """
        texts = {path.resolve(): text for path, text in snapshot.result.sources.items()}
        by_path = diagnostics_by_document(snapshot.result.diagnostics, texts)
        published: set[Path] = set()
        for document in self.model.documents:
            path = document.path.resolve()
            published.add(path)
            self.text_document_publish_diagnostics(
                types.PublishDiagnosticsParams(
                    uri=path_to_uri(path),
                    diagnostics=by_path.get(path, []),
                    version=snapshot.versions.get(path, document.version),
                )
            )
        for path in self.__published - published:
            self.text_document_publish_diagnostics(
                types.PublishDiagnosticsParams(uri=path_to_uri(path), diagnostics=[])
            )
        self.__published = published

    def report_server_error(self, error: Exception, source: ServerErrors) -> None:
        """Write unhandled protocol and request failures to the server log.

        The extension receives this stderr output in its language-server
        channel; the exception does not create a client message box.
        """
        _LOGGER.error("unhandled %s: %s", source.__name__, error, exc_info=error)


def create_server(
    *,
    compiler_root: Path | None = None,
    raw_pointers: bool = False,
    max_workers: int = 1,
) -> YianLanguageServer:
    """Build a server with every feature of this stage registered."""
    server = YianLanguageServer(
        compiler_root=compiler_root, raw_pointers=raw_pointers, max_workers=max_workers
    )
    __register_features(server)
    return server


# ── features ───────────────────────────────────────────────────────────────────


def __register_features(server: YianLanguageServer) -> None:
    async def initialize(ls: YianLanguageServer, params: types.InitializeParams) -> None:
        root = __workspace_root(params)
        if root is None:
            _LOGGER.info("no workspace folder; analyzing opened files only")
            return
        # The *workspace* decides the mode: a workspace folder that
        # is (or lives inside) a package is analyzed in package mode, anything
        # else in standalone mode.  Which file the user happens to open later
        # does not switch modes.
        project_root, result = await asyncio.to_thread(ls.model.load_directory, root)
        ls.model.apply_directory(root, project_root, result)
        if result is None:
            _LOGGER.info("%s is not a YIAN package; standalone mode", root)
            return
        project = result.project
        if project is None:
            _LOGGER.warning(
                "project %s could not be loaded: %s",
                root,
                "; ".join(f"{d.code} {d.message}" for d in result.diagnostics) or "no reason given",
            )
            return
        _LOGGER.info(
            "project %s at %s: %d packages, %d files",
            project.root_package,
            ls.model.project_root,
            len(project.packages),
            len(project.files),
        )
        for diagnostic in result.diagnostics:
            _LOGGER.warning("%s %s (%s)", diagnostic.code, diagnostic.message, diagnostic.path)
    server.feature(types.INITIALIZE)(initialize)

    def initialized(ls: YianLanguageServer, params: types.InitializedParams) -> None:
        __register_watchers(ls)
        ls.publish_project_diagnostics()
        if ls.model.project_root is None:
            # Standalone mode: the file set is "opened documents + standard
            # library", which is empty right now, so there is nothing to analyze
            # until the first document arrives (see ``did_open``).
            _LOGGER.info("standalone mode: waiting for a document")
            return
        ls.analyze_now("startup")
    server.feature(types.INITIALIZED)(initialized)

    def did_open(ls: YianLanguageServer, params: types.DidOpenTextDocumentParams) -> None:
        document = params.text_document
        ls.model.open(uri_to_path(document.uri), document.text, document.version)
        # Opening a file is deliberate, like saving one: run the whole prefix so
        # a freshly opened buffer starts with its type errors, hover and semantic
        # highlighting available, and only later keystrokes take the cheap path.
        _LOGGER.info("textDocument/didOpen %s v%s", document.uri, document.version)
        if ls.model.project_root is None and ls.model.workspace_root is not None:
            ls.reload_project("didOpen")
        else:
            ls.analyze_now("didOpen")
    server.feature(types.TEXT_DOCUMENT_DID_OPEN)(did_open)

    def did_change(ls: YianLanguageServer, params: types.DidChangeTextDocumentParams) -> None:
        changes = params.content_changes
        if not changes:
            return
        document = params.text_document
        # Full synchronisation: the last change carries the whole document.
        ls.model.change(uri_to_path(document.uri), changes[-1].text, document.version)
        __document_changed(ls, "didChange", document.uri, document.version)
    server.feature(types.TEXT_DOCUMENT_DID_CHANGE)(did_change)

    # ── navigation ───────────────────────────────────────────────

    async def definition(
        ls: YianLanguageServer, params: types.DefinitionParams
    ) -> types.Location | None:
        located = await __located(ls, params.text_document.uri, params.position)
        if located is None:
            return None
        result, navigator, path, row, col = located
        def __resolve() -> types.Location | None:
            resolution = navigator.resolve(path, row, col)
            return None if resolution is None or resolution.target is None else location(resolution.target, navigator)
        return await __query(ls, result, __resolve)
    server.feature(types.TEXT_DOCUMENT_DEFINITION)(definition)

    async def hover_at(ls: YianLanguageServer, params: types.HoverParams) -> types.Hover | None:
        located = await __located(ls, params.text_document.uri, params.position)
        if located is None:
            return None
        result, navigator, path, row, col = located
        span = __span_of(result, path, params.position)
        if span is None:
            return None
        def __hover() -> types.Hover | None:
            resolution = navigator.resolve(path, row, col)
            return None if resolution is None else hover(resolution, navigator, span)
        return await __query(ls, result, __hover)
    server.feature(types.TEXT_DOCUMENT_HOVER)(hover_at)

    async def symbols(
        ls: YianLanguageServer, params: types.DocumentSymbolParams
    ) -> list[types.DocumentSymbol] | None:
        snapshot = await __snapshot(ls)
        if snapshot is None:
            return None
        path = uri_to_path(params.text_document.uri)
        navigator = __navigator(ls, snapshot)
        return await __query(ls, snapshot.result, lambda: document_symbols(navigator.declarations_in(path), navigator))
    server.feature(types.TEXT_DOCUMENT_DOCUMENT_SYMBOL)(symbols)

    # ── semantic highlighting ──────────────────────────────

    async def semantic_tokens(
        ls: YianLanguageServer, params: types.SemanticTokensParams
    ) -> types.SemanticTokens | None:
        # The client re-asks on every visible edit, so this feature reads the
        # cache instead of filling it: colours follow the
        # type checker when a full snapshot is current, and fall back to the
        # client's TextMate highlighting in between — an empty array is not an
        # error.
        snapshot = ls.model.fresh_snapshot
        if snapshot is None:
            return types.SemanticTokens(data=[])
        navigator = __navigator(ls, snapshot)
        path = uri_to_path(params.text_document.uri)
        revision = snapshot.revision
        data = await ls.analysis.run_query(lambda: encode(classify(navigator, path), navigator.text_of(path)))
        return types.SemanticTokens(data=data if revision == ls.model.revision else [])
    server.feature(types.TEXT_DOCUMENT_SEMANTIC_TOKENS_FULL, legend())(semantic_tokens)

    # ── completion and signature help ────────────────────────────

    async def completions(
        ls: YianLanguageServer, params: types.CompletionParams
    ) -> types.CompletionList | None:
        analysis = await __analysis(ls)
        if analysis is None:
            return None
        result, navigator = analysis
        located = __compiler_position(result, params.text_document.uri, params.position)
        if located is None:
            return None
        path, row, col = located
        context = __context(ls, result)
        return await __query(ls, result, lambda: completion_list(
            complete(context, path, row, col, std_root=ls.model.std_root), navigator
        ))
    server.feature(
        types.TEXT_DOCUMENT_COMPLETION,
        types.CompletionOptions(trigger_characters=[".", ":", "<"]),
    )(completions)

    async def signature(
        ls: YianLanguageServer, params: types.SignatureHelpParams
    ) -> types.SignatureHelp | None:
        analysis = await __analysis(ls)
        if analysis is None:
            return None
        result, _ = analysis
        located = __compiler_position(result, params.text_document.uri, params.position)
        if located is None:
            return None
        path, row, col = located
        context = __context(ls, result)
        def __signature() -> types.SignatureHelp | None:
            info = signature_info(context, path, row, col, std_root=ls.model.std_root)
            return None if info is None else signature_help(info)
        return await __query(ls, result, __signature)
    server.feature(
        types.TEXT_DOCUMENT_SIGNATURE_HELP,
        types.SignatureHelpOptions(trigger_characters=["(", ","]),
    )(signature)

    # ── formatting ────────────────────────────────────────────────────────────

    def formatting(
        ls: YianLanguageServer, params: types.DocumentFormattingParams
    ) -> list[types.TextEdit] | None:
        # Formatting reads the buffer, not the snapshot: it needs no analysis, and
        # a file that does not parse yet gets no edits rather than a partial
        # rewrite.
        path = uri_to_path(params.text_document.uri)
        text = ls.model.documents.text(path)
        return document_edits(text, path)
    server.feature(types.TEXT_DOCUMENT_FORMATTING)(formatting)

    # ── references, rename and quick fixes ───────────────────────

    async def references(
        ls: YianLanguageServer, params: types.ReferenceParams
    ) -> list[types.Location] | None:
        located = await __located(ls, params.text_document.uri, params.position)
        if located is None:
            return None
        result, navigator, path, row, col = located
        context = __context(ls, result)
        def __references() -> list[types.Location] | None:
            found = find_references(
                context, path, row, col,
                include_declaration=params.context.include_declaration,
                std_root=ls.model.std_root,
            )
            return None if found is None else locations(found, navigator)
        return await __query(ls, result, __references)
    server.feature(types.TEXT_DOCUMENT_REFERENCES)(references)

    async def highlights(
        ls: YianLanguageServer, params: types.DocumentHighlightParams
    ) -> list[types.DocumentHighlight] | None:
        located = await __located(ls, params.text_document.uri, params.position)
        if located is None:
            return None
        result, navigator, path, row, col = located
        context = __context(ls, result)
        def __highlights() -> list[types.DocumentHighlight] | None:
            found = find_references(context, path, row, col, std_root=ls.model.std_root)
            if found is None:
                return None
            same_file = ReferenceResult(
                target=found.target,
                sites=tuple(site for site in found.sites if site.span.path.resolve() == path.resolve()),
            )
            return document_highlights(same_file, navigator)
        return await __query(ls, result, __highlights)
    server.feature(types.TEXT_DOCUMENT_DOCUMENT_HIGHLIGHT)(highlights)

    async def prepare_rename(
        ls: YianLanguageServer, params: types.PrepareRenameParams
    ) -> types.PrepareRenamePlaceholder | None:
        """Whether a rename is possible here, and what it would rename.

        The placeholder is what the client shows before the user types the new
        name, so refusing here is how "not a symbol" becomes a clear message
        instead of an edit that does nothing.
        """
        located = await __located(ls, params.text_document.uri, params.position)
        if located is None:
            return None
        result, navigator, path, row, col = located
        context = __context(ls, result)
        def __prepare() -> types.PrepareRenamePlaceholder | None:
            found = find_references(context, path, row, col, std_root=ls.model.std_root)
            if found is None:
                return None
            return types.PrepareRenamePlaceholder(
                range=to_range(found.target.span, navigator), placeholder=found.target.name
            )
        return await __query(ls, result, __prepare)
    server.feature(types.TEXT_DOCUMENT_PREPARE_RENAME)(prepare_rename)

    async def rename(
        ls: YianLanguageServer, params: types.RenameParams
    ) -> types.WorkspaceEdit | None:
        located = await __located(ls, params.text_document.uri, params.position)
        if located is None:
            return None
        result, navigator, path, row, col = located
        context = __context(ls, result)
        outcome = await __query(ls, result, lambda: rename_symbol(
            context, path, row, col, params.new_name, std_root=ls.model.std_root
        ))
        if not outcome.ok:
            # A refused rename is a *request* failure with a readable reason, not
            # a server error: the client shows the message instead of applying a
            # partial edit (拒绝批量修改).
            raise JsonRpcException(outcome.refusal, code=REQUEST_FAILED)
        return await __query(ls, result, lambda: workspace_edit(outcome, navigator))
    server.feature(types.TEXT_DOCUMENT_RENAME)(rename)

    async def code_action(
        ls: YianLanguageServer, params: types.CodeActionParams
    ) -> list[types.CodeAction]:
        navigator, path, result = await __file_context(ls, params.text_document.uri)
        if navigator is None or path is None or result is None:
            return []
        return await __query(ls, result, lambda: code_actions(
            import_removals(result, navigator, path), navigator, path
        ))
    server.feature(
        types.TEXT_DOCUMENT_CODE_ACTION,
        types.CodeActionOptions(code_action_kinds=[types.CodeActionKind.QuickFix]),
    )(code_action)

    def did_save(ls: YianLanguageServer, params: types.DidSaveTextDocumentParams) -> None:
        # Registering this feature also advertises `save: true`, which is what
        # makes the client send the notification at all.  A save is a deliberate
        # pause, so the analysis does not wait for the debounce.
        uri = params.text_document.uri
        _LOGGER.info("textDocument/didSave %s", uri)
        if ls.model.workspace_root is not None:
            ls.reload_project("didSave")
        else:
            ls.model.invalidate()
            ls.analyze_now("didSave")
    server.feature(types.TEXT_DOCUMENT_DID_SAVE)(did_save)

    def did_close(ls: YianLanguageServer, params: types.DidCloseTextDocumentParams) -> None:
        uri = params.text_document.uri
        ls.model.close(uri_to_path(uri))
        __document_changed(ls, "didClose", uri, None)
    server.feature(types.TEXT_DOCUMENT_DID_CLOSE)(did_close)

    def shutdown(ls: YianLanguageServer, params: None) -> None:
        # A scheduled analysis must not run after shutdown: the client is gone.
        ls.cancel_analysis()
    server.feature(types.SHUTDOWN)(shutdown)

    def watched_files(ls: YianLanguageServer, params: types.DidChangeWatchedFilesParams) -> None:
        __watched_files_changed(ls, params)
    server.feature(types.WORKSPACE_DID_CHANGE_WATCHED_FILES)(watched_files)


async def __snapshot(server: YianLanguageServer) -> Snapshot | None:
    """The current full analysis, or ``None`` when the server cannot produce one.

    A semantic request is a reason to run the whole prefix,
    so whatever it produces also refreshes the diagnostics the editor shows.
    """
    revision = server.model.revision
    snapshot = await server.analysis.request_full()
    if revision != server.model.revision:
        raise JsonRpcException("document changed during analysis", code=-32801)
    return snapshot


async def __query(
    server: YianLanguageServer, expected: AnalysisResult, work: Callable[[], _QueryResult]
) -> _QueryResult:
    snapshot = server.model.fresh_snapshot
    if snapshot is None or snapshot.result is not expected:
        raise JsonRpcException("document changed before query", code=-32801)
    result = await server.analysis.run_query(work)
    if server.model.fresh_snapshot is not snapshot:
        raise JsonRpcException("document changed during query", code=-32801)
    return result


def __context(server: YianLanguageServer, result: AnalysisResult) -> QueryContext:
    snapshot = server.model.fresh_snapshot
    if snapshot is None or snapshot.result is not result:
        raise JsonRpcException("document changed before query", code=-32801)
    return snapshot.queries


def __navigator(server: YianLanguageServer, snapshot: Snapshot) -> Navigator:
    return server.navigator(snapshot)


async def __analysis(
    server: YianLanguageServer,
) -> tuple[AnalysisResult, Navigator] | None:
    """The current analysis and a navigator over it, or ``None``.

    Every query starts here: the navigator answers "what is at this position",
    and the raw result is what completion and semantic tokens read their tables
    from.
    """
    snapshot = await __snapshot(server)
    if snapshot is None:
        return None
    return snapshot.result, __navigator(server, snapshot)


def __compiler_position(
    result: AnalysisResult, uri: str, position: types.Position
) -> tuple[Path, int, int] | None:
    """The document path and the compiler position for a client position.

    The client speaks 0-based lines and UTF-16 characters; the analysis speaks
    0-based rows and columns counted in code points, so the conversion happens
    here, at the boundary, and only for a document the analysis actually read.
    """
    path = uri_to_path(uri)
    lines = __text_of(result, path).splitlines()
    if not 0 <= position.line < len(lines):
        return None
    return path, position.line, to_compiler_column(lines[position.line], position.character)


def __text_of(result: AnalysisResult, path: Path) -> str:
    for candidate, text in result.sources.items():
        if candidate.resolve() == path.resolve():
            return text
    return ""


def __span_of(result: AnalysisResult, path: Path, position: types.Position) -> SrcSpan | None:
    """A one-character span at the client position, for hover's range."""
    located = __compiler_position(result, path.as_uri(), position)
    if located is None:
        return None
    _, row, column = located
    start = SrcPosition(row, column, path)
    return SrcSpan(start, SrcPosition(row, column + 1, path))


async def __located(
    server: YianLanguageServer, uri: str, position: types.Position
) -> tuple[AnalysisResult, Navigator, Path, int, int] | None:
    """Navigator, path and compiler position for one client request."""
    analysis = await __analysis(server)
    if analysis is None:
        return None
    result, navigator = analysis
    located = __compiler_position(result, uri, position)
    if located is None:
        return None
    path, row, col = located
    return result, navigator, path, row, col


async def __file_context(
    server: YianLanguageServer, uri: str
) -> tuple[Navigator | None, Path | None, AnalysisResult | None]:
    """Navigator, path and result for a request that is about a whole file."""
    analysis = await __analysis(server)
    if analysis is None:
        return None, None, None
    result, navigator = analysis
    return navigator, uri_to_path(uri), result


def __document_changed(
    server: YianLanguageServer, event: str, uri: str, version: int | None
) -> None:
    """Record a document event and schedule the two analysis timers.

    The workspace invalidates its snapshots when the text or file set changes.
    The coordinator schedules a syntax-only run after a short idle period and
    a complete run after a longer idle period.
    """
    _LOGGER.info("textDocument/%s %s v%s", event, uri, version if version is not None else "-")
    server.schedule_analysis(event)


def __watched_files_changed(
    server: YianLanguageServer, params: types.DidChangeWatchedFilesParams
) -> None:
    """React to files that changed outside the editor.

    A manifest change can add or remove packages, so it reloads the project
    model; a source change only invalidates the snapshot.  Both leave the
    document overlay alone: an unsaved buffer still wins over disk.
    """
    if not params.changes:
        return
    reload_needed = False
    for event in params.changes:
        path = uri_to_path(event.uri)
        _LOGGER.info(
            "workspace file %s %s", types.FileChangeType(event.type).name.lower(), path
        )
        if path.name == "package.anx" or (
            path.suffix == ".an" and event.type != types.FileChangeType.Changed
        ):
            reload_needed = True
    if reload_needed and server.model.workspace_root is not None:
        server.reload_project("project file change")
        return
    server.model.invalidate()
    server.schedule_analysis("watched files")


def __register_watchers(server: YianLanguageServer) -> None:
    """Ask the client to report ``.an`` and manifest changes made outside it.

    Registration is dynamic: the client tells us in ``initialize`` whether it
    supports it, and a client that does not simply never sends the events.
    """
    options = server.client_capabilities.workspace
    watched = None if options is None else options.did_change_watched_files
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
    except Exception as error:  # pragma: no cover - the client refused the request
        _LOGGER.warning("could not register file watchers: %s", error)


def __workspace_root(params: types.InitializeParams) -> Path | None:
    """The directory to look for ``package.anx`` in.

    A workspace folder wins over the deprecated single-root fields; a window with
    neither analyzes whatever documents it opens.
    """
    folders = params.workspace_folders or []
    for folder in folders:
        try:
            return uri_to_path(folder.uri)
        except ValueError:
            continue
    if params.root_uri is not None:
        try:
            return uri_to_path(params.root_uri)
        except ValueError:
            return None
    if params.root_path:
        return Path(params.root_path)
    return None
