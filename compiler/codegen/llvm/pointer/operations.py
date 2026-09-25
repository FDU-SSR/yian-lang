# pyright: reportUnknownMemberType=false
"""Pointer arithmetic and comparison emission."""

from __future__ import annotations

from llvmlite import ir

from compiler.analysis.ty import ty as Type
from compiler.analysis.ty.context import TypeCtx
from compiler.codegen.abi import lockmech as ABI
from compiler.codegen.llvm.function.core import FunctionCore
from compiler.codegen.llvm.pointer.representation import PointerRepresentation
from compiler.codegen.llvm.base.types import LLTypeCtx
from compiler.codegen.llvm.base.value import LLValue
from compiler.frontend.parse.operator import BinaryOperator


class PointerOps:
    """Emit pointer-specific value operations using shared representation rules."""

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

    def element_ptr(self, base: LLValue, offset: LLValue) -> LLValue:
        ptr_type = self.__type_ctx[base.type_id]
        assert isinstance(ptr_type, Type.PointerType)
        if self.__ll_type_ctx.is_zst(ptr_type.pointee_type):
            return self.__core.ir.undef(base.type_id)
        if isinstance(offset.ir_val, ir.Constant) and offset.ir_val.constant == 0:  # type: ignore
            return base
        if self.__pointers.is_fat(base):
            index = self.__pointers.extract_fat_field(base, ABI.FAT_INDEX)
            new_index = LLValue(
                self.__type_ctx.u64_id,
                self.__builder.add(index.ir_val, offset.ir_val),  # type: ignore
            )
            return self.__pointers.insert_field_value(base, new_index, ABI.FAT_INDEX)
        element_type_id = ptr_type.pointee_type
        element_ll = self.__ll_type_ctx.get_ll_type(element_type_id).ir_type
        source = element_ll if base.ir_val.type.is_opaque else None  # type: ignore
        result = self.__builder.gep(
            base.ir_val, [offset.ir_val], inbounds=False, source_etype=source
        )  # type: ignore
        return LLValue(base.type_id, result)

    def ptr_diff(self, lhs: LLValue, rhs: LLValue) -> LLValue:
        lhs_ty = self.__type_ctx[lhs.type_id]
        assert isinstance(lhs_ty, Type.PointerType)
        elem_size = self.__ll_type_ctx.get_type_size(lhs_ty.pointee_type)
        if self.__pointers.is_fat(lhs):
            addr_l = self.__pointers.fat_addr_i128(lhs, lhs_ty.pointee_type)
            addr_r = self.__pointers.fat_addr_i128(rhs, lhs_ty.pointee_type)
            diff128 = self.__builder.sub(addr_l, addr_r)  # type: ignore
            diff64 = self.__builder.trunc(diff128, ir.IntType(64))  # type: ignore
            result = self.__builder.sdiv(
                diff64, ir.Constant(ir.IntType(64), elem_size)  # type: ignore
            )  # type: ignore
        else:
            lhs_int = self.__builder.ptrtoint(lhs.ir_val, ir.IntType(64))  # type: ignore
            rhs_int = self.__builder.ptrtoint(rhs.ir_val, ir.IntType(64))  # type: ignore
            byte_diff = self.__builder.sub(lhs_int, rhs_int)  # type: ignore
            result = self.__builder.sdiv(
                byte_diff, ir.Constant(ir.IntType(64), elem_size)  # type: ignore
            )  # type: ignore
        return LLValue(self.__type_ctx.i64_id, result)  # type: ignore

    def ptr_cmp(self, op: BinaryOperator, lhs: LLValue, rhs: LLValue) -> LLValue:
        lhs_ty = self.__type_ctx[lhs.type_id]
        assert isinstance(lhs_ty, Type.PointerType)
        if self.__ll_type_ctx.is_zst(lhs_ty.pointee_type):
            eq = op in (BinaryOperator.Eq, BinaryOperator.Leq, BinaryOperator.Geq)
            return LLValue(
                self.__type_ctx.bool_id,
                ir.Constant(ir.IntType(1), 1 if eq else 0),  # type: ignore
            )
        data_l, idx_l = self.__pointers.cmp_fat_operands(lhs)
        data_r, idx_r = self.__pointers.cmp_fat_operands(rhs)
        if op in (BinaryOperator.Eq, BinaryOperator.Neq):
            data_eq = self.__builder.icmp_signed("==", data_l, data_r)  # type: ignore
            idx_eq = self.__builder.icmp_unsigned("==", idx_l, idx_r)  # type: ignore
            both = self.__builder.and_(data_eq, idx_eq)  # type: ignore
            result = self.__builder.not_(both) if op == BinaryOperator.Neq else both  # type: ignore
        else:
            predicate = {
                BinaryOperator.Lt: "<", BinaryOperator.Gt: ">",
                BinaryOperator.Leq: "<=", BinaryOperator.Geq: ">=",
            }[op]
            result = self.__builder.icmp_unsigned(predicate, idx_l, idx_r)  # type: ignore
        return LLValue(self.__type_ctx.bool_id, result)  # type: ignore
