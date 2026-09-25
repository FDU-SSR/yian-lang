"""Scalar arithmetic and pointer-operator routing for CFG lowering."""
from __future__ import annotations

from compiler.analysis.ty import ty as Type
from compiler.analysis.ty.type_ops import default_literals
from compiler.codegen.cfg import ir as IR
from compiler.codegen.cfg.lower.memory import MemoryOps
from compiler.codegen.cfg.lower.state import FunctionState
from compiler.codegen.cfg.lower.values import ValueLowerer
from compiler.frontend.parse.operator import BinaryOperator, UnaryOperator


class ScalarOps:
    def __init__(self, state: FunctionState, memory: MemoryOps, values: ValueLowerer) -> None:
        self.__state = state
        self.__memory = memory
        self.__values = values

    def build_binary(self, op: BinaryOperator, lhs: IR.Value, rhs: IR.Value, type_id: int) -> IR.Value:
        type_id = default_literals(self.__state.session.type_ctx, type_id)
        # ── route pointer arithmetic to dedicated instructions ──
        lhs_ty = self.__state.session.type_ctx[lhs.type_id]
        rhs_ty = self.__state.session.type_ctx[rhs.type_id]

        # LLVM requires shift operands to have the same integer width.
        if op.is_shift() and lhs.type_id != rhs.type_id:
            rhs = self.__values.build_cast(rhs, lhs.type_id)

        if op == BinaryOperator.Add:
            if isinstance(lhs_ty, Type.PointerType):
                return self.__memory.build_element_ptr(lhs, rhs, type_id)
            if isinstance(rhs_ty, Type.PointerType):
                return self.__memory.build_element_ptr(rhs, lhs, type_id)

        if op == BinaryOperator.Sub:
            if isinstance(lhs_ty, Type.PointerType) and isinstance(rhs_ty, Type.PointerType):
                return self.__memory.build_ptr_diff(lhs, rhs)
            if isinstance(lhs_ty, Type.PointerType):
                # ptr - int → negate offset then ElementPtr
                zero = IR.IntLiteral(value=0, type_id=rhs.type_id)
                neg_offset = self.build_binary(BinaryOperator.Sub, zero, rhs, rhs.type_id)
                return self.__memory.build_element_ptr(lhs, neg_offset, type_id)

        # ── 指针比较字段化(指针比较)──
        # 胖指针(双方 PointerType 且 pointee 非 ZST)的比较路由至 PtrCmp:
        # 序比较先插 CheckPtrCmp(data 相等前提,跨对象失败);相等比较按
        # (data, index) 二元组。FunctionPointerType 非 PointerType,不参与。
        if op.is_comparison() and self.__state.pointers.is_fat_pointer(lhs) and self.__state.pointers.is_fat_pointer(rhs):
            return self.__memory.build_ptr_cmp(op, lhs, rhs, type_id)

        result = IR.Reg(name=self.__state.emitter.new_name(), type_id=type_id)
        return self.__state.emitter.emit(IR.Binary(result=result, op=op, lhs=lhs, rhs=rhs)).result

    def build_unary(self, op: UnaryOperator, operand: IR.Value, type_id: int) -> IR.Value:
        type_id = default_literals(self.__state.session.type_ctx, type_id)
        result = IR.Reg(name=self.__state.emitter.new_name(), type_id=type_id)
        return self.__state.emitter.emit(IR.Unary(result=result, op=op, operand=operand)).result
