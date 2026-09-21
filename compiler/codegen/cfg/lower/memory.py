
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
        # 按指针层级插入检查(按 type_id 分派):
        #   PointerType → safe_access(p, 1) = live(p) ∧ in_bounds(p, 1) 前检
        #   RefType     → 仅 live(r)(T& 免 in_bounds)
        frame_locked = self.__host.checks.is_frame_locked(ptr) or self.__host.checks.is_live_known(ptr)
        live_covered = frame_locked or self.__host.checks.dedup(self.__host.checks.live_key(ptr))
        if isinstance(ptr_type, Type.RefType) and not self.__host.raw_pointers:
            if live_covered:
                _ch_block().debug(lambda: "check skip Load(T&): 帧内或同出处 live 已覆盖")
            elif self.__host.checks.dedup(self.__host.checks.ptr_key(ptr, "ref")):
                _ch_block().debug(lambda: "check dedup Load(T&): live(r) 共享(同块同值相邻)")
            else:
                self.__host.emitter.emit(IR.CheckRefAccess(ptr=ptr))
                _ch_block().debug(lambda: "check insert Load(T&): live(r) 仅 live,免 in_bounds (tiered-pointers)")
        elif self.__host.is_fat_pointer(ptr):
            if live_covered and self.__host.checks.merge_access(ptr, live=False):
                _ch_block().debug(lambda: "check merge Load: ElementArith∧InBounds(同出处 live 已覆盖)")
            elif not live_covered and self.__host.checks.merge_access(ptr):
                _ch_block().debug(lambda: "check merge Load: ElementArith∧InBounds∧live 合取检查")
            elif self.__host.checks.dedup(self.__host.checks.ptr_key(ptr, "safe")):
                _ch_block().debug(lambda: "check dedup Load: safe_access(p,1) 共享(同块同值相邻)")
            else:
                self.__host.emitter.emit(IR.CheckSafeAccess(ptr=ptr, live=not live_covered))
                _ch_block().debug(lambda: "check insert Load: safe_access(p,1) = live(p) ∧ in_bounds(p,1)")
        result = IR.Reg(name=self.__host.emitter.new_name(), type_id=ptr_type.pointee_type)
        return self.__host.emitter.emit(IR.Load(result=result, ptr=ptr)).result

    def build_store(self, value: IR.Value, ptr: IR.Value) -> None:
        ptr_type = self.__host.type_ctx[ptr.type_id]
        # 按指针层级插入检查(按 type_id 分派,同 Load 的检查与地址折算):
        #   PointerType → safe_access(p, 1)
        #   RefType     → 仅 live(r)(T& 免 in_bounds)
        frame_locked = self.__host.checks.is_frame_locked(ptr) or self.__host.checks.is_live_known(ptr)
        live_covered = frame_locked or self.__host.checks.dedup(self.__host.checks.live_key(ptr))
        if isinstance(ptr_type, Type.RefType) and not self.__host.raw_pointers:
            if live_covered:
                _ch_block().debug(lambda: "check skip Store(T&): 帧内或同出处 live 已覆盖")
            elif self.__host.checks.dedup(self.__host.checks.ptr_key(ptr, "ref")):
                _ch_block().debug(lambda: "check dedup Store(T&): live(r) 共享(同块同值相邻)")
            else:
                self.__host.emitter.emit(IR.CheckRefAccess(ptr=ptr))
                _ch_block().debug(lambda: "check insert Store(T&): live(r) 仅 live,免 in_bounds (tiered-pointers)")
        elif self.__host.is_fat_pointer(ptr):
            if live_covered and self.__host.checks.merge_access(ptr, live=False):
                _ch_block().debug(lambda: "check merge Store: ElementArith∧InBounds(同出处 live 已覆盖)")
            elif not live_covered and self.__host.checks.merge_access(ptr):
                _ch_block().debug(lambda: "check merge Store: ElementArith∧InBounds∧live 合取检查")
            elif self.__host.checks.dedup(self.__host.checks.ptr_key(ptr, "safe")):
                _ch_block().debug(lambda: "check dedup Store: safe_access(p,1) 共享(同块同值相邻)")
            else:
                self.__host.emitter.emit(IR.CheckSafeAccess(ptr=ptr, live=not live_covered))
                _ch_block().debug(lambda: "check insert Store: safe_access(p,1) = live(p) ∧ in_bounds(p,1)")
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
        # CFG 层插入检查:算术 → 良构检查(0 ≤ index+n ≤ size)
        if self.__host.is_fat_pointer(base):
            # 嵌套派生链(安全修复 复核):同 FieldPtr——base 有挂起义务先补发再继续
            owed_elem = self.__host.checks.pop_owed_in_bounds(base)
            if owed_elem is not None:
                if self.__host.checks.dedup(self.__host.checks.ptr_key(owed_elem, "ib")):
                    _ch_block().debug(lambda: "check dedup ElementPtr flush: in_bounds(elem,1) 已检查(嵌套链, 检查合并)")
                else:
                    self.__host.emitter.emit(IR.CheckInBounds(ptr=owed_elem))
                    _ch_block().debug(lambda: "check insert ElementPtr flush: 嵌套派生链补发 in_bounds(elem,1) (合并检查义务消费)")
            if self.__host.checks.dedup(self.__host.checks.pair_key(base, offset, "elarith")):
                _ch_block().debug(lambda: "check dedup ElementPtr: well_formed(p') 共享(同 base/offset, 检查合并)")
            else:
                self.__host.emitter.emit(IR.CheckElementArith(base=base, offset=offset))
                _ch_block().debug(lambda: "check insert ElementPtr: well_formed(p')")
        result = IR.Reg(name=self.__host.emitter.new_name(), type_id=result_type)
        elem_ptr = self.__host.emitter.emit(IR.ElementPtr(result=result, base=base, offset=offset)).result
        # 合并跟踪:记录派生链 (base, offset),供 FieldPtr→Load/Store 合取检查
        if self.__host.is_fat_pointer(base):
            self.__host.checks.note_element_derived(elem_ptr, base, offset)
            self.__host.checks.inherit_frame_lock(elem_ptr, base)
            self.__host.checks.inherit_root(elem_ptr, base)
        # 惰性左值路径:沿裸基址的算术派生保持裸(检查已由基址判定跳过)
        if self.__host.checks.is_raw(base):
            self.__host.checks.mark_raw(elem_ptr)
        return elem_ptr

    def build_field_ptr(self, base: IR.Value, field_index: int, field_type: int) -> IR.Value:
        # 按指针层级插入检查(按 type_id 分派):
        #   PointerType → in_bounds(p_s, 1)(重锚定前提,对 one-past-end 的 s 取字段时失败)
        #   RefType     → 仅 live(r)(T& 免 in_bounds;引用无 index/size,恒指单个元素)
        merged_elem_name: str | None = None
        base_ty = self.__host.type_ctx[base.type_id]
        if isinstance(base_ty, Type.RefType) and not self.__host.raw_pointers:
            if self.__host.checks.is_frame_locked(base):
                _ch_block().debug(lambda: "check skip FieldPtr(T&): 帧内引用 live 恒真")
            elif self.__host.checks.dedup(self.__host.checks.live_key(base)) or self.__host.checks.dedup(self.__host.checks.ptr_key(base, "ref")):
                _ch_block().debug(lambda: "check dedup FieldPtr(T&): live(r) 共享(同块同值或同出处)")
            else:
                self.__host.emitter.emit(IR.CheckRefAccess(ptr=base))
                _ch_block().debug(lambda: "check insert FieldPtr(T&): live(r) 仅 live,免 in_bounds (tiered-pointers)")
        elif self.__host.is_fat_pointer(base):
            # 嵌套派生链(安全修复 复核):base 是挂起 FieldPtr 结果时先补发
            # in_bounds(elem,1)(消费义务)再派发——OOB 读/写必须先于访问
            # 报告安全错误,不得推迟到终止符补发(每访问前提仍成立)。
            owed_elem = self.__host.checks.pop_owed_in_bounds(base)
            if owed_elem is not None:
                if self.__host.checks.dedup(self.__host.checks.ptr_key(owed_elem, "ib")):
                    _ch_block().debug(lambda: "check dedup FieldPtr flush: in_bounds(elem,1) 已检查(嵌套链, 检查合并)")
                else:
                    self.__host.emitter.emit(IR.CheckInBounds(ptr=owed_elem))
                    _ch_block().debug(lambda: "check insert FieldPtr flush: 嵌套派生链补发 in_bounds(elem,1) (合并检查义务消费)")
            elem_entry = self.__host.checks.elem_entry(base)
            if elem_entry is not None and isinstance(elem_entry, IR.Reg):
                # 合并路径:in_bounds(elem,1) 挂起为义务,并入访问点的
                # CheckElementAccess 合取检查;若访问不相邻,失效点(调用/
                # Delete/终止)补发——one-past-end 前提不丢。
                merged_elem_name = elem_entry.name
                _ch_block().debug(lambda: "check merge FieldPtr: in_bounds 挂起并入 ElementAccess (检查合并, 派生链可对)")
            elif self.__host.checks.dedup(self.__host.checks.ptr_key(base, "ib")):
                _ch_block().debug(lambda: "check dedup FieldPtr: in_bounds(p_s,1) 共享(同块同值相邻)")
            else:
                self.__host.emitter.emit(IR.CheckInBounds(ptr=base))
                _ch_block().debug(lambda: "check insert FieldPtr: in_bounds(p_s,1) (重锚定前提)")
        result = IR.Reg(name=self.__host.emitter.new_name(), type_id=self.__host.type_ctx.alloc_pointer(field_type))
        field_ptr = self.__host.emitter.emit(IR.FieldPtr(result=result, base=base, field_index=field_index)).result
        if merged_elem_name is not None:
            self.__host.checks.note_field_derived(field_ptr, merged_elem_name)
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
            self.__host.emitter.emit(IR.CheckPtrDiff(lhs=lhs, rhs=rhs))
            _ch_block().debug(lambda: "check insert PtrDiff: data 相等 + 良构 + 无回绕")
        result = IR.Reg(name=self.__host.emitter.new_name(), type_id=TypeCtx.i64_id)
        return self.__host.emitter.emit(IR.PtrDiff(result=result, lhs=lhs, rhs=rhs)).result

    def build_ptr_cmp(self, op: BinaryOperator, lhs: IR.Value, rhs: IR.Value, type_id: int) -> IR.Value:
        # 指针序比较检查:序比较先查 data 相等(前提,跨对象序比较失败);
        # 相等比较 按 (data, index) 二元组、无前提检查。
        # 与 ElementPtr 算术一致:比较本身不访问内存,不检查 allocation live。
        if op in (BinaryOperator.Lt, BinaryOperator.Gt, BinaryOperator.Leq, BinaryOperator.Geq):
            self.__host.emitter.emit(IR.CheckPtrCmp(lhs=lhs, rhs=rhs))
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
