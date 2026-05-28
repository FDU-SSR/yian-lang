from __future__ import annotations

from compiler.analysis.error import AnalysisError
from compiler.analysis.passes.expr_evaluator import ExprEvaluator
from compiler.analysis.passes.sem_ctx import SemCtx
from compiler.analysis.symbol.symbol import SymbolKind
from compiler.analysis.ty import ty as Type
from compiler.analysis.ty.generic_inference import GenericInference
from compiler.analysis.unit import hir as HIR
from compiler.analysis.ty.context import LookupResult
from compiler.frontend.parse import ast as AST
from compiler.utils.IR.position import SrcSpan


class CallDispatcher:
    def __init__(self, ctx: SemCtx, expr: ExprEvaluator):
        self.__ctx = ctx
        self.__expr = expr

    def dispatch_method_call(self, span: SrcSpan, receiver: HIR.Expr, method_name: str, generic_args: list[int] | None, args: list[HIR.Expr], context_name: str) -> HIR.Expr:
        lookup = self.__ctx.type_ctx.method_lookup(receiver, method_name, generic_args, args)

        if lookup is None:
            raise AnalysisError(f"Unknown {context_name} '{method_name}'", span)

        return self.__dispatch_method_call(span, receiver, lookup, args, context_name)

    def handle_call(self, node: AST.Call) -> HIR.Expr:
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
                return self.__handle_type_call(node.span, HIR.Ty(span=callee.span, type_id=symbol.type_id, is_place=False), node.args)

    def __handle_function_call(self, span: SrcSpan, func_type_id: int, func_name: str, args: list[AST.Arg]) -> HIR.Expr:
        if self.__has_named_arg(args):
            raise AnalysisError(f"named arguments are not supported for function call '{func_name}'", span)

        func_ty = self.__ctx.type_ctx[func_type_id]
        if not isinstance(func_ty, Type.FunctionType):
            raise AnalysisError(f"'{func_name}' is not a function", span)

        parameters = func_ty.parameters(self.__ctx.type_ctx)
        expected_type_ids = [param.type_id for param in parameters]
        coerced_args, inference = self.__infer_arguments(span, expected_type_ids, args, f"function call '{func_name}'")

        instantiated_func_id = inference.instantiate(func_type_id)
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

        if isinstance(ty, Type.StructType):
            return self.__handle_struct_construction(span, callable_type.type_id, args)

        if isinstance(ty, (Type.IntType, Type.FloatType, Type.IntLiteralType, Type.FloatLiteralType, Type.CharType)):
            return self.__handle_cast(span, callable_type.type_id, args)

        raise AnalysisError(f"type '{self.__ctx.type_ctx.get_name(callable_type.type_id)}' is not callable", span)

    def __handle_struct_construction(self, span: SrcSpan, struct_type_id: int, args: list[AST.Arg]) -> HIR.Expr:
        struct_ty = self.__ctx.type_ctx[struct_type_id]
        assert isinstance(struct_ty, Type.StructType)

        fields = struct_ty.get_fields(self.__ctx.type_ctx)
        coerced_fields, inference = self.__resolve_named_or_positional_struct_args(span, struct_type_id, fields, args)

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

    def __dispatch_method_call(self, span: SrcSpan, receiver: HIR.Expr, lookup: LookupResult, args: list[HIR.Expr], context_name: str) -> HIR.MethodCall:
        """Common method call construction after `method_lookup` succeeded.

        `lookup.method_id` is expected to be an instantiated/concrete method type id.
        """
        method_type = self.__ctx.type_ctx[lookup.method_id]
        assert isinstance(method_type, Type.MethodType)
        parameters = method_type.parameters(self.__ctx.type_ctx)
        expected_type_ids = [method_type.receiver_type(self.__ctx.type_ctx)] + [param.type_id for param in parameters]

        coerced_receiver, coerced_args, inference = self.__infer_receiver_and_args(span, receiver, expected_type_ids, args, context_name)
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
            return HIR.VariantConstruct(span=span, enum_id=enum_type_id, variant=variant, args=coerced_args, type_id=enum_type_id, is_place=False)

        if len(args) != 1:
            raise AnalysisError(f"variant '{variant.name}' expects 1 argument, got {len(args)}", span)

        if variant.payload_type is None:
            raise AnalysisError(f"variant '{variant.name}' does not take any arguments", span)

        value = self.__infer_named_or_positional_values(span, [variant.payload_type], [args[0]], "variant construction")[0]
        return HIR.VariantConstruct(span=span, enum_id=enum_type_id, variant=variant, args={variant.name: value}, type_id=enum_type_id, is_place=False)

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

    def __infer_named_or_positional_values(self, span: SrcSpan, expected_type_ids: list[int], args: list[AST.Arg], context_name: str) -> list[HIR.Expr]:
        if len(expected_type_ids) != len(args):
            raise AnalysisError(f"{context_name} expects {len(expected_type_ids)} arguments, got {len(args)}", span)

        inference = GenericInference(self.__ctx.type_ctx, span)
        values = [self.__expr.value(arg.value) for arg in args]
        for expected_type_id, value in zip(expected_type_ids, values):
            inference.constrain(expected_type_id, value.type_id)

        return [self.__expr.coerce(value, inference.instantiate(expected_type_id)) for expected_type_id, value in zip(expected_type_ids, values)]

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
        if len(args) != 1:
            raise AnalysisError(f"variant '{variant.name}' expects one named payload argument", span)

        arg = args[0]
        assert arg.name is not None
        if variant.payload_type is None:
            raise AnalysisError(f"variant '{variant.name}' does not take any arguments", span)

        value = self.__infer_named_or_positional_values(span, [variant.payload_type], [arg], "variant construction")[0]
        return {arg.name.name: value}

    def __has_named_arg(self, args: list[AST.Arg]) -> bool:
        return any(arg.name is not None for arg in args)

    def __is_function_pointer_type(self, type_id: int) -> bool:
        return isinstance(self.__ctx.type_ctx[type_id], Type.FunctionPointerType)
