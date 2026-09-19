"""References, rename and quick fixes as protocol payloads.

The compiler side decides which spans a symbol occupies
(:mod:`compiler.analysis.refactor`) and where every import statement begins and
ends (:class:`~compiler.analysis.view.AnalysisView`); this module turns that into
a `Location` list, a `DocumentHighlight` list, a `WorkspaceEdit`, and
`CodeAction`s — and gives a refusal a message the user can read instead of an edit
nobody asked for.

The "remove this import" fix lives here rather than on the compiler side because
it is a decision about *editing text*: which diagnostics deserve an action, and
which bytes have to go so the remaining statement still parses.  The facts it
works from — the diagnostics, the statement's extent, the tokens inside it — all
come from the analysis.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from lsprotocol import types

from compiler.analysis.diagnostics import Diagnostic
from compiler.analysis.navigation import Navigator
from compiler.analysis.positions import path_to_uri, to_lsp_range
from compiler.analysis.refactor import ReferenceResult, RenameResult
from compiler.analysis.session import AnalysisResult
from compiler.frontend.lex import token as Tok
from compiler.frontend.lex.position import SrcPosition, SrcSpan

__all__ = [
    "ImportRemoval",
    "code_actions",
    "document_highlights",
    "import_removals",
    "locations",
    "to_range",
    "workspace_edit",
]

#: Diagnostic codes that mean "this import cannot work"; each is a candidate for
#: the "remove this import" quick fix.  Offering an edit for a code is the
#: editor's decision — the codes themselves are the compiler's.
IMPORT_FAILURE_CODES = frozenset(
    {
        "E304",  # the imported name is not found in that module
        "E307",  # the target is a package entry module
        "E308",  # unknown package
        "E309",  # the package is not a declared dependency
        "E310",  # the path is not a module
    }
)


@dataclass(frozen=True)
class ImportRemoval:
    """An import that cannot work, and the span whose removal fixes it.

    For ``from X import a, b;`` where only ``a`` fails, the span covers ``a`` and
    its separator, not the whole statement: the valid import stays.
    """

    diagnostic: Diagnostic
    span: SrcSpan


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
    symbols are untouched.
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



# ── the "remove this import" fix ───────────────────────────────────────────────


def import_removals(
    result: AnalysisResult, navigator: Navigator, path: Path
) -> tuple[ImportRemoval, ...]:
    """Quick fixes for import statements in *path* that cannot work.

    A broken import is reported against a span inside the statement; the fix
    removes the whole statement (or one name out of several, with the separator
    next to it).  The statement's extent comes from the parser, which is the only
    component that saw its terminator; this function reads the tokens *inside*
    that extent to find the separators.
    """
    view = navigator.view
    tokens = view.tokens_of(path)
    statements = view.import_statements(path)
    if not tokens or not statements:
        return ()
    removals: list[ImportRemoval] = []
    for diagnostic in result.diagnostics:
        if diagnostic.code not in IMPORT_FAILURE_CODES:
            continue
        if diagnostic.span.path.resolve() != path.resolve():
            continue
        statement = next(
            (span for span in statements if __contains(span, diagnostic.span)), None
        )
        if statement is None:
            continue
        span = __import_removal_span(tokens, path, diagnostic.span, statement)
        if span is not None:
            removals.append(ImportRemoval(diagnostic=diagnostic, span=span))
    return tuple(removals)


def __import_removal_span(
    tokens: tuple[Tok.Token, ...], path: Path, diagnostic: SrcSpan, statement: SrcSpan
) -> SrcSpan | None:
    """The span to delete to make *diagnostic*'s import go away.

    Whole statements are removed for `import a.b.c;` and for the path of a
    `from` import; a single name out of several is removed together with the
    separator next to it, so the rest of the statement stays valid.
    """
    inside = [
        token
        for token in tokens
        if token.span.path.resolve() == path.resolve()
        and __is_real(token.span)
        and __contains(statement, token.span)
    ]
    if not inside:
        return None
    whole = statement
    keyword = __keyword_index(inside, Tok.KeywordKind.From)
    import_index = __keyword_index(inside, Tok.KeywordKind.Import)
    if keyword is None or import_index is None:
        return whole
    if __before(diagnostic, inside[import_index].span):
        # The failure is in the module path, not in one of the imported names.
        return whole
    items = __comma_items(inside[import_index + 1 :])
    if len(items) <= 1:
        return whole
    for index, item in enumerate(items):
        if item and __contains(item[0].span, diagnostic) or (item and __contains(item[-1].span, diagnostic)):
            return __item_removal(tokens, items, index)
    return whole


def __item_removal(
    tokens: tuple[Tok.Token, ...], items: list[list[Tok.Token]], index: int
) -> SrcSpan:
    """The span of one comma-separated import item plus its separator."""
    item = items[index]
    span = SrcSpan(item[0].span.start.clone(), item[-1].span.end.clone())
    if index + 1 < len(items):
        # The separator after the item goes with it, and so does the space the
        # separator left behind: `import Nope, Meters;` must not become
        # `import  Meters;`.  The lexer drops whitespace, so the gap is measured
        # between the comma and the next token when they share a line.
        end = __comma_after(tokens, span)
        following = next(
            (
                token
                for token in tokens
                if (token.span.start.row, token.span.start.col) > (end.row, end.col)
            ),
            None,
        )
        if following is not None and following.span.start.row == end.row:
            end = following.span.start
        return SrcSpan(span.start.clone(), end.clone())
    # The last item: the separator before it is the one to remove, or
    # `import a, b;` would become `import a, ;`.
    return SrcSpan(__comma_before(tokens, span).clone(), span.end.clone())


def __comma_items(tokens: list[Tok.Token]) -> list[list[Tok.Token]]:
    """Split tokens into comma-separated groups."""
    items: list[list[Tok.Token]] = []
    current: list[Tok.Token] = []
    for token in tokens:
        if isinstance(token, Tok.Punctuator) and token.kind in (
            Tok.PunctuatorKind.Comma,
            Tok.PunctuatorKind.Semicolon,
        ):
            items.append(current)
            current = []
            continue
        current.append(token)
    if current:
        items.append(current)
    return items


def __keyword_index(tokens: list[Tok.Token], keyword: Tok.KeywordKind) -> int | None:
    for index, token in enumerate(tokens):
        if isinstance(token, Tok.Keyword) and token.kind == keyword:
            return index
    return None


def __comma_before(tokens: tuple[Tok.Token, ...], span: SrcSpan) -> SrcPosition:
    for token in reversed(tokens):
        if isinstance(token, Tok.Punctuator) and token.kind == Tok.PunctuatorKind.Comma:
            if (token.span.start.row, token.span.start.col) <= (span.start.row, span.start.col):
                return token.span.start
    return span.start


def __comma_after(tokens: tuple[Tok.Token, ...], span: SrcSpan) -> SrcPosition:
    for token in tokens:
        if isinstance(token, Tok.Punctuator) and token.kind == Tok.PunctuatorKind.Comma:
            if (token.span.start.row, token.span.start.col) >= (span.end.row, span.end.col):
                return token.span.end
    return span.end


def __before(position: SrcSpan, reference: SrcSpan) -> bool:
    """True when *position* starts before *reference* does."""
    return (position.start.row, position.start.col) < (reference.start.row, reference.start.col)


def __is_real(span: SrcSpan) -> bool:
    return not (span.start.row == 0 and span.start.col == 0 and span.end.row == 0 and span.end.col == 0)


def __contains(span: SrcSpan, inner: SrcSpan) -> bool:
    return (span.start.row, span.start.col) <= (inner.start.row, inner.start.col) and (
        inner.end.row,
        inner.end.col,
    ) <= (span.end.row, span.end.col)
