"""What each name in a file *is*, for semantic highlighting (plan §5.4, §7 P6).

The TextMate grammar stays the base: it colours keywords, literals and comments
without any analysis.  These tokens are the overlay that knows what an identifier
*means*, taken from the two tables the analysis already filled in — the
declaration index and the recorded references (P5) — so a name is never
classified from its spelling.

Only identifiers and the primitive type keywords get a token.  Strings, comments
and numbers are left to the grammar, which also means a file whose analysis
stopped early simply gets no overlay rather than a wrong one (plan §7 P6: 语义
高亮失败时保留 P1 的基础高亮).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from compiler.analysis.index import Declaration, DeclarationKind
from compiler.analysis.navigation import Navigator, Target
from compiler.analysis.session import AnalysisResult
from compiler.frontend.lex import token as Tok
from compiler.frontend.lex.position import SrcSpan

__all__ = ["SemanticKind", "SemanticModifier", "SemanticToken", "classify"]


class SemanticKind(Enum):
    """The subset of LSP token types this server classifies."""

    TYPE = "type"
    STRUCT = "struct"
    ENUM = "enum"
    TRAIT = "interface"
    TYPE_PARAMETER = "typeParameter"
    FUNCTION = "function"
    METHOD = "method"
    FIELD = "property"
    VARIANT = "enumMember"
    VARIABLE = "variable"
    PARAMETER = "parameter"


class SemanticModifier(Enum):
    """The subset of LSP token modifiers this server sets."""

    DECLARATION = "declaration"
    DEFAULT_LIBRARY = "defaultLibrary"


@dataclass(frozen=True)
class SemanticToken:
    """One classified name: its exact extent and what it is."""

    span: SrcSpan
    kind: SemanticKind
    modifiers: frozenset[SemanticModifier] = frozenset()


def classify(
    result: AnalysisResult, path: Path, *, std_root: Path | None = None
) -> tuple[SemanticToken, ...]:
    """Classify every name in *path*, in source order.

    Names the analysis never resolved produce nothing: an unresolved identifier
    is either an error the editor already shows or a name the analysis could not
    reach, and guessing a colour for it would be worse than leaving the grammar's.
    """
    tokens = result.tokens.get(path.resolve())
    if tokens is None:
        return ()
    navigator = Navigator(result, std_root=std_root)
    classified: list[SemanticToken] = []
    for token in tokens:
        match token:
            case Tok.Keyword(kind=kind) if kind.value in PRIMITIVE_TYPE_KEYWORDS:
                classified.append(SemanticToken(span=token.span, kind=SemanticKind.TYPE))
            case Tok.Identifier():
                semantic = __classify_identifier(navigator, path, token.span)
                if semantic is not None:
                    classified.append(semantic)
            case _:
                continue
    return tuple(classified)


#: Keywords that name a primitive type.  They are keywords to the lexer, so a
#: grammar rule colours them; the overlay marks them as types.
PRIMITIVE_TYPE_KEYWORDS = frozenset(
    {
        "void",
        "bool",
        "char",
        "str",
        "i8",
        "i16",
        "i32",
        "i64",
        "u8",
        "u16",
        "u32",
        "u64",
        "f16",
        "f32",
        "f64",
        "int",
        "uint",
        "float",
    }
)

KIND_OF_DECLARATION: dict[DeclarationKind, SemanticKind | None] = {
    DeclarationKind.STRUCT: SemanticKind.STRUCT,
    DeclarationKind.ENUM: SemanticKind.ENUM,
    DeclarationKind.TRAIT: SemanticKind.TRAIT,
    DeclarationKind.ALIAS: SemanticKind.TYPE,
    DeclarationKind.FUNCTION: SemanticKind.FUNCTION,
    DeclarationKind.METHOD: SemanticKind.METHOD,
    DeclarationKind.FIELD: SemanticKind.FIELD,
    DeclarationKind.VARIANT: SemanticKind.VARIANT,
    DeclarationKind.VARIABLE: SemanticKind.VARIABLE,
    DeclarationKind.PARAMETER: SemanticKind.PARAMETER,
}


def __classify_identifier(
    navigator: Navigator, path: Path, span: SrcSpan
) -> SemanticToken | None:
    row, col = span.start.row, span.start.col
    declaration = navigator.declaration_starting_at(path, row, col)
    if declaration is not None:
        target = navigator.target_of_declaration(declaration, path)
        if target is None:
            return None
        return __token(span, target, declaration=declaration, navigator=navigator)
    reference = navigator.reference_starting_at(path, row, col)
    if reference is None:
        return None
    target = navigator.target_of(reference.target)
    if target is None:
        return None
    return __token(span, target, declaration=None, navigator=navigator)


def __token(
    span: SrcSpan,
    target: Target,
    *,
    declaration: Declaration | None,
    navigator: Navigator,
) -> SemanticToken | None:
    kind = __kind_of(target, declaration)
    if kind is None:
        return None
    modifiers: set[SemanticModifier] = set()
    if declaration is not None:
        # The declaration site: what an editor marks as the definition.
        modifiers.add(SemanticModifier.DECLARATION)
    if target.stdlib:
        modifiers.add(SemanticModifier.DEFAULT_LIBRARY)
    return SemanticToken(span=span, kind=kind, modifiers=frozenset(modifiers))


def __kind_of(target: Target, declaration: Declaration | None) -> SemanticKind | None:
    if declaration is not None:
        kind = KIND_OF_DECLARATION.get(declaration.kind)
        if kind is not None:
            return kind
        # An import statement names something declared elsewhere: the target
        # says what it is, so fall through to the reference side.
    return KIND_OF_REFERENCE.get(target.kind)


#: Reference-side kinds.  A reference resolves to a declaration whose kind is
#: already known, so the mapping is the same table minus the two kinds that only
#: ever appear on the declaration side.
KIND_OF_REFERENCE: dict[DeclarationKind, SemanticKind | None] = {
    **KIND_OF_DECLARATION,
    DeclarationKind.MODULE: None,
    DeclarationKind.IMPORT: None,
}
