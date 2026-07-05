from __future__ import annotations

from typing import Callable

from compiler.frontend.lex import token as Tok
from compiler.frontend.lex.token import Token
from compiler.frontend.parse import ast as AST
from compiler.frontend.parse.error import ParseError

# ------------------------------------------------------------------
# frozenset常量：consume_separated / consume_until 的参数
# 避免在热路径上重复构造set字面量
# ------------------------------------------------------------------

SEP_COMMA = frozenset({Tok.PunctuatorKind.Comma})
SEP_DOT = frozenset({Tok.PunctuatorKind.Dot})
SEP_PIPE = frozenset({Tok.PunctuatorKind.Pipe})
TERM_SEMICOLON = frozenset({Tok.PunctuatorKind.Semicolon})
TERM_RPAREN = frozenset({Tok.PunctuatorKind.RParen})
TERM_RBRACE = frozenset({Tok.PunctuatorKind.RBrace})
TERM_RANGLE = frozenset({Tok.PunctuatorKind.RAngle})
TERM_RBRACKET = frozenset({Tok.PunctuatorKind.RBracket})
TERM_FAT_ARROW = frozenset({Tok.PunctuatorKind.FatArrow})
SEMI_OR_COMMA = frozenset({Tok.PunctuatorKind.Semicolon, Tok.PunctuatorKind.Comma})
EMPTY_SET: frozenset[Tok.PunctuatorKind] = frozenset()


class TokenStream:
    def __init__(self, tokens: list[Token]):
        self.__tokens = tokens
        self.__index = 0

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

    def consume_annots(self) -> list[AST.Annot]:
        """Consumes annotations (e.g., @BitCopy) and returns them as a list."""
        annots: list[AST.Annot] = []
        while not self.at_end():
            match self.peek():
                case Tok.Punctuator(kind=Tok.PunctuatorKind.At, span=at_span):
                    self.advance()
                    ident = self.consume_identifier()
                    kind = AST.AnnotKind.try_from_name(ident.name)
                    if kind is None:
                        raise ParseError(f"unknown annotation '@{ident.name}'", at_span + ident.span)
                    annots.append(AST.Annot(span=at_span + ident.span, kind=kind))
                case _:
                    break
        return annots

    def consume_attrs(self) -> list[AST.Attr]:
        """Consumes attributes (e.g., #[attr]) and returns them as a list."""
        attrs: list[AST.Attr] = []
        while not self.at_end():
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

    def consume_separated[ItemType](self, item_parser: Callable[[], ItemType], separators: frozenset[Tok.PunctuatorKind], terminators: frozenset[Tok.PunctuatorKind]) -> list[ItemType]:
        """Consumes a separated list of items parsed by the given item_parser function."""
        items: list[ItemType] = []
        token = self.peek()
        while not (isinstance(token, Tok.Punctuator) and token.kind in terminators):
            items.append(item_parser())

            token = self.peek()
            if isinstance(token, Tok.Punctuator) and token.kind in separators:
                self.consume_punctuator(token.kind)
                token = self.peek()
            else:
                break
        return items

    def consume_until[ItemType](self, item_parser: Callable[[], ItemType], terminators: frozenset[Tok.PunctuatorKind]) -> list[ItemType]:
        """Consumes items parsed by the given item_parser function until a terminator is encountered."""
        items: list[ItemType] = []
        while True:
            token = self.peek()
            if isinstance(token, Tok.Punctuator) and token.kind in terminators:
                break
            items.append(item_parser())
        return items

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

    def consume_semicolon(self) -> Tok.Punctuator:
        """Consumes an optional semicolon token. No-op if semicolon is not present."""
        token = self.peek()
        if isinstance(token, Tok.Punctuator) and token.kind == Tok.PunctuatorKind.Semicolon:
            return self.consume_punctuator(Tok.PunctuatorKind.Semicolon)
        return Tok.Punctuator(Tok.PunctuatorKind.Semicolon, token.span)
