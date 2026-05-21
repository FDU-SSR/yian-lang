from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

from compiler.analysis.symbol.context import SymbolCtx
from compiler.frontend.parse import ast as AST
from compiler.frontend.parse.ast import Program


@dataclass
class UnitData:
    unit_id: int
    program: Program
    path: Path
    symbol_ctx: SymbolCtx = field(default_factory=SymbolCtx, hash=False, repr=False, compare=False)

    def items(self) -> Iterator[AST.ProgramItem]:
        return iter(self.program.items)
