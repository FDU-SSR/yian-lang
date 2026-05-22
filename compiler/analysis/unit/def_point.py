from __future__ import annotations

from dataclasses import dataclass, field

from compiler.analysis.symbol.context import SymbolCtx
from compiler.analysis.unit import hir as HIR
from compiler.frontend.parse import ast as AST


@dataclass
class DefPoint:
    type_id: int
    unit_id: int
    ast_body: AST.Block
    symbol_ctx: SymbolCtx
    body: HIR.Block | None = None
    locals: list[int] = field(default_factory=list[int])
