from __future__ import annotations

from typing import TYPE_CHECKING

from compiler.analysis.error import AnalysisError
from compiler.analysis.lowering.assign_check import check_simple_assign_source
from compiler.analysis.symbol.symbol import SymbolKind
from compiler.analysis.ty import ty as Type
from compiler.analysis.unit import hir as HIR
from compiler.frontend.lex.position import SrcSpan
from compiler.frontend.parse import ast as AST

if TYPE_CHECKING:
    from compiler.analysis.lowering.expr_checker import ExprChecker
    from compiler.analysis.lowering.sem_ctx import SemCtx


class ClosureHelper:
    """Handles closures from TypeCheck through HIR→CFG lowering.

    TypeCheck phase (M2):
    - check_closure_expr(): type-check closure definition, produce HIR.Closure
    - handle_closure_call(): produce HIR.Invoke for a closure call

    Lowering phase (M3):
    - lower_closure_to_struct(): HIR.Closure → HIR.StructConstruct
    - lower_closure_call(): HIR.Invoke → HIR.MethodCall
    """

    def __init__(self, ctx: SemCtx, expr_checker: ExprChecker):
        self.__ctx = ctx
        self.__expr = expr_checker

    # ------------------------------------------------------------------
    # TypeCheck phase
    # ------------------------------------------------------------------

    def check_closure_expr(self, node: AST.ClosureExpr) -> HIR.Closure:
        """Type-check a closure definition, returning HIR.Closure.

        Does NOT lower to struct — that happens at the HIR→CFG boundary (M3).
        """
        type_ctx = self.__ctx.type_ctx
        symbol_ctx = self.__ctx.symbol_ctx
        assert symbol_ctx is not None

        # 1. Type-check capture expressions
        capture_values: dict[str, HIR.Expr] = {}
        for capture in node.captures:
            cap_expr = self.__expr.value(capture.expr)
            # Validate assignment semantics for non-simple types
            if not type_ctx.is_simple_type(cap_expr.type_id):
                check_simple_assign_source(cap_expr, type_ctx, capture.span)
            capture_values[capture.name.name] = cap_expr

        # 2. Resolve parameter types
        parameters: list[Type.Parameter] = []
        for param in node.params:
            param_type_id = self.__ctx.resolve_type(param.var_type)
            parameters.append(Type.Parameter(name=param.name.name, type_id=param_type_id))

        # 3. Resolve return type
        if node.return_type is not None:
            return_type = self.__ctx.resolve_type(node.return_type)
        else:
            return_type = type_ctx.void_id

        # 4. Allocate ClosureType
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

        # 5. Type-check the closure body in a new scope
        symbol_ctx.enter_scope()

        # Register capture variables as local variables in the closure body scope
        for name, expr in capture_values.items():
            symbol_ctx.add_symbol(name, SymbolKind.Variable, expr.type_id)

        # Register parameters as local variables
        for param in parameters:
            symbol_ctx.add_symbol(param.name, SymbolKind.Variable, param.type_id)

        # Type-check the body
        body = self.__expr.check_block(node.body)

        symbol_ctx.exit_scope()

        # 6. Store body for M3 lowering
        type_ctx.store_closure_body(closure_type_id, body, capture_values)

        # 7. Return HIR.Closure (maintaining ClosureType)
        return HIR.Closure(
            span=node.span,
            type_id=closure_type_id,
            captures=capture_values,
            is_place=True,
        )

    def handle_closure_call(self, span: SrcSpan, callee: HIR.Expr,
                            args: list[AST.Arg]) -> HIR.Invoke:
        """Handle a call to a closure value, producing HIR.Invoke."""
        if args and any(arg.name is not None for arg in args):
            raise AnalysisError("named arguments are not supported for closure calls", span)

        type_ctx = self.__ctx.type_ctx
        closure_type_id = type_ctx.resolve_aliases(callee.type_id)
        closure_ty = type_ctx[closure_type_id]
        assert isinstance(closure_ty, Type.ClosureType), f"expected ClosureType, got {type(closure_ty).__name__}"

        params = closure_ty.parameters
        if len(params) != len(args):
            raise AnalysisError(
                f"closure expects {len(params)} arguments, got {len(args)}", span,
            )

        coerced_args = [
            self.__expr.coerce(self.__expr.value(arg.value), param.type_id)
            for arg, param in zip(args, params)
        ]

        return HIR.Invoke(
            span=span,
            callable=callee,
            args=coerced_args,
            type_id=closure_ty.return_type,
            is_place=False,
        )
