from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from compiler.analysis.passes.sem_ctx import SemCtx
from compiler.analysis.unit import hir as HIR
from compiler.frontend.parse import ast as AST
from compiler.utils.IR.position import SrcSpan


@dataclass
class ExprResult:
    hir: HIR.Expr
    type_id: int
    is_place: bool


class ExprChecker:
    """Expression checker and lowering facade.

    For now this is a minimal stub. Concrete implementations should perform
    semantic checks and return `ExprResult` carrying HIR nodes and type ids.
    """

    def __init__(self, ctx: SemCtx):
        self.__ctx = ctx

    def eval(self, expr: AST.Expr) -> HIR.Expr:
        """Evaluate an expression in statement position (value dropped).

        Returns a HIR.Expr representing the evaluated expression (may be a noop wrapper).
        """
        raise NotImplementedError()

    def value(self, expr: AST.Expr, expected: Optional[int] = None) -> ExprResult:
        """Evaluate an expression and return its value and type information."""
        raise NotImplementedError()

    def as_place(self, expr: AST.Expr) -> ExprResult:
        """Treat an expression as an l-value/place."""
        raise NotImplementedError()

    def call_method(self, receiver: HIR.Expr, method_name: str, generic_args: list[int] | None, args: list[HIR.Expr]) -> ExprResult:
        raise NotImplementedError()

    def call_eq(self, lhs: HIR.Expr, rhs: HIR.Expr) -> ExprResult:
        """Emit a `PartialEq` equality call between `lhs` and `rhs`.

        This is a thin convenience wrapper that should perform trait/method
        resolution and construct an appropriate `ExprResult` representing the
        equality comparison. It is intentionally left as an interface stub.
        """
        raise NotImplementedError()

    def assign(self, span: SrcSpan, target: HIR.Expr, value: HIR.Expr) -> HIR.Binary:
        raise NotImplementedError()

    def logical_not(self, operand: HIR.Expr) -> ExprResult:
        raise NotImplementedError()

    def into_iter(self, iterable: HIR.Expr) -> ExprResult:
        raise NotImplementedError()
