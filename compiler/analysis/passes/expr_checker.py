from __future__ import annotations

from compiler.analysis.error import AnalysisError
from compiler.analysis.passes.op_builder import OpBuilder
from compiler.analysis.passes.sem_ctx import SemCtx
from compiler.analysis.symbol.symbol import SymbolKind
from compiler.analysis.ty import ty as Type
from compiler.analysis.ty.context import TypeCtx
from compiler.analysis.unit import hir as HIR
from compiler.config.constants import IntrinsicType
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
        raise NotImplementedError()

    def __handle_method_call(self, node: AST.MethodCall) -> HIR.Expr:
        raise NotImplementedError()

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
                    match IntrinsicType.from_str(literal.suffix):
                        case IntrinsicType.Void:
                            type_id = TypeCtx.void_id
                        case IntrinsicType.Bool:
                            type_id = TypeCtx.bool_id
                        case IntrinsicType.Char:
                            type_id = TypeCtx.char_id
                        case IntrinsicType.Str:
                            type_id = TypeCtx.str_id
                        case IntrinsicType.I8:
                            type_id = TypeCtx.i8_id
                        case IntrinsicType.I16:
                            type_id = TypeCtx.i16_id
                        case IntrinsicType.I32:
                            type_id = TypeCtx.i32_id
                        case IntrinsicType.I64:
                            type_id = TypeCtx.i64_id
                        case IntrinsicType.U8:
                            type_id = TypeCtx.u8_id
                        case IntrinsicType.U16:
                            type_id = TypeCtx.u16_id
                        case IntrinsicType.U32:
                            type_id = TypeCtx.u32_id
                        case IntrinsicType.U64:
                            type_id = TypeCtx.u64_id
                        case IntrinsicType.F16:
                            type_id = TypeCtx.f16_id
                        case IntrinsicType.F32:
                            type_id = TypeCtx.f32_id
                        case IntrinsicType.F64:
                            type_id = TypeCtx.f64_id
                        case IntrinsicType.Int:
                            type_id = TypeCtx.i32_id
                        case IntrinsicType.UInt:
                            type_id = TypeCtx.u64_id
                        case IntrinsicType.Float:
                            type_id = TypeCtx.f64_id
                return HIR.IntLiteral(span=node.span, value=literal.value, type_id=type_id, is_place=False)
            case Tok.FloatLiteral():
                if literal.suffix is None:
                    type_id = TypeCtx.float_literal_id
                else:
                    match IntrinsicType.from_str(literal.suffix):
                        case IntrinsicType.Void:
                            type_id = TypeCtx.void_id
                        case IntrinsicType.Bool:
                            type_id = TypeCtx.bool_id
                        case IntrinsicType.Char:
                            type_id = TypeCtx.char_id
                        case IntrinsicType.Str:
                            type_id = TypeCtx.str_id
                        case IntrinsicType.I8:
                            type_id = TypeCtx.i8_id
                        case IntrinsicType.I16:
                            type_id = TypeCtx.i16_id
                        case IntrinsicType.I32:
                            type_id = TypeCtx.i32_id
                        case IntrinsicType.I64:
                            type_id = TypeCtx.i64_id
                        case IntrinsicType.U8:
                            type_id = TypeCtx.u8_id
                        case IntrinsicType.U16:
                            type_id = TypeCtx.u16_id
                        case IntrinsicType.U32:
                            type_id = TypeCtx.u32_id
                        case IntrinsicType.U64:
                            type_id = TypeCtx.u64_id
                        case IntrinsicType.F16:
                            type_id = TypeCtx.f16_id
                        case IntrinsicType.F32:
                            type_id = TypeCtx.f32_id
                        case IntrinsicType.F64:
                            type_id = TypeCtx.f64_id
                        case IntrinsicType.Int:
                            type_id = TypeCtx.i32_id
                        case IntrinsicType.UInt:
                            type_id = TypeCtx.u64_id
                        case IntrinsicType.Float:
                            type_id = TypeCtx.f64_id
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
        element_type_id = self.__common_array_element_type(elements, node.span)
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

    def __common_array_element_type(self, elements: list[HIR.Expr], span: SrcSpan) -> int:
        type_ids = [element.type_id for element in elements]
        definite_type_ids = [type_id for type_id in type_ids if not self.__is_literal_type(type_id)]

        if definite_type_ids:
            first_type_id = definite_type_ids[0]
            if any(type_id != first_type_id for type_id in definite_type_ids[1:]):
                element_names = ", ".join(self.__ctx.type_ctx.get_name(type_id) for type_id in type_ids)
                raise AnalysisError(f"array elements must have a compatible type, got [{element_names}]", span)
            return first_type_id

        return self.__common_literal_type(type_ids, span)

    def __common_literal_type(self, type_ids: list[int], span: SrcSpan) -> int:
        result_type_id = type_ids[0]
        for next_type_id in type_ids[1:]:
            result_type_id = self.__gcd_literal_type(result_type_id, next_type_id, span)
        return result_type_id

    def __gcd_literal_type(self, left_type_id: int, right_type_id: int, span: SrcSpan) -> int:
        if left_type_id == right_type_id:
            return left_type_id

        left_ty = self.__ctx.type_ctx[left_type_id]
        right_ty = self.__ctx.type_ctx[right_type_id]

        if isinstance(left_ty, Type.IntLiteralType):
            if isinstance(right_ty, Type.IntLiteralType):
                return TypeCtx.int_literal_id
            if isinstance(right_ty, Type.FloatLiteralType):
                return TypeCtx.float_literal_id
        if isinstance(left_ty, Type.FloatLiteralType):
            if isinstance(right_ty, Type.IntLiteralType | Type.FloatLiteralType):
                return TypeCtx.float_literal_id

        if isinstance(left_ty, Type.ArrayType) and isinstance(right_ty, Type.ArrayType):
            if left_ty.length != right_ty.length:
                left_name = self.__ctx.type_ctx.get_name(left_type_id)
                right_name = self.__ctx.type_ctx.get_name(right_type_id)
                raise AnalysisError(f"array elements must have a compatible type, got [{left_name}, {right_name}]", span)
            element_type_id = self.__gcd_literal_type(left_ty.element_type, right_ty.element_type, span)
            return self.__ctx.type_ctx.alloc_array(element_type_id, left_ty.length)

        if isinstance(left_ty, Type.TupleType) and isinstance(right_ty, Type.TupleType):
            if len(left_ty.element_types) != len(right_ty.element_types):
                left_name = self.__ctx.type_ctx.get_name(left_type_id)
                right_name = self.__ctx.type_ctx.get_name(right_type_id)
                raise AnalysisError(f"array elements must have a compatible type, got [{left_name}, {right_name}]", span)
            element_types = [
                self.__gcd_literal_type(left_elem, right_elem, span)
                for left_elem, right_elem in zip(left_ty.element_types, right_ty.element_types)
            ]
            return self.__ctx.type_ctx.alloc_tuple(element_types)

        left_name = self.__ctx.type_ctx.get_name(left_type_id)
        right_name = self.__ctx.type_ctx.get_name(right_type_id)
        raise AnalysisError(f"array elements must have a compatible type, got [{left_name}, {right_name}]", span)

    def __is_literal_type(self, type_id: int) -> bool:
        ty = self.__ctx.type_ctx[type_id]
        match ty:
            case Type.IntLiteralType() | Type.FloatLiteralType():
                return True
            case Type.ArrayType(element_type=element_type):
                return self.__is_literal_type(element_type)
            case Type.TupleType(element_types=element_types):
                return all(self.__is_literal_type(element_type) for element_type in element_types)
            case _:
                return False

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
        raise NotImplementedError()

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
