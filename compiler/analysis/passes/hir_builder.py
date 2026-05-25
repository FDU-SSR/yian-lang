from __future__ import annotations

from compiler.analysis.ty import ty as Type
from compiler.analysis.unit import hir as HIR
from compiler.utils.IR.position import SrcSpan


def build_block(span: SrcSpan, stmts: list[HIR.Stmt]) -> HIR.Block:
    return HIR.Block(span=span, stmts=stmts)


def build_loop(span: SrcSpan, stmts: list[HIR.Stmt]) -> HIR.Loop:
    return HIR.Loop(span=span, body=build_block(span, stmts))


def build_enum_match_arm(
    span: SrcSpan,
    variant: Type.EnumVariant,
    unpack_fields: list[int] | None,
    body: HIR.Block,
) -> HIR.EnumMatchArm:
    return HIR.EnumMatchArm(span=span, variant=variant, unpack_fields=unpack_fields, body=body)


def build_enum_match(span: SrcSpan, value: HIR.Expr, arms: list[HIR.EnumMatchArm]) -> HIR.EnumMatch:
    return HIR.EnumMatch(span=span, value=value, arms=arms)
