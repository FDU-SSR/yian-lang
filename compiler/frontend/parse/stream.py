from __future__ import annotations
from typing import Callable

from compiler.frontend.lex.token import Identifier, Keyword, KeywordKind, Literal, LiteralKind, Punctuator, PunctuatorKind, Token
from compiler.frontend.parse import ast as AST
from compiler.frontend.parse.parser import ParseError


class TokenStream:
    def __init__(self, tokens: list[Token]):
        self.__tokens = tokens
        self.__index = 0

        self.__concat_space()

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

    def consume_keyword(self, expected_kind: KeywordKind) -> Keyword:
        token = self.peek()
        if token is None:
            raise ValueError(f"Expected keyword '{expected_kind}' but got end of token stream")
        if not isinstance(token, Keyword) or token.kind != expected_kind:
            raise ValueError(f"Expected keyword '{expected_kind}' but got '{token}'")
        self.advance()
        return token

    def consume_punctuator(self, expected_kind: PunctuatorKind) -> Punctuator:
        token = self.peek()
        if token is None:
            raise ValueError(f"Expected punctuator '{expected_kind}' but got end of token stream")
        if not isinstance(token, Punctuator) or token.kind != expected_kind:
            raise ValueError(f"Expected punctuator '{expected_kind}' but got '{token}'")
        self.advance()
        return token

    def consume_spaces(self) -> None:
        """Skips consecutive space tokens."""
        while not self.at_end():
            token = self.peek()
            if isinstance(token, Punctuator) and token.kind in {PunctuatorKind.Space, PunctuatorKind.Endl}:
                self.advance()
            else:
                break

    def consume_attrs(self) -> list[AST.Attr]:
        """Consumes attributes (e.g., #[attr]) and returns them as a list."""
        attrs: list[AST.Attr] = []
        while not self.at_end():
            self.consume_spaces()
            match self.peek():
                case Keyword(KeywordKind.Pub, span):
                    self.advance()
                    attrs.append(AST.Attr(span=span, kind=AST.AttrKind.Pub))
                case Keyword(KeywordKind.Static, span):
                    self.advance()
                    attrs.append(AST.Attr(span=span, kind=AST.AttrKind.Static))
                case _:
                    break
        return attrs

    def consume_separated[ItemType](self, item_parser: Callable[[], ItemType], separators: set[PunctuatorKind]) -> list[ItemType]:
        """Consumes a separated list of items parsed by the given item_parser function."""
        items: list[ItemType] = []
        while True:
            self.consume_spaces()
            items.append(item_parser())
            self.consume_spaces()

            token = self.peek()
            if isinstance(token, Punctuator) and token.kind in separators:
                self.consume_punctuator(token.kind)
            else:
                break
        return items

    def consume_until[ItemType](self, item_parser: Callable[[], ItemType], terminators: set[PunctuatorKind]) -> list[ItemType]:
        """Consumes items parsed by the given item_parser function until a terminator is encountered."""
        items: list[ItemType] = []
        while True:
            self.consume_spaces()
            token = self.peek()
            if isinstance(token, Punctuator) and token.kind in terminators:
                break
            items.append(item_parser())
        return items

    def consume_generics(self) -> list[AST.Identifier]:
        """Consumes generic parameters enclosed in angle brackets and returns them as a list of identifiers."""
        generics: list[AST.Identifier] = []
        token = self.peek()
        if isinstance(token, Punctuator) and token.kind == PunctuatorKind.Less:
            self.consume_punctuator(PunctuatorKind.Less)
            generics = self.consume_separated(self.consume_identifier, {PunctuatorKind.Comma})
            self.consume_punctuator(PunctuatorKind.Greater)

        return generics

    def consume_identifier(self) -> AST.Identifier:
        """Consumes and returns the next token if it is an identifier/keyword, otherwise raises an error."""
        token = self.next()
        match token:
            case Keyword(kind, span):
                self.advance()
                return AST.Identifier(name=kind.value, span=span)
            case Identifier(name, span):
                self.advance()
                return AST.Identifier(name=name, span=span)
            case _:
                raise ValueError(f"Expected identifier or keyword but got '{token}'")

    def consume_integer_literal(self) -> int:
        """Consumes and returns the next token if it is an integer literal, otherwise raises an error."""
        token = self.next()
        match token:
            case Literal(kind=LiteralKind.Integer, value=value):
                self.advance()
                assert isinstance(value, int)
                return value
            case _:
                raise ParseError(f"Expected integer literal but got '{token}'", token.span)

    def function_like(self) -> bool:
        """Checks if the following tokens match the pattern of a function definition."""
        raise NotImplementedError("Function-like pattern checking not implemented yet")

    def __concat_space(self) -> None:
        """Concatenates consecutive space tokens into a single token with the combined span."""
        concatenated_tokens: list[Token] = []
        current_space_token: Punctuator | None = None

        for token in self.__tokens:
            if not isinstance(token, Punctuator) or token.kind != PunctuatorKind.Space:
                if current_space_token is not None:
                    concatenated_tokens.append(current_space_token)
                    current_space_token = None
                concatenated_tokens.append(token)
            else:
                if current_space_token is None:
                    current_space_token = token
                else:
                    # Extend the span of the current space token to include the new one
                    current_space_token.span += token.span

        if current_space_token is not None:
            concatenated_tokens.append(current_space_token)

        self.__tokens = concatenated_tokens
