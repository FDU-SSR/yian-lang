from __future__ import annotations

from compiler.frontend.parse import ast as AST
from compiler.frontend.parse.stream import TokenStream


class ExprParser:
    def __init__(self, stream: TokenStream):
        self.__stream = stream

    def parse_expr(self) -> AST.Expr:
        """Parses an expression from the token stream."""
        raise NotImplementedError("Expression parsing not implemented yet")
