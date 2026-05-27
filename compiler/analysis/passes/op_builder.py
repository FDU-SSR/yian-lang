from __future__ import annotations

from compiler.analysis.error import AnalysisError
from compiler.analysis.passes.expr_evaluator import ExprEvaluator
from compiler.analysis.passes.sem_ctx import SemCtx
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

    def build_binary(self, span: SrcSpan, op: BinaryOperator, left: AST.Expr, right: AST.Expr) -> HIR.Expr:
        match op:
            case BinaryOperator.Add:
                return self.__build_add(span, left, right)
            case BinaryOperator.Sub:
                return self.__build_sub(span, left, right)
            case BinaryOperator.Mul:
                return self.__build_mul(span, left, right)
            case BinaryOperator.Div:
                return self.__build_div(span, left, right)
            case BinaryOperator.Mod:
                return self.__build_mod(span, left, right)
            case BinaryOperator.BitAnd:
                return self.__build_bitand(span, left, right)
            case BinaryOperator.BitOr:
                return self.__build_bitor(span, left, right)
            case BinaryOperator.BitXor:
                return self.__build_bitxor(span, left, right)
            case BinaryOperator.Shl:
                return self.__build_shl(span, left, right)
            case BinaryOperator.Shr:
                return self.__build_shr(span, left, right)
            case BinaryOperator.Eq | BinaryOperator.Neq | BinaryOperator.Lt | BinaryOperator.Gt | BinaryOperator.Leq | BinaryOperator.Geq:
                return self.__build_cmp(span, op, left, right)
            case BinaryOperator.LogicalAnd | BinaryOperator.LogicalOr:
                return self.__build_logical(span, op, left, right)
            case BinaryOperator.Assign:
                return self.__build_assign(span, left, right)
            case BinaryOperator.AddAssign:
                return self.__build_add_assign(span, left, right)
            case BinaryOperator.SubAssign:
                return self.__build_sub_assign(span, left, right)
            case BinaryOperator.MulAssign:
                return self.__build_mul_assign(span, left, right)
            case BinaryOperator.DivAssign:
                return self.__build_div_assign(span, left, right)
            case BinaryOperator.ModAssign:
                return self.__build_mod_assign(span, left, right)
            case BinaryOperator.BitAndAssign:
                return self.__build_bitand_assign(span, left, right)
            case BinaryOperator.BitOrAssign:
                return self.__build_bitor_assign(span, left, right)
            case BinaryOperator.BitXorAssign:
                return self.__build_bitxor_assign(span, left, right)
            case BinaryOperator.ShlAssign:
                return self.__build_shl_assign(span, left, right)
            case BinaryOperator.ShrAssign:
                return self.__build_shr_assign(span, left, right)
            case BinaryOperator.Index:
                return self.__build_index(span, left, right)
            case BinaryOperator.In | BinaryOperator.NotIn:
                return self.__build_in(span, op, left, right)
            case BinaryOperator.Range:
                return self.__build_range(span, left, right)

    def build_unary(self, span: SrcSpan, op: UnaryOperator, operand: AST.Expr) -> HIR.Expr:
        match op:
            case UnaryOperator.Neg:
                return self.__build_neg(span, operand)
            case UnaryOperator.BitNot:
                return self.__build_bitnot(span, operand)
            case UnaryOperator.LogicalNot:
                return self.__build_logical_not(span, operand)
            case UnaryOperator.Deref:
                return self.__build_deref(span, operand)
            case UnaryOperator.AddrOf:
                return self.__build_addr_of(span, operand)

    def build_field_access(self, span: SrcSpan, receiver: AST.Expr, field_name: str) -> HIR.Expr:
        receiver_hir = self.__evaluator.value(receiver)
        if isinstance(receiver_hir, HIR.Var):
            # struct field access
            res = self.__build_field_access(span, receiver_hir, field_name)
        elif isinstance(receiver_hir, HIR.Ty):
            # enum variant construction
            res = self.__build_variant_construct(span, receiver_hir.type_id, field_name)
        else:
            raise AnalysisError("field access is only supported on struct instances and enum types", span)

        return res

    def build_dyn_value(self, span: SrcSpan, value: AST.Expr) -> HIR.Expr:
        value_hir = self.__evaluator.value(value)

        ptr_type_id = self.__type_ctx.alloc_pointer(value_hir.type_id)
        expr = HIR.DynValue(span=span, value=value_hir, type_id=ptr_type_id, is_place=False)
        return expr

    def build_dyn_buffer(self, span: SrcSpan, target_type: ASTType, size: AST.Expr) -> HIR.Expr:
        target_type_id = self.__ctx.resolve_type(target_type)
        size_hir = self.__evaluator.coerce(self.__evaluator.value(size), TypeCtx.u64_id)

        ptr_type_id = self.__type_ctx.alloc_pointer(target_type_id)
        expr = HIR.DynBuffer(span=span, element_type=target_type_id, length=size_hir, type_id=ptr_type_id, is_place=False)
        return expr

    def __build_add(self, span: SrcSpan, left: AST.Expr, right: AST.Expr) -> HIR.Expr:
        raise NotImplementedError()

    def __build_sub(self, span: SrcSpan, left: AST.Expr, right: AST.Expr) -> HIR.Expr:
        raise NotImplementedError()

    def __build_mul(self, span: SrcSpan, left: AST.Expr, right: AST.Expr) -> HIR.Expr:
        raise NotImplementedError()

    def __build_div(self, span: SrcSpan, left: AST.Expr, right: AST.Expr) -> HIR.Expr:
        raise NotImplementedError()

    def __build_mod(self, span: SrcSpan, left: AST.Expr, right: AST.Expr) -> HIR.Expr:
        raise NotImplementedError()

    def __build_bitand(self, span: SrcSpan, left: AST.Expr, right: AST.Expr) -> HIR.Expr:
        raise NotImplementedError()

    def __build_bitor(self, span: SrcSpan, left: AST.Expr, right: AST.Expr) -> HIR.Expr:
        raise NotImplementedError()

    def __build_bitxor(self, span: SrcSpan, left: AST.Expr, right: AST.Expr) -> HIR.Expr:
        raise NotImplementedError()

    def __build_shl(self, span: SrcSpan, left: AST.Expr, right: AST.Expr) -> HIR.Expr:
        raise NotImplementedError()

    def __build_shr(self, span: SrcSpan, left: AST.Expr, right: AST.Expr) -> HIR.Expr:
        raise NotImplementedError()

    def __build_cmp(self, span: SrcSpan, op: BinaryOperator, left: AST.Expr, right: AST.Expr) -> HIR.Expr:
        raise NotImplementedError()

    def __build_logical(self, span: SrcSpan, op: BinaryOperator, left: AST.Expr, right: AST.Expr) -> HIR.Expr:
        raise NotImplementedError()

    def __build_assign(self, span: SrcSpan, left: AST.Expr, right: AST.Expr) -> HIR.Expr:
        raise NotImplementedError()

    def __build_add_assign(self, span: SrcSpan, left: AST.Expr, right: AST.Expr) -> HIR.Expr:
        raise NotImplementedError()

    def __build_sub_assign(self, span: SrcSpan, left: AST.Expr, right: AST.Expr) -> HIR.Expr:
        raise NotImplementedError()

    def __build_mul_assign(self, span: SrcSpan, left: AST.Expr, right: AST.Expr) -> HIR.Expr:
        raise NotImplementedError()

    def __build_div_assign(self, span: SrcSpan, left: AST.Expr, right: AST.Expr) -> HIR.Expr:
        raise NotImplementedError()

    def __build_mod_assign(self, span: SrcSpan, left: AST.Expr, right: AST.Expr) -> HIR.Expr:
        raise NotImplementedError()

    def __build_bitand_assign(self, span: SrcSpan, left: AST.Expr, right: AST.Expr) -> HIR.Expr:
        raise NotImplementedError()

    def __build_bitor_assign(self, span: SrcSpan, left: AST.Expr, right: AST.Expr) -> HIR.Expr:
        raise NotImplementedError()

    def __build_bitxor_assign(self, span: SrcSpan, left: AST.Expr, right: AST.Expr) -> HIR.Expr:
        raise NotImplementedError()

    def __build_shl_assign(self, span: SrcSpan, left: AST.Expr, right: AST.Expr) -> HIR.Expr:
        raise NotImplementedError()

    def __build_shr_assign(self, span: SrcSpan, left: AST.Expr, right: AST.Expr) -> HIR.Expr:
        raise NotImplementedError()

    def __build_index(self, span: SrcSpan, left: AST.Expr, right: AST.Expr) -> HIR.Expr:
        raise NotImplementedError()

    def __build_in(self, span: SrcSpan, op: BinaryOperator, left: AST.Expr, right: AST.Expr) -> HIR.Expr:
        raise NotImplementedError()

    def __build_range(self, span: SrcSpan, left: AST.Expr, right: AST.Expr) -> HIR.Expr:
        raise NotImplementedError()

    def __build_neg(self, span: SrcSpan, operand: AST.Expr) -> HIR.Expr:
        raise NotImplementedError()

    def __build_bitnot(self, span: SrcSpan, operand: AST.Expr) -> HIR.Expr:
        raise NotImplementedError()

    def __build_logical_not(self, span: SrcSpan, operand: AST.Expr) -> HIR.Expr:
        raise NotImplementedError()

    def __build_deref(self, span: SrcSpan, operand: AST.Expr) -> HIR.Expr:
        raise NotImplementedError()

    def __build_addr_of(self, span: SrcSpan, operand: AST.Expr) -> HIR.Expr:
        raise NotImplementedError()

    def __build_field_access(self, span: SrcSpan, receiver: HIR.Var, field_name: str) -> HIR.Expr:
        raise NotImplementedError()

    def __build_variant_construct(self, span: SrcSpan, enum_type_id: int, variant_name: str) -> HIR.Expr:
        raise NotImplementedError()
