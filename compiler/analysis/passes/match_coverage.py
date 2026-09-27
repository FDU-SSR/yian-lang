"""Coverage and usefulness analysis for typed match patterns."""

from __future__ import annotations

from collections.abc import Mapping

from compiler.analysis.diagnostics import Diagnostic, Severity, W501_UNREACHABLE_PATTERN
from compiler.analysis.error import AnalysisError
from compiler.analysis.facts.pattern_coverage import PatternCoverageQueries
from compiler.analysis.ty import ty as Type
from compiler.analysis.ty.context import TypeCtx
from compiler.analysis.unit import hir as HIR
from compiler.analysis.unit.def_point import DefPoint
from compiler.analysis.unit.hir_traversal import HirVisitor


class MatchCoverage(HirVisitor):
    """Check matches after compile-time branches have been selected."""

    def __init__(self, def_points: Mapping[int, DefPoint], type_ctx: TypeCtx) -> None:
        self.__def_points = def_points
        self.__ctx = type_ctx
        self.__queries = PatternCoverageQueries(type_ctx)
        self.__warnings: list[Diagnostic] = []

    def run(self, *, recover: bool = False) -> tuple[AnalysisError, ...]:
        errors: list[AnalysisError] = []
        for definition in self.__def_points.values():
            if definition.body is None:
                continue
            try:
                self.visit_expr(definition.body)
            except AnalysisError as error:
                if not recover:
                    raise
                errors.append(error)
        return tuple(errors)

    def warnings(self) -> tuple[Diagnostic, ...]:
        return tuple(self.__warnings)

    def enter_expr(self, expr: HIR.Expr) -> bool:
        if isinstance(expr, HIR.Match):
            self.__check_match(expr)
        return True

    def __check_match(self, expr: HIR.Match) -> None:
        value_type = self.__ctx[self.__ctx.resolve_aliases(expr.value.type_id)]
        type_id = value_type.pointee_type if expr.is_ref and isinstance(value_type, Type.RefType) else expr.value.type_id
        needs_fallback = any(
            arm.guard is not None or self.__queries.contains_opaque(arm.pattern) for arm in expr.arms
        )
        if needs_fallback and (not expr.arms or not self.__is_fallback(expr.arms[-1])):
            raise AnalysisError(
                "Guarded or opaque match requires a final unguarded '_ => ...' or 'name @ _ => ...' arm",
                expr.span,
            )

        rows: list[list[HIR.Pattern]] = []
        side_effect_seen = False
        unconditional_catchall = False
        for arm in expr.arms:
            if unconditional_catchall or (
                not side_effect_seen and self.__queries.witness(rows, arm.pattern, type_id) is None
            ):
                self.__warnings.append(Diagnostic(
                    code=W501_UNREACHABLE_PATTERN,
                    severity=Severity.WARNING,
                    message="unreachable match arm",
                    span=arm.span,
                ))
            if self.__is_fallback(arm):
                unconditional_catchall = True
            if arm.guard is not None or self.__queries.contains_opaque(arm.pattern):
                rows.clear()
                side_effect_seen = True
            elif not side_effect_seen:
                rows.append([arm.pattern])

        if needs_fallback:
            return
        wildcard = HIR.WildcardPattern(expr.span, type_id)
        witness = self.__queries.witness(rows, wildcard, type_id)
        if witness is not None:
            example = witness[0] if witness else "_"
            raise AnalysisError(f"non-exhaustive match: missing {example}", expr.span)

    def __is_fallback(self, arm: HIR.MatchArm) -> bool:
        if arm.guard is not None:
            return False
        pattern = arm.pattern
        return isinstance(pattern, HIR.WildcardPattern) or (
            isinstance(pattern, HIR.BindPattern) and isinstance(pattern.inner, HIR.WildcardPattern)
        )
