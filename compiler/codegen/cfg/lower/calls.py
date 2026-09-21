
"""C7（调用部分）：调用/方法调用/间接调用下降。

从 `CfgBuilder` 搬出的调用簇；表达式求值经 `CallsHost.resolve_val` 回调，
类型转换与取址经 `CallsHost.values`（`ValueLowerer`）。
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from compiler.analysis.ty import ty as Type
from compiler.analysis.ty.context import TypeCtx
from compiler.analysis.unit import hir as HIR
from compiler.codegen.cfg import ir as IR
from compiler.codegen.cfg.passes.checks import CheckState
from compiler.codegen.cfg.passes.emitter import FunctionEmitter
from compiler.codegen.cfg.lower.values import ValueLowerer
from compiler.utils.log import CompilerLog


def _ch_block():
    """cfg.block 日志通道（类体内引用，按约定用单下划线）。"""
    return CompilerLog.get("cfg.block")


@dataclass(frozen=True)
class CallsHost:
    """调用下降向构建器借用的能力。"""

    emitter: FunctionEmitter
    checks: CheckState
    type_ctx: TypeCtx
    resolve_val: Callable[[HIR.Expr], IR.Value]
    set_terminator: Callable[[IR.Terminator], None]
    is_fat_pointer: Callable[[IR.Value], bool]
    values: "ValueLowerer"

class CallsLowerer:
    """调用下降器。"""

    def __init__(self, host: CallsHost) -> None:
        self.__host = host

    def resolve_call(self, expr: HIR.Call) -> IR.Value:
        arg_vals = [self.__host.resolve_val(arg) for arg in expr.args]
        result = self.build_call(expr.func, arg_vals, expr.type_id)
        # If the callee returns never, control never returns — terminate block
        if expr.type_id == self.__host.type_ctx.never_id:
            self.__host.set_terminator(IR.Panic(IR.StringLiteral(value="unreachable: never-returning function returned", type_id=TypeCtx.str_id)))
        return result

    def resolve_invoke(self, expr: HIR.Invoke) -> IR.Value:
        callee = self.__host.resolve_val(expr.callable)
        arg_vals = [self.__host.resolve_val(arg) for arg in expr.args]
        resolved = self.__host.type_ctx.resolve_aliases(expr.callable.type_id)
        if isinstance(self.__host.type_ctx[resolved], Type.FunctionType):
            result = self.build_call(resolved, arg_vals, expr.type_id)
            if expr.type_id == self.__host.type_ctx.never_id:
                self.__host.set_terminator(IR.Panic(IR.StringLiteral(value="unreachable: never-returning function returned", type_id=TypeCtx.str_id)))
            return result
        return self.build_invoke(callee, arg_vals, expr.type_id)

    def resolve_method_call(self, expr: HIR.MethodCall) -> IR.Value:
        method_type = self.__host.type_ctx[expr.method_id]
        assert isinstance(method_type, Type.MethodType)

        arg_vals = [self.__host.resolve_val(arg) for arg in expr.args]

        if method_type.custom_def.is_static:
            return self.build_call(expr.method_id, arg_vals, expr.type_id)

        # non-static: pass receiver address as the first argument.
        # 调用侧 receiver 折算:方法签名 receiver 是 T&(24B,分级指针),而调用侧实参
        # 几乎全是 T*(40B 胖指针)——值变量(VarPtr)、指针变量(p.method() auto-deref
        # 后)、field 派生均如此;唯一"天然 24B"的是方法体内 self.method()(T& deref,
        # __resolve_deref_addr 返回 operand 值即 T&)。统一折算到 alloc_ref(pointee):
        # __build_cast 的 LLVM T*→T& 分支做 40B→24B 收缩,已是 T& 时走 T&→T& identity。
        receiver_addr = self.__host.values.resolve_addr_fat(expr.receiver)
        # 调用侧修复:调用侧 T* receiver 折算(T*→T&)前恢复 in_bounds 检查。
        # 折算删除 index/size 字段,方法体内 self 访问仅剩 CheckRefAccess(live),
        # one-past-end 指针(index==size, 良构)的 in_bounds 语义随之丢失。
        # 折算前对胖 T* receiver 发射 CheckInBounds(p,1)(与
        # FieldPtr/解引用同一前提);RefType receiver(方法体内 self.method())天然
        # 24B 引用、无越界概念,__is_fat_pointer 恒 False 自动跳过;raw 模式无检查。
        if self.__host.is_fat_pointer(receiver_addr):
            if self.__host.checks.dedup(self.__host.checks.ptr_key(receiver_addr, "ib")):
                _ch_block().debug(lambda: "check dedup MethodCall receiver: in_bounds(p,1) 共享(同块同值相邻)")
            else:
                self.__host.emitter.emit(IR.CheckRequest(kind=IR.CHECK_REQUEST_IN_BOUNDS, operands=[receiver_addr]))
                _ch_block().debug(lambda: "check insert MethodCall receiver: in_bounds(p,1) (调用折算→安全修复, one-past-end 恢复)")
        ref_type_id = self.receiver_ref_type(receiver_addr)
        receiver_ref = self.__host.values.build_cast(receiver_addr, ref_type_id)
        return self.build_call(expr.method_id, [receiver_ref] + arg_vals, expr.type_id)

    def receiver_ref_type(self, receiver_addr: IR.Value) -> int:
        """调用折算: receiver 折算目标类型 = alloc_ref(接收者值类型)。

        receiver_addr.type_id 形态:值变量/auto-deref 指针变量/field 派生为 T*
        (pointee 即接收者值类型);方法体内 self.method() 已是 T&。两者 pointee
        都是值类型,统一 alloc_ref;LLVM cast 按 src/dst 分派收缩或 identity。
        """
        resolved = self.__host.type_ctx.resolve_aliases(receiver_addr.type_id)
        addr_ty = self.__host.type_ctx[resolved]
        if isinstance(addr_ty, (Type.PointerType, Type.RefType)):
            return self.__host.type_ctx.alloc_ref(addr_ty.pointee_type)
        return receiver_addr.type_id

    def build_call(self, callee_type: int, args: list[IR.Value], result_type: int) -> IR.Value:
        # 检查合并:调用可能释放/写锁槽 → 失效(先补发挂起 InBounds 义务,保证逃逸前失败)
        self.__host.checks.invalidate()
        result = IR.Reg(name=self.__host.emitter.new_name(), type_id=result_type)
        return self.__host.emitter.emit(IR.Call(result=result, callee_type=callee_type, args=args)).result

    def build_invoke(self, callee: IR.Value, args: list[IR.Value], result_type: int) -> IR.Value:
        self.__host.checks.invalidate()
        result = IR.Reg(name=self.__host.emitter.new_name(), type_id=result_type)
        return self.__host.emitter.emit(IR.Invoke(result=result, callee=callee, args=args)).result
