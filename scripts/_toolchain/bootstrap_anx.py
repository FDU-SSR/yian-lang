#!/usr/bin/env python3
"""Build the first native anx from this checkout's fixed bootstrap closure.

This is an installation helper, not a general package manager. The package
recipe is deliberately explicit so changes to the compiler bootstrap closure
fail with a useful error and require an intentional recipe update.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import NoReturn


@dataclass(frozen=True)
class PackageRecipe:
    name: str
    root: str
    kind: str
    dependencies: tuple[str, ...] = ()
    ffi: bool = False
    entry: str | None = None
    llvm_major: int | None = None
    llvm_components: tuple[str, ...] = ()
    llvm_sources: tuple[str, ...] = ()


# This traversal matches anx's manifest walk and source indexing order. Source
# files within each package are sorted before being passed to yianc.
PACKAGE_ORDER = ("anx", "yianc", "json", "llvm", "ffi", "toml", "std")
PACKAGES = {
    "anx": PackageRecipe(
        "anx", "bootstrap/anx", "hybrid", ("json", "toml", "yianc"), entry="src/main.an"
    ),
    "yianc": PackageRecipe(
        "yianc", "bootstrap/yianc", "hybrid", ("json", "llvm"), ffi=True,
        entry="src/main.an", llvm_major=22, llvm_components=("core",),
        llvm_sources=("native/host.c",),
    ),
    "json": PackageRecipe("json", "bootstrap/json", "lib"),
    "llvm": PackageRecipe(
        "llvm", "bootstrap/llvm", "lib", ("ffi",), ffi=True, llvm_major=22,
        llvm_components=("core", "analysis", "bitwriter", "target", "nativecodegen", "passes"),
        llvm_sources=("native/target.c",),
    ),
    "ffi": PackageRecipe("ffi", "bootstrap/ffi", "lib", ffi=True),
    "toml": PackageRecipe("toml", "bootstrap/toml", "lib"),
    "std": PackageRecipe("std", "lib", "lib", ffi=True),
}


class BootstrapError(Exception):
    """A fixed bootstrap recipe no longer matches the checkout."""


@dataclass(frozen=True)
class BootstrapRecipe:
    package_map: dict[str, object]
    source_files: tuple[Path, ...]


def _fail(message: str) -> NoReturn:
    raise BootstrapError(message)


def _manifest(root: Path, recipe: PackageRecipe) -> dict[str, object]:
    package_root = root / recipe.root
    manifest_path = package_root / "package.anx"
    if not package_root.is_dir():
        _fail(f"bootstrap package root is missing: {package_root}")
    if not (package_root / "src").is_dir():
        _fail(f"bootstrap package source root is missing: {package_root / 'src'}")
    try:
        with manifest_path.open("rb") as stream:
            document = tomllib.load(stream)
    except OSError as error:
        _fail(f"cannot read bootstrap manifest {manifest_path}: {error}")
    except tomllib.TOMLDecodeError as error:
        _fail(f"cannot parse bootstrap manifest {manifest_path}: {error}")

    package = document.get("package")
    if not isinstance(package, dict):
        _fail(f"bootstrap manifest has no [package] table: {manifest_path}")
    if package.get("name") != recipe.name or package.get("kind") != recipe.kind:
        _fail(
            f"fixed bootstrap recipe mismatch in {manifest_path}: "
            f"expected name={recipe.name!r}, kind={recipe.kind!r}"
        )

    raw_dependencies = document.get("dependencies", {})
    if not isinstance(raw_dependencies, dict):
        _fail(f"dependencies must be a table in {manifest_path}")
    expected_paths = {
        name: str((root / PACKAGES[name].root).resolve())
        for name in recipe.dependencies
    }
    actual_paths: dict[str, str] = {}
    for name, value in raw_dependencies.items():
        if not isinstance(value, dict) or not isinstance(value.get("path"), str):
            _fail(f"dependency {name!r} must use a fixed path in {manifest_path}")
        actual_paths[name] = str((package_root / value["path"]).resolve())
    if actual_paths != expected_paths:
        _fail(
            f"fixed dependency closure changed in {manifest_path}: "
            f"expected {expected_paths}, found {actual_paths}"
        )
    if document.get("dev-dependencies", {}):
        _fail(f"bootstrap packages cannot add development dependencies: {manifest_path}")

    actual_ffi = package.get("ffi", False)
    if type(actual_ffi) is not bool or actual_ffi is not recipe.ffi:
        _fail(f"fixed FFI permission changed in {manifest_path}")

    if recipe.entry is None:
        if package.get("entry") is not None:
            _fail(f"fixed library entry changed in {manifest_path}")
        entry_path = None
    else:
        actual_entry = package.get("entry", "src/main.an")
        if actual_entry != recipe.entry:
            _fail(f"fixed package entry changed in {manifest_path}: {actual_entry!r}")
        entry_path = package_root / recipe.entry
        if not entry_path.is_file():
            _fail(f"bootstrap package entry is missing: {entry_path}")

    expected_native = None
    if recipe.llvm_major is not None:
        expected_native = {
            "major": recipe.llvm_major,
            "components": list(recipe.llvm_components),
            "sources": list(recipe.llvm_sources),
        }
    raw_native_table = document.get("native", {})
    if not isinstance(raw_native_table, dict):
        _fail(f"native metadata must be a table in {manifest_path}")
    raw_native = raw_native_table.get("llvm")
    if raw_native != expected_native:
        _fail(f"fixed native LLVM metadata changed in {manifest_path}")

    result: dict[str, object] = {
        "sourceRoot": str((package_root / "src").resolve()),
        "kind": recipe.kind,
        "dependencies": sorted(recipe.dependencies),
        "ffi": recipe.ffi,
    }
    if entry_path is not None:
        result["entry"] = str(entry_path.resolve())
    if expected_native is not None:
        for source in recipe.llvm_sources:
            if not (package_root / source).is_file():
                _fail(f"native LLVM shim is missing: {package_root / source}")
        result["nativeLLVM"] = expected_native
    return result


def build_recipe(checkout: Path) -> BootstrapRecipe:
    """Validate the fixed manifest closure and produce the seed inputs."""
    root = checkout.resolve()
    packages: dict[str, object] = {}
    source_files: list[Path] = []
    for name in PACKAGE_ORDER:
        recipe = PACKAGES[name]
        packages[name] = _manifest(root, recipe)
        source_root = root / recipe.root / "src"
        files = sorted(path.resolve() for path in source_root.rglob("*.an") if path.is_file())
        if not files:
            _fail(f"bootstrap package has no .an sources: {source_root}")
        source_files.extend(files)
    return BootstrapRecipe(
        package_map={"format": 2, "root": "anx", "packages": packages},
        source_files=tuple(source_files),
    )


def compiler_arguments(recipe: BootstrapRecipe, map_path: Path, output: Path) -> list[str]:
    """Return the exact Python yianc argv for the one fixed seed package."""
    return [
        "--packages", str(map_path), "-t", "exe", "-O2", "-o", str(output),
        *(str(path) for path in recipe.source_files),
    ]


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--python", required=True, help="Python interpreter with yianc dependencies")
    parser.add_argument("--output", required=True, type=Path, help="absolute seed anx output path")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    root = Path(__file__).resolve().parents[2]
    output = args.output
    if not output.is_absolute():
        print("error: --output must be an absolute path", file=sys.stderr)
        return 2
    if output.exists():
        print(f"error: refusing to replace existing seed output: {output}", file=sys.stderr)
        return 2

    try:
        recipe = build_recipe(root)
        output.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="yian-native-anx-seed-") as temporary:
            map_path = Path(temporary) / "package-map.json"
            map_path.write_text(json.dumps(recipe.package_map, indent=2) + "\n", encoding="utf-8")
            environment = os.environ.copy()
            environment["PYTHONPATH"] = str(root / "python")
            environment["YIAN_ROOT"] = str(root)
            environment["YIAN_LIB"] = str(root / "lib" / "src")
            environment.pop("YIANC", None)
            command = [args.python, "-m", "yianc.main", *compiler_arguments(recipe, map_path, output)]
            completed = subprocess.run(command, cwd=root, env=environment, check=False)
            if completed.returncode != 0:
                output.unlink(missing_ok=True)
                return completed.returncode
        if not output.is_file():
            _fail(f"Python yianc succeeded without creating the seed executable: {output}")
        return 0
    except (BootstrapError, OSError, subprocess.SubprocessError) as error:
        output.unlink(missing_ok=True)
        print(f"error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
