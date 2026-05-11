from compiler.frontend.lex.position import SrcPosition, SrcSpan
from compiler.frontend.lex.token import (EOF, Identifier, Keyword, KeywordKind, Literal, Punctuator, PunctuatorKind,
                                         Token)

START_IDENTIFIER = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ_")
IN_IDENTIFIER = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_")

START_NUMBER = set("0123456789")
IN_NUMBER = set("0123456789_xXbBoOeEpP+-abcdefABCDEFiuf")  # exclude '.'


class LexError(ValueError):
    def __init__(self, message: str, span: SrcSpan):
        super().__init__(message)
        self.span = span


class Lexer:
    def __init__(self, source: str):
        self.__source = source

        self.__pos = SrcPosition(0, 1)

        self.__index = 0
        self.__tokens: list[Token] = []

    def lex(self) -> None:
        """
        Lexes the source code into tokens.
        """
        while self.__index < len(self.__source):
            self.__tokens.append(self.__next_token())
        self.__tokens.append(EOF(self.__pos.into_span()))

    def export(self) -> list[Token]:
        """
        Exports the lexed tokens.
        """
        return self.__tokens

    def __next(self) -> str:
        """
        Returns the next character from the source code.
        """
        if self.__index >= len(self.__source):
            raise StopIteration("End of source code reached")
        char = self.__source[self.__index]
        self.__index += 1

        if char == "\n":
            self.__pos.row += 1
            self.__pos.col = 1
        else:
            self.__pos.col += 1

        return char

    def __skip_ignored(self) -> None:
        """
        Skips whitespace and line comments.
        """
        while self.__index < len(self.__source):
            ch = self.__peek()
            if ch is None:
                return
            if ch.isspace():
                self.__next()
                continue
            if ch == "/" and self.__peek_n(2) == "/":
                self.__skip_line_comment()
                continue
            return

    def __peek(self) -> str | None:
        """
        Peeks the next character from the source code without consuming it.
        """
        if self.__index >= len(self.__source):
            return None
        return self.__source[self.__index]

    def __peek_n(self, n: int) -> str | None:
        """
        Peeks the next n characters from the source code without consuming them.
        """
        if self.__index + n > len(self.__source):
            return None
        return self.__source[self.__index + n - 1]

    def __next_token(self) -> Token:
        """
        Lexes the next token from the source code.

        Assumes that the caller has already checked that there are more characters to read.
        """
        self.__skip_ignored()
        if self.__index >= len(self.__source):
            return EOF(self.__pos.into_span())

        start_pos = self.__pos.clone()
        ch = self.__next()

        if ch in START_IDENTIFIER:
            return self.__lex_identifier_or_keyword(ch, start_pos)
        elif ch in START_NUMBER:
            return self.__lex_number(ch, start_pos)
        elif ch == '"':
            return self.__lex_string(start_pos)
        elif ch == "'":
            return self.__lex_char(start_pos)
        else:
            return self.__lex_delimiter_or_operator(ch, start_pos)

    def __lex_identifier_or_keyword(self, tok_str: str, start_pos: SrcPosition) -> Token:
        """
        Lexes an identifier or a keyword from the source code.

        Assumes that the caller has already consumed the first character of the identifier or keyword.
        """
        c = self.__peek()
        while c is not None and c in IN_IDENTIFIER:
            self.__next()
            tok_str += c

            c = self.__peek()

        span = SrcSpan(start_pos, self.__pos.clone())
        tok = KeywordKind.try_from_str(tok_str)
        if tok is not None:
            return Keyword(tok, span)
        else:
            return Identifier(tok_str, span)

    def __lex_number(self, tok_str: str, start_pos: SrcPosition) -> Token:
        """
        Lexes a number literal from the source code.

        Assumes that the caller has already consumed the first character of the number literal.
        """
        c = self.__peek()
        while c is not None and c in IN_NUMBER:
            self.__next()
            tok_str += c

            c = self.__peek()

        if c == ".":
            c_after_dot = self.__peek_n(2)
            if c_after_dot in START_NUMBER:
                self.__next()
                tok_str += "."
                self.__next()
                tok_str += c_after_dot
                c = self.__peek()
                while c is not None and c in IN_NUMBER:
                    self.__next()
                    tok_str += c

                    c = self.__peek()

        span = SrcSpan(start_pos, self.__pos.clone())
        try:
            return Literal(tok_str, span)
        except ValueError as exc:
            raise LexError(str(exc), span) from exc

    def __lex_string(self, start_pos: SrcPosition) -> Token:
        """
        Lexes a string literal from the source code.

        Assumes that the caller has already consumed the opening double quote.
        """
        tok_str = "\""
        c = self.__peek()
        while c is not None and c != "\"":
            # if the current character is a backslash
            # we need to accept the next character even if it is a double quote
            if c == "\\":
                self.__next()
                tok_str += "\\"
                c = self.__peek()
                if c is not None:
                    self.__next()
                    tok_str += c
                    c = self.__peek()
            else:
                self.__next()
                tok_str += c
                c = self.__peek()

        if c is None or c != "\"":
            raise LexError("Unterminated string literal", SrcSpan(start_pos, self.__pos.clone()))

        tok_str += "\""
        self.__next()
        span = SrcSpan(start_pos, self.__pos.clone())
        try:
            return Literal(tok_str, span)
        except ValueError as exc:
            raise LexError(str(exc), span) from exc

    def __lex_char(self, start_pos: SrcPosition) -> Token:
        """
        Lexes a char literal from the source code.

        Assumes that the caller has already consumed the opening single quote.
        """
        tok_str = "'"
        c = self.__peek()
        while c is not None and c != "'":
            # if the current character is a backslash
            # we need to accept the next character even if it is a single quote
            if c == "\\":
                self.__next()
                tok_str += "\\"
                c = self.__peek()
                if c is not None:
                    self.__next()
                    tok_str += c
                    c = self.__peek()
            else:
                self.__next()
                tok_str += c
                c = self.__peek()

        if c is None or c != "'":
            raise LexError("Unterminated char literal", SrcSpan(start_pos, self.__pos.clone()))

        tok_str += "'"
        self.__next()
        span = SrcSpan(start_pos, self.__pos.clone())
        try:
            return Literal(tok_str, span)
        except ValueError as exc:
            raise LexError(str(exc), span) from exc

    def __skip_line_comment(self) -> None:
        """
        Skips a line comment starting at the current slash.
        """
        self.__next()
        self.__next()

        while self.__index < len(self.__source):
            if self.__peek() == "\n":
                return
            self.__next()

    def __lex_delimiter_or_operator(self, tok_str: str, start_pos: SrcPosition) -> Token:
        """
        Lexes a delimiter or an operator from the source code.

        Assumes that the caller has already consumed the first character of the delimiter or operator.
        """
        if tok_str == "\n":
            return Punctuator(PunctuatorKind.Endl, SrcSpan(start_pos, self.__pos.clone()))

        next_char = self.__peek()
        if next_char is not None:
            combined = tok_str + next_char
            if PunctuatorKind.try_from_str(combined) is not None:
                self.__next()
                tok_str = combined

        span = SrcSpan(start_pos, self.__pos.clone())
        try:
            kind = PunctuatorKind.from_str(tok_str)
        except ValueError as exc:
            raise LexError(str(exc), span) from exc
        return Punctuator(kind, span)
