from __future__ import annotations

from collections.abc import Callable

from compiler.frontend.lex.position import SrcSpan
from compiler.frontend.lex.token import (Identifier, IntLiteral, Keyword,
                                         KeywordKind, Punctuator,
                                         PunctuatorKind)
from compiler.frontend.parse import ast as AST
from compiler.frontend.parse import ast_type as Ty
from compiler.frontend.parse.ast_type import ASTType
from compiler.frontend.parse.error import ParseError
from compiler.frontend.parse.operator import BinaryOperator, UnaryOperator
from compiler.frontend.parse.stream import (SEP_COMMA, TERM_RANGLE,
                                            TERM_RPAREN, TokenStream)


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
                case Punctuator(kind=PunctuatorKind.LAngle):
                    # generic type application, e.g., `Option<int>`
                    base = self.__parse_instance(base)
                case Punctuator(kind=PunctuatorKind.Star):
                    # pointer type, e.g., `int*`
                    base = self.__parse_pointer(base)
                case Punctuator(kind=PunctuatorKind.Ampersand):
                    # reference type, e.g., `int&`
                    base = self.__parse_ref(base)
                case Punctuator(kind=PunctuatorKind.LBracket):
                    # array type, e.g., `int[10]`
                    base = self.__parse_array(base)
                case _:
                    break
            token = self.__stream.peek()

        return base

    MAPPING: dict[KeywordKind, Callable[[SrcSpan], ASTType]] = {
        KeywordKind.I8: lambda span: Ty.IntType(span=span, width=1, signed=True),
        KeywordKind.U8: lambda span: Ty.IntType(span=span, width=1, signed=False),
        KeywordKind.I16: lambda span: Ty.IntType(span=span, width=2, signed=True),
        KeywordKind.U16: lambda span: Ty.IntType(span=span, width=2, signed=False),
        KeywordKind.I32: lambda span: Ty.IntType(span=span, width=4, signed=True),
        KeywordKind.U32: lambda span: Ty.IntType(span=span, width=4, signed=False),
        KeywordKind.I64: lambda span: Ty.IntType(span=span, width=8, signed=True),
        KeywordKind.U64: lambda span: Ty.IntType(span=span, width=8, signed=False),
        KeywordKind.F16: lambda span: Ty.FloatType(span=span, width=2),
        KeywordKind.F32: lambda span: Ty.FloatType(span=span, width=4),
        KeywordKind.F64: lambda span: Ty.FloatType(span=span, width=8),
        KeywordKind.Int: lambda span: Ty.IntType(span=span, width=4, signed=True),   # default int type is i32
        KeywordKind.Uint: lambda span: Ty.IntType(span=span, width=4, signed=False),  # default uint type is u32
        KeywordKind.Float: lambda span: Ty.FloatType(span=span, width=8),             # default float type is f64
        KeywordKind.Bool: lambda span: Ty.BoolType(span=span),
        KeywordKind.Str: lambda span: Ty.StrType(span=span),
        KeywordKind.Char: lambda span: Ty.CharType(span=span),
        KeywordKind.Void: lambda span: Ty.VoidType(span=span),
    }

    def __parse_base(self) -> ASTType:
        """Parses a base type (e.g., identifier or primitive type) from the token stream."""
        token = self.__stream.next()

        if isinstance(token, Keyword) and token.kind in self.MAPPING:
            return self.MAPPING[token.kind](token.span)

        if isinstance(token, Punctuator) and token.kind == PunctuatorKind.Exclamation:
            return Ty.NeverType(span=token.span)

        if isinstance(token, Keyword) and token.kind == KeywordKind.Fn:
            # function type, e.g., `fn(int, str) -> bool`
            self.__stream.consume_punctuator(PunctuatorKind.LParen)
            param_types = self.__stream.consume_separated(self.parse_type, SEP_COMMA, TERM_RPAREN)
            self.__stream.consume_punctuator(PunctuatorKind.RParen)

            arrow_token = self.__stream.peek()
            if isinstance(arrow_token, Punctuator) and arrow_token.kind == PunctuatorKind.Arrow:
                self.__stream.consume_punctuator(PunctuatorKind.Arrow)

                return_type = self.parse_type()
            else:
                return_type = Ty.VoidType(span=token.span)

            return Ty.FunctionType(span=token.span, param_types=param_types, return_type=return_type)

        if isinstance(token, Identifier):
            return Ty.NamedType(span=token.span, name=AST.Identifier(span=token.span, name=token.name))

        if isinstance(token, Punctuator) and token.kind == PunctuatorKind.LParen:
            # '(' already consumed by next() above.
            #   ()       -> empty tuple
            #   (T)      -> grouped type, just T (NOT a 1-tuple)
            #   (T,)     -> single-element tuple
            #   (A, B..) -> tuple
            peeked = self.__stream.peek()
            if isinstance(peeked, Punctuator) and peeked.kind == PunctuatorKind.RParen:
                self.__stream.consume_punctuator(PunctuatorKind.RParen)
                return Ty.TupleType(span=token.span, element_types=[])

            first = self.parse_type()
            peeked = self.__stream.peek()
            if isinstance(peeked, Punctuator) and peeked.kind == PunctuatorKind.RParen:
                # grouped type '(T)' — parentheses are just precedence
                self.__stream.consume_punctuator(PunctuatorKind.RParen)
                return first

            self.__stream.consume_punctuator(PunctuatorKind.Comma)
            peeked = self.__stream.peek()
            if isinstance(peeked, Punctuator) and peeked.kind == PunctuatorKind.RParen:
                # single-element tuple '(T,)'
                self.__stream.consume_punctuator(PunctuatorKind.RParen)
                return Ty.TupleType(span=token.span, element_types=[first])

            rest = self.__stream.consume_separated(self.parse_type, SEP_COMMA, TERM_RPAREN)
            self.__stream.consume_punctuator(PunctuatorKind.RParen)
            return Ty.TupleType(span=token.span, element_types=[first] + rest)

        raise ParseError(f"Expected type but got '{token}'", token.span)

    def __parse_instance(self, base: ASTType) -> ASTType:
        """Parses a generic type application from the token stream."""
        self.__stream.consume_punctuator(PunctuatorKind.LAngle)
        generic_args = self.__stream.consume_separated(
            self.parse_generic_arg,
            SEP_COMMA,
            TERM_RANGLE,
        )
        self.__stream.consume_punctuator(PunctuatorKind.RAngle)

        return Ty.InstanceType(span=base.span, base=base, generic_args=generic_args)

    def __parse_pointer(self, base: ASTType) -> ASTType:
        """Parses a pointer type from the token stream."""
        self.__stream.consume_punctuator(PunctuatorKind.Star)
        return Ty.PointerType(span=base.span, pointee_type=base)

    def __parse_ref(self, base: ASTType) -> ASTType:
        """Parses a reference type from the token stream."""
        self.__stream.consume_punctuator(PunctuatorKind.Ampersand)
        return Ty.RefType(span=base.span, pointee_type=base)

    def __parse_array(self, base: ASTType) -> ASTType:
        """Parses an array or slice type from the token stream.

        Slice:  T[]           → SliceType
        Array:  T[10]         → ArrayType(LiteralConstExpr)
                T[N]          → ArrayType(GenericConstExpr)
        """
        self.__stream.consume_punctuator(PunctuatorKind.LBracket)

        token = self.__stream.peek()
        if isinstance(token, Punctuator) and token.kind == PunctuatorKind.RBracket:
            # slice type, e.g., `int[]`
            self.__stream.consume_punctuator(PunctuatorKind.RBracket)
            return Ty.SliceType(span=base.span, element_type=base)

        # fixed-size array — size is a compile-time integer expression
        size = self.__parse_const_expr()
        self.__stream.consume_punctuator(PunctuatorKind.RBracket)
        return Ty.ArrayType(span=base.span, element_type=base, size=size)

    def __parse_const_expr(self) -> Ty.ConstExpr:
        """Parse an integer expression used as a type-level value."""
        return self.__parse_const_expr_bp(1)

    def __parse_const_expr_bp(self, min_bp: int) -> Ty.ConstExpr:
        token = self.__stream.peek()
        unary = UnaryOperator.try_from_token(token)
        if unary in (UnaryOperator.Neg, UnaryOperator.BitNot):
            self.__stream.advance()
            operand = self.__parse_const_expr_bp(unary.rbp)
            lhs: Ty.ConstExpr = Ty.UnaryConstExpr(
                span=token.span + operand.span,
                op=unary,
                operand=operand,
            )
        elif isinstance(token, Punctuator) and token.kind == PunctuatorKind.LParen:
            self.__stream.advance()
            lhs = self.__parse_const_expr_bp(1)
            self.__stream.consume_punctuator(PunctuatorKind.RParen)
        elif isinstance(token, IntLiteral):
            self.__stream.advance()
            lhs = Ty.LiteralConstExpr(span=token.span, literal=token)
        elif isinstance(token, (Identifier, Keyword)):
            name = self.__stream.consume_identifier()
            lhs = Ty.GenericConstExpr(span=name.span, name=name)
        else:
            raise ParseError(f"Expected integer constant expression but got '{token}'", token.span)

        allowed = {
            BinaryOperator.Add,
            BinaryOperator.Sub,
            BinaryOperator.Mul,
            BinaryOperator.Div,
            BinaryOperator.Mod,
            BinaryOperator.BitAnd,
            BinaryOperator.BitOr,
            BinaryOperator.BitXor,
            BinaryOperator.Shl,
            BinaryOperator.Shr,
        }
        while True:
            op = BinaryOperator.try_from_token(self.__stream.peek())
            if op not in allowed or op.lbp < min_bp:
                break
            self.__stream.advance()
            rhs = self.__parse_const_expr_bp(op.rbp)
            lhs = Ty.BinaryConstExpr(span=lhs.span + rhs.span, op=op, left=lhs, right=rhs)
        return lhs

    def parse_generic_arg(self) -> ASTType | Ty.ConstExpr:
        """Parses a generic argument — either a type or a constant expression.

        Used for InstanceType generic args where the parser cannot distinguish
        type args from const args without type context. Literal values are
        parsed as ConstExpr; identifiers and type expressions are parsed as
        ASTType, and the resolver resolves their names through the current
        symbol context, including const-generic bindings.
        """
        token = self.__stream.peek()
        if isinstance(token, IntLiteral):
            return self.__parse_const_expr()
        unary = UnaryOperator.try_from_token(token)
        if unary in (UnaryOperator.Neg, UnaryOperator.BitNot):
            return self.__parse_const_expr()

        mark = self.__stream.mark()
        try:
            parsed_type = self.parse_type()
        except ParseError:
            self.__stream.reset(mark)
            return self.__parse_const_expr()
        op = BinaryOperator.try_from_token(self.__stream.peek())
        if op in {
            BinaryOperator.Add,
            BinaryOperator.Sub,
            BinaryOperator.Mul,
            BinaryOperator.Div,
            BinaryOperator.Mod,
            BinaryOperator.BitAnd,
            BinaryOperator.BitOr,
            BinaryOperator.BitXor,
            BinaryOperator.Shl,
            BinaryOperator.Shr,
        }:
            self.__stream.reset(mark)
            return self.__parse_const_expr()
        return parsed_type
