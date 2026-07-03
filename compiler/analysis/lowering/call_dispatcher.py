from __future__ import annotations

from typing import TYPE_CHECKING

from compiler.analysis.error import AnalysisError
from compiler.analysis.lowering.assign_check import check_simple_assign_source
from compiler.analysis.lowering.expr_evaluator import ExprEvaluator
from compiler.analysis.symbol.symbol import SymbolKind
from compiler.analysis.ty import ty as Type
from compiler.analysis.ty.context import LookupResult
from compiler.analysis.ty.generic_inference import GenericInference
from compiler.analysis.unit import hir as HIR
from compiler.frontend.lex.position import SrcSpan
from compiler.frontend.parse import ast as AST
from compiler.frontend.parse.ast_type import GenericConstExpr, LiteralConstExpr
from compiler.frontend.parse.operator import UnaryOperator

if TYPE_CHECKING:
    from compiler.analysis.lowering.sem_ctx import SemCtx

# Built-in instruction names — all are expressions with different return types:
#   sizeof → u64,  bitcast → ptr,  sys_read/sys_write → void,  panic → never
BUILTIN_NAMES = frozenset({"sizeof", "bitcast", "sys_read", "sys_write", "panic", "bitcopy", "open", "close", "assume_init"})


class CallDispatcher:
    def __init__(self, ctx: SemCtx, expr: ExprEvaluator):
        self.__ctx = ctx
        self.__expr = expr

    def dispatch_method_call(self, span: SrcSpan, receiver: HIR.Expr, method_name: str, generic_args: list[int] | None, args: list[HIR.Expr], context_name: str) -> HIR.Expr:
        lookup = self.__ctx.type_ctx.method_lookup(receiver, method_name, generic_args, args)

        if lookup is None:
            raise AnalysisError(f"Unknown {context_name} '{method_name}' on {self.__ctx.type_ctx.get_name(receiver.type_id)}", span)

        # Static call site (Self.foo() / Type.foo()) requires a static method
        # and has no auto-deref (there is no instance to deref).
        if isinstance(receiver, HIR.Ty):
            method_ty = self.__ctx.type_ctx[lookup.method_id]
            assert isinstance(method_ty, Type.MethodType)
            if not method_ty.custom_def.is_static:
                type_name = self.__ctx.type_ctx.get_name(receiver.type_id)
                raise AnalysisError(
                    f"cannot call instance method '{method_name}' as a static method on '{type_name}'; "
                    f"declare it 'static fn' or call it on an instance",
                    span,
                )
            # Static calls have no auto-deref chain — skip directly to build.
            return self.build_method_call(span, receiver, lookup, args, context_name)

        # auto-deref: insert deref nodes for each level in the deref chain
        for _ in range(lookup.deref_count):
            current_ty = self.__ctx.type_ctx[receiver.type_id]
            if isinstance(current_ty, Type.PointerType):
                # pointer deref
                receiver = HIR.Unary(span, UnaryOperator.Deref, receiver, current_ty.pointee_type, is_place=True)
            else:
                # Deref trait deref: call deref() method
                deref_lookup = self.__ctx.type_ctx.method_lookup(receiver, "deref", None, [])
                assert deref_lookup is not None, f"Deref trait impl expected for type '{self.__ctx.type_ctx.get_name(receiver.type_id)}'"
                deref_call = self.build_method_call(span, receiver, deref_lookup, [], context_name)
                result_ty = self.__ctx.type_ctx[deref_call.type_id]
                if isinstance(result_ty, Type.PointerType):
                    # deref() returns a pointer → auto-deref the result (Deref trait special handling)
                    receiver = HIR.Unary(span, UnaryOperator.Deref, deref_call, result_ty.pointee_type, is_place=True)
                else:
                    receiver = deref_call

        return self.build_method_call(span, receiver, lookup, args, context_name)

    def handle_call(self, node: AST.Call) -> HIR.Expr:
        # bitcast<ptr_type>(expr) — callee is a TypeItem with generic ptr type
        if isinstance(node.callee, AST.TypeItem) and node.callee.name.name == "bitcast":
            return self.__handle_bitcast(node, node.callee)

        if isinstance(node.callee, AST.Identifier):
            return self.__handle_named_call(node, node.callee)

        callee = self.__expr.value(node.callee)
        if isinstance(callee, HIR.Ty):
            return self.__handle_type_call(node.span, callee, node.args)
        if self.__is_function_pointer_type(callee.type_id):
            return self.__handle_invocation(node.span, callee, node.args)

        raise AnalysisError("expression is not callable", node.span)

    def handle_method_call(self, node: AST.MethodCall) -> HIR.Expr:
        receiver = self.__expr.value(node.receiver)
        if isinstance(receiver, HIR.Ty):
            return self.__handle_static_or_variant_method_call(node, receiver)
        return self.__handle_instance_method_call(node, receiver)

    def __handle_named_call(self, node: AST.Call, callee: AST.Identifier) -> HIR.Expr:
        assert self.__ctx.symbol_ctx is not None

        # Intercept built-in instruction names before the symbol lookup.
        if callee.name in BUILTIN_NAMES:
            return self.__handle_builtin(node, callee)

        symbol = self.__ctx.symbol_ctx.lookup(callee.name)
        if symbol is None:
            raise AnalysisError(f"Unknown identifier '{callee.name}'", callee.span)

        match symbol.kind:
            case SymbolKind.Function:
                return self.__handle_function_call(node.span, symbol.type_id, callee.name, node.args)
            case SymbolKind.Variable:
                if self.__has_named_arg(node.args):
                    raise AnalysisError("named arguments are not supported for callable values", node.span)
                callable_expr = HIR.Var(span=callee.span, symbol_id=symbol.symbol_id, type_id=symbol.type_id, is_place=False)
                return self.__handle_invocation(node.span, callable_expr, node.args)
            case SymbolKind.Type:
                type_id = self.__ctx.type_ctx.resolve_aliases(symbol.type_id)
                return self.__handle_type_call(node.span, HIR.Ty(span=callee.span, type_id=type_id, is_place=False), node.args)
            case SymbolKind.ConstGeneric:
                raise AnalysisError(f"'{callee.name}' is a generic constant and cannot be called", node.span)

    def __handle_builtin(self, node: AST.Call, callee: AST.Identifier) -> HIR.Expr:
        """Lower a call to a built-in name into the appropriate HIR node."""
        match callee.name:
            case "panic":
                return self.__handle_panic(node)
            case "bitcopy":
                return self.__handle_bitcopy(node)
            case "sizeof":
                return self.__handle_sizeof(node)
            case "bitcast":
                raise AnalysisError("'bitcast' requires generic target type: use bitcast<ptr_type>(expr)", callee.span)
            case "sys_write":
                return self.__handle_sys_write(node)
            case "sys_read":
                return self.__handle_sys_read(node)
            case "open":
                return self.__handle_open(node)
            case "close":
                return self.__handle_close(node)
            case "assume_init":
                return self.__handle_assume_init(node)
            case _:
                raise AnalysisError(f"Unknown built-in '{callee.name}'", callee.span)

    def __handle_panic(self, stmt: AST.Call) -> HIR.Panic:
        if any(arg.name is not None for arg in stmt.args):
            raise AnalysisError("named arguments are not supported for 'panic'", stmt.span)
        if len(stmt.args) != 1:
            raise AnalysisError(f"'panic' expects exactly 1 argument, got {len(stmt.args)}", stmt.span)

        message = self.__expr.value(stmt.args[0].value)
        message = self.__expr.coerce(message, self.__ctx.type_ctx.str_id)
        return HIR.Panic(span=stmt.span, message=message, type_id=self.__ctx.type_ctx.never_id, is_place=False)

    def __handle_bitcopy(self, node: AST.Call) -> HIR.Expr:
        if any(arg.name is not None for arg in node.args):
            raise AnalysisError("named arguments are not supported for 'bitcopy'", node.span)
        if len(node.args) != 1:
            raise AnalysisError(f"'bitcopy' expects exactly 1 argument, got {len(node.args)}", node.span)
        value = self.__expr.value(node.args[0].value)
        return HIR.BitCopy(span=node.span, value=value, type_id=value.type_id, is_place=False)

    def __handle_assume_init(self, node: AST.Call) -> HIR.Expr:
        """Lower `assume_init(expr)` into HIR.AssumeInit."""
        if any(arg.name is not None for arg in node.args):
            raise AnalysisError("named arguments are not supported for 'assume_init'", node.span)
        if len(node.args) != 1:
            raise AnalysisError(f"'assume_init' expects exactly 1 argument, got {len(node.args)}", node.span)
        value = self.__expr.value(node.args[0].value)
        return HIR.AssumeInit(span=node.span, value=value, type_id=value.type_id, is_place=False)

    def __handle_sizeof(self, node: AST.Call) -> HIR.Expr:
        """Lower `sizeof(type)` into HIR.SizeOf.

        The argument must evaluate to a type (HIR.Ty). This supports simple type
        names like `sizeof(i32)` as well as generic types like `sizeof(Vec<u8>)`.
        """
        if any(arg.name is not None for arg in node.args):
            raise AnalysisError("named arguments are not supported for 'sizeof'", node.span)
        if len(node.args) != 1:
            raise AnalysisError(f"'sizeof' expects exactly 1 argument, got {len(node.args)}", node.span)

        # Evaluate the argument as an expression. For type names this will
        # produce HIR.Ty, which carries the resolved type_id.
        arg_hir = self.__expr.value(node.args[0].value)
        if not isinstance(arg_hir, HIR.Ty):
            raise AnalysisError("'sizeof' expects a type as its argument", node.args[0].span)

        # sizeof returns usize (u64)
        usize_type_id = self.__ctx.type_ctx.u64_id
        return HIR.SizeOf(
            span=node.span,
            target_type=arg_hir.type_id,
            type_id=usize_type_id,
            is_place=False,
        )

    def __handle_bitcast(self, node: AST.Call, callee: AST.TypeItem) -> HIR.Expr:
        """Lower `bitcast<ptr_type>(expr)` into HIR.BitCast.

        Requirements:
        - Exactly 1 generic argument (the target pointer type)
        - Exactly 1 call argument (the pointer expression to cast)
        - Both must be pointer types
        """
        if any(arg.name is not None for arg in node.args):
            raise AnalysisError("named arguments are not supported for 'bitcast'", node.span)
        if len(node.args) != 1:
            raise AnalysisError(f"'bitcast' expects exactly 1 argument, got {len(node.args)}", node.span)
        if len(callee.generics) != 1:
            raise AnalysisError(
                f"'bitcast' expects exactly 1 generic argument (target pointer type), got {len(callee.generics)}",
                callee.span,
            )

        # Resolve the target pointer type from the generic argument.
        generic_arg = callee.generics[0]
        if isinstance(generic_arg, (LiteralConstExpr, GenericConstExpr)):
            raise AnalysisError(
                "'bitcast' expects a type argument, got a const expression",
                callee.span,
            )
        assert self.__ctx.symbol_ctx is not None
        target_type_id = self.__ctx.resolve_type(generic_arg)

        # Evaluate the expression argument (must be a pointer expression).
        value = self.__expr.value(node.args[0].value)

        # Validate that both the value and target are pointer types.
        value_ty = self.__ctx.type_ctx[value.type_id]
        target_ty = self.__ctx.type_ctx[target_type_id]
        if not isinstance(value_ty, (Type.PointerType, Type.NullPtrType)):
            raise AnalysisError(
                f"'bitcast' expects a pointer expression, got '{self.__ctx.type_ctx.get_name(value.type_id)}'",
                node.args[0].span,
            )
        if not isinstance(target_ty, Type.PointerType):
            raise AnalysisError(
                f"'bitcast' target type must be a pointer type, got '{self.__ctx.type_ctx.get_name(target_type_id)}'",
                callee.span,
            )

        return HIR.BitCast(
            span=node.span,
            value=value,
            target_type=target_type_id,
            type_id=target_type_id,
            is_place=False,
        )

    def __handle_sys_write(self, node: AST.Call) -> HIR.Expr:
        """Lower `sys_write(fd, buf)` into HIR.SysWrite."""
        if self.__has_named_arg(node.args):
            raise AnalysisError("named arguments are not supported for 'sys_write'", node.span)
        if len(node.args) != 2:
            raise AnalysisError(f"'sys_write' expects exactly 2 arguments, got {len(node.args)}", node.span)

        fd = self.__expr.coerce(self.__expr.value(node.args[0].value), self.__ctx.type_ctx.i32_id)
        buf = self.__expr.coerce(self.__expr.value(node.args[1].value), self.__ctx.type_ctx.str_id)
        return HIR.SysWrite(
            span=node.span,
            fd=fd,
            buf=buf,
            type_id=self.__ctx.type_ctx.void_id,
            is_place=False,
        )

    def __handle_sys_read(self, node: AST.Call) -> HIR.Expr:
        """Lower `sys_read(fd, buf)` into HIR.SysRead."""
        if self.__has_named_arg(node.args):
            raise AnalysisError("named arguments are not supported for 'sys_read'", node.span)
        if len(node.args) != 2:
            raise AnalysisError(f"'sys_read' expects exactly 2 arguments, got {len(node.args)}", node.span)

        fd = self.__expr.coerce(self.__expr.value(node.args[0].value), self.__ctx.type_ctx.i32_id)
        buf = self.__expr.value(node.args[1].value)
        return HIR.SysRead(
            span=node.span,
            fd=fd,
            buf=buf,
            type_id=self.__ctx.type_ctx.str_id,
            is_place=False,
        )

    def __handle_open(self, node: AST.Call) -> HIR.Expr:
        """Lower `open(path, flags)` into HIR.Open."""
        if self.__has_named_arg(node.args):
            raise AnalysisError("named arguments are not supported for 'open'", node.span)
        if len(node.args) != 2:
            raise AnalysisError(f"'open' expects exactly 2 arguments, got {len(node.args)}", node.span)

        path = self.__expr.coerce(self.__expr.value(node.args[0].value), self.__ctx.type_ctx.str_id)
        flags = self.__expr.coerce(self.__expr.value(node.args[1].value), self.__ctx.type_ctx.i32_id)
        return HIR.Open(
            span=node.span,
            path=path,
            flags=flags,
            type_id=self.__ctx.type_ctx.i32_id,
            is_place=False,
        )

    def __handle_close(self, node: AST.Call) -> HIR.Expr:
        """Lower `close(fd)` into HIR.Close."""
        if self.__has_named_arg(node.args):
            raise AnalysisError("named arguments are not supported for 'close'", node.span)
        if len(node.args) != 1:
            raise AnalysisError(f"'close' expects exactly 1 argument, got {len(node.args)}", node.span)

        fd = self.__expr.coerce(self.__expr.value(node.args[0].value), self.__ctx.type_ctx.i32_id)
        return HIR.Close(
            span=node.span,
            fd=fd,
            type_id=self.__ctx.type_ctx.i32_id,
            is_place=False,
        )

    def __handle_function_call(self, span: SrcSpan, func_type_id: int, func_name: str, args: list[AST.Arg]) -> HIR.Expr:
        if self.__has_named_arg(args):
            raise AnalysisError(f"named arguments are not supported for function call '{func_name}'", span)

        func_ty = self.__ctx.type_ctx[func_type_id]
        if not isinstance(func_ty, Type.FunctionType):
            raise AnalysisError(f"'{func_name}' is not a function", span)

        parameters = func_ty.parameters(self.__ctx.type_ctx)
        expected_type_ids = [param.type_id for param in parameters]
        coerced_args, inference = self.__infer_arguments(span, expected_type_ids, args, f"function call '{func_name}'")
        for arg in coerced_args:
            if not self.__ctx.type_ctx.is_simple_type(arg.type_id):
                check_simple_assign_source(arg, self.__ctx.type_ctx, span)

        instantiated_func_id = inference.instantiate(func_type_id)
        # report reachable instantiated function to the semantic context
        self.__ctx.report_def(instantiated_func_id)

        instantiated_func_ty = self.__ctx.type_ctx[instantiated_func_id]
        assert isinstance(instantiated_func_ty, Type.FunctionType)
        return HIR.Call(
            span=span,
            func=instantiated_func_id,
            args=coerced_args,
            type_id=instantiated_func_ty.return_type(self.__ctx.type_ctx),
            is_place=False,
        )

    def __handle_invocation(self, span: SrcSpan, callable_expr: HIR.Expr, args: list[AST.Arg]) -> HIR.Expr:
        if self.__has_named_arg(args):
            raise AnalysisError("named arguments are not supported for callable values", span)

        if not self.__is_function_pointer_type(callable_expr.type_id):
            raise AnalysisError("expression is not callable", span)

        callable_ty = self.__ctx.type_ctx[callable_expr.type_id]
        assert isinstance(callable_ty, Type.FunctionPointerType)

        if len(callable_ty.parameter_types) != len(args):
            raise AnalysisError(f"callable expects {len(callable_ty.parameter_types)} arguments, got {len(args)}", span)

        coerced_args = [self.__expr.coerce(self.__expr.value(arg.value), param_type) for arg, param_type in zip(args, callable_ty.parameter_types)]
        return HIR.Invoke(span=span, callable=callable_expr, args=coerced_args, type_id=callable_ty.return_type, is_place=False)

    def __handle_type_call(self, span: SrcSpan, callable_type: HIR.Ty, args: list[AST.Arg]) -> HIR.Expr:
        ty = self.__ctx.type_ctx[callable_type.type_id]

        if isinstance(ty, Type.FunctionType):
            return self.__handle_function_call(span, callable_type.type_id, self.__ctx.type_ctx.get_name(callable_type.type_id), args)

        if isinstance(ty, Type.StructType):
            return self.__handle_struct_construction(span, callable_type.type_id, args)

        if isinstance(ty, (Type.IntType, Type.FloatType, Type.IntLiteralType, Type.FloatLiteralType, Type.CharType)):
            return self.__handle_cast(span, callable_type.type_id, args)

        raise AnalysisError(f"type '{self.__ctx.type_ctx.get_name(callable_type.type_id)}' is not callable", span)

    def __handle_struct_construction(self, span: SrcSpan, struct_type_id: int, args: list[AST.Arg]) -> HIR.Expr:
        struct_ty = self.__ctx.type_ctx[struct_type_id]
        assert isinstance(struct_ty, Type.StructType)

        fields = self.__ctx.type_ctx.get_struct_fields(struct_type_id)
        coerced_fields, inference = self.__resolve_named_or_positional_struct_args(span, struct_type_id, fields, args)
        for field_value in coerced_fields.values():
            if not self.__ctx.type_ctx.is_simple_type(field_value.type_id):
                check_simple_assign_source(field_value, self.__ctx.type_ctx, span)

        instantiated_struct_id = inference.instantiate(struct_type_id)
        return HIR.StructConstruct(span=span, struct_id=instantiated_struct_id, field_values=coerced_fields, type_id=instantiated_struct_id, is_place=False)

    def __handle_cast(self, span: SrcSpan, target_type_id: int, args: list[AST.Arg]) -> HIR.Expr:
        if self.__has_named_arg(args):
            raise AnalysisError("named arguments are not supported for type casts", span)

        if len(args) != 1:
            raise AnalysisError("type casts accept exactly one argument", span)

        value = self.__expr.value(args[0].value)
        return HIR.Cast(span=span, value=value, target_type=target_type_id, type_id=target_type_id, is_place=False)

    def __handle_instance_method_call(self, node: AST.MethodCall, receiver: HIR.Expr) -> HIR.Expr:
        args = self.__resolve_positional_args(node.args, "method call")
        return self.dispatch_method_call(
            node.span,
            receiver,
            node.method_name.name,
            [self.__ctx.resolve_type(generic) for generic in node.generics] if node.generics else None,
            args,
            "method call",
        )

    def __handle_static_or_variant_method_call(self, node: AST.MethodCall, receiver: HIR.Ty) -> HIR.Expr:
        ty = self.__ctx.type_ctx[receiver.type_id]

        if isinstance(ty, Type.EnumType):
            variant = ty.get_variant_by_name(node.method_name.name, self.__ctx.type_ctx)
            if variant is not None:
                return self.__handle_variant_construction(node.span, receiver.type_id, variant, node.args)

        args = self.__resolve_positional_args(node.args, "static method call")
        return self.dispatch_method_call(
            node.span,
            receiver,
            node.method_name.name,
            [self.__ctx.resolve_type(generic) for generic in node.generics] if node.generics else None,
            args,
            "static method call",
        )

    def build_method_call(self, span: SrcSpan, receiver: HIR.Expr, lookup: LookupResult, args: list[HIR.Expr], context_name: str) -> HIR.MethodCall:
        """Construct HIR.MethodCall from a successful `method_lookup` result.

        Handles GenericInference + coercion for receiver and args,
        reports the instantiated method via SemCtx.report_def,
        and returns a fully-typed HIR.MethodCall node.

        `lookup.method_id` is expected to be an instantiated/concrete method type id.
        """
        method_type = self.__ctx.type_ctx[lookup.method_id]
        assert isinstance(method_type, Type.MethodType)
        parameters = method_type.parameters(self.__ctx.type_ctx)
        expected_type_ids = [method_type.receiver_type(self.__ctx.type_ctx)] + [param.type_id for param in parameters]

        coerced_receiver, coerced_args, inference = self.__infer_receiver_and_args(span, receiver, expected_type_ids, args, context_name)
        for arg in coerced_args:
            if not self.__ctx.type_ctx.is_simple_type(arg.type_id):
                check_simple_assign_source(arg, self.__ctx.type_ctx, span)
        # report reachable instantiated method to the semantic context
        self.__ctx.report_def(lookup.method_id)

        return HIR.MethodCall(
            span=span,
            receiver=coerced_receiver,
            method_id=lookup.method_id,
            args=coerced_args,
            type_id=inference.instantiate(method_type.return_type(self.__ctx.type_ctx)),
            is_place=False,
        )

    def __handle_variant_construction(self, span: SrcSpan, enum_type_id: int, variant: Type.EnumVariant, args: list[AST.Arg]) -> HIR.Expr:
        if not args:
            if variant.payload_type is not None:
                raise AnalysisError(f"variant '{variant.name}' expects an argument", span)
            return HIR.VariantConstruct(span=span, enum_id=enum_type_id, variant=variant, args=None, type_id=enum_type_id, is_place=False)

        if self.__has_named_arg(args):
            coerced_args = self.__resolve_named_variant_args(span, variant, args)
            for val in coerced_args.values():
                if not self.__ctx.type_ctx.is_simple_type(val.type_id):
                    check_simple_assign_source(val, self.__ctx.type_ctx, span)
            return HIR.VariantConstruct(span=span, enum_id=enum_type_id, variant=variant, args=coerced_args, type_id=enum_type_id, is_place=False)

        if variant.payload_type is None:
            raise AnalysisError(f"variant '{variant.name}' does not take any arguments", span)

        # payload_type 非 None 时必定是 StructType
        payload_ty = self.__ctx.type_ctx[variant.payload_type]
        assert isinstance(payload_ty, Type.StructType)
        fields = self.__ctx.type_ctx.get_struct_fields(payload_ty.type_id)
        if len(args) != len(fields):
            raise AnalysisError(f"variant '{variant.name}' expects {len(fields)} arguments, got {len(args)}", span)

        arg_values = [self.__expr.value(arg.value) for arg in args]
        field_type_ids = [field.type_id for field in fields]

        inference = GenericInference(self.__ctx.type_ctx, span)
        for field_type_id, arg_value in zip(field_type_ids, arg_values):
            inference.constrain(field_type_id, arg_value.type_id)

        coerced_values = [self.__expr.coerce(val, inference.instantiate(field_type_id)) for field_type_id, val in zip(field_type_ids, arg_values)]
        for val in coerced_values:
            if not self.__ctx.type_ctx.is_simple_type(val.type_id):
                check_simple_assign_source(val, self.__ctx.type_ctx, span)
        args_dict = {field.name: val for field, val in zip(fields, coerced_values)}
        return HIR.VariantConstruct(span=span, enum_id=enum_type_id, variant=variant, args=args_dict, type_id=enum_type_id, is_place=False)

    def __resolve_positional_args(self, args: list[AST.Arg], context_name: str) -> list[HIR.Expr]:
        if self.__has_named_arg(args):
            raise AnalysisError(f"named arguments are not supported for {context_name}", args[0].span)
        return [self.__expr.value(arg.value) for arg in args]

    def __infer_arguments(self, span: SrcSpan, expected_type_ids: list[int], args: list[AST.Arg], context_name: str) -> tuple[list[HIR.Expr], GenericInference]:
        if len(expected_type_ids) != len(args):
            raise AnalysisError(f"{context_name} expects {len(expected_type_ids)} arguments, got {len(args)}", span)

        inference = GenericInference(self.__ctx.type_ctx, span)
        values = [self.__expr.value(arg.value) for arg in args]
        for expected_type_id, value in zip(expected_type_ids, values):
            inference.constrain(expected_type_id, value.type_id)

        coerced_args = [self.__expr.coerce(value, inference.instantiate(expected_type_id)) for expected_type_id, value in zip(expected_type_ids, values)]
        return coerced_args, inference

    def __infer_receiver_and_args(self, span: SrcSpan, receiver: HIR.Expr, expected_type_ids: list[int], args: list[HIR.Expr], context_name: str) -> tuple[HIR.Expr, list[HIR.Expr], GenericInference]:
        if len(expected_type_ids) != len(args) + 1:
            raise AnalysisError(f"{context_name} expects {len(expected_type_ids) - 1} arguments, got {len(args)}", span)

        inference = GenericInference(self.__ctx.type_ctx, span)
        inference.constrain(expected_type_ids[0], receiver.type_id)
        for expected_type_id, value in zip(expected_type_ids[1:], args):
            inference.constrain(expected_type_id, value.type_id)

        coerced_receiver = self.__expr.coerce(receiver, inference.instantiate(expected_type_ids[0]))
        coerced_args = [self.__expr.coerce(value, inference.instantiate(expected_type_id)) for expected_type_id, value in zip(expected_type_ids[1:], args)]
        return coerced_receiver, coerced_args, inference

    def __resolve_named_or_positional_struct_args(self, span: SrcSpan, struct_type_id: int, fields: list[Type.StructField], args: list[AST.Arg]) -> tuple[dict[str, HIR.Expr], GenericInference]:
        if not args:
            inference = GenericInference(self.__ctx.type_ctx, span)
            return {}, inference

        if self.__has_named_arg(args):
            if any(arg.name is None for arg in args):
                raise AnalysisError("named and positional arguments cannot be mixed", span)

            field_by_name = {field.name: field for field in fields}
            resolved_values: dict[str, HIR.Expr] = {}
            for arg in args:
                assert arg.name is not None
                field = field_by_name.get(arg.name.name)
                if field is None:
                    raise AnalysisError(f"unknown field '{arg.name.name}' in struct constructor", arg.name.span)
                if field.name in resolved_values:
                    raise AnalysisError(f"duplicate field '{field.name}' in struct constructor", arg.name.span)
                resolved_values[field.name] = self.__expr.value(arg.value)

            if len(resolved_values) != len(fields):
                missing = [field.name for field in fields if field.name not in resolved_values]
                if missing:
                    raise AnalysisError(f"missing struct fields: {', '.join(missing)}", span)
            inference = GenericInference(self.__ctx.type_ctx, span)
            for field in fields:
                inference.constrain(field.type_id, resolved_values[field.name].type_id)

            return (
                {
                    field.name: self.__expr.coerce(resolved_values[field.name], inference.instantiate(field.type_id))
                    for field in fields
                },
                inference,
            )

        if len(args) != len(fields):
            raise AnalysisError(
                f"struct '{self.__ctx.type_ctx.get_name(struct_type_id)}' expects {len(fields)} fields, got {len(args)}",
                span,
            )

        inference = GenericInference(self.__ctx.type_ctx, span)
        values = [self.__expr.value(arg.value) for arg in args]
        for field, value in zip(fields, values):
            inference.constrain(field.type_id, value.type_id)

        return (
            {
                field.name: self.__expr.coerce(value, inference.instantiate(field.type_id))
                for field, value in zip(fields, values)
            },
            inference,
        )

    def __resolve_named_variant_args(self, span: SrcSpan, variant: Type.EnumVariant, args: list[AST.Arg]) -> dict[str, HIR.Expr]:
        if any(arg.name is None for arg in args):
            raise AnalysisError("named and positional arguments cannot be mixed", span)
        if variant.payload_type is None:
            raise AnalysisError(f"variant '{variant.name}' does not take any arguments", span)

        payload_ty = self.__ctx.type_ctx[variant.payload_type]
        assert isinstance(payload_ty, Type.StructType)
        fields = self.__ctx.type_ctx.get_struct_fields(payload_ty.type_id)
        field_by_name = {field.name: field for field in fields}

        resolved_values: dict[str, HIR.Expr] = {}
        for arg in args:
            assert arg.name is not None
            field = field_by_name.get(arg.name.name)
            if field is None:
                raise AnalysisError(f"unknown field '{arg.name.name}' in variant '{variant.name}'", arg.name.span)
            if field.name in resolved_values:
                raise AnalysisError(f"duplicate field '{field.name}' in variant '{variant.name}'", arg.name.span)
            resolved_values[field.name] = self.__expr.value(arg.value)

        if len(resolved_values) != len(fields):
            missing = [field.name for field in fields if field.name not in resolved_values]
            if missing:
                raise AnalysisError(f"missing variant fields: {', '.join(missing)}", span)

        inference = GenericInference(self.__ctx.type_ctx, span)
        for field in fields:
            inference.constrain(field.type_id, resolved_values[field.name].type_id)

        return {
            field.name: self.__expr.coerce(resolved_values[field.name], inference.instantiate(field.type_id))
            for field in fields
        }

    def __has_named_arg(self, args: list[AST.Arg]) -> bool:
        return any(arg.name is not None for arg in args)

    def __is_function_pointer_type(self, type_id: int) -> bool:
        return isinstance(self.__ctx.type_ctx[type_id], Type.FunctionPointerType)
