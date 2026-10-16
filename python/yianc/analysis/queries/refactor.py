"""Symbol references and rename facts.

Everything here is based on **symbol identity**, never on the text: a reference is
a name the analysis resolved to a particular declaration, so two same-named
symbols in different scopes stay apart, and a rename edits exactly those spans.
That is also why a rename can refuse: when the analysis cannot account for every
use of the name, editing some of them would leave the program half-renamed, and
refusing is the safe answer (bulk edits require confirmed symbol identities).

Protocol edits and quick fixes are rendered by the client adapter, using the
spans produced here.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from yianc.analysis.diagnostics import Diagnostic
from yianc.analysis.queries.context import QueryContext
from yianc.analysis.queries.availability import Completeness
from yianc.analysis.queries.navigation import Navigator, Target
from yianc.analysis.queries.view import AnalysisView
from yianc.frontend.lex import token as Tok
from yianc.frontend.lex.position import SrcPosition, SrcSpan

if TYPE_CHECKING:
    from yianc.analysis.session import AnalysisResult

__all__ = [
    "ReferenceSite",
    "RenameEdit",
    "RenameResult",
    "PrepareRenameResult",
    "ImportRemoval",
    "find_references",
    "find_import_removals",
    "prepare_rename",
    "rename",
]


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
    completeness: Completeness = Completeness.COMPLETE


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
class PrepareRenameResult:
    """The symbol name range a client may edit, or the reason it is unsafe."""

    target: Target | None = None
    refusal: str | None = None

    @property
    def ok(self) -> bool:
        return self.refusal is None and self.target is not None


@dataclass(frozen=True)
class ImportRemoval:
    """An import diagnostic and the source span that removes its invalid import."""

    diagnostic: Diagnostic
    span: SrcSpan


IMPORT_FAILURE_CODES = frozenset({"E304", "E307", "E308", "E309", "E310"})


def find_import_removals(
    result: AnalysisResult, view: AnalysisView, path: Path
) -> tuple[ImportRemoval, ...]:
    """Find import failures and calculate edits while the analysis snapshot is live."""
    canonical_path = path.resolve()
    tokens = view.tokens_of(path)
    statements = view.import_statements(path)
    if not tokens or not statements:
        return ()

    removals: list[ImportRemoval] = []
    for diagnostic in result.diagnostics:
        if diagnostic.code not in IMPORT_FAILURE_CODES:
            continue
        if diagnostic.span.path.resolve() != canonical_path:
            continue
        statement = next(
            (span for span in statements if __contains(span, diagnostic.span)), None
        )
        if statement is None:
            continue
        span = __import_removal_span(tokens, diagnostic.span, statement)
        if span is not None:
            removals.append(ImportRemoval(diagnostic=diagnostic, span=span))

    return tuple(removals)


def __import_removal_span(
    tokens: tuple[Tok.Token, ...], diagnostic: SrcSpan, statement: SrcSpan
) -> SrcSpan | None:
    """Remove one invalid item plus its adjacent separator, or the whole statement."""
    inside = [
        token
        for token in tokens
        if __is_real(token.span)
        and __contains(statement, token.span)
    ]
    if not inside:
        return None

    from_index = __keyword_index(inside, Tok.KeywordKind.From)
    import_index = __keyword_index(inside, Tok.KeywordKind.Import)
    if from_index is None or import_index is None:
        return statement
    if __before(diagnostic, inside[import_index].span):
        return statement

    items = __comma_items(inside[import_index + 1 :])
    if len(items) <= 1:
        return statement
    for index, item in enumerate(items):
        if item and (
            __contains(item[0].span, diagnostic)
            or __contains(item[-1].span, diagnostic)
        ):
            return __item_removal(tokens, items, index)
    return statement


def __item_removal(
    tokens: tuple[Tok.Token, ...], items: list[list[Tok.Token]], index: int
) -> SrcSpan:
    item = items[index]
    span = SrcSpan(item[0].span.start.clone(), item[-1].span.end.clone())
    if index + 1 < len(items):
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

    return SrcSpan(__comma_before(tokens, span).clone(), span.end.clone())


def __comma_items(tokens: list[Tok.Token]) -> list[list[Tok.Token]]:
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
    return (position.start.row, position.start.col) < (
        reference.start.row,
        reference.start.col,
    )


def __is_real(span: SrcSpan) -> bool:
    return not span.is_synthetic()


def __contains(span: SrcSpan, inner: SrcSpan) -> bool:
    return (span.start.row, span.start.col) <= (inner.start.row, inner.start.col) and (
        inner.end.row,
        inner.end.col,
    ) <= (span.end.row, span.end.col)


def find_references(
    context: QueryContext,
    path: Path,
    row: int,
    col: int,
    *,
    include_declaration: bool = True,
    std_root: Path | None = None,
    navigator: Navigator | None = None,
) -> ReferenceResult | None:
    """Every recorded naming of the symbol at ``(row, col)``.

    A reference counts when it resolved to the *same declaration* as the position
    does, which is symbol identity: a local `x` and a global `x` never mix.
    """
    active_navigator = navigator or Navigator(context, std_root=std_root)
    return __references_at(active_navigator, path, row, col, include_declaration)


def __references_at(
    navigator: Navigator, path: Path, row: int, col: int, include_declaration: bool
) -> ReferenceResult | None:
    """The reference set of the symbol at a position, over one navigator."""
    resolution = navigator.resolve(path, row, col)
    if resolution is None or resolution.target is None:
        return None
    target = resolution.target
    sites = [
        ReferenceSite(span=reference.span)
        for reference in navigator.references_to(target)
    ]
    if include_declaration:
        declaration = navigator.declaration_for(target)
        if declaration is not None and declaration.span is not None:
            sites.append(ReferenceSite(span=declaration.span, declaration=True))
    return ReferenceResult(target=target, sites=tuple(__sorted_sites(sites, navigator)),
                           completeness=Completeness.COMPLETE if navigator.view.semantic_complete and navigator.references_consistent(target)
                           else Completeness.PARTIAL)


def prepare_rename(
    context: QueryContext,
    path: Path,
    row: int,
    col: int,
    *,
    std_root: Path | None = None,
    navigator: Navigator | None = None,
) -> PrepareRenameResult:
    """Check whether the resolved declaration can be renamed safely."""
    active_navigator = navigator or Navigator(context, std_root=std_root)
    resolution = active_navigator.resolve(path, row, col)
    if resolution is None or resolution.target is None:
        return PrepareRenameResult(refusal="nothing to rename here")

    target = resolution.target
    if not context.result.ok() or not active_navigator.references_consistent(target):
        return PrepareRenameResult(target=target, refusal="reference facts are incomplete; rename is unavailable")
    if target.stdlib:
        return PrepareRenameResult(
            target=target,
            refusal=f"'{target.name}' is declared in the standard library",
        )

    unchecked = __unchecked_name_uses(context, target, active_navigator.view)
    if unchecked:
        where = unchecked[0]
        return PrepareRenameResult(
            target=target,
            refusal=(
                f"a definition the analysis did not check uses '{target.name}' "
                f"({where.path.name}:{where.start.row + 1}); rename it there first"
            ),
        )

    return PrepareRenameResult(target=target)


def rename(
    context: QueryContext,
    path: Path,
    row: int,
    col: int,
    new_name: str,
    *,
    std_root: Path | None = None,
    navigator: Navigator | None = None,
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
    navigator = navigator or Navigator(context, std_root=std_root)
    prepared = prepare_rename(
        context, path, row, col, std_root=std_root, navigator=navigator
    )
    if not prepared.ok:
        return RenameResult(refusal=prepared.refusal, target=prepared.target)

    references = __references_at(navigator, path, row, col, include_declaration=True)
    if references is None:
        return RenameResult(refusal="nothing to rename here")
    target = references.target
    edits: list[RenameEdit] = []
    for site in references.sites:
        if not site.declaration and __name_at(navigator.view, site.span) != target.name:
            # An alias: the local spelling is a different name, and only the
            # import statement that introduced it changes.
            continue
        edits.append(RenameEdit(path=site.span.path, span=site.span, new_name=new_name))
    if not edits:
        return RenameResult(refusal=f"nothing to rename for '{target.name}'", target=target)
    return RenameResult(edits=tuple(edits), target=target)


def __unchecked_name_uses(
    context: QueryContext, target: Target, view: AnalysisView | None = None
) -> tuple[SrcSpan, ...]:
    """Names spelled like *target* inside definitions the analysis never checked.

    A generic body that is never instantiated is not type checked, so no
    reference was recorded inside it.  If such a body names the symbol, a rename
    cannot be trusted to be complete, and the caller refuses.
    """
    active_view = view or AnalysisView(context)
    return active_view.unchecked_name_uses(target.name)


def __name_at(view: AnalysisView, span: SrcSpan) -> str | None:
    return view.identifier_name_at(span)


def __sorted_sites(sites: list[ReferenceSite], navigator: Navigator) -> list[ReferenceSite]:
    unique: dict[tuple[Path, int, int], ReferenceSite] = {}
    for site in sites:
        unique.setdefault(navigator.span_key(site.span), site)
    return sorted(
        unique.values(),
        key=lambda site: (str(navigator.canonical_path(site.span.path)), site.span.start.row, site.span.start.col),
    )


def __is_identifier(name: str) -> bool:
    """True when *name* can be written as a YIAN identifier."""
    return name.isidentifier() and Tok.KeywordKind.try_from_str(name) is None
