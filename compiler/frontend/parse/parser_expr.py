from __future__ import annotations

from compiler.frontend.parse import ast as AST
from compiler.frontend.parse.operator import BinaryOperator
from compiler.frontend.parse.stream import TokenStream


class ExprParser:
    def __init__(self, stream: TokenStream):
        self.__stream = stream

    def parse_expr(self) -> AST.Expr:
        """Parses an expression from the token stream."""
        return self.__parse_expr_bp(1)

    def __parse_expr_bp(self, min_bp: int) -> AST.Expr:
        """Pratt parser for expressions with operator precedence."""
        # parse the left-hand side (LHS) of the expression
        lhs = self.__parse_primary()

        while True:
            op = BinaryOperator.try_from_token(self.__stream)

            if op is None:
                break

            if op.lbp < min_bp:
                break

            rhs = self.__parse_expr_bp(op.rbp)
            lhs = AST.Binary(span=lhs.span + rhs.span, op=op, left=lhs, right=rhs)

        return lhs

    def __parse_primary(self) -> AST.Expr:
        """Parses a primary expression (e.g., literal, identifier, or parenthesized expression) from the token stream."""
        raise NotImplementedError("Parsing of primary expressions is not implemented yet")
