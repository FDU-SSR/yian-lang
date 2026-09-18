"""The editor's view of a YIAN project: documents, project model, snapshots.

The language server answers questions about a *workspace*, not about one file.
This module holds that state on the Python side (plan §5.7): the open documents
as an overlay over disk, the project model produced by ``anx``, and the analysis
snapshot those two imply.

Nothing here talks LSP — positions and URIs are converted at the protocol
boundary (:mod:`compiler.analysis.positions`), so this module stays usable from a
test or a future client.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

from anx.diagnostics import AX_DEPENDENCY_CYCLE, Diagnostic as AnxDiagnostic
from anx.project import MANIFEST_NAME, CycleError, LoadResult, Project, discover, load
from compiler.analysis.documents import Document, DocumentStore
from compiler.analysis.index import Declaration, Index
from compiler.analysis.package_map import PackageMap
from compiler.analysis.session import AnalysisResult, AnalysisSession
from compiler.analysis.source_provenance import resolve_stdlib_root

__all__ = ["Snapshot", "Workspace"]


@dataclass(frozen=True)
class Snapshot:
    """One immutable analysis of the workspace.

    A snapshot is replaced, never patched: the analysis passes rewrite their
    units in place, so a new run always starts from the current text (plan §5.3,
    §5.12).  ``generation`` counts snapshots in this process and is what makes a
    stale result recognisable to a caller that held on to one.
    """

    generation: int
    #: Project root, or ``None`` when the workspace is a standalone source file.
    project_root: Path | None
    project: Project | None
    #: The files this snapshot analyzed, in the order the compiler numbered them.
    files: tuple[Path, ...]
    result: AnalysisResult

    @property
    def index(self) -> Index | None:
        """The declaration index, or ``None`` when the run stopped early."""
        return self.result.index

    def declarations_in(self, path: Path) -> tuple[Declaration, ...]:
        """Declarations made in *path*, empty when there is no index."""
        index = self.result.index
        return () if index is None else index.in_file(path)


class Workspace:
    """Documents plus project model plus the latest analysis snapshot.

    The workspace is *not* thread-safe: it is owned by the language server's
    single-threaded request loop (plan §5.10).  Analysis runs only when
    :meth:`snapshot` is called, and reuses the previous run while the inputs are
    unchanged, so repeated requests on an idle editor cost one key computation.
    """

    def __init__(self, *, compiler_root: Path | None = None, raw_pointers: bool = False) -> None:
        self.__compiler_root = compiler_root
        self.__raw_pointers = raw_pointers
        self.__std_root = resolve_stdlib_root(compiler_root)
        self.__documents = DocumentStore()
        self.__project_root: Path | None = None
        self.__project: Project | None = None
        self.__packages: PackageMap | None = None
        self.__session = AnalysisSession(
            compiler_root=compiler_root, packages=None, raw_pointers=raw_pointers
        )
        self.__snapshot: Snapshot | None = None
        self.__syntax: Snapshot | None = None
        self.__generation = 0

    # ── documents ──────────────────────────────────────────────────────────────

    @property
    def documents(self) -> DocumentStore:
        """The in-memory overlay: unsaved buffers win over the files on disk."""
        return self.__documents

    def open(self, path: Path, text: str, version: int | None = None) -> None:
        """Record an opened or saved document."""
        self.__documents.add(Document(path=path, text=text, version=version))

    def change(self, path: Path, text: str, version: int | None = None) -> None:
        """Record new text for an open document.

        The client sends whole documents (``textDocumentSync = Full``, plan
        §5.10), so this is the same operation as :meth:`open`.
        """
        self.open(path, text, version)

    def close(self, path: Path) -> None:
        """Forget a closed document; reads fall back to the file on disk."""
        self.__documents.remove(path)

    def is_open(self, path: Path) -> bool:
        """True when *path* currently has an in-memory version."""
        return path in self.__documents

    # ── project model (anx) ───────────────────────────────────────────────────

    @property
    def project(self) -> Project | None:
        """The loaded project, or ``None`` in standalone mode."""
        return self.__project

    @property
    def project_root(self) -> Path | None:
        """Directory holding ``package.anx``, or ``None`` in standalone mode."""
        return self.__project_root

    @property
    def std_root(self) -> Path:
        """The standard library source root this workspace analyzes against."""
        return self.__std_root

    def use_directory(self, start: Path) -> LoadResult | None:
        """Point the workspace at the project containing *start*.

        Returns the load result — which carries the ``AX`` diagnostics a broken
        manifest produces — or ``None`` when *start* is not inside a project, in
        which case the workspace falls back to analyzing the open documents
        against the standard library.
        """
        root = discover(start)
        if root is None:
            self.__project_root = None
            self.__project = None
            self.__packages = None
            self.__session = self.__make_session(None)
            self.invalidate()
            return None
        return self.use_project(root)

    def use_project(self, root: Path) -> LoadResult:
        """Load the project at *root* and analyze against its package map.

        A dependency cycle has no meaningful partial graph, so ``anx`` reports it
        as an exception rather than a load result; it becomes a diagnostic here
        because a language server may not crash on a broken project.
        """
        self.__project_root = root.resolve()
        try:
            result = load(root, std_root=self.__std_root)
        except CycleError as error:
            result = LoadResult(
                None, (AnxDiagnostic(AX_DEPENDENCY_CYCLE, str(error), root / MANIFEST_NAME),)
            )
        self.__project = result.project
        if result.project is None:
            self.__packages = None
        else:
            self.__packages = PackageMap.from_document(
                result.project.compiler_package_map(),
                source=str(result.project.packages[result.project.root_package].manifest_path),
            )
        self.__session = self.__make_session(self.__packages)
        self.invalidate()
        return result

    def files(self) -> tuple[Path, ...]:
        """Every file the next analysis covers.

        In a project that is the loaded file index — including packages nothing
        imports, so a library function the program never calls still has a
        declaration (plan §2.2) — followed by any open document the index does not
        own, so a scratch file or a repository example outside ``src/`` is
        analyzed too.  Without a project it is the open documents plus the
        standard library.
        """
        if self.__project is None:
            return tuple(self.__standalone_files())
        files = list(self.__project.files)
        seen = {path.resolve() for path in files}
        for document in self.__documents:
            path = document.path.resolve()
            if path.suffix == ".an" and path not in seen:
                seen.add(path)
                files.append(path)
        return tuple(files)

    # ── analysis ──────────────────────────────────────────────────────────────

    @property
    def snapshot(self) -> Snapshot:
        """The full analysis, reusing the previous one while inputs match."""
        return self.__snapshot_now(syntax_only=False)

    @property
    def syntax_snapshot(self) -> Snapshot:
        """The front-end-only analysis, cached like the full one.

        Plan §5.12 level b: while text is changing the editor only needs the
        diagnostics lexing, parsing and desugaring can decide.  At a few hundred
        files that is ~70-110 ms against ~200-470 ms for the full prefix, and it
        is honest — it reports what it actually ran, never a stale type error.
        """
        return self.__snapshot_now(syntax_only=True)

    @property
    def fresh_snapshot(self) -> Snapshot | None:
        """The cached *full* snapshot while it still matches the inputs.

        Semantic tokens are recomputed on every visible edit, so asking for a
        full analysis there would undo level b; ``None`` tells the caller the
        text moved on and it should answer without types (the editor then keeps
        its TextMate highlighting, plan §5.12).
        """
        return self.__cached(syntax_only=False)

    def invalidate(self) -> None:
        """Drop the cached snapshots; the next request re-analyzes."""
        self.__snapshot = None
        self.__syntax = None

    def __snapshot_now(self, *, syntax_only: bool) -> Snapshot:
        cached = self.__cached(syntax_only=syntax_only)
        if cached is not None:
            return cached

        files = self.files()
        result = self.__session.analyze(
            files, documents=self.__documents, syntax_only=syntax_only
        )
        self.__generation += 1
        snapshot = Snapshot(
            generation=self.__generation,
            project_root=self.__project_root,
            project=self.__project,
            files=files,
            result=result,
        )
        if syntax_only:
            self.__syntax = snapshot
        else:
            self.__snapshot = snapshot
        return snapshot

    def __cached(self, *, syntax_only: bool) -> Snapshot | None:
        """The cached snapshot for the current inputs, or ``None``."""
        cached = self.__syntax if syntax_only else self.__snapshot
        if cached is None:
            return None
        files = self.files()
        key = self.__session.snapshot_key(files, documents=self.__documents)
        if cached.result.key == key and cached.files == files:
            return cached
        return None

    # ── internals ─────────────────────────────────────────────────────────────

    def __make_session(self, packages: PackageMap | None) -> AnalysisSession:
        return AnalysisSession(
            compiler_root=self.__compiler_root,
            packages=packages,
            raw_pointers=self.__raw_pointers,
        )

    def __standalone_files(self) -> Iterator[Path]:
        seen: set[Path] = set()
        for document in self.__documents:
            path = document.path.resolve()
            if path.suffix == ".an" and path not in seen:
                seen.add(path)
                yield path
        if not self.__std_root.is_dir():
            return
        for path in sorted(self.__std_root.rglob("*.an")):
            resolved = path.resolve()
            if resolved not in seen:
                seen.add(resolved)
                yield resolved
