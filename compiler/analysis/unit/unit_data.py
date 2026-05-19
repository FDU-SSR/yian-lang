from __future__ import annotations

from compiler.analysis.symbol.context import SymbolCtx
from compiler.frontend.parse.ast import Program


class UnitData:
    def __init__(self, program: Program, unit_id: int):
        self.__program = program
        self.__unit_id = unit_id

        self.__symbol_ctx = SymbolCtx(unit_id)
