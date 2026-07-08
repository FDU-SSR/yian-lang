from __future__ import annotations

from compiler.analysis.error import AnalysisError
from compiler.analysis.lowering.assign_check import (
    build_assign, check_simple_assign_source)
from compiler.analysis.lowering.call_dispatcher import CallDispatcher
from compiler.analysis.lowering.op_builder import OpBuilder
from compiler.analysis.lowering.sem_ctx import LoopFrame, SemCtx
from compiler.analysis.symbol.symbol import SymbolKind
from compiler.analysis.ty import ty as Type
from compiler.analysis.ty.context import TypeCtx
from compiler.analysis.unit import hir as HIR
from compiler.frontend.lex import token as Tok
from compiler.frontend.lex.position import SrcSpan
from compiler.frontend.parse import ast as AST
from compiler.frontend.parse import ast_type as ASTTy
from compiler.frontend.parse.ast_type import GenericConstExpr, LiteralConstExpr
from compiler.frontend.parse.operator import BinaryOperator, UnaryOperator
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

    def __init__(self, ctx: SemCtx):
        self.__ctx = ctx
        self.__call_dispatcher = CallDispatcher(ctx, self)
        self.__op_builder = OpBuilder(ctx, self, self.__call_dispatcher)

    def value(self, expr: AST.Expr) -> HIR.Expr:
        """Evaluate an expression and return its value (HIR.Expr)."""
        ch_expr().trace(lambda: f"value({type(expr).__name__})")
        match expr:
            # --- control-flow / statement-like expressions ---
            case AST.Block():
                return self.check_block(expr)
            case AST.If():
                return self.lower_if(expr)
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
            case AST.ArrayRepeat():
                return self.__handle_array_repeat(expr)

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
        if symbol is None or symbol.kind not in (SymbolKind.Type, SymbolKind.ConstGeneric, SymbolKind.Function):
            raise AnalysisError(f"Unknown type '{node.name.name}'", node.name.span)

        generic_arg_ids = [self.resolve_generic_arg(arg) for arg in node.generics]
        type_id = self.__ctx.type_ctx.resolve_aliases(symbol.type_id)
        if generic_arg_ids:
            type_id = self.__ctx.type_ctx.alloc_instance(type_id, generic_arg_ids)

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

        match symbol.kind:
            case SymbolKind.Variable:
                return HIR.Var(span=node.span, symbol_id=symbol.symbol_id, type_id=symbol.type_id, is_place=True)
            case SymbolKind.Function:
                self.__ctx.report_def(symbol.type_id)
                return HIR.Ty(span=node.span, type_id=symbol.type_id, is_place=True)
            case SymbolKind.Type | SymbolKind.ConstGeneric:
                ty = self.__ctx.type_ctx[symbol.type_id]
                if isinstance(ty, Type.LiteralValueType):
                    assert isinstance(ty.value, int)
                    return HIR.IntLiteral(span=node.span, value=ty.value, type_id=ty.value_type, is_place=False)
                return HIR.Ty(span=node.span, type_id=symbol.type_id, is_place=False)

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
            case Tok.NullptrLiteral():
                return HIR.NullptrLiteral(span=node.span, type_id=TypeCtx.null_ptr_id, is_place=False)

    def __handle_tuple(self, node: AST.Tuple) -> HIR.Expr:
        elements = [self.value(element) for element in node.elements]
        for element in elements:
            if not self.__ctx.type_ctx.is_simple_type(element.type_id):
                check_simple_assign_source(element, self.__ctx.type_ctx, node.span)
        type_id = self.__ctx.type_ctx.alloc_tuple([element.type_id for element in elements])
        return HIR.Tuple(span=node.span, field_values=elements, type_id=type_id, is_place=False)

    def __handle_array(self, node: AST.Array) -> HIR.Expr:
        if not node.elements:
            raise AnalysisError("Cannot infer the type of an empty array literal", node.span)

        elements = [self.value(element) for element in node.elements]
        element_type_id = self.__ctx.type_ctx.infer_common_type([element.type_id for element in elements], node.span, "array elements")
        if any(element.type_id != element_type_id for element in elements):
            elements = [self.coerce(element, element_type_id) for element in elements]
        if not self.__ctx.type_ctx.is_simple_type(element_type_id):
            for element in elements:
                check_simple_assign_source(element, self.__ctx.type_ctx, node.span)

        length_id = self.__ctx.type_ctx.alloc_literal_value(len(elements), self.__ctx.type_ctx.u64_id)
        type_id = self.__ctx.type_ctx.alloc_array(element_type_id, length_id)
        return HIR.Array(span=node.span, element_type=element_type_id, elements=elements, type_id=type_id, is_place=False)

    def __handle_array_repeat(self, node: AST.ArrayRepeat) -> HIR.Expr:
        element = self.value(node.element)
        element_type_id = self.__ctx.type_ctx.default_literals(element.type_id)
        if not self.__ctx.type_ctx.is_simple_type(element_type_id):
            check_simple_assign_source(element, self.__ctx.type_ctx, node.span)

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
        if expr.type_id == expected:
            return expr
        ch_coerce().trace(lambda: f"coerce {self.__ctx.type_ctx.get_name(expr.type_id)} -> {self.__ctx.type_ctx.get_name(expected)}")

        # never is subtype of everything — no coercion needed
        if expr.type_id == TypeCtx.never_id:
            return expr

        expected_ty = self.__ctx.type_ctx[expected]
        expr_ty = self.__ctx.type_ctx[expr.type_id]

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
            case HIR.NullptrLiteral():
                if not isinstance(expected_ty, Type.PointerType):
                    raise AnalysisError(f"cannot coerce nullptr to '{self.__ctx.type_ctx.get_name(expected)}'", expr.span)
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
            case HIR.Block() | HIR.If() | HIR.Loop() | HIR.Match():
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
        value_type = self.__ctx.type_ctx[value_expr.type_id]

        is_ref = False
        inner_type = value_type
        if isinstance(value_type, Type.PointerType):
            pointee_type = self.__ctx.type_ctx[value_type.pointee_type]
            if isinstance(pointee_type, (Type.IntType, Type.CharType, Type.EnumType)):
                is_ref = True
                inner_type = pointee_type

        if isinstance(inner_type, (Type.IntType, Type.CharType, Type.EnumType)):
            return self.__lower_match(stmt, value_expr, is_ref=is_ref)
        return self.__lower_match_with_partial_eq(stmt, value_expr)

    def lower_return(self, stmt: AST.Return) -> HIR.Return:
        assert self.__ctx.symbol_ctx is not None

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
        if not self.__ctx.type_ctx.is_simple_type(return_type_id):
            check_simple_assign_source(value_expr, self.__ctx.type_ctx, stmt.span)
        return HIR.Return(span=stmt.span, value=value_expr, type_id=TypeCtx.never_id, is_place=False)

    def lower_break(self, stmt: AST.Break) -> HIR.Break:
        if not self.__ctx.loop_stack:
            raise AnalysisError("'break' is only allowed inside a loop", stmt.span)

        loop_frame = self.__ctx.loop_stack[-1]
        value: HIR.Expr | None = None
        if stmt.expr is not None:
            value = self.value(stmt.expr)
            if not self.__ctx.type_ctx.is_simple_type(value.type_id):
                check_simple_assign_source(value, self.__ctx.type_ctx, stmt.span)
            loop_frame.break_value_type_ids.append(value.type_id)
        else:
            loop_frame.break_value_type_ids.append(TypeCtx.void_id)

        return HIR.Break(span=stmt.span, type_id=TypeCtx.never_id, is_place=False, value=value)

    def lower_continue(self, stmt: AST.Continue) -> HIR.Continue:
        if not self.__ctx.loop_stack:
            raise AnalysisError("'continue' is only allowed inside a loop", stmt.span)

        return HIR.Continue(span=stmt.span, type_id=TypeCtx.never_id, is_place=False)

    def lower_delete(self, stmt: AST.Delete) -> HIR.Delete:
        assert self.__ctx.symbol_ctx is not None

        target_expr = self.value(stmt.target)
        target_type = self.__ctx.type_ctx[target_expr.type_id]
        if not isinstance(target_type, Type.PointerType):
            raise AnalysisError("delete target must be a pointer expression", stmt.target.span)
        return HIR.Delete(span=stmt.span, target=target_expr, type_id=TypeCtx.void_id, is_place=False)

    def __declare_local_symbol(self, name: AST.Identifier, type_id: int) -> int:
        assert self.__ctx.symbol_ctx is not None

        symbol_id = self.__ctx.symbol_ctx.add_symbol(name.name, SymbolKind.Variable, type_id)
        if symbol_id is None:
            raise AnalysisError(f"Variable '{name.name}' is already defined in the current scope", name.span)
        self.__ctx.push_local(symbol_id)
        return symbol_id

    def __lower_match(self, stmt: AST.Match, value_expr: HIR.Expr, is_ref: bool = False) -> HIR.Match:
        assert self.__ctx.symbol_ctx is not None

        enum_type_id = value_expr.type_id
        if is_ref:
            value_type = self.__ctx.type_ctx[value_expr.type_id]
            assert isinstance(value_type, Type.PointerType)
            enum_type_id = value_type.pointee_type

        arms: list[HIR.MatchArm] = []
        arm_body_types: list[int] = []

        for pat, arm_block in stmt.arms:
            match pat:
                case AST.IntPattern():
                    body = self.check_block(arm_block)
                    arm_body_types.append(body.type_id)
                    for lit in pat.values:
                        pattern: HIR.Pattern | None = HIR.IntPattern(pat.span, lit.value, enum_type_id)
                        arms.append(HIR.MatchArm(span=pat.span, pattern=pattern, body=body))
                case AST.CharPattern():
                    body = self.check_block(arm_block)
                    arm_body_types.append(body.type_id)
                    for lit in pat.values:
                        pattern = HIR.CharPattern(pat.span, lit.value)
                        arms.append(HIR.MatchArm(span=pat.span, pattern=pattern, body=body))
                case AST.EnumPattern():
                    body = self.check_block(arm_block)
                    arm_body_types.append(body.type_id)
                    for ident in pat.variants:
                        variant = self.__resolve_enum_variant(ident, enum_type_id)
                        pattern = HIR.EnumPattern(pat.span, variant, None)
                        arms.append(HIR.MatchArm(span=pat.span, pattern=pattern, body=body))
                case AST.PayloadPattern():
                    variant = self.__resolve_enum_variant(pat.variant, enum_type_id)
                    if variant.payload_type is None:
                        raise AnalysisError(f"Variant '{pat.variant.name}' has no payload to bind", pat.span)
                    payload_ty = self.__ctx.type_ctx[variant.payload_type]
                    assert isinstance(payload_ty, Type.StructType)
                    field_types = [f.type_id for f in self.__ctx.type_ctx.get_struct_fields(payload_ty.type_id)]
                    if len(pat.fields) != len(field_types):
                        raise AnalysisError(
                            f"Pattern for variant '{pat.variant.name}' binds {len(pat.fields)} names but variant payload has {len(field_types)} fields",
                            pat.span,
                        )
                    self.__ctx.enter_scope()
                    try:
                        unpack_fields: list[int] = []
                        for ident, ftype in zip(pat.fields, field_types):
                            binding_type = self.__ctx.type_ctx.alloc_pointer(ftype) if is_ref else ftype
                            sym_id = self.__declare_local_symbol(ident, binding_type)
                            unpack_fields.append(sym_id)
                        body = self.check_block(arm_block)
                        arm_body_types.append(body.type_id)
                        pattern = HIR.EnumPattern(pat.span, variant, unpack_fields)
                        arms.append(HIR.MatchArm(span=pat.span, pattern=pattern, body=body))
                    finally:
                        self.__ctx.exit_scope()
                case AST.WildcardPattern():
                    body = self.check_block(arm_block)
                    arm_body_types.append(body.type_id)
                    arms.append(HIR.MatchArm(span=pat.span, pattern=None, body=body))
                case _:
                    raise AnalysisError(f"Unsupported pattern type {type(pat).__name__}", pat.span)

        match_type_id = self.__ctx.type_ctx.merge_types(arm_body_types, stmt.span)
        match_type_id = self.__ctx.type_ctx.default_literals(match_type_id)
        return HIR.Match(span=stmt.span, value=value_expr, arms=arms, type_id=match_type_id, is_place=False, is_ref=is_ref)

    def __resolve_enum_variant(self, ident: AST.Identifier, enum_type_id: int) -> Type.EnumVariant:
        enum_ty = self.__ctx.type_ctx[enum_type_id]
        assert isinstance(enum_ty, Type.EnumType)
        variant = enum_ty.get_variant_by_name(ident.name, self.__ctx.type_ctx)
        if variant is None:
            raise AnalysisError(f"Unknown enum variant '{ident.name}'", ident.span)
        return variant

    def __lower_match_with_partial_eq(self, stmt: AST.Match, value_expr: HIR.Expr) -> HIR.Block:
        cond_and_blocks: list[tuple[HIR.Expr, HIR.Block]] = []
        default_block: HIR.Block | None = None

        for pat, arm_block in stmt.arms:
            if isinstance(pat, AST.WildcardPattern):
                default_block = self.check_block(arm_block)
                continue

            cond_expr = self.__pattern_to_eq_cond(pat, value_expr)
            body = self.check_block(arm_block)
            cond_and_blocks.append((cond_expr, body))

        # Build nested if-chain from cond/block pairs
        current_else = default_block
        for cond, block in reversed(cond_and_blocks):
            current_else = HIR.Block(
                span=cond.span, stmts=[
                    HIR.If(span=cond.span, cond=cond, then_branch=block,
                           else_branch=current_else, type_id=TypeCtx.void_id, is_place=False)
                ], type_id=TypeCtx.void_id, is_place=False)
        return current_else if current_else is not None else HIR.Block(
            span=stmt.span, stmts=[], type_id=TypeCtx.void_id, is_place=False)

    def __pattern_to_eq_cond(self, pat: AST.Pattern, value_expr: HIR.Expr) -> HIR.Expr:
        conds: list[HIR.Expr] = []

        match pat:
            case AST.IntPattern():
                for lit in pat.values:
                    rhs = HIR.IntLiteral(span=lit.span, value=lit.value, type_id=value_expr.type_id, is_place=False)
                    conds.append(self.call_eq(value_expr, rhs))
            case AST.CharPattern():
                for lit in pat.values:
                    rhs = HIR.CharLiteral(span=lit.span, value=lit.value, type_id=value_expr.type_id, is_place=False)
                    conds.append(self.call_eq(value_expr, rhs))
            case AST.StrPattern():
                for lit in pat.values:
                    rhs = HIR.StrLiteral(span=lit.span, value=lit.value, type_id=value_expr.type_id, is_place=False)
                    conds.append(self.call_eq(value_expr, rhs))
            case AST.EnumPattern():
                enum_ty = self.__ctx.type_ctx[value_expr.type_id]
                assert isinstance(enum_ty, Type.EnumType)
                for ident in pat.variants:
                    variant = enum_ty.get_variant_by_name(ident.name, self.__ctx.type_ctx)
                    if variant is None:
                        raise AnalysisError(f"Unknown enum variant '{ident.name}'", ident.span)
                    rhs = HIR.VariantConstruct(span=ident.span, enum_id=value_expr.type_id, variant=variant, args=None, type_id=value_expr.type_id, is_place=False)
                    conds.append(self.call_eq(value_expr, rhs))
            case AST.PayloadPattern():
                raise AnalysisError("Payload patterns are not supported by PartialEq-based lowering", pat.span)
            case _:
                raise AnalysisError(f"Pattern type {type(pat).__name__} not supported by PartialEq lowering", pat.span)

        if len(conds) == 0:
            return HIR.BoolLiteral(span=pat.span, value=False, type_id=TypeCtx.bool_id, is_place=False)

        expr = conds[0]
        for c in conds[1:]:
            expr = HIR.Binary(span=expr.span, op=BinaryOperator.LogicalOr, left=expr, right=c, type_id=TypeCtx.bool_id, is_place=False)
        return expr
