"""Semantic tokens: what each name is, and how that travels.

The compiler answers *what* a name is — the declaration index, the recorded
resolutions, the rendered type of a declaration — and this module is the
editor's half of the feature: which of the protocol's token types that maps
onto, whether a site is a declaration or a use, and the delta-encoded integer
array the protocol actually transfers (UTF-16 lengths and columns, which only the
document text can resolve).

Only identifiers and the primitive type keywords get a token.  Strings, comments
and numbers are left to the grammar, which also means a file whose analysis
stopped early gets no overlay rather than a wrong one (语义高亮失败
时保留 的基础高亮).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from lsprotocol import types

from compiler.analysis.index import Declaration, DeclarationKind
from compiler.analysis.navigation import Navigator, Target
from compiler.analysis.positions import utf16_length
from compiler.frontend.lex import token as Tok
from compiler.frontend.lex.position import SrcSpan

__all__ = [
    "LEGEND",
    "SemanticKind",
    "SemanticModifier",
    "SemanticToken",
    "classify",
    "encode",
    "legend",
]


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

#: Reference-side kinds.  A reference resolves to a declaration whose kind is
#: already known, so the mapping is the same table minus the two kinds that only
#: ever appear on the declaration side.
KIND_OF_REFERENCE: dict[DeclarationKind, SemanticKind | None] = {
    **KIND_OF_DECLARATION,
    DeclarationKind.MODULE: None,
    DeclarationKind.IMPORT: None,
}

#: Advertised order is part of the protocol: the integers in the data array are
#: indices into these lists.
TOKEN_TYPES: tuple[str, ...] = (
    SemanticKind.TYPE.value,
    SemanticKind.STRUCT.value,
    SemanticKind.ENUM.value,
    SemanticKind.TRAIT.value,
    SemanticKind.TYPE_PARAMETER.value,
    SemanticKind.FUNCTION.value,
    SemanticKind.METHOD.value,
    SemanticKind.FIELD.value,
    SemanticKind.VARIANT.value,
    SemanticKind.VARIABLE.value,
    SemanticKind.PARAMETER.value,
)

TOKEN_MODIFIERS: tuple[str, ...] = (
    SemanticModifier.DECLARATION.value,
    SemanticModifier.DEFAULT_LIBRARY.value,
)

LEGEND = types.SemanticTokensLegend(
    token_types=list(TOKEN_TYPES), token_modifiers=list(TOKEN_MODIFIERS)
)

KIND_INDEX = {name: index for index, name in enumerate(TOKEN_TYPES)}
MODIFIER_BITS = {name: 1 << index for index, name in enumerate(TOKEN_MODIFIERS)}


def legend() -> types.SemanticTokensLegend:
    """The legend this server classifies against."""
    return LEGEND


def classify(navigator: Navigator, path: Path) -> tuple[SemanticToken, ...]:
    """Classify every name in *path*, in source order.

    Names the analysis never resolved produce nothing: an unresolved identifier
    is either an error the editor already shows or a name the analysis could not
    reach, and guessing a colour for it would be worse than leaving the grammar's.
    """
    tokens = navigator.tokens_of(path)
    if not tokens:
        return ()
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


def encode(tokens: tuple[SemanticToken, ...], text: str) -> list[int]:
    """Encode tokens as LSP's delta-encoded array.

    Five numbers per token: line delta, start-character delta, length, token type
    index, modifier bit set.  Lengths and columns are UTF-16 code units, and the
    deltas are relative to the previous token, so the input has to be sorted.
    """
    lines = text.splitlines()
    data: list[int] = []
    previous_line = 0
    previous_character = 0
    for token in sorted(tokens, key=lambda item: (item.span.start.row, item.span.start.col)):
        row = token.span.start.row
        line_text = lines[row] if 0 <= row < len(lines) else ""
        start = max(0, token.span.start.col - 1)
        if token.span.end.row == row:
            length = utf16_length(line_text[start : max(start, token.span.end.col - 1)])
        else:
            # A name never spans lines; clamp rather than emit a wrong range.
            length = utf16_length(line_text[start:])
        if length <= 0:
            continue
        character = utf16_length(line_text[:start])
        delta_line = row - previous_line
        delta_character = character - previous_character if delta_line == 0 else character
        bits = 0
        for modifier in token.modifiers:
            bits |= MODIFIER_BITS.get(modifier.value, 0)
        data.extend([delta_line, delta_character, length, KIND_INDEX[token.kind.value], bits])
        previous_line = row
        previous_character = character
    return data


# ── classification ─────────────────────────────────────────────────────────────


def __classify_identifier(
    navigator: Navigator, path: Path, span: SrcSpan
) -> SemanticToken | None:
    row, col = span.start.row, span.start.col
    declaration = navigator.declaration_starting_at(path, row, col)
    if declaration is not None:
        target = navigator.target_of_declaration(declaration, path)
        if target is None:
            return None
        return __token(span, target, declaration=declaration)
    reference = navigator.reference_starting_at(path, row, col)
    if reference is None:
        return None
    target = navigator.target_of(reference.target)
    if target is None:
        return None
    return __token(span, target, declaration=None)


def __token(
    span: SrcSpan, target: Target, *, declaration: Declaration | None
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
