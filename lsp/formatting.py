"""Formatting as the protocol wants it.

The formatter is a compiler-side tool (:mod:`compiler.format`); this module only
turns its result into the edit a client expects: one replacement covering the
whole document, so formatting is a single undo step.  Text that cannot be
formatted — it does not lex or parse — produces no edit at all: leaving the buffer
alone is better than rewriting part of it.
"""

from __future__ import annotations

from pathlib import Path

from lsprotocol import types

from compiler.analysis.positions import utf16_length
from compiler.format import format_text

__all__ = ["document_edits", "document_range"]


def document_edits(text: str, path: Path) -> list[types.TextEdit]:
    """The edits that reformat *text*, or none when there is nothing to do."""
    formatted = format_text(text, path=path)
    if formatted is None or formatted == text:
        return []
    return [types.TextEdit(range=document_range(text), new_text=formatted)]


def document_range(text: str) -> types.Range:
    """A range covering the whole document, in UTF-16 columns."""
    lines = text.splitlines()
    if not lines:
        return types.Range(
            start=types.Position(line=0, character=0), end=types.Position(line=0, character=0)
        )
    last = len(lines) - 1
    return types.Range(
        start=types.Position(line=0, character=0),
        end=types.Position(line=last, character=utf16_length(lines[last])),
    )
