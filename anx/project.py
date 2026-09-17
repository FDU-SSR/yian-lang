"""Pure project model for anx.

``load()`` reads manifests, walks path dependencies and indexes source files.
It never compiles anything, spawns a process or writes to the project, so the
command line and the language server can share it.

The model is immutable: every mapping handed to a caller is a read-only view.
Loading reports problems as :class:`Diagnostic` values instead of raising, so a
single pass yields every independently discoverable error
(docs/plan/anx-design.md §3, §4, §7).
"""

from __future__ import annotations

from collections.abc import Mapping
from collections.abc import Sequence
from dataclasses import dataclass
from dataclasses import replace
from enum import Enum
from pathlib import Path
from types import MappingProxyType

from anx.diagnostics import (
    AX_BIN_AS_DEPENDENCY,
    AX_DEPENDENCY_MANIFEST_MISSING,
    AX_DEPENDENCY_NAME_MISMATCH,
    AX_DEPENDENCY_PATH_MISSING,
    AX_DUPLICATE_PACKAGE,
    AX_ENTRY_MISMATCH,
    AX_NESTED_SOURCE_ROOTS,
    AX_NO_PROJECT_ROOT,
    Diagnostic,
    RESERVED_PACKAGE_NAMES,
    diagnostic_payload,
    sort_diagnostics,
)
from anx.manifest import DEFAULT_ENTRY
from anx.manifest import MANIFEST_NAME
from anx.manifest import Manifest
from anx.manifest import read_manifest

STD_PACKAGE = "std"

__all__ = [
    "CycleError",
    "Dependency",
    "Diagnostic",
    "ImportFailure",
    "ImportResolution",
    "LoadResult",
    "Package",
    "PackageKind",
    "Project",
    "SourceFile",
    "STD_PACKAGE",
    "default_stdlib_root",
    "discover",
    "load",
]


class CycleError(Exception):
    """A dependency cycle, reported as the full ``a → b → a`` path."""

    def __init__(self, cycle: list[str]) -> None:
        self.cycle = cycle
        super().__init__(f"Circular dependency: {' → '.join(cycle)}")


class PackageKind(Enum):
    BIN = "bin"  # has an entry, produces an executable, cannot be a dependency
    LIB = "lib"  # no entry, analysis only, can be a dependency
    HYBRID = "hybrid"  # has an entry and can be a dependency


@dataclass(frozen=True)
class Dependency:
    """One resolved dependency edge, named by the dependency's canonical name."""

    name: str
    path: Path  # the dependency's package root


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

    def resolve_import(self, importer: Path, paths: Sequence[str]) -> ImportResolution:
        """Resolve an import the way the compiler's package mode does (docs §5.1).

        A pure function: no I/O, no source parsing.  The editor passes the path
        segments it already read and maps the returned failure onto the matching
        diagnostic; the compiler implements the same rules over ``--packages``.
        """
        if not paths:
            return ImportResolution(None, ImportFailure.NOT_A_MODULE)

        first = paths[0]
        target_package = self.packages.get(first)
        if target_package is None:
            return ImportResolution(None, ImportFailure.UNKNOWN_PACKAGE)

        importer_file = self.files.get(importer)
        if importer_file is not None:
            visible = {
                importer_file.package,
                *self.dependencies.get(importer_file.package, ()),
                self.std_package,
            }
            if first not in visible:
                return ImportResolution(None, ImportFailure.NOT_VISIBLE)

        # A directory is not a module: the path must end in an existing .an file.
        if len(paths) == 1:
            return ImportResolution(None, ImportFailure.NOT_A_MODULE)

        candidate = target_package.source_root.joinpath(*paths[1:]).with_suffix(".an")
        found = self.files.get(candidate)
        if found is None:
            return ImportResolution(None, ImportFailure.NOT_A_MODULE)

        if found.path in self.__entry_paths():
            return ImportResolution(None, ImportFailure.ENTRY_MODULE)
        return ImportResolution(found, None)

    def __entry_paths(self) -> frozenset[Path]:
        return frozenset(
            package.entry for package in self.packages.values() if package.entry is not None
        )

    def compiler_package_map(self) -> dict[str, object]:
        """Render the ``--packages`` v2 payload for this project (docs §6.1)."""
        packages: dict[str, object] = {}
        for name, package in self.packages.items():
            spec: dict[str, object] = {
                "sourceRoot": str(package.source_root),
                "kind": package.kind.value,
                "dependencies": [dependency.name for dependency in package.dependencies],
            }
            if package.entry is not None:
                spec["entry"] = str(package.entry)
            packages[name] = spec
        return {"format": 2, "root": self.root_package, "packages": packages}

    def describe(self, diagnostics: Sequence[Diagnostic] = ()) -> dict[str, object]:
        """Render the dependency graph, file index and diagnostics as JSON.

        This is the payload of ``anx graph --json``: the same data the CLI and
        the language server see, in a shape a script can consume without
        parsing diagnostics out of stderr.
        """
        packages: dict[str, object] = {}
        for name, package in self.packages.items():
            packages[name] = {
                "kind": package.kind.value,
                "root": str(package.root),
                "manifest": str(package.manifest_path),
                "sourceRoot": str(package.source_root),
                "entry": None if package.entry is None else str(package.entry),
                "dependencies": [dependency.name for dependency in package.dependencies],
            }
        return {
            "root": self.root_package,
            "std": self.std_package,
            "packages": packages,
            "files": [
                {"path": str(source.path), "package": source.package, "module": list(source.module)}
                for source in self.files.values()
            ],
            "dependencies": {name: list(deps) for name, deps in self.dependencies.items()},
            "diagnostics": [diagnostic_payload(diagnostic) for diagnostic in diagnostics],
        }


@dataclass(frozen=True)
class LoadResult:
    """A loaded project plus the diagnostics that are safe to report.

    ``project`` is ``None`` only when the root package itself is unusable: no
    manifest (``AX001``), an unusable manifest (``AX002``), a reserved root name
    (``AX011``) or a source layout that contradicts its ``kind`` (``AX008``).
    Otherwise the usable part of the graph is returned even when diagnostics
    remain; a broken dependency subtree is skipped.
    """

    project: Project | None
    diagnostics: tuple[Diagnostic, ...]


def default_stdlib_root() -> Path:
    """Source root of the standard library shipped with this checkout.

    Resolved lazily so that importing this module performs no I/O.
    """
    return Path(__file__).resolve().parent.parent / "lib" / "src"


def discover(start: Path) -> Path | None:
    """Return the nearest project root at or above *start*.

    A file argument starts from its directory; the search stops at the
    filesystem root and returns ``None`` when no ``package.anx`` is found.
    """
    current = start.resolve()
    if current.is_file():
        current = current.parent
    for candidate in (current, *current.parents):
        if (candidate / MANIFEST_NAME).is_file():
            return candidate
    return None


def load(root: Path, *, std_root: Path | None = None) -> LoadResult:
    """Load the project rooted at *root*.

    ``CycleError`` is still raised for a dependency cycle: a cycle has no
    meaningful partial graph to return, and it is detected after every manifest
    has been read but before any source file is collected (§4.2).
    """
    root = root.resolve()
    std_src = (std_root or default_stdlib_root()).resolve()

    root_manifest_path = root / MANIFEST_NAME
    if not root_manifest_path.is_file():
        return LoadResult(
            None,
            (Diagnostic(AX_NO_PROJECT_ROOT, f"No {MANIFEST_NAME} found in {root}", root),),
        )

    root_manifest, root_diagnostics = read_manifest(root_manifest_path)
    diagnostics: list[Diagnostic] = list(root_diagnostics)
    if root_manifest is None or root_manifest.name in RESERVED_PACKAGE_NAMES:
        return LoadResult(None, sort_diagnostics(diagnostics))

    manifests: dict[str, Manifest] = {root_manifest.name: root_manifest}
    roots: dict[str, Path] = {root_manifest.name: root}
    order: list[str] = []
    adjacency: dict[str, tuple[str, ...]] = {}

    def walk(name: str, path: Path, manifest: Manifest) -> None:
        order.append(name)
        declared: list[str] = []
        for spec in manifest.dependencies:
            dep_root = spec.path
            dep_manifest_path = dep_root / MANIFEST_NAME
            if not dep_root.is_dir():
                diagnostics.append(
                    Diagnostic(
                        AX_DEPENDENCY_PATH_MISSING,
                        f"Dependency '{spec.key}': path {dep_root} does not exist",
                        path / MANIFEST_NAME,
                    )
                )
                continue
            if not dep_manifest_path.is_file():
                diagnostics.append(
                    Diagnostic(
                        AX_DEPENDENCY_MANIFEST_MISSING,
                        f"Dependency '{spec.key}': no {MANIFEST_NAME} in {dep_root}",
                        dep_root,
                    )
                )
                continue

            dep_manifest, dep_diagnostics = read_manifest(dep_manifest_path)
            diagnostics.extend(dep_diagnostics)
            if dep_manifest is None or dep_manifest.name in RESERVED_PACKAGE_NAMES:
                continue

            canonical = dep_manifest.name
            if spec.key != canonical:
                diagnostics.append(
                    Diagnostic(
                        AX_DEPENDENCY_NAME_MISMATCH,
                        f"Dependency key '{spec.key}' does not match the package name '{canonical}'",
                        dep_manifest_path,
                        hint=f'Rename the key to "{canonical}", or rename the package to "{spec.key}".',
                    )
                )

            known = roots.get(canonical)
            if known is not None:
                if known != dep_root:
                    diagnostics.append(
                        Diagnostic(
                            AX_DUPLICATE_PACKAGE,
                            f"Package '{canonical}' is already resolved to {known}, "
                            f"but '{spec.key}' points at {dep_root}",
                            dep_manifest_path,
                        )
                    )
                    continue
                declared.append(canonical)
                continue

            if dep_manifest.kind == PackageKind.BIN.value:
                diagnostics.append(
                    Diagnostic(
                        AX_BIN_AS_DEPENDENCY,
                        f"Dependency '{canonical}' has kind 'bin' and cannot be used as a dependency",
                        dep_manifest_path,
                        hint='Declare the package as kind "lib" or "hybrid" to expose a library interface.',
                    )
                )
                continue

            manifests[canonical] = dep_manifest
            roots[canonical] = dep_root
            declared.append(canonical)
            walk(canonical, dep_root, dep_manifest)
        adjacency[name] = tuple(sorted(set(declared)))

    walk(root_manifest.name, root, root_manifest)
    __check_cycles(adjacency)

    built: dict[str, Package] = {}
    for name in order:
        package, package_diagnostics = __build_package(name, roots[name], manifests[name])
        diagnostics.extend(package_diagnostics)
        if package is not None:
            built[name] = package

    if root_manifest.name not in built:
        return LoadResult(None, sort_diagnostics(diagnostics))

    built[STD_PACKAGE] = Package(
        name=STD_PACKAGE,
        kind=PackageKind.LIB,
        version="",
        root=std_src.parent,
        manifest_path=std_src.parent / MANIFEST_NAME,
        source_root=std_src,
        entry=None,
        dependencies=(),
    )

    diagnostics.extend(__nested_source_root_diagnostics(built))

    dependencies: dict[str, tuple[str, ...]] = {}
    for name in [*order, STD_PACKAGE]:
        if name not in built:
            continue
        dependencies[name] = tuple(dep for dep in adjacency.get(name, ()) if dep in built)

    packages: dict[str, Package] = {}
    for name, package in built.items():
        edges = dependencies[name]
        packages[name] = replace(
            package,
            dependencies=tuple(Dependency(name=dep, path=roots[dep]) for dep in edges),
        )

    files = __index_files(packages, [*order, STD_PACKAGE])
    project = Project(
        root_package=root_manifest.name,
        packages=MappingProxyType(packages),
        files=MappingProxyType(files),
        dependencies=MappingProxyType(dependencies),
        std_package=STD_PACKAGE,
    )
    return LoadResult(project=project, diagnostics=sort_diagnostics(diagnostics))


def __build_package(
    name: str, root: Path, manifest: Manifest
) -> tuple[Package | None, tuple[Diagnostic, ...]]:
    """Validate one package's source layout and, if it holds, build the package.

    Returns ``(None, diagnostics)`` when the layout contradicts ``kind``; the
    caller then skips this subtree (or, for the root package, the project).
    """
    manifest_path = root / MANIFEST_NAME
    source_root = root / "src"
    if not source_root.is_dir():
        return None, (
            Diagnostic(
                AX_ENTRY_MISMATCH,
                f"Package '{name}' has no source root at {source_root}",
                manifest_path,
                hint='Create it and move the package sources into "src/".',
            ),
        )
    source_root = source_root.resolve()

    kind = PackageKind(manifest.kind)
    diagnostics: list[Diagnostic] = []
    entry: Path | None = None

    if kind is PackageKind.LIB:
        if manifest.entry is not None:
            diagnostics.append(
                Diagnostic(
                    AX_ENTRY_MISMATCH,
                    f"Package '{name}' has kind 'lib' and must not declare 'entry'",
                    manifest_path,
                    hint='Remove "entry", or switch to kind "hybrid" if it also needs an executable.',
                )
            )
        default_entry = source_root / Path(DEFAULT_ENTRY).name
        if default_entry.is_file():
            diagnostics.append(
                Diagnostic(
                    AX_ENTRY_MISMATCH,
                    f"Package '{name}' has kind 'lib' but contains {default_entry}",
                    default_entry,
                    hint='Move or remove src/main.an, or switch to kind "hybrid".',
                )
            )
    else:
        raw_entry = manifest.entry if manifest.entry is not None else DEFAULT_ENTRY
        candidate = (root / raw_entry).resolve()
        if not candidate.is_file():
            diagnostics.append(
                Diagnostic(
                    AX_ENTRY_MISMATCH,
                    f"Package '{name}': entry file {candidate} does not exist",
                    manifest_path,
                    hint=f'Create it, or point "entry" at an existing .an file under {source_root}.',
                )
            )
        elif candidate.suffix != ".an":
            diagnostics.append(
                Diagnostic(
                    AX_ENTRY_MISMATCH,
                    f"Package '{name}': entry {candidate} is not a .an file",
                    manifest_path,
                )
            )
        elif source_root not in candidate.parents:
            diagnostics.append(
                Diagnostic(
                    AX_ENTRY_MISMATCH,
                    f"Package '{name}': entry {candidate} is outside the source root {source_root}",
                    manifest_path,
                )
            )
        else:
            entry = candidate

    if diagnostics:
        return None, tuple(diagnostics)

    return (
        Package(
            name=name,
            kind=kind,
            version=manifest.version,
            root=root,
            manifest_path=manifest_path,
            source_root=source_root,
            entry=entry,
            dependencies=(),
        ),
        (),
    )


def __nested_source_root_diagnostics(packages: Mapping[str, Package]) -> tuple[Diagnostic, ...]:
    """Report every pair of mutually nested source roots (``AX013``, §3.6).

    The standard library is excluded: it lives in the compiler checkout and is
    never a user-visible sibling of the project's packages.
    """
    roots = {
        name: package.source_root
        for name, package in packages.items()
        if name != STD_PACKAGE
    }
    diagnostics: list[Diagnostic] = []
    for outer_name, outer_root in sorted(roots.items()):
        for inner_name, inner_root in sorted(roots.items()):
            if inner_name == outer_name:
                continue
            if outer_root in inner_root.parents:
                diagnostics.append(
                    Diagnostic(
                        AX_NESTED_SOURCE_ROOTS,
                        f"Package '{inner_name}' source root {inner_root} is nested inside "
                        f"package '{outer_name}' source root {outer_root}",
                        inner_root,
                        hint="Dependencies must live outside the depending package's src/ directory.",
                    )
                )
    return tuple(diagnostics)


def __index_files(packages: Mapping[str, Package], order: list[str]) -> dict[Path, SourceFile]:
    """Index ``*.an`` files, attributing each to its deepest source root.

    The insertion order follows the dependency walk with the standard library
    last, which is the order the compiler relies on for unit numbering.
    """
    source_roots = {name: package.source_root for name, package in packages.items()}
    files: dict[Path, SourceFile] = {}
    for name in order:
        package = packages.get(name)
        if package is None:
            continue
        for path in sorted(package.source_root.rglob("*.an")):
            owner = __owner_of(path, source_roots)
            if owner != name:
                continue
            files[path] = SourceFile(
                path=path,
                package=owner,
                module=path.relative_to(package.source_root).with_suffix("").parts,
            )
    return files


def __owner_of(path: Path, source_roots: Mapping[str, Path]) -> str:
    """Return the package whose source root is the longest prefix of *path*."""
    best = ""
    best_depth = -1
    for name, root in source_roots.items():
        if root not in path.parents:
            continue
        depth = len(root.parts)
        if depth > best_depth:
            best = name
            best_depth = depth
    assert best != "", f"{path} is not under any source root"
    return best


def __check_cycles(adjacency: Mapping[str, tuple[str, ...]]) -> None:
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
