"""The editor's view of a YIAN project: documents, project model, snapshots.

The language server answers questions about a *workspace*, not about one file.
This module holds that state on the Python side: the open documents
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
from compiler.analysis.index import Declaration, DeclarationIndex
from compiler.analysis.package_map import PackageMap
from compiler.analysis.session import AnalysisResult, AnalysisSession
from compiler.analysis.source_provenance import resolve_stdlib_root

__all__ = ["Snapshot", "Workspace"]


@dataclass(frozen=True)
class Snapshot:
    """A completed analysis tied to one workspace input revision."""

    generation: int
    revision: int
    #: Project root, or ``None`` when the workspace is a standalone source file.
    project_root: Path | None
    project: Project | None
    #: The files this snapshot analyzed, in the order the compiler numbered them.
    files: tuple[Path, ...]
    result: AnalysisResult

    @property
    def index(self) -> DeclarationIndex | None:
        """The declaration index, or ``None`` when the run stopped early."""
        return self.result.index

    def declarations_in(self, path: Path) -> tuple[Declaration, ...]:
        """Declarations made in *path*, empty when there is no index."""
        index = self.result.index
        return () if index is None else index.in_file(path)


class Workspace:
    """Event-loop-owned documents, project model, revision, and completed snapshots.

    The analysis worker receives captured inputs and never mutates this object.
    Snapshot lookup compares revision numbers without reading source files.
    """

    def __init__(self, *, compiler_root: Path | None = None, raw_pointers: bool = False) -> None:
        self.__compiler_root = compiler_root
        self.__raw_pointers = raw_pointers
        self.__std_root = resolve_stdlib_root(compiler_root)
        self.__documents = DocumentStore()
        self.__project_root: Path | None = None
        self.__workspace_root: Path | None = None
        self.__project: Project | None = None
        self.__project_diagnostics: tuple[AnxDiagnostic, ...] = ()
        self.__packages: PackageMap | None = None
        self.__session = AnalysisSession(
            compiler_root=compiler_root, packages=None, raw_pointers=raw_pointers
        )
        self.__snapshot: Snapshot | None = None
        self.__syntax: Snapshot | None = None
        self.__generation = 0
        self.__revision = 0

    @property
    def revision(self) -> int:
        return self.__revision

    @property
    def workspace_root(self) -> Path | None:
        return self.__workspace_root

    # ── documents ──────────────────────────────────────────────────────────────

    @property
    def documents(self) -> DocumentStore:
        """The in-memory overlay: unsaved buffers win over the files on disk."""
        return self.__documents

    def open(self, path: Path, text: str, version: int | None = None) -> None:
        """Record an opened or saved document."""
        self.__documents.add(Document(path=path, text=text, version=version))
        self.invalidate()

    def change(self, path: Path, text: str, version: int | None = None) -> None:
        """Record new text for an open document.

        The client sends whole documents, so this is the same operation as ``open``.
        """
        self.open(path, text, version)

    def close(self, path: Path) -> None:
        """Forget a closed document; reads fall back to the file on disk."""
        self.__documents.remove(path)
        self.invalidate()

    def is_open(self, path: Path) -> bool:
        """True when *path* currently has an in-memory version."""
        return path in self.__documents

    # ── project model (anx) ───────────────────────────────────────────────────

    @property
    def project(self) -> Project | None:
        """The loaded project, or ``None`` in standalone mode."""
        return self.__project

    @property
    def project_diagnostics(self) -> tuple[AnxDiagnostic, ...]:
        return self.__project_diagnostics

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
        root, result = self.load_directory(start)
        self.apply_directory(start, root, result)
        return result

    def load_directory(self, start: Path) -> tuple[Path | None, LoadResult | None]:
        """Read project topology without changing workspace state."""
        root = discover(start)
        if root is None:
            return None, None
        try:
            result = load(root, std_root=self.__std_root)
        except CycleError as error:
            result = LoadResult(
                None, (AnxDiagnostic(AX_DEPENDENCY_CYCLE, str(error), root / MANIFEST_NAME),)
            )
        return root, result

    def apply_directory(self, start: Path, root: Path | None, result: LoadResult | None) -> None:
        """Install a discovery result on the protocol event loop."""
        self.__workspace_root = start.resolve()
        if root is None:
            self.__project_root = None
            self.__project = None
            self.__project_diagnostics = ()
            self.__packages = None
            self.__session = self.__make_session(None)
            self.invalidate()
            return
        assert result is not None
        self.apply_project(root, result)

    def apply_project(self, root: Path, result: LoadResult) -> None:
        """Install a project model loaded outside the protocol event loop."""
        self.__project_root = root.resolve()
        self.__project = result.project
        self.__project_diagnostics = result.diagnostics
        self.__packages = None if result.project is None else PackageMap.from_document(
            result.project.compiler_package_map(),
            source=str(result.project.packages[result.project.root_package].manifest_path),
        )
        self.__session = self.__make_session(self.__packages)
        self.invalidate()

    def use_project(self, root: Path) -> LoadResult:
        """Load the project at *root* and analyze against its package map.

        A dependency cycle has no meaningful partial graph, so ``anx`` reports it
        as an exception rather than a load result; it becomes a diagnostic here
        because a language server may not crash on a broken project.
        """
        try:
            result = load(root, std_root=self.__std_root)
        except CycleError as error:
            result = LoadResult(
                None, (AnxDiagnostic(AX_DEPENDENCY_CYCLE, str(error), root / MANIFEST_NAME),)
            )
        self.apply_project(root, result)
        return result

    def files(self) -> tuple[Path, ...]:
        """Every file the next analysis covers.

        In a project that is the loaded file index — including packages nothing
        imports, so a library function the program never calls still has a
        declaration — followed by any open document the index does not
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
    def fresh_snapshot(self) -> Snapshot | None:
        """The cached *full* snapshot while it still matches the inputs.

        Semantic tokens are recomputed on every visible edit, so asking for a
        full analysis there would undo the syntax-only granularity; ``None``
        tells the caller the text moved on and it should answer without types
        (the editor then keeps its TextMate highlighting).
        """
        return self.__cached(syntax_only=False)

    @property
    def fresh_syntax_snapshot(self) -> Snapshot | None:
        return self.__cached(syntax_only=True)

    def invalidate(self) -> None:
        """Advance the input revision and discard completed snapshots."""
        self.__revision += 1
        self.__snapshot = None
        self.__syntax = None

    def capture(self) -> tuple[tuple[Path, ...], tuple[Document, ...], AnalysisSession]:
        """Capture editor-owned inputs before handing analysis to a worker."""
        return self.files(), tuple(self.__documents), self.__session

    def accept(self, revision: int, files: tuple[Path, ...], result: AnalysisResult) -> Snapshot | None:
        """Accept a worker result only if its inputs are still current."""
        if revision != self.__revision:
            return None
        self.__generation += 1
        snapshot = Snapshot(
            generation=self.__generation,
            revision=revision,
            project_root=self.__project_root,
            project=self.__project,
            files=files,
            result=result,
        )
        if result.syntax_only:
            self.__syntax = snapshot
        else:
            self.__snapshot = snapshot
        return snapshot

    def __cached(self, *, syntax_only: bool) -> Snapshot | None:
        """The cached snapshot for the current inputs, or ``None``."""
        cached = self.__syntax if syntax_only else self.__snapshot
        if cached is None:
            return None
        if cached.revision == self.__revision:
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
