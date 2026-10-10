"""LSP legend and delta encoding for compiler semantic-token facts."""

from __future__ import annotations

from pathlib import Path

from lsprotocol import types

from compiler.analysis.queries.navigation import Navigator
from compiler.analysis.queries.semantic import (
    SemanticKind,
    SemanticModifier,
    SemanticToken,
    classify as classify_semantic_tokens,
)
from compiler.interop.positions import utf16_length

__all__ = [
    "LEGEND",
    "SemanticKind",
    "SemanticModifier",
    "SemanticToken",
    "classify",
    "encode",
    "legend",
]

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
    """Return the protocol legend in the same order used by ``encode``."""
    return LEGEND


def classify(navigator: Navigator, path: Path) -> tuple[SemanticToken, ...]:
    """Adapt the compiler's snapshot classifier for the existing LSP call sites."""
    return classify_semantic_tokens(navigator, path)


def encode(tokens: tuple[SemanticToken, ...], text: str) -> list[int]:
    """Encode sorted source facts as LSP's UTF-16 delta array."""
    lines = text.splitlines()
    data: list[int] = []
    previous_line = 0
    previous_character = 0
    for token in sorted(tokens, key=lambda item: (item.span.start.row, item.span.start.col)):
        row = token.span.start.row
        line_text = lines[row] if 0 <= row < len(lines) else ""
        start = max(0, token.span.start.col)
        if token.span.end.row == row:
            length = utf16_length(line_text[start : max(start, token.span.end.col)])
        else:
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
