"""Shared discovery, staging, identity, and build operations for benchmark projects."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


_PROJECT_ID = re.compile(r"[A-Za-z][A-Za-z0-9_-]*\Z")
_SCALE_MARKER = "// bench-scale"
_SCALE_LINE = re.compile(
    r"^(?P<head>\s*let\s+[A-Za-z_]\w*\s*:\s*[A-Za-z_]\w*\s*=\s*)"
    r"(?P<value>\d+)(?P<tail>;\s*// bench-scale[^\r\n]*)$",
    re.MULTILINE,
)
_SKIP_DIRS = {".git", "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache", "build"}
_SKIP_FILES = {".DS_Store"}
_CFLAGS = ("CFLAGS", "CPPFLAGS", "LDFLAGS")


@dataclass(frozen=True)
class BenchmarkProject:
    source: str
    name: str
    root: Path
    yian: Path
    c: Path | None
    raw_overlay: Path | None

    @property
    def key(self) -> str:
        return f"{self.source}/{self.name}"

    @property
    def spec_path(self) -> Path:
        return self.root / "spec.json"


def _inside(path: Path, root: Path, where: str, *, strict: bool = True) -> Path:
    try:
        resolved = path.resolve(strict=strict)
    except OSError as error:
        raise ValueError(f"{where}: cannot resolve {path}: {error}") from error
    if not resolved.is_relative_to(root):
        raise ValueError(f"{where}: path escapes benchmark root: {path}")
    return resolved


def _bench_root(project: BenchmarkProject) -> Path:
    if project.root.name != project.name or project.root.parent.name != project.source:
        raise ValueError(f"{project.key}: project root does not match its source/name")
    root = project.root.parent.parent.resolve(strict=True)
    _inside(project.root, root, project.key)
    if project.yian != project.root / "yian":
        raise ValueError(f"{project.key}: yian path must be <project>/yian")
    expected_c = project.root / "c"
    if project.c is not None and project.c != expected_c:
        raise ValueError(f"{project.key}: C path must be <project>/c")
    if project.raw_overlay is not None and project.raw_overlay != project.root / "raw":
        raise ValueError(f"{project.key}: raw overlay path must be <project>/raw")
    return root


def _load_toml(path: Path, key: str) -> dict[str, object]:
    try:
        value = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, tomllib.TOMLDecodeError) as error:
        raise ValueError(f"{key}: invalid package manifest {path}: {error}") from error
    if not isinstance(value, dict):
        raise ValueError(f"{key}: package manifest must be a TOML table: {path}")
    return value


def _package_info(package_root: Path, bench_root: Path, key: str) -> tuple[str, tuple[Path, ...]]:
    _inside(package_root, bench_root, key)
    manifest_path = package_root / "package.anx"
    _inside(manifest_path, bench_root, key)
    data = _load_toml(manifest_path, key)
    package = data.get("package")
    if not isinstance(package, dict) or not isinstance(package.get("name"), str):
        raise ValueError(f"{key}: {manifest_path} requires [package].name")
    package_name = package["name"]
    dependencies: list[Path] = []
    for section_name in ("dependencies", "dev-dependencies"):
        section = data.get(section_name, {})
        if not isinstance(section, dict):
            raise ValueError(f"{key}: {manifest_path} [{section_name}] must be a table")
        for dependency_name, declaration in sorted(section.items()):
            if not isinstance(declaration, dict) or not isinstance(declaration.get("path"), str):
                raise ValueError(
                    f"{key}: {manifest_path} dependency {dependency_name!r} must declare a local path"
                )
            relative = Path(declaration["path"])
            if relative.is_absolute():
                raise ValueError(f"{key}: {manifest_path} dependency {dependency_name!r} has an absolute path")
            dependency = Path(os.path.abspath(package_root / relative))
            canonical = _inside(dependency, bench_root, key)
            if not canonical.is_dir() or not (dependency / "package.anx").is_file():
                raise ValueError(
                    f"{key}: {manifest_path} dependency {dependency_name!r} has no local package at {dependency}"
                )
            dependencies.append(dependency)
    return package_name, tuple(dependencies)


def _tree_files(root: Path, bench_root: Path) -> Iterable[tuple[Path, Path]]:
    """Yield (path relative to root, source path), following only in-tree symlinks."""
    active: set[Path] = set()

    def visit(directory: Path, relative: Path) -> Iterable[tuple[Path, Path]]:
        canonical = _inside(directory, bench_root, str(root))
        if canonical in active:
            raise ValueError(f"{root}: directory/symlink cycle while traversing {directory}")
        active.add(canonical)
        try:
            children = sorted(directory.iterdir(), key=lambda path: path.name)
            for child in children:
                _inside(child, bench_root, str(root))
                child_relative = relative / child.name
                if child.is_dir():
                    if child.name in _SKIP_DIRS:
                        continue
                    yield from visit(child, child_relative)
                elif child.is_file() and child.name not in _SKIP_FILES and child.suffix not in {".pyc", ".pyo"}:
                    yield child_relative, child
        except OSError as error:
            raise ValueError(f"{root}: cannot read project tree at {directory}: {error}") from error
        finally:
            active.remove(canonical)

    yield from visit(root, Path())


def _validate_yian_package(project_root: Path, bench_root: Path, key: str) -> None:
    package_root = project_root / "yian"
    if not package_root.is_dir():
        raise ValueError(f"{key}: missing YIAN package directory {package_root}")
    package_name, _ = _package_info(package_root, bench_root, key)
    data = _load_toml(package_root / "package.anx", key)
    declaration = data["package"]
    assert isinstance(declaration, dict)
    kind = declaration.get("kind", "bin")
    if kind not in {"bin", "hybrid"}:
        raise ValueError(f"{key}: YIAN benchmark package {package_name!r} must be bin or hybrid")
    entry = declaration.get("entry", "src/main.an")
    if not isinstance(entry, str) or Path(entry).is_absolute():
        raise ValueError(f"{key}: invalid YIAN entry in {package_root / 'package.anx'}")
    entry_path = package_root / entry
    resolved = _inside(entry_path, bench_root, key)
    if not resolved.is_file() or resolved.suffix != ".an" or not resolved.is_relative_to(package_root.resolve()):
        raise ValueError(f"{key}: missing or invalid YIAN main module {entry_path}")


def _validate_raw_overlay(project_root: Path, yian_root: Path, overlay: Path, bench_root: Path, key: str) -> None:
    if not overlay.is_dir():
        raise ValueError(f"{key}: raw overlay is not a directory: {overlay}")
    for relative, path in _tree_files(overlay, bench_root):
        if path.suffix != ".an":
            raise ValueError(f"{key}: raw overlay contains a non-.an file: {path}")
        target = yian_root / relative
        if not target.is_file():
            raise ValueError(f"{key}: raw overlay must replace an existing YIAN source: {path} -> {target}")
        _inside(target, bench_root, key)


def _load_project(root: Path, bench_root: Path) -> BenchmarkProject:
    source, name = root.parent.name, root.name
    key = f"{source}/{name}"
    if not _PROJECT_ID.fullmatch(source) or not _PROJECT_ID.fullmatch(name):
        raise ValueError(f"{key}: source and project ID must be simple path components")
    _inside(root, bench_root, key)
    spec_path = root / "spec.json"
    try:
        data = json.loads(spec_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ValueError(f"{key}: invalid project spec {spec_path}: {error}") from error
    if not isinstance(data, dict) or data.get("source") != source or data.get("name") != name:
        raise ValueError(f"{key}: spec source/name must match its directory path")
    # Catch escaping links before any builder can consume files from the source tree.
    tuple(_tree_files(root, bench_root))

    yian = root / "yian"
    _validate_yian_package(root, bench_root, key)
    c_path = root / "c"
    if c_path.exists():
        _inside(c_path, bench_root, key)
        cmake = c_path / "CMakeLists.txt"
        if not cmake.is_file():
            raise ValueError(f"{key}: missing CMake project file {cmake}")
        c: Path | None = c_path
    elif source == "ALLOC":
        c = None
    else:
        raise ValueError(f"{key}: paired benchmark is missing C project directory {c_path}")

    raw_path = root / "raw"
    raw_overlay = raw_path if raw_path.exists() or raw_path.is_symlink() else None
    if raw_overlay is not None:
        _inside(raw_overlay, bench_root, key)
        _validate_raw_overlay(root, yian, raw_overlay, bench_root, key)
    return BenchmarkProject(source, name, root, yian, c, raw_overlay)


def discover_projects(bench_root: Path) -> list[BenchmarkProject]:
    """Discover registered two-level projects without mutating the benchmark tree."""
    try:
        root = Path(bench_root).resolve(strict=True)
    except OSError as error:
        raise ValueError(f"benchmark root is unavailable: {bench_root}: {error}") from error
    if not root.is_dir():
        raise ValueError(f"benchmark root is not a directory: {root}")
    projects: list[BenchmarkProject] = []
    for source_dir in sorted(root.iterdir(), key=lambda path: path.name):
        if source_dir.name in {"build", "native", "results", "sets"} or not source_dir.is_dir():
            continue
        _inside(source_dir, root, "benchmark discovery")
        for project_root in sorted(source_dir.iterdir(), key=lambda path: path.name):
            if not project_root.is_dir():
                continue
            _inside(project_root, root, "benchmark discovery")
            if (project_root / "spec.json").exists():
                projects.append(_load_project(project_root, root))
    return projects


def _dependency_closure(project: BenchmarkProject, bench_root: Path) -> tuple[Path, ...]:
    ordered: list[Path] = []
    seen: set[Path] = set()
    active: list[Path] = []

    def visit(package_root: Path) -> None:
        canonical = _inside(package_root, bench_root, project.key)
        if canonical in active:
            cycle = " -> ".join(str(path) for path in (*active, canonical))
            raise ValueError(f"{project.key}: local package dependency cycle: {cycle}")
        if canonical in seen:
            return
        active.append(canonical)
        _, dependencies = _package_info(package_root, bench_root, project.key)
        for dependency in dependencies:
            visit(dependency)
        active.pop()
        seen.add(canonical)
        ordered.append(package_root)

    visit(project.yian)
    # The project tree is copied as a whole; dependencies are copied after it.
    return tuple(path for path in ordered if path != project.yian and not path.is_relative_to(project.root))


def _copy_tree(source: Path, destination: Path, bench_root: Path, key: str) -> None:
    for relative, path in _tree_files(source, bench_root):
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            if not target.is_file() or target.read_bytes() != path.read_bytes():
                raise ValueError(f"{key}: conflicting dependency file while staging {target}")
            continue
        shutil.copy2(path, target)


def _scale_source(path: Path, scale: int, key: str) -> None:
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as error:
        raise ValueError(f"{key}: cannot read scale source {path}: {error}") from error
    updated, count = _SCALE_LINE.subn(
        lambda match: f"{match.group('head')}{scale}{match.group('tail')}", text
    )
    if count != 1:
        raise ValueError(f"{key}: expected exactly one `{_SCALE_MARKER}` in root YIAN sources, found {count}")
    path.write_text(updated, encoding="utf-8", newline="")


def _workspace_for(project: BenchmarkProject, profile: str, raw: bool, build_root: Path) -> tuple[Path, Path]:
    bench_root = _bench_root(project)
    if profile not in {"fast", "full"}:
        raise ValueError(f"{project.key}: profile must be 'fast' or 'full', got {profile!r}")
    try:
        build_root.mkdir(parents=True, exist_ok=True)
        canonical_build_root = build_root.resolve(strict=True)
    except OSError as error:
        raise ValueError(f"{project.key}: cannot prepare build root {build_root}: {error}") from error
    if canonical_build_root.is_relative_to(bench_root):
        raise ValueError(f"{project.key}: build root must be outside the read-only benchmark tree")
    state = "raw" if raw else "fat"
    workspace = canonical_build_root / "projects" / profile / project.source / project.name / state
    return workspace, canonical_build_root


def _reset_workspace(workspace: Path, build_root: Path) -> None:
    if workspace.is_symlink():
        workspace.unlink()
    elif workspace.exists():
        resolved = workspace.resolve(strict=True)
        if not resolved.is_relative_to(build_root) or resolved == build_root:
            raise ValueError(f"refusing to replace workspace outside build root: {workspace}")
        shutil.rmtree(workspace)
    workspace.mkdir(parents=True, exist_ok=True)


def materialize_yian(
    project: BenchmarkProject, profile: str, raw: bool, scale: int | None, build_root: Path
) -> Path:
    """Create a clean isolated project/dependency mirror and apply raw/scale edits there."""
    key = project.key
    bench_root = _bench_root(project)
    if scale is not None and (not isinstance(scale, int) or isinstance(scale, bool) or scale <= 0):
        raise ValueError(f"{key}: scale must be a positive integer or None")
    if not isinstance(raw, bool):
        raise ValueError(f"{key}: raw mode must be a bool")
    workspace, canonical_build_root = _workspace_for(project, profile, raw, Path(build_root))
    _reset_workspace(workspace, canonical_build_root)
    staged_bench = workspace / "bench"
    staged_project = staged_bench / project.root.relative_to(bench_root)
    try:
        _copy_tree(project.root, staged_project, bench_root, key)
        for dependency in _dependency_closure(project, bench_root):
            destination = staged_bench / dependency.relative_to(bench_root)
            _copy_tree(dependency, destination, bench_root, key)

        staged_yian = staged_project / "yian"
        if raw and project.raw_overlay is not None:
            for relative, overlay_source in _tree_files(project.raw_overlay, bench_root):
                target = staged_yian / relative
                if not target.is_file():
                    raise ValueError(f"{key}: raw overlay target vanished during staging: {target}")
                shutil.copy2(overlay_source, target)

        if scale is not None:
            sources = sorted((staged_yian / "src").rglob("*.an"))
            try:
                matches = [path for path in sources if _SCALE_MARKER in path.read_text(encoding="utf-8")]
            except (OSError, UnicodeError) as error:
                raise ValueError(f"{key}: cannot inspect staged YIAN scale markers: {error}") from error
            if len(matches) != 1:
                raise ValueError(f"{key}: expected exactly one `{_SCALE_MARKER}` in root YIAN sources, found {len(matches)}")
            _scale_source(matches[0], scale, key)
        return staged_yian
    except Exception:
        shutil.rmtree(workspace, ignore_errors=True)
        raise


def _project_files(project: BenchmarkProject, bench_root: Path) -> dict[str, Path]:
    roots = [project.root, *_dependency_closure(project, bench_root)]
    files: dict[str, Path] = {}
    for root in roots:
        for relative, path in _tree_files(root, bench_root):
            key = (root / relative).relative_to(bench_root).as_posix()
            previous = files.get(key)
            if previous is not None and previous.read_bytes() != path.read_bytes():
                raise ValueError(f"{project.key}: conflicting source files contribute to digest path {key}")
            files[key] = path
    return files


def project_digest(project: BenchmarkProject) -> str:
    """Hash project/dependency inputs and any declared FASTA generator identity."""
    bench_root = _bench_root(project)
    entries = {
        relative: path.read_bytes()
        for relative, path in _project_files(project, bench_root).items()
    }
    try:
        spec = json.loads(project.spec_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ValueError(f"{project.key}: cannot read project spec {project.spec_path}: {error}") from error
    if not isinstance(spec, dict):
        raise ValueError(f"{project.key}: project spec must be a JSON object: {project.spec_path}")
    io = spec.get("io", {})
    stdin = io.get("stdin", {}) if isinstance(io, dict) else {}
    fasta_n = stdin.get("fasta_n") if isinstance(stdin, dict) else None
    if fasta_n is not None:
        if project.source != "BG" or project.key == "BG/fasta":
            raise ValueError(f"{project.key}: invalid FASTA generator dependency declaration")
        generators = [
            item for item in discover_projects(bench_root)
            if item.key == "BG/fasta"
        ]
        if len(generators) != 1:
            raise ValueError(
                f"{project.key}: expected one registered BG/fasta input generator, found {len(generators)}"
            )
        generator_key = "__project_identity__/generated-input/BG/fasta"
        if generator_key in entries:
            raise ValueError(f"{project.key}: generated-input identity path collides with a project file")
        entries[generator_key] = project_digest(generators[0]).encode("ascii")

    digest = hashlib.sha256()
    for relative, content in sorted(entries.items()):
        name = relative.encode("utf-8")
        digest.update(len(name).to_bytes(8, "big"))
        digest.update(name)
        digest.update(len(content).to_bytes(8, "big"))
        digest.update(content)
    return digest.hexdigest()


def _canonical_tool(path: Path | str, key: str, label: str) -> Path:
    try:
        return Path(path).expanduser().resolve(strict=True)
    except OSError as error:
        raise RuntimeError(f"{key}: {label} is unavailable at {path}: {error}") from error


def _find_tool(name: str, key: str) -> Path:
    found = shutil.which(name)
    if found is None:
        raise RuntimeError(f"{key}: required tool {name!r} is not on PATH")
    return _canonical_tool(found, key, name)


def _clean_build_env() -> dict[str, str]:
    environment = os.environ.copy()
    for name in _CFLAGS:
        environment.pop(name, None)
    return environment


def _remove_output(output: Path, bench_root: Path, key: str) -> None:
    target = output.expanduser().absolute()
    try:
        resolved = target.resolve(strict=False)
    except OSError as error:
        raise ValueError(f"{key}: invalid output path {output}: {error}") from error
    if resolved.is_relative_to(bench_root):
        raise ValueError(f"{key}: build output must be outside the read-only benchmark tree: {output}")
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.is_symlink() or target.is_file():
        target.unlink()
    elif target.exists():
        raise ValueError(f"{key}: output path is not a file: {target}")


def _run(command: list[str], cwd: Path, environment: dict[str, str], key: str, stage: str) -> subprocess.CompletedProcess[str]:
    try:
        result = subprocess.run(command, cwd=cwd, env=environment, capture_output=True, text=True, check=False)
    except OSError as error:
        raise RuntimeError(f"{key}: {stage} could not run {command[0]}: {error}") from error
    if result.returncode != 0:
        raise RuntimeError(
            f"{key}: {stage} failed with exit {result.returncode}: {command!r}\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
    return result


def _publish(source: Path, output: Path, bench_root: Path, key: str) -> Path:
    if not source.is_file():
        raise RuntimeError(f"{key}: successful build did not produce executable {source}")
    target = output.expanduser().absolute()
    if target.resolve(strict=False).is_relative_to(bench_root):
        raise ValueError(f"{key}: build output must be outside the read-only benchmark tree: {output}")
    temporary = target.with_name(f".{target.name}.{os.getpid()}.tmp")
    try:
        shutil.copy2(source, temporary)
        os.replace(temporary, target)
    finally:
        if temporary.exists():
            temporary.unlink()
    return target


def build_yian(
    project: BenchmarkProject,
    profile: str,
    raw: bool,
    scale: int | None,
    compiler: Path,
    anx: Path,
    compiler_root: Path,
    output: Path,
    optimization: str = "-O2",
) -> Path:
    """Build one staged fat/raw package with the selected native toolchain."""
    key = project.key
    bench_root = _bench_root(project)
    _remove_output(Path(output), bench_root, key)
    compiler_path = _canonical_tool(compiler, key, "yianc")
    anx_path = _canonical_tool(anx, key, "anx")
    try:
        compiler_root_path = Path(compiler_root).expanduser().resolve(strict=True)
    except OSError as error:
        raise RuntimeError(f"{key}: compiler source root is unavailable at {compiler_root}: {error}") from error
    library_root = compiler_root_path / "lib" / "src"
    if not library_root.is_dir():
        raise RuntimeError(f"{key}: compiler standard library is missing: {library_root}")
    clang = _find_tool("clang", key)
    staged_yian = materialize_yian(project, profile, raw, scale, Path(output).parent)
    build_dir = staged_yian / "build"
    if build_dir.is_symlink() or build_dir.exists():
        if build_dir.is_symlink():
            build_dir.unlink()
        else:
            shutil.rmtree(build_dir)
    command = [str(anx_path), "build", optimization]
    if raw:
        command.append("--raw-pointers")
    environment = _clean_build_env()
    environment["YIANC"] = str(compiler_path)
    environment["YIAN_LIB"] = str(library_root.resolve())
    environment["YIAN_CC"] = str(clang)
    _run(command, staged_yian, environment, key, "YIAN build")
    return _publish(build_dir / "app", Path(output), bench_root, key)


def build_c(project: BenchmarkProject, asan: bool, output: Path, optimization: str = "-O2") -> Path:
    """Build only the CMake bench_app target with explicit clang and fixed flags."""
    key = project.key
    bench_root = _bench_root(project)
    if project.c is None:
        raise ValueError(f"{key}: allocator-only project has no C reference")
    _remove_output(Path(output), bench_root, key)
    cmake = _find_tool("cmake", key)
    clang = _find_tool("clang", key)
    target = Path(output).expanduser().absolute()
    mode = "asan" if asan else "plain"
    build_dir = target.parent / f".{target.name}.{mode}.cmake"
    if build_dir.is_symlink():
        build_dir.unlink()
    elif build_dir.exists():
        shutil.rmtree(build_dir)
    build_dir.mkdir(parents=True, exist_ok=True)
    flags = f"{optimization} -ffp-contract=off"
    environment = _clean_build_env()
    configure = [
        str(cmake), "-S", str(project.c), "-B", str(build_dir),
        f"-DCMAKE_C_COMPILER={clang}", f"-DCMAKE_C_FLAGS={flags}",
        "-DCMAKE_BUILD_TYPE=", "-DCMAKE_C_STANDARD=11",
        "-DCMAKE_C_STANDARD_REQUIRED=ON", "-DCMAKE_C_EXTENSIONS=ON",
        f"-DBENCH_ASAN={'ON' if asan else 'OFF'}",
    ]
    _run(configure, project.c, environment, key, "CMake configure")
    _run([str(cmake), "--build", str(build_dir), "--target", "bench_app", "--parallel", "1"], project.c, environment, key, "CMake build")
    return _publish(build_dir / "bench_app", target, bench_root, key)
