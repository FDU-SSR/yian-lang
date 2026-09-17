"""Parsing and validation of ``package.anx`` manifests.

A manifest never raises on user error: :func:`read_manifest` returns the parsed
manifest together with every diagnostic it can report on its own, so that
``load()`` can collect diagnostics from a whole dependency graph in one pass
(docs/plan/anx-design.md §3.2, §4.1, §7.2).
"""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from anx.diagnostics import (
    AX_BAD_MANIFEST,
    AX_RESERVED_PACKAGE_NAME,
    Diagnostic,
    RESERVED_PACKAGE_NAMES,
    sort_diagnostics,
)

MANIFEST_NAME = "package.anx"
DEFAULT_VERSION = "0.1.0"
DEFAULT_ENTRY = "src/main.an"

#: Accepted ``[package].kind`` values, in the order the manual lists them.
KINDS = ("bin", "lib", "hybrid")

__IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")
__VERSION = re.compile(r"[0-9]+(?:\.[0-9]+)*(?:[-+][0-9A-Za-z.-]+)?\Z")


@dataclass(frozen=True)
class DependencySpec:
    """One ``[dependencies]`` or ``[dev-dependencies]`` entry as written.

    ``key`` is the name used in the manifest; ``path`` is already resolved
    against the manifest's directory.  Whether the key matches the dependency's
    own package name is checked against the dependency's manifest (§3.5).
    """

    key: str
    path: Path


@dataclass(frozen=True)
class Manifest:
    name: str
    kind: str  # "bin" | "lib" | "hybrid"; validated against KINDS
    entry: str | None  # raw, relative to the package root
    version: str
    dependencies: tuple[DependencySpec, ...]
    #: Resolved like ``dependencies`` but visible only to ``anx test`` (Cargo's
    #: dev-dependencies): project code cannot import them and a normal build
    #: does not compile them.
    dev_dependencies: tuple[DependencySpec, ...]


def __as_table(value: object) -> dict[str, object] | None:
    """Return *value* as a string-keyed table, or ``None`` when it is not one.

    ``tomllib`` hands back ``dict[str, Any]``; narrowing through this helper
    keeps the unknown types out of the validation logic.
    """
    if not isinstance(value, dict):
        return None
    table: dict[str, object] = {}
    for key, item in cast("dict[object, object]", value).items():
        if not isinstance(key, str):
            return None
        table[key] = item
    return table


def read_manifest(path: Path) -> tuple[Manifest | None, tuple[Diagnostic, ...]]:
    """Read and validate the manifest at *path*.

    Returns ``(None, diagnostics)`` when the manifest is unusable as a whole
    (unreadable, unparseable, or missing an acceptable ``name``/``kind``); in
    that case the caller skips the package rather than guessing.  Otherwise the
    manifest is returned even if some diagnostics remain — a bad dependency
    entry only drops that entry.
    """
    try:
        with open(path, "rb") as handle:
            data = __as_table(tomllib.load(handle))
    except tomllib.TOMLDecodeError as exc:
        return None, (Diagnostic(AX_BAD_MANIFEST, f"Could not parse manifest: {exc}", path),)
    except OSError as exc:
        return None, (Diagnostic(AX_BAD_MANIFEST, f"Could not read manifest: {exc}", path),)
    if data is None:
        return None, (Diagnostic(AX_BAD_MANIFEST, "Manifest is not a TOML table", path),)

    table = __as_table(data.get("package"))
    if table is None:
        return None, (Diagnostic(AX_BAD_MANIFEST, "Missing [package] table", path),)

    diagnostics: list[Diagnostic] = []

    name_value = table.get("name")
    name = name_value if isinstance(name_value, str) else ""
    if name == "":
        diagnostics.append(Diagnostic(AX_BAD_MANIFEST, "Missing or empty [package].name", path))
    elif not __IDENTIFIER.match(name):
        diagnostics.append(
            Diagnostic(AX_BAD_MANIFEST, f"'{name}' is not a valid package name", path)
        )
    elif name in RESERVED_PACKAGE_NAMES:
        diagnostics.append(
            Diagnostic(
                AX_RESERVED_PACKAGE_NAME,
                f"Package name '{name}' is reserved by the standard library",
                path,
                hint="Pick another name; 'std' always refers to the bundled standard library.",
            )
        )
    name_ok = bool(name) and __IDENTIFIER.match(name) is not None

    kind_value = table.get("kind", KINDS[0])
    kind = kind_value if isinstance(kind_value, str) else ""
    if kind not in KINDS:
        diagnostics.append(
            Diagnostic(
                AX_BAD_MANIFEST,
                f"[package].kind must be one of {', '.join(repr(k) for k in KINDS)}",
                path,
            )
        )
    kind_ok = kind in KINDS

    entry_value = table.get("entry")
    entry: str | None = None
    if entry_value is not None:
        if isinstance(entry_value, str) and entry_value != "":
            entry = entry_value
        else:
            diagnostics.append(
                Diagnostic(AX_BAD_MANIFEST, "[package].entry must be a non-empty string", path)
            )

    version_value = table.get("version", DEFAULT_VERSION)
    if isinstance(version_value, str) and __VERSION.match(version_value):
        version = version_value
    else:
        version = DEFAULT_VERSION
        diagnostics.append(
            Diagnostic(
                AX_BAD_MANIFEST,
                '[package].version must be a version string such as "0.1.0"',
                path,
            )
        )

    dependencies, dependency_diagnostics = __read_dependencies(data, path, "dependencies")
    diagnostics.extend(dependency_diagnostics)
    dev_dependencies, dev_diagnostics = __read_dependencies(data, path, "dev-dependencies")
    diagnostics.extend(dev_diagnostics)

    if not name_ok or not kind_ok:
        return None, sort_diagnostics(diagnostics)

    manifest = Manifest(
        name=name,
        kind=kind,
        entry=entry,
        version=version,
        dependencies=dependencies,
        dev_dependencies=dev_dependencies,
    )
    return manifest, sort_diagnostics(diagnostics)


def __read_dependencies(
    data: dict[str, object], path: Path, table: str
) -> tuple[tuple[DependencySpec, ...], tuple[Diagnostic, ...]]:
    """Parse one dependency table (``dependencies`` or ``dev-dependencies``)."""
    diagnostics: list[Diagnostic] = []
    specs: list[DependencySpec] = []
    raw = __as_table(data.get(table, {}))
    if raw is None:
        diagnostics.append(Diagnostic(AX_BAD_MANIFEST, f"[{table}] must be a table", path))
        return (), tuple(diagnostics)

    for key, value in raw.items():
        if not __IDENTIFIER.match(key):
            diagnostics.append(
                Diagnostic(AX_BAD_MANIFEST, f"'{key}' is not a valid dependency name", path)
            )
            continue
        entry = __as_table(value)
        dep_path = entry.get("path") if entry is not None else None
        if not isinstance(dep_path, str) or dep_path == "":
            diagnostics.append(
                Diagnostic(
                    AX_BAD_MANIFEST,
                    f"Dependency '{key}' in [{table}] must set a non-empty string 'path'",
                    path,
                )
            )
            continue
        specs.append(DependencySpec(key=key, path=(path.parent / dep_path).resolve()))

    return tuple(specs), tuple(diagnostics)
