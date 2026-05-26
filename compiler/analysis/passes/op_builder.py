from __future__ import annotations

from typing import NoReturn, Optional

from compiler.analysis.error import AnalysisError
from compiler.analysis.passes.expr_evaluator import ExprEvaluator
from compiler.analysis.passes.sem_ctx import SemCtx
from compiler.analysis.ty import ty as Type
from compiler.analysis.ty.context import TypeCtx
from compiler.analysis.unit import hir as HIR
from compiler.frontend.parse import ast as AST
from compiler.frontend.parse.ast_type import ASTType
from compiler.frontend.parse.operator import BinaryOperator, UnaryOperator
from compiler.utils.IR.position import SrcSpan


class OpBuilder:
    """Operator semantics builder.

    This module is responsible for deciding builtin vs trait-based operator
    semantics and constructing the corresponding HIR nodes. Implementations
    of the methods below should follow the project's op routing rules.
    """

    def __init__(self, ctx: SemCtx, expr_evaluator: ExprEvaluator) -> None:
        self.__ctx = ctx
        self.__type_ctx = ctx.type_ctx
        self.__evaluator = expr_evaluator

    def __expectation_checker(self, span: SrcSpan, expr: HIR.Expr, expected: Optional[int]) -> None:
        if expected is not None and expr.type_id != expected:
            actual_name = self.__type_ctx.get_name(expr.type_id)
            expected_name = self.__type_ctx.get_name(expected)
            raise AnalysisError(f"Expected type '{expected_name}' but got '{actual_name}'", span)

    def __type_mismatch(self, span: SrcSpan, actual: int, expected: str, context: str) -> NoReturn:
        actual_name = self.__type_ctx.get_name(actual)
        raise AnalysisError(f"Expected {expected} but got '{actual_name}' in {context}", span)

    def build_binary(self, span: SrcSpan, op: BinaryOperator, left: AST.Expr, right: AST.Expr, expected: Optional[int] = None) -> HIR.Expr:
        match op:
            case BinaryOperator.Add:
                return self.__build_add(span, left, right, expected)
            case BinaryOperator.Sub:
                return self.__build_sub(span, left, right, expected)
            case BinaryOperator.Mul:
                return self.__build_mul(span, left, right, expected)
            case BinaryOperator.Div:
                return self.__build_div(span, left, right, expected)
            case BinaryOperator.Mod:
                return self.__build_mod(span, left, right, expected)
            case BinaryOperator.BitAnd:
                return self.__build_bitand(span, left, right, expected)
            case BinaryOperator.BitOr:
                return self.__build_bitor(span, left, right, expected)
            case BinaryOperator.BitXor:
                return self.__build_bitxor(span, left, right, expected)
            case BinaryOperator.Shl:
                return self.__build_shl(span, left, right, expected)
            case BinaryOperator.Shr:
                return self.__build_shr(span, left, right, expected)
            case BinaryOperator.Eq | BinaryOperator.Neq | BinaryOperator.Lt | BinaryOperator.Gt | BinaryOperator.Leq | BinaryOperator.Geq:
                return self.__build_cmp(span, op, left, right, expected)
            case BinaryOperator.LogicalAnd | BinaryOperator.LogicalOr:
                return self.__build_logical(span, op, left, right, expected)
            case BinaryOperator.Assign:
                return self.__build_assign(span, left, right, expected)
            case BinaryOperator.AddAssign:
                return self.__build_add_assign(span, left, right, expected)
            case BinaryOperator.SubAssign:
                return self.__build_sub_assign(span, left, right, expected)
            case BinaryOperator.MulAssign:
                return self.__build_mul_assign(span, left, right, expected)
            case BinaryOperator.DivAssign:
                return self.__build_div_assign(span, left, right, expected)
            case BinaryOperator.ModAssign:
                return self.__build_mod_assign(span, left, right, expected)
            case BinaryOperator.BitAndAssign:
                return self.__build_bitand_assign(span, left, right, expected)
            case BinaryOperator.BitOrAssign:
                return self.__build_bitor_assign(span, left, right, expected)
            case BinaryOperator.BitXorAssign:
                return self.__build_bitxor_assign(span, left, right, expected)
            case BinaryOperator.ShlAssign:
                return self.__build_shl_assign(span, left, right, expected)
            case BinaryOperator.ShrAssign:
                return self.__build_shr_assign(span, left, right, expected)
            case BinaryOperator.Index:
                return self.__build_index(span, left, right, expected)
            case BinaryOperator.In | BinaryOperator.NotIn:
                return self.__build_in(span, op, left, right, expected)
            case BinaryOperator.Range:
                return self.__build_range(span, left, right, expected)

    def build_unary(self, span: SrcSpan, op: UnaryOperator, operand: AST.Expr, expected: Optional[int] = None) -> HIR.Expr:
        match op:
            case UnaryOperator.Neg:
                return self.__build_neg(span, operand, expected)
            case UnaryOperator.BitNot:
                return self.__build_bitnot(span, operand, expected)
            case UnaryOperator.LogicalNot:
                return self.__build_logical_not(span, operand, expected)
            case UnaryOperator.Deref:
                return self.__build_deref(span, operand, expected)
            case UnaryOperator.AddrOf:
                return self.__build_addr_of(span, operand, expected)

    def build_field_access(self, span: SrcSpan, receiver: AST.Expr, field_name: str, expected: Optional[int] = None) -> HIR.Expr:
        receiver_hir = self.__evaluator.value(receiver)
        if isinstance(receiver_hir, HIR.Var):
            # struct field access
            res = self.__build_field_access(span, receiver_hir, field_name)
        elif isinstance(receiver_hir, HIR.Ty):
            # enum variant construction
            res = self.__build_variant_construct(span, receiver_hir.type_id, field_name)
        else:
            raise AnalysisError("field access is only supported on struct instances and enum types", span)

        self.__expectation_checker(span, res, expected)
        return res

    def build_dyn_value(self, span: SrcSpan, value: AST.Expr, expected: Optional[int] = None) -> HIR.Expr:
        if expected is not None:
            expected_ty = self.__type_ctx[expected]
            if not isinstance(expected_ty, Type.PointerType):
                self.__type_mismatch(span, expected, "pointer type", "dyn value")
            value_hir = self.__evaluator.value(value, expected_ty.pointee_type)
        else:
            value_hir = self.__evaluator.value(value)

        ptr_type_id = self.__type_ctx.alloc_pointer(value_hir.type_id)
        expr = HIR.DynValue(span=span, value=value_hir, type_id=ptr_type_id, is_place=False)
        return expr

    def build_dyn_buffer(self, span: SrcSpan, target_type: ASTType, size: AST.Expr, expected: Optional[int] = None) -> HIR.Expr:
        target_type_id = self.__ctx.resolve_type(target_type)
        size_hir = self.__evaluator.value(size, TypeCtx.u64_id)

        ptr_type_id = self.__type_ctx.alloc_pointer(target_type_id)
        expr = HIR.DynBuffer(span=span, element_type=target_type_id, length=size_hir, type_id=ptr_type_id, is_place=False)
        self.__expectation_checker(span, expr, expected)
        return expr

    def __build_add(self, span: SrcSpan, left: AST.Expr, right: AST.Expr, expected: Optional[int]) -> HIR.Expr:
        raise NotImplementedError()

    def __build_sub(self, span: SrcSpan, left: AST.Expr, right: AST.Expr, expected: Optional[int]) -> HIR.Expr:
        raise NotImplementedError()

    def __build_mul(self, span: SrcSpan, left: AST.Expr, right: AST.Expr, expected: Optional[int]) -> HIR.Expr:
        raise NotImplementedError()

    def __build_div(self, span: SrcSpan, left: AST.Expr, right: AST.Expr, expected: Optional[int]) -> HIR.Expr:
        raise NotImplementedError()

    def __build_mod(self, span: SrcSpan, left: AST.Expr, right: AST.Expr, expected: Optional[int]) -> HIR.Expr:
        raise NotImplementedError()

    def __build_bitand(self, span: SrcSpan, left: AST.Expr, right: AST.Expr, expected: Optional[int]) -> HIR.Expr:
        raise NotImplementedError()

    def __build_bitor(self, span: SrcSpan, left: AST.Expr, right: AST.Expr, expected: Optional[int]) -> HIR.Expr:
        raise NotImplementedError()

    def __build_bitxor(self, span: SrcSpan, left: AST.Expr, right: AST.Expr, expected: Optional[int]) -> HIR.Expr:
        raise NotImplementedError()

    def __build_shl(self, span: SrcSpan, left: AST.Expr, right: AST.Expr, expected: Optional[int]) -> HIR.Expr:
        raise NotImplementedError()

    def __build_shr(self, span: SrcSpan, left: AST.Expr, right: AST.Expr, expected: Optional[int]) -> HIR.Expr:
        raise NotImplementedError()

    def __build_cmp(self, span: SrcSpan, op: BinaryOperator, left: AST.Expr, right: AST.Expr, expected: Optional[int]) -> HIR.Expr:
        raise NotImplementedError()

    def __build_logical(self, span: SrcSpan, op: BinaryOperator, left: AST.Expr, right: AST.Expr, expected: Optional[int]) -> HIR.Expr:
        raise NotImplementedError()

    def __build_assign(self, span: SrcSpan, left: AST.Expr, right: AST.Expr, expected: Optional[int]) -> HIR.Expr:
        raise NotImplementedError()

    def __build_add_assign(self, span: SrcSpan, left: AST.Expr, right: AST.Expr, expected: Optional[int]) -> HIR.Expr:
        raise NotImplementedError()

    def __build_sub_assign(self, span: SrcSpan, left: AST.Expr, right: AST.Expr, expected: Optional[int]) -> HIR.Expr:
        raise NotImplementedError()

    def __build_mul_assign(self, span: SrcSpan, left: AST.Expr, right: AST.Expr, expected: Optional[int]) -> HIR.Expr:
        raise NotImplementedError()

    def __build_div_assign(self, span: SrcSpan, left: AST.Expr, right: AST.Expr, expected: Optional[int]) -> HIR.Expr:
        raise NotImplementedError()

    def __build_mod_assign(self, span: SrcSpan, left: AST.Expr, right: AST.Expr, expected: Optional[int]) -> HIR.Expr:
        raise NotImplementedError()

    def __build_bitand_assign(self, span: SrcSpan, left: AST.Expr, right: AST.Expr, expected: Optional[int]) -> HIR.Expr:
        raise NotImplementedError()

    def __build_bitor_assign(self, span: SrcSpan, left: AST.Expr, right: AST.Expr, expected: Optional[int]) -> HIR.Expr:
        raise NotImplementedError()

    def __build_bitxor_assign(self, span: SrcSpan, left: AST.Expr, right: AST.Expr, expected: Optional[int]) -> HIR.Expr:
        raise NotImplementedError()

    def __build_shl_assign(self, span: SrcSpan, left: AST.Expr, right: AST.Expr, expected: Optional[int]) -> HIR.Expr:
        raise NotImplementedError()

    def __build_shr_assign(self, span: SrcSpan, left: AST.Expr, right: AST.Expr, expected: Optional[int]) -> HIR.Expr:
        raise NotImplementedError()

    def __build_index(self, span: SrcSpan, left: AST.Expr, right: AST.Expr, expected: Optional[int]) -> HIR.Expr:
        raise NotImplementedError()

    def __build_in(self, span: SrcSpan, op: BinaryOperator, left: AST.Expr, right: AST.Expr, expected: Optional[int]) -> HIR.Expr:
        raise NotImplementedError()

    def __build_range(self, span: SrcSpan, left: AST.Expr, right: AST.Expr, expected: Optional[int]) -> HIR.Expr:
        raise NotImplementedError()

    def __build_neg(self, span: SrcSpan, operand: AST.Expr, expected: Optional[int]) -> HIR.Expr:
        raise NotImplementedError()

    def __build_bitnot(self, span: SrcSpan, operand: AST.Expr, expected: Optional[int]) -> HIR.Expr:
        raise NotImplementedError()

    def __build_logical_not(self, span: SrcSpan, operand: AST.Expr, expected: Optional[int]) -> HIR.Expr:
        raise NotImplementedError()

    def __build_deref(self, span: SrcSpan, operand: AST.Expr, expected: Optional[int]) -> HIR.Expr:
        raise NotImplementedError()

    def __build_addr_of(self, span: SrcSpan, operand: AST.Expr, expected: Optional[int]) -> HIR.Expr:
        raise NotImplementedError()

    def __build_field_access(self, span: SrcSpan, receiver: HIR.Var, field_name: str) -> HIR.Expr:
        raise NotImplementedError()

    def __build_variant_construct(self, span: SrcSpan, enum_type_id: int, variant_name: str) -> HIR.Expr:
        raise NotImplementedError()
