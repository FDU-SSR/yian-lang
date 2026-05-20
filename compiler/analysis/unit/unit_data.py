from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

from compiler.analysis.symbol.context import SymbolCtx
from compiler.frontend.parse import ast as AST
from compiler.frontend.parse.ast import Program


class UnitData:
    def __init__(self, program: Program, unit_id: int, path: Path) -> None:
        self.__program = program
        self.__unit_id = unit_id

        self.path = path
        self.symbol_ctx = SymbolCtx(unit_id)

    def items(self) -> Iterator[AST.ProgramItem]:
        return iter(self.__program.items)
