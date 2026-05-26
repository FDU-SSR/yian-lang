from __future__ import annotations

from typing import Optional

from compiler.analysis.error import AnalysisError
from compiler.analysis.passes.op_builder import OpBuilder
from compiler.analysis.passes.sem_ctx import SemCtx
from compiler.analysis.ty.context import TypeCtx
from compiler.analysis.unit import hir as HIR
from compiler.frontend.parse import ast as AST
from compiler.frontend.parse.operator import BinaryOperator, UnaryOperator
from compiler.utils.IR.position import SrcSpan


class ExprChecker:
    """Expression checker and lowering facade.

    For now this is a minimal stub. Concrete implementations should perform
    semantic checks and return `HIR.Expr` carrying HIR nodes and type ids.
    """

    def __init__(self, ctx: SemCtx):
        self.__ctx = ctx
        self.__op_builder = OpBuilder(ctx, self)

    def value(self, expr: AST.Expr, expected: Optional[int] = None) -> HIR.Expr:
        """Evaluate an expression and return its value (HIR.Expr)."""
        # Dispatch based on AST node kind to dedicated handlers.
        match expr:
            case AST.Binary():
                return self.__handle_binary(expr, expected)
            case AST.Unary():
                return self.__handle_unary(expr, expected)
            case AST.FieldAccess():
                return self.__handle_field_access(expr, expected)
            case AST.Call():
                return self.__handle_call(expr, expected)
            case AST.MethodCall():
                return self.__handle_method_call(expr, expected)
            case AST.DynValue():
                return self.__handle_dyn_value(expr, expected)
            case AST.DynBuffer():
                return self.__handle_dyn_buffer(expr, expected)
            case AST.TypeItem():
                return self.__handle_type_item(expr, expected)
            case AST.Identifier():
                return self.__handle_identifier(expr, expected)
            case AST.Literal():
                return self.__handle_literal(expr, expected)
            case AST.Tuple():
                return self.__handle_tuple(expr, expected)
            case AST.Array():
                return self.__handle_array(expr, expected)

    def __handle_binary(self, node: AST.Binary, expected: Optional[int]) -> HIR.Expr:
        return self.__op_builder.build_binary(node.span, node.op, node.left, node.right, expected)

    def __handle_unary(self, node: AST.Unary, expected: Optional[int]) -> HIR.Expr:
        return self.__op_builder.build_unary(node.span, node.op, node.operand, expected)

    def __handle_field_access(self, node: AST.FieldAccess, expected: Optional[int]) -> HIR.Expr:
        return self.__op_builder.build_field_access(node.span, node.receiver, node.field_name.name, expected)

    def __handle_call(self, node: AST.Call, expected: Optional[int]) -> HIR.Expr:
        raise NotImplementedError()

    def __handle_method_call(self, node: AST.MethodCall, expected: Optional[int]) -> HIR.Expr:
        raise NotImplementedError()

    def __handle_dyn_value(self, node: AST.DynValue, expected: Optional[int]) -> HIR.Expr:
        return self.__op_builder.build_dyn_value(node.span, node.value, expected)

    def __handle_dyn_buffer(self, node: AST.DynBuffer, expected: Optional[int]) -> HIR.Expr:
        return self.__op_builder.build_dyn_buffer(node.span, node.target_type, node.size, expected)

    def __handle_type_item(self, node: AST.TypeItem, expected: Optional[int]) -> HIR.Expr:
        raise NotImplementedError()

    def __handle_identifier(self, node: AST.Identifier, expected: Optional[int]) -> HIR.Expr:
        raise NotImplementedError()

    def __handle_literal(self, node: AST.Literal, expected: Optional[int]) -> HIR.Expr:
        raise NotImplementedError()

    def __handle_tuple(self, node: AST.Tuple, expected: Optional[int]) -> HIR.Expr:
        raise NotImplementedError()

    def __handle_array(self, node: AST.Array, expected: Optional[int]) -> HIR.Expr:
        raise NotImplementedError()

    def as_place(self, expr: AST.Expr) -> HIR.Expr:
        """Treat an expression as an l-value/place and return HIR.Expr."""
        raise NotImplementedError()

    def call_method(self, receiver: HIR.Expr, method_name: str, generic_args: list[int] | None, args: list[HIR.Expr]) -> HIR.Expr:
        raise NotImplementedError()

    def call_into_iter(self, iterable: HIR.Expr) -> HIR.Expr:
        return self.call_method(iterable, "into_iter", None, [])

    def call_next(self, iterator: HIR.Expr) -> HIR.Expr:
        return self.call_method(iterator, "next", None, [])

    def call_eq(self, lhs: HIR.Expr, rhs: HIR.Expr) -> HIR.Expr:
        return self.call_method(lhs, "eq", [rhs.type_id], [rhs])

    def assign(self, span: SrcSpan, target: HIR.Expr, value: HIR.Expr) -> HIR.Binary:
        if not target.is_place:
            raise AnalysisError("assignment target must be an l-value", span)

        if target.type_id != value.type_id:
            target_name = self.__ctx.type_ctx.get_name(target.type_id)
            value_name = self.__ctx.type_ctx.get_name(value.type_id)
            raise AnalysisError(f"cannot assign value of type '{value_name}' to '{target_name}'", span)

        return HIR.Binary(
            span=span,
            op=BinaryOperator.Assign,
            left=target,
            right=value,
            type_id=target.type_id,
            is_place=False,
        )

    def logical_not(self, operand: HIR.Expr) -> HIR.Expr:
        if operand.type_id != TypeCtx.bool_id:
            operand_name = self.__ctx.type_ctx.get_name(operand.type_id)
            raise AnalysisError(f"logical not expects a bool value, got '{operand_name}'", operand.span)

        return HIR.Unary(
            span=operand.span,
            op=UnaryOperator.LogicalNot,
            operand=operand,
            type_id=TypeCtx.bool_id,
            is_place=False,
        )
