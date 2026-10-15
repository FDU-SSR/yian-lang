"""Protocol-neutral semantic token classification for one analysis snapshot."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from yianc.analysis.queries.index import Declaration, DeclarationKind
from yianc.analysis.queries.navigation import Navigator, Target
from yianc.frontend.lex import token as Tok
from yianc.frontend.lex.position import SrcSpan

__all__ = ["SemanticKind", "SemanticModifier", "SemanticToken", "classify"]


class SemanticKind(Enum):
    """The semantic categories the compiler can associate with a name."""

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
    """Source facts attached to a classified name."""

    DECLARATION = "declaration"
    DEFAULT_LIBRARY = "defaultLibrary"


@dataclass(frozen=True)
class SemanticToken:
    """One classified name with its exact source range and semantic role."""

    span: SrcSpan
    kind: SemanticKind
    modifiers: frozenset[SemanticModifier] = frozenset()


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

KIND_OF_REFERENCE: dict[DeclarationKind, SemanticKind | None] = {
    **KIND_OF_DECLARATION,
    DeclarationKind.MODULE: None,
    DeclarationKind.IMPORT: None,
}


def classify(navigator: Navigator, path: Path) -> tuple[SemanticToken, ...]:
    """Classify resolved identifiers and primitive type keywords in source order."""
    classified: list[SemanticToken] = []
    for token in navigator.tokens_of(path):
        match token:
            case Tok.Keyword(kind=kind) if kind.value in PRIMITIVE_TYPE_KEYWORDS:
                classified.append(SemanticToken(span=token.span, kind=SemanticKind.TYPE))
            case Tok.Identifier():
                semantic = _classify_identifier(navigator, path, token.span)
                if semantic is not None:
                    classified.append(semantic)
            case _:
                continue
    return tuple(classified)


def _classify_identifier(
    navigator: Navigator, path: Path, span: SrcSpan
) -> SemanticToken | None:
    row, col = span.start.row, span.start.col
    declaration = navigator.declaration_starting_at(path, row, col)
    if declaration is not None:
        # Only the declaration's identifier token receives the declaration modifier.
        if declaration.span != span:
            return None
        target = navigator.target_of_declaration(declaration, path)
        if target is None:
            return None
        return _token(span, target, declaration=declaration)

    reference = navigator.reference_starting_at(path, row, col)
    if reference is None:
        return None
    target = navigator.target_of(reference.target)
    if target is None:
        return None
    return _token(span, target, declaration=None)


def _token(
    span: SrcSpan, target: Target, *, declaration: Declaration | None
) -> SemanticToken | None:
    kind = _kind_of(target, declaration)
    if kind is None:
        return None
    modifiers: set[SemanticModifier] = set()
    if declaration is not None:
        modifiers.add(SemanticModifier.DECLARATION)
    if target.stdlib:
        modifiers.add(SemanticModifier.DEFAULT_LIBRARY)
    return SemanticToken(span=span, kind=kind, modifiers=frozenset(modifiers))


def _kind_of(
    target: Target, declaration: Declaration | None
) -> SemanticKind | None:
    if declaration is not None:
        kind = KIND_OF_DECLARATION.get(declaration.kind)
        if kind is not None:
            return kind
        # Imports use the resolved target kind; unresolved names remain unclassified.
    return KIND_OF_REFERENCE.get(target.kind)
