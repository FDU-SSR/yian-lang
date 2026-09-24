
"""系统内建下降：sys_read/write、open/close、sqrt、memcpy、进程参数。

只依赖发射句柄、检查簇与 `is_fat_view` 判定；不引用其它下降簇。
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from compiler.analysis.ty.context import TypeCtx
from compiler.analysis.unit import hir as HIR
from compiler.codegen.cfg import ir as IR
from compiler.codegen.cfg.lower.checks import CheckState
from compiler.codegen.cfg.lower.emitter import FunctionEmitter


@dataclass(frozen=True)
class SysHost:
    """系统内建下降向构建器借用的能力。"""

    emitter: FunctionEmitter
    checks: CheckState
    resolve_val: Callable[[HIR.Expr], IR.Value]
    is_fat_view: Callable[[IR.Value], bool]

class SysLowerer:
    """系统内建下降器。"""

    def __init__(self, host: SysHost) -> None:
        self.__host = host

    def resolve_sys_read(self, expr: HIR.Builtin) -> IR.Value:
        fd = self.__host.resolve_val(expr.args[0])
        buf = self.__host.resolve_val(expr.args[1])
        return self.build_sys_read(fd, buf)

    def resolve_sys_write(self, expr: HIR.Builtin) -> IR.Value:
        fd = self.__host.resolve_val(expr.args[0])
        buf = self.__host.resolve_val(expr.args[1])
        return self.build_sys_write(fd, buf)

    def build_sys_read(self, fd: IR.Value, buf: IR.Value) -> IR.Value:
        if self.__host.is_fat_view(buf):
            self.__host.emitter.emit(IR.CheckRequest(
                kind=IR.CHECK_REQUEST_VIEW, operands=[buf],
                live=not self.__host.checks.is_frame_locked(buf),
            ))
        result = IR.Reg(name=self.__host.emitter.new_name(), type_id=TypeCtx.str_id)
        return self.__host.emitter.emit(IR.SysRead(result=result, fd=fd, buf=buf)).result

    def build_sys_write(self, fd: IR.Value, buf: IR.Value) -> IR.Value:
        if self.__host.is_fat_view(buf):
            self.__host.emitter.emit(IR.CheckRequest(
                kind=IR.CHECK_REQUEST_VIEW, operands=[buf],
                live=not self.__host.checks.is_frame_locked(buf),
            ))
        self.__host.emitter.emit(IR.SysWrite(fd=fd, buf=buf))
        return self.__host.emitter.void_reg()

    def resolve_open(self, expr: HIR.Builtin) -> IR.Value:
        path = self.__host.resolve_val(expr.args[0])
        flags = self.__host.resolve_val(expr.args[1])
        return self.build_open(path, flags)

    def resolve_close(self, expr: HIR.Builtin) -> IR.Value:
        fd = self.__host.resolve_val(expr.args[0])
        return self.build_close(fd)

    def build_open(self, path: IR.Value, flags: IR.Value) -> IR.Value:
        if self.__host.is_fat_view(path):
            self.__host.emitter.emit(IR.CheckRequest(
                kind=IR.CHECK_REQUEST_VIEW, operands=[path],
                live=not self.__host.checks.is_frame_locked(path),
            ))
        result = IR.Reg(name=self.__host.emitter.new_name(), type_id=TypeCtx.i32_id)
        return self.__host.emitter.emit(IR.Open(result=result, path=path, flags=flags)).result

    def build_close(self, fd: IR.Value) -> IR.Value:
        result = IR.Reg(name=self.__host.emitter.new_name(), type_id=TypeCtx.i32_id)
        return self.__host.emitter.emit(IR.Close(result=result, fd=fd)).result

    def resolve_sqrt(self, expr: HIR.Builtin) -> IR.Value:
        value = self.__host.resolve_val(expr.args[0])
        result = IR.Reg(name=self.__host.emitter.new_name(), type_id=expr.type_id)
        return self.__host.emitter.emit(IR.Sqrt(result=result, value=value)).result

    def resolve_sin(self, expr: HIR.Builtin) -> IR.Value:
        value = self.__host.resolve_val(expr.args[0])
        result = IR.Reg(name=self.__host.emitter.new_name(), type_id=expr.type_id)
        return self.__host.emitter.emit(IR.Sin(result=result, value=value)).result

    def resolve_cos(self, expr: HIR.Builtin) -> IR.Value:
        value = self.__host.resolve_val(expr.args[0])
        result = IR.Reg(name=self.__host.emitter.new_name(), type_id=expr.type_id)
        return self.__host.emitter.emit(IR.Cos(result=result, value=value)).result

    def resolve_mem_copy(self, expr: HIR.Builtin) -> IR.Value:
        dest = self.__host.resolve_val(expr.args[0])
        src = self.__host.resolve_val(expr.args[1])
        count = self.__host.resolve_val(expr.args[2])
        self.__host.emitter.emit(IR.MemCopy(dest=dest, src=src, count=count))
        return self.__host.emitter.void_reg()

    def resolve_arg_count(self, _expr: HIR.Builtin) -> IR.Value:
        result = IR.Reg(name=self.__host.emitter.new_name(), type_id=TypeCtx.u64_id)
        return self.__host.emitter.emit(IR.ArgCount(result=result)).result

    def resolve_arg_bytes(self, expr: HIR.Builtin) -> IR.Value:
        index = self.__host.resolve_val(expr.args[0])
        result = IR.Reg(name=self.__host.emitter.new_name(), type_id=expr.type_id)
        return self.__host.emitter.emit(IR.ArgBytes(result=result, index=index)).result
