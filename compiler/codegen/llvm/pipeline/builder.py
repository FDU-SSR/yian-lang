# pyright: reportUnknownMemberType=false
"""Composition root for function-scoped LLVM emitters."""

from __future__ import annotations

from typing import cast

from llvmlite import ir

from compiler.analysis.ty import ty as Type
from compiler.analysis.ty.context import TypeCtx
from compiler.codegen.llvm.pointer.allocation import AllocationEmitter
from compiler.codegen.llvm.emitters.aggregates import AggregateEmitter
from compiler.codegen.llvm.emitters.calls import CallEmitter
from compiler.codegen.llvm.pointer.safety import FatSafety
from compiler.codegen.llvm.function.core import BuilderPosition, FunctionCore, FunctionFlow
from compiler.codegen.llvm.emitters.memory import MemoryEmitter
from compiler.codegen.llvm.base.module import LLFunction, LLModule
from compiler.codegen.llvm.pointer.operations import PointerOps
from compiler.codegen.llvm.pointer.representation import PointerRepresentation
from compiler.codegen.llvm.emitters.system import SystemEmitter
from compiler.codegen.llvm.base.types import LLTypeCtx
from compiler.codegen.llvm.base.value import LLValue
from compiler.codegen.llvm.emitters.values import ValueEmitter


class LLBuilder:
    """Assemble and expose the cooperating emitters for one LLVM function."""

    def __init__(
        self,
        func: LLFunction,
        module: LLModule,
        ll_type_ctx: LLTypeCtx,
        type_ctx: TypeCtx,
        raw_pointers: bool = False,
    ) -> None:
        self.__module = module
        self.__type_ctx = type_ctx
        self.__ll_type_ctx = ll_type_ctx
        self.__raw_pointers = raw_pointers
        self.__core = FunctionCore(func, module, ll_type_ctx, type_ctx, raw_pointers)
        self.__pointers = PointerRepresentation(self.__core)
        self.__safety = FatSafety(self.__core, self.__pointers)
        self.__allocation = AllocationEmitter(self.__core, self.__pointers, self.__safety)
        self.__memory = MemoryEmitter(self.__core, self.__pointers)
        self.__aggregates = AggregateEmitter(
            self.__core, self.__pointers, self.__memory, self.__safety
        )
        self.__pointer_ops = PointerOps(self.__core, self.__pointers)
        self.__values = ValueEmitter(self.__core, self.__pointers, self.__safety)
        self.__calls = CallEmitter(self.__core, self.__pointers, self.__safety)
        self.__system = SystemEmitter(self.__core, self.__pointers)

    @property
    def flow(self) -> FunctionFlow:
        return self.__core.flow

    @property
    def pointers(self) -> PointerRepresentation:
        return self.__pointers

    @property
    def safety(self) -> FatSafety:
        return self.__safety

    @property
    def allocation(self) -> AllocationEmitter:
        return self.__allocation

    @property
    def memory(self) -> MemoryEmitter:
        return self.__memory

    @property
    def aggregates(self) -> AggregateEmitter:
        return self.__aggregates

    @property
    def pointer_ops(self) -> PointerOps:
        return self.__pointer_ops

    @property
    def values(self) -> ValueEmitter:
        return self.__values

    @property
    def calls(self) -> CallEmitter:
        return self.__calls

    @property
    def system(self) -> SystemEmitter:
        return self.__system

    def i32(self, value: int) -> LLValue:
        return LLValue(self.__type_ctx.u32_id, ir.Constant(ir.IntType(32), value))  # type: ignore

    def i64(self, value: int) -> LLValue:
        return LLValue(self.__type_ctx.u64_id, ir.Constant(ir.IntType(64), value))  # type: ignore

    def undef(self, type_id: int) -> LLValue:
        return self.__core.ir.undef(type_id)

    def sizeof_const(self, type_id: int) -> LLValue:
        size = self.__ll_type_ctx.get_type_size(type_id)
        return LLValue(self.__type_ctx.u64_id, ir.Constant(ir.IntType(64), size))  # type: ignore

    def dangling_value(self, type_id: int) -> LLValue:
        """Create the non-dereferenceable sentinel value for a typed dangling pointer."""
        pointer_type = self.__type_ctx[
            self.__type_ctx.resolve_aliases(type_id)
        ]
        if not isinstance(pointer_type, Type.PointerType):
            raise ValueError(f"not a pointer type: {type(pointer_type).__name__}")
        if self.__ll_type_ctx.is_zst(type_id):
            return self.undef(type_id)

        pointee_type_id = pointer_type.pointee_type
        alignment = self.__ll_type_ctx.get_type_alignment(pointee_type_id)
        aligned_integer = ir.Constant(ir.IntType(64), alignment)  # type: ignore
        address = cast(
            ir.Value,
            self.flow.builder.inttoptr(aligned_integer, ir.PointerType()),  # type: ignore
        )
        if self.__raw_pointers:
            return LLValue(type_id, address)
        data = LLValue(self.__type_ctx.alloc_pointer(self.__type_ctx.u8_id), address)  # type: ignore
        word = LLValue(self.__type_ctx.u64_id, self.__pointers.literal_word())
        zero = self.i64(0)
        return self.__pointers.build_fat(data, word, zero, zero, type_id)

    def string_literal(self, value: str, type_id: int) -> LLValue:
        """Create a string value backed by an immutable module-global byte array."""
        encoded = value.encode("utf-8")
        global_var = self.__module.get_string_global(encoded)
        pointer = global_var.gep(
            [ir.Constant(ir.IntType(32), 0), ir.Constant(ir.IntType(32), 0)]  # type: ignore
        )
        length = ir.Constant(ir.IntType(64), len(encoded))  # type: ignore
        if self.__raw_pointers:
            return LLValue(type_id, ir.Constant.literal_struct([pointer, length]))  # type: ignore
        return LLValue(
            type_id,
            ir.Constant.literal_struct([pointer, self.__pointers.literal_word(), length]),  # type: ignore
        )

    def position_at(self, label: str, where: BuilderPosition = BuilderPosition.End) -> None:
        self.__core.flow.position_at(label, where)

    @property
    def current_block_label(self) -> str:
        return self.__core.flow.current_block_label
