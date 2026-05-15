from __future__ import annotations
from copy import deepcopy

from compiler.frontend.lex.token import Identifier, Keyword, KeywordKind, Punctuator, PunctuatorKind
from compiler.frontend.parse import ast as AST
from compiler.frontend.parse import ast_type as Ty
from compiler.frontend.parse.ast_type import ASTType
from compiler.frontend.parse.parser import ParseError
from compiler.frontend.parse.stream import TokenStream
from compiler.utils.IR.position import SrcSpan


class TypeParser:
    def __init__(self, stream: TokenStream):
        self.__stream = stream

    def parse_type(self) -> ASTType:
        """Parses a type from the token stream."""
        base = self.__parse_base()

        # absorbs subsequent type modifiers
        token = self.__stream.peek()
        while True:
            match token:
                case Punctuator(kind=PunctuatorKind.Less):
                    # generic type application, e.g., `Option<int>`
                    base = self.__parse_instantiated(base)
                case Punctuator(kind=PunctuatorKind.Star):
                    # pointer type, e.g., `int*`
                    base = self.__parse_pointer(base)
                case Punctuator(kind=PunctuatorKind.LBracket):
                    # array type, e.g., `int[10]`
                    base = self.__parse_array(base)
                case _:
                    break
            token = self.__stream.peek()

        return base

    MAPPING: dict[KeywordKind, ASTType] = {
        KeywordKind.I8: Ty.IntType(span=SrcSpan.empty(), width=1, signed=True),
        KeywordKind.U8: Ty.IntType(span=SrcSpan.empty(), width=1, signed=False),
        KeywordKind.I16: Ty.IntType(span=SrcSpan.empty(), width=2, signed=True),
        KeywordKind.U16: Ty.IntType(span=SrcSpan.empty(), width=2, signed=False),
        KeywordKind.I32: Ty.IntType(span=SrcSpan.empty(), width=4, signed=True),
        KeywordKind.U32: Ty.IntType(span=SrcSpan.empty(), width=4, signed=False),
        KeywordKind.I64: Ty.IntType(span=SrcSpan.empty(), width=8, signed=True),
        KeywordKind.U64: Ty.IntType(span=SrcSpan.empty(), width=8, signed=False),
        KeywordKind.F32: Ty.FloatType(span=SrcSpan.empty(), width=4),
        KeywordKind.F64: Ty.FloatType(span=SrcSpan.empty(), width=8),
        KeywordKind.Bool: Ty.BoolType(span=SrcSpan.empty()),
        KeywordKind.Str: Ty.StrType(span=SrcSpan.empty()),
        KeywordKind.Char: Ty.CharType(span=SrcSpan.empty()),
        KeywordKind.Void: Ty.VoidType(span=SrcSpan.empty()),
    }

    def __parse_base(self) -> ASTType:
        """Parses a base type (e.g., identifier or primitive type) from the token stream."""
        token = self.__stream.next()

        if isinstance(token, Keyword) and token.kind in self.MAPPING:
            ty = deepcopy(self.MAPPING[token.kind])
            ty.span = token.span
            return ty

        if isinstance(token, Keyword) and token.kind == KeywordKind.Fn:
            # function type, e.g., `fn(int, str) -> bool`
            self.__stream.consume_keyword(KeywordKind.Fn)
            self.__stream.consume_punctuator(PunctuatorKind.LParen)
            param_types = self.__stream.consume_separated(self.parse_type, {PunctuatorKind.Comma})
            self.__stream.consume_punctuator(PunctuatorKind.RParen)

            self.__stream.consume_spaces()
            arrow_token = self.__stream.peek()
            if isinstance(arrow_token, Punctuator) and arrow_token.kind == PunctuatorKind.Arrow:
                self.__stream.consume_punctuator(PunctuatorKind.Arrow)

                self.__stream.consume_spaces()
                return_type = self.parse_type()
            else:
                return_type = Ty.VoidType(span=token.span)

            return Ty.FunctionType(span=token.span, param_types=param_types, return_type=return_type)

        if isinstance(token, Identifier):
            return Ty.NamedType(span=token.span, name=AST.Identifier(span=token.span, name=token.name))

        if isinstance(token, Punctuator) and token.kind == PunctuatorKind.LParen:
            # tuple type, e.g., `(int, str)`
            self.__stream.consume_punctuator(PunctuatorKind.LParen)
            element_types = self.__stream.consume_separated(self.parse_type, {PunctuatorKind.Comma})
            self.__stream.consume_punctuator(PunctuatorKind.RParen)
            return Ty.TupleType(span=token.span, element_types=element_types)

        raise ParseError(f"Expected type but got '{token}'", token.span)

    def __parse_instantiated(self, base: ASTType) -> ASTType:
        """Parses a generic type application from the token stream."""
        self.__stream.consume_punctuator(PunctuatorKind.Less)
        generic_args = self.__stream.consume_separated(self.parse_type, {PunctuatorKind.Comma})
        self.__stream.consume_punctuator(PunctuatorKind.Greater)

        return Ty.InstantiatedType(span=base.span, base=base, generic_args=generic_args)

    def __parse_pointer(self, base: ASTType) -> ASTType:
        """Parses a pointer type from the token stream."""
        self.__stream.consume_punctuator(PunctuatorKind.Star)
        return Ty.PointerType(span=base.span, pointee_type=base)

    def __parse_array(self, base: ASTType) -> ASTType:
        """Parses an array type from the token stream."""
        self.__stream.consume_punctuator(PunctuatorKind.LBracket)

        token = self.__stream.peek()
        if isinstance(token, Punctuator) and token.kind == PunctuatorKind.RBracket:
            # slice type, e.g., `int[]`
            self.__stream.consume_punctuator(PunctuatorKind.RBracket)
            return Ty.SliceType(span=base.span, element_type=base)
        # fixed-size array type, e.g., `int[10]`
        size = self.__stream.consume_integer_literal()
        self.__stream.consume_punctuator(PunctuatorKind.RBracket)
        return Ty.ArrayType(span=base.span, element_type=base, size=size)
