from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, NoReturn

from compiler.analysis.error import AnalysisError
from compiler.analysis.lowering.call_dispatcher import CallDispatcher
from compiler.analysis.lowering.expr_evaluator import ExprEvaluator
from compiler.analysis.lowering.sem_ctx import DefKind
from compiler.analysis.ty import ty as Type
from compiler.analysis.ty.context import TypeCtx
from compiler.analysis.unit import hir as HIR
from compiler.frontend.lex.position import SrcSpan
from compiler.frontend.parse import ast as AST
from compiler.frontend.parse.ast_type import ASTType
from compiler.frontend.parse.operator import BinaryOperator, UnaryOperator

if TYPE_CHECKING:
    from compiler.analysis.lowering.sem_ctx import SemCtx


class OperandType(Enum):
    Integer = "Integer"
    Float = "Float"
    Bool = "Bool"
    Char = "Char"
    Overloaded = "Overloaded"


@dataclass
class BinaryOpDesc:
    """Descriptor for a binary operator that follows the simple `__binary_helper` pattern."""

    op: BinaryOperator
    symbol: str
    allowed_operand_types: set[OperandType]
    result_type_id: int | None = None
    is_assign: bool = False


@dataclass
class UnaryOpDesc:
    """Descriptor for a unary operator that follows the simple `__unary_helper` pattern."""

    op: UnaryOperator
    symbol: str
    allowed_operand_types: set[OperandType]


BINARY_OP_TABLE: dict[BinaryOperator, BinaryOpDesc] = {
    # ---- arithmetic ----
    BinaryOperator.Mul: BinaryOpDesc(BinaryOperator.Mul, "*", {OperandType.Integer, OperandType.Float, OperandType.Overloaded}),
    BinaryOperator.Div: BinaryOpDesc(BinaryOperator.Div, "/", {OperandType.Integer, OperandType.Float, OperandType.Overloaded}),
    BinaryOperator.Mod: BinaryOpDesc(BinaryOperator.Mod, "%", {OperandType.Integer, OperandType.Float, OperandType.Overloaded}),
    # ---- bitwise ----
    BinaryOperator.BitAnd: BinaryOpDesc(BinaryOperator.BitAnd, "&", {OperandType.Integer, OperandType.Bool, OperandType.Overloaded}),
    BinaryOperator.BitOr: BinaryOpDesc(BinaryOperator.BitOr, "|", {OperandType.Integer, OperandType.Bool, OperandType.Overloaded}),
    BinaryOperator.BitXor: BinaryOpDesc(BinaryOperator.BitXor, "^", {OperandType.Integer, OperandType.Bool, OperandType.Overloaded}),
    # ---- comparison (result is bool) ----
    BinaryOperator.Eq: BinaryOpDesc(BinaryOperator.Eq, "Eq", {OperandType.Integer, OperandType.Float, OperandType.Bool, OperandType.Char, OperandType.Overloaded}, result_type_id=TypeCtx.bool_id),
    BinaryOperator.Neq: BinaryOpDesc(BinaryOperator.Neq, "Neq", {OperandType.Integer, OperandType.Float, OperandType.Bool, OperandType.Char, OperandType.Overloaded}, result_type_id=TypeCtx.bool_id),
    BinaryOperator.Lt: BinaryOpDesc(BinaryOperator.Lt, "Lt", {OperandType.Integer, OperandType.Float, OperandType.Overloaded}, result_type_id=TypeCtx.bool_id),
    BinaryOperator.Gt: BinaryOpDesc(BinaryOperator.Gt, "Gt", {OperandType.Integer, OperandType.Float, OperandType.Overloaded}, result_type_id=TypeCtx.bool_id),
    BinaryOperator.Leq: BinaryOpDesc(BinaryOperator.Leq, "Leq", {OperandType.Integer, OperandType.Float, OperandType.Overloaded}, result_type_id=TypeCtx.bool_id),
    BinaryOperator.Geq: BinaryOpDesc(BinaryOperator.Geq, "Geq", {OperandType.Integer, OperandType.Float, OperandType.Overloaded}, result_type_id=TypeCtx.bool_id),
    # ---- logical ----
    BinaryOperator.LogicalAnd: BinaryOpDesc(BinaryOperator.LogicalAnd, "LogicalAnd", {OperandType.Bool}),
    BinaryOperator.LogicalOr: BinaryOpDesc(BinaryOperator.LogicalOr, "LogicalOr", {OperandType.Bool}),
    # ---- compound assignment ----
    BinaryOperator.MulAssign: BinaryOpDesc(BinaryOperator.MulAssign, "*=", {OperandType.Integer, OperandType.Float, OperandType.Overloaded}, is_assign=True),
    BinaryOperator.DivAssign: BinaryOpDesc(BinaryOperator.DivAssign, "/=", {OperandType.Integer, OperandType.Float, OperandType.Overloaded}, is_assign=True),
    BinaryOperator.ModAssign: BinaryOpDesc(BinaryOperator.ModAssign, "%=", {OperandType.Integer, OperandType.Float, OperandType.Overloaded}, is_assign=True),
    BinaryOperator.BitAndAssign: BinaryOpDesc(BinaryOperator.BitAndAssign, "&=", {OperandType.Integer, OperandType.Overloaded}, is_assign=True),
    BinaryOperator.BitOrAssign: BinaryOpDesc(BinaryOperator.BitOrAssign, "|=", {OperandType.Integer, OperandType.Overloaded}, is_assign=True),
    BinaryOperator.BitXorAssign: BinaryOpDesc(BinaryOperator.BitXorAssign, "^=", {OperandType.Integer, OperandType.Overloaded}, is_assign=True),
}

UNARY_OP_TABLE: dict[UnaryOperator, UnaryOpDesc] = {
    UnaryOperator.Neg: UnaryOpDesc(UnaryOperator.Neg, "-", {OperandType.Integer, OperandType.Float, OperandType.Overloaded}),
    UnaryOperator.BitNot: UnaryOpDesc(UnaryOperator.BitNot, "~", {OperandType.Integer, OperandType.Bool, OperandType.Overloaded}),
    UnaryOperator.LogicalNot: UnaryOpDesc(UnaryOperator.LogicalNot, "!", {OperandType.Bool}),
}


class OpBuilder:
    """Operator semantics builder.

    This module is responsible for deciding builtin vs trait-based operator
    semantics and constructing the corresponding HIR nodes. Implementations
    of the methods below should follow the project's op routing rules.
    """

    def __init__(self, ctx: SemCtx, expr_evaluator: ExprEvaluator, call_dispatcher: CallDispatcher) -> None:
        self.__ctx = ctx
        self.__type_ctx = ctx.type_ctx
        self.__evaluator = expr_evaluator
        self.__call_dispatcher = call_dispatcher

        self.__OP_INFO = {
            BinaryOperator.Add: (Type.IntrinsicCustomType.Add, "add"),
            BinaryOperator.Sub: (Type.IntrinsicCustomType.Sub, "sub"),
            BinaryOperator.Mul: (Type.IntrinsicCustomType.Mul, "mul"),
            BinaryOperator.Div: (Type.IntrinsicCustomType.Div, "div"),
            BinaryOperator.Mod: (Type.IntrinsicCustomType.Rem, "rem"),

            BinaryOperator.BitAnd: (Type.IntrinsicCustomType.BitAnd, "bit_and"),
            BinaryOperator.BitOr: (Type.IntrinsicCustomType.BitOr, "bit_or"),
            BinaryOperator.BitXor: (Type.IntrinsicCustomType.BitXor, "bit_xor"),

            BinaryOperator.Shl: (Type.IntrinsicCustomType.Shl, "shl"),
            BinaryOperator.Shr: (Type.IntrinsicCustomType.Shr, "shr"),

            BinaryOperator.Eq: (Type.IntrinsicCustomType.PartialEq, "eq"),
            BinaryOperator.Neq: (Type.IntrinsicCustomType.PartialEq, "ne"),
            BinaryOperator.Lt: (Type.IntrinsicCustomType.PartialOrd, "lt"),
            BinaryOperator.Gt: (Type.IntrinsicCustomType.PartialOrd, "gt"),
            BinaryOperator.Leq: (Type.IntrinsicCustomType.PartialOrd, "le"),
            BinaryOperator.Geq: (Type.IntrinsicCustomType.PartialOrd, "ge"),

            BinaryOperator.AddAssign: (Type.IntrinsicCustomType.AddAssign, "add_assign"),
            BinaryOperator.SubAssign: (Type.IntrinsicCustomType.SubAssign, "sub_assign"),
            BinaryOperator.MulAssign: (Type.IntrinsicCustomType.MulAssign, "mul_assign"),
            BinaryOperator.DivAssign: (Type.IntrinsicCustomType.DivAssign, "div_assign"),
            BinaryOperator.ModAssign: (Type.IntrinsicCustomType.RemAssign, "rem_assign"),
            BinaryOperator.BitAndAssign: (Type.IntrinsicCustomType.BitAndAssign, "bitand_assign"),
            BinaryOperator.BitOrAssign: (Type.IntrinsicCustomType.BitOrAssign, "bitor_assign"),
            BinaryOperator.BitXorAssign: (Type.IntrinsicCustomType.BitXorAssign, "bitxor_assign"),
            BinaryOperator.ShlAssign: (Type.IntrinsicCustomType.ShlAssign, "shl_assign"),
            BinaryOperator.ShrAssign: (Type.IntrinsicCustomType.ShrAssign, "shr_assign"),

            BinaryOperator.Index: (Type.IntrinsicCustomType.Index, "index"),

            UnaryOperator.Neg: (Type.IntrinsicCustomType.Neg, "neg"),
            UnaryOperator.BitNot: (Type.IntrinsicCustomType.BitNot, "bit_not"),
            UnaryOperator.Deref: (Type.IntrinsicCustomType.Deref, "deref"),
        }

    def build_binary(self, span: SrcSpan, op: BinaryOperator, left: AST.Expr, right: AST.Expr) -> HIR.Expr:
        # ---- operators with dedicated logic ----
        match op:
            case BinaryOperator.Add:
                return self.__build_add(span, left, right)
            case BinaryOperator.Sub:
                return self.__build_sub(span, left, right)
            case BinaryOperator.AddAssign:
                return self.__build_add_assign(span, left, right)
            case BinaryOperator.SubAssign:
                return self.__build_sub_assign(span, left, right)
            case BinaryOperator.Assign:
                return self.__build_assign(span, left, right)
            case BinaryOperator.Index:
                return self.__build_index(span, left, right)
            case BinaryOperator.Shl:
                return self.__build_shl(span, left, right)
            case BinaryOperator.Shr:
                return self.__build_shr(span, left, right)
            case BinaryOperator.ShlAssign:
                return self.__build_shl_assign(span, left, right)
            case BinaryOperator.ShrAssign:
                return self.__build_shr_assign(span, left, right)
            case _:
                pass

        # ---- table-driven operators ----
        desc = BINARY_OP_TABLE.get(op)
        if desc is not None:
            return self.__build_table_binary(span, left, right, desc)

        raise AnalysisError(f"Unhandled binary operator: {op}", span)

    def build_unary(self, span: SrcSpan, op: UnaryOperator, operand: AST.Expr) -> HIR.Expr:
        # ---- operators with dedicated logic ----
        match op:
            case UnaryOperator.Deref:
                return self.__build_deref(span, operand)
            case UnaryOperator.AddrOf:
                return self.__build_addr_of(span, operand)
            case _:
                pass

        # ---- table-driven operators ----
        desc = UNARY_OP_TABLE.get(op)
        if desc is not None:
            return self.__build_table_unary(span, operand, desc)

        raise AnalysisError(f"Unhandled unary operator: {op}", span)

    def build_field_access(self, span: SrcSpan, receiver: AST.Expr, field_name: str) -> HIR.Expr:
        receiver_hir = self.__evaluator.value(receiver)
        receiver_ty = self.__type_ctx[receiver_hir.type_id]

        # auto-deref: only handle PointerType/RefType, NOT the Deref trait
        # (t3:T& 引用同 T* 支持自动解引用取字段)
        while isinstance(receiver_ty, (Type.PointerType, Type.RefType)):
            receiver_hir = HIR.Unary(span, UnaryOperator.Deref, receiver_hir, receiver_ty.pointee_type, is_place=True)
            receiver_ty = self.__type_ctx[receiver_ty.pointee_type]

        if isinstance(receiver_ty, Type.StructType):
            return self.__build_field_access(span, receiver_hir, field_name)
        if isinstance(receiver_hir, HIR.Ty) and isinstance(receiver_ty, Type.EnumType):
            return self.__build_variant_construct(span, receiver_hir.type_id, field_name)

        raise AnalysisError("field access is only supported on struct instances and enum types", span)

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
        left_hir = self.__evaluator.value(left)
        right_hir = self.__evaluator.value(right)

        builtin_expr = self.__binary_helper(
            span=span,
            op=BinaryOperator.Add,
            left=left_hir,
            right=right_hir,
            allowed_operand_types={OperandType.Integer, OperandType.Float, OperandType.Overloaded},
        )
        if builtin_expr is not None:
            return builtin_expr

        left_ty = self.__type_ctx[left_hir.type_id]
        right_ty = self.__type_ctx[right_hir.type_id]

        pointer_operand: HIR.Expr | None = None
        offset_operand: HIR.Expr | None = None

        if isinstance(left_ty, Type.PointerType):
            pointer_operand = left_hir
            offset_operand = right_hir
        elif isinstance(right_ty, Type.PointerType):
            pointer_operand = right_hir
            offset_operand = left_hir

        if pointer_operand is not None and offset_operand is not None and self.__type_ctx.is_integer_type(offset_operand.type_id):
            offset_value = self.__evaluator.coerce(offset_operand, TypeCtx.u64_id)
            return HIR.Binary(
                span=span,
                op=BinaryOperator.Add,
                left=pointer_operand,
                right=offset_value,
                type_id=pointer_operand.type_id,
                is_place=False,
            )

        self.__raise_unsupported_binary_operator(span, "+", left_hir.type_id, right_hir.type_id)

    def __build_sub(self, span: SrcSpan, left: AST.Expr, right: AST.Expr) -> HIR.Expr:
        left_hir = self.__evaluator.value(left)
        right_hir = self.__evaluator.value(right)

        builtin_expr = self.__binary_helper(
            span=span,
            op=BinaryOperator.Sub,
            left=left_hir,
            right=right_hir,
            allowed_operand_types={OperandType.Integer, OperandType.Float, OperandType.Overloaded},
        )
        if builtin_expr is not None:
            return builtin_expr

        left_ty = self.__type_ctx[left_hir.type_id]
        right_ty = self.__type_ctx[right_hir.type_id]

        if isinstance(left_ty, Type.PointerType) and isinstance(right_ty, Type.PointerType):
            if left_hir.type_id != right_hir.type_id:
                left_name = self.__type_ctx.get_name(left_hir.type_id)
                right_name = self.__type_ctx.get_name(right_hir.type_id)
                raise AnalysisError(f"operator '-' is not supported between '{left_name}' and '{right_name}'", span)

            return HIR.Binary(
                span=span,
                op=BinaryOperator.Sub,
                left=left_hir,
                right=right_hir,
                type_id=TypeCtx.i64_id,
                is_place=False,
            )

        if isinstance(left_ty, Type.PointerType) and self.__type_ctx.is_integer_type(right_hir.type_id):
            offset_value = self.__evaluator.coerce(right_hir, TypeCtx.u64_id)
            return HIR.Binary(
                span=span,
                op=BinaryOperator.Sub,
                left=left_hir,
                right=offset_value,
                type_id=left_hir.type_id,
                is_place=False,
            )

        self.__raise_unsupported_binary_operator(span, "-", left_hir.type_id, right_hir.type_id)

    def __build_shl(self, span: SrcSpan, left: AST.Expr, right: AST.Expr) -> HIR.Expr:
        left_hir = self.__evaluator.value(left)
        right_hir = self.__evaluator.value(right)

        builtin_expr = self.__shift_helper(
            span=span,
            op=BinaryOperator.Shl,
            left=left_hir,
            right=right_hir
        )
        return builtin_expr

    def __build_shr(self, span: SrcSpan, left: AST.Expr, right: AST.Expr) -> HIR.Expr:
        left_hir = self.__evaluator.value(left)
        right_hir = self.__evaluator.value(right)

        builtin_expr = self.__shift_helper(
            span=span,
            op=BinaryOperator.Shr,
            left=left_hir,
            right=right_hir
        )
        return builtin_expr

    def __build_assign(self, span: SrcSpan, left: AST.Expr, right: AST.Expr) -> HIR.Expr:
        from compiler.analysis.lowering.assign_check import build_assign
        left_hir = self.__evaluator.value(left)
        right_hir = self.__evaluator.value(right)
        return build_assign(self.__type_ctx, self.__evaluator.coerce, span, left_hir, right_hir)

    def __build_add_assign(self, span: SrcSpan, left: AST.Expr, right: AST.Expr) -> HIR.Expr:
        left_hir = self.__evaluator.value(left)
        right_hir = self.__evaluator.value(right)

        if not left_hir.is_place:
            raise AnalysisError("left operand of assignment must be a place expression", span)

        builtin_expr = self.__binary_helper(
            span=span,
            op=BinaryOperator.AddAssign,
            left=left_hir,
            right=right_hir,
            allowed_operand_types={OperandType.Integer, OperandType.Float, OperandType.Overloaded},
        )
        if builtin_expr is not None:
            return builtin_expr

        left_ty = self.__type_ctx[left_hir.type_id]
        if isinstance(left_ty, Type.PointerType) and self.__type_ctx.is_integer_type(right_hir.type_id):
            offset_value = self.__evaluator.coerce(right_hir, TypeCtx.u64_id)
            return HIR.Binary(
                span=span,
                op=BinaryOperator.AddAssign,
                left=left_hir,
                right=offset_value,
                type_id=left_hir.type_id,
                is_place=False,
            )

        self.__raise_unsupported_binary_operator(span, "+=", left_hir.type_id, right_hir.type_id)

    def __build_sub_assign(self, span: SrcSpan, left: AST.Expr, right: AST.Expr) -> HIR.Expr:
        left_hir = self.__evaluator.value(left)
        right_hir = self.__evaluator.value(right)

        if not left_hir.is_place:
            raise AnalysisError("left operand of assignment must be a place expression", span)

        builtin_expr = self.__binary_helper(
            span=span,
            op=BinaryOperator.SubAssign,
            left=left_hir,
            right=right_hir,
            allowed_operand_types={OperandType.Integer, OperandType.Float, OperandType.Overloaded},
        )
        if builtin_expr is not None:
            return builtin_expr

        left_ty = self.__type_ctx[left_hir.type_id]
        if isinstance(left_ty, Type.PointerType) and self.__type_ctx.is_integer_type(right_hir.type_id):
            offset_value = self.__evaluator.coerce(right_hir, TypeCtx.u64_id)
            return HIR.Binary(
                span=span,
                op=BinaryOperator.SubAssign,
                left=left_hir,
                right=offset_value,
                type_id=left_hir.type_id,
                is_place=False,
            )

        self.__raise_unsupported_binary_operator(span, "-=", left_hir.type_id, right_hir.type_id)

    def __build_shl_assign(self, span: SrcSpan, left: AST.Expr, right: AST.Expr) -> HIR.Expr:
        left_hir = self.__evaluator.value(left)
        right_hir = self.__evaluator.value(right)

        if not left_hir.is_place:
            raise AnalysisError("left operand of assignment must be a place expression", span)

        builtin_expr = self.__shift_helper(
            span=span,
            op=BinaryOperator.ShlAssign,
            left=left_hir,
            right=right_hir
        )
        return builtin_expr

    def __build_shr_assign(self, span: SrcSpan, left: AST.Expr, right: AST.Expr) -> HIR.Expr:
        left_hir = self.__evaluator.value(left)
        right_hir = self.__evaluator.value(right)

        if not left_hir.is_place:
            raise AnalysisError("left operand of assignment must be a place expression", span)

        builtin_expr = self.__shift_helper(
            span=span,
            op=BinaryOperator.ShrAssign,
            left=left_hir,
            right=right_hir
        )
        return builtin_expr

    def __build_index(self, span: SrcSpan, left: AST.Expr, right: AST.Expr) -> HIR.Expr:
        left_hir = self.__evaluator.value(left)
        right_hir = self.__evaluator.value(right)

        left_ty = self.__type_ctx[left_hir.type_id]

        # Tuple indexing
        if isinstance(left_ty, Type.TupleType):
            if not isinstance(right_hir, HIR.IntLiteral):
                raise AnalysisError("Tuple index must be an integer literal.", span)

            index_val = right_hir.value
            if index_val < 0 or index_val >= len(left_ty.element_types):
                raise AnalysisError(f"Tuple index {index_val} out of bounds for tuple of size {len(left_ty.element_types)}.", span)

            elem_type = left_ty.element_types[index_val]
            return HIR.TupleAccess(span, left_hir, index_val, elem_type, is_place=left_hir.is_place)

        # T[N] 整数索引 → 内联数组访问(不走 Index trait;性能优化, 越界检查在 CFG 层)
        if isinstance(left_ty, Type.ArrayType) and self.__type_ctx.is_integer_type(right_hir.type_id):
            # 仅 length 为编译期字面量时内联;泛型 N(ConstGenericType)保守回落 trait
            length_ty = self.__type_ctx[left_ty.length]
            if isinstance(length_ty, Type.LiteralValueType):
                index_value = self.__evaluator.coerce(right_hir, TypeCtx.u64_id)
                return HIR.ArrayAccess(
                    span=span, array=left_hir, index=index_value,
                    element_type=left_ty.element_type, type_id=left_ty.element_type,
                    length=length_ty.value, is_place=left_hir.is_place,
                )
            # 非字面量 length:fall through to trait overload

        # T[] 整数索引 → 内建切片访问(不走 Index trait;性能优化, 检查在 CFG 层)
        if isinstance(left_ty, Type.SliceType) and self.__type_ctx.is_integer_type(right_hir.type_id):
            index_value = self.__evaluator.coerce(right_hir, TypeCtx.u64_id)
            return HIR.SliceAccess(
                span=span, slice=left_hir, index=index_value,
                element_type=left_ty.element_type, type_id=left_ty.element_type,
                is_place=left_hir.is_place,
            )

        # 4) Index overload via trait
        overloaded_expr = self.__resolve_overloaded_operator(span, BinaryOperator.Index, left_hir, [right_hir])
        if overloaded_expr is not None:
            # if index() returns a pointer, add a deref to make it an lvalue
            result_type = self.__type_ctx[overloaded_expr.type_id]
            if isinstance(result_type, Type.PointerType):
                overloaded_expr = HIR.Unary(span, UnaryOperator.Deref, overloaded_expr, result_type.pointee_type, is_place=True)
            return overloaded_expr

        raise AnalysisError(f"Cannot apply index operator {BinaryOperator.Index} to type '{self.__type_ctx.get_name(left_hir.type_id)}'.", span)

    def __build_deref(self, span: SrcSpan, operand: AST.Expr) -> HIR.Expr:
        operand_hir = self.__evaluator.value(operand)

        operand_ty = self.__type_ctx[operand_hir.type_id]
        # t3:T& 引用支持解引用(仅 live 检查,免 in_bounds)
        if isinstance(operand_ty, (Type.PointerType, Type.RefType)):
            return HIR.Unary(span, UnaryOperator.Deref, operand_hir, operand_ty.pointee_type, is_place=True)

        # deref overload via trait
        overloaded_expr = self.__resolve_overloaded_operator(span, UnaryOperator.Deref, operand_hir, [])
        if overloaded_expr is not None:
            result_type = self.__type_ctx[overloaded_expr.type_id]
            if isinstance(result_type, Type.PointerType):
                return HIR.Unary(span, UnaryOperator.Deref, overloaded_expr, result_type.pointee_type, is_place=True)
            else:
                return overloaded_expr

        raise AnalysisError(f"Cannot apply dereference operator to type '{self.__type_ctx.get_name(operand_hir.type_id)}'.", span)

    def __build_addr_of(self, span: SrcSpan, operand: AST.Expr) -> HIR.Expr:
        operand_hir = self.__evaluator.value(operand)

        if isinstance(operand_hir, HIR.Ty):
            func_ty = self.__type_ctx[operand_hir.type_id]
            if isinstance(func_ty, Type.FunctionType):
                params = func_ty.parameters(self.__type_ctx)
                param_type_ids = [p.type_id for p in params]
                ret_type_id = func_ty.return_type(self.__type_ctx)
                fn_ptr_type_id = self.__type_ctx.alloc_function_pointer(param_type_ids, ret_type_id)
                return HIR.Unary(span, UnaryOperator.AddrOf, operand_hir, fn_ptr_type_id, is_place=False)

        # rvalue addr-of is allowed — CFG builder will alloca a stack temporary
        ptr_type_id = self.__type_ctx.alloc_pointer(operand_hir.type_id)
        return HIR.Unary(span, UnaryOperator.AddrOf, operand_hir, ptr_type_id, is_place=False)

    def __build_field_access(self, span: SrcSpan, receiver: HIR.Expr, field_name: str) -> HIR.Expr:
        struct_ty = self.__type_ctx[receiver.type_id]
        assert isinstance(struct_ty, Type.StructType)

        struct_field = struct_ty.get_field_by_name(field_name, self.__type_ctx)
        if struct_field is None:
            raise AnalysisError(f"Struct '{self.__type_ctx.get_name(receiver.type_id)}' has no field named '{field_name}'.", span)

        if struct_field.access_mode == Type.AccessMode.Private:
            if not self.__can_access_private_field(struct_ty):
                raise AnalysisError(
                    f"Field '{field_name}' of struct '{self.__type_ctx.get_name(receiver.type_id)}' "
                    f"is private and cannot be accessed from unit {self.__ctx.unit_id} "
                    f"unless inside an impl for that struct",
                    span,
                )

        return HIR.FieldAccess(span, receiver, struct_field, struct_field.type_id, is_place=receiver.is_place)

    def __can_access_private_field(self, struct_ty: Type.StructType) -> bool:
        """Check whether the current def context allows private field access to *struct_ty*."""
        # Same unit — any code in the struct's defining file can access private fields.
        if self.__ctx.unit_id == struct_ty.custom_def.unit_id:
            return True

        # Inside an impl for the struct — methods have access.
        if self.__ctx.def_kind == DefKind.Method:
            receiver = self.__ctx.receiver_type_id
            if receiver is not None:
                receiver_ty = self.__type_ctx[receiver]
                if isinstance(receiver_ty, Type.PointerType):
                    receiver = receiver_ty.pointee_type
                if isinstance(self.__type_ctx[receiver], Type.StructType):
                    receiver_struct = self.__type_ctx[receiver]
                    assert isinstance(receiver_struct, Type.StructType)
                    if id(receiver_struct.custom_def) == id(struct_ty.custom_def):
                        return True

        return False

    def __build_variant_construct(self, span: SrcSpan, enum_type_id: int, variant_name: str) -> HIR.Expr:
        enum_ty = self.__type_ctx[enum_type_id]
        assert isinstance(enum_ty, Type.EnumType)

        variant = enum_ty.get_variant_by_name(variant_name, self.__type_ctx)
        if variant is None:
            raise AnalysisError(f"Enum '{self.__type_ctx.get_name(enum_type_id)}' has no variant named '{variant_name}'.", span)

        return HIR.VariantConstruct(span, enum_type_id, variant, None, enum_type_id, is_place=False)

    # ------------------------------------------------------------------
    # table-driven generic builders
    # ------------------------------------------------------------------

    def __build_table_binary(self, span: SrcSpan, left: AST.Expr, right: AST.Expr, desc: BinaryOpDesc) -> HIR.Expr:
        left_hir = self.__evaluator.value(left)
        right_hir = self.__evaluator.value(right)

        if desc.is_assign and not left_hir.is_place:
            raise AnalysisError("left operand of assignment must be a place expression", span)

        builtin_expr = self.__binary_helper(
            span=span,
            op=desc.op,
            left=left_hir,
            right=right_hir,
            allowed_operand_types=desc.allowed_operand_types,
            result_type_id=desc.result_type_id,
        )
        if builtin_expr is not None:
            return builtin_expr

        # ZST values: all indistinguishable — EQ/LEQ/GEQ true, NE/LT/GT false.
        if desc.op in (BinaryOperator.Eq, BinaryOperator.Neq, BinaryOperator.Lt,
                        BinaryOperator.Gt, BinaryOperator.Leq, BinaryOperator.Geq):
            if self.__type_ctx.is_zst(left_hir.type_id):
                op = desc.op
                is_true = op in (BinaryOperator.Eq, BinaryOperator.Leq, BinaryOperator.Geq)
                return HIR.BoolLiteral(span, is_true, TypeCtx.bool_id, is_place=False)

        if desc.op in (BinaryOperator.Eq, BinaryOperator.Neq, BinaryOperator.Lt, BinaryOperator.Gt, BinaryOperator.Leq, BinaryOperator.Geq):
            left_ty = self.__type_ctx[left_hir.type_id]
            right_ty = self.__type_ctx[right_hir.type_id]
            if isinstance(left_ty, Type.PointerType) and isinstance(right_ty, Type.PointerType):
                if left_ty.pointee_type == right_ty.pointee_type:
                    return HIR.Binary(span, desc.op, left_hir, right_hir, TypeCtx.bool_id, is_place=False)

        self.__raise_unsupported_binary_operator(span, desc.symbol, left_hir.type_id, right_hir.type_id)

    def __build_table_unary(self, span: SrcSpan, operand: AST.Expr, desc: UnaryOpDesc) -> HIR.Expr:
        operand_hir = self.__evaluator.value(operand)

        builtin_expr = self.__unary_helper(
            span=span,
            op=desc.op,
            operand=operand_hir,
            allowed_operand_types=desc.allowed_operand_types,
        )
        if builtin_expr is not None:
            return builtin_expr

        self.__raise_unsupported_unary_operator(span, desc.symbol, operand_hir.type_id)

    def __shift_helper(self, span: SrcSpan, op: BinaryOperator, left: HIR.Expr, right: HIR.Expr) -> HIR.Expr:
        """Helper function for building shift operator expressions.

        1. Both operands are builtin integer types, but can be different integer types
        2. Operator can be overloaded and left operand implements the corresponding trait
        """
        left_operand_type = self.__get_operand_type(left.type_id, {OperandType.Integer, OperandType.Overloaded})
        right_operand_type = self.__get_operand_type(right.type_id, {OperandType.Integer, OperandType.Overloaded})
        if left_operand_type != right_operand_type:
            self.__raise_unsupported_binary_operator(span, str(op), left.type_id, right.type_id)
        operand_type = left_operand_type

        if operand_type != OperandType.Overloaded:
            result_type_id = left.type_id
            return HIR.Binary(span, op, left, right, result_type_id, is_place=False)

        overloaded_expr = self.__resolve_overloaded_operator(span, op, left, [right])
        if overloaded_expr is not None:
            return overloaded_expr

        self.__raise_unsupported_binary_operator(span, str(op), left.type_id, right.type_id)

    def __binary_helper(
        self,
        span: SrcSpan,
        op: BinaryOperator,
        left: HIR.Expr,
        right: HIR.Expr,
        allowed_operand_types: set[OperandType],
        result_type_id: int | None = None,
    ) -> HIR.Expr | None:
        """Helper function for building binary operator expressions.

        1. Operand types are both builtin types
        2. Operator can be overloaded and operands implement the corresponding trait
        """
        left_operand_type = self.__get_operand_type(left.type_id, allowed_operand_types)
        right_operand_type = self.__get_operand_type(right.type_id, allowed_operand_types)

        if OperandType.Overloaded not in {left_operand_type, right_operand_type}:
            operand_type_id = self.__type_ctx.merge_types([left.type_id, right.type_id], span)
            left = self.__evaluator.coerce(left, operand_type_id)
            right = self.__evaluator.coerce(right, operand_type_id)
            if result_type_id is None:
                result_type_id = operand_type_id
            return HIR.Binary(span, op, left, right, result_type_id, is_place=False)

        if OperandType.Overloaded not in allowed_operand_types:
            return None

        overloaded_expr = self.__resolve_overloaded_operator(span, op, left, [right])
        return overloaded_expr

    def __unary_helper(self, span: SrcSpan, op: UnaryOperator, operand: HIR.Expr, allowed_operand_types: set[OperandType]) -> HIR.Expr | None:
        operand_type = self.__get_operand_type(operand.type_id, allowed_operand_types)

        if operand_type != OperandType.Overloaded:
            return HIR.Unary(span, op, operand, operand.type_id, is_place=False)

        if OperandType.Overloaded not in allowed_operand_types:
            return None

        overloaded_expr = self.__resolve_overloaded_operator(span, op, operand, [])
        return overloaded_expr

    def __get_operand_type(self, type_id: int, allowed_operand_types: set[OperandType]) -> OperandType:
        ty = self.__type_ctx[type_id]
        if isinstance(ty, (Type.IntType, Type.IntLiteralType)) and OperandType.Integer in allowed_operand_types:
            return OperandType.Integer
        if isinstance(ty, (Type.FloatType, Type.FloatLiteralType)) and OperandType.Float in allowed_operand_types:
            return OperandType.Float
        if isinstance(ty, Type.BoolType) and OperandType.Bool in allowed_operand_types:
            return OperandType.Bool
        if isinstance(ty, Type.CharType) and OperandType.Char in allowed_operand_types:
            return OperandType.Char
        return OperandType.Overloaded

    def __resolve_overloaded_operator(self, span: SrcSpan, op: BinaryOperator | UnaryOperator, receiver: HIR.Expr, args: list[HIR.Expr]) -> HIR.MethodCall | None:
        trait_kind, method_name = self.__OP_INFO[op]

        # Non-consuming operators take pointer params — wrap args with &.
        if trait_kind in (Type.IntrinsicCustomType.PartialEq,
                          Type.IntrinsicCustomType.PartialOrd,
                          Type.IntrinsicCustomType.Index):
            args = [HIR.Unary(span=span, op=UnaryOperator.AddrOf, operand=arg,
                              type_id=self.__type_ctx.alloc_pointer(arg.type_id),
                              is_place=False)
                    for arg in args]

        trait_id = TypeCtx.intrinsic_custom_type(trait_kind)

        lookup = self.__type_ctx.method_lookup(receiver, method_name, None, args)
        if lookup is None:
            return None
        impl_trait_id = lookup.impl.trait
        if impl_trait_id is None:
            return None

        impl_trait_ty = self.__type_ctx[impl_trait_id]
        trait_ty = self.__type_ctx[trait_id]
        if not isinstance(impl_trait_ty, Type.TraitType) or not isinstance(trait_ty, Type.TraitType):
            return None

        if id(impl_trait_ty.custom_def) != id(trait_ty.custom_def):
            return None

        return self.__call_dispatcher.build_method_call(span, receiver, lookup, args, "operator")

    def __raise_unsupported_unary_operator(self, span: SrcSpan, operator_symbol: str, operand_type_id: int) -> NoReturn:
        operand_name = self.__type_ctx.get_name(operand_type_id)
        raise AnalysisError(
            f"operator '{operator_symbol}' is not supported for type '{operand_name}'",
            span,
        )

    def __raise_unsupported_binary_operator(self, span: SrcSpan, operator_symbol: str, left_type_id: int, right_type_id: int) -> NoReturn:
        left_name = self.__type_ctx.get_name(left_type_id)
        right_name = self.__type_ctx.get_name(right_type_id)
        raise AnalysisError(
            f"operator '{operator_symbol}' is not supported between '{left_name}' and '{right_name}'",
            span,
        )
