from __future__ import annotations

from compiler.frontend.lex import token as Tok
from compiler.frontend.parse import ast as AST
from compiler.frontend.parse.error import ParseError
from compiler.frontend.parse.operator import BinaryOperator, UnaryOperator
from compiler.frontend.parse.parser_type import TypeParser
from compiler.frontend.parse.stream import TokenStream


class ExprParser:
    def __init__(self, stream: TokenStream, type_parser: TypeParser):
        self.__stream = stream
        self.__type_parser = type_parser

    def parse_expr(self) -> AST.Expr:
        """Parses an expression from the token stream."""
        return self.__parse_expr_bp(1)

    def __parse_expr_bp(self, min_bp: int) -> AST.Expr:
        """Pratt parser for expressions with operator precedence."""
        # parse the left-hand side (LHS) of the expression
        lhs = self.__parse_prefix()

        while True:

            op = BinaryOperator.try_from_token(self.__stream.peek())

            if op is None:
                break

            if op.lbp < min_bp:
                break

            self.__stream.advance()

            rhs = self.__parse_expr_bp(op.rbp)
            lhs = AST.Binary(span=lhs.span + rhs.span, op=op, left=lhs, right=rhs)

        return lhs

    def __parse_prefix(self) -> AST.Expr:
        """Parses the prefix part of an expression."""
        # try to parse a unary operator
        op = UnaryOperator.try_from_token(self.__stream.peek())
        if op is not None:
            self.__stream.advance()

            operand = self.__parse_expr_bp(op.rbp)
            return AST.Unary(span=operand.span, op=op, operand=operand)

        # try to parse a dyn expression
        token = self.__stream.peek()
        if isinstance(token, Tok.Keyword) and token.kind == Tok.KeywordKind.Dyn:
            return self.__parse_dyn()

        # parse a primary expression
        expr = self.__parse_primary()
        return self.__parse_postfix(expr)

    def __parse_dyn(self) -> AST.Expr:
        """Parses a dyn expression."""
        self.__stream.consume_keyword(Tok.KeywordKind.Dyn)

        token = self.__stream.peek()
        if isinstance(token, Tok.Punctuator) and token.kind == Tok.PunctuatorKind.LBracket:
            # dyn[array_size] Type
            self.__stream.consume_punctuator(Tok.PunctuatorKind.LBracket)
            array_size = self.parse_expr()
            self.__stream.consume_punctuator(Tok.PunctuatorKind.RBracket)

            target_type = self.__type_parser.parse_type()
            return AST.DynBuffer(span=token.span, target_type=target_type, size=array_size)

        # dyn value
        value = self.parse_expr()
        return AST.DynValue(span=token.span, value=value)

    def __parse_primary(self) -> AST.Expr:
        """Parses a primary expression."""
        token = self.__stream.peek()
        match token:
            case Tok.Keyword(kind=Tok.KeywordKind.True_):
                self.__stream.consume_keyword(Tok.KeywordKind.True_)
                return AST.Literal(span=token.span, literal=Tok.BoolLiteral(raw="true", span=token.span, value=True))
            case Tok.Keyword(kind=Tok.KeywordKind.False_):
                self.__stream.consume_keyword(Tok.KeywordKind.False_)
                return AST.Literal(span=token.span, literal=Tok.BoolLiteral(raw="false", span=token.span, value=False))
            case Tok.Identifier() | Tok.Keyword():
                # Identifiers and (non-literal) keywords both represent names in
                # expression context (type names, function names, built-in calls).
                ident = self.__stream.consume_identifier()

                # handle generic arguments (e.g., Type<...>, bitcast<T*>, Array<T, 5>)
                # LAngle means no preceding whitespace — always a generic opening
                next_token = self.__stream.peek()
                if isinstance(next_token, Tok.Punctuator) and next_token.kind == Tok.PunctuatorKind.LAngle:
                    self.__stream.consume_punctuator(Tok.PunctuatorKind.LAngle)
                    generics = self.__stream.consume_separated(self.__type_parser.parse_generic_arg, {Tok.PunctuatorKind.Comma}, {Tok.PunctuatorKind.RAngle})
                    self.__stream.consume_punctuator(Tok.PunctuatorKind.RAngle)
                    return AST.TypeItem(span=ident.span, name=ident, generics=generics)

                return ident
            case Tok.IntLiteral() | Tok.FloatLiteral() | Tok.CharLiteral() | Tok.StrLiteral():
                self.__stream.advance()
                return AST.Literal(span=token.span, literal=token)
            case Tok.Punctuator(kind=Tok.PunctuatorKind.LParen):
                self.__stream.consume_punctuator(Tok.PunctuatorKind.LParen)
                expr = self.parse_expr()

                next_token = self.__stream.peek()
                if isinstance(next_token, Tok.Punctuator) and next_token.kind == Tok.PunctuatorKind.Comma:
                    # tuple expression
                    items: list[AST.Expr] = [expr]
                    self.__stream.consume_punctuator(Tok.PunctuatorKind.Comma)
                    next_token = self.__stream.peek()
                    if isinstance(next_token, Tok.Punctuator) and next_token.kind == Tok.PunctuatorKind.RParen:
                        # single-element tuple (e.g., (expr,))
                        self.__stream.consume_punctuator(Tok.PunctuatorKind.RParen)
                        return AST.Tuple(span=token.span, elements=items)

                    items += self.__stream.consume_separated(self.parse_expr, {Tok.PunctuatorKind.Comma}, {Tok.PunctuatorKind.RParen})
                    self.__stream.consume_punctuator(Tok.PunctuatorKind.RParen)
                    return AST.Tuple(span=token.span, elements=items)

                # parenthesized expression
                self.__stream.consume_punctuator(Tok.PunctuatorKind.RParen)
                return expr
            case Tok.Punctuator(kind=Tok.PunctuatorKind.LBracket):
                # array expression
                self.__stream.consume_punctuator(Tok.PunctuatorKind.LBracket)
                items = self.__stream.consume_separated(self.parse_expr, {Tok.PunctuatorKind.Comma}, {Tok.PunctuatorKind.RBracket})
                self.__stream.consume_punctuator(Tok.PunctuatorKind.RBracket)
                return AST.Array(span=token.span, elements=items)
            case _:
                raise ParseError(f"Unexpected token '{token}' while parsing expression", token.span)

    def __parse_postfix(self, expr: AST.Expr) -> AST.Expr:
        """Parses postfix expressions (e.g., function calls, field access)."""
        # absorb postfix as long as possible
        while True:
            token = self.__stream.peek()
            match token:
                case Tok.Punctuator(kind=Tok.PunctuatorKind.LParen):
                    # function call
                    self.__stream.consume_punctuator(Tok.PunctuatorKind.LParen)
                    args = self.__stream.consume_separated(self.parse_arg, {Tok.PunctuatorKind.Comma}, {Tok.PunctuatorKind.RParen})
                    self.__stream.consume_punctuator(Tok.PunctuatorKind.RParen)

                    expr = AST.Call(span=expr.span + token.span, callee=expr, args=args)

                case Tok.Punctuator(kind=Tok.PunctuatorKind.Dot):
                    # field access or method call
                    self.__stream.consume_punctuator(Tok.PunctuatorKind.Dot)

                    field_or_method_name = self.__stream.consume_identifier()
                    next_token = self.__stream.peek()
                    match next_token:
                        case Tok.Punctuator(kind=Tok.PunctuatorKind.LParen):
                            # method call
                            self.__stream.consume_punctuator(Tok.PunctuatorKind.LParen)
                            args = self.__stream.consume_separated(self.parse_arg, {Tok.PunctuatorKind.Comma}, {Tok.PunctuatorKind.RParen})
                            self.__stream.consume_punctuator(Tok.PunctuatorKind.RParen)

                            expr = AST.MethodCall(span=expr.span, receiver=expr, method_name=field_or_method_name, generics=[], args=args)
                        case Tok.Punctuator(kind=Tok.PunctuatorKind.LAngle):
                            # Generic method call: expr.name<T>(args)
                            # LAngle means no preceding whitespace — always generic opening
                            self.__stream.consume_punctuator(Tok.PunctuatorKind.LAngle)
                            generics = self.__stream.consume_separated(self.__type_parser.parse_type, {Tok.PunctuatorKind.Comma}, {Tok.PunctuatorKind.RAngle})
                            self.__stream.consume_punctuator(Tok.PunctuatorKind.RAngle)

                            self.__stream.consume_punctuator(Tok.PunctuatorKind.LParen)
                            args = self.__stream.consume_separated(self.parse_arg, {Tok.PunctuatorKind.Comma}, {Tok.PunctuatorKind.RParen})
                            self.__stream.consume_punctuator(Tok.PunctuatorKind.RParen)

                            expr = AST.MethodCall(span=expr.span, receiver=expr, method_name=field_or_method_name, generics=generics, args=args)
                        case _:
                            # field access
                            expr = AST.FieldAccess(span=expr.span, receiver=expr, field_name=field_or_method_name)

                case Tok.Punctuator(kind=Tok.PunctuatorKind.LBracket):
                    # array indexing
                    self.__stream.consume_punctuator(Tok.PunctuatorKind.LBracket)
                    index_expr = self.parse_expr()
                    self.__stream.consume_punctuator(Tok.PunctuatorKind.RBracket)

                    expr = AST.Binary(span=expr.span, op=BinaryOperator.Index, left=expr, right=index_expr)

                case _:
                    return expr

    def parse_arg(self) -> AST.Arg:
        """Parses a single argument, which can be either positional (expr) or named (name=expr)."""
        token = self.__stream.peek()
        if isinstance(token, Tok.Identifier):
            # could be a named argument or a positional argument
            # look ahead for '='
            ahead = self.__stream.peek_nth(1)
            if isinstance(ahead, Tok.Punctuator) and ahead.kind == Tok.PunctuatorKind.Equal:
                # named argument
                name = self.__stream.consume_identifier()
                self.__stream.consume_punctuator(Tok.PunctuatorKind.Equal)
                value = self.parse_expr()
                return AST.Arg(span=name.span + value.span, name=name, value=value)

        # positional argument
        value = self.parse_expr()
        return AST.Arg(span=value.span, name=None, value=value)
