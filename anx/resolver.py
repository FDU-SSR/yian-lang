"""Compatibility wrapper over :mod:`anx.project`.

The project model now lives in ``anx.project``; ``resolve`` keeps the old
``(all_files, pkg_roots)`` shape for callers that still want it.
"""

from __future__ import annotations

from pathlib import Path

from anx.project import CycleError, load

__all__ = ["CycleError", "resolve"]


def resolve(root: Path, std_lib: Path) -> tuple[list[Path], dict[str, Path]]:
    """Return the compiler's file list and package-name → source-root map.

    The file order matches the package walk order, with the standard library
    last, which is what the compiler relies on for unit numbering.
    """
    result = load(root, std_root=std_lib)
    project = result.project
    assert project is not None  # A0: load() always yields a project
    all_files = list(project.files.keys())
    pkg_roots = {name: package.source_root for name, package in project.packages.items()}
    return all_files, pkg_roots
