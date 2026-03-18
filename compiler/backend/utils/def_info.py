from dataclasses import dataclass

from compiler.config.defs import SymbolId
from compiler.utils.IR import DefPoint
from llvmlite import ir


@dataclass
class DefInfo:
    def_point: DefPoint
    entry_block: ir.Block
    builder: ir.IRBuilder
    symbol_table: dict[SymbolId, ir.AllocaInstr]
    self_obj: ir.AllocaInstr | None
