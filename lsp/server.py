"""The YIAN language server: protocol adapter over the analysis session.

Scope (plan §7 P3/P4): the process form, document synchronisation, the workspace
snapshot, and published diagnostics.  Navigation requests arrive in P5.

Two rules shape this module:

* **stdout is the JSON-RPC channel** (plan §5.10), so the server only ever writes
  to it through pygls.  Everything human-readable goes to the ``stderr`` logger.
* **the protocol layer holds no language knowledge** (plan §1.3): a handler's job
  is to turn an LSP payload into a :class:`~lsp.workspace.Workspace` call and a
  log line, nothing more.

Analysis is *lazy and debounced* rather than per keystroke (plan §5.12 level a):
a burst of edits arms one timer, and the analysis runs when typing pauses.  That
is what keeps a half-typed `x.` or an unfinished string from painting the file
red, and it is the reason a stale publish cannot happen — the timer coalesces
changes, and every publish carries the document version it was computed from.
"""

from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path
from typing import TYPE_CHECKING

from lsprotocol import types
from pygls.lsp.server import LanguageServer

from compiler.analysis.navigation import Navigator
from compiler.frontend.lex.position import SrcPosition, SrcSpan
from compiler.analysis.positions import path_to_uri, to_compiler_column, uri_to_path
from lsp.diagnostics import diagnostics_by_document
from lsp.navigation import document_symbols, hover, location
from lsp.workspace import Snapshot, Workspace

if TYPE_CHECKING:
    from pygls.server import ServerErrors

__all__ = ["SERVER_NAME", "SERVER_VERSION", "YianLanguageServer", "create_server"]

SERVER_NAME = "yian-lsp"
SERVER_VERSION = "0.1.0"

#: How long the server waits for typing to pause before analyzing.  The value is
#: the debounce half of plan §5.12 level a; it is deliberately short enough to
#: feel immediate and long enough to swallow a keystroke burst.
DEBOUNCE_SECONDS = 0.2

# Single underscore on purpose: `YianLanguageServer` mentions this module-level
# private, and a double underscore inside that class body would be mangled to
# `_YianLanguageServer__LOGGER` and fail at run time (AGENTS.md).
_LOGGER = logging.getLogger(__name__)


class YianLanguageServer(LanguageServer):
    """One server process, serving one editor window.

    Handlers run on a single worker thread (``max_workers=1``): analysis is
    CPU-bound and the workspace is stateful, so requests are serialized in the
    order the client sent them.  ``$/cancelRequest`` still works — pygls cancels
    a request that has not started yet (plan §5.10).
    """

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
            # (plan §5.3, §5.10), so applying incremental diffs buys nothing.
            text_document_sync_kind=types.TextDocumentSyncKind.Full,
            max_workers=max_workers,
        )
        self.model = Workspace(compiler_root=compiler_root, raw_pointers=raw_pointers)
        self.__timer: asyncio.TimerHandle | None = None
        #: Documents the last analysis published diagnostics for.  Anything that
        #: drops out of the next round has to be cleared explicitly, or the
        #: Problems panel keeps entries for a file nobody analyzes any more.
        self.__published: set[Path] = set()

    # ── analysis scheduling (plan §5.12 level a) ───────────────────────────────

    def schedule_analysis(self, reason: str) -> None:
        """Analyze once the editor goes quiet; the latest event in a burst wins."""
        if self.__timer is not None:
            self.__timer.cancel()
        self.__timer = asyncio.get_running_loop().call_later(
            DEBOUNCE_SECONDS, self.__analyze, reason
        )

    def analyze_now(self, reason: str) -> None:
        """Analyze without waiting for the debounce: a save is a deliberate act."""
        self.__cancel_timer()
        self.__analyze(reason)

    def cancel_analysis(self) -> None:
        """Forget a scheduled analysis; the server is shutting down."""
        self.__cancel_timer()

    def __cancel_timer(self) -> None:
        if self.__timer is not None:
            self.__timer.cancel()
            self.__timer = None

    def __analyze(self, reason: str) -> None:
        self.__timer = None
        started = time.perf_counter()
        try:
            snapshot = self.model.snapshot
        except Exception as error:  # a compiler bug, not something the user typed
            _LOGGER.error("analysis failed (%s): %s", reason, error, exc_info=error)
            return
        elapsed = (time.perf_counter() - started) * 1000
        index = snapshot.index
        _LOGGER.info(
            "analysis #%d (%s): %d files, %d declarations, %d diagnostics in %.0f ms",
            snapshot.generation,
            reason,
            len(snapshot.files),
            0 if index is None else len(index.declarations),
            len(snapshot.result.diagnostics),
            elapsed,
        )
        self.__publish(snapshot)

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
                    version=snapshot.result.versions.get(path, document.version),
                )
            )
        for path in self.__published - published:
            self.text_document_publish_diagnostics(
                types.PublishDiagnosticsParams(uri=path_to_uri(path), diagnostics=[])
            )
        self.__published = published

    def report_server_error(self, error: Exception, source: ServerErrors) -> None:
        """Log a protocol-level failure instead of interrupting the user.

        pygls would show a message box; a broken request is a server bug or a
        malformed client message, and neither should take over the editor.  The
        line ends up in the extension's output channel through stderr.
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
    @server.feature(types.INITIALIZE)
    def initialize(ls: YianLanguageServer, params: types.InitializeParams) -> None:
        root = __workspace_root(params)
        if root is None:
            _LOGGER.info("no workspace folder; analyzing opened files only")
            return
        # The *workspace* decides the mode (plan §5.5): a workspace folder that
        # is (or lives inside) a package is analyzed in package mode, anything
        # else in standalone mode.  Which file the user happens to open later
        # does not switch modes.
        result = ls.model.use_directory(root)
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

    @server.feature(types.INITIALIZED)
    def initialized(ls: YianLanguageServer, params: types.InitializedParams) -> None:
        __register_watchers(ls)
        if ls.model.project_root is None:
            # Standalone mode: the file set is "opened documents + standard
            # library", which is empty right now, so there is nothing to analyze
            # until the first document arrives (see ``did_open``).
            _LOGGER.info("standalone mode: waiting for a document")
            return
        ls.analyze_now("startup")

    @server.feature(types.TEXT_DOCUMENT_DID_OPEN)
    def did_open(ls: YianLanguageServer, params: types.DidOpenTextDocumentParams) -> None:
        document = params.text_document
        ls.model.open(uri_to_path(document.uri), document.text, document.version)
        __document_changed(ls, "didOpen", document.uri, document.version)

    @server.feature(types.TEXT_DOCUMENT_DID_CHANGE)
    def did_change(ls: YianLanguageServer, params: types.DidChangeTextDocumentParams) -> None:
        changes = params.content_changes
        if not changes:
            return
        document = params.text_document
        # Full synchronisation: the last change carries the whole document.
        ls.model.change(uri_to_path(document.uri), changes[-1].text, document.version)
        __document_changed(ls, "didChange", document.uri, document.version)

    # ── navigation (plan §7 P5) ───────────────────────────────────────────────

    @server.feature(types.TEXT_DOCUMENT_DEFINITION)
    def definition(
        ls: YianLanguageServer, params: types.DefinitionParams
    ) -> types.Location | None:
        navigator, row, col = __query(ls, params.text_document.uri, params.position)
        if navigator is None or row is None or col is None:
            return None
        resolution = navigator.resolve(uri_to_path(params.text_document.uri), row, col)
        if resolution is None or resolution.target is None:
            return None
        return location(resolution.target, navigator)

    @server.feature(types.TEXT_DOCUMENT_HOVER)
    def hover_at(ls: YianLanguageServer, params: types.HoverParams) -> types.Hover | None:
        navigator, row, col = __query(ls, params.text_document.uri, params.position)
        if navigator is None or row is None or col is None:
            return None
        path = uri_to_path(params.text_document.uri)
        resolution = navigator.resolve(path, row, col)
        if resolution is None:
            return None
        span = __span_of(navigator, path, params.position)
        if span is None:
            return None
        return hover(resolution, navigator, span)

    @server.feature(types.TEXT_DOCUMENT_DOCUMENT_SYMBOL)
    def symbols(
        ls: YianLanguageServer, params: types.DocumentSymbolParams
    ) -> list[types.DocumentSymbol] | None:
        snapshot = __snapshot(ls)
        if snapshot is None:
            return None
        path = uri_to_path(params.text_document.uri)
        navigator = __navigator(ls, snapshot)
        return document_symbols(navigator.declarations_in(path), navigator)

    @server.feature(types.TEXT_DOCUMENT_DID_SAVE)
    def did_save(ls: YianLanguageServer, params: types.DidSaveTextDocumentParams) -> None:
        # Registering this feature also advertises `save: true`, which is what
        # makes the client send the notification at all.  A save is a deliberate
        # pause, so the analysis does not wait for the debounce.
        uri = params.text_document.uri
        ls.model.invalidate()
        _LOGGER.info("textDocument/didSave %s", uri)
        ls.analyze_now("didSave")

    @server.feature(types.TEXT_DOCUMENT_DID_CLOSE)
    def did_close(ls: YianLanguageServer, params: types.DidCloseTextDocumentParams) -> None:
        uri = params.text_document.uri
        ls.model.close(uri_to_path(uri))
        __document_changed(ls, "didClose", uri, None)

    @server.feature(types.SHUTDOWN)
    def shutdown(ls: YianLanguageServer, params: None) -> None:
        # A scheduled analysis must not run after shutdown: the client is gone.
        ls.cancel_analysis()

    @server.feature(types.WORKSPACE_DID_CHANGE_WATCHED_FILES)
    def watched_files(ls: YianLanguageServer, params: types.DidChangeWatchedFilesParams) -> None:
        __watched_files_changed(ls, params)


def __snapshot(server: YianLanguageServer) -> Snapshot | None:
    """The current analysis, or ``None`` when the server cannot produce one."""
    try:
        return server.model.snapshot
    except Exception as error:  # a compiler bug, not something the user typed
        _LOGGER.error("analysis failed (navigation): %s", error, exc_info=error)
        return None


def __navigator(server: YianLanguageServer, snapshot: Snapshot) -> Navigator:
    return Navigator(snapshot.result, std_root=server.model.std_root)


def __query(
    server: YianLanguageServer, uri: str, position: types.Position
) -> tuple[Navigator | None, int | None, int | None]:
    """The navigator plus the compiler position for a client position.

    The client speaks 0-based UTF-16; the analysis speaks 1-based code points
    (plan §5.1), so the conversion happens here, at the boundary, and only for a
    document the analysis actually read.
    """
    snapshot = __snapshot(server)
    if snapshot is None:
        return None, None, None
    navigator = __navigator(server, snapshot)
    path = uri_to_path(uri)
    lines = navigator.text_of(path).splitlines()
    if not 0 <= position.line < len(lines):
        return navigator, None, None
    column = to_compiler_column(lines[position.line], position.character)
    return navigator, position.line, column


def __span_of(navigator: Navigator, path: Path, position: types.Position) -> SrcSpan | None:
    """A one-character span at the client position, for hover's range."""
    lines = navigator.text_of(path).splitlines()
    if not 0 <= position.line < len(lines):
        return None
    column = to_compiler_column(lines[position.line], position.character)
    start = SrcPosition(position.line, column, path)
    return SrcSpan(start, SrcPosition(position.line, column + 1, path))


def __document_changed(
    server: YianLanguageServer, event: str, uri: str, version: int | None
) -> None:
    """Record a document event and arm the analysis timer.

    Every event invalidates the snapshot — the text changed, or the file set did
    — and then arms one debounce timer, so a burst of keystrokes costs a single
    analysis (plan §5.12 level a).  Whatever changed is picked up by that
    analysis; nothing here needs to know what it was.
    """
    server.model.invalidate()
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
        if path.name == "package.anx":
            reload_needed = True
    root = server.model.project_root
    if reload_needed and root is not None:
        server.model.use_project(root)
        server.analyze_now("manifest change")
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
