"""Trivia recovery for the formatter.

The lexer drops whitespace and comments, so the source text between two tokens is
the only record of them.  That gap can hold nothing but whitespace and comments —
string and character literals are single tokens, so their contents never appear
here — which makes the gaps a complete and unambiguous source of trivia.  The one
exception is an f-string's interior: its ``{`` is not covered by a token, so the
formatter treats those regions as opaque (see `layout.py`).

Nothing here changes the compiler: this module only reads text and tokens.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from compiler.frontend.lex.position import SrcPosition, SrcSpan
from compiler.frontend.lex.token import Token

__all__ = ["Comment", "Gap", "gaps_in"]


@dataclass(frozen=True)
class Comment:
    """One comment, with what the formatter needs to place it again."""

    span: SrcSpan
    text: str
    #: True for ``/* … */``; a block comment may span lines.
    block: bool = False
    #: True when only whitespace precedes it on its own source line: it keeps its
    #: own line instead of trailing the token before it.
    own_line: bool = True
    #: True when a blank line separates it from the previous item (token or
    #: comment).  At most one blank line is ever kept.
    blank_before: bool = False


@dataclass(frozen=True)
class Gap:
    """The trivia between two tokens (or before the first / after the last)."""

    comments: tuple[Comment, ...] = ()
    #: True when the next token starts on a new line in the source.
    broken: bool = False
    #: True when a blank line separates the previous item from the next token.
    blank_before: bool = False


def gaps_in(text: str, tokens: Sequence[Token]) -> tuple[Gap, ...]:
    """Trivia before each token, plus one gap for the end of the file.

    The result has ``len(tokens) + 1`` entries: ``gaps[i]`` precedes
    ``tokens[i]``, and the last entry follows the final token.
    """
    if not tokens:
        return (Gap(),)
    lines = text.splitlines(keepends=True)
    path = tokens[0].span.path
    result: list[Gap] = []
    previous = SrcPosition(0, 0, path)
    for token in tokens:
        result.append(__gap(lines, path, previous, token.span.start))
        previous = token.span.end
    result.append(__gap(lines, path, previous, None))
    return tuple(result)


def __gap(lines: Sequence[str], path: Path, start: SrcPosition, end: SrcPosition | None) -> Gap:
    """The trivia between two positions, comment by comment.

    Columns are tracked 0-based inside the gap and converted to the compiler's
    1-based convention only when a comment's span is built.
    """
    piece = _slice(lines, start, end)
    if not piece:
        return Gap()
    comments: list[Comment] = []
    row, col = start.row, start.col - 1
    # A comment shares its line with the previous token unless the gap starts at
    # the beginning of a line (the file start) — a newline inside the gap resets
    # this below.
    only_space = start.col <= 1
    newlines_since_item = 0
    index = 0
    while index < len(piece):
        char = piece[index]
        if char == "\n":
            row += 1
            col = 0
            newlines_since_item += 1
            only_space = True
            index += 1
            continue
        if char == "/" and piece.startswith("//", index):
            stop = piece.find("\n", index)
            stop = len(piece) if stop < 0 else stop
            body = piece[index:stop]
            comments.append(
                Comment(
                    span=_comment_span(path, row, col, body),
                    text=body,
                    own_line=only_space,
                    blank_before=newlines_since_item >= 2,
                )
            )
            col += len(body)
            index = stop
            only_space = False
            newlines_since_item = 0
            continue
        if char == "/" and piece.startswith("/*", index):
            stop = piece.find("*/", index + 2)
            stop = len(piece) if stop < 0 else stop + 2
            body = piece[index:stop]
            comments.append(
                Comment(
                    span=_comment_span(path, row, col, body),
                    text=body,
                    block=True,
                    own_line=only_space,
                    blank_before=newlines_since_item >= 2,
                )
            )
            rows = body.count("\n")
            col = len(body.rsplit("\n", 1)[-1]) if rows else col + len(body)
            row += rows
            index = stop
            only_space = False
            newlines_since_item = 0
            continue
        if not char.isspace():
            only_space = False
        col += 1
        index += 1
    if comments:
        broken = newlines_since_item > 0
        blank = newlines_since_item >= 2
    else:
        broken = piece.count("\n") > 0
        blank = piece.count("\n") >= 2
    return Gap(comments=tuple(comments), broken=broken, blank_before=blank)


def _comment_span(path: Path, row: int, col: int, body: str) -> SrcSpan:
    """The span of a comment body starting at 0-based ``(row, col)``.

    Single underscore: a module-level private the class body above would mangle if
    it were written with two (AGENTS.md).
    """
    rows = body.count("\n")
    if rows:
        # The column after the last character of the closing line, 1-based.
        end = SrcPosition(row + rows, len(body.rsplit("\n", 1)[-1]) + 1, path)
    else:
        end = SrcPosition(row, col + 1 + len(body), path)
    return SrcSpan(SrcPosition(row, col + 1, path), end)


def _slice(lines: Sequence[str], start: SrcPosition, end: SrcPosition | None) -> str:
    """The text between two positions; ``end=None`` means end of file.

    A position past the last line is the end of the file: the lexer's synthetic
    ``EOF`` token sits there, so this is a normal case rather than an error.
    """
    if start.row >= len(lines):
        return ""
    if end is None or end.row >= len(lines):
        return lines[start.row][max(0, start.col - 1) :] + "".join(lines[start.row + 1 :])
    # A column of 0 marks the start of the file; everything else is 1-based.
    start_column = max(0, start.col - 1)
    if start.row == end.row:
        return lines[start.row][start_column : end.col - 1]
    pieces = [lines[start.row][start_column:]]
    pieces.extend(lines[start.row + 1 : end.row])
    pieces.append(lines[end.row][: end.col - 1])
    return "".join(pieces)
