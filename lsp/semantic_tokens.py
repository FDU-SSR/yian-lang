"""Semantic tokens as the protocol wants them (plan §5.4, §7 P6).

The classification itself lives on the compiler side
(:mod:`compiler.analysis.semantic`); this module holds the LSP shape of it: the
legend advertised at `initialize`, and the delta-encoded integer array the
protocol actually transfers.  That encoding is why this file needs the document
text — positions are in UTF-16 units, which only the text can resolve.
"""

from __future__ import annotations

from lsprotocol import types

from compiler.analysis.positions import utf16_length
from compiler.analysis.semantic import SemanticKind, SemanticModifier, SemanticToken

__all__ = ["LEGEND", "encode", "legend"]

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

LEGEND = types.SemanticTokensLegend(token_types=list(TOKEN_TYPES), token_modifiers=list(TOKEN_MODIFIERS))

KIND_INDEX = {name: index for index, name in enumerate(TOKEN_TYPES)}
MODIFIER_BITS = {name: 1 << index for index, name in enumerate(TOKEN_MODIFIERS)}


def legend() -> types.SemanticTokensLegend:
    """The legend this server classifies against."""
    return LEGEND


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
