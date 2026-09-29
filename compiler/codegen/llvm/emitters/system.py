# pyright: reportUnknownMemberType=false
"""Operating-system, math, argv, and process-termination lowering."""

from __future__ import annotations

from typing import cast

from llvmlite import ir

from compiler.analysis.ty.context import TypeCtx
from compiler.codegen.abi import lockmech as ABI
from compiler.codegen.llvm.function.core import FunctionCore
from compiler.codegen.llvm.base.intrinsics import IntrinsicKind
from compiler.codegen.llvm.pointer.representation import PointerRepresentation
from compiler.codegen.llvm.base.value import LLValue
from compiler.codegen.llvm.base.types import LLTypeCtx
from compiler.runtime_error import RuntimeErrorCode


class SystemEmitter:
    """Lower system operations and process arguments for one function."""

    def __init__(self, core: FunctionCore, pointers: PointerRepresentation) -> None:
        self.__core = core
        self.__pointers = pointers

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

    def sys_write(self, fd: LLValue, buf: LLValue) -> None:
        self.__core.ir.call_intrinsic(
            IntrinsicKind.Write,
            [fd, self.__extract_value_raw(buf, 0), self.__slice_len_field(buf)],
        )

    def sys_write_bytes(self, fd: LLValue, buf: LLValue) -> LLValue:
        return self.__core.ir.call_intrinsic(
            IntrinsicKind.Write,
            [fd, self.__extract_value_raw(buf, 0), self.__slice_len_field(buf)],
        )

    def sys_read(self, fd: LLValue, buf: LLValue) -> LLValue:
        buf_ptr = self.__extract_value_raw(buf, 0)
        buf_len = self.__slice_len_field(buf)
        bytes_read = self.__core.ir.call_intrinsic(IntrinsicKind.Read, [fd, buf_ptr, buf_len])
        value = self.__core.ir.undef(self.__type_ctx.str_id)
        value = self.__core.ir.insert_value(value, buf_ptr, 0)
        if self.__raw_pointers:
            value = self.__core.ir.insert_value(value, bytes_read, 1)
        else:
            value = self.__core.ir.insert_value(value, self.__extract_value_raw(buf, 1), 1)
            value = self.__core.ir.insert_value(value, bytes_read, 2)
        return value

    def sys_read_bytes(self, fd: LLValue, buf: LLValue) -> LLValue:
        return self.__core.ir.call_intrinsic(
            IntrinsicKind.Read,
            [fd, self.__extract_value_raw(buf, 0), self.__slice_len_field(buf)],
        )

    def open(self, path: LLValue, flags: LLValue) -> LLValue:
        mode = LLValue(self.__type_ctx.u32_id, ir.Constant(ir.IntType(32), 420))  # type: ignore
        return self.__core.ir.call_intrinsic(
            IntrinsicKind.Open,
            [self.__extract_value_raw(path, 0), flags, mode],
        )

    def close(self, fd: LLValue) -> LLValue:
        return self.__core.ir.call_intrinsic(IntrinsicKind.Close, [fd])

    def sqrt(self, value: LLValue) -> LLValue:
        return self.__core.ir.call_intrinsic(IntrinsicKind.Sqrt, [value])

    def sin(self, value: LLValue) -> LLValue:
        return self.__core.ir.call_intrinsic(IntrinsicKind.Sin, [value])

    def cos(self, value: LLValue) -> LLValue:
        return self.__core.ir.call_intrinsic(IntrinsicKind.Cos, [value])

    def arg_count(self) -> LLValue:
        loaded = self.__builder.load(self.__core.context.module.argc_global)  # type: ignore
        extended = cast(ir.Value, self.__builder.zext(loaded, ir.IntType(64)))  # type: ignore
        return LLValue(self.__type_ctx.u64_id, extended)

    def arg_bytes(self, index: LLValue) -> LLValue:
        argc = self.__builder.load(self.__core.context.module.argc_global)  # type: ignore
        argc_u64 = self.__builder.zext(argc, ir.IntType(64))  # type: ignore
        in_range = self.__builder.icmp_unsigned("<", index.ir_val, argc_u64)  # type: ignore
        self.__core.flow.emit_check(
            LLValue(self.__type_ctx.bool_id, in_range), RuntimeErrorCode.S001, "argv-index"
        )

        argv = self.__builder.load(
            self.__core.context.module.argv_global, typ=self.__ll_type_ctx.ptr_type
        )  # type: ignore
        slot = self.__builder.gep(
            argv, [index.ir_val], inbounds=False, source_etype=self.__ll_type_ctx.ptr_type
        )  # type: ignore
        ptr_ir = self.__builder.load(slot, typ=self.__ll_type_ctx.ptr_type)  # type: ignore
        ptr_type_id = self.__type_ctx.alloc_pointer(self.__type_ctx.u8_id)
        ptr = LLValue(ptr_type_id, ptr_ir)
        nonnull = self.__builder.icmp_unsigned(
            "!=", ptr_ir, cast(ir.Value, ir.Constant(ptr_ir.type, None))  # type: ignore
        )  # type: ignore
        self.__core.flow.emit_check(
            LLValue(self.__type_ctx.bool_id, nonnull), RuntimeErrorCode.S002, "argv-null"
        )

        length = self.__core.ir.call_intrinsic(IntrinsicKind.StrLen, [ptr])
        slice_type_id = self.__type_ctx.alloc_slice(self.__type_ctx.u8_id)
        if self.__raw_pointers:
            value = self.__core.ir.undef(slice_type_id)
            value = self.__core.ir.insert_value(value, ptr, 0)
            return self.__core.ir.insert_value(value, length, 1)

        word = LLValue(self.__type_ctx.u64_id, self.__pointers.env_word())
        zero = LLValue(self.__type_ctx.u64_id, ir.Constant(ir.IntType(64), 0))  # type: ignore
        return self.__pointers.build_fat(ptr, word, zero, length, slice_type_id)

    def process_exit(self, code: LLValue) -> None:
        self.__core.ir.call_intrinsic(IntrinsicKind.ImmediateExit, [code])
        self.__builder.unreachable()

    def panic(self, message: LLValue) -> None:
        self.__builder.call(
            self.__core.context.module.get_panic(),
            [self.__extract_value_raw(message, 0).ir_val, self.__slice_len_field(message).ir_val],
        )
        self.__builder.unreachable()

    def __slice_len_field(self, base: LLValue) -> LLValue:
        index = ABI.SLICE_SIZE if not self.__raw_pointers else 1
        return self.__extract_value_raw(base, index)

    def __extract_value_raw(self, base: LLValue, index: int) -> LLValue:
        return LLValue(base.type_id, self.__builder.extract_value(base.ir_val, index))  # type: ignore
