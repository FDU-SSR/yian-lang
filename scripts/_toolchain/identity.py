"""Source and artifact identities for installed native tools."""

from __future__ import annotations

import hashlib
import json
import subprocess
import tomllib
from pathlib import Path
from typing import Any

BUILD_INFO_FORMAT = 1
COMPONENT_ROOTS = {
    "yianc": "bootstrap/yianc",
    "anx": "bootstrap/anx",
    "yian-lsp": "lsp",
}
SOURCE_SUFFIXES = {".an", ".c", ".h"}
RESOURCE_ROOTS = ("lib/src", "runtime/src", "runtime/include")


class IdentityError(Exception):
    """An input set cannot be represented by a stable source identity."""


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _package_closure(root: Path, package_root: Path, seen: set[Path]) -> None:
    package_root = package_root.resolve()
    if package_root in seen:
        return
    if not package_root.is_relative_to(root):
        raise IdentityError(f"package dependency escapes the checkout: {package_root}")
    manifest = package_root / "package.anx"
    if not package_root.is_dir() or not manifest.is_file():
        raise IdentityError(f"package source or manifest is missing: {package_root}")
    seen.add(package_root)
    try:
        with manifest.open("rb") as stream:
            document = tomllib.load(stream)
    except (OSError, tomllib.TOMLDecodeError) as error:
        raise IdentityError(f"cannot read package manifest {manifest}: {error}") from error
    dependencies = document.get("dependencies", {})
    if not isinstance(dependencies, dict):
        raise IdentityError(f"invalid dependency table in {manifest}")
    for name, value in sorted(dependencies.items()):
        if not isinstance(value, dict) or not isinstance(value.get("path"), str):
            raise IdentityError(f"dependency {name!r} is not a local package in {manifest}")
        _package_closure(root, package_root / value["path"], seen)


def component_source_files(root: Path, component: str) -> tuple[Path, ...]:
    try:
        relative_root = COMPONENT_ROOTS[component]
    except KeyError as error:
        raise IdentityError(f"unknown toolchain component: {component}") from error
    root = root.resolve()
    seen: set[Path] = set()
    _package_closure(root, root / relative_root, seen)
    files: set[Path] = set()
    for package_root in seen:
        manifest = package_root / "package.anx"
        files.add(manifest)
        for subdirectory in (package_root / "src", package_root / "native"):
            if not subdirectory.is_dir():
                continue
            for path in subdirectory.rglob("*"):
                if path.is_file() and path.suffix in SOURCE_SUFFIXES:
                    files.add(path.resolve())
    # The language standard library is implicitly compiled by anx builds and
    # therefore participates in each native component's source identity.
    std_root = root / "lib" / "src"
    if not std_root.is_dir():
        raise IdentityError(f"standard library sources are missing: {std_root}")
    files.update(path.resolve() for path in std_root.rglob("*.an") if path.is_file())
    return tuple(sorted(files, key=lambda path: path.relative_to(root).as_posix()))


def paths_sha256(root: Path, paths: tuple[Path, ...] | list[Path]) -> str:
    root = root.resolve()
    digest = hashlib.sha256()
    for path in sorted((path.resolve() for path in paths), key=lambda item: item.relative_to(root).as_posix()):
        if not path.is_file():
            raise IdentityError(f"source input is not a regular file: {path}")
        relative = path.relative_to(root).as_posix().encode("utf-8")
        digest.update(len(relative).to_bytes(8, "big"))
        digest.update(relative)
        digest.update(bytes.fromhex(file_sha256(path)))
    return digest.hexdigest()


def component_source_sha256(root: Path, component: str) -> str:
    return paths_sha256(root, component_source_files(root, component))


def resource_files(root: Path) -> tuple[Path, ...]:
    root = root.resolve()
    files: list[Path] = []
    for relative in RESOURCE_ROOTS:
        directory = root / relative
        if not directory.is_dir():
            raise IdentityError(f"installed resource source is missing: {directory}")
        files.extend(path.resolve() for path in directory.rglob("*") if path.is_file())
    return tuple(files)


def resources_source_sha256(root: Path) -> str:
    return paths_sha256(root, resource_files(root))


def tree_sha256(root: Path, *, excluded_names: frozenset[str] = frozenset()) -> str:
    root = root.resolve()
    paths = tuple(
        path.resolve()
        for path in root.rglob("*")
        if path.is_file() and path.name not in excluded_names
    )
    return paths_sha256(root, list(paths))


def tool_build_info(
    root: Path,
    *,
    yianc: Path,
    anx: Path,
    seed_anx: Path,
    python: Path,
    build_options: dict[str, Any],
    build_tools: dict[str, dict[str, str]],
    resource_stage: Path,
) -> dict[str, Any]:
    """Create machine-readable source and output identities for a cold install."""
    try:
        revision = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "HEAD"],
            check=False,
            capture_output=True,
            text=True,
        ).stdout.strip()
    except OSError:
        revision = ""
    return {
        "format": BUILD_INFO_FORMAT,
        "revision": revision or None,
        "components": {
            "yianc": {
                "source_sha256": component_source_sha256(root, "yianc"),
                "artifact_sha256": file_sha256(yianc),
                "seed_sha256": file_sha256(seed_anx),
                "builder": "python-reference",
            },
            "anx": {
                "source_sha256": component_source_sha256(root, "anx"),
                "artifact_sha256": file_sha256(anx),
                "seed_sha256": file_sha256(seed_anx),
                "builder": "staged-yianc",
            },
        },
        "reference_compiler_source_sha256": _python_compiler_source_sha256(root),
        "resources": {
            "source_sha256": resources_source_sha256(root),
            "artifact_sha256": tree_sha256(resource_stage),
        },
        "build_options": build_options,
        "build_tools": build_tools,
        "seed_anx_sha256": file_sha256(seed_anx),
        "build_python": {"path": str(python), "sha256": file_sha256(python)},
    }


def _python_compiler_source_sha256(root: Path) -> str:
    compiler_root = root / "python" / "yianc"
    if not compiler_root.is_dir():
        raise IdentityError(f"Python reference compiler sources are missing: {compiler_root}")
    paths = tuple(path.resolve() for path in compiler_root.rglob("*.py") if path.is_file())
    return paths_sha256(root, list(paths))


def dump_build_info(path: Path, document: dict[str, Any]) -> None:
    path.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8")
