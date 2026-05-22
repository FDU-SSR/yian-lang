from compiler.analysis.passes.type_check import TypeCheck
from compiler.analysis.symbol.context import SymbolCtx
from compiler.analysis.unit import hir as HIR
from compiler.frontend.parse import ast as AST


class ExprCheck:
    def __init__(self, type_checker: TypeCheck):
        self.__type_checker = type_checker

    def eval(self, expr: AST.Expr, symbol_ctx: SymbolCtx) -> HIR.Expr:
        """Evaluate an expression and deprect its value."""
        raise NotImplementedError("Expression checking is not implemented yet")

    def value(self, expr: AST.Expr, symbol_ctx: SymbolCtx, expected: int | None) -> HIR.Expr:
        """Evaluate an expression and return its value."""
        raise NotImplementedError("Expression checking is not implemented yet")
