from compiler.analysis.passes.type_check import TypeCheck
from compiler.analysis.symbol.context import SymbolCtx
from compiler.analysis.unit import hir as HIR
from compiler.frontend.parse import ast as AST
from compiler.utils.IR.position import SrcSpan


class ExprCheck:
    def __init__(self, type_checker: TypeCheck):
        self.__type_checker = type_checker

    def eval(self, expr: AST.Expr, symbol_ctx: SymbolCtx) -> HIR.Expr:
        """Evaluate an expression and deprect its value."""
        raise NotImplementedError("Expression checking is not implemented yet")

    def value(self, expr: AST.Expr, symbol_ctx: SymbolCtx, expected: int | None) -> HIR.Expr:
        """Evaluate an expression and return its value."""
        raise NotImplementedError("Expression checking is not implemented yet")

    def call_method(self, receiver: HIR.Expr, method_name: str, generic_args: list[int] | None, args: list[HIR.Expr], symbol_ctx: SymbolCtx) -> HIR.MethodCall:
        """Call a method on a receiver expression."""
        raise NotImplementedError("Method call is not implemented yet")

    def assign(self, span: SrcSpan, target: HIR.Expr, value: HIR.Expr) -> HIR.Binary:
        """Assign a value to a target expression."""
        raise NotImplementedError("Assignment is not implemented yet")

    def logical_not(self, operand: HIR.Expr, symbol_ctx: SymbolCtx) -> HIR.Unary:
        """Apply logical NOT operator to an operand expression."""
        raise NotImplementedError("Logical NOT is not implemented yet")

    def into_iter(self, iterable: HIR.Expr, symbol_ctx: SymbolCtx) -> HIR.Expr:
        """Call into_iter() method to get an iterator from an iterable expression."""
        raise NotImplementedError("into_iter is not implemented yet")
