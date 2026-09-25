# pyright: reportUnknownMemberType=false
"""Allocation and release lowering shared by raw and fat pointer modes."""

from __future__ import annotations

from typing import cast

from llvmlite import ir

from compiler.analysis.ty import ty as Type
from compiler.analysis.ty.context import TypeCtx
from compiler.codegen.abi import lockmech as ABI
from compiler.codegen.llvm.function.core import FunctionCore
from compiler.codegen.llvm.pointer.safety import FatSafety
from compiler.codegen.llvm.base.intrinsics import IntrinsicKind
from compiler.codegen.llvm.pointer.representation import PointerRepresentation
from compiler.codegen.llvm.base.types import LLTypeCtx
from compiler.codegen.llvm.base.value import LLValue
from compiler.runtime_error import RuntimeErrorCode
from compiler.runtime_lib import class_index_for_payload


class AllocationEmitter:
    """Select and emit allocation behavior for the active pointer mode."""

    def __init__(
        self, core: FunctionCore, pointers: PointerRepresentation, fat_safety: FatSafety,
    ) -> None:
        self.__core = core
        self.__pointers = pointers
        self.__fat_safety = fat_safety

    @property
    def __type_ctx(self) -> TypeCtx:
        return self.__core.context.type_ctx

    @property
    def __ll_type_ctx(self) -> LLTypeCtx:
        return self.__core.context.ll_type_ctx

    @property
    def __raw_pointers(self) -> bool:
        return self.__core.context.raw_pointers

    @property
    def __builder(self) -> ir.IRBuilder:
        return self.__core.flow.builder

    def malloc(self, type_id: int, size: LLValue, key: LLValue | None) -> LLValue:
        ptr_type_id = self.__type_ctx.alloc_pointer(type_id)
        if self.__type_ctx.is_zst(type_id):
            return self.__core.ir.undef(ptr_type_id)

        payload = self.__allocation_payload(type_id, size)
        if self.__raw_pointers:
            raw = self.__core.ir.call_intrinsic(IntrinsicKind.Malloc, [payload])
            nonnull = self.__builder.icmp_signed("!=", raw.ir_val, ir.Constant(raw.ir_val.type, None))  # type: ignore
            self.__core.flow.emit_check(
                LLValue(self.__type_ctx.bool_id, nonnull), RuntimeErrorCode.R002, "malloc-null"
            )
            typed = self.__core.ir.bitcast(
                raw.ir_val, self.__ll_type_ctx.get_ll_type(ptr_type_id).ir_type
            )
            return LLValue(ptr_type_id, typed)

        class_index = self.__constant_class_index(size.ir_val, self.__ll_type_ctx.get_type_size(type_id))
        return self.__fat_safety.allocate_fat(type_id, size, payload, class_index)

    def realloc(self, type_id: int, ptr: LLValue, size: LLValue) -> LLValue:
        ptr_type_id = self.__type_ctx.alloc_pointer(type_id)
        if self.__type_ctx.is_zst(type_id):
            return self.__core.ir.undef(ptr_type_id)

        payload = self.__allocation_payload(type_id, size)
        if self.__raw_pointers:
            raw_ptr_type_id = self.__type_ctx.alloc_pointer(self.__type_ctx.u8_id)
            raw_ptr = LLValue(
                raw_ptr_type_id,
                self.__core.ir.bitcast(
                    ptr.ir_val, self.__ll_type_ctx.get_ll_type(raw_ptr_type_id).ir_type
                ),
            )
            resized = self.__core.ir.call_intrinsic(IntrinsicKind.Realloc, [raw_ptr, payload])
            nonnull = self.__builder.icmp_signed("!=", resized.ir_val, ir.Constant(resized.ir_val.type, None))  # type: ignore
            self.__core.flow.emit_check(
                LLValue(self.__type_ctx.bool_id, nonnull), RuntimeErrorCode.R002, "realloc-null"
            )
            typed = self.__core.ir.bitcast(
                resized.ir_val, self.__ll_type_ctx.get_ll_type(ptr_type_id).ir_type
            )
            return LLValue(ptr_type_id, typed)

        if not self.__pointers.is_fat(ptr):
            raise ValueError("fat-pointer realloc requires a fat root pointer")
        old_count = self.__pointers.extract_fat_field(ptr, ABI.FAT_SIZE).ir_val
        use_new_count = self.__builder.icmp_unsigned("<", old_count, size.ir_val)  # type: ignore
        copy_count = self.__builder.select(use_new_count, old_count, size.ir_val)  # type: ignore
        elem_size = self.__ll_type_ctx.get_type_size(type_id)
        copy_bytes = cast(
            ir.Value,
            self.__builder.mul(copy_count, ir.Constant(ir.IntType(64), elem_size)),  # type: ignore
        )
        class_index = self.__constant_class_index(size.ir_val, elem_size)
        replacement = self.__fat_safety.allocate_fat(type_id, size, payload, class_index)
        dest = self.__pointers.fat_addr(replacement, type_id)
        src = self.__pointers.fat_addr(ptr, type_id)
        self.__core.ir.copy_bytes(dest.ir_val, src.ir_val, copy_bytes)
        self.__fat_safety.release_fat(ptr)
        return replacement

    def delete(self, ptr: LLValue) -> None:
        if self.__type_ctx.is_zst(ptr.type_id):
            return
        if self.__pointers.is_fat(ptr):
            self.__fat_safety.release_fat(ptr)
            return

        pointer_type_id = self.__type_ctx.alloc_pointer(self.__type_ctx.u8_id)
        if isinstance(self.__type_ctx[ptr.type_id], Type.SliceType):
            data = self.__builder.extract_value(ptr.ir_val, 0)  # type: ignore
            self.__core.ir.call_intrinsic(IntrinsicKind.Free, [LLValue(pointer_type_id, data)])
            return
        self.__core.ir.call_intrinsic(IntrinsicKind.Free, [LLValue(pointer_type_id, ptr.ir_val)])

    def __allocation_payload(self, type_id: int, size: LLValue) -> LLValue:
        """Compute checked, nonzero allocation bytes for the selected mode."""
        elem_size = self.__ll_type_ctx.get_type_size(type_id)
        if not self.__raw_pointers:
            self.__fat_safety.check_view_count(size, "vcap")

        i128: ir.IntType = ir.IntType(128)  # type: ignore
        if not self.__raw_pointers and elem_size < (1 << 32):
            payload_ir = self.__builder.mul(
                size.ir_val, ir.Constant(ir.IntType(64), elem_size)  # type: ignore
            )
        else:
            size128 = self.__builder.zext(size.ir_val, i128)  # type: ignore
            payload128 = self.__builder.mul(size128, ir.Constant(i128, elem_size))  # type: ignore
            total128 = payload128
            if not self.__raw_pointers:
                total128 = self.__builder.add(
                    total128, ir.Constant(i128, ABI.BlockHeader.BYTES)  # type: ignore
                )
            fits = self.__builder.icmp_unsigned("<", total128, ir.Constant(i128, 1 << 64))  # type: ignore
            self.__core.flow.emit_check(
                LLValue(self.__type_ctx.bool_id, fits), RuntimeErrorCode.R001, "mof"
            )
            payload_ir = self.__builder.trunc(payload128, ir.IntType(64))  # type: ignore
        zero = ir.Constant(ir.IntType(64), 0)  # type: ignore
        one = ir.Constant(ir.IntType(64), 1)  # type: ignore
        nonzero = self.__builder.icmp_unsigned("!=", payload_ir, zero)  # type: ignore
        normalized = self.__builder.select(nonzero, payload_ir, one)  # type: ignore
        return LLValue(self.__type_ctx.u64_id, normalized)  # type: ignore

    @staticmethod
    def __constant_class_index(count_ir: ir.Value, elem_size: int) -> int | None:
        if not isinstance(count_ir, ir.Constant):  # type: ignore
            return None
        count = count_ir.constant  # type: ignore
        if not isinstance(count, int) or count < 0:
            return None
        payload = count * elem_size
        if payload == 0:
            payload = 1
        return class_index_for_payload(payload)
