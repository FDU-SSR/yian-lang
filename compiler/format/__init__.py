"""Source formatting for YIAN.

The formatter reads a file the way the compiler does — lexer, then parser — and
re-emits the token stream with canonical whitespace, line breaks and comment
placement.  It never adds or removes a token, so a formatted file means the same
program; see `docs/plan/formatter-plan.md` for the rules and guarantees.

Public entry point: :func:`compiler.format.format_text`.
"""

from compiler.format.formatter import format_text, format_tokens

__all__ = ["format_text", "format_tokens"]
