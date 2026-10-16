
"""Memory and pointer operations emitted while lowering HIR to CFG."""
from __future__ import annotations

from yianc.analysis.ty import ty as Type
from yianc.frontend.parse.operator import BinaryOperator
from yianc.analysis.ty.context import TypeCtx
from yianc.codegen.cfg import ir as IR
from yianc.codegen.cfg.lower.state import FunctionState
from yianc.utils.log import CompilerLog


def _ch_block():
    """cfg.block logging channel (single underscore permits references from class bodies)."""
    return CompilerLog.get("cfg.block")


class MemoryOps:
    """Memory and pointer primitive lowering."""

    def __init__(self, state: FunctionState) -> None:
        self.__state = state

    def build_load(self, ptr: IR.Value) -> IR.Value:
        ptr_type = self.__state.session.type_ctx[ptr.type_id]
        assert isinstance(ptr_type, (Type.PointerType, Type.RefType))
        # The insertion pass reconstructs prechecks from IR: T& checks only live, while fat pointers check
        # safe_access(p,1) or the combined ElementArith/InBounds/live predicate.
        result = IR.Reg(name=self.__state.emitter.new_name(), type_id=ptr_type.pointee_type)
        return self.__state.emitter.emit(IR.Load(result=result, ptr=ptr)).result

    def build_store(self, value: IR.Value, ptr: IR.Value) -> None:
        # The insertion pass reconstructs access prechecks from IR, using the same dispatch as Load.
        self.__state.emitter.emit(IR.Store(ptr=ptr, value=value))

    def build_malloc(self, type_id: int, size: IR.Value) -> IR.Value:
        # CFG Malloc returns <data=b+H, word, index=0, size=n>, constructed by LLVM.
        # LLVM generates word from the block base and the header's previous generation, incremented on allocation.
        # ZST pointees use the undef fast path without header writes; raw mode has no header.
        key: IR.Value | None = None
        result = IR.Reg(name=self.__state.emitter.new_name(), type_id=self.__state.session.type_ctx.alloc_pointer(type_id))
        malloc = self.__state.emitter.emit(IR.Malloc(result=result, type_id=type_id, size=size, key=key)).result
        return malloc

    def build_realloc(self, type_id: int, ptr: IR.Value, size: IR.Value) -> IR.Value:
        result = IR.Reg(
            name=self.__state.emitter.new_name(),
            type_id=self.__state.session.type_ctx.alloc_pointer(type_id),
        )
        realloc = self.__state.emitter.emit(
            IR.Realloc(result=result, type_id=type_id, ptr=ptr, size=size)
        ).result
        return realloc

    def build_element_ptr(self, base: IR.Value, offset: IR.Value, result_type: int) -> IR.Value:
        # The insertion pass reconstructs arithmetic validity (0 <= index+n <= size) and discharges nested
        # derivation obligations along IR.ElementPtr edges; this layer only emits nodes and records provenance.
        result = IR.Reg(name=self.__state.emitter.new_name(), type_id=result_type)
        elem_ptr = self.__state.emitter.emit(IR.ElementPtr(result=result, base=base, offset=offset)).result
        return elem_ptr

    def build_field_ptr(self, base: IR.Value, field_index: int, field_type: int) -> IR.Value:
        # The insertion pass reconstructs re-anchoring preconditions (in_bounds(p_s,1), or live for T&) and
        # their combined checks along IR.FieldPtr edges; this layer emits nodes and records provenance.
        result = IR.Reg(name=self.__state.emitter.new_name(), type_id=self.__state.session.type_ctx.alloc_pointer(field_type))
        field_ptr = self.__state.emitter.emit(IR.FieldPtr(result=result, base=base, field_index=field_index)).result
        return field_ptr

    def build_ptr_diff(self, lhs: IR.Value, rhs: IR.Value) -> IR.Value:
        # CFG preconditions: equal data, valid arithmetic, and no wrap; cross-object differences fail.
        # As with ElementPtr arithmetic, this operation does not access memory or check allocation liveness.
        if self.__state.pointers.is_fat_pointer(lhs) and self.__state.pointers.is_fat_pointer(rhs):
            self.__state.emitter.emit(IR.CheckRequest(kind=IR.CHECK_REQUEST_PTRDIFF, operands=[lhs, rhs]))
            _ch_block().debug(lambda: "check insert PtrDiff: data 相等 + 良构 + 无回绕")
        result = IR.Reg(name=self.__state.emitter.new_name(), type_id=TypeCtx.i64_id)
        return self.__state.emitter.emit(IR.PtrDiff(result=result, lhs=lhs, rhs=rhs)).result

    def build_ptr_cmp(self, op: BinaryOperator, lhs: IR.Value, rhs: IR.Value, type_id: int) -> IR.Value:
        # Ordering checks equal data first; cross-object ordering fails.
        # Equality compares the (data,index) pair without a precondition check.
        # Like ElementPtr arithmetic, comparison does not access memory or check allocation liveness.
        if op in (BinaryOperator.Lt, BinaryOperator.Gt, BinaryOperator.Leq, BinaryOperator.Geq):
            self.__state.emitter.emit(IR.CheckRequest(kind=IR.CHECK_REQUEST_PTRCMP, operands=[lhs, rhs]))
            _ch_block().debug(lambda: "check insert PtrCmp: data 相等")
        result = IR.Reg(name=self.__state.emitter.new_name(), type_id=type_id)
        return self.__state.emitter.emit(IR.PtrCmp(result=result, op=op, lhs=lhs, rhs=rhs)).result


    def build_extract_value(self, base: IR.Value, field_index: int, type_id: int) -> IR.Value:
        result = IR.Reg(name=self.__state.emitter.new_name(), type_id=type_id)
        return self.__state.emitter.emit(IR.ExtractValue(result=result, base=base, field_index=field_index)).result
