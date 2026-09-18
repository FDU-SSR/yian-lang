"""References, rename and quick fixes as protocol payloads (plan §7 P7).

The compiler side decides which spans a symbol occupies
(:mod:`compiler.analysis.refactor`); this module turns that into a `Location`
list, a `DocumentHighlight` list, a `WorkspaceEdit`, and `CodeAction`s — and
gives a refusal a message the user can read instead of an edit nobody asked for.
"""

from __future__ import annotations

from pathlib import Path

from lsprotocol import types

from compiler.analysis.navigation import Navigator
from compiler.analysis.positions import path_to_uri, to_lsp_range
from compiler.analysis.refactor import ImportRemoval, ReferenceResult, RenameResult
from compiler.frontend.lex.position import SrcSpan

__all__ = [
    "code_actions",
    "document_highlights",
    "locations",
    "to_range",
    "workspace_edit",
]


def to_range(span: SrcSpan, navigator: Navigator) -> types.Range:
    """Convert a compiler span to an LSP range, in the span's own document."""
    rendered = to_lsp_range(span, navigator.text_of(span.path))
    return types.Range(
        start=types.Position(
            line=rendered["start"]["line"], character=rendered["start"]["character"]
        ),
        end=types.Position(line=rendered["end"]["line"], character=rendered["end"]["character"]),
    )


def locations(result: ReferenceResult, navigator: Navigator) -> list[types.Location]:
    """Every naming of the symbol, as jump targets."""
    return [
        types.Location(uri=path_to_uri(site.span.path), range=to_range(site.span, navigator))
        for site in result.sites
    ]


def document_highlights(result: ReferenceResult, navigator: Navigator) -> list[types.DocumentHighlight]:
    """Reference highlighting for one file.

    A declaration reads as a write and a use as a read, which is how the editor
    can style them differently.
    """
    return [
        types.DocumentHighlight(
            range=to_range(site.span, navigator),
            kind=(
                types.DocumentHighlightKind.Write
                if site.declaration
                else types.DocumentHighlightKind.Read
            ),
        )
        for site in result.sites
    ]


def workspace_edit(result: RenameResult, navigator: Navigator) -> types.WorkspaceEdit:
    """The rename as one atomic edit, which is what makes it undoable as a whole.

    Edits are grouped per file, and each one replaces the symbol's *name span*:
    nothing is matched by text, so strings, comments and unrelated same-named
    symbols are untouched (plan §7 P7).
    """
    changes: dict[str, list[types.TextEdit]] = {}
    for edit in result.edits:
        uri = path_to_uri(edit.path)
        changes.setdefault(uri, []).append(
            types.TextEdit(range=to_range(edit.span, navigator), new_text=edit.new_name)
        )
    return types.WorkspaceEdit(changes=changes)


def code_actions(
    removals: tuple[ImportRemoval, ...],
    navigator: Navigator,
    path: Path,
) -> list[types.CodeAction]:
    """Quick fixes for the file, each carrying the diagnostic it addresses.

    `CodeAction.diagnostics` is what links an action to the problem it fixes, so
    the editor can offer it from the Problems panel or the light bulb.
    """
    actions: list[types.CodeAction] = []
    for removal in removals:
        if removal.span.path.resolve() != path.resolve():
            continue
        actions.append(
            types.CodeAction(
                title=f"Remove this import: {removal.diagnostic.message.splitlines()[0]}",
                kind=types.CodeActionKind.QuickFix,
                diagnostics=[
                    types.Diagnostic(
                        range=to_range(removal.diagnostic.span, navigator),
                        message=removal.diagnostic.message,
                        code=removal.diagnostic.code,
                        source="yian",
                    )
                ],
                edit=types.WorkspaceEdit(
                    changes={
                        path_to_uri(path): [
                            types.TextEdit(
                                range=to_range(removal.span, navigator), new_text=""
                            )
                        ]
                    }
                ),
            )
        )
    return actions

