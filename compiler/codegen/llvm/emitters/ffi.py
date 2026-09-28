# pyright: reportUnknownMemberType=false
"""FFI address extraction and byte-copy operations."""

from __future__ import annotations

from llvmlite import ir

from compiler.analysis.ty import ty as Type
from compiler.codegen.llvm.base.intrinsics import IntrinsicKind
from compiler.codegen.llvm.base.value import LLValue
from compiler.codegen.llvm.function.core import FunctionCore
from compiler.codegen.llvm.pointer.representation import PointerRepresentation
from compiler.codegen.llvm.pointer.safety import FatSafety
from compiler.runtime_error import RuntimeErrorCode


class FfiEmitter:
    def __init__(self, core: FunctionCore, pointers: PointerRepresentation, safety: FatSafety) -> None:
        self.__core = core
        self.__pointers = pointers
        self.__safety = safety

    def addr(self, reference: LLValue, result_type: int) -> LLValue:
        type_ctx = self.__core.context.type_ctx
        ty = type_ctx[type_ctx.resolve_aliases(reference.type_id)]
        assert isinstance(ty, Type.RefType)
        self.__safety.check_ref_access(reference)
        address = self.__pointers.fat_addr(reference, ty.pointee_type)
        return LLValue(result_type, address.ir_val)

    def parts(self, view: LLValue, result_type: int) -> LLValue:
        self.__check_view(view)
        data = self.__core.flow.builder.extract_value(view.ir_val, 0)  # type: ignore
        length = self.__view_length(view)
        result = self.__core.ir.undef(result_type)
        type_ctx = self.__core.context.type_ctx
        element_type = type_ctx[type_ctx.resolve_aliases(view.type_id)]
        pointee = element_type.element_type if isinstance(element_type, Type.SliceType) else type_ctx.u8_id
        result = self.__core.ir.insert_value(result, LLValue(type_ctx.alloc_cptr(pointee), data), 0)
        return self.__core.ir.insert_value(result, LLValue(type_ctx.u64_id, length), 1)

    def null(self, result_type: int) -> LLValue:
        ll_type = self.__core.context.ll_type_ctx.get_ll_type(result_type).ir_type
        return LLValue(result_type, ir.Constant(ll_type, None))  # type: ignore

    def ptr_cast(self, pointer: LLValue, result_type: int) -> LLValue:
        target_type = self.__core.context.ll_type_ctx.get_ll_type(result_type).ir_type
        value = self.__core.ir.bitcast(pointer.ir_val, target_type)
        return LLValue(result_type, value)

    def copy(self, destination: LLValue, source: LLValue, count: LLValue, from_c: bool) -> None:
        view = destination if from_c else source
        self.__check_view(view)
        length = self.__view_length(view)
        within = self.__core.flow.builder.icmp_unsigned("<=", count.ir_val, length)  # type: ignore
        self.__core.flow.emit_check(
            LLValue(self.__core.context.type_ctx.bool_id, within),
            RuntimeErrorCode.S001, "ffi-copy",
        )
        data = self.__core.flow.builder.extract_value(view.ir_val, 0)  # type: ignore
        target = data if from_c else destination.ir_val
        source_address = source.ir_val if from_c else data
        byte_ptr = self.__core.context.type_ctx.alloc_pointer(self.__core.context.type_ctx.u8_id)
        self.__core.ir.call_intrinsic(
            IntrinsicKind.MemCopy,
            [LLValue(byte_ptr, target), LLValue(byte_ptr, source_address), count],
        )

    def __view_length(self, view: LLValue) -> ir.Value:
        index = 1 if self.__core.context.raw_pointers else 2
        return self.__core.flow.builder.extract_value(view.ir_val, index)  # type: ignore

    def __check_view(self, view: LLValue) -> None:
        type_ctx = self.__core.context.type_ctx
        resolved = type_ctx.resolve_aliases(view.type_id)
        self.__safety.check_view_access(LLValue(resolved, view.ir_val))
