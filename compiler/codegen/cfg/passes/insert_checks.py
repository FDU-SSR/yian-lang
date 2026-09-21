"""P8（路线 B）：把 `CheckRequest` 标记物化为具体检查节点。

逐规则迁移：只有已迁移的规则会发标记，未迁移的仍由下降侧直接发 `Check*`。
本 pass 目前覆盖 R13（`PtrDiff`/`PtrCmp`）与 R15（裸数组上界）三条最"语义化"的规则，
作为路线 B 的端到端样板；其余规则按计划 §5.5 分组迁移。
"""
from __future__ import annotations

from compiler.codegen.cfg import ir as IR
from compiler.codegen.cfg.passes.context import PassContext


def __materialize(request: IR.CheckRequest) -> IR.Stmt:
    """按 kind 物化标记（未知 kind 直接报错，避免静默丢检查）。"""
    match request.kind:
        case IR.CHECK_REQUEST_PTRDIFF:
            lhs, rhs = request.operands
            return IR.CheckPtrDiff(lhs=lhs, rhs=rhs)
        case IR.CHECK_REQUEST_PTRCMP:
            lhs, rhs = request.operands
            return IR.CheckPtrCmp(lhs=lhs, rhs=rhs)
        case IR.CHECK_REQUEST_RAW_BOUNDS:
            return IR.CheckRawBounds(index=request.operands[0], length=request.extra)
        case _:
            raise ValueError(f"unknown check request kind: {request.kind}")


def run(func: IR.Function, ctx: PassContext) -> None:
    """就地替换：标记位置即检查位置（与迁移前的节点序列逐条对应）。"""
    del ctx  # 语义上下文都编码在标记里，暂不需要 type_ctx
    for block in func.blocks:
        if not any(isinstance(stmt, IR.CheckRequest) for stmt in block.stmts):
            continue
        stmts: list[IR.Stmt] = []
        for stmt in block.stmts:
            if isinstance(stmt, IR.CheckRequest):
                stmts.append(__materialize(stmt))
            else:
                stmts.append(stmt)
        block.stmts = stmts
