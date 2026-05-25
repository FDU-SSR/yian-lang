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
    variant: Type.EnumVariant | None,
    unpack_fields: list[int] | None,
    body: HIR.Block,
) -> HIR.MatchArm:
    return HIR.MatchArm(span=span, variant=variant, unpack_fields=unpack_fields, body=body)


def build_enum_match(span: SrcSpan, value: HIR.Expr, arms: list[HIR.MatchArm]) -> HIR.Match:
    return HIR.Match(span=span, value=value, arms=arms)


def build_switch_arm(span: SrcSpan, pattern: int | None, int_width: int, body: HIR.Block) -> HIR.SwitchArm:
    return HIR.SwitchArm(span=span, pattern=pattern, int_width=int_width, body=body)


def build_switch(span: SrcSpan, value: HIR.Expr, arms: list[HIR.SwitchArm]) -> HIR.Switch:
    return HIR.Switch(span=span, value=value, arms=arms)


def build_if_chain(span: SrcSpan, cond_and_blocks: list[tuple[HIR.Expr, HIR.Block]], default: HIR.Block | None) -> HIR.Block:
    """Build nested `If` nodes from a list of (cond, block) pairs and an optional default block.

    Returns a single `Block` that contains the top-level `If` or the default block if provided.
    """
    current_else = default
    for cond, block in reversed(cond_and_blocks):
        current_else = build_block(
            cond.span,
            [HIR.If(span=cond.span, cond=cond, then_branch=block, else_branch=current_else)]
        )
    if current_else is None:
        # empty block as fallback
        return build_block(span, [])
    return current_else
