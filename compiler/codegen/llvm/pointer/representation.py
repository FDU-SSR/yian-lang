# pyright: reportUnknownMemberType=false
"""Raw and fat pointer value layouts and address calculations."""

from __future__ import annotations

from llvmlite import ir

from compiler.analysis.ty import ty as Type
from compiler.analysis.ty.context import TypeCtx
from compiler.codegen.abi import lockmech as ABI
from compiler.codegen.llvm.function.core import FunctionCore
from compiler.codegen.llvm.base.value import LLValue


class PointerRepresentation:
    """Construct and inspect pointer-family values without emitting safety checks."""

    def __init__(self, core: FunctionCore) -> None:
        self.__core = core

    @property
    def __type_ctx(self) -> TypeCtx:
        return self.__core.context.type_ctx

    @property
    def __ll_type_ctx(self):
        return self.__core.context.ll_type_ctx

    @property
    def __raw_pointers(self) -> bool:
        return self.__core.context.raw_pointers

    @property
    def __builder(self) -> ir.IRBuilder:
        return self.__core.flow.builder

    def is_fat(self, value: LLValue) -> bool:
        if self.__raw_pointers:
            return False
        ty = self.__type_ctx[self.__type_ctx.resolve_aliases(value.type_id)]
        return (
            isinstance(ty, (Type.PointerType, Type.SliceType, Type.StrType, Type.RefType))
            and isinstance(value.ir_val.type, ir.LiteralStructType)  # type: ignore
            and len(value.ir_val.type.elements) > 0  # type: ignore
        )

    def is_fat_type(self, type_id: int) -> bool:
        if self.__raw_pointers:
            return False
        ty = self.__type_ctx[self.__type_ctx.resolve_aliases(type_id)]
        if isinstance(ty, Type.PointerType):
            return not self.__type_ctx.is_zst(ty.pointee_type)
        return isinstance(ty, (Type.SliceType, Type.StrType, Type.RefType))

    def extract_fat_field(self, value: LLValue, index: int) -> LLValue:
        ir_value = self.__builder.extract_value(value.ir_val, index)  # type: ignore
        if index == ABI.FAT_DATA:
            field_type = self.__type_ctx.alloc_pointer(self.__type_ctx.u8_id)
        else:
            field_type = self.__type_ctx.u64_id
            if isinstance(ir_value.type, ir.IntType) and ir_value.type.width < 64:  # type: ignore
                ir_value = self.__builder.zext(ir_value, ir.IntType(64))  # type: ignore
        return LLValue(field_type, ir_value)  # type: ignore

    def insert_field_value(self, aggregate: LLValue, value: LLValue, index: int) -> LLValue:
        dest: ir.Type = aggregate.ir_val.type.elements[index]  # type: ignore
        ir_value: ir.Value = value.ir_val
        if (
            isinstance(dest, ir.IntType) and isinstance(ir_value.type, ir.IntType)  # type: ignore
            and ir_value.type.width > dest.width  # type: ignore
        ):
            ir_value = self.__builder.trunc(ir_value, dest)  # type: ignore
        result = self.__builder.insert_value(aggregate.ir_val, ir_value, index)  # type: ignore
        return LLValue(aggregate.type_id, result)

    def narrow_view_value(self, value: ir.Value, aggregate: ir.Value, index: int) -> ir.Value:
        dest: ir.Type = aggregate.type.elements[index]  # type: ignore
        if (
            isinstance(dest, ir.IntType) and isinstance(value.type, ir.IntType)  # type: ignore
            and value.type.width > dest.width  # type: ignore
        ):
            return self.__builder.trunc(value, dest)  # type: ignore
        return value

    @staticmethod
    def check_static_view_count(count: int) -> None:
        if count > ABI.MAX_VIEW_COUNT:
            raise ValueError(
                f"array length {count} exceeds the 32-bit view element limit {ABI.MAX_VIEW_COUNT}"
            )

    def build_fat(
        self,
        data: LLValue,
        word: LLValue,
        index: LLValue,
        size: LLValue,
        type_id: int,
    ) -> LLValue:
        ty = self.__type_ctx[self.__type_ctx.resolve_aliases(type_id)]
        value = self.__core.ir.undef(type_id)
        value = self.__core.ir.insert_value(value, data, ABI.FAT_DATA)
        value = self.__core.ir.insert_value(value, word, ABI.FAT_WORD)
        if isinstance(ty, Type.PointerType):
            value = self.insert_field_value(value, index, ABI.FAT_INDEX)
            value = self.insert_field_value(value, size, ABI.FAT_SIZE)
        elif isinstance(ty, (Type.SliceType, Type.StrType)):
            value = self.__core.ir.insert_value(value, size, ABI.SLICE_SIZE)
        elif not isinstance(ty, Type.RefType):
            raise ValueError(f"not a pointer-family type: {type(ty).__name__}")
        return value

    def literal_word(self) -> ir.Value:
        return ir.Constant(ir.IntType(64), ABI.LITERAL_WORD)  # type: ignore

    def env_word(self) -> ir.Value:
        return ir.Constant(ir.IntType(64), ABI.ENV_WORD)  # type: ignore

    def fat_data(self, value: LLValue) -> LLValue:
        if self.is_fat(value):
            return self.extract_fat_field(value, ABI.FAT_DATA)
        return value

    def promote_fat(self, value: LLValue) -> LLValue:
        if self.is_fat_type(value.type_id) and not self.is_fat(value):
            data = LLValue(self.__type_ctx.alloc_pointer(self.__type_ctx.u8_id), value.ir_val)  # type: ignore
            word = LLValue(self.__type_ctx.u64_id, self.literal_word())  # type: ignore
            zero = LLValue(self.__type_ctx.u64_id, ir.Constant(ir.IntType(64), 0))  # type: ignore
            one = LLValue(self.__type_ctx.u64_id, ir.Constant(ir.IntType(64), 1))  # type: ignore
            return self.build_fat(data, word, zero, one, value.type_id)
        return value

    def fat_addr(self, value: LLValue, pointee_type_id: int) -> LLValue:
        if self.is_fat(value):
            data = self.extract_fat_field(value, ABI.FAT_DATA).ir_val
            pointee_ll = self.__ll_type_ctx.get_ll_type(pointee_type_id).ir_type
            ty = self.__type_ctx[self.__type_ctx.resolve_aliases(value.type_id)]
            if isinstance(ty, Type.RefType):
                addr = data
            else:
                index = self.extract_fat_field(value, ABI.FAT_INDEX).ir_val
                addr = self.__builder.gep(data, [index], inbounds=False, source_etype=pointee_ll)  # type: ignore
        else:
            addr = value.ir_val
        return LLValue(self.__type_ctx.alloc_pointer(pointee_type_id), addr)  # type: ignore

    def fat_addr_i128(self, value: LLValue, pointee_type_id: int) -> ir.Value:
        data = self.extract_fat_field(value, ABI.FAT_DATA).ir_val
        index = self.extract_fat_field(value, ABI.FAT_INDEX).ir_val
        base128 = self.__builder.zext(self.__builder.ptrtoint(data, ir.IntType(64)), ir.IntType(128))  # type: ignore
        idx128 = self.__builder.zext(index, ir.IntType(128))  # type: ignore
        scaled = self.__builder.mul(
            idx128, ir.Constant(ir.IntType(128), self.__ll_type_ctx.get_type_size(pointee_type_id))  # type: ignore
        )
        return self.__builder.add(base128, scaled)  # type: ignore

    def cmp_fat_operands(self, value: LLValue) -> tuple[ir.Value, ir.Value]:
        if self.is_fat(value):
            return (
                self.extract_fat_field(value, ABI.FAT_DATA).ir_val,
                self.extract_fat_field(value, ABI.FAT_INDEX).ir_val,
            )
        ty = self.__type_ctx[value.type_id]
        if isinstance(ty, Type.PointerType):
            return value.ir_val, ir.Constant(ir.IntType(64), 0)  # type: ignore
        raise ValueError(f"Unsupported pointer comparison operand: {type(ty).__name__}")

    def slice_ptr_fat(self, base: LLValue, ptr_type_id: int) -> LLValue:
        data = LLValue(
            self.__type_ctx.alloc_pointer(self.__type_ctx.u8_id),
            self.__builder.extract_value(base.ir_val, ABI.SLICE_DATA),  # type: ignore
        )
        word = LLValue(
            self.__type_ctx.u64_id,
            self.__builder.extract_value(base.ir_val, ABI.SLICE_WORD),  # type: ignore
        )
        size = LLValue(
            self.__type_ctx.u64_id,
            self.__builder.extract_value(base.ir_val, ABI.SLICE_SIZE),  # type: ignore
        )
        zero = LLValue(self.__type_ctx.u64_id, ir.Constant(ir.IntType(64), 0))  # type: ignore
        return self.build_fat(data, word, zero, size, ptr_type_id)
