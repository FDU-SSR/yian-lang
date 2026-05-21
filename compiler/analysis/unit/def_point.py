from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from compiler.analysis.symbol.context import SymbolCtx
from compiler.analysis.unit import hir as HIR


@dataclass
class DefPoint:
    type_id: int
    body: HIR.Block
    path: Path
    symbol_ctx: SymbolCtx
