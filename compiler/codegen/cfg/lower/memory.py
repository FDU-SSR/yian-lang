
"""C8：内存与指针原语下降（load/store/malloc、element/field ptr、ptr 算术与比较）。

检查的插入点仍写在这一簇里（P8 才升级为独立 pass），检查状态经 `MemoryHost.checks`
（`CheckState`）访问；类型转换经 `MemoryHost.values`（`ValueLowerer`）。
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from compiler.analysis.ty import ty as Type
from compiler.analysis.ty.type_ops import default_literals
from compiler.frontend.parse.operator import BinaryOperator, UnaryOperator
from compiler.analysis.ty.context import TypeCtx
from compiler.codegen.cfg import ir as IR
from compiler.codegen.cfg.passes.checks import CheckState
from compiler.codegen.cfg.passes.emitter import FunctionEmitter
from compiler.codegen.cfg.lower.values import ValueLowerer
from compiler.utils.log import CompilerLog


def _ch_block():
    """cfg.block 日志通道（类体内引用，按约定用单下划线）。"""
    return CompilerLog.get("cfg.block")


@dataclass(frozen=True)
class MemoryHost:
    """内存/指针原语下降向构建器借用的能力。"""

    emitter: FunctionEmitter
    checks: CheckState
    type_ctx: TypeCtx
    raw_pointers: bool
    is_fat_pointer: Callable[[IR.Value], bool]
    values: "ValueLowerer"

class MemoryLowerer:
    """内存/指针原语下降器。"""

    def __init__(self, host: MemoryHost) -> None:
        self.__host = host

    def build_load(self, ptr: IR.Value) -> IR.Value:
        ptr_type = self.__host.type_ctx[self.__host.type_ctx.resolve_aliases(ptr.type_id)]
        assert isinstance(ptr_type, (Type.PointerType, Type.RefType))
        # 访问前检由检查插入 pass 依 IR 重建（T& 只查 live；胖指针查
        # safe_access(p,1) 或 ElementArith∧InBounds∧live 合取）。
        result = IR.Reg(name=self.__host.emitter.new_name(), type_id=ptr_type.pointee_type)
        return self.__host.emitter.emit(IR.Load(result=result, ptr=ptr)).result

    def build_store(self, value: IR.Value, ptr: IR.Value) -> None:
        # 访问前检由检查插入 pass 依 IR 重建（分派同 Load）。
        self.__host.emitter.emit(IR.Store(ptr=ptr, value=value))

    def build_malloc(self, type_id: int, size: IR.Value) -> IR.Value:
        # CFG 层:Malloc 返回 4 字段聚合 ⟨data=b+H, word, index=0, size=n⟩(LLVM 层构造)。
        # word 由 LLVM 层按块首地址与块头里的上一代算好(分配处 +1), 不再用全局堆键。
        # pointee 为 ZST 时维持快路径(undef,不写块头);raw 模式无块头。
        key: IR.Value | None = None
        result = IR.Reg(name=self.__host.emitter.new_name(), type_id=self.__host.type_ctx.alloc_pointer(type_id))
        malloc = self.__host.emitter.emit(IR.Malloc(result=result, type_id=type_id, size=size, key=key)).result
        if not self.__host.raw_pointers:
            self.__host.checks.mark_root(malloc)
        return malloc

    def build_element_ptr(self, base: IR.Value, offset: IR.Value, result_type: int) -> IR.Value:
        # 良构检查（0 ≤ index+n ≤ size）与嵌套派生链的义务补发由检查插入 pass
        # 依 `IR.ElementPtr` 边重建；这里只发节点与登记出处。
        result = IR.Reg(name=self.__host.emitter.new_name(), type_id=result_type)
        elem_ptr = self.__host.emitter.emit(IR.ElementPtr(result=result, base=base, offset=offset)).result
        if self.__host.is_fat_pointer(base):
            self.__host.checks.inherit_frame_lock(elem_ptr, base)
            self.__host.checks.inherit_root(elem_ptr, base)
        # 惰性左值路径:沿裸基址的算术派生保持裸(检查已由基址判定跳过)
        if self.__host.checks.is_raw(base):
            self.__host.checks.mark_raw(elem_ptr)
        return elem_ptr

    def build_field_ptr(self, base: IR.Value, field_index: int, field_type: int) -> IR.Value:
        # 重锚定前提（in_bounds(p_s,1) / T& 的 live）与其合取合并由检查插入 pass
        # 依 `IR.FieldPtr` 边重建；这里只发节点与登记出处。
        result = IR.Reg(name=self.__host.emitter.new_name(), type_id=self.__host.type_ctx.alloc_pointer(field_type))
        field_ptr = self.__host.emitter.emit(IR.FieldPtr(result=result, base=base, field_index=field_index)).result
        # 惰性左值路径:沿裸基址的字段派生保持裸(检查已由基址判定跳过)
        if self.__host.checks.is_raw(base):
            self.__host.checks.mark_raw(field_ptr)
        else:
            self.__host.checks.inherit_frame_lock(field_ptr, base)
            self.__host.checks.inherit_root(field_ptr, base)
        return field_ptr

    def build_ptr_diff(self, lhs: IR.Value, rhs: IR.Value) -> IR.Value:
        # CFG 层插入检查:data 相等 + 良构 + 无回绕(异对象指针差失败)。
        # 与 ElementPtr 算术一致:该运算不访问内存,不检查 allocation live。
        if self.__host.is_fat_pointer(lhs) and self.__host.is_fat_pointer(rhs):
            self.__host.emitter.emit(IR.CheckRequest(kind=IR.CHECK_REQUEST_PTRDIFF, operands=[lhs, rhs]))
            _ch_block().debug(lambda: "check insert PtrDiff: data 相等 + 良构 + 无回绕")
        result = IR.Reg(name=self.__host.emitter.new_name(), type_id=TypeCtx.i64_id)
        return self.__host.emitter.emit(IR.PtrDiff(result=result, lhs=lhs, rhs=rhs)).result

    def build_ptr_cmp(self, op: BinaryOperator, lhs: IR.Value, rhs: IR.Value, type_id: int) -> IR.Value:
        # 指针序比较检查:序比较先查 data 相等(前提,跨对象序比较失败);
        # 相等比较 按 (data, index) 二元组、无前提检查。
        # 与 ElementPtr 算术一致:比较本身不访问内存,不检查 allocation live。
        if op in (BinaryOperator.Lt, BinaryOperator.Gt, BinaryOperator.Leq, BinaryOperator.Geq):
            self.__host.emitter.emit(IR.CheckRequest(kind=IR.CHECK_REQUEST_PTRCMP, operands=[lhs, rhs]))
            _ch_block().debug(lambda: "check insert PtrCmp: data 相等")
        result = IR.Reg(name=self.__host.emitter.new_name(), type_id=type_id)
        return self.__host.emitter.emit(IR.PtrCmp(result=result, op=op, lhs=lhs, rhs=rhs)).result

    def build_binary(self, op: BinaryOperator, lhs: IR.Value, rhs: IR.Value, type_id: int) -> IR.Value:
        type_id = default_literals(self.__host.type_ctx, type_id)
        # ── route pointer arithmetic to dedicated instructions ──
        lhs_ty = self.__host.type_ctx[lhs.type_id]
        rhs_ty = self.__host.type_ctx[rhs.type_id]

        # LLVM requires shift operands to have the same integer width.
        if op.is_shift() and lhs.type_id != rhs.type_id:
            rhs = self.__host.values.build_cast(rhs, lhs.type_id)

        if op == BinaryOperator.Add:
            if isinstance(lhs_ty, Type.PointerType):
                return self.build_element_ptr(lhs, rhs, type_id)
            if isinstance(rhs_ty, Type.PointerType):
                return self.build_element_ptr(rhs, lhs, type_id)

        if op == BinaryOperator.Sub:
            if isinstance(lhs_ty, Type.PointerType) and isinstance(rhs_ty, Type.PointerType):
                return self.build_ptr_diff(lhs, rhs)
            if isinstance(lhs_ty, Type.PointerType):
                # ptr - int → negate offset then ElementPtr
                zero = IR.IntLiteral(value=0, type_id=rhs.type_id)
                neg_offset = self.build_binary(BinaryOperator.Sub, zero, rhs, rhs.type_id)
                return self.build_element_ptr(lhs, neg_offset, type_id)

        # ── 指针比较字段化(指针比较)──
        # 胖指针(双方 PointerType 且 pointee 非 ZST)的比较路由至 PtrCmp:
        # 序比较先插 CheckPtrCmp(data 相等前提,跨对象失败);相等比较按
        # (data, index) 二元组。FunctionPointerType 非 PointerType,不参与。
        if op.is_comparison() and self.__host.is_fat_pointer(lhs) and self.__host.is_fat_pointer(rhs):
            return self.build_ptr_cmp(op, lhs, rhs, type_id)

        result = IR.Reg(name=self.__host.emitter.new_name(), type_id=type_id)
        return self.__host.emitter.emit(IR.Binary(result=result, op=op, lhs=lhs, rhs=rhs)).result

    def build_unary(self, op: UnaryOperator, operand: IR.Value, type_id: int) -> IR.Value:
        type_id = default_literals(self.__host.type_ctx, type_id)
        result = IR.Reg(name=self.__host.emitter.new_name(), type_id=type_id)
        return self.__host.emitter.emit(IR.Unary(result=result, op=op, operand=operand)).result

    def build_extract_value(self, base: IR.Value, field_index: int, type_id: int) -> IR.Value:
        result = IR.Reg(name=self.__host.emitter.new_name(), type_id=type_id)
        return self.__host.emitter.emit(IR.ExtractValue(result=result, base=base, field_index=field_index)).result
