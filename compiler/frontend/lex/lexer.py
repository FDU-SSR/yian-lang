from compiler.frontend.lex.position import SrcPosition, SrcSpan
from compiler.frontend.lex.token import EOF, Identifier, Keyword, KeywordKind, Literal, Token


START_IDENTIFIER = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ_")
IN_IDENTIFIER = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_")

START_NUMBER = set("0123456789")
IN_NUMBER = set("0123456789_xXbBoO.eEpP+-abcdefABCDEFiuf")


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

        Assumes that the caller has already checked that there are more characters to read.
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

    def __next_char(self) -> str:
        """
        Returns the next non-whitespace character from the source code.

        Assumes that the caller has already checked that there are more characters to read.
        """
        ch = self.__next()
        while ch.isspace() and ch != "\n":
            ch = self.__next()
        return ch

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
        return self.__source[self.__index:self.__index + n]

    def __next_token(self) -> Token:
        """
        Lexes the next token from the source code.

        Assumes that the caller has already checked that there are more characters to read.
        """
        ch = self.__next_char()

        if ch in START_IDENTIFIER:
            return self.__lex_identifier_or_keyword(ch)
        elif ch in START_NUMBER:
            return self.__lex_number(ch)
        elif ch == '"':
            return self.__lex_string()
        elif ch == "'":
            return self.__lex_char()
        else:
            return self.__lex_delimiter_or_operator(ch)

    def __lex_identifier_or_keyword(self, ch: str) -> Token:
        """
        Lexes an identifier or a keyword from the source code.

        Assumes that the caller has already consumed the first character of the identifier or keyword.
        """
        start_pos = self.__pos.clone()

        c = self.__peek()
        while c is not None and c in IN_IDENTIFIER:
            self.__next()
            ch += c

            c = self.__peek()

        span = SrcSpan(start_pos, self.__pos.clone())
        tok = KeywordKind.try_from_str(ch)
        if tok is not None:
            return Keyword(tok, span)
        else:
            return Identifier(ch, span)

    def __lex_number(self, ch: str) -> Token:
        """
        Lexes a number literal from the source code.

        Assumes that the caller has already consumed the first character of the number literal.
        """
        start_pos = self.__pos.clone()

        c = self.__peek()
        while c is not None and c in IN_NUMBER:
            self.__next()
            ch += c

            c = self.__peek()

        span = SrcSpan(start_pos, self.__pos.clone())
        return Literal(ch, span)

    def __lex_string(self) -> Token:
        """
        Lexes a string literal from the source code.

        Assumes that the caller has already consumed the opening double quote.
        """
        start_pos = self.__pos.clone()

        ch = "\""
        c = self.__peek()
        while c is not None and c != "\"":
            # if the current character is a backslash
            # we need to accept the next character even if it is a double quote
            if c == "\\":
                self.__next()
                ch += "\\"
                c = self.__peek()
                if c is not None:
                    self.__next()
                    ch += c
                    c = self.__peek()
            else:
                self.__next()
                ch += c
                c = self.__peek()

        if c is None or c != "\"":
            raise ValueError("Unterminated string literal")

        ch += "\""
        self.__next()
        span = SrcSpan(start_pos, self.__pos.clone())
        return Literal(ch, span)

    def __lex_char(self) -> Token:
        """
        Lexes a char literal from the source code.

        Assumes that the caller has already consumed the opening single quote.
        """
        # TODO
        return EOF(self.__pos.into_span())

    def __lex_delimiter_or_operator(self, ch: str) -> Token:
        """
        Lexes a delimiter or an operator from the source code.

        Assumes that the caller has already consumed the first character of the delimiter or operator.
        """
        # TODO
        return EOF(self.__pos.into_span())
