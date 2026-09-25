
"""System builtin lowering for CFG."""
from __future__ import annotations

from compiler.analysis.ty.context import TypeCtx
from compiler.analysis.unit import hir as HIR
from compiler.codegen.cfg import ir as IR
from compiler.codegen.cfg.lower.resolver import ExprResolver
from compiler.codegen.cfg.lower.state import FunctionState


class SysLowerer:
    """系统内建下降器。"""

    def __init__(self, state: FunctionState, resolver: ExprResolver) -> None:
        self.__state = state
        self.__resolver = resolver

    def resolve_sys_read(self, expr: HIR.Builtin) -> IR.Value:
        fd = self.__resolver.resolve_val(expr.args[0])
        buf = self.__resolver.resolve_val(expr.args[1])
        return self.build_sys_read(fd, buf)

    def resolve_sys_write(self, expr: HIR.Builtin) -> IR.Value:
        fd = self.__resolver.resolve_val(expr.args[0])
        buf = self.__resolver.resolve_val(expr.args[1])
        return self.build_sys_write(fd, buf)

    def build_sys_read(self, fd: IR.Value, buf: IR.Value) -> IR.Value:
        if self.__state.pointers.is_fat_view(buf):
            self.__state.emitter.emit(IR.CheckRequest(
                kind=IR.CHECK_REQUEST_VIEW, operands=[buf],
                live=not self.__state.pointers.is_frame_locked(buf),
            ))
        result = IR.Reg(name=self.__state.emitter.new_name(), type_id=TypeCtx.str_id)
        return self.__state.emitter.emit(IR.SysRead(result=result, fd=fd, buf=buf)).result

    def build_sys_write(self, fd: IR.Value, buf: IR.Value) -> IR.Value:
        if self.__state.pointers.is_fat_view(buf):
            self.__state.emitter.emit(IR.CheckRequest(
                kind=IR.CHECK_REQUEST_VIEW, operands=[buf],
                live=not self.__state.pointers.is_frame_locked(buf),
            ))
        self.__state.emitter.emit(IR.SysWrite(fd=fd, buf=buf))
        return self.__state.emitter.void_reg()

    def resolve_open(self, expr: HIR.Builtin) -> IR.Value:
        path = self.__resolver.resolve_val(expr.args[0])
        flags = self.__resolver.resolve_val(expr.args[1])
        return self.build_open(path, flags)

    def resolve_close(self, expr: HIR.Builtin) -> IR.Value:
        fd = self.__resolver.resolve_val(expr.args[0])
        return self.build_close(fd)

    def build_open(self, path: IR.Value, flags: IR.Value) -> IR.Value:
        if self.__state.pointers.is_fat_view(path):
            self.__state.emitter.emit(IR.CheckRequest(
                kind=IR.CHECK_REQUEST_VIEW, operands=[path],
                live=not self.__state.pointers.is_frame_locked(path),
            ))
        result = IR.Reg(name=self.__state.emitter.new_name(), type_id=TypeCtx.i32_id)
        return self.__state.emitter.emit(IR.Open(result=result, path=path, flags=flags)).result

    def build_close(self, fd: IR.Value) -> IR.Value:
        result = IR.Reg(name=self.__state.emitter.new_name(), type_id=TypeCtx.i32_id)
        return self.__state.emitter.emit(IR.Close(result=result, fd=fd)).result

    def resolve_sqrt(self, expr: HIR.Builtin) -> IR.Value:
        value = self.__resolver.resolve_val(expr.args[0])
        result = IR.Reg(name=self.__state.emitter.new_name(), type_id=expr.type_id)
        return self.__state.emitter.emit(IR.Sqrt(result=result, value=value)).result

    def resolve_sin(self, expr: HIR.Builtin) -> IR.Value:
        value = self.__resolver.resolve_val(expr.args[0])
        result = IR.Reg(name=self.__state.emitter.new_name(), type_id=expr.type_id)
        return self.__state.emitter.emit(IR.Sin(result=result, value=value)).result

    def resolve_cos(self, expr: HIR.Builtin) -> IR.Value:
        value = self.__resolver.resolve_val(expr.args[0])
        result = IR.Reg(name=self.__state.emitter.new_name(), type_id=expr.type_id)
        return self.__state.emitter.emit(IR.Cos(result=result, value=value)).result

    def resolve_mem_copy(self, expr: HIR.Builtin) -> IR.Value:
        dest = self.__resolver.resolve_val(expr.args[0])
        src = self.__resolver.resolve_val(expr.args[1])
        count = self.__resolver.resolve_val(expr.args[2])
        self.__state.emitter.emit(IR.MemCopy(dest=dest, src=src, count=count))
        return self.__state.emitter.void_reg()

    def resolve_arg_count(self, _expr: HIR.Builtin) -> IR.Value:
        result = IR.Reg(name=self.__state.emitter.new_name(), type_id=TypeCtx.u64_id)
        return self.__state.emitter.emit(IR.ArgCount(result=result)).result

    def resolve_arg_bytes(self, expr: HIR.Builtin) -> IR.Value:
        index = self.__resolver.resolve_val(expr.args[0])
        result = IR.Reg(name=self.__state.emitter.new_name(), type_id=expr.type_id)
        return self.__state.emitter.emit(IR.ArgBytes(result=result, index=index)).result
