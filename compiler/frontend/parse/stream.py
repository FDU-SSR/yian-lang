from __future__ import annotations

from typing import Callable

from compiler.frontend.lex import token as Tok
from compiler.frontend.lex.token import Token
from compiler.frontend.parse import ast as AST
from compiler.frontend.parse.error import ParseError


class TokenStream:
    def __init__(self, tokens: list[Token]):
        self.__tokens = tokens
        self.__index = 0

        self.__concat_space()

    def at_end(self) -> bool:
        token = self.peek()
        return isinstance(token, Tok.Punctuator) and token.kind == Tok.PunctuatorKind.EOF

    def peek(self) -> Token:
        return self.__tokens[self.__index]

    def peek_nth(self, n: int) -> Token | None:
        if self.__index + n >= len(self.__tokens):
            return None
        return self.__tokens[self.__index + n]

    def peek_n(self, n: int) -> list[Token]:
        return self.__tokens[self.__index: self.__index + n]

    def next(self) -> Token:
        if self.at_end():
            raise ParseError("End of token stream reached", self.peek().span)
        token = self.__tokens[self.__index]
        self.__index += 1
        return token

    def advance(self) -> None:
        if self.at_end():
            raise ParseError("End of token stream reached", self.peek().span)
        self.__index += 1

    def consume_keyword(self, expected_kind: Tok.KeywordKind) -> Tok.Keyword:
        token = self.peek()
        if not isinstance(token, Tok.Keyword) or token.kind != expected_kind:
            raise ParseError(f"Expected keyword '{expected_kind}' but got '{token}'", token.span)
        self.advance()
        return token

    def consume_punctuator(self, expected_kind: Tok.PunctuatorKind) -> Tok.Punctuator:
        token = self.peek()
        if not isinstance(token, Tok.Punctuator) or token.kind != expected_kind:
            raise ParseError(f"Expected punctuator '{expected_kind}' but got '{token}'", token.span)
        self.advance()
        return token

    def consume_spaces(self) -> None:
        """Skips consecutive space tokens."""
        while not self.at_end():
            token = self.peek()
            if isinstance(token, Tok.Punctuator) and token.kind in {Tok.PunctuatorKind.Space, Tok.PunctuatorKind.Endl}:
                self.advance()
            else:
                break

    def consume_spaces_inline(self) -> None:
        """Skips consecutive space tokens but stops at newlines."""
        while not self.at_end():
            token = self.peek()
            if isinstance(token, Tok.Punctuator) and token.kind == Tok.PunctuatorKind.Space:
                self.advance()
            else:
                break

    def consume_attrs(self) -> list[AST.Attr]:
        """Consumes attributes (e.g., #[attr]) and returns them as a list."""
        attrs: list[AST.Attr] = []
        while not self.at_end():
            self.consume_spaces()
            match self.peek():
                case Tok.Keyword(kind=Tok.KeywordKind.Pub, span=span):
                    self.advance()
                    attrs.append(AST.Attr(span=span, kind=AST.AttrKind.Pub))
                case Tok.Keyword(kind=Tok.KeywordKind.Static, span=span):
                    self.advance()
                    attrs.append(AST.Attr(span=span, kind=AST.AttrKind.Static))
                case _:
                    break
        return attrs

    def consume_separated[ItemType](self, item_parser: Callable[[], ItemType], separators: set[Tok.PunctuatorKind], terminators: set[Tok.PunctuatorKind]) -> list[ItemType]:
        """Consumes a separated list of items parsed by the given item_parser function."""
        items: list[ItemType] = []
        token = self.peek()
        while not (isinstance(token, Tok.Punctuator) and token.kind in terminators):
            self.consume_spaces()
            items.append(item_parser())
            self.consume_spaces_inline()

            token = self.peek()
            if isinstance(token, Tok.Punctuator) and token.kind in separators:
                self.consume_punctuator(token.kind)
                self.consume_spaces_inline()
                token = self.peek()
            else:
                break
        return items

    def consume_until[ItemType](self, item_parser: Callable[[], ItemType], terminators: set[Tok.PunctuatorKind]) -> list[ItemType]:
        """Consumes items parsed by the given item_parser function until a terminator is encountered."""
        items: list[ItemType] = []
        while True:
            self.consume_spaces()
            token = self.peek()
            if isinstance(token, Tok.Punctuator) and token.kind in terminators:
                break
            items.append(item_parser())
        return items

    def consume_generics(self) -> list[AST.Identifier]:
        """Consumes generic parameters enclosed in angle brackets and returns them as a list of identifiers."""
        generics: list[AST.Identifier] = []
        token = self.peek()
        if isinstance(token, Tok.Punctuator) and token.kind == Tok.PunctuatorKind.Less:
            self.consume_punctuator(Tok.PunctuatorKind.Less)
            generics = self.consume_separated(self.consume_identifier, {Tok.PunctuatorKind.Comma}, {Tok.PunctuatorKind.Greater})
            self.consume_punctuator(Tok.PunctuatorKind.Greater)

        return generics

    def consume_identifier(self) -> AST.Identifier:
        """Consumes and returns the next token if it is an identifier/keyword, otherwise raises an error."""
        token = self.next()
        match token:
            case Tok.Keyword(kind, span):
                return AST.Identifier(name=kind.value, span=span)
            case Tok.Identifier(name, span):
                return AST.Identifier(name=name, span=span)
            case _:
                raise ParseError(f"Expected identifier or keyword but got '{token}'", token.span)

    def consume_integer_literal(self) -> int:
        """Consumes and returns the next token if it is an integer literal, otherwise raises an error."""
        token = self.next()
        match token:
            case Tok.IntLiteral(value=value):
                return value
            case _:
                raise ParseError(f"Expected integer literal but got '{token}'", token.span)

    def __concat_space(self) -> None:
        """Concatenates consecutive space tokens into a single token with the combined span."""
        concatenated_tokens: list[Token] = []
        current_space_token: Tok.Punctuator | None = None

        for token in self.__tokens:
            if not isinstance(token, Tok.Punctuator) or token.kind != Tok.PunctuatorKind.Space:
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

    def function_like(self) -> bool:
        """
        Checks if the following tokens match the pattern of a function definition.

        Ident<IdentT1, IdentT2, ...>(
        """
        index = 0

        def check_identifier() -> bool:
            nonlocal index
            token = self.peek_nth(index)
            if not isinstance(token, Tok.Identifier):
                return False
            index += 1
            return True

        def skip_spaces() -> None:
            nonlocal index
            while True:
                token = self.peek_nth(index)
                if isinstance(token, Tok.Punctuator) and token.kind == Tok.PunctuatorKind.Space:
                    index += 1
                else:
                    break

        # Check for identifier
        if not check_identifier():
            return False

        # Check for optional generic parameters
        token = self.peek_nth(index)
        if isinstance(token, Tok.Punctuator) and token.kind == Tok.PunctuatorKind.Less:
            index += 1
            while True:
                skip_spaces()
                if not check_identifier():
                    return False
                skip_spaces()
                token = self.peek_nth(index)
                if isinstance(token, Tok.Punctuator) and token.kind == Tok.PunctuatorKind.Comma:
                    index += 1
                elif isinstance(token, Tok.Punctuator) and token.kind == Tok.PunctuatorKind.Greater:
                    index += 1
                    break
                else:
                    return False

        # Check for opening parenthesis
        token = self.peek_nth(index)
        if not isinstance(token, Tok.Punctuator) or token.kind != Tok.PunctuatorKind.LParen:
            return False

        return True

    def var_decl_like(self) -> bool:
        """
        Checks if the following tokens match the pattern of a variable declaration.

        Type Ident =
        """
        token = self.peek()
        if isinstance(token, Tok.Keyword) and token.kind in {Tok.KeywordKind.Fn}:
            return True

        index = 0

        def skip_brackets(open_kind: Tok.PunctuatorKind, close_kind: Tok.PunctuatorKind) -> None:
            nonlocal index
            token = self.peek_nth(index)
            if isinstance(token, Tok.Punctuator) and token.kind == open_kind:
                index += 1
                depth = 1
                while depth > 0:
                    token = self.peek_nth(index)
                    if token is None:
                        return
                    if isinstance(token, Tok.Punctuator):
                        if token.kind == open_kind:
                            depth += 1
                        elif token.kind == close_kind:
                            depth -= 1
                    index += 1

        # Check for type
        while True:
            token = self.peek_nth(index)
            match token:
                case Tok.Keyword():
                    index += 1
                case Tok.Identifier():
                    index += 1
                case Tok.Punctuator(kind=Tok.PunctuatorKind.Star):
                    index += 1
                case Tok.Punctuator(kind=Tok.PunctuatorKind.LBracket):
                    skip_brackets(Tok.PunctuatorKind.LBracket, Tok.PunctuatorKind.RBracket)
                case Tok.Punctuator(kind=Tok.PunctuatorKind.Less):
                    skip_brackets(Tok.PunctuatorKind.Less, Tok.PunctuatorKind.Greater)
                case Tok.Punctuator(kind=Tok.PunctuatorKind.LParen):
                    skip_brackets(Tok.PunctuatorKind.LParen, Tok.PunctuatorKind.RParen)
                case _:
                    break

        # Check for space
        token = self.peek_nth(index)
        if not isinstance(token, Tok.Punctuator) or token.kind != Tok.PunctuatorKind.Space:
            return False
        index += 1

        # Check for identifier
        token = self.peek_nth(index)
        if not isinstance(token, Tok.Identifier):
            return False

        return True
