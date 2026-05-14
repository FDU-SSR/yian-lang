from __future__ import annotations

from compiler.frontend.parse import ast as AST
from compiler.frontend.parse.parser_expr import ExprParser
from compiler.frontend.parse.parser_type import TypeParser
from compiler.frontend.parse.stream import TokenStream


class StmtParser:
    def __init__(self, stream: TokenStream, expr_parser: ExprParser, type_parser: TypeParser):
        self.__stream = stream

    def parse_stmt(self) -> AST.Stmt:
        """Parses a statement from the token stream."""
        raise NotImplementedError("Statement parsing not implemented yet")
