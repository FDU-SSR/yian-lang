"""Specialize compile-time conditional expressions in typed HIR."""

from __future__ import annotations

from collections.abc import Callable, Mapping

from compiler.analysis.const_eval import ConstantExpressionEvaluator
from compiler.analysis.error import AnalysisError
from compiler.analysis.ty import ty as Type
from compiler.analysis.ty.context import TypeCtx
from compiler.analysis.unit import hir as HIR
from compiler.analysis.unit.def_point import DefPoint
from compiler.analysis.unit.hir_traversal import HirRewriter


class ComptimeIfSpecializer(HirRewriter):
    """Evaluate ``comptime if`` nodes and remove the unselected HIR branch."""

    def __init__(self, def_points: Mapping[int, DefPoint], type_ctx: TypeCtx,
                 raw_pointers: bool, type_size: Callable[[int], int]) -> None:
        self.__def_points = def_points
        self.__type_ctx = type_ctx
        self.__raw_pointers = raw_pointers
        self.__const_eval = ConstantExpressionEvaluator(
            type_ctx, raw_pointers, type_size, "comptime if condition"
        )
        self.__edges: dict[int, set[int]] = {}
        self.__current_def_type_id = -1

    def run(self, *, recover: bool = False) -> tuple[AnalysisError, ...]:
        errors: list[AnalysisError] = []
        for def_point in list(self.__def_points.values()):
            if def_point.body is None:
                continue
            self.__current_def_type_id = def_point.type_id
            try:
                def_point.body = self.rewrite_block(def_point.body)
            except AnalysisError as error:
                if not recover:
                    raise
                errors.append(error)
                def_point.body = None
        return tuple(errors)

    def generated_definitions(self, candidates: dict[int, DefPoint], entry_type_id: int | None) -> dict[int, DefPoint]:
        """Select the specialized procedures reachable from the actual entry."""
        if entry_type_id is None:
            return {}
        key_by_type: dict[int, int] = {}
        for key, def_point in candidates.items():
            key_by_type[key] = key
            key_by_type[def_point.type_id] = key

        entry_key = key_by_type.get(entry_type_id)
        if entry_key is None:
            return {}
        reachable: set[int] = set()
        pending = [entry_key]
        while pending:
            key = pending.pop()
            if key in reachable:
                continue
            reachable.add(key)
            for referenced_type in self.__edges.get(candidates[key].type_id, set()):
                referenced_key = key_by_type.get(referenced_type)
                if referenced_key is not None and referenced_key not in reachable:
                    pending.append(referenced_key)
        return {key: def_point for key, def_point in candidates.items() if key in reachable}

    def rewrite_expr(self, expr: HIR.Expr) -> HIR.Expr:
        if isinstance(expr, HIR.ComptimeIf):
            condition = self.rewrite_expr(expr.cond)
            value, _ = self.__const_eval.evaluate(condition)
            if type(value) is not bool:
                raise AnalysisError("comptime if condition is not compile-time evaluable: condition is not bool", expr.cond.span)
            branch = expr.then_branch if value else expr.else_branch
            return self.rewrite_block(branch)
        if isinstance(expr, HIR.CompileConfig):
            if expr.name != "IS_RAW_MODE":
                raise AnalysisError(
                    f"comptime if condition is not compile-time evaluable: unknown compile configuration '{expr.name}'",
                    expr.span,
                )
            return HIR.BoolLiteral(
                span=expr.span,
                value=self.__raw_pointers,
                type_id=TypeCtx.bool_id,
                is_place=False,
            )

        if isinstance(expr, HIR.Call):
            self.__record_procedure(expr.func)
        elif isinstance(expr, HIR.MethodCall):
            self.__record_procedure(expr.method_id)
        elif isinstance(expr, HIR.TraitObjectCoerce):
            for method_id in expr.method_ids:
                self.__record_procedure(method_id)
        elif isinstance(expr, (HIR.Ty, HIR.Closure, HIR.Var)):
            self.__record_procedure(expr.type_id)

        return super().rewrite_expr(expr)

    def rewrite_children(self, expr: HIR.Expr) -> None:
        if isinstance(expr, HIR.Invoke):
            expr.callable = self.rewrite_expr(expr.callable)
            self.__record_procedure(expr.callable.type_id)
            expr.args = [self.rewrite_expr(arg) for arg in expr.args]
            return
        super().rewrite_children(expr)

    def __record_procedure(self, type_id: int) -> None:
        resolved = self.__type_ctx.resolve_aliases(type_id)
        ty = self.__type_ctx[resolved]
        if isinstance(ty, (Type.FunctionType, Type.MethodType, Type.ClosureType)):
            self.__edges.setdefault(self.__current_def_type_id, set()).add(type_id)
