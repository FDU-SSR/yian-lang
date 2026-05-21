from __future__ import annotations

from dataclasses import dataclass

from compiler.analysis.symbol.context import SymbolCtx
from compiler.analysis.unit import hir as HIR


@dataclass
class DefPoint:
    type_id: int
    unit_id: int
    body: HIR.Block
    symbol_ctx: SymbolCtx
