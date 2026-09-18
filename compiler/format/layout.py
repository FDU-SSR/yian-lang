"""The formatter's layout engine.

Input is the source text plus the lexer's tokens; output is canonical text.  The
token sequence is the single source of content — the engine only decides where
whitespace, line breaks and comments go — which is what makes "formatting never
changes the program" a property of the design rather than a promise.

Three facts about the front end shape the rules:

* ``<`` and ``>`` are lexed by *whitespace context* (``LAngle``/``RAngle`` when no
  whitespace precedes them, ``Less``/``Greater`` otherwise), so the engine keys
  off the token kind: a generic bracket hugs its neighbours, a comparison gets
  spaces.  Writing ``a < b`` as ``a<b`` would change the token kind, which is
  exactly what the token-preservation check exists to catch.
* Comments and blank lines live in the gaps between tokens (see `trivia.py`).
* An f-string's interior is not fully covered by tokens, so a whole f-string is
  emitted verbatim: the author's spacing inside it is left alone.

Line breaks are conservative: the engine forces one only where the structure
demands it (after ``{``, after ``;``, before ``}``) and otherwise keeps the breaks
the author wrote.  It never introduces a break the author did not have, so it
does not re-flow long lines.
"""

from __future__ import annotations

from collections.abc import Sequence

from compiler.format.trivia import Comment, Gap, gaps_in
from compiler.frontend.lex import token as Tok
from compiler.frontend.lex.position import SrcPosition
from compiler.frontend.lex.token import Token

__all__ = ["INDENT", "format_tokens"]

#: One indentation level.  The editor configuration for ``[yian]`` says 4 spaces.
INDENT = 4

#: Keywords after which ``(`` belongs to the syntax rather than to a call.
_CONDITION_KEYWORDS = frozenset({"if", "elif", "while", "for", "return", "assert", "defer", "in"})

#: Keywords that expect an operand to follow, so a ``-``/``*``/``&`` after them is
#: a prefix operator rather than a binary one.
_OPERAND_KEYWORDS = frozenset(
    {"return", "let", "in", "assert", "defer", "if", "elif", "while", "else", "match"}
)

#: Operators written with a space on both sides.
_BINARY = frozenset(
    {
        Tok.PunctuatorKind.Equal,
        Tok.PunctuatorKind.EqualEqual,
        Tok.PunctuatorKind.NotEqual,
        Tok.PunctuatorKind.Less,
        Tok.PunctuatorKind.Greater,
        Tok.PunctuatorKind.LessEqual,
        Tok.PunctuatorKind.GreaterEqual,
        Tok.PunctuatorKind.Slash,
        Tok.PunctuatorKind.Percent,
        Tok.PunctuatorKind.Caret,
        Tok.PunctuatorKind.AmpersandAmpersand,
        Tok.PunctuatorKind.PipePipe,
        Tok.PunctuatorKind.Pipe,
        Tok.PunctuatorKind.PlusEqual,
        Tok.PunctuatorKind.MinusEqual,
        Tok.PunctuatorKind.StarEqual,
        Tok.PunctuatorKind.SlashEqual,
        Tok.PunctuatorKind.PercentEqual,
        Tok.PunctuatorKind.CaretEqual,
        Tok.PunctuatorKind.AmpersandEqual,
        Tok.PunctuatorKind.PipeEqual,
        Tok.PunctuatorKind.LessLess,
        Tok.PunctuatorKind.GreaterGreater,
        Tok.PunctuatorKind.LessLessEqual,
        Tok.PunctuatorKind.GreaterGreaterEqual,
        Tok.PunctuatorKind.Arrow,
        Tok.PunctuatorKind.FatArrow,
    }
)

#: Tokens that hug what precedes them.
_HUG_BEFORE = frozenset(
    {
        Tok.PunctuatorKind.Comma,
        Tok.PunctuatorKind.Semicolon,
        Tok.PunctuatorKind.RParen,
        Tok.PunctuatorKind.RBracket,
        Tok.PunctuatorKind.Dot,
        Tok.PunctuatorKind.Colon,
    }
)

#: Tokens that hug what follows them.
_HUG_AFTER = frozenset(
    {
        Tok.PunctuatorKind.LParen,
        Tok.PunctuatorKind.LBracket,
        Tok.PunctuatorKind.Dot,
        Tok.PunctuatorKind.At,
        Tok.PunctuatorKind.Exclamation,
        Tok.PunctuatorKind.Tilde,
    }
)

#: What may follow a postfix pointer or reference type instead of an operand —
#: the test that tells `i32*` (a type) from `a * b` (multiplication).
_TYPE_TAIL = frozenset(
    {
        Tok.PunctuatorKind.Comma,
        Tok.PunctuatorKind.RParen,
        Tok.PunctuatorKind.RBracket,
        Tok.PunctuatorKind.RBrace,
        Tok.PunctuatorKind.Equal,
        Tok.PunctuatorKind.Semicolon,
        Tok.PunctuatorKind.LBrace,
        Tok.PunctuatorKind.LBracket,
        Tok.PunctuatorKind.Colon,
        Tok.PunctuatorKind.Star,
        Tok.PunctuatorKind.Ampersand,
        Tok.PunctuatorKind.RAngle,
    }
)

#: Tokens that can end an operand.
_CLOSERS = frozenset(
    {
        Tok.PunctuatorKind.RParen,
        Tok.PunctuatorKind.RBracket,
        Tok.PunctuatorKind.RBrace,
        Tok.PunctuatorKind.RAngle,
    }
)

#: Prefix operators (their operand hugs them: ``*p``, ``-x``, ``&x``, ``!ok``).
_PREFIX_OPERATORS = frozenset(
    {
        Tok.PunctuatorKind.Star,
        Tok.PunctuatorKind.Ampersand,
        Tok.PunctuatorKind.Minus,
        Tok.PunctuatorKind.Plus,
        Tok.PunctuatorKind.Exclamation,
        Tok.PunctuatorKind.Tilde,
    }
)


def format_tokens(text: str, tokens: Sequence[Token]) -> str:
    """Render *tokens* — which came from *text* — as canonical source."""
    real = [token for token in tokens if not _is_eof(token)]
    if not real:
        return ""
    return _Layout(text, real, gaps_in(text, real)).render()


# ── spacing ────────────────────────────────────────────────────────────────────


def _is(token: Token, *kinds: Tok.PunctuatorKind) -> bool:
    return isinstance(token, Tok.Punctuator) and token.kind in kinds


def _word_like(token: Token) -> bool:
    return isinstance(token, (Tok.Identifier, Tok.Keyword, Tok.FStrStart)) or isinstance(
        token,
        (
            Tok.IntLiteral,
            Tok.FloatLiteral,
            Tok.CharLiteral,
            Tok.StrLiteral,
            Tok.BoolLiteral,
            Tok.FStrLiteral,
            Tok.FStrEnd,
        ),
    )


def _spaces_before_paren(token: Token) -> bool:
    """True for the keywords that keep a space before their `(`."""
    return isinstance(token, Tok.Keyword) and token.kind.value in (
        _CONDITION_KEYWORDS | {"match", "else"}
    )


def _is_prefix(token: Token, before: Token | None) -> bool:
    """True when *token* is a prefix operator (``*p``, ``-x``, ``&x``).

    A prefix operator is one whose left neighbour cannot end an operand: another
    operator, an opening bracket, a comma, or a keyword that expects a value.
    """
    if not _is(token, *_PREFIX_OPERATORS):
        return False
    if before is None:
        return True
    if isinstance(before, Tok.Keyword):
        return before.kind.value in _OPERAND_KEYWORDS
    return not (_word_like(before) or _is(before, *_CLOSERS))


# ── the pass ───────────────────────────────────────────────────────────────────


class _Layout:
    """One formatting pass: the state lives here, the rules live above."""

    def __init__(self, text: str, tokens: Sequence[Token], gaps: Sequence[Gap]) -> None:
        self.__text = text
        self.__tokens = list(tokens)
        self.__gaps = list(gaps)
        self.__out: list[str] = []
        self.__line_open = False
        self.__pending = 0
        self.__depth = 0
        self.__parens = 0
        self.__inline = self.__inline_indices()
        self.__blank_pending = False
        self.__postfix = self.__postfix_markers()
        self.__tight_equals = self.__field_initializers()
        self.__closure_open, self.__closure_close = self.__closure_bars()

    def render(self) -> str:
        index = 0
        while index < len(self.__tokens):
            token = self.__tokens[index]
            if isinstance(token, Tok.FStrStart):
                index = self.__fstring(index)
                continue
            self.__token(token, index)
            index += 1
        self.__end_line()
        rendered = "".join(self.__out)
        return rendered.rstrip("\n") + "\n" if rendered.strip() else ""

    # ── spacing ───────────────────────────────────────────────────────────────

    def __space_before(self, index: int) -> bool:
        """Whether a space belongs before ``tokens[index]`` on its current line."""
        token = self.__tokens[index]
        previous = self.__tokens[index - 1]
        before_previous = self.__tokens[index - 2] if index >= 2 else None

        if index in self.__closure_close:
            return False  # the closing bar hugs the parameter list
        if index - 1 in self.__closure_open:
            return False  # the first parameter hugs the opening bar
        if index - 1 in self.__closure_close:
            return True  # the body starts after the closing bar
        if _is(token, Tok.PunctuatorKind.LAngle, Tok.PunctuatorKind.RAngle):
            return False
        if _is(previous, Tok.PunctuatorKind.LAngle):
            return False
        if _is(previous, Tok.PunctuatorKind.RAngle):
            # A generic closes against what hugs it (`Vec<T>.new()`, `Vec<T>*`)
            # but not against an operator: `Vec<T> = v` keeps its space.
            return not (
                _is(
                    token,
                    *_HUG_BEFORE,
                    Tok.PunctuatorKind.Dot,
                    Tok.PunctuatorKind.LParen,
                    Tok.PunctuatorKind.LBracket,
                    Tok.PunctuatorKind.Star,
                    Tok.PunctuatorKind.Ampersand,
                    Tok.PunctuatorKind.RAngle,
                )
            )
        if _is(token, Tok.PunctuatorKind.RBrace):
            # `{}` hugs; a one-line block keeps `{ return x; }` readable.
            return not _is(previous, Tok.PunctuatorKind.LBrace)
        if _is(token, *_HUG_BEFORE):
            return False
        if _is(token, Tok.PunctuatorKind.LBracket):
            return not (_word_like(previous) or _is(previous, *_CLOSERS))
        if _is(token, Tok.PunctuatorKind.LParen):
            # A call, a cast or a grouping hugs: `print(…)`, `i32(…)`, `Self(…)`.
            return not (
                (_word_like(previous) and not _spaces_before_paren(previous))
                or _is(
                    previous,
                    Tok.PunctuatorKind.RParen,
                    Tok.PunctuatorKind.RBracket,
                    Tok.PunctuatorKind.RAngle,
                    Tok.PunctuatorKind.LParen,
                    Tok.PunctuatorKind.LBracket,
                    Tok.PunctuatorKind.Dot,
                )
            )
        if _is(previous, *_HUG_AFTER):
            return False
        if _is(token, Tok.PunctuatorKind.Equal) and index in self.__tight_equals:
            return False
        if _is(previous, Tok.PunctuatorKind.Equal) and index - 1 in self.__tight_equals:
            return False
        if _is(token, Tok.PunctuatorKind.At):
            return True
        if _is(token, *_BINARY) or _is(token, Tok.PunctuatorKind.Minus, Tok.PunctuatorKind.Plus):
            return True
        if _is(previous, Tok.PunctuatorKind.Colon, Tok.PunctuatorKind.Comma):
            return True
        if _is(previous, Tok.PunctuatorKind.Minus, Tok.PunctuatorKind.Plus):
            return not _is_prefix(previous, before_previous)
        if _is(previous, Tok.PunctuatorKind.Star, Tok.PunctuatorKind.Ampersand):
            # `*p` (prefix) hugs its operand; `a * b` and `i32* = p` keep a space.
            return not _is_prefix(previous, before_previous)
        if _is(token, Tok.PunctuatorKind.Star, Tok.PunctuatorKind.Ampersand):
            return index not in self.__postfix
        if _is(token, Tok.PunctuatorKind.DotDot) or _is(previous, Tok.PunctuatorKind.DotDot):
            return False
        return True

    # ── one token ─────────────────────────────────────────────────────────────

    def __token(self, token: Token, index: int) -> None:
        previous = self.__tokens[index - 1] if index else None
        gap = self.__gaps[index]
        self.__comments(gap)
        if previous is None:
            # The file's first token still keeps any blank line the author left
            # after the leading comments.
            if self.__out:
                self.__blank_pending = gap.blank_before
            self.__write(token_text(token))
            self.__track(token, index)
            return
        if self.__breaks(previous, token, index, gap):
            self.__newline(self.__indent_for(token, index), blank=gap.blank_before)
        elif self.__line_open and self.__space_before(index):
            self.__out.append(" ")
        self.__write(token_text(token))
        self.__track(token, index)

    def __comments(self, gap: Gap) -> None:
        for comment in gap.comments:
            text = _comment_text(comment)
            if comment.own_line or not self.__line_open:
                if self.__line_open:
                    self.__end_line()
                self.__newline(self.__content_indent(), blank=comment.blank_before)
                self.__write(text)
                self.__end_line()
            else:
                self.__write(" " + text)
                self.__end_line()

    def __fstring(self, index: int) -> int:
        """Emit a whole f-string verbatim; return the index after it."""
        stop = index
        while stop < len(self.__tokens) and not isinstance(self.__tokens[stop], Tok.FStrEnd):
            stop += 1
        after = stop + 1 if stop < len(self.__tokens) else stop
        start = self.__tokens[index].span.start
        end = (
            self.__tokens[after].span.start
            if after < len(self.__tokens)
            else self.__tokens[stop].span.end
        )
        previous = self.__tokens[index - 1] if index else None
        if previous is None:
            self.__write(_region(self.__text, start, end).rstrip())
            return after
        if self.__breaks(previous, self.__tokens[index], index, self.__gaps[index]):
            self.__newline(self.__indent_for(self.__tokens[index], index))
        elif self.__line_open and self.__space_before(index):
            self.__out.append(" ")
        self.__write(_region(self.__text, start, end).rstrip())
        return after

    # ── line breaking and indentation ─────────────────────────────────────────

    def __breaks(self, previous: Token, token: Token, index: int, gap: Gap) -> bool:
        if _is(previous, Tok.PunctuatorKind.LBrace) and not self.__is_inline(index - 1):
            return True
        if _is(previous, Tok.PunctuatorKind.Semicolon) and not self.__is_inline(index - 1):
            return True
        if _is(token, Tok.PunctuatorKind.RBrace) and not self.__is_inline(index):
            return True
        if _is(previous, Tok.PunctuatorKind.RBrace):
            if isinstance(token, Tok.Keyword) and token.kind.value in ("else", "elif"):
                return False
            if _is(
                token,
                Tok.PunctuatorKind.Semicolon,
                Tok.PunctuatorKind.Comma,
                Tok.PunctuatorKind.RParen,
                Tok.PunctuatorKind.RBracket,
                Tok.PunctuatorKind.Dot,
                Tok.PunctuatorKind.RBrace,
                Tok.PunctuatorKind.RAngle,
            ):
                return False
            return True
        return gap.broken

    def __indent_for(self, token: Token, index: int) -> int:
        level = self.__depth
        if _is(token, Tok.PunctuatorKind.RBrace) and not self.__is_inline(index):
            level -= 1
        # A line that closes a bracket aligns with what opened it, not one level in.
        closes = _is(token, Tok.PunctuatorKind.RParen, Tok.PunctuatorKind.RBracket)
        if self.__parens - (1 if closes else 0) > 0:
            level += 1
        return max(0, level)

    def __content_indent(self) -> int:
        return max(0, self.__depth + (1 if self.__parens > 0 else 0))

    def __track(self, token: Token, index: int) -> None:
        if not isinstance(token, Tok.Punctuator):
            return
        if _is(token, Tok.PunctuatorKind.LParen, Tok.PunctuatorKind.LBracket):
            self.__parens += 1
        elif _is(token, Tok.PunctuatorKind.RParen, Tok.PunctuatorKind.RBracket):
            self.__parens = max(0, self.__parens - 1)
        elif _is(token, Tok.PunctuatorKind.LBrace) and not self.__is_inline(index):
            self.__depth += 1
        elif _is(token, Tok.PunctuatorKind.RBrace) and not self.__is_inline(index):
            self.__depth = max(0, self.__depth - 1)

    # ── writing ───────────────────────────────────────────────────────────────

    def __write(self, text: str) -> None:
        if not text:
            return
        if not self.__line_open:
            if self.__blank_pending and self.__out:
                self.__out.append("\n")
            self.__blank_pending = False
            self.__out.append(" " * (INDENT * self.__pending))
            self.__line_open = True
        self.__out.append(text)

    def __newline(self, level: int, *, blank: bool = False) -> None:
        self.__end_line()
        self.__pending = level
        self.__blank_pending = blank and bool(self.__out)

    def __end_line(self) -> None:
        if not self.__line_open:
            return
        while self.__out and self.__out[-1] == " ":
            self.__out.pop()
        self.__out.append("\n")
        self.__line_open = False

    # ── braces ────────────────────────────────────────────────────────────────

    def __postfix_markers(self) -> frozenset[int]:
        """Indices of ``*``/``&`` that close a pointer or reference type.

        ``i32*``, ``T**`` and ``Vec<i32>*`` are postfix; ``*p``, ``&x`` and
        ``a * b`` are not.  The engine cannot ask the type checker, so it reads the
        neighbourhood: an operand has just ended and what follows cannot start one.
        """
        markers: set[int] = set()
        operand_end = False
        for index, token in enumerate(self.__tokens):
            if _is(token, Tok.PunctuatorKind.Star, Tok.PunctuatorKind.Ampersand):
                after = self.__tokens[index + 1] if index + 1 < len(self.__tokens) else None
                if operand_end and (after is None or _is(after, *_TYPE_TAIL)):
                    markers.add(index)
                operand_end = index in markers
                continue
            operand_end = _word_like(token) or _is(token, *_CLOSERS)
        return frozenset(markers)

    def __field_initializers(self) -> frozenset[int]:
        """Indices of ``=`` that initialise a named field (``Point(x=1, y=2)``).

        Inside an argument list an ``=`` follows a name that follows ``(`` or ``,``,
        which is what a construction looks like — unlike `let x = 1` or `x = 1`.
        """
        tight: set[int] = set()
        for index, token in enumerate(self.__tokens):
            if not _is(token, Tok.PunctuatorKind.Equal) or index == 0:
                continue
            if not isinstance(self.__tokens[index - 1], Tok.Identifier):
                continue
            opening = self.__tokens[index - 2] if index >= 2 else None
            if opening is not None and _is(
                opening, Tok.PunctuatorKind.LParen, Tok.PunctuatorKind.Comma
            ):
                tight.add(index)
        return frozenset(tight)

    def __closure_bars(self) -> tuple[frozenset[int], frozenset[int]]:
        """Indices of the ``|`` tokens that open and close closure parameters.

        A ``|`` in a position where an operand could not end starts a closure; the
        next ``|`` closes it.  `a | b` has an operand on the left, so it stays a
        binary operator.
        """
        opens: set[int] = set()
        closes: set[int] = set()
        opening: int | None = None
        operand_end = False
        for index, token in enumerate(self.__tokens):
            if _is(token, Tok.PunctuatorKind.Pipe):
                if opening is None and not operand_end:
                    opening = index
                    opens.add(index)
                elif opening is not None:
                    closes.add(index)
                    opening = None
                operand_end = False
                continue
            operand_end = _word_like(token) or _is(token, *_CLOSERS) or index in self.__postfix
        return frozenset(opens), frozenset(closes)

    def __inline_indices(self) -> frozenset[int]:
        """Indices inside a brace pair whose braces share one source line."""
        stack: list[int] = []
        inline: set[int] = set()
        for index, token in enumerate(self.__tokens):
            if _is(token, Tok.PunctuatorKind.LBrace):
                stack.append(index)
            elif _is(token, Tok.PunctuatorKind.RBrace) and stack:
                open_index = stack.pop()
                if self.__tokens[open_index].span.start.row == token.span.start.row:
                    inline.update(range(open_index, index + 1))
        return frozenset(inline)

    def __is_inline(self, index: int) -> bool:
        return index in self.__inline


# ── tokens and text ────────────────────────────────────────────────────────────


#: Two punctuator kinds carry a descriptive enum value rather than their
#: spelling, so the engine maps them back before writing.
_SPELLING: dict[Tok.PunctuatorKind, str] = {
    Tok.PunctuatorKind.LAngle: "<",
    Tok.PunctuatorKind.RAngle: ">",
}


def token_text(token: Token) -> str:
    """The source spelling of *token*, which the engine re-emits unchanged."""
    if isinstance(token, Tok.Punctuator):
        return _SPELLING.get(token.kind, token.kind.value)
    if isinstance(token, Tok.Keyword):
        return token.kind.value
    if isinstance(token, Tok.Identifier):
        return token.name
    if isinstance(token, Tok.FStrStart):
        return token.raw
    if isinstance(token, (Tok.IntLiteral, Tok.FloatLiteral, Tok.CharLiteral, Tok.StrLiteral, Tok.BoolLiteral)):
        return token.raw
    # The remaining f-string parts only appear inside a region, which the engine
    # emits verbatim; nothing calls this for them.
    return ""


def _comment_text(comment: Comment) -> str:
    """A comment's text with the trailing whitespace of each line removed.

    The visible content is the author's and is never rewritten; only the ends of
    its lines are cleaned, which is the same rule the rest of the file gets.
    Single underscore: the class body above calls this, so the double form would
    be mangled (AGENTS.md).
    """
    return "\n".join(line.rstrip() for line in comment.text.split("\n"))


def _region(text: str, start: SrcPosition, end: SrcPosition) -> str:
    """The source between two positions (single underscore: module-level private
    the class body above calls, so it must not be mangled)."""
    lines = text.splitlines(keepends=True)
    start_column = max(0, start.col - 1)
    if start.row == end.row:
        return lines[start.row][start_column : end.col - 1] if start.row < len(lines) else ""
    pieces = [lines[start.row][start_column:]]
    pieces.extend(lines[start.row + 1 : end.row])
    pieces.append(lines[end.row][: end.col - 1] if end.row < len(lines) else "")
    return "".join(pieces)


def _is_eof(token: Token) -> bool:
    return _is(token, Tok.PunctuatorKind.EOF)
