# pyright: reportUnknownMemberType=false
"""Aggregate construction, extraction, and enum payload access."""

from __future__ import annotations

from llvmlite import ir

from compiler.analysis.ty import ty as Type
from compiler.analysis.ty.context import TypeCtx
from compiler.codegen.abi import lockmech as ABI
from compiler.codegen.llvm.function.core import FunctionCore
from compiler.codegen.llvm.pointer.safety import FatSafety
from compiler.codegen.llvm.emitters.memory import MemoryEmitter
from compiler.codegen.llvm.pointer.representation import PointerRepresentation
from compiler.codegen.llvm.base.types import LLTypeCtx
from compiler.codegen.llvm.base.value import LLValue


class AggregateEmitter:
    """Emit aggregate values and operate on enum payload storage."""

    def __init__(
        self, core: FunctionCore, pointers: PointerRepresentation, memory: MemoryEmitter,
        fat_safety: FatSafety,
    ) -> None:
        self.__core = core
        self.__pointers = pointers
        self.__memory = memory
        self.__fat_safety = fat_safety

    @property
    def __type_ctx(self) -> TypeCtx:
        return self.__core.context.type_ctx

    @property
    def __ll_type_ctx(self) -> LLTypeCtx:
        return self.__core.context.ll_type_ctx

    @property
    def __builder(self) -> ir.IRBuilder:
        return self.__core.flow.builder

    def extract_value(self, base: LLValue, index: int) -> LLValue:
        base_type = self.__type_ctx[base.type_id]
        if isinstance(base_type, Type.StructType):
            field_type = self.__type_ctx.get_struct_fields(base.type_id)[index].type_id
        elif isinstance(base_type, Type.TupleType):
            field_type = base_type.element_types[index]
        elif isinstance(base_type, Type.EnumType):
            field_type = self.__type_ctx.u32_id
        elif isinstance(base_type, (Type.PointerType, Type.RefType)):
            field_type = self.__type_ctx.alloc_pointer(self.__type_ctx.u8_id) \
                if index in (0, 1) else self.__type_ctx.u64_id
        elif isinstance(base_type, Type.SliceType):
            if index == 0:
                field_type = self.__type_ctx.alloc_pointer(base_type.element_type)
            elif index == 1:
                field_type = self.__type_ctx.alloc_pointer(self.__type_ctx.u8_id)
            elif index in (2, 3):
                field_type = self.__type_ctx.u64_id
            else:
                raise ValueError(f"slice has no field {index}")
        elif isinstance(base_type, Type.StrType):
            if index in (0, 1):
                field_type = self.__type_ctx.alloc_pointer(self.__type_ctx.u8_id)
            elif index in (2, 3):
                field_type = self.__type_ctx.u64_id
            else:
                raise ValueError(f"str has no field {index}")
        else:
            field_type = base.type_id

        if self.__ll_type_ctx.is_zst(field_type):
            return self.__core.ir.undef(field_type)
        if isinstance(base_type, (Type.SliceType, Type.StrType)) and index == 0:
            if self.__core.context.raw_pointers:
                return LLValue(field_type, self.__builder.extract_value(base.ir_val, 0))  # type: ignore
            return self.__pointers.slice_ptr_fat(base, field_type)
        if isinstance(base_type, (Type.SliceType, Type.StrType)) and index == 3:
            mapped = 1 if self.__core.context.raw_pointers else ABI.SLICE_SIZE
            return LLValue(field_type, self.__builder.extract_value(base.ir_val, mapped))  # type: ignore
        return LLValue(field_type, self.__builder.extract_value(base.ir_val, index))  # type: ignore

    def insert_value(self, aggregate: LLValue, value: LLValue, index: int) -> LLValue:
        return self.__core.ir.insert_value(aggregate, value, index)

    def build(self, type_id: int, field_values: list[LLValue]) -> LLValue:
        if self.__ll_type_ctx.is_zst(type_id):
            return self.__core.ir.undef(type_id)
        type_def = self.__type_ctx[type_id]
        if isinstance(type_def, (Type.SliceType, Type.StrType)):
            element_type = type_def.element_type if isinstance(type_def, Type.SliceType) \
                else self.__type_ctx.u8_id
            data = self.__pointers.fat_addr(field_values[0], element_type)
            if self.__core.context.raw_pointers:
                value = self.__core.ir.undef(type_id)
                value = self.insert_value(value, LLValue(self.__type_ctx.alloc_pointer(element_type), data.ir_val), 0)  # type: ignore
                return self.insert_value(value, field_values[1], 1)
            word = self.__pointers.extract_fat_field(field_values[0], ABI.FAT_WORD)
            return self.__pointers.build_fat(
                LLValue(self.__type_ctx.alloc_pointer(element_type), data.ir_val),  # type: ignore
                word,
                self.__u64(0),
                field_values[1],
                type_id,
            )

        all_constant = all(isinstance(field.ir_val, ir.Constant) for field in field_values)  # type: ignore
        has_zst = any(self.__ll_type_ctx.is_zst(field.type_id) for field in field_values)
        needs_fat_synthesis = any(
            self.__pointers.is_fat_type(field.type_id) and not self.__pointers.is_fat(field)
            for field in field_values
        )
        if not has_zst and not needs_fat_synthesis and all_constant:
            ll_type = self.__ll_type_ctx.get_ll_type(type_id).ir_type
            return LLValue(type_id, ir.Constant(ll_type, [field.ir_val for field in field_values]))  # type: ignore

        value = self.__core.ir.undef(type_id)
        for index, field in enumerate(field_values):
            if self.__ll_type_ctx.is_zst(field.type_id):
                continue
            if self.__pointers.is_fat_type(field.type_id) and not self.__pointers.is_fat(field):
                field = self.__fat_safety.synthesize_fat_value(field, self.__slice_size(field_values, index))
            value = self.insert_value(value, field, index)
        return value

    def array(self, type_id: int, elements: list[LLValue]) -> LLValue:
        return self.build(type_id, elements)

    def construct_enum_variant(
        self,
        enum_type_id: int,
        discriminant: int,
        payload_type: int | None,
        payload_fields: list[LLValue] | None,
    ) -> LLValue:
        if self.__ll_type_ctx.is_niche_enum(enum_type_id):
            if payload_type is None:
                ll_type = self.__ll_type_ctx.get_ll_type(enum_type_id).ir_type
                return LLValue(enum_type_id, self.__zero_const(ll_type))
            assert payload_fields is not None and len(payload_fields) == 1
            return LLValue(enum_type_id, self.__pointers.promote_fat(payload_fields[0]).ir_val)

        if payload_type is None or self.__type_ctx.is_zst(payload_type):
            value = self.__core.ir.undef(enum_type_id)
            discriminant_value = LLValue(self.__type_ctx.u32_id, self.__i32(discriminant))
            return self.__core.ir.insert_value(value, discriminant_value, 0)

        assert payload_fields is not None
        enum_ptr = self.__memory.alloca(enum_type_id)
        disc_ptr = self.__builder.gep(
            enum_ptr.ir_val, [self.__i32(0), self.__i32(0)], inbounds=True  # type: ignore
        )  # type: ignore
        self.__builder.store(self.__i32(discriminant), disc_ptr)  # type: ignore

        payload_arr_ptr = self.__builder.gep(
            enum_ptr.ir_val, [self.__i32(0), self.__i32(1)], inbounds=True  # type: ignore
        )  # type: ignore
        payload_ptr = self.__core.ir.bitcast(payload_arr_ptr, self.__ll_type_ctx.ptr_type)
        payload_ll = self.__ll_type_ctx.get_ll_type(payload_type).ir_type
        payload_type_fields = self.__type_ctx.get_struct_fields(payload_type)
        for index, field in enumerate(payload_fields):
            if index >= len(payload_type_fields):
                break
            if self.__ll_type_ctx.is_zst(payload_type_fields[index].type_id):
                continue
            field_ptr = self.__builder.gep(
                payload_ptr,
                [self.__i32(0), self.__i32(index)],
                inbounds=True,
                source_etype=payload_ll,
            )  # type: ignore
            self.__memory.store(field, LLValue(self.__type_ctx.alloc_pointer(payload_type_fields[index].type_id), field_ptr))  # type: ignore
        return self.__memory.load(enum_ptr)

    def unpack_enum_payload(
        self, matched: LLValue, payload_type_id: int, fields: list[tuple[int, int]],
    ) -> None:
        payload_type = self.__type_ctx[payload_type_id]
        assert isinstance(payload_type, Type.StructType)
        matched_type = self.__type_ctx[matched.type_id]
        enum_type_id = matched_type.pointee_type \
            if isinstance(matched_type, (Type.PointerType, Type.RefType)) else matched.type_id
        base_ptr = self.__address(matched, enum_type_id)

        if self.__ll_type_ctx.is_niche_enum(enum_type_id):
            payload_fields = self.__type_ctx.get_struct_fields(payload_type_id)
            for field_index, symbol_id in fields:
                if field_index == 0:
                    field_type_id = payload_fields[field_index].type_id
                    field_ptr = LLValue(self.__type_ctx.alloc_pointer(field_type_id), base_ptr.ir_val)  # type: ignore
                    field_value = self.__memory.load(field_ptr)
                    self.__memory.store(field_value, self.__core.context.func.get_var_ptr(symbol_id))
            return
        if self.__type_ctx.is_zst(payload_type_id):
            return

        payload_fields = self.__type_ctx.get_struct_fields(payload_type_id)
        enum_ll = self.__ll_type_ctx.get_ll_type(enum_type_id).ir_type
        payload_ll = self.__ll_type_ctx.get_ll_type(payload_type_id).ir_type
        payload = self.__builder.gep(
            base_ptr.ir_val, [self.__i32(0), self.__i32(1)],
            inbounds=True, source_etype=enum_ll,
        )  # type: ignore
        for field_index, symbol_id in fields:
            if field_index >= len(payload_fields):
                break
            field_type_id = payload_fields[field_index].type_id
            field_ptr = self.__builder.gep(
                payload,
                [self.__i32(0), self.__i32(field_index)],
                inbounds=True,
                source_etype=payload_ll,
            )  # type: ignore
            field_value = self.__memory.load(
                LLValue(self.__type_ctx.alloc_pointer(field_type_id), field_ptr)  # type: ignore
            )
            self.__memory.store(field_value, self.__core.context.func.get_var_ptr(symbol_id))

    def unpack_enum_payload_ref(
        self, matched: LLValue, payload_type_id: int, fields: list[tuple[int, int]],
    ) -> None:
        payload_type = self.__type_ctx[payload_type_id]
        assert isinstance(payload_type, Type.StructType)
        matched_type = self.__type_ctx[matched.type_id]
        enum_type_id = matched_type.pointee_type \
            if isinstance(matched_type, (Type.PointerType, Type.RefType)) else matched.type_id
        base_ptr = self.__address(matched, enum_type_id)
        payload_fields = self.__type_ctx.get_struct_fields(payload_type_id)

        if self.__ll_type_ctx.is_niche_enum(enum_type_id):
            for field_index, symbol_id in fields:
                if field_index == 0:
                    field_type_id = payload_fields[field_index].type_id
                    field_ptr = LLValue(self.__type_ctx.alloc_pointer(field_type_id), base_ptr.ir_val)  # type: ignore
                    self.__memory.store(
                        self.__pointers.promote_fat(field_ptr),
                        self.__core.context.func.get_var_ptr(symbol_id),
                    )
            return
        if self.__type_ctx.is_zst(payload_type_id):
            return

        enum_ll = self.__ll_type_ctx.get_ll_type(enum_type_id).ir_type
        payload_ll = self.__ll_type_ctx.get_ll_type(payload_type_id).ir_type
        payload = self.__builder.gep(
            base_ptr.ir_val, [self.__i32(0), self.__i32(1)],
            inbounds=True, source_etype=enum_ll,
        )  # type: ignore
        for field_index, symbol_id in fields:
            if field_index >= len(payload_fields):
                break
            field_type_id = payload_fields[field_index].type_id
            field_ptr = self.__builder.gep(
                payload,
                [self.__i32(0), self.__i32(field_index)],
                inbounds=True,
                source_etype=payload_ll,
            )  # type: ignore
            field_value = self.__pointers.promote_fat(
                LLValue(self.__type_ctx.alloc_pointer(field_type_id), field_ptr)  # type: ignore
            )
            self.__memory.store(field_value, self.__core.context.func.get_var_ptr(symbol_id))

    def is_all_zero(self, value: LLValue) -> LLValue:
        """Compare each scalar field of a niche payload with its zero value."""
        ll_type = value.ir_val.type  # type: ignore
        if isinstance(ll_type, ir.LiteralStructType):  # type: ignore
            result: ir.Value | None = None
            for index in range(len(ll_type.elements)):  # type: ignore
                field = self.__builder.extract_value(value.ir_val, index)  # type: ignore
                field_is_zero = self.__is_field_zero(field)
                result = field_is_zero if result is None \
                    else self.__builder.and_(result, field_is_zero)  # type: ignore
            assert result is not None
            return LLValue(self.__type_ctx.bool_id, result)
        return LLValue(self.__type_ctx.bool_id, self.__is_field_zero(value.ir_val))

    def __address(self, value: LLValue, pointee_type_id: int) -> LLValue:
        if self.__pointers.is_fat(value):
            return self.__pointers.fat_addr(value, pointee_type_id)
        return value

    def __slice_size(self, fields: list[LLValue], index: int) -> LLValue:
        for field in fields[index + 1:]:
            if field.type_id == self.__type_ctx.u64_id:
                return field
        return self.__u64(1)

    def __zero_const(self, ll_type: ir.Type) -> ir.Constant:
        if isinstance(ll_type, ir.LiteralStructType):
            return ir.Constant.literal_struct([self.__zero_const(field) for field in ll_type.elements])  # type: ignore
        if isinstance(ll_type, ir.PointerType):
            return ir.Constant(ll_type, None)  # type: ignore
        if isinstance(ll_type, ir.IntType):
            return ir.Constant(ll_type, 0)  # type: ignore
        if isinstance(ll_type, ir.types._BaseFloatType):  # type: ignore
            return ir.Constant(ll_type, 0.0)  # type: ignore
        raise ValueError(f"cannot build zero constant for {ll_type}")

    def __is_field_zero(self, value: ir.Value) -> ir.Value:
        if isinstance(value.type, ir.PointerType):  # type: ignore
            return self.__builder.icmp_signed("==", value, ir.Constant(value.type, None))  # type: ignore
        if isinstance(value.type, ir.IntType):  # type: ignore
            return self.__builder.icmp_signed("==", value, ir.Constant(value.type, 0))  # type: ignore
        if isinstance(value.type, ir.types._BaseFloatType):  # type: ignore
            return self.__builder.fcmp_ordered("==", value, ir.Constant(value.type, 0.0))  # type: ignore
        raise ValueError(f"unsupported zero-check field type: {value.type}")  # type: ignore

    def __i32(self, value: int) -> ir.Value:
        return ir.Constant(ir.IntType(32), value)  # type: ignore

    def __u64(self, value: int) -> LLValue:
        return LLValue(self.__type_ctx.u64_id, ir.Constant(ir.IntType(64), value))  # type: ignore
