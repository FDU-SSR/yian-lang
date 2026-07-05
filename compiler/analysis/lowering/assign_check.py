"""
Unified assignment validation for the YIAN compiler.

Provides a single entry point for building assignment HIR nodes,
ensuring consistent enforcement of the assignment constraints
defined in bak/design.md §2.
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


def is_simple_assign_source(expr: HIR.Expr) -> bool:
    """Return True if *expr* qualifies as a simple-assignment source.

    Per bak/design.md §2.2: simple assignment is allowed when the source is
    a literal, a construction expression, or a function/method call.
    """
    if isinstance(expr, (HIR.IntLiteral, HIR.FloatLiteral, HIR.CharLiteral, HIR.StrLiteral, HIR.BoolLiteral, HIR.NullptrLiteral)):
        return True
    if isinstance(expr, (HIR.StructConstruct, HIR.VariantConstruct, HIR.Array, HIR.ArrayRepeat, HIR.Tuple)):
        return True
    if isinstance(expr, (HIR.Call, HIR.MethodCall, HIR.Invoke)):
        return True
    if isinstance(expr, HIR.BitCopy):
        return True
    return False


def check_simple_assign_source(expr: HIR.Expr, type_ctx: TypeCtx, span: SrcSpan) -> None:
    """Raise AnalysisError if *expr* is not a valid simple-assignment source
    for a non-simple target type."""
    if is_simple_assign_source(expr):
        return
    name = type_ctx.get_name(expr.type_id)
    raise AnalysisError(
        f"type '{name}' does not support simple assignment; "
        f"use .clone() or .move()", span,
    )


def build_assign(type_ctx: TypeCtx, coerce: Callable[[HIR.Expr, int], HIR.Expr], span: SrcSpan, target: HIR.Expr, value: HIR.Expr) -> HIR.Binary:
    """Build a ``HIR.Binary(Assign, target, value)`` with full validation.

    1. Verify *target* is a place expression.
    2. Coerce *value* to *target*'s type.
    3. Enforce assignment constraint (§2.2 / §2.3).
    4. Return the HIR assignment node.
    """
    check_assign_target(target, span)

    if value.type_id != type_ctx.never_id:
        value = coerce(value, target.type_id)

    if not type_ctx.is_simple_type(target.type_id):
        check_simple_assign_source(value, type_ctx, span)

    return HIR.Binary(span=span, op=BinaryOperator.Assign, left=target, right=value, type_id=target.type_id, is_place=False)
