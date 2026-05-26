from __future__ import annotations

from typing import Optional, Protocol

from compiler.analysis.unit import hir as HIR
from compiler.frontend.parse import ast as AST


class ExprEvaluator(Protocol):
    def value(self, expr: AST.Expr, expected: Optional[int] = None) -> HIR.Expr:
        ...
