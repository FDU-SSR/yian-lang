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

from compiler.analysis.navigation import Navigator, Target
from compiler.analysis.session import AnalysisResult
from compiler.analysis.view import AnalysisView
from compiler.frontend.lex import token as Tok
from compiler.frontend.lex.position import SrcSpan

__all__ = [
    "ReferenceSite",
    "RenameEdit",
    "RenameResult",
    "find_references",
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
    return __references_at(Navigator(result, std_root=std_root), path, row, col, include_declaration)


def __references_at(
    navigator: Navigator, path: Path, row: int, col: int, include_declaration: bool
) -> ReferenceResult | None:
    """The reference set of the symbol at a position, over one navigator."""
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
    navigator = Navigator(result, std_root=std_root)
    references = __references_at(navigator, path, row, col, include_declaration=True)
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
        if not site.declaration and __name_at(navigator.view, site.span) != target.name:
            # An alias: the local spelling is a different name, and only the
            # import statement that introduced it changes.
            continue
        edits.append(RenameEdit(path=site.span.path, span=site.span, new_name=new_name))
    if not edits:
        return RenameResult(refusal=f"nothing to rename for '{target.name}'", target=target)
    return RenameResult(edits=tuple(edits), target=target)


def __unchecked_name_uses(result: AnalysisResult, target: Target) -> tuple[SrcSpan, ...]:
    """Names spelled like *target* inside definitions the analysis never checked.

    A generic body that is never instantiated is not type checked, so no
    reference was recorded inside it.  If such a body names the symbol, a rename
    cannot be trusted to be complete, and the caller refuses.
    """
    view = AnalysisView(result)
    if not view.has_types:
        return ()
    checked = set(view.def_points())
    candidates: list[SrcSpan] = []
    for type_id, body, unit_id in view.procedures():
        if type_id in checked:
            continue
        path = view.unit_path(unit_id)
        if path is None:
            continue
        file_tokens = view.tokens_of(path)
        extent = body.full_span()
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


def __name_at(view: AnalysisView, span: SrcSpan) -> str | None:
    text = view.text_of(span.path)
    if not text:
        return None
    lines = text.splitlines()
    if not 0 <= span.start.row < len(lines):
        return None
    line = lines[span.start.row]
    return line[span.start.col - 1 : span.end.col - 1]


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
