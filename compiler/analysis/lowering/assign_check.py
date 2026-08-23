"""
Unified assignment validation for the YIAN compiler.

Provides a single entry point for building assignment HIR nodes.
Assignment follows C-style bitwise-copy semantics: the value is
coerced to the target type and copied by bits into the target place.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Callable

from compiler.analysis.error import AnalysisError
from compiler.analysis.unit import hir as HIR
from compiler.frontend.parse.operator import BinaryOperator

if TYPE_CHECKING:
    from compiler.analysis.ty.context import TypeCtx
    from compiler.frontend.lex.position import SrcSpan


def check_assign_target(target: HIR.Expr, span: SrcSpan) -> None:
    """Raise AnalysisError if *target* is not a valid assignment target."""
    if not target.is_place:
        raise AnalysisError("assignment target must be an l-value", span)


def build_assign(type_ctx: TypeCtx, coerce: Callable[[HIR.Expr, int], HIR.Expr], span: SrcSpan, target: HIR.Expr, value: HIR.Expr) -> HIR.Binary:
    """Build a ``HIR.Binary(Assign, target, value)``.

    1. Verify *target* is a place expression.
    2. Coerce *value* to *target*'s type.
    3. Return the HIR assignment node.
    """
    check_assign_target(target, span)

    if value.type_id != type_ctx.never_id:
        value = coerce(value, target.type_id)

    return HIR.Binary(span=span, op=BinaryOperator.Assign, left=target, right=value, type_id=target.type_id, is_place=False)
