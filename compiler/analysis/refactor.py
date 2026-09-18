"""Finding, renaming and quick-fixing code (plan §7 P7).

Everything here is based on **symbol identity**, never on the text: a reference is
a name the analysis resolved to a particular declaration, so two same-named
symbols in different scopes stay apart, and a rename edits exactly those spans.
That is also why a rename can refuse: when the analysis cannot account for every
use of the name, editing some of them would leave the program half-renamed, and
refusing is the safe answer (plan §7 P7: 无法确认符号身份时拒绝批量修改).

Formatting is deliberately absent — see the project readme for the evaluation;
the short version is that the AST keeps no comment or whitespace trivia, so a
formatter would need a separate token-preserving pass.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from compiler.analysis.diagnostics import Diagnostic
from compiler.analysis.navigation import Navigator, Target
from compiler.analysis.session import AnalysisResult
from compiler.frontend.lex import token as Tok
from compiler.frontend.parse import ast as AST
from compiler.frontend.lex.position import SrcPosition, SrcSpan

__all__ = [
    "ImportRemoval",
    "ReferenceSite",
    "RenameEdit",
    "RenameResult",
    "find_references",
    "import_removal_actions",
    "rename",
]

#: Diagnostic codes that mean "this import cannot work"; each is a candidate for
#: the "remove this import" quick fix.
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
class ReferenceSite:
    """One place a symbol is named."""

    span: SrcSpan
    #: True for the declaration itself rather than a use.
    declaration: bool = False


@dataclass(frozen=True)
class ReferenceResult:
    """A symbol and every place it is named."""

    target: Target
    sites: tuple[ReferenceSite, ...]


@dataclass(frozen=True)
class RenameEdit:
    """One file's worth of rename edits."""

    path: Path
    #: The span to replace and what to put there.
    span: SrcSpan
    new_name: str


@dataclass(frozen=True)
class RenameResult:
    """Either the edits, or the reason the rename was refused."""

    edits: tuple[RenameEdit, ...] = ()
    refusal: str | None = None
    target: Target | None = None

    @property
    def ok(self) -> bool:
        return self.refusal is None


@dataclass(frozen=True)
class ImportRemoval:
    """An import that cannot work, and the span whose removal fixes it.

    For ``from X import a, b;`` where only ``a`` fails, the span covers ``a`` and
    its separator, not the whole statement: the valid import stays.
    """

    diagnostic: Diagnostic
    span: SrcSpan


def find_references(
    result: AnalysisResult,
    path: Path,
    row: int,
    col: int,
    *,
    include_declaration: bool = True,
    std_root: Path | None = None,
) -> ReferenceResult | None:
    """Every recorded naming of the symbol at ``(row, col)``.

    A reference counts when it resolved to the *same declaration* as the position
    does, which is symbol identity: a local `x` and a global `x` never mix.
    """
    navigator = Navigator(result, std_root=std_root)
    resolution = navigator.resolve(path, row, col)
    if resolution is None or resolution.target is None:
        return None
    target = resolution.target
    key = __span_key(target.span)
    sites: list[ReferenceSite] = []
    for reference in navigator.all_references():
        resolved = navigator.target_of(reference.target)
        if resolved is not None and __span_key(resolved.span) == key:
            sites.append(ReferenceSite(span=reference.span))
    if include_declaration:
        for declaration in navigator.declarations_in(target.span.path):
            span = declaration.span
            if span is not None and __span_key(span) == key:
                sites.append(ReferenceSite(span=span, declaration=True))
    return ReferenceResult(target=target, sites=tuple(__sorted_sites(sites)))


def rename(
    result: AnalysisResult,
    path: Path,
    row: int,
    col: int,
    new_name: str,
    *,
    std_root: Path | None = None,
) -> RenameResult:
    """Rename the symbol at ``(row, col)``, or explain why it is not safe.

    The edits cover the declaration, every resolved use, and the imported name of
    an aliased import.  A use spelled with a different name than the declaration
    (an alias) is left alone: only the import's target name changes there.  When
    the analysis cannot account for the name — a definition it never checked might
    use it — the rename is refused rather than left half-done.
    """
    if not __is_identifier(new_name):
        return RenameResult(refusal=f"'{new_name}' is not a valid identifier")
    references = find_references(result, path, row, col, std_root=std_root)
    if references is None:
        return RenameResult(refusal="nothing to rename here")
    target = references.target
    if target.stdlib:
        return RenameResult(
            refusal=f"'{target.name}' is declared in the standard library", target=target
        )
    unchecked = __unchecked_name_uses(result, target)
    if unchecked:
        where = unchecked[0]
        return RenameResult(
            refusal=(
                f"a definition the analysis did not check uses '{target.name}' "
                f"({where.path.name}:{where.start.row + 1}); rename it there first"
            ),
            target=target,
        )
    edits: list[RenameEdit] = []
    for site in references.sites:
        if not site.declaration and __name_at(result, site.span) != target.name:
            # An alias: the local spelling is a different name, and only the
            # import statement that introduced it changes.
            continue
        edits.append(RenameEdit(path=site.span.path, span=site.span, new_name=new_name))
    if not edits:
        return RenameResult(refusal=f"nothing to rename for '{target.name}'", target=target)
    return RenameResult(edits=tuple(edits), target=target)


def import_removal_actions(result: AnalysisResult, path: Path) -> tuple[ImportRemoval, ...]:
    """Quick fixes for import statements that cannot work.

    A broken import is reported against a span inside the statement; the fix is to
    remove the whole statement, whose extent comes from the token stream (the AST
    keeps only the ``from``/``import`` keyword's span).
    """
    tokens = result.tokens.get(path.resolve())
    if tokens is None:
        return ()
    removals: list[ImportRemoval] = []
    for diagnostic in result.diagnostics:
        if diagnostic.code not in IMPORT_FAILURE_CODES:
            continue
        if diagnostic.span.path.resolve() != path.resolve():
            continue
        span = __import_removal_span(path, tokens, diagnostic.span)
        if span is not None:
            removals.append(ImportRemoval(diagnostic=diagnostic, span=span))
    return tuple(removals)


# ── internals ─────────────────────────────────────────────────────────────────


def __import_removal_span(
    path: Path, tokens: tuple[Tok.Token, ...], diagnostic: SrcSpan
) -> SrcSpan | None:
    """The span to delete to make *diagnostic*'s import go away.

    Whole statements are removed for `import a.b.c;` and for the path of a
    `from` import; a single name out of several is removed together with the
    separator next to it, so the rest of the statement stays valid.
    """
    file_tokens = [
        token
        for token in tokens
        if token.span.path.resolve() == path.resolve() and __is_real(token.span)
    ]
    statement = __statement_at(file_tokens, diagnostic.start)
    if not statement:
        return None
    whole = SrcSpan(statement[0].span.start.clone(), statement[-1].span.end.clone())
    keyword = __keyword_index(statement, Tok.KeywordKind.From)
    import_index = __keyword_index(statement, Tok.KeywordKind.Import)
    if keyword is None or import_index is None:
        return whole
    if __before(diagnostic, statement[import_index].span):
        # The failure is in the module path, not in one of the imported names.
        return whole
    items = __comma_items(statement[import_index + 1 :])
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
        # The separator after the item goes with it.
        return SrcSpan(span.start.clone(), __comma_after(tokens, span).clone())
    # The last item: the separator before it is the one to remove, or
    # `import a, b;` would become `import a, ;`.
    return SrcSpan(__comma_before(tokens, span).clone(), span.end.clone())


def __statement_at(tokens: list[Tok.Token], position: SrcPosition) -> list[Tok.Token]:
    """The statement containing *position*: everything between two ``;``.

    Taking the statement as a whole matters: the nearest keyword before the
    position is the ``import`` of a ``from … import`` statement, but the
    statement's first keyword is the ``from``, and only that tells the two forms
    apart.
    """
    last: int | None = None
    for index, token in enumerate(tokens):
        if (token.span.start.row, token.span.start.col) <= (position.row, position.col):
            last = index
    if last is None:
        return []
    start = 0
    for index in range(last, -1, -1):
        token = tokens[index]
        if isinstance(token, Tok.Punctuator) and token.kind == Tok.PunctuatorKind.Semicolon:
            start = index + 1
            break
    end = len(tokens)
    for index in range(start, len(tokens)):
        token = tokens[index]
        if isinstance(token, Tok.Punctuator) and token.kind in (
            Tok.PunctuatorKind.Semicolon,
            Tok.PunctuatorKind.LBrace,
            Tok.PunctuatorKind.RBrace,
        ):
            end = index + 1
            break
    return tokens[start:end]


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
            if (token.span.end.row, token.span.end.col) <= (span.start.row, span.start.col):
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


def __unchecked_name_uses(result: AnalysisResult, target: Target) -> tuple[SrcSpan, ...]:
    """Names spelled like *target* inside definitions the analysis never checked.

    A generic body that is never instantiated is not type checked, so no
    reference was recorded inside it.  If such a body names the symbol, a rename
    cannot be trusted to be complete, and the caller refuses.
    """
    type_ctx = result.type_ctx
    if type_ctx is None:
        return ()
    checked = set(result.def_points)
    candidates: list[SrcSpan] = []
    for type_id, body, unit_id in type_ctx.iter_procedures():
        if type_id in checked:
            continue
        unit = result.units.get(unit_id)
        if unit is None:
            continue
        file_tokens = result.tokens.get(unit.path.resolve(), ())
        extent = __body_extent(file_tokens, body)
        if extent is None:
            continue
        for token in file_tokens:
            if (
                isinstance(token, Tok.Identifier)
                and token.name == target.name
                and __contains(extent, token.span)
            ):
                candidates.append(token.span)
    return tuple(candidates)


def __body_extent(tokens: tuple[Tok.Token, ...], body: AST.Block) -> SrcSpan | None:
    """A body's extent: its opening brace through the matching closing one.

    Statement spans are not uniformly wide (a ``return`` statement's span is the
    keyword), so the extent comes from brace matching instead.
    """
    start = None
    for index, token in enumerate(tokens):
        if (token.span.start.row, token.span.start.col) == (
            body.span.start.row,
            body.span.start.col,
        ):
            start = index
            break
    if start is None:
        return None
    depth = 0
    for index in range(start, len(tokens)):
        token = tokens[index]
        if not isinstance(token, Tok.Punctuator):
            continue
        if token.kind == Tok.PunctuatorKind.LBrace:
            depth += 1
        elif token.kind == Tok.PunctuatorKind.RBrace:
            depth -= 1
            if depth == 0:
                return SrcSpan(tokens[start].span.start.clone(), token.span.end.clone())
    return None


def __name_at(result: AnalysisResult, span: SrcSpan) -> str | None:
    text = __text_of(result, span.path)
    if not text:
        return None
    lines = text.splitlines()
    if not 0 <= span.start.row < len(lines):
        return None
    line = lines[span.start.row]
    return line[span.start.col - 1 : span.end.col - 1]


def __text_of(result: AnalysisResult, path: Path) -> str:
    for candidate, text in result.sources.items():
        if candidate.resolve() == path.resolve():
            return text
    return ""


def __contains(span: SrcSpan, inner: SrcSpan) -> bool:
    return (span.start.row, span.start.col) <= (inner.start.row, inner.start.col) and (
        inner.end.row,
        inner.end.col,
    ) <= (span.end.row, span.end.col)


def __span_key(span: SrcSpan) -> tuple[str, int, int]:
    return (str(span.path.resolve()), span.start.row, span.start.col)


def __sorted_sites(sites: list[ReferenceSite]) -> list[ReferenceSite]:
    unique: dict[tuple[str, int, int], ReferenceSite] = {}
    for site in sites:
        unique.setdefault(__span_key(site.span), site)
    return sorted(
        unique.values(),
        key=lambda site: (str(site.span.path), site.span.start.row, site.span.start.col),
    )


def __is_identifier(name: str) -> bool:
    """True when *name* can be written as a YIAN identifier."""
    return name.isidentifier() and Tok.KeywordKind.try_from_str(name) is None
