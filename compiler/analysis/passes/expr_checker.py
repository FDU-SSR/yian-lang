from __future__ import annotations

from compiler.analysis.error import AnalysisError
from compiler.analysis.passes.call_dispatcher import CallDispatcher
from compiler.analysis.passes.op_builder import OpBuilder
from compiler.analysis.passes.sem_ctx import SemCtx
from compiler.analysis.symbol.symbol import SymbolKind
from compiler.analysis.ty import ty as Type
from compiler.analysis.ty.context import TypeCtx
from compiler.analysis.unit import hir as HIR
from compiler.frontend.lex import token as Tok
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
        self.__call_dispatcher = CallDispatcher(ctx, self)

    def value(self, expr: AST.Expr) -> HIR.Expr:
        """Evaluate an expression and return its value (HIR.Expr)."""
        match expr:
            case AST.Binary():
                return self.__handle_binary(expr)
            case AST.Unary():
                return self.__handle_unary(expr)
            case AST.FieldAccess():
                return self.__handle_field_access(expr)
            case AST.Call():
                return self.__handle_call(expr)
            case AST.MethodCall():
                return self.__handle_method_call(expr)
            case AST.DynValue():
                return self.__handle_dyn_value(expr)
            case AST.DynBuffer():
                return self.__handle_dyn_buffer(expr)
            case AST.TypeItem():
                return self.__handle_type_item(expr)
            case AST.Identifier():
                return self.__handle_identifier(expr)
            case AST.Literal():
                return self.__handle_literal(expr)
            case AST.Tuple():
                return self.__handle_tuple(expr)
            case AST.Array():
                return self.__handle_array(expr)

    def __handle_binary(self, node: AST.Binary) -> HIR.Expr:
        return self.__op_builder.build_binary(node.span, node.op, node.left, node.right)

    def __handle_unary(self, node: AST.Unary) -> HIR.Expr:
        return self.__op_builder.build_unary(node.span, node.op, node.operand)

    def __handle_field_access(self, node: AST.FieldAccess) -> HIR.Expr:
        return self.__op_builder.build_field_access(node.span, node.receiver, node.field_name.name)

    def __handle_call(self, node: AST.Call) -> HIR.Expr:
        return self.__call_dispatcher.handle_call(node)

    def __handle_method_call(self, node: AST.MethodCall) -> HIR.Expr:
        return self.__call_dispatcher.handle_method_call(node)

    def __handle_dyn_value(self, node: AST.DynValue) -> HIR.Expr:
        return self.__op_builder.build_dyn_value(node.span, node.value)

    def __handle_dyn_buffer(self, node: AST.DynBuffer) -> HIR.Expr:
        return self.__op_builder.build_dyn_buffer(node.span, node.target_type, node.size)

    def __handle_type_item(self, node: AST.TypeItem) -> HIR.Expr:
        assert self.__ctx.symbol_ctx is not None

        symbol = self.__ctx.symbol_ctx.lookup(node.name.name)
        if symbol is None or symbol.kind != SymbolKind.Type:
            raise AnalysisError(f"Unknown type '{node.name.name}'", node.name.span)

        generic_arg_ids = [self.__ctx.resolve_type(generic) for generic in node.generics]
        type_id = symbol.type_id
        if generic_arg_ids:
            type_id = self.__ctx.type_ctx.alloc_instance(type_id, generic_arg_ids)

        return HIR.Ty(span=node.span, type_id=type_id, is_place=False)

    def __handle_identifier(self, node: AST.Identifier) -> HIR.Expr:
        assert self.__ctx.symbol_ctx is not None

        symbol = self.__ctx.symbol_ctx.lookup(node.name)
        if symbol is None:
            raise AnalysisError(f"Unknown identifier '{node.name}'", node.span)

        match symbol.kind:
            case SymbolKind.Variable:
                return HIR.Var(span=node.span, symbol_id=symbol.symbol_id, type_id=symbol.type_id, is_place=False)
            case SymbolKind.Type:
                return HIR.Ty(span=node.span, type_id=symbol.type_id, is_place=False)
            case _:
                raise AnalysisError(f"Identifier '{node.name}' cannot be used as an expression", node.span)

    def __handle_literal(self, node: AST.Literal) -> HIR.Expr:
        literal = node.literal

        match literal:
            case Tok.IntLiteral():
                if literal.suffix is None:
                    type_id = TypeCtx.int_literal_id
                else:
                    intrinsic_type = Type.IntrinsicType.from_str(literal.suffix)
                    if intrinsic_type is None:
                        raise AnalysisError(f"Unknown intrinsic type suffix '{literal.suffix}'", node.span)
                    type_id = TypeCtx.intrinsic_type(intrinsic_type)
                return HIR.IntLiteral(span=node.span, value=literal.value, type_id=type_id, is_place=False)
            case Tok.FloatLiteral():
                if literal.suffix is None:
                    type_id = TypeCtx.float_literal_id
                else:
                    intrinsic_type = Type.IntrinsicType.from_str(literal.suffix)
                    if intrinsic_type is None:
                        raise AnalysisError(f"Unknown intrinsic type suffix '{literal.suffix}'", node.span)
                    type_id = TypeCtx.intrinsic_type(intrinsic_type)
                return HIR.FloatLiteral(span=node.span, value=literal.value, type_id=type_id, is_place=False)
            case Tok.CharLiteral():
                return HIR.CharLiteral(span=node.span, value=literal.value, type_id=TypeCtx.char_id, is_place=False)
            case Tok.StrLiteral():
                return HIR.StrLiteral(span=node.span, value=literal.value, type_id=TypeCtx.str_id, is_place=False)
            case Tok.BoolLiteral():
                return HIR.BoolLiteral(span=node.span, value=literal.value, type_id=TypeCtx.bool_id, is_place=False)

    def __handle_tuple(self, node: AST.Tuple) -> HIR.Expr:
        elements = [self.value(element) for element in node.elements]
        type_id = self.__ctx.type_ctx.alloc_tuple([element.type_id for element in elements])
        return HIR.Tuple(span=node.span, field_values=elements, type_id=type_id, is_place=False)

    def __handle_array(self, node: AST.Array) -> HIR.Expr:
        if not node.elements:
            raise AnalysisError("Cannot infer the type of an empty array literal", node.span)

        elements = [self.value(element) for element in node.elements]
        element_type_id = self.__ctx.type_ctx.infer_common_type([element.type_id for element in elements], node.span, "array elements")
        if any(element.type_id != element_type_id for element in elements):
            elements = [self.coerce(element, element_type_id) for element in elements]

        type_id = self.__ctx.type_ctx.alloc_array(element_type_id, len(elements))
        return HIR.Array(span=node.span, element_type=element_type_id, elements=elements, type_id=type_id, is_place=False)

    def coerce(self, expr: HIR.Expr, expected: int) -> HIR.Expr:
        if expr.type_id == expected:
            return expr

        expected_ty = self.__ctx.type_ctx[expected]

        match expr:
            case HIR.IntLiteral():
                if not isinstance(expected_ty, (Type.IntType, Type.FloatType, Type.IntLiteralType, Type.FloatLiteralType)):
                    raise AnalysisError(f"cannot coerce integer literal to '{self.__ctx.type_ctx.get_name(expected)}'", expr.span)
                expr.type_id = expected
                return expr
            case HIR.FloatLiteral():
                if not isinstance(expected_ty, (Type.FloatType, Type.FloatLiteralType)):
                    raise AnalysisError(f"cannot coerce float literal to '{self.__ctx.type_ctx.get_name(expected)}'", expr.span)
                expr.type_id = expected
                return expr
            case HIR.CharLiteral() | HIR.StrLiteral() | HIR.BoolLiteral() | HIR.Var() | HIR.Ty():
                if expr.type_id != expected:
                    raise AnalysisError(
                        f"Expected type '{self.__ctx.type_ctx.get_name(expected)}' but got '{self.__ctx.type_ctx.get_name(expr.type_id)}'",
                        expr.span,
                    )
                return expr
            case HIR.Tuple():
                if not isinstance(expected_ty, Type.TupleType):
                    raise AnalysisError(f"Expected tuple type but got '{self.__ctx.type_ctx.get_name(expected)}'", expr.span)
                if len(expected_ty.element_types) != len(expr.field_values):
                    raise AnalysisError("Tuple element count does not match the expected type", expr.span)
                expr.field_values = [
                    self.coerce(field, expected_element)
                    for field, expected_element in zip(expr.field_values, expected_ty.element_types)
                ]
                expr.type_id = expected
                return expr
            case HIR.Array():
                if not isinstance(expected_ty, Type.ArrayType):
                    raise AnalysisError(f"Expected array type but got '{self.__ctx.type_ctx.get_name(expected)}'", expr.span)
                expr.elements = [self.coerce(element, expected_ty.element_type) for element in expr.elements]
                expr.element_type = expected_ty.element_type
                expr.type_id = expected
                return expr
            case HIR.Binary():
                expr.left = self.coerce(expr.left, expected)
                expr.right = self.coerce(expr.right, expected)
                expr.type_id = expected
                return expr
            case HIR.Unary():
                expr.operand = self.coerce(expr.operand, expected)
                expr.type_id = expected
                return expr
            case HIR.DynValue():
                if isinstance(expected_ty, Type.PointerType):
                    expr.value = self.coerce(expr.value, expected_ty.pointee_type)
                    expr.type_id = expected
                    return expr
                expr.value = self.coerce(expr.value, expected)
                expr.type_id = expected
                return expr
            case HIR.DynBuffer():
                expr.length = self.coerce(expr.length, TypeCtx.u64_id)
                expr.type_id = expected
                return expr
            case HIR.FieldAccess():
                expr.receiver = self.coerce(expr.receiver, expr.receiver.type_id)
                expr.type_id = expected
                return expr
            case HIR.Call():
                expr.args = [self.coerce(arg, arg.type_id) for arg in expr.args]
                expr.type_id = expected
                return expr
            case HIR.Invoke():
                expr.callable = self.coerce(expr.callable, expr.callable.type_id)
                expr.args = [self.coerce(arg, arg.type_id) for arg in expr.args]
                expr.type_id = expected
                return expr
            case HIR.MethodCall():
                expr.receiver = self.coerce(expr.receiver, expr.receiver.type_id)
                expr.args = [self.coerce(arg, arg.type_id) for arg in expr.args]
                expr.type_id = expected
                return expr
            case HIR.StructConstruct():
                expr.field_values = {
                    name: self.coerce(field_expr, field_expr.type_id)
                    for name, field_expr in expr.field_values.items()
                }
                expr.type_id = expected
                return expr
            case HIR.VariantConstruct():
                if expr.args is not None:
                    expr.args = {
                        name: self.coerce(field_expr, field_expr.type_id)
                        for name, field_expr in expr.args.items()
                    }
                expr.type_id = expected
                return expr
            case HIR.Cast() | HIR.BitCast():
                expr.value = self.coerce(expr.value, expr.value.type_id)
                expr.type_id = expected
                return expr
            case HIR.SizeOf():
                expr.type_id = expected
                return expr

    def as_place(self, expr: AST.Expr) -> HIR.Expr:
        """Treat an expression as an l-value/place and return HIR.Expr."""
        if isinstance(expr, AST.Identifier):
            assert self.__ctx.symbol_ctx is not None
            symbol = self.__ctx.symbol_ctx.lookup(expr.name)
            if symbol is None:
                raise AnalysisError(f"Unknown identifier '{expr.name}'", expr.span)
            if symbol.kind != SymbolKind.Variable:
                raise AnalysisError(f"Identifier '{expr.name}' is not an l-value", expr.span)
            return HIR.Var(span=expr.span, symbol_id=symbol.symbol_id, type_id=symbol.type_id, is_place=True)

        hir_expr = self.value(expr)
        if not hir_expr.is_place:
            raise AnalysisError("expression is not an l-value", expr.span)
        return hir_expr

    def call_method(self, receiver: HIR.Expr, method_name: str, generic_args: list[int] | None, args: list[HIR.Expr]) -> HIR.Expr:
        return self.__call_dispatcher.dispatch_method_call(receiver.span, receiver, method_name, generic_args, args, "method call")

    def call_into_iter(self, iterable: HIR.Expr) -> HIR.Expr:
        return self.call_method(iterable, "into_iter", None, [])

    def call_next(self, iterator: HIR.Expr) -> HIR.Expr:
        return self.call_method(iterator, "next", None, [])

    def call_eq(self, lhs: HIR.Expr, rhs: HIR.Expr) -> HIR.Expr:
        return self.call_method(lhs, "eq", None, [rhs])

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
