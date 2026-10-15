"""Diagnostic shape and the error-code catalogue shared by the project loader.

Codes are declared here once so that the loader, the command line and the
language server agree on the strings they print and assert on.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

# --- Error codes -----------------------------------------------------------
AX_NO_PROJECT_ROOT = "AX001"
AX_BAD_MANIFEST = "AX002"
AX_DEPENDENCY_PATH_MISSING = "AX003"
AX_DEPENDENCY_MANIFEST_MISSING = "AX004"
AX_DEPENDENCY_NAME_MISMATCH = "AX005"
AX_DEPENDENCY_CYCLE = "AX006"
AX_DUPLICATE_PACKAGE = "AX007"
AX_ENTRY_MISMATCH = "AX008"
AX_UNDECLARED_DEPENDENCY = "AX009"
AX_IMPORT_NOT_A_MODULE = "AX010"
AX_RESERVED_PACKAGE_NAME = "AX011"
AX_UNKNOWN_PACKAGE = "AX012"
AX_NESTED_SOURCE_ROOTS = "AX013"
AX_IMPORT_ENTRY_MODULE = "AX014"
AX_BIN_AS_DEPENDENCY = "AX015"

#: Package names the loader refuses to assign to a user package.
RESERVED_PACKAGE_NAMES = frozenset({"std"})


@dataclass(frozen=True)
class Diagnostic:
    """A structured project diagnostic."""

    code: str
    message: str
    path: Path | None = None
    span: tuple[int, int] | None = None
    hint: str | None = None


def __sort_key(diagnostic: Diagnostic) -> tuple[int, str, int, str, str]:
    """Sort by path (``None`` first), then span start, then code."""
    has_path = diagnostic.path is not None
    path = str(diagnostic.path) if diagnostic.path is not None else ""
    span_start = diagnostic.span[0] if diagnostic.span is not None else -1
    return (1 if has_path else 0, path, span_start, diagnostic.code, diagnostic.message)


def sort_diagnostics(diagnostics: Iterable[Diagnostic]) -> tuple[Diagnostic, ...]:
    """Return *diagnostics* in the stable order callers may compare."""
    return tuple(sorted(diagnostics, key=__sort_key))


def format_diagnostic(diagnostic: Diagnostic) -> str:
    """Render one diagnostic as the ``error[AXnnn]: ...`` text the CLI prints."""
    location = f"{diagnostic.path}: " if diagnostic.path is not None else ""
    text = f"error[{diagnostic.code}]: {location}{diagnostic.message}"
    if diagnostic.hint is not None:
        text += f"\n  hint: {diagnostic.hint}"
    return text


def diagnostic_payload(diagnostic: Diagnostic) -> dict[str, object]:
    """The JSON shape of one diagnostic, for structured output (``anx graph``)."""
    return {
        "code": diagnostic.code,
        "message": diagnostic.message,
        "path": None if diagnostic.path is None else str(diagnostic.path),
        "span": None if diagnostic.span is None else list(diagnostic.span),
        "hint": diagnostic.hint,
    }
