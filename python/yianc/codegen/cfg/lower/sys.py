"""System builtin lowering for CFG."""
from __future__ import annotations

from yianc.analysis.unit import hir as HIR
from yianc.codegen.cfg import ir as IR
from yianc.codegen.cfg.lower.resolver import ExprResolver
from yianc.codegen.cfg.lower.state import FunctionState


class SysLowerer:
    """Lower the retained system-facing builtins."""

    def __init__(self, state: FunctionState, resolver: ExprResolver) -> None:
        self.__state = state
        self.__resolver = resolver

    def resolve_mem_copy(self, expr: HIR.Builtin) -> IR.Value:
        dest = self.__resolver.resolve_val(expr.args[0])
        src = self.__resolver.resolve_val(expr.args[1])
        count = self.__resolver.resolve_val(expr.args[2])
        self.__state.emitter.emit(IR.MemCopy(dest=dest, src=src, count=count))
        return self.__state.emitter.void_reg()

    def resolve_arg_bytes(self, expr: HIR.Builtin) -> IR.Value:
        index = self.__resolver.resolve_val(expr.args[0])
        result = IR.Reg(name=self.__state.emitter.new_name(), type_id=expr.type_id)
        return self.__state.emitter.emit(IR.ArgBytes(result=result, index=index)).result
