"""Pure project model for anx.

``load()`` reads manifests, walks path dependencies and indexes source files.
It never compiles anything, spawns a process or writes to the project, so the
command line and the language server can share it.

The model is immutable: every mapping handed to a caller is a read-only view.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from types import MappingProxyType

from anx.manifest import Manifest


class CycleError(Exception):
    """A dependency cycle, reported as the full ``a → b → a`` path."""

    def __init__(self, cycle: list[str]) -> None:
        self.cycle = cycle
        super().__init__(f"Circular dependency: {' → '.join(cycle)}")


@dataclass(frozen=True)
class Diagnostic:
    """A structured project diagnostic.

    The shape is fixed here so loaders and callers agree on it; A1 starts
    producing them, A0 never does.
    """

    code: str
    message: str
    path: Path | None = None
    span: tuple[int, int] | None = None
    hint: str | None = None


class PackageKind(Enum):
    BIN = "bin"  # has an entry, produces an executable, cannot be a dependency
    LIB = "lib"  # no entry, analysis only, can be a dependency
    HYBRID = "hybrid"  # has an entry and can be a dependency


@dataclass(frozen=True)
class Dependency:
    """One entry of the depending package's ``[dependencies]`` table."""

    name: str  # the canonical name of the dependency
    path: Path  # its package root, resolved


@dataclass(frozen=True)
class Package:
    name: str  # canonical name: the import prefix
    kind: PackageKind
    version: str
    root: Path  # directory holding package.anx
    manifest_path: Path
    source_root: Path  # root / "src"
    entry: Path | None  # BIN/HYBRID entry file; None for LIB
    dependencies: tuple[Dependency, ...]  # sorted by name


@dataclass(frozen=True)
class SourceFile:
    path: Path
    package: str  # canonical name of the owning package
    module: tuple[str, ...]  # path segments under source_root, without ".an"


class ImportFailure(Enum):
    """Why an import did not resolve; A2 maps these onto AX009/AX010/AX012/AX014."""

    UNKNOWN_PACKAGE = "unknown_package"
    NOT_VISIBLE = "not_visible"
    NOT_A_MODULE = "not_a_module"
    ENTRY_MODULE = "entry_module"


@dataclass(frozen=True)
class ImportResolution:
    """Exactly one of ``target`` / ``failure`` is set."""

    target: SourceFile | None
    failure: ImportFailure | None


@dataclass(frozen=True)
class Project:
    root_package: str
    packages: Mapping[str, Package]
    files: Mapping[Path, SourceFile]
    dependencies: Mapping[str, tuple[str, ...]]
    std_package: str

    def file_of(self, path: Path) -> SourceFile | None:
        return self.files.get(path)

    def package_of(self, path: Path) -> Package | None:
        found = self.files.get(path)
        if found is None:
            return None
        return self.packages.get(found.package)


@dataclass(frozen=True)
class LoadResult:
    """A loaded project plus the diagnostics that are safe to report.

    ``project`` is ``None`` only when the root package itself is unusable (A1);
    A0 raises instead, so it always returns a project.
    """

    project: Project | None
    diagnostics: tuple[Diagnostic, ...]


def default_stdlib_root() -> Path:
    """Source root of the standard library shipped with this checkout.

    Resolved lazily so that importing this module performs no I/O.
    """
    return Path(__file__).resolve().parent.parent / "lib" / "src"


def load(root: Path, *, std_root: Path | None = None) -> LoadResult:
    """Load the project rooted at *root*.

    Raises the same errors as the resolver it replaces (``FileNotFoundError``
    for a missing manifest or ``src/``, ``CycleError`` for dependency cycles);
    the standard library is injected under the ``std`` name and is never walked
    from a manifest.
    """
    root = root.resolve()
    std_src = (std_root or default_stdlib_root()).resolve()

    manifests: dict[str, Manifest] = {}
    package_roots: dict[str, Path] = {}
    adjacency: dict[str, tuple[str, ...]] = {}

    def walk(name: str, path: Path) -> None:
        if name in adjacency:
            return
        manifest = Manifest.from_file(path / "package.anx")
        manifests[name] = manifest
        package_roots[name] = path
        adjacency[name] = tuple(manifest.dependencies.keys())
        for dep_name, dep in manifest.dependencies.items():
            walk(dep_name, dep.path)

    root_manifest = Manifest.from_file(root / "package.anx")
    walk(root_manifest.name, root)
    _check_cycles(adjacency)

    packages: dict[str, Package] = {}
    files: dict[Path, SourceFile] = {}
    dependencies: dict[str, tuple[str, ...]] = {}

    for name, path in package_roots.items():
        src = path / "src"
        if not src.is_dir():
            raise FileNotFoundError(f"Package '{name}': src/ directory not found at {src}")
        manifest = manifests[name]
        packages[name] = Package(
            name=name,
            kind=PackageKind.BIN,
            version=manifest.version,
            root=path,
            manifest_path=path / "package.anx",
            source_root=src.resolve(),
            entry=_default_entry(src),
            dependencies=tuple(
                sorted(
                    (Dependency(name=dep_name, path=dep.path) for dep_name, dep in manifest.dependencies.items()),
                    key=lambda dep: dep.name,
                )
            ),
        )
        dependencies[name] = adjacency[name]
        _index_files(files, src, name)

    packages["std"] = Package(
        name="std",
        kind=PackageKind.LIB,
        version="",
        root=std_src.parent,
        manifest_path=std_src.parent / "package.anx",
        source_root=std_src,
        entry=None,
        dependencies=(),
    )
    dependencies["std"] = ()
    _index_files(files, std_src, "std")

    project = Project(
        root_package=root_manifest.name,
        packages=MappingProxyType(packages),
        files=MappingProxyType(files),
        dependencies=MappingProxyType(dependencies),
        std_package="std",
    )
    return LoadResult(project=project, diagnostics=())


def _default_entry(src: Path) -> Path | None:
    entry = src / "main.an"
    return entry if entry.is_file() else None


def _index_files(files: dict[Path, SourceFile], scan_root: Path, package: str) -> None:
    """Index every ``.an`` file under *scan_root* in the existing sort order."""
    for path in sorted(scan_root.rglob("*.an")):
        files[path] = SourceFile(
            path=path,
            package=package,
            module=path.relative_to(scan_root).with_suffix("").parts,
        )


def _check_cycles(adjacency: Mapping[str, tuple[str, ...]]) -> None:
    visited: set[str] = set()
    in_stack: list[str] = []

    def dfs(node: str) -> None:
        if node in in_stack:
            idx = in_stack.index(node)
            raise CycleError(in_stack[idx:] + [node])
        if node in visited:
            return
        visited.add(node)
        in_stack.append(node)
        for dep in adjacency.get(node, ()):
            dfs(dep)
        in_stack.pop()

    for pkg in adjacency:
        dfs(pkg)
