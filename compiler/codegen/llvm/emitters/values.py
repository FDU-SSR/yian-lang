# pyright: reportUnknownMemberType=false
"""Scalar arithmetic, comparisons, unary operators, and casts."""

from __future__ import annotations

from typing import cast

from llvmlite import ir

from compiler.analysis.ty import ty as Type
from compiler.analysis.ty.context import TypeCtx
from compiler.codegen.cfg import ir as CFG
from compiler.codegen.llvm.function.core import FunctionCore
from compiler.codegen.llvm.pointer.safety import FatSafety
from compiler.codegen.llvm.pointer.representation import PointerRepresentation
from compiler.codegen.llvm.base.types import LLTypeCtx
from compiler.codegen.llvm.base.value import LLValue
from compiler.frontend.parse.operator import BinaryOperator, UnaryOperator


class ValueEmitter:
    """Emit scalar operations while using shared pointer representation rules."""

    ARITH_OPS = {
        BinaryOperator.Add: "add", BinaryOperator.Sub: "sub",
        BinaryOperator.Mul: "mul", BinaryOperator.Div: "sdiv",
        BinaryOperator.Mod: "srem", BinaryOperator.BitAnd: "and_",
        BinaryOperator.BitOr: "or_", BinaryOperator.BitXor: "xor",
        BinaryOperator.Shl: "shl", BinaryOperator.Shr: "ashr",
    }

    FLOAT_ARITH_OPS = {
        BinaryOperator.Add: "fadd", BinaryOperator.Sub: "fsub",
        BinaryOperator.Mul: "fmul", BinaryOperator.Div: "fdiv",
        BinaryOperator.Mod: "frem",
    }

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
    def __builder(self) -> ir.IRBuilder:
        return self.__core.flow.builder

    def binary(self, op: BinaryOperator, lhs: LLValue, rhs: LLValue) -> LLValue:
        if op.is_comparison():
            value = self.__cmp_impl(op, lhs.ir_val, rhs.ir_val, lhs.type_id)
            return LLValue(self.__type_ctx.bool_id, value)
        value = self.__arith_impl(op, lhs.ir_val, rhs.ir_val, lhs.type_id)
        return LLValue(lhs.type_id, value)

    def unary(self, op: UnaryOperator, operand: LLValue) -> LLValue:
        if op == UnaryOperator.Neg:
            if isinstance(operand.ir_val.type, ir.types._BaseFloatType):  # type: ignore
                value = self.__builder.fsub(
                    ir.Constant(operand.ir_val.type, 0.0), operand.ir_val  # type: ignore
                )  # type: ignore
            else:
                value = self.__builder.neg(operand.ir_val)  # type: ignore
        elif op == UnaryOperator.LogicalNot:
            value = self.__builder.not_(operand.ir_val)  # type: ignore
        elif op == UnaryOperator.BitNot:
            value = self.__builder.xor(
                operand.ir_val, ir.Constant(operand.ir_val.type, -1)  # type: ignore
            )  # type: ignore
        else:
            raise ValueError(f"Unsupported unary: {op}")
        return LLValue(operand.type_id, value)  # type: ignore

    def cast(self, value: LLValue, to_type: int, raw: bool = False) -> LLValue:
        value_type_id = self.__type_ctx.resolve_aliases(value.type_id)
        to_type = self.__type_ctx.resolve_aliases(to_type)
        src = self.__type_ctx[value_type_id]
        dst = self.__type_ctx[to_type]
        dest_ll_type = self.__ll_type_ctx.get_ll_type(to_type).ir_type

        if self.__ll_type_ctx.is_zst(value_type_id) or self.__ll_type_ctx.is_zst(to_type):
            empty_array: Type.ArrayType | None = None
            if isinstance(src, Type.ArrayType):
                empty_array = src
            elif isinstance(src, (Type.PointerType, Type.RefType)):
                pointee = self.__type_ctx[src.pointee_type]
                if isinstance(pointee, Type.ArrayType):
                    empty_array = pointee
            is_empty = False
            if empty_array is not None:
                length_ty = self.__type_ctx[empty_array.length]
                is_empty = isinstance(length_ty, Type.LiteralValueType) and length_ty.value == 0
            if is_empty and isinstance(dst, Type.PointerType) \
                    and not self.__type_ctx.is_zst(dst.pointee_type):
                slot = self.__builder.gep(
                    self.__core.context.module.get_lock_table(),
                    [ir.Constant(ir.IntType(64), 0), ir.Constant(ir.IntType(64), CFG.LITERAL_LOCK_INDEX)],  # type: ignore
                    inbounds=True,
                )  # type: ignore
                if self.__core.context.raw_pointers or raw:
                    byte_slot = cast(
                        ir.Value,
                        self.__builder.bitcast(slot, ir.PointerType(ir.IntType(8))),  # type: ignore
                    )
                    result = self.__core.ir.bitcast(byte_slot, dest_ll_type)
                else:
                    data = LLValue(self.__type_ctx.alloc_pointer(self.__type_ctx.u8_id), slot)  # type: ignore
                    word = LLValue(self.__type_ctx.u64_id, self.__pointers.literal_word())
                    zero = self.__u64(0)
                    result = self.__pointers.build_fat(data, word, zero, zero, to_type).ir_val
            else:
                result = ir.Constant(dest_ll_type, ir.Undefined)  # type: ignore
        elif isinstance(src, (Type.IntType, Type.CharType, Type.BoolType)) and isinstance(
            dst, (Type.IntType, Type.CharType, Type.BoolType)
        ):
            src_width = self.__ll_type_ctx.get_ll_type(value.type_id).ir_type.width  # type: ignore
            dest_width = dest_ll_type.width  # type: ignore
            if dest_width > src_width:
                if isinstance(src, Type.CharType) or (isinstance(src, Type.IntType) and not src.signed):
                    result = self.__builder.zext(value.ir_val, dest_ll_type)  # type: ignore
                else:
                    result = self.__builder.sext(value.ir_val, dest_ll_type)  # type: ignore
            elif dest_width < src_width:
                result = self.__builder.trunc(value.ir_val, dest_ll_type)  # type: ignore
            else:
                result = value.ir_val
        elif isinstance(src, Type.IntType) and isinstance(dst, Type.FloatType):
            result = (self.__builder.sitofp(value.ir_val, dest_ll_type) if src.signed
                      else self.__builder.uitofp(value.ir_val, dest_ll_type))  # type: ignore
        elif isinstance(src, Type.FloatType) and isinstance(dst, Type.IntType):
            if dst.signed:
                result = self.__builder.fptosi(value.ir_val, dest_ll_type)  # type: ignore
            else:
                zero_f = ir.Constant(value.ir_val.type, 0.0)  # type: ignore
                pos = self.__builder.fcmp_ordered(">=", value.ir_val, zero_f)  # type: ignore
                raw_value = self.__builder.fptoui(value.ir_val, dest_ll_type)  # type: ignore
                result = self.__builder.select(pos, raw_value, ir.Constant(dest_ll_type, 0))  # type: ignore
        elif isinstance(src, Type.FloatType) and isinstance(dst, Type.FloatType):
            result = (self.__builder.fpext(value.ir_val, dest_ll_type) if src.size < dst.size
                      else self.__builder.fptrunc(value.ir_val, dest_ll_type))  # type: ignore
        elif isinstance(src, Type.PointerType) and isinstance(dst, Type.PointerType):
            result = self.__cast_pointer(value, src, dst, to_type, dest_ll_type, raw)
        elif isinstance(src, Type.RefType) and isinstance(dst, Type.RefType):
            result = value.ir_val
        elif isinstance(src, Type.PointerType) and isinstance(dst, Type.RefType):
            result = self.__pointer_to_ref(value, src, dst, to_type, dest_ll_type)
        elif isinstance(src, Type.RefType) and isinstance(dst, Type.PointerType):
            result = self.__ref_to_pointer(value, src, dst, to_type, dest_ll_type)
        elif isinstance(src, Type.PointerType) and isinstance(dst, Type.SliceType):
            result = self.__pointer_to_slice(value, src, to_type)
        elif isinstance(src, Type.SliceType) and isinstance(dst, Type.RefType):
            result = self.__slice_to_ref(value, to_type)
        else:
            raise ValueError(f"Unsupported cast: {type(src).__name__} → {type(dst).__name__}")
        return LLValue(to_type, result)  # type: ignore

    def __cast_pointer(
        self, value: LLValue, src: Type.PointerType, dst: Type.PointerType,
        to_type: int, dest_ll_type: ir.Type, raw: bool,
    ) -> ir.Value:
        if self.__ll_type_ctx.is_zst(dst.pointee_type):
            return ir.Constant(dest_ll_type, ir.Undefined)  # type: ignore
        if raw:
            pointee_ll = self.__ll_type_ctx.get_ll_type(dst.pointee_type).ir_type
            return self.__core.ir.bitcast(value.ir_val, pointee_ll.as_pointer())  # type: ignore
        if self.__pointers.is_fat(value):
            src_pointee = self.__type_ctx[src.pointee_type]
            if isinstance(src_pointee, Type.ArrayType) and src_pointee.element_type == dst.pointee_type:
                eff = self.__pointers.fat_addr(value, src.pointee_type)
                data = LLValue(self.__type_ctx.alloc_pointer(self.__type_ctx.u8_id), eff.ir_val)  # type: ignore
                word = self.__pointers.extract_fat_field(value, CFG.FAT_WORD)
                zero = self.__u64(0)
                self.__check_static_view_count(src_pointee)
                size = self.__u64(self.__literal_array_length(src_pointee))
                return self.__pointers.build_fat(data, word, zero, size, to_type).ir_val
            return value.ir_val
        src_pointee = self.__type_ctx[src.pointee_type]
        if not self.__core.context.raw_pointers and isinstance(src_pointee, Type.ArrayType) \
                and src_pointee.element_type == dst.pointee_type:
            data = LLValue(self.__type_ctx.alloc_pointer(self.__type_ctx.u8_id), value.ir_val)  # type: ignore
            word = LLValue(self.__type_ctx.u64_id, self.__pointers.literal_word())
            zero = self.__u64(0)
            size = self.__u64(self.__literal_array_length(src_pointee))
            return self.__pointers.build_fat(data, word, zero, size, to_type).ir_val
        return self.__core.ir.bitcast(value.ir_val, dest_ll_type)

    def __pointer_to_ref(
        self, value: LLValue, src: Type.PointerType, dst: Type.RefType,
        to_type: int, dest_ll_type: ir.Type,
    ) -> ir.Value:
        if self.__core.context.raw_pointers:
            src_pointee = self.__type_ctx[src.pointee_type]
            if isinstance(src_pointee, Type.ArrayType) and src_pointee.element_type == dst.pointee_type:
                return self.__core.ir.bitcast(value.ir_val, dest_ll_type)
            return value.ir_val
        if self.__pointers.is_fat(value):
            eff = self.__pointers.fat_addr(value, src.pointee_type)
            data = LLValue(self.__type_ctx.alloc_pointer(self.__type_ctx.u8_id), eff.ir_val)  # type: ignore
            word = self.__pointers.extract_fat_field(value, CFG.FAT_WORD)
            zero = self.__u64(0)
            return self.__pointers.build_fat(data, word, zero, zero, to_type).ir_val
        return value.ir_val

    def __ref_to_pointer(
        self, value: LLValue, src: Type.RefType, dst: Type.PointerType,
        to_type: int, dest_ll_type: ir.Type,
    ) -> ir.Value:
        if self.__core.context.raw_pointers:
            src_pointee = self.__type_ctx[src.pointee_type]
            if isinstance(src_pointee, Type.ArrayType) and src_pointee.element_type == dst.pointee_type:
                return self.__core.ir.bitcast(value.ir_val, dest_ll_type)
            return value.ir_val
        if self.__pointers.is_fat(value):
            data = self.__pointers.extract_fat_field(value, CFG.FAT_DATA)
            word = self.__pointers.extract_fat_field(value, CFG.FAT_WORD)
            zero = self.__u64(0)
            src_pointee = self.__type_ctx[src.pointee_type]
            size = self.__u64(1)
            if isinstance(src_pointee, Type.ArrayType) and src_pointee.element_type == dst.pointee_type:
                self.__check_static_view_count(src_pointee)
                size = self.__u64(self.__literal_array_length(src_pointee))
            return self.__pointers.build_fat(data, word, zero, size, to_type).ir_val
        return value.ir_val

    def __pointer_to_slice(self, value: LLValue, src: Type.PointerType, to_type: int) -> ir.Value:
        data = self.__pointers.fat_addr(value, src.pointee_type)
        if self.__core.context.raw_pointers:
            slice_value = self.__core.ir.undef(to_type)
            slice_value = self.__core.ir.insert_value(slice_value, data, 0)
            slice_value = self.__core.ir.insert_value(slice_value, self.__u64(0), 1)
            return slice_value.ir_val
        word = self.__pointers.extract_fat_field(value, CFG.FAT_WORD)
        size = self.__pointers.extract_fat_field(value, CFG.FAT_SIZE)
        index = self.__pointers.extract_fat_field(value, CFG.FAT_INDEX)
        remaining = LLValue(self.__type_ctx.u64_id, self.__builder.sub(size.ir_val, index.ir_val))  # type: ignore
        return self.__pointers.build_fat(data, word, self.__u64(0), remaining, to_type).ir_val

    def __slice_to_ref(self, value: LLValue, to_type: int) -> ir.Value:
        if self.__core.context.raw_pointers:
            return self.__builder.extract_value(value.ir_val, 0)  # type: ignore
        data = LLValue(self.__type_ctx.alloc_pointer(self.__type_ctx.u8_id),
                       self.__builder.extract_value(value.ir_val, CFG.FAT_DATA))  # type: ignore
        word = LLValue(self.__type_ctx.u64_id,
                       self.__builder.extract_value(value.ir_val, CFG.FAT_WORD))  # type: ignore
        zero = self.__u64(0)
        return self.__pointers.build_fat(data, word, zero, zero, to_type).ir_val

    def __literal_array_length(self, array: Type.ArrayType) -> int:
        length = self.__type_ctx[array.length]
        assert isinstance(length, Type.LiteralValueType)
        return length.value

    def __check_static_view_count(self, array: Type.ArrayType) -> None:
        PointerRepresentation.check_static_view_count(self.__literal_array_length(array))

    def __cmp_impl(self, op: BinaryOperator, lhs: ir.Value, rhs: ir.Value, type_id: int) -> ir.Value:
        if self.__type_ctx.is_zst(type_id):
            return ir.Constant(ir.IntType(1), 1 if op == BinaryOperator.Eq else 0)  # type: ignore
        if isinstance(lhs.type, ir.LiteralStructType) or isinstance(rhs.type, ir.LiteralStructType):  # type: ignore
            return self.__cmp_fat_values(op, lhs, rhs)
        predicate = {
            BinaryOperator.Eq: "==", BinaryOperator.Neq: "!=",
            BinaryOperator.Lt: "<", BinaryOperator.Gt: ">",
            BinaryOperator.Leq: "<=", BinaryOperator.Geq: ">=",
        }[op]
        if isinstance(lhs.type, (ir.IntType, ir.PointerType)):  # type: ignore
            if isinstance(lhs.type, ir.PointerType) and lhs.type != rhs.type:  # type: ignore
                opaque = ir.PointerType(ir.IntType(8))  # type: ignore
                lhs = self.__core.ir.bitcast(lhs, opaque)
                rhs = self.__core.ir.bitcast(rhs, opaque)
            ty = self.__type_ctx[type_id]
            if isinstance(ty, Type.IntType) and not ty.signed:
                return self.__builder.icmp_unsigned(predicate, lhs, rhs)  # type: ignore
            return self.__builder.icmp_signed(predicate, lhs, rhs)  # type: ignore
        return self.__builder.fcmp_ordered(predicate, lhs, rhs)  # type: ignore

    def __cmp_fat_values(self, op: BinaryOperator, lhs: ir.Value, rhs: ir.Value) -> ir.Value:
        return self.__fat_safety.cmp_fat_values(op, lhs, rhs)

    def __arith_impl(self, op: BinaryOperator, lhs: ir.Value, rhs: ir.Value, type_id: int) -> ir.Value:
        if isinstance(lhs.type, ir.types._BaseFloatType):  # type: ignore
            return getattr(self.__builder, self.FLOAT_ARITH_OPS[op])(lhs, rhs)
        ty = self.__type_ctx[type_id]
        if isinstance(ty, Type.IntType) and not ty.signed:
            if op == BinaryOperator.Div:
                return self.__builder.udiv(lhs, rhs)  # type: ignore
            if op == BinaryOperator.Mod:
                return self.__builder.urem(lhs, rhs)  # type: ignore
            if op == BinaryOperator.Shr:
                return self.__builder.lshr(lhs, rhs)  # type: ignore
        return getattr(self.__builder, self.ARITH_OPS[op])(lhs, rhs)

    def __u64(self, value: int) -> LLValue:
        return LLValue(self.__type_ctx.u64_id, ir.Constant(ir.IntType(64), value))  # type: ignore
