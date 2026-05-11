from compiler.frontend.lex.token import Token
from compiler.utils.IR.position import SrcSpan


class ParseError(ValueError):
    def __init__(self, message: str, span: SrcSpan):
        super().__init__(message)
        self.span = span


class TokenStream:
    def __init__(self, tokens: list[Token]):
        self.__tokens = tokens
        self.__index = 0

    def at_end(self) -> bool:
        return self.__index >= len(self.__tokens)

    def peek(self) -> Token | None:
        if self.at_end():
            return None
        return self.__tokens[self.__index]

    def next(self) -> Token:
        if self.at_end():
            raise StopIteration("End of token stream reached")
        token = self.__tokens[self.__index]
        self.__index += 1
        return token

    def advance(self) -> None:
        if self.at_end():
            raise StopIteration("End of token stream reached")
        self.__index += 1

    def consume(self, expected_token: Token) -> Token:
        token = self.peek()
        if token is None:
            raise ValueError(f"Expected '{expected_token}' but got end of token stream")
        if token != expected_token:
            raise ValueError(f"Expected '{expected_token}' but got '{token}'")
        self.advance()
        return token


class Parser:
    def __init__(self, tokens: list[Token]):
        self.__stream = TokenStream(tokens)
