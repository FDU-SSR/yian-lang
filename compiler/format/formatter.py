"""The formatter's entry point.

Formatting is defined only for text the front end can read: the engine re-emits
the lexer's tokens, so it needs a lexed and parsed file.  Input that fails either
step produces ``None`` — "no formatting result" — rather than a half-rewritten
file, and the callers (CLI, editor) then leave the source alone.
"""

from __future__ import annotations

from pathlib import Path

from compiler.analysis.session import ANALYSIS_ERRORS
from compiler.format.layout import format_tokens
from compiler.frontend.lex.lexer import Lexer
from compiler.frontend.lex.token import Token
from compiler.frontend.parse.parser import Parser

__all__ = ["format_text", "format_tokens", "lex_tokens"]

#: A path is only used for spans and error messages, so a buffer without one gets
#: a placeholder instead of failing.
_PLACEHOLDER = Path("<buffer>.an")


def format_text(text: str, *, path: Path | None = None) -> str | None:
    """Canonical source for *text*, or ``None`` when it cannot be formatted.

    *path* names the file for position tracking; it does not have to exist.
    """
    tokens = lex_tokens(text, path or _PLACEHOLDER)
    if tokens is None:
        return None
    try:
        Parser(tokens).parse()
    except ANALYSIS_ERRORS:
        return None
    return format_tokens(text, tokens)


def lex_tokens(text: str, path: Path) -> list[Token] | None:
    """The lexer's tokens for *text*, or ``None`` when it does not lex."""

    lexer = Lexer(path, text=text)
    try:
        lexer.lex()
    except ANALYSIS_ERRORS:
        return None
    return lexer.export()
