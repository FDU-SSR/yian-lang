"""Narrow expression-resolution dependency used by CFG leaf lowerers."""
from __future__ import annotations

from typing import Protocol

from compiler.analysis.unit import hir as HIR
from compiler.codegen.cfg import ir as IR


class ExprResolver(Protocol):
    def resolve_val(self, expr: HIR.Expr) -> IR.Value: ...
