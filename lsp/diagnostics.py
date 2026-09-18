"""Compiler diagnostics as LSP diagnostics (plan §7 P4).

The conversion is deliberately narrow: the compiler's
:class:`~compiler.analysis.diagnostics.Diagnostic` already carries everything the
editor needs (code, severity, message, span), so this module only maps it onto
the protocol shape and hands the span to :func:`~compiler.analysis.positions.to_lsp_range`,
the single place that knows the two position models differ (plan §5.1).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from pathlib import Path

from lsprotocol import types

from compiler.analysis.diagnostics import Diagnostic, Severity
from compiler.analysis.positions import to_lsp_range

__all__ = ["SOURCE_NAME", "diagnostics_by_document", "to_lsp_diagnostic"]

#: `source` shown in the Problems panel; also how a diagnostic is attributed when
#: several language servers report on the same file.
SOURCE_NAME = "yian"

__SEVERITY = {
    Severity.ERROR: types.DiagnosticSeverity.Error,
    Severity.WARNING: types.DiagnosticSeverity.Warning,
    Severity.INFORMATION: types.DiagnosticSeverity.Information,
    Severity.HINT: types.DiagnosticSeverity.Hint,
}


def to_lsp_diagnostic(diagnostic: Diagnostic, text: str) -> types.Diagnostic:
    """Render one compiler diagnostic for the client.

    *text* is the document the range is measured against — the in-memory version
    during an edit, so the range is right even though the file on disk is older.
    """
    span = to_lsp_range(diagnostic.span, text)
    return types.Diagnostic(
        range=types.Range(
            start=types.Position(
                line=span["start"]["line"], character=span["start"]["character"]
            ),
            end=types.Position(line=span["end"]["line"], character=span["end"]["character"]),
        ),
        severity=__SEVERITY.get(diagnostic.severity, types.DiagnosticSeverity.Error),
        code=diagnostic.code,
        source=SOURCE_NAME,
        message=diagnostic.message,
    )


def diagnostics_by_document(
    diagnostics: Iterable[Diagnostic], texts: Mapping[Path, str]
) -> dict[Path, list[types.Diagnostic]]:
    """Group compiler diagnostics by the document they belong to.

    The client is sent one `publishDiagnostics` per document, so a whole-program
    analysis has to be split by span path.  *texts* is the analyzed text of each
    file; a diagnostic that points outside it (an import that resolves nowhere)
    is rendered against an empty document instead of being dropped.
    """
    grouped: dict[Path, list[types.Diagnostic]] = {}
    for diagnostic in diagnostics:
        path = diagnostic.span.path.resolve()
        grouped.setdefault(path, []).append(to_lsp_diagnostic(diagnostic, texts.get(path, "")))
    return grouped
