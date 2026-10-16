from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

from yianc.analysis.symbol.context import SymbolCtx
from yianc.frontend.parse import ast as AST
from yianc.frontend.parse.ast import Program


@dataclass
class UnitData:
    unit_id: int
    program: Program
    path: Path
    symbol_ctx: SymbolCtx = field(default_factory=SymbolCtx, hash=False, repr=False, compare=False)
    is_stdlib: bool = False
    allows_restricted_ops: bool = False
    allows_ffi: bool = False

    def items(self) -> Iterator[AST.ProgramItem]:
        return iter(self.program.items)
