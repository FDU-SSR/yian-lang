from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from compiler.analysis.symbol.context import SymbolCtx
from compiler.analysis.ty.context import TypeCtx
from compiler.analysis.unit import hir as HIR
from compiler.frontend.parse import ast as AST

if TYPE_CHECKING:
    from compiler.analysis.passes.definite_assignment import FuncAnalysis


@dataclass
class DefPoint:
    type_id: int
    unit_id: int
    ast_body: AST.Block
    symbol_ctx: SymbolCtx
    body: HIR.Block | None = None
    locals: list[int] = field(default_factory=list[int])
    params: list[int] = field(default_factory=list[int])  # self included if method
    validity: FuncAnalysis | None = None

    def export(self, type_ctx: TypeCtx | None = None) -> str:
        from compiler.analysis.unit.hir_export import export_def_point

        return export_def_point(self, type_ctx)
