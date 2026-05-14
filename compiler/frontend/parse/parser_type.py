from __future__ import annotations

from compiler.frontend.lex.token import Punctuator, PunctuatorKind
from compiler.frontend.parse.ast_type import ASTType
from compiler.frontend.parse import ast_type as Ty
from compiler.frontend.parse.stream import TokenStream


class TypeParser:
    def __init__(self, stream: TokenStream):
        self.__stream = stream

    def parse_type(self) -> ASTType:
        """Parses a type from the token stream."""
        base = self.__parse_base()

        # absorbs subsequent type modifiers
        token = self.__stream.peek()
        while token is not None:
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

    def __parse_base(self) -> ASTType:
        """Parses a base type (e.g., identifier or primitive type) from the token stream."""
        raise NotImplementedError("Base type parsing not implemented yet")

    def __parse_instantiated(self, base: ASTType) -> ASTType:
        """Parses a generic type application from the token stream."""
        self.__stream.consume_punctuator(PunctuatorKind.Less)
        generic_args = self.__stream.consume_separated(self.parse_type, {PunctuatorKind.Comma})
        self.__stream.consume_punctuator(PunctuatorKind.Greater)

        return Ty.InstantiatedType(span=base.span, base=base, generic_args=generic_args)

    def __parse_pointer(self, base: ASTType) -> ASTType:
        """Parses a pointer type from the token stream."""
        raise NotImplementedError("Pointer type parsing not implemented yet")

    def __parse_array(self, base: ASTType) -> ASTType:
        """Parses an array type from the token stream."""
        raise NotImplementedError("Array type parsing not implemented yet")
