from __future__ import annotations

from typing import Optional

from compiler.analysis.error import AnalysisError
from compiler.analysis.passes.sem_ctx import SemCtx
from compiler.analysis.ty.context import TypeCtx
from compiler.analysis.unit import hir as HIR
from compiler.frontend.parse import ast as AST
from compiler.frontend.parse.operator import BinaryOperator, UnaryOperator
from compiler.utils.IR.position import SrcSpan


class ExprChecker:
    """Expression checker and lowering facade.

    For now this is a minimal stub. Concrete implementations should perform
    semantic checks and return `HIR.Expr` carrying HIR nodes and type ids.
    """

    def __init__(self, ctx: SemCtx):
        self.__ctx = ctx

    def eval(self, expr: AST.Expr) -> HIR.Expr:
        """Evaluate an expression in statement position (value dropped).

        Returns a HIR.Expr representing the evaluated expression (may be a noop wrapper).
        """
        raise NotImplementedError()

    def value(self, expr: AST.Expr, expected: Optional[int] = None) -> HIR.Expr:
        """Evaluate an expression and return its value (HIR.Expr)."""
        raise NotImplementedError()

    def as_place(self, expr: AST.Expr) -> HIR.Expr:
        """Treat an expression as an l-value/place and return HIR.Expr."""
        raise NotImplementedError()

    def call_method(self, receiver: HIR.Expr, method_name: str, generic_args: list[int] | None, args: list[HIR.Expr]) -> HIR.Expr:
        raise NotImplementedError()

    def call_into_iter(self, iterable: HIR.Expr) -> HIR.Expr:
        return self.call_method(iterable, "into_iter", None, [])

    def call_next(self, iterator: HIR.Expr) -> HIR.Expr:
        return self.call_method(iterator, "next", None, [])

    def call_eq(self, lhs: HIR.Expr, rhs: HIR.Expr) -> HIR.Expr:
        return self.call_method(lhs, "eq", [rhs.type_id], [rhs])

    def assign(self, span: SrcSpan, target: HIR.Expr, value: HIR.Expr) -> HIR.Binary:
        if not target.is_place:
            raise AnalysisError("assignment target must be an l-value", span)

        if target.type_id != value.type_id:
            target_name = self.__ctx.type_ctx.get_name(target.type_id)
            value_name = self.__ctx.type_ctx.get_name(value.type_id)
            raise AnalysisError(f"cannot assign value of type '{value_name}' to '{target_name}'", span)

        return HIR.Binary(
            span=span,
            op=BinaryOperator.Assign,
            left=target,
            right=value,
            type_id=target.type_id,
            is_place=False,
        )

    def logical_not(self, operand: HIR.Expr) -> HIR.Expr:
        if operand.type_id != TypeCtx.bool_id:
            operand_name = self.__ctx.type_ctx.get_name(operand.type_id)
            raise AnalysisError(f"logical not expects a bool value, got '{operand_name}'", operand.span)

        return HIR.Unary(
            span=operand.span,
            op=UnaryOperator.LogicalNot,
            operand=operand,
            type_id=TypeCtx.bool_id,
            is_place=False,
        )
