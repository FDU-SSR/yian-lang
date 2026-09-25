"""CFG module passed between lowering and transformation passes."""
from __future__ import annotations

from dataclasses import dataclass

from compiler.analysis.ty.context import TypeCtx
from compiler.codegen.cfg import ir as IR
from compiler.frontend.lex.position import SrcSpan


@dataclass
class CfgFunction:
    function: IR.Function
    span: SrcSpan
    return_type: int
    next_name: int

    def void_value(self) -> IR.Value:
        result = IR.Reg(name=str(self.next_name), type_id=TypeCtx.void_id)
        self.next_name += 1
        return result


class CfgModule:
    def __init__(self, type_ctx: TypeCtx, raw_pointers: bool) -> None:
        self.type_ctx = type_ctx
        self.raw_pointers = raw_pointers
        self.functions: dict[int, CfgFunction] = {}

    def add(self, record: CfgFunction) -> None:
        self.functions[record.function.type_id] = record

    def export(self) -> dict[int, IR.Function]:
        return {type_id: record.function for type_id, record in self.functions.items()}
