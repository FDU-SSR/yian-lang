from __future__ import annotations

from typing import TYPE_CHECKING

from compiler.analysis.lowering.assign_check import check_simple_assign_source
from compiler.analysis.ty import ty as Type
from compiler.analysis.ty.ty import AccessMode, StructField
from compiler.analysis.unit import hir as HIR
from compiler.frontend.parse import ast as AST

if TYPE_CHECKING:
    from compiler.analysis.lowering.expr_checker import ExprChecker
    from compiler.analysis.lowering.sem_ctx import SemCtx


class ClosureHelper:
    """TypeCheck helper for closures.

    ``check_closure_expr`` allocates types (ClosureType, anonymous struct,
    call-method), registers the closure AST body as a procedure so the
    worklist picks it up later, and returns HIR.Closure.
    """

    closure_counter = 0

    def __init__(self, ctx: SemCtx, expr_checker: ExprChecker):
        self.__ctx = ctx
        self.__expr = expr_checker

    def check_closure_expr(self, node: AST.ClosureExpr) -> HIR.Closure:
        """Phase-1: allocate types, register AST body for later type-check."""
        type_ctx = self.__ctx.type_ctx

        # --- capture expressions (evaluated in enclosing scope) ---
        capture_values: dict[str, HIR.Expr] = {}
        for capture in node.captures:
            cap_expr = self.__expr.value(capture.expr)
            if not type_ctx.is_simple_type(cap_expr.type_id):
                check_simple_assign_source(cap_expr, type_ctx, capture.span)
            capture_values[capture.name.name] = cap_expr

        # --- parameter types ---
        parameters: list[Type.Parameter] = []
        for param in node.params:
            param_type_id = self.__ctx.resolve_type(param.var_type)
            parameters.append(Type.Parameter(name=param.name.name, type_id=param_type_id))

        # --- return type ---
        if node.return_type is not None:
            return_type = self.__ctx.resolve_type(node.return_type)
        else:
            return_type = type_ctx.void_id

        # --- allocate ClosureType ---
        captured_vars = [
            Type.CapturedVar(name=name, type_id=expr.type_id)
            for name, expr in capture_values.items()
        ]
        closure_type_id = type_ctx.alloc_closure(
            captured_vars=captured_vars,
            parameters=parameters,
            return_type=return_type,
            span=node.span,
        )
        closure_ty = type_ctx[closure_type_id]
        assert isinstance(closure_ty, Type.ClosureType)

        # --- generate anonymous struct ---
        ClosureHelper.closure_counter += 1
        struct_name = f"%closure_{ClosureHelper.closure_counter}"
        struct_type_id = type_ctx.alloc_struct(struct_name, node.span)
        struct_ty = type_ctx[struct_type_id]
        assert isinstance(struct_ty, Type.StructType)
        for idx, cv in enumerate(captured_vars):
            struct_ty.custom_def.fields.append(
                StructField(name=cv.name, type_id=cv.type_id, access_mode=AccessMode.Private, index=idx))
        struct_ty.custom_def.unit_id = self.__ctx.unit_id

        # --- generate call-method type ---
        method_type_id = type_ctx.alloc_method("call", node.span)
        method_ty = type_ctx[method_type_id]
        assert isinstance(method_ty, Type.MethodType)
        method_ty.custom_def.receiver_type = struct_type_id
        method_ty.custom_def.parameters = parameters
        method_ty.custom_def.return_type = return_type
        method_ty.custom_def.is_static = False
        method_ty.custom_def.is_header = False

        # --- store lowering info in ClosureType ---
        closure_ty.struct_type_id = struct_type_id
        closure_ty.call_method_type_id = method_type_id

        # --- register AST body as procedure (keyed by closure_type_id) ---
        type_ctx.add_procedure(closure_type_id, node.body, self.__ctx.unit_id)
        self.__ctx.report_def(closure_type_id)

        # --- return HIR.Closure (variable keeps ClosureType for dispatch) ---
        return HIR.Closure(
            span=node.span,
            type_id=closure_type_id,
            captures=capture_values,
            is_place=True,
        )
