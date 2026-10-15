from __future__ import annotations

from typing import Callable

from yianc.frontend.lex import token as Tok
from yianc.frontend.lex.token import Token
from yianc.frontend.parse import ast as AST
from yianc.frontend.parse.error import ParseError

# ------------------------------------------------------------------
# frozenset constants passed to consume_separated / consume_until.
# Avoid repeated set-literal construction on hot paths.
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
    def __init__(self, tokens: list[Token], *, editor: bool = False):
        self.__tokens = tokens
        self.__index = 0
        self.editor = editor
        self.errors: list[ParseError] = []

    def previous_span(self):
        return self.__tokens[max(0, self.__index - 1)].span

    def declaration_boundary(self) -> bool:
        """A named function cannot be an element of an unfinished argument list."""
        ahead = 0
        token: Tok.Token | None = self.peek()
        while isinstance(token, Tok.Keyword) and token.kind in (Tok.KeywordKind.Pub, Tok.KeywordKind.Ffi, Tok.KeywordKind.Static):
            ahead += 1
            attribute_open = self.peek_nth(ahead)
            if token.kind is Tok.KeywordKind.Pub and isinstance(attribute_open, Tok.Punctuator) and attribute_open.kind is Tok.PunctuatorKind.LParen:
                ffi = self.peek_nth(ahead + 1)
                close = self.peek_nth(ahead + 2)
                if not (isinstance(ffi, Tok.Keyword) and ffi.kind is Tok.KeywordKind.Ffi and
                        isinstance(close, Tok.Punctuator) and close.kind is Tok.PunctuatorKind.RParen):
                    return False
                ahead += 3
            token = self.peek_nth(ahead)
        name = self.peek_nth(ahead + 1)
        opening = self.peek_nth(ahead + 2)
        return (isinstance(token, Tok.Keyword) and token.kind is Tok.KeywordKind.Fn
                and isinstance(name, Tok.Identifier) and isinstance(opening, Tok.Punctuator)
                and opening.kind in (Tok.PunctuatorKind.LParen, Tok.PunctuatorKind.LAngle))

    def synchronize(self, terminators: frozenset[Tok.PunctuatorKind]) -> None:
        """Consume one damaged region without interpreting strings or comments."""
        depth = 0
        while not self.at_end():
            if self.editor and self.declaration_boundary():
                return
            token = self.peek()
            if isinstance(token, Tok.Punctuator):
                if depth == 0 and token.kind in terminators:
                    return
                if token.kind in (Tok.PunctuatorKind.LParen, Tok.PunctuatorKind.LBracket, Tok.PunctuatorKind.LBrace):
                    depth += 1
                elif token.kind in (Tok.PunctuatorKind.RParen, Tok.PunctuatorKind.RBracket, Tok.PunctuatorKind.RBrace):
                    if depth == 0:
                        return
                    depth -= 1
            self.advance()

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

    def mark(self) -> int:
        """Snapshot the cursor position for speculative parsing (:meth:`reset`)."""
        return self.__index

    def reset(self, mark: int) -> None:
        """Move the cursor to a position returned by :meth:`mark`."""
        self.__index = mark

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
            if self.editor and expected_kind in (
                Tok.PunctuatorKind.RParen, Tok.PunctuatorKind.RBracket,
                Tok.PunctuatorKind.RAngle, Tok.PunctuatorKind.RBrace,
                Tok.PunctuatorKind.Semicolon,
            ):
                self.errors.append(ParseError(f"Expected punctuator '{expected_kind}' but got '{token}'", token.span))
                return Tok.Punctuator(expected_kind, token.span)
            raise ParseError(f"Expected punctuator '{expected_kind}' but got '{token}'", token.span)
        self.advance()
        return token

    def consume_attrs(self) -> list[AST.Attr]:
        """Consumes attributes (e.g., #[attr]) and returns them as a list."""
        attrs: list[AST.Attr] = []
        while not self.at_end():
            match self.peek():
                case Tok.Keyword(kind=Tok.KeywordKind.Pub, span=span):
                    self.advance()
                    next_token = self.peek()
                    if isinstance(next_token, Tok.Punctuator) and next_token.kind == Tok.PunctuatorKind.LParen:
                        self.advance()
                        self.consume_keyword(Tok.KeywordKind.Ffi)
                        self.consume_punctuator(Tok.PunctuatorKind.RParen)
                        attrs.append(AST.Attr(span=span, kind=AST.AttrKind.PubFfi))
                    else:
                        attrs.append(AST.Attr(span=span, kind=AST.AttrKind.Pub))
                case Tok.Keyword(kind=Tok.KeywordKind.Ffi, span=span):
                    self.advance()
                    attrs.append(AST.Attr(span=span, kind=AST.AttrKind.Ffi))
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
            if self.editor and (self.at_end() or self.declaration_boundary() or isinstance(token, Tok.Punctuator) and token.kind in (
                Tok.PunctuatorKind.RBrace, Tok.PunctuatorKind.Semicolon,
            )):
                break
            try:
                items.append(item_parser())
            except ParseError as error:
                if not self.editor:
                    raise
                self.errors.append(error)
                self.synchronize(terminators | separators)

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
            if self.editor and self.at_end():
                break
            mark = self.mark()
            try:
                items.append(item_parser())
            except ParseError as error:
                if not self.editor:
                    raise
                self.errors.append(error)
                self.synchronize(terminators | TERM_SEMICOLON)
                boundary = self.peek()
                if isinstance(boundary, Tok.Punctuator) and boundary.kind == Tok.PunctuatorKind.Semicolon:
                    self.advance()
                if self.mark() == mark and not self.at_end():
                    self.advance()
        return items

    def consume_identifier(self) -> AST.Identifier:
        """Consumes and returns the next token if it is an identifier/keyword, otherwise raises an error."""
        token = self.peek()
        if self.editor and not isinstance(token, (Tok.Identifier, Tok.Keyword)):
            raise ParseError(f"Expected identifier or keyword but got '{token}'", token.span)
        token = self.next()
        match token:
            case Tok.Keyword(kind=Tok.KeywordKind.IsRawMode, span=span):
                raise ParseError("'IS_RAW_MODE' is a reserved compile-time configuration", span)
            case Tok.Keyword(kind=Tok.KeywordKind.Defer, span=span):
                raise ParseError("'defer' is a reserved keyword", span)
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
