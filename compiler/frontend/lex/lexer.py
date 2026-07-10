"""Character-at-a-time lexer — whitespace-insensitive, produces Token stream."""

from __future__ import annotations

# from compiler.frontend.lex.token import Identifier, Keyword, KeywordKind, Literal, Punctuator, PunctuatorKind, Token
from pathlib import Path

from compiler.frontend.lex import token as Tok
from compiler.frontend.lex.token import Token
from compiler.frontend.lex.position import SrcPosition, SrcSpan
from compiler.utils.log import CompilerLog

ch_lex = lambda: CompilerLog.get("lex")

START_IDENTIFIER = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ_")
IN_IDENTIFIER = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_")

START_NUMBER = set("0123456789")
IN_NUMBER = set("0123456789_xXbBoOeEpP+-abcdefABCDEFiuf")  # exclude '.'


class LexError(ValueError):
    def __init__(self, message: str, span: SrcSpan):
        super().__init__(message)
        self.span = span


class CharStream:
    def __init__(self, path: Path):
        self.__source = path.read_text()
        self.__src_len = len(self.__source)
        self.__index = 0
        self.pos = SrcPosition(0, 1, path)

    def at_end(self) -> bool:
        return self.__index >= self.__src_len

    def next(self) -> str:
        if self.__index >= self.__src_len:
            raise StopIteration("End of source code reached")
        ch = self.__source[self.__index]
        self.__index += 1

        if ch == "\n":
            self.pos.row += 1
            self.pos.col = 1
        else:
            self.pos.col += 1

        return ch

    def advance(self) -> None:
        if self.__index >= self.__src_len:
            raise StopIteration("End of source code reached")

        if self.__source[self.__index] == "\n":
            self.pos.row += 1
            self.pos.col = 1
        else:
            self.pos.col += 1

        self.__index += 1

    def advance_n(self, n: int) -> None:
        for i in range(n):
            if self.__index >= self.__src_len:
                raise StopIteration("End of source code reached")
            if self.__source[self.__index] == "\n":
                self.pos.row += 1
                self.pos.col = 1
            else:
                self.pos.col += 1
            self.__index += 1

    def peek(self) -> str | None:
        if self.__index >= self.__src_len:
            return None
        return self.__source[self.__index]

    def peek_n(self, n: int) -> str | None:
        if self.__index + n > self.__src_len:
            return None
        return self.__source[self.__index:self.__index + n]

    def consume(self, expected: str) -> str:
        elen = len(expected)
        if self.__index + elen > self.__src_len or self.__source[self.__index:self.__index + elen] != expected:
            raise ValueError(f"Expected '{expected}'")
        for ch in expected:
            if ch == "\n":
                self.pos.row += 1
                self.pos.col = 1
            else:
                self.pos.col += 1
        self.__index += elen
        return expected

    # ── bulk-skip methods (operate directly on the string for speed) ──────

    def skip_ws(self) -> bool:
        """Skip whitespace characters in bulk. Returns True if any were skipped."""
        src = self.__source
        idx = self.__index
        end = self.__src_len
        start = idx
        while idx < end and src[idx].isspace():
            if src[idx] == "\n":
                self.pos.row += 1
                self.pos.col = 1
            else:
                self.pos.col += 1
            idx += 1
        self.__index = idx
        return idx != start

    def skip_line_comment(self) -> None:
        """Skip to end of line (current position is just after '//')."""
        src = self.__source
        idx = self.__index
        end = self.__src_len
        while idx < end and src[idx] != "\n":
            idx += 1
        if idx < end:
            idx += 1  # consume the newline
        self.pos.row += 1
        self.pos.col = 1
        self.__index = idx

    def peek_nth(self, n: int) -> str | None:
        """Peek the nth character ahead (1-based)."""
        pos = self.__index + n - 1
        if pos >= self.__src_len:
            return None
        return self.__source[pos]

    def skip_block_comment(self) -> None:
        """Skip to '*/' (current position is just after '/*')."""
        src = self.__source
        idx = self.__index
        end = self.__src_len
        while idx + 1 < end and not (src[idx] == "*" and src[idx + 1] == "/"):
            if src[idx] == "\n":
                self.pos.row += 1
                self.pos.col = 1
            else:
                self.pos.col += 1
            idx += 1
        if idx + 1 >= end:
            raise LexError("Unterminated block comment", self.pos.into_span())
        self.__index = idx + 2  # skip '*/'
        self.pos.col += 2


class Lexer:
    def __init__(self, path: Path):
        self.__stream = CharStream(path)
        self.__tokens: list[Token] = []

    def lex(self) -> None:
        """
        Lexes the source code into tokens.
        """
        while True:
            prev_was_ws = self.__skip_ignored()
            token = self.__next_token(prev_was_ws)
            ch_lex().trace(lambda: str(token))
            self.__tokens.append(token)
            if isinstance(token, Tok.Punctuator) and token.kind == Tok.PunctuatorKind.EOF:
                break

    def export(self) -> list[Token]:
        """
        Exports the lexed tokens.
        """
        return self.__tokens

    def __skip_ignored(self) -> bool:
        """Skip whitespace and comments. Returns True if any whitespace was consumed."""
        saw_whitespace = False
        while not self.__stream.at_end():
            ch = self.__stream.peek()
            if ch is None:
                return saw_whitespace
            if ch.isspace():
                self.__stream.skip_ws()
                saw_whitespace = True
                continue
            if ch == "/":
                n2 = self.__stream.peek_n(2)
                if n2 == "//":
                    self.__stream.consume("//")
                    self.__stream.skip_line_comment()
                    saw_whitespace = True
                    continue
                if n2 == "/*":
                    self.__stream.consume("/*")
                    self.__stream.skip_block_comment()
                    saw_whitespace = True
                    continue
            return saw_whitespace
        return saw_whitespace

    def __next_token(self, prev_was_ws: bool) -> Token:
        """
        Lexes the next token from the source code.

        Assumes that the caller has already checked that there are more characters to read.
        """
        if self.__stream.at_end():
            return Tok.Punctuator(Tok.PunctuatorKind.EOF, self.__stream.pos.into_span())

        start_pos = self.__stream.pos.clone()
        ch = self.__stream.next()

        if ch == '"':
            return self.__lex_string(start_pos)
        if ch == "'":
            return self.__lex_char(start_pos)
        if ch == "b" and self.__stream.peek() == "'":
            return self.__lex_byte(start_pos)
        if ch in START_IDENTIFIER:
            return self.__lex_identifier_or_keyword(ch, start_pos)
        if ch in START_NUMBER:
            return self.__lex_number(ch, start_pos)
        return self.__lex_punctuator(ch, start_pos, prev_was_ws)

    def __lex_identifier_or_keyword(self, tok_str: str, start_pos: SrcPosition) -> Token:
        """
        Lexes an identifier or a keyword from the source code.

        Assumes that the caller has already consumed the first character of the identifier or keyword.
        """
        c = self.__stream.peek()
        while c is not None and c in IN_IDENTIFIER:
            tok_str += self.__stream.next()
            c = self.__stream.peek()

        span = SrcSpan(start_pos, self.__stream.pos.clone())
        tok = Tok.KeywordKind.try_from_str(tok_str)
        if tok is not None:
            return Tok.Keyword(tok, span)
        else:
            return Tok.Identifier(tok_str, span)

    def __lex_number(self, tok_str: str, start_pos: SrcPosition) -> Token:
        """
        Lexes a number literal from the source code.

        Assumes that the caller has already consumed the first character of the number literal.
        """
        c = self.__stream.peek()
        while c is not None and c in IN_NUMBER:
            tok_str += self.__stream.next()
            c = self.__stream.peek()

        if c == ".":
            c_after_dot = self.__stream.peek_nth(2)
            if c_after_dot in START_NUMBER:
                tok_str += self.__stream.consume(".")
                tok_str += self.__stream.next()
                c = self.__stream.peek()
                while c is not None and c in IN_NUMBER:
                    tok_str += self.__stream.next()
                    c = self.__stream.peek()

        span = SrcSpan(start_pos, self.__stream.pos.clone())
        try:
            if any(c in ".pP" for c in tok_str) or (not tok_str.startswith(("0x", "0X")) and any(c in "eE" for c in tok_str)):
                value, suffix = Tok.parse_float_value(tok_str)
                return Tok.FloatLiteral(tok_str, span, value, suffix)
            value, suffix = Tok.parse_integer_value(tok_str)
            return Tok.IntLiteral(tok_str, span, value, suffix)
        except ValueError as exc:
            raise LexError(str(exc), span) from exc

    def __lex_string(self, start_pos: SrcPosition) -> Token:
        """
        Lexes a string literal from the source code.

        Assumes that the caller has already consumed the opening double quote.
        """
        tok_str = "\""
        c = self.__stream.peek()
        while c is not None and c != "\"":
            tok_str += self.__stream.next()

            # if the current character is a backslash
            # we need to accept the next character even if it is a double quote
            if c == "\\":
                c = self.__stream.peek()
                if c is not None:
                    tok_str += self.__stream.next()

            c = self.__stream.peek()

        # after loop, make sure we ended with a closing quote
        tok_str += self.__stream.consume("\"")

        span = SrcSpan(start_pos, self.__stream.pos.clone())
        try:
            return Tok.StrLiteral(tok_str, span, Tok.parse_string_value(tok_str))
        except ValueError as exc:
            raise LexError(str(exc), span) from exc

    def __lex_char(self, start_pos: SrcPosition) -> Token:
        """
        Lexes a char literal from the source code.

        Assumes that the caller has already consumed the opening single quote.
        """
        tok_str = "'"
        c = self.__stream.peek()
        while c is not None and c != "'":
            tok_str += self.__stream.next()

            # if the current character is a backslash
            # we need to accept the next character even if it is a single quote
            if c == "\\":
                c = self.__stream.peek()
                if c is not None:
                    tok_str += self.__stream.next()

            c = self.__stream.peek()

        # after loop, make sure we ended with a closing quote
        tok_str += self.__stream.consume("'")

        span = SrcSpan(start_pos, self.__stream.pos.clone())
        try:
            return Tok.CharLiteral(tok_str, span, Tok.parse_char_value(tok_str))
        except ValueError as exc:
            raise LexError(str(exc), span) from exc

    def __lex_byte(self, start_pos: SrcPosition) -> Token:
        """
        Lexes a byte literal (integer literal with a 'b' prefix) from the source code.
        """
        tok_str = "b'"
        self.__stream.consume("'")  # consume the opening single quote after 'b'
        c = self.__stream.peek()
        while c is not None and c != "'":
            tok_str += self.__stream.next()

            # if the current character is a backslash
            # we need to accept the next character even if it is a single quote
            if c == "\\":
                c = self.__stream.peek()
                if c is not None:
                    tok_str += self.__stream.next()

            c = self.__stream.peek()

        # after loop, make sure we ended with a closing quote
        tok_str += self.__stream.consume("'")

        span = SrcSpan(start_pos, self.__stream.pos.clone())
        try:
            return Tok.IntLiteral(tok_str, span, Tok.parse_byte_value(tok_str), suffix="u8")
        except ValueError as exc:
            raise LexError(str(exc), span) from exc

    def __lex_punctuator(self, tok_str: str, start_pos: SrcPosition, prev_was_ws: bool) -> Token:
        """
        Lexes a delimiter or an operator from the source code.

        Uses whitespace context to distinguish:
        - `<` without preceding whitespace → LAngle (generic opening)
        - `<` with preceding whitespace    → Less (comparison)
        - `>` without preceding whitespace → RAngle (generic closing)
        - `>` with preceding whitespace    → Greater (comparison)
        """
        span = SrcSpan(start_pos, self.__stream.pos.clone())

        if tok_str == "<" and not prev_was_ws:
            # Generic opening bracket — don't merge with following <
            return Tok.Punctuator(Tok.PunctuatorKind.LAngle, span)

        if tok_str == ">" and not prev_was_ws:
            # Generic closing bracket — don't merge with following >
            return Tok.Punctuator(Tok.PunctuatorKind.RAngle, span)

        # Multi-character operator combining — keep merging while the
        # combined string is a valid token kind (handles <<=, >>=).
        while True:
            next_char = self.__stream.peek()
            if next_char is None:
                break
            combined = tok_str + next_char
            if Tok.PunctuatorKind.try_from_str(combined) is None:
                break
            self.__stream.advance()
            tok_str = combined
            span = SrcSpan(start_pos, self.__stream.pos.clone())

        try:
            kind = Tok.PunctuatorKind.from_str(tok_str)
        except ValueError as exc:
            raise LexError(str(exc), span) from exc
        return Tok.Punctuator(kind, span)
