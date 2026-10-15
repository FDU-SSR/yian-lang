from __future__ import annotations

from dataclasses import dataclass, field

from yianc.analysis.symbol.context import SymbolCtx
from yianc.analysis.ty.context import TypeCtx
from yianc.analysis.unit import hir as HIR
from yianc.frontend.parse import ast as AST


@dataclass
class DefPoint:
    type_id: int
    unit_id: int
    ast_body: AST.Block
    symbol_ctx: SymbolCtx
    body: HIR.Block | None = None
    locals: list[int] = field(default_factory=list[int])
    params: list[int] = field(default_factory=list[int])  # semantic parameters; closure receiver is CFG-local

    def export(self, type_ctx: TypeCtx | None = None) -> str:
        from yianc.analysis.unit.hir_export import export_def_point

        return export_def_point(self, type_ctx)
