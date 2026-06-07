from __future__ import annotations

from typing import Protocol

from compiler.analysis.unit import hir as HIR
from compiler.frontend.parse import ast as AST


class ExprEvaluator(Protocol):
    def value(self, expr: AST.Expr) -> HIR.Expr:
        ...

    def coerce(self, expr: HIR.Expr, expected: int) -> HIR.Expr:
        ...
