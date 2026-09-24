# pyright: reportUnknownMemberType=false
"""Stack slots, loads/stores, aggregate field addresses, and bulk memory ops."""

from __future__ import annotations

from llvmlite import ir

from compiler.analysis.ty import ty as Type
from compiler.analysis.ty.context import TypeCtx
from compiler.codegen.cfg import ir as CFG
from compiler.codegen.llvm.function.core import FunctionCore
from compiler.codegen.llvm.base.intrinsics import IntrinsicKind
from compiler.codegen.llvm.pointer.representation import PointerRepresentation
from compiler.codegen.llvm.base.value import LLValue
from compiler.codegen.llvm.base.types import LLTypeCtx


class MemoryEmitter:
    """Lower memory operations while delegating pointer layout to its owner."""

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
    def __builder(self) -> ir.IRBuilder:
        return self.__core.flow.builder

    def var_ptr(
        self,
        symbol_id: int,
        frame_word: LLValue | None = None,
        raw: bool = False,
    ) -> LLValue:
        alloca_ptr = self.__core.context.func.get_var_ptr(symbol_id)
        if raw or not self.__pointers.is_fat_type(alloca_ptr.type_id):
            return alloca_ptr
        data = LLValue(self.__type_ctx.alloc_pointer(self.__type_ctx.u8_id), alloca_ptr.ir_val)  # type: ignore
        word = LLValue(
            self.__type_ctx.u64_id,
            self.__pointers.literal_word() if frame_word is None else frame_word.ir_val,
        )
        zero = self.__u64(0)
        one = self.__u64(1)
        return self.__pointers.build_fat(data, word, zero, one, alloca_ptr.type_id)

    def alloca(self, type_id: int) -> LLValue:
        ptr_type_id = self.__type_ctx.alloc_pointer(type_id)
        ll_type = self.__ll_type_ctx.get_ll_type(type_id).ir_type
        entry_block = self.__core.context.func.entry_block
        if self.__builder.block is not entry_block:  # type: ignore
            raw_pointer = self.__core.flow.alloca_in_entry(ll_type)
        else:
            raw_pointer = self.__builder.alloca(ll_type)  # type: ignore
        return LLValue(ptr_type_id, raw_pointer)  # type: ignore

    def alloca_store(
        self,
        value: LLValue,
        frame_word: LLValue | None = None,
        raw: bool = False,
    ) -> LLValue:
        alloca_value = self.alloca(value.type_id)
        self.store(value, alloca_value)
        if raw or not self.__pointers.is_fat_type(alloca_value.type_id):
            return alloca_value
        if frame_word is None:
            raise ValueError("fat temporary alloca missing current frame lock")
        data = LLValue(self.__type_ctx.alloc_pointer(self.__type_ctx.u8_id), alloca_value.ir_val)  # type: ignore
        zero = self.__u64(0)
        one = self.__u64(1)
        return self.__pointers.build_fat(
            data, LLValue(self.__type_ctx.u64_id, frame_word.ir_val), zero, one, alloca_value.type_id
        )

    def load(self, ptr: LLValue) -> LLValue:
        ptr_type = self.__type_ctx[
            self.__type_ctx.resolve_aliases(ptr.type_id)
        ]
        assert isinstance(ptr_type, (Type.PointerType, Type.RefType))
        pointee_type_id = self.__type_ctx.resolve_aliases(ptr_type.pointee_type)
        if self.__ll_type_ctx.is_zst(pointee_type_id):
            return self.__core.ir.undef(pointee_type_id)
        address = self.__pointers.fat_addr(ptr, pointee_type_id).ir_val if self.__pointers.is_fat(ptr) else ptr.ir_val
        return LLValue(pointee_type_id, self.__load_memory_value(pointee_type_id, address))

    def store(self, value: LLValue, ptr: LLValue) -> None:
        if self.__ll_type_ctx.is_zst(value.type_id):
            return
        ptr_type = self.__type_ctx[ptr.type_id]
        if self.__pointers.is_fat(ptr):
            assert isinstance(ptr_type, (Type.PointerType, Type.RefType))
            address = self.__pointers.fat_addr(ptr, ptr_type.pointee_type).ir_val
        else:
            address = ptr.ir_val
        self.__store_memory_value(value, address)

    def gep(self, base: LLValue, indices: list[int]) -> LLValue:
        base_type = self.__type_ctx[self.__type_ctx.resolve_aliases(base.type_id)]
        if isinstance(base_type, (Type.PointerType, Type.RefType)):
            pointee_type_id = self.__type_ctx.resolve_aliases(base_type.pointee_type)
        else:
            pointee_type_id = self.__type_ctx.resolve_aliases(base.type_id)
        if self.__ll_type_ctx.is_zst(pointee_type_id):
            return self.__core.ir.undef(self.__type_ctx.alloc_pointer(pointee_type_id))

        source_ll = self.__ll_type_ctx.get_ll_type(pointee_type_id).ir_type
        for idx in indices[1:]:
            ty = self.__type_ctx[self.__type_ctx.resolve_aliases(pointee_type_id)]
            if isinstance(ty, Type.StructType):
                fields = self.__type_ctx.get_struct_fields(ty.type_id)
                pointee_type_id = fields[idx].type_id
            elif isinstance(ty, Type.TupleType):
                pointee_type_id = ty.element_types[idx]
            elif isinstance(ty, Type.ArrayType):
                pointee_type_id = ty.element_type
            elif isinstance(ty, Type.SliceType):
                if idx == 0:
                    pointee_type_id = self.__type_ctx.alloc_pointer(ty.element_type)
                elif idx == 1:
                    pointee_type_id = self.__type_ctx.alloc_pointer(self.__type_ctx.u8_id)
                else:
                    pointee_type_id = self.__type_ctx.u64_id
            elif isinstance(ty, Type.StrType):
                pointee_type_id = (
                    self.__type_ctx.alloc_pointer(self.__type_ctx.u8_id)
                    if idx == 0 else self.__type_ctx.u64_id
                )

        result_type_id = self.__type_ctx.alloc_pointer(pointee_type_id)
        if self.__ll_type_ctx.is_zst(pointee_type_id):
            return self.__core.ir.undef(result_type_id)
        if self.__pointers.is_fat(base):
            base_def = self.__type_ctx[base.type_id]
            element_ll = self.__ll_type_ctx.get_ll_type(base_def.pointee_type).ir_type  # type: ignore[union-attr]
            address = self.__pointers.fat_addr(base, base_def.pointee_type)  # type: ignore[union-attr]
            idx_values = [self.i32(index) for index in indices]
            source = element_ll if address.ir_val.type.is_opaque else None  # type: ignore
            field_addr = self.__builder.gep(
                address.ir_val, idx_values, inbounds=True, source_etype=source
            )  # type: ignore
            field_ptr = LLValue(self.__type_ctx.alloc_pointer(self.__type_ctx.u8_id), field_addr)  # type: ignore
            if isinstance(base_def, Type.PointerType):
                value = self.__builder.insert_value(base.ir_val, field_ptr.ir_val, CFG.FAT_DATA)  # type: ignore
                value = self.__builder.insert_value(
                    value,
                    self.__pointers.narrow_view_value(self.__u64(0).ir_val, base.ir_val, CFG.FAT_INDEX),
                    CFG.FAT_INDEX,
                )  # type: ignore
                value = self.__builder.insert_value(
                    value,
                    self.__pointers.narrow_view_value(self.__u64(1).ir_val, base.ir_val, CFG.FAT_SIZE),
                    CFG.FAT_SIZE,
                )  # type: ignore
                return LLValue(result_type_id, value)
            word = self.__pointers.extract_fat_field(base, CFG.FAT_WORD)
            return self.__pointers.build_fat(
                field_ptr, word, self.__u64(0), self.__u64(1), result_type_id
            )
        idx_values = [self.i32(index) for index in indices]
        source = source_ll if base.ir_val.type.is_opaque else None  # type: ignore
        value = self.__builder.gep(base.ir_val, idx_values, inbounds=True, source_etype=source)  # type: ignore
        return LLValue(result_type_id, value)

    def mem_copy(self, dest: LLValue, src: LLValue, count: LLValue) -> None:
        if self.__ll_type_ctx.is_zst(dest.type_id) or self.__ll_type_ctx.is_zst(src.type_id):
            return
        dest_addr = self.__pointers.fat_addr(dest, self.__pointee_type_id(dest.type_id))
        src_addr = self.__pointers.fat_addr(src, self.__pointee_type_id(src.type_id))
        self.__core.ir.call_intrinsic(
            IntrinsicKind.MemCopy,
            [
                LLValue(self.__type_ctx.alloc_pointer(self.__type_ctx.u8_id), dest_addr.ir_val),  # type: ignore
                LLValue(self.__type_ctx.alloc_pointer(self.__type_ctx.u8_id), src_addr.ir_val),  # type: ignore
                count,
            ],
        )

    def mem_set_pattern(self, dest: LLValue, value: LLValue, count: LLValue) -> None:
        if self.__ll_type_ctx.is_zst(dest.type_id):
            return
        dest_addr = self.__pointers.fat_addr(dest, self.__pointee_type_id(dest.type_id))
        pattern_type: ir.Type = value.ir_val.type  # type: ignore
        if self.__ll_type_ctx.get_type_size(value.type_id) == 1 \
                and isinstance(pattern_type, ir.IntType) and pattern_type.width in (1, 8):  # type: ignore
            byte_value = value.ir_val
            if pattern_type.width == 1:  # type: ignore
                byte_value = self.__builder.zext(byte_value, ir.IntType(8))  # type: ignore
            self.__core.ir.call_intrinsic(
                IntrinsicKind.MemSet,
                [dest_addr, LLValue(self.__type_ctx.u8_id, byte_value), count],  # type: ignore
            )
            return
        callee = self.__core.context.module.intrinsics.get_memset_pattern(
            dest_addr.ir_val.type, value.ir_val.type, count.ir_val.type  # type: ignore
        )
        self.__builder.call(
            callee,
            [dest_addr.ir_val, value.ir_val, count.ir_val, ir.Constant(ir.IntType(1), 0)],  # type: ignore
        )

    def __pointee_type_id(self, pointer_type_id: int) -> int:
        ty = self.__type_ctx[self.__type_ctx.resolve_aliases(pointer_type_id)]
        assert isinstance(ty, Type.PointerType), type(ty).__name__
        return ty.pointee_type

    def __is_bool_type(self, type_id: int) -> bool:
        resolved = self.__type_ctx.resolve_aliases(type_id)
        return isinstance(self.__type_ctx[resolved], Type.BoolType)

    def __load_memory_value(self, type_id: int, address: ir.Value) -> ir.Value:
        if self.__is_bool_type(type_id):
            if isinstance(address.type, ir.PointerType) and not address.type.is_opaque:  # type: ignore
                address = self.__core.ir.bitcast(address, self.__ll_type_ctx.ptr_type)
            loaded = self.__builder.load(address, typ=ir.IntType(8))  # type: ignore
            return self.__builder.trunc(loaded, ir.IntType(1))  # type: ignore
        return self.__builder.load(
            address, typ=self.__ll_type_ctx.get_ll_type(type_id).ir_type
        )  # type: ignore

    def __store_memory_value(self, value: LLValue, address: ir.Value) -> None:
        ir_value = value.ir_val
        if self.__is_bool_type(value.type_id):
            ir_value = self.__builder.zext(ir_value, ir.IntType(8))  # type: ignore
            if isinstance(address.type, ir.PointerType) and not address.type.is_opaque:  # type: ignore
                address = self.__core.ir.bitcast(address, self.__ll_type_ctx.ptr_type)
        self.__builder.store(ir_value, address)  # type: ignore

    def __u64(self, value: int) -> LLValue:
        return LLValue(self.__type_ctx.u64_id, ir.Constant(ir.IntType(64), value))  # type: ignore

    def i32(self, value: int) -> ir.Value:
        return ir.Constant(ir.IntType(32), value)  # type: ignore
