from __future__ import annotations

from compiler.analysis.error import AnalysisError
from compiler.analysis.lowering.assign_check import build_assign
from compiler.analysis.lowering.builtin_dispatcher import BuiltinDispatcher
from compiler.analysis.lowering.call_dispatcher import CallDispatcher
from compiler.analysis.lowering.closure import ClosureHelper
from compiler.analysis.lowering.op_builder import OpBuilder
from compiler.analysis.lowering.state import LoopFrame, DefinitionState
from compiler.analysis.symbol.symbol import SymbolKind
from compiler.analysis.ty import ty as Type
from compiler.analysis.ty.context import TypeCtx
from compiler.analysis.unit import hir as HIR
from compiler.frontend.lex import token as Tok
from compiler.frontend.lex.position import SrcSpan
from compiler.frontend.parse import ast as AST
from compiler.frontend.parse import ast_type as ASTTy
from compiler.frontend.parse.ast_type import GenericConstExpr, LiteralConstExpr
from compiler.frontend.parse.operator import UnaryOperator
from compiler.utils.log import CompilerLog

# Lazy channel accessors — called at runtime, after CompilerLog is initialised.


def ch_expr():
    return CompilerLog.get("type_check.expr")


def ch_coerce():
    return CompilerLog.get("type_check.coerce")


class ExprChecker:
    """Expression checker and lowering facade.

    For now this is a minimal stub. Concrete implementations should perform
    semantic checks and return `HIR.Expr` carrying HIR nodes and type ids.
    """

    def __init__(self, ctx: DefinitionState):
        self.__ctx = ctx
        self.__call_dispatcher = CallDispatcher(ctx, self)
        self.__builtin_dispatcher = BuiltinDispatcher(ctx, self)
        self.__closure_helper = ClosureHelper(ctx, self)
        self.__op_builder = OpBuilder(ctx, self, self.__call_dispatcher)
        self.__defer_depth = 0

    def value(self, expr: AST.Expr) -> HIR.Expr:
        """Evaluate an expression and return its value (HIR.Expr)."""
        ch_expr().trace(lambda: f"value({type(expr).__name__})")
        match expr:
            # --- control-flow / statement-like expressions ---
            case AST.Block():
                return self.check_block(expr)
            case AST.If():
                return self.lower_if(expr)
            case AST.ComptimeIf():
                return self.lower_comptime_if(expr)
            case AST.Loop():
                return self.lower_loop(expr)
            case AST.Match():
                return self.lower_match(expr)
            case AST.Return():
                return self.lower_return(expr)
            case AST.Break():
                return self.lower_break(expr)
            case AST.Continue():
                return self.lower_continue(expr)
            case AST.Defer():
                return self.lower_defer(expr)
            case AST.Delete():
                return self.lower_delete(expr)
            case AST.VarDecl():
                return self.lower_var_decl(expr)
            case AST.Semi():
                return self.lower_semi(expr)
            case AST.For() | AST.While() | AST.Assert():
                raise AnalysisError(f"Unexpected statement type {type(expr).__name__} after desugaring", expr.span)
            # --- original expression types ---
            case AST.Binary():
                return self.__handle_binary(expr)
            case AST.Unary():
                return self.__handle_unary(expr)
            case AST.FieldAccess():
                return self.__handle_field_access(expr)
            case AST.Call():
                return self.__call_dispatcher.handle_call(expr)
            case AST.Builtin():
                return self.__builtin_dispatcher.handle(expr)
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
            case AST.CompileConfig():
                return HIR.CompileConfig(
                    span=expr.span,
                    name=expr.name,
                    type_id=TypeCtx.bool_id,
                    is_place=False,
                )
            case AST.Literal():
                return self.__handle_literal(expr)
            case AST.Tuple():
                return self.__handle_tuple(expr)
            case AST.Array():
                return self.__handle_array(expr)
            case AST.ArrayRepeat():
                return self.__handle_array_repeat(expr)
            case AST.ClosureExpr():
                return self.__closure_helper.check_closure_expr(expr)

    def __handle_binary(self, node: AST.Binary) -> HIR.Expr:
        return self.__op_builder.build_binary(node.span, node.op, node.left, node.right)

    def __handle_unary(self, node: AST.Unary) -> HIR.Expr:
        return self.__op_builder.build_unary(node.span, node.op, node.operand)

    def __handle_field_access(self, node: AST.FieldAccess) -> HIR.Expr:
        result = self.__op_builder.build_field_access(
            node.span, node.receiver, node.field_name.name
        )
        match result:
            case HIR.FieldAccess(field=field, type_id=type_id):
                self.__ctx.names.record(node.field_name.span, field, type_id)
            case HIR.VariantConstruct(variant=variant, type_id=type_id):
                # A variant without payload is spelled like a field access
                # (`Shape.Point`), so it arrives here rather than as a call.
                self.__ctx.names.record(node.field_name.span, variant, type_id)
            case _:
                pass
        return result

    def __handle_method_call(self, node: AST.MethodCall) -> HIR.Expr:
        result = self.__call_dispatcher.handle_method_call(node)
        match result:
            case HIR.MethodCall(method_id=method_id, type_id=type_id):
                # The method itself, not the receiver's type.
                self.__ctx.names.record(node.method_name.span, method_id, type_id)
            case HIR.TraitObjectMethodCall(method_id=method_id, type_id=type_id):
                self.__ctx.names.record(node.method_name.span, method_id, type_id)
            case HIR.VariantConstruct(variant=variant, type_id=type_id):
                self.__ctx.names.record(node.method_name.span, variant, type_id)
            case _:
                pass
        return result

    def __handle_dyn_value(self, node: AST.DynValue) -> HIR.Expr:
        return self.__op_builder.build_dyn_value(node.span, node.value)

    def __handle_dyn_buffer(self, node: AST.DynBuffer) -> HIR.Expr:
        return self.__op_builder.build_dyn_buffer(node.span, node.element, node.size)

    def __handle_type_item(self, node: AST.TypeItem) -> HIR.Expr:
        assert self.__ctx.symbol_ctx is not None

        generic_arg_ids = [self.resolve_generic_arg(arg) for arg in node.generics]
        # Hardcoded type constructors (Tuple / Fn) have no registered
        # symbol — intercept by name before the symbol lookup.
        type_id = self.__ctx.type_ctx.try_builtin_ctor(node.name.name, generic_arg_ids)
        if type_id is not None:
            return HIR.Ty(span=node.span, type_id=type_id, is_place=False)

        symbol = self.__ctx.symbol_ctx.lookup(node.name.name)
        if symbol is None or symbol.kind not in (SymbolKind.Type, SymbolKind.ConstGeneric, SymbolKind.Function):
            raise AnalysisError(f"Unknown type '{node.name.name}'", node.name.span)

        # A type written in an expression (`Point.new(...)`, `Pair<Meters>.of(...)`)
        # is a reference like any other: recording it is what lets navigation,
        # hover and member completion know what the receiver is.
        self.__ctx.names.record(node.name.span, symbol, symbol.type_id)

        type_id = self.__ctx.type_ctx.alloc_instance(symbol.type_id, generic_arg_ids)
        type_id = self.__ctx.type_ctx.resolve_aliases(type_id)

        if symbol.kind == SymbolKind.Function:
            self.__ctx.report_def(type_id)

        return HIR.Ty(span=node.span, type_id=type_id, is_place=False)

    def resolve_generic_arg(self, arg: AST.ASTType | AST.ConstExpr) -> int:
        """Resolve a generic argument — either a type or a const expression — to a TypeId."""
        match arg:
            case LiteralConstExpr() | GenericConstExpr():
                return self.__resolve_const_expr(arg)
            case _:
                return self.__ctx.resolve_type(arg)

    def __resolve_const_expr(self, const_expr: AST.ConstExpr) -> int:
        """Resolve a ConstExpr to a TypeId."""
        match const_expr:
            case LiteralConstExpr(literal=Tok.IntLiteral(value=v)):
                return self.__ctx.type_ctx.alloc_literal_value(v, self.__ctx.type_ctx.u64_id)
            case GenericConstExpr(name=name):
                assert self.__ctx.symbol_ctx is not None
                symbol = self.__ctx.symbol_ctx.lookup(name.name)
                assert symbol is not None, f"Undefined const generic '{name.name}'"
                return symbol.type_id
            case _:
                raise AnalysisError("Unsupported const expression", const_expr.span)

    def __handle_identifier(self, node: AST.Identifier) -> HIR.Expr:
        assert self.__ctx.symbol_ctx is not None

        symbol = self.__ctx.symbol_ctx.lookup(node.name)
        if symbol is None:
            raise AnalysisError(f"Unknown identifier '{node.name}'", node.span)

        # Record the declaration at the identifier span for navigation and hover.
        match symbol.kind:
            case SymbolKind.Variable:
                self.__ctx.names.record(node.span, symbol, symbol.type_id)
                return HIR.Var(span=node.span, symbol_id=symbol.symbol_id, type_id=symbol.type_id, is_place=True)
            case SymbolKind.Function:
                if self.__ctx.type_ctx.contains_generic(symbol.type_id):
                    raise AnalysisError(
                        f"cannot use generic function '{node.name}' as a value; "
                        f"a function variable must bind a concrete function",
                        node.span,
                    )
                self.__ctx.names.record(node.span, symbol, symbol.type_id)
                self.__ctx.report_def(symbol.type_id)
                return HIR.Ty(span=node.span, type_id=symbol.type_id, is_place=True)
            case SymbolKind.Type | SymbolKind.ConstGeneric:
                type_id = self.__ctx.type_ctx.resolve_aliases(symbol.type_id)
                ty = self.__ctx.type_ctx[type_id]
                if isinstance(ty, Type.LiteralValueType):
                    assert isinstance(ty.value, int)
                    self.__ctx.names.record(node.span, symbol, ty.value_type)
                    return HIR.IntLiteral(span=node.span, value=ty.value, type_id=ty.value_type, is_place=False)
                self.__ctx.names.record(node.span, symbol, type_id)
                return HIR.Ty(span=node.span, type_id=type_id, is_place=False)

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

        length_id = self.__ctx.type_ctx.alloc_literal_value(len(elements), self.__ctx.type_ctx.u64_id)
        type_id = self.__ctx.type_ctx.alloc_array(element_type_id, length_id)
        return HIR.Array(span=node.span, element_type=element_type_id, elements=elements, type_id=type_id, is_place=False)

    def __handle_array_repeat(self, node: AST.ArrayRepeat) -> HIR.Expr:
        element = self.value(node.element)
        element_type_id = self.__ctx.type_ctx.default_literals(element.type_id)

        count_expr = self.value(node.count)
        count_type_id = self.__extract_count_type_id(count_expr, node.span)

        type_id = self.__ctx.type_ctx.alloc_array(element_type_id, count_type_id)
        return HIR.ArrayRepeat(span=node.span, element_type=element_type_id,
                               element=element, type_id=type_id, is_place=False)

    def __extract_count_type_id(self, expr: HIR.Expr, span: SrcSpan) -> int:
        """Extract the length type_id for :meth:`alloc_array` from a count expression.

        - A literal integer creates a fresh ``LiteralValueType``.
        - An already-resolved ``LiteralValueType`` (e.g. from a const generic
          that has been instantiated) is used as-is.
        - A ``HIR.Ty`` whose type is a ``ConstGenericType`` is used as-is
          (generic array ``[T; N]`` — the length will be resolved during
          monomorphisation).
        """
        cty = self.__ctx.type_ctx[expr.type_id]
        if isinstance(cty, Type.LiteralValueType):
            return expr.type_id
        if isinstance(expr, HIR.IntLiteral):
            return self.__ctx.type_ctx.alloc_literal_value(
                expr.value, self.__ctx.type_ctx.u64_id)
        if isinstance(expr, HIR.Ty):
            return expr.type_id
        raise AnalysisError(
            "array repeat count must be a compile-time constant", span)

    def coerce(self, expr: HIR.Expr, expected: int) -> HIR.Expr:
        # A declaration keeps the alias's own type id, so an alias and the type it
        # stands for are two ids for one type: compare (and reshape) in resolved
        # space.  The node keeps the annotated id, i.e. `expected`.
        expected_resolved = self.__ctx.type_ctx.resolve_aliases(expected)
        expr_resolved = self.__ctx.type_ctx.resolve_aliases(expr.type_id)

        if self.__ctx.type_ctx.is_same_type(expr.type_id, expected):
            return expr
        ch_coerce().trace(lambda: f"coerce {self.__ctx.type_ctx.get_name(expr.type_id)} -> {self.__ctx.type_ctx.get_name(expected)}")

        # never is subtype of everything — no coercion needed
        if expr.type_id == TypeCtx.never_id:
            return expr

        expected_ty = self.__ctx.type_ctx[expected_resolved]
        expr_ty = self.__ctx.type_ctx[expr_resolved]

        if isinstance(expected_ty, Type.TraitObjectType):
            trait_type_id = expected_ty.trait_type_id
            self.__ctx.type_ctx.check_trait_object_safe(trait_type_id, expr.span)
            if not isinstance(expr_ty, (Type.PointerType, Type.RefType)):
                raise AnalysisError(
                    f"trait object '{self.__ctx.type_ctx.get_name(expected)}' requires a concrete pointer or reference, "
                    f"got '{self.__ctx.type_ctx.get_name(expr.type_id)}'",
                    expr.span,
                )
            concrete_type_id = expr_ty.pointee_type
            concrete_resolved = self.__ctx.type_ctx.resolve_aliases(concrete_type_id)
            if isinstance(self.__ctx.type_ctx[concrete_resolved], Type.TraitObjectType):
                raise AnalysisError("trait-object upcasts are not supported", expr.span)
            if isinstance(expr, HIR.Unary) and expr.op == UnaryOperator.AddrOf and not expr.operand.is_place:
                raise AnalysisError("cannot create a trait object from a temporary value", expr.span)
            impl_match = self.__ctx.type_ctx.get_trait_impl(concrete_type_id, trait_type_id)
            if impl_match is None:
                raise AnalysisError(
                    f"type '{self.__ctx.type_ctx.get_name(concrete_type_id)}' does not implement "
                    f"'{self.__ctx.type_ctx.get_name(trait_type_id)}'",
                    expr.span,
                )
            impl, substitutions = impl_match
            trait_ty = self.__ctx.type_ctx[self.__ctx.type_ctx.resolve_aliases(trait_type_id)]
            assert isinstance(trait_ty, Type.TraitType)
            method_ids: list[int] = []
            for method_name, trait_method_id in self.__ctx.type_ctx.get_trait_methods(trait_type_id).items():
                trait_method_ty = self.__ctx.type_ctx[trait_method_id]
                assert isinstance(trait_method_ty, Type.MethodType)
                if trait_method_ty.custom_def.is_static:
                    continue
                impl_method_id = impl.methods[method_name]
                concrete_method_id = self.__ctx.type_ctx.canonical(
                    self.__ctx.type_ctx.instantiate(impl_method_id, substitutions)
                )
                method_ids.append(concrete_method_id)
                self.__ctx.report_def(concrete_method_id)
            return HIR.TraitObjectCoerce(
                span=expr.span,
                value=expr,
                concrete_type_id=concrete_type_id,
                trait_type_id=trait_type_id,
                method_ids=method_ids,
                type_id=expected,
                is_place=False,
            )

        # Array decay: T[N] -> T*.  Take the address of an array value and
        # re-anchor the resulting T[N]* at its first element.
        if (
            isinstance(expected_ty, Type.PointerType)
            and isinstance(expr_ty, Type.ArrayType)
            and expected_resolved == self.__ctx.type_ctx.alloc_pointer(expr_ty.element_type)
        ):
            addr = HIR.Unary(
                span=expr.span,
                op=UnaryOperator.AddrOf,
                operand=expr,
                type_id=self.__ctx.type_ctx.alloc_pointer(expr.type_id),
                is_place=False,
            )
            return HIR.BitCast(
                span=expr.span,
                value=addr,
                target_type=expected,
                type_id=expected,
                is_place=False,
            )

        # Re-anchor T[N]* -> T* without copying the array.
        if isinstance(expected_ty, Type.PointerType) and isinstance(expr_ty, Type.PointerType):
            pointee_ty = self.__ctx.type_ctx[expr_ty.pointee_type]
            if isinstance(pointee_ty, Type.ArrayType) and expected_resolved == self.__ctx.type_ctx.alloc_pointer(pointee_ty.element_type):
                return HIR.BitCast(
                    span=expr.span,
                    value=expr,
                    target_type=expected,
                    type_id=expected,
                    is_place=False,
                )

        # Tiered-pointer degradation (docs/security.md §tiered-pointers):
        # explicit annotation downgrades along T* → T[] → T&. The value-level
        # representation (dropping index/size fields) is codegen's job — here we
        # relabel via BitCast and let codegen re-shape the value.
        if isinstance(expr_ty, Type.PointerType) and isinstance(expected_ty, Type.SliceType) and expected_resolved == self.__ctx.type_ctx.alloc_slice(expr_ty.pointee_type):
            # Raw pointer mode: a bare `T*` carries no length, so downgrading it
            # to `T[]` would fabricate a size out of thin air. Reject it and ask
            # the user to materialize the slice explicitly.
            if self.__ctx.type_ctx.raw_pointers:
                raise AnalysisError(
                    f"cannot coerce '{self.__ctx.type_ctx.get_name(expr.type_id)}' to '{self.__ctx.type_ctx.get_name(expected)}' in raw pointer mode; "
                    "implicit pointer-to-slice conversion is not supported",
                    expr.span,
                )
            return HIR.BitCast(span=expr.span, value=expr, target_type=expected, type_id=expected, is_place=False)
        # `T* → U&`: pointee 本身也可以 coerce(字面量定型)。先把被取址的表达式收敛到
        # 期望的 pointee(取址结果随之成为 `U*`), 再走下面的 `T* → T&` 降级。否则
        # `&<字面量>` 得到的 `T*` 会原样带进引用里, 临时量按字面量类型物化, 读出来是垃圾。
        if isinstance(expr_ty, Type.PointerType) and isinstance(expected_ty, Type.RefType) \
                and not self.__ctx.type_ctx.is_same_type(expr_ty.pointee_type, expected_ty.pointee_type) \
                and isinstance(expr, HIR.Unary) and expr.op == UnaryOperator.AddrOf:
            expr.operand = self.coerce(expr.operand, expected_ty.pointee_type)
            expr.type_id = self.__ctx.type_ctx.alloc_pointer(expected_ty.pointee_type)
            expr_resolved = self.__ctx.type_ctx.resolve_aliases(expr.type_id)
            expr_ty = self.__ctx.type_ctx[expr_resolved]
        if isinstance(expr_ty, Type.PointerType) and isinstance(expected_ty, Type.RefType) \
                and expected_resolved == self.__ctx.type_ctx.alloc_ref(expr_ty.pointee_type):
            return HIR.BitCast(
                span=expr.span,
                value=expr,
                target_type=expected,
                type_id=expected,
                is_place=False,
            )

        if isinstance(expr_ty, Type.SliceType) and isinstance(expected_ty, Type.RefType) \
                and expected_resolved == self.__ctx.type_ctx.alloc_ref(expr_ty.element_type):
            return HIR.BitCast(
                span=expr.span,
                value=expr,
                target_type=expected,
                type_id=expected,
                is_place=False,
            )

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
            case HIR.CharLiteral() | HIR.StrLiteral() | HIR.BoolLiteral() | HIR.Var() | HIR.Ty() | HIR.Closure():
                if not self.__ctx.type_ctx.is_same_type(expr.type_id, expected):
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
            case HIR.ArrayRepeat():
                if not isinstance(expected_ty, Type.ArrayType):
                    raise AnalysisError(f"Expected array type but got '{self.__ctx.type_ctx.get_name(expected)}'", expr.span)
                expr.element = self.coerce(expr.element, expected_ty.element_type)
                expr.element_type = expected_ty.element_type
                expr.type_id = expected
                return expr
            case HIR.Binary():
                if isinstance(expr_ty, (Type.IntLiteralType, Type.FloatLiteralType)):
                    expr.left = self.coerce(expr.left, expected)
                    expr.right = self.coerce(expr.right, expected)
                    expr.type_id = expected
                return expr
            case HIR.Unary():
                if isinstance(expr_ty, (Type.IntLiteralType, Type.FloatLiteralType)):
                    expr.operand = self.coerce(expr.operand, expected)
                    expr.type_id = expected
                elif isinstance(expr_ty, Type.PointerType) and isinstance(expected_ty, Type.PointerType):
                    expr.operand = self.coerce(expr.operand, expected_ty.pointee_type)
                    expr.type_id = expected
                elif expr.type_id != expected:
                    raise AnalysisError(
                        f"Expected type '{self.__ctx.type_ctx.get_name(expected)}' "
                        f"but got '{self.__ctx.type_ctx.get_name(expr.type_id)}'",
                        expr.span,
                    )
                return expr
            case HIR.DynValue():
                if not isinstance(expected_ty, Type.PointerType):
                    raise AnalysisError(f"Expected pointer type for dynamic value, got '{self.__ctx.type_ctx.get_name(expected)}'", expr.span)
                expr.value = self.coerce(expr.value, expected_ty.pointee_type)
                expr.type_id = expected
                return expr
            case HIR.DynBuffer():
                if not isinstance(expected_ty, Type.PointerType):
                    raise AnalysisError(f"Expected pointer type for dynamic buffer, got '{self.__ctx.type_ctx.get_name(expected)}'", expr.span)
                if expr.element is None:
                    # ZST element: nothing to initialize, pointee must match as written.
                    if not self.__ctx.type_ctx.is_same_type(expr.element_type, expected_ty.pointee_type):
                        raise AnalysisError(
                            f"Expected type '{self.__ctx.type_ctx.get_name(expected)}' but got '{self.__ctx.type_ctx.get_name(expr.type_id)}'",
                            expr.span,
                        )
                else:
                    expr.element = self.coerce(expr.element, expected_ty.pointee_type)
                    expr.element_type = expected_ty.pointee_type
                expr.type_id = expected
                return expr
            case HIR.Block() | HIR.If() | HIR.ComptimeIf() | HIR.Loop() | HIR.Match():
                # Expression-typed control flow: coerce by updating type_id
                # (literal types → concrete types, etc.)
                if isinstance(expr_ty, (Type.IntLiteralType, Type.FloatLiteralType)):
                    if not isinstance(expected_ty, (Type.IntType, Type.FloatType, Type.IntLiteralType, Type.FloatLiteralType)):
                        raise AnalysisError(
                            f"cannot coerce literal-typed block/if/loop/match to '{self.__ctx.type_ctx.get_name(expected)}'",
                            expr.span)
                    expr.type_id = expected
                elif expr.type_id != expected:
                    raise AnalysisError(
                        f"Expected type '{self.__ctx.type_ctx.get_name(expected)}' "
                        f"but got '{self.__ctx.type_ctx.get_name(expr.type_id)}'",
                        expr.span,
                    )
                return expr
            case _:
                if expr.type_id != expected:
                    raise AnalysisError(
                        f"Expected type '{self.__ctx.type_ctx.get_name(expected)}' "
                        f"but got '{self.__ctx.type_ctx.get_name(expr.type_id)}'",
                        expr.span,
                    )
                return expr

    def call_method(self, receiver: HIR.Expr, method_name: str, generic_args: list[int] | None, args: list[HIR.Expr]) -> HIR.Expr:
        return self.__call_dispatcher.dispatch_method_call(receiver.span, receiver, method_name, generic_args, args, "method call")

    def call_into_iter(self, iterable: HIR.Expr) -> HIR.Expr:
        return self.call_method(iterable, "into_iter", None, [])

    def call_next(self, iterator: HIR.Expr) -> HIR.Expr:
        return self.call_method(iterator, "next", None, [])

    def call_eq(self, lhs: HIR.Expr, rhs: HIR.Expr) -> HIR.Expr:
        rhs_ptr = HIR.Unary(span=rhs.span, op=UnaryOperator.AddrOf, operand=rhs, type_id=self.__ctx.type_ctx.alloc_pointer(rhs.type_id), is_place=False)
        return self.call_method(lhs, "eq", None, [rhs_ptr])

    def assign(self, span: SrcSpan, target: HIR.Expr, value: HIR.Expr) -> HIR.Binary:
        return build_assign(self.__ctx.type_ctx, self.coerce, span, target, value)

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

    # ------------------------------------------------------------------
    # block / control-flow lowering (expression-oriented)
    # ------------------------------------------------------------------

    def check_block(self, ast_block: AST.Block) -> HIR.Block:
        self.__ctx.enter_scope()
        stmts: list[HIR.Expr] = []
        try:
            for stmt in ast_block.stmts:
                stmts.append(self.value(stmt))
        finally:
            self.__ctx.exit_scope()
        block_type_id = stmts[-1].type_id if stmts else TypeCtx.void_id
        return HIR.Block(span=ast_block.span, stmts=stmts, type_id=block_type_id, is_place=False)

    def lower_var_decl(self, stmt: AST.VarDecl) -> HIR.Let:
        assert self.__ctx.symbol_ctx is not None

        if isinstance(stmt.var_type, ASTTy.DeducedType):
            if stmt.init_expr is None:
                raise AnalysisError("cannot infer the type of a variable without an initializer", stmt.span)
            init_expr = self.value(stmt.init_expr)
            var_type_id = self.__ctx.type_ctx.default_literals(init_expr.type_id)
            init_expr = self.coerce(init_expr, var_type_id)
        else:
            var_type_id = self.__ctx.resolve_type(stmt.var_type)
            resolved_var_type = self.__ctx.type_ctx.resolve_aliases(var_type_id)
            if isinstance(self.__ctx.type_ctx[resolved_var_type], Type.TraitType):
                raise AnalysisError("trait types are unsized; use 'Trait&' for a trait object", stmt.span)
            init_expr = self.coerce(self.value(stmt.init_expr), var_type_id) if stmt.init_expr is not None else None

        symbol_id = self.__declare_local_symbol(stmt.name, var_type_id)

        init_hir: HIR.Expr | None = None
        if init_expr is not None:
            var = HIR.Var(span=stmt.name.span, symbol_id=symbol_id, type_id=var_type_id, is_place=True)
            init_hir = self.assign(stmt.span, var, init_expr)

        return HIR.Let(span=stmt.span, init=init_hir, type_id=TypeCtx.void_id, is_place=False, symbol_id=symbol_id)

    def lower_semi(self, stmt: AST.Semi) -> HIR.Expr:
        expr = self.value(stmt.expr)
        # Divergent expressions (return/break/continue/panic) should not be
        # wrapped in Semi — their never-typed semantics must propagate.
        if expr.type_id == TypeCtx.never_id:
            return expr
        return HIR.Semi(span=stmt.span, expr=expr, type_id=TypeCtx.void_id, is_place=False)

    def lower_if(self, stmt: AST.If) -> HIR.If:
        assert self.__ctx.symbol_ctx is not None

        if stmt.elif_branches:
            raise AnalysisError("Unexpected elif branches after desugaring", stmt.span)

        cond_expr = self.coerce(self.value(stmt.condition), TypeCtx.bool_id)
        then_block = self.check_block(stmt.then_branch)
        else_block = self.check_block(stmt.else_branch) if stmt.else_branch is not None else None

        branch_types = [then_block.type_id]
        if else_block is not None:
            branch_types.append(else_block.type_id)
        else:
            branch_types.append(TypeCtx.void_id)
        if_type_id = self.__ctx.type_ctx.merge_types(branch_types, stmt.span)
        if_type_id = self.__ctx.type_ctx.default_literals(if_type_id)

        return HIR.If(span=stmt.span, cond=cond_expr, then_branch=then_block, else_branch=else_block, type_id=if_type_id, is_place=False)

    def lower_comptime_if(self, stmt: AST.ComptimeIf) -> HIR.ComptimeIf:
        cond_expr = self.coerce(self.value(stmt.condition), TypeCtx.bool_id)
        then_block = self.check_block(stmt.then_branch)
        else_block = self.check_block(stmt.else_branch)
        if_type_id = self.__ctx.type_ctx.merge_types([then_block.type_id, else_block.type_id], stmt.span)
        if_type_id = self.__ctx.type_ctx.default_literals(if_type_id)

        return HIR.ComptimeIf(
            span=stmt.span,
            cond=cond_expr,
            then_branch=then_block,
            else_branch=else_block,
            type_id=if_type_id,
            is_place=False,
        )

    def lower_loop(self, stmt: AST.Loop) -> HIR.Loop:
        loop_frame = LoopFrame(span=stmt.span)
        self.__ctx.push_loop(loop_frame)
        try:
            body_block = self.check_block(stmt.body)
            if not loop_frame.break_value_type_ids:
                loop_type_id = TypeCtx.never_id
            else:
                loop_type_id = self.__ctx.type_ctx.merge_types(
                    loop_frame.break_value_type_ids, stmt.span)
                loop_type_id = self.__ctx.type_ctx.default_literals(loop_type_id)
            return HIR.Loop(span=stmt.span, body=body_block, type_id=loop_type_id, is_place=False)
        finally:
            self.__ctx.pop_loop()

    def lower_match(self, stmt: AST.Match) -> HIR.Expr:
        assert self.__ctx.symbol_ctx is not None
        value_expr = self.value(stmt.expr)
        value_expr.type_id = self.__ctx.type_ctx.default_literals(value_expr.type_id)
        value_type = self.__ctx.type_ctx[self.__ctx.type_ctx.resolve_aliases(value_expr.type_id)]
        if isinstance(value_type, Type.PointerType):
            if not isinstance(value_expr, HIR.Unary) or value_expr.op != UnaryOperator.AddrOf:
                raise AnalysisError("match does not accept pointer scrutinees; use '&value' or a reference", stmt.expr.span)
            value_expr = self.coerce(value_expr, self.__ctx.type_ctx.alloc_ref(value_type.pointee_type))
            value_type = self.__ctx.type_ctx[self.__ctx.type_ctx.resolve_aliases(value_expr.type_id)]
        is_ref = isinstance(value_type, Type.RefType)
        matched_type_id = value_type.pointee_type if is_ref else value_expr.type_id
        root_enum = isinstance(self.__ctx.type_ctx[self.__ctx.type_ctx.resolve_aliases(matched_type_id)], Type.EnumType)
        arms: list[HIR.MatchArm] = []
        body_types: list[int] = []
        for arm in stmt.arms:
            self.__ctx.enter_scope()
            try:
                bindings: dict[str, int] = {}
                pattern = self.__check_pattern(arm.pattern, matched_type_id, root_enum, is_ref, bindings, set(), False)
                guard = self.coerce(self.value(arm.guard), TypeCtx.bool_id) if arm.guard is not None else None
                body = self.check_block(arm.body)
                arms.append(HIR.MatchArm(span=arm.span, pattern=pattern, guard=guard, body=body))
                body_types.append(body.type_id)
            finally:
                self.__ctx.exit_scope()
        match_type_id = self.__ctx.type_ctx.merge_types(body_types, stmt.span) if body_types else TypeCtx.never_id
        match_type_id = self.__ctx.type_ctx.default_literals(match_type_id)
        return HIR.Match(span=stmt.span, value=value_expr, arms=arms, type_id=match_type_id, is_place=False, is_ref=is_ref)

    def lower_return(self, stmt: AST.Return) -> HIR.Return:
        assert self.__ctx.symbol_ctx is not None

        if self.__defer_depth > 0:
            raise AnalysisError("'return' is not allowed inside a deferred action", stmt.span)

        return_type_id = self.__ctx.current_return_type()
        if return_type_id is None:
            raise AnalysisError("return is not allowed outside of a function or method", stmt.span)

        if stmt.expr is None:
            if return_type_id != TypeCtx.void_id:
                raise AnalysisError("missing return value", stmt.span)
            return HIR.Return(span=stmt.span, value=None, type_id=TypeCtx.never_id, is_place=False)

        if return_type_id == TypeCtx.void_id:
            raise AnalysisError("void function cannot return a value", stmt.expr.span)

        value_expr = self.coerce(self.value(stmt.expr), return_type_id)
        return HIR.Return(span=stmt.span, value=value_expr, type_id=TypeCtx.never_id, is_place=False)

    def lower_break(self, stmt: AST.Break) -> HIR.Break:
        if self.__defer_depth > 0:
            raise AnalysisError("'break' is not allowed inside a deferred action", stmt.span)

        if not self.__ctx.loop_stack:
            raise AnalysisError("'break' is only allowed inside a loop", stmt.span)

        loop_frame = self.__ctx.loop_stack[-1]
        value: HIR.Expr | None = None
        if stmt.expr is not None:
            value = self.value(stmt.expr)
            loop_frame.break_value_type_ids.append(value.type_id)
        else:
            loop_frame.break_value_type_ids.append(TypeCtx.void_id)

        return HIR.Break(span=stmt.span, type_id=TypeCtx.never_id, is_place=False, value=value)

    def lower_continue(self, stmt: AST.Continue) -> HIR.Continue:
        if self.__defer_depth > 0:
            raise AnalysisError("'continue' is not allowed inside a deferred action", stmt.span)

        if not self.__ctx.loop_stack:
            raise AnalysisError("'continue' is only allowed inside a loop", stmt.span)

        return HIR.Continue(span=stmt.span, type_id=TypeCtx.never_id, is_place=False)

    def lower_defer(self, stmt: AST.Defer) -> HIR.Defer:
        if self.__defer_depth > 0:
            raise AnalysisError("nested defer is not allowed inside a deferred action", stmt.span)

        self.__defer_depth += 1
        try:
            action = self.value(stmt.action)
        finally:
            self.__defer_depth -= 1

        return HIR.Defer(
            span=stmt.span,
            action=action,
            type_id=TypeCtx.void_id,
            is_place=False,
        )

    def lower_delete(self, stmt: AST.Delete) -> HIR.Delete:
        assert self.__ctx.symbol_ctx is not None

        target_expr = self.value(stmt.target)
        target_type = self.__ctx.type_ctx[target_expr.type_id]
        if not isinstance(target_type, (Type.PointerType, Type.SliceType, Type.RefType)):
            # del-view: T*/T[]/T& 均可作 del 目标(释放动作三族通用,is_raw
            # data 分量运行期拦截偏移释放);其余类型仍拒绝——含 StrType(不可达
            # 防御:str 始终为值类型,不会以指针形态出现)
            raise AnalysisError("delete target must be a pointer, slice, or reference expression", stmt.target.span)
        return HIR.Delete(span=stmt.span, target=target_expr, type_id=TypeCtx.void_id, is_place=False)

    def __declare_local_symbol(self, name: AST.Identifier, type_id: int) -> int:
        assert self.__ctx.symbol_ctx is not None

        symbol_id = self.__ctx.symbol_ctx.add_symbol(name.name, SymbolKind.Variable, type_id, span=name.span)
        if symbol_id is None:
            raise AnalysisError(f"Variable '{name.name}' is already defined in the current scope", name.span)
        self.__ctx.push_local(symbol_id)
        return symbol_id

    def __check_pattern(
        self, pat: AST.Pattern, type_id: int, root_enum: bool, by_ref: bool,
        bindings: dict[str, int], seen: set[str], reuse: bool,
    ) -> HIR.Pattern:
        ctx = self.__ctx.type_ctx
        ty = ctx[ctx.resolve_aliases(type_id)]
        match pat:
            case AST.WildcardPattern():
                return HIR.WildcardPattern(pat.span, type_id)
            case AST.NamePattern():
                if root_enum:
                    return self.__check_variant(pat.span, pat.name, None, None, False, type_id, by_ref, bindings, seen, reuse)
                return self.__bind_pattern(pat.name, HIR.WildcardPattern(pat.span, type_id), type_id, by_ref, bindings, seen, reuse)
            case AST.BindPattern():
                inner = self.__check_pattern(pat.inner, type_id, root_enum, by_ref, bindings, seen, reuse)
                return self.__bind_pattern(pat.name, inner, type_id, by_ref, bindings, seen, reuse)
            case AST.OrPattern():
                original = set(seen)
                first_seen = set(seen)
                alternatives = [self.__check_pattern(pat.alternatives[0], type_id, root_enum, by_ref, bindings, first_seen, reuse)]
                expected = first_seen - original
                for alternative in pat.alternatives[1:]:
                    branch_seen = set(original)
                    alternatives.append(self.__check_pattern(alternative, type_id, root_enum, by_ref, bindings, branch_seen, True))
                    if branch_seen - original != expected:
                        raise AnalysisError("OR alternatives must bind the same names", alternative.span)
                seen.update(expected)
                return HIR.OrPattern(pat.span, type_id, alternatives)
            case AST.LiteralPattern():
                return self.__check_literal_pattern(pat, type_id)
            case AST.RangePattern():
                if not isinstance(ty, (Type.IntType, Type.CharType)):
                    raise AnalysisError("Range pattern requires an integer or char", pat.span)
                lower = self.__pattern_endpoint(pat.lower, type_id)
                upper = self.__pattern_endpoint(pat.upper, type_id)
                if lower > upper:
                    raise AnalysisError("Range lower bound exceeds upper bound", pat.span)
                return HIR.RangePattern(pat.span, type_id, lower, upper)
            case AST.ConstructPattern():
                if isinstance(ty, Type.EnumType):
                    if pat.qualifier is not None and not ctx.is_same_type(self.__ctx.resolve_type(pat.qualifier), type_id):
                        raise AnalysisError("Enum pattern qualifier does not match the scrutinee type", pat.span)
                    return self.__check_variant(pat.span, pat.name, pat.positional, pat.named, pat.rest, type_id, by_ref, bindings, seen, reuse)
                if isinstance(ty, Type.StructType):
                    named_type = pat.qualifier if pat.qualifier is not None else ASTTy.NamedType(span=pat.name.span, name=pat.name)
                    if not ctx.is_same_type(self.__ctx.resolve_type(named_type), type_id):
                        raise AnalysisError("Struct pattern type does not match the scrutinee type", pat.span)
                    if pat.positional is not None and pat.positional:
                        raise AnalysisError("Struct patterns use named fields", pat.span)
                    fields = self.__check_named_fields(pat.named or [], ty, pat.rest, by_ref, bindings, seen, reuse, pat.span)
                    return HIR.StructPattern(pat.span, type_id, fields)
                raise AnalysisError("Constructor pattern requires a struct or enum", pat.span)
            case AST.TuplePattern():
                if not isinstance(ty, Type.TupleType) or len(ty.element_types) != len(pat.elements):
                    raise AnalysisError("Tuple pattern has the wrong type or arity", pat.span)
                elements = [self.__check_pattern(sub, sub_type, False, by_ref, bindings, seen, reuse)
                            for sub, sub_type in zip(pat.elements, ty.element_types)]
                return HIR.TuplePattern(pat.span, type_id, elements)
            case AST.SequencePattern():
                if not isinstance(ty, (Type.ArrayType, Type.SliceType)):
                    raise AnalysisError("Sequence pattern requires an array or slice", pat.span)
                element_type = ty.element_type
                prefix = [self.__check_pattern(sub, element_type, False, by_ref, bindings, seen, reuse) for sub in pat.prefix]
                suffix = [self.__check_pattern(sub, element_type, False, by_ref, bindings, seen, reuse) for sub in pat.suffix]
                return HIR.SequencePattern(pat.span, type_id, prefix, suffix, pat.rest)
            case _:
                raise AnalysisError(f"Unsupported pattern type {type(pat).__name__}", pat.span)

    def __bind_pattern(
        self, name: AST.Identifier, inner: HIR.Pattern, type_id: int, by_ref: bool,
        bindings: dict[str, int], seen: set[str], reuse: bool,
    ) -> HIR.BindPattern:
        if name.name in seen:
            raise AnalysisError(f"Duplicate pattern binding '{name.name}'", name.span)
        seen.add(name.name)
        binding_type = self.__ctx.type_ctx.alloc_ref(type_id) if by_ref else type_id
        if reuse:
            symbol_id = bindings.get(name.name)
            if symbol_id is None or self.__ctx.symbol_ctx is None or not self.__ctx.type_ctx.is_same_type(self.__ctx.symbol_ctx.get(symbol_id).type_id, binding_type):
                raise AnalysisError(f"OR binding '{name.name}' has a different type or mode", name.span)
        else:
            symbol_id = self.__declare_local_symbol(name, binding_type)
            bindings[name.name] = symbol_id
        return HIR.BindPattern(name.span, type_id, symbol_id, inner)

    def __check_literal_pattern(self, pat: AST.LiteralPattern, type_id: int) -> HIR.LiteralPattern:
        ty = self.__ctx.type_ctx[self.__ctx.type_ctx.resolve_aliases(type_id)]
        literal = pat.literal
        if isinstance(literal, Tok.IntLiteral):
            self.__check_integer_literal(literal, type_id)
            return HIR.LiteralPattern(pat.span, type_id, literal.value)
        if isinstance(literal, Tok.CharLiteral) and isinstance(ty, Type.CharType):
            return HIR.LiteralPattern(pat.span, type_id, literal.value)
        if isinstance(literal, Tok.BoolLiteral) and isinstance(ty, Type.BoolType):
            return HIR.LiteralPattern(pat.span, type_id, literal.value)
        if isinstance(literal, Tok.StrLiteral) and isinstance(ty, Type.StrType):
            hidden = AST.Identifier(span=pat.span, name=f"$match_condition_{id(pat)}")
            symbol_id = self.__declare_local_symbol(hidden, type_id)
            value = HIR.Var(span=pat.span, symbol_id=symbol_id, type_id=type_id, is_place=True)
            expected = HIR.StrLiteral(span=pat.span, value=literal.value, type_id=type_id, is_place=False)
            condition = self.call_eq(value, expected)
            return HIR.LiteralPattern(pat.span, type_id, literal.value, condition, symbol_id)
        raise AnalysisError("Literal pattern does not match the scrutinee type", pat.span)

    def __check_integer_value(self, value: int, ty: Type.Ty, span: SrcSpan) -> None:
        if not isinstance(ty, Type.IntType):
            raise AnalysisError("Integer pattern requires an integer", span)
        bits = ty.size * 8
        minimum = -(1 << (bits - 1)) if ty.signed else 0
        maximum = (1 << (bits - 1)) - 1 if ty.signed else (1 << bits) - 1
        if not minimum <= value <= maximum:
            raise AnalysisError("Integer pattern is outside the scrutinee type's range", span)

    def __check_integer_literal(self, literal: Tok.IntLiteral, type_id: int) -> None:
        ctx = self.__ctx.type_ctx
        if literal.suffix is not None:
            intrinsic = Type.IntrinsicType.from_str(literal.suffix)
            if intrinsic is None:
                raise AnalysisError(f"Unknown intrinsic type suffix '{literal.suffix}'", literal.span)
            if not ctx.is_same_type(TypeCtx.intrinsic_type(intrinsic), type_id):
                raise AnalysisError("Integer pattern suffix does not match the scrutinee type", literal.span)
        self.__check_integer_value(literal.value, ctx[ctx.resolve_aliases(type_id)], literal.span)

    def __pattern_endpoint(self, literal: Tok.IntLiteral | Tok.CharLiteral, type_id: int) -> int:
        ty = self.__ctx.type_ctx[self.__ctx.type_ctx.resolve_aliases(type_id)]
        if isinstance(literal, Tok.IntLiteral):
            self.__check_integer_literal(literal, type_id)
            return literal.value
        if not isinstance(ty, Type.CharType):
            raise AnalysisError("Character range requires a char scrutinee", literal.span)
        return ord(literal.value)

    def __check_variant(
        self, span: SrcSpan, name: AST.Identifier, positional: list[AST.Pattern] | None,
        named: list[AST.FieldPattern] | None, rest: bool, type_id: int, by_ref: bool,
        bindings: dict[str, int], seen: set[str], reuse: bool,
    ) -> HIR.EnumPattern:
        ctx = self.__ctx.type_ctx
        enum_ty = ctx[ctx.resolve_aliases(type_id)]
        assert isinstance(enum_ty, Type.EnumType)
        variant = enum_ty.get_variant_by_name(name.name, ctx)
        if variant is None:
            raise AnalysisError(f"Unknown enum variant '{name.name}'", name.span)
        self.__ctx.names.record(name.span, variant)
        if positional is None and named is None and not rest:
            return HIR.EnumPattern(span, variant, type_id, None)
        if variant.payload_type is None:
            raise AnalysisError(f"Variant '{name.name}' has no payload", span)
        payload = ctx[ctx.resolve_aliases(variant.payload_type)]
        assert isinstance(payload, Type.StructType)
        fields = payload.get_fields(ctx)
        if positional is not None:
            if len(positional) != len(fields):
                raise AnalysisError("Positional payload pattern has the wrong arity", span)
            checked: list[tuple[int, HIR.Pattern]] = []
            for sub, field in zip(positional, fields):
                self.__op_builder.check_field_visible(payload, field, sub.span)
                checked.append((field.index, self.__check_pattern(sub, field.type_id, False, by_ref, bindings, seen, reuse)))
            return HIR.EnumPattern(span, variant, type_id, checked)
        checked = self.__check_named_fields(named or [], payload, rest, by_ref, bindings, seen, reuse, span)
        return HIR.EnumPattern(span, variant, type_id, checked)

    def __check_named_fields(
        self, fields: list[AST.FieldPattern], ty: Type.StructType, rest: bool,
        by_ref: bool, bindings: dict[str, int], seen: set[str], reuse: bool, span: SrcSpan,
    ) -> list[tuple[int, HIR.Pattern]]:
        all_fields = ty.get_fields(self.__ctx.type_ctx)
        used: set[str] = set()
        checked: list[tuple[int, HIR.Pattern]] = []
        for entry in fields:
            if entry.name.name in used:
                raise AnalysisError(f"Duplicate field '{entry.name.name}' in pattern", entry.span)
            used.add(entry.name.name)
            field = ty.get_field_by_name(entry.name.name, self.__ctx.type_ctx)
            if field is None:
                raise AnalysisError(f"Unknown field '{entry.name.name}' in pattern", entry.span)
            self.__op_builder.check_field_visible(ty, field, entry.span)
            checked.append((field.index, self.__check_pattern(entry.pattern, field.type_id, False, by_ref, bindings, seen, reuse)))
        if not rest and len(used) != len(all_fields):
            raise AnalysisError("Field pattern must list every field or end with '..'", span)
        return checked
