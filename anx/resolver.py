"""Compatibility wrapper over :mod:`anx.project`.

The project model lives in ``anx.project``; ``resolve`` keeps the old
``(all_files, pkg_roots)`` shape for callers that still want it.  New code
should call :func:`anx.project.load` and handle its diagnostics.
"""

from __future__ import annotations

from pathlib import Path

from anx.diagnostics import Diagnostic, format_diagnostic
from anx.project import CycleError, load

__all__ = ["CycleError", "ProjectError", "resolve"]


class ProjectError(Exception):
    """Raised by :func:`resolve` when loading produced diagnostics."""

    def __init__(self, diagnostics: tuple[Diagnostic, ...]) -> None:
        self.diagnostics = diagnostics
        super().__init__("\n".join(format_diagnostic(d) for d in diagnostics))


def resolve(root: Path, std_lib: Path) -> tuple[list[Path], dict[str, Path]]:
    """Return the compiler's file list and package-name → source-root map.

    The file order matches the package walk order, with the standard library
    last, which is what the compiler relies on for unit numbering.
    """
    result = load(root, std_root=std_lib)
    if result.diagnostics:
        raise ProjectError(result.diagnostics)
    project = result.project
    if project is None:
        raise ProjectError(result.diagnostics)
    all_files = list(project.files.keys())
    pkg_roots = {name: package.source_root for name, package in project.packages.items()}
    return all_files, pkg_roots
