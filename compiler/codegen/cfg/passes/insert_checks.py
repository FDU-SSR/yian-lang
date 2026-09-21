"""P8（路线 B）：把 `CheckRequest` 标记物化为具体检查节点。

逐规则迁移：只有已迁移的规则会发标记，未迁移的仍由下降侧直接发 `Check*`。
当前覆盖：R13（`PtrDiff`/`PtrCmp`）、R15（裸数组上界）、R16（数组退化 `InBounds`、
`T[]→T&` 的 `SliceNonEmpty`）、R17（`CheckViewAccess`）、R18（receiver `InBounds`）、
R11/R12 的发射点（`ElementArith`、`FieldPtr` 义务补发的 `InBounds`）；
`live` 等下降期已判定的上下文先编码进标记，等第 4 组把 provenance 分析搬进 pass 后再由 pass 自行推导。
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
        case IR.CHECK_REQUEST_VIEW:
            return IR.CheckViewAccess(view=request.operands[0], live=request.live)
        case IR.CHECK_REQUEST_SLICE_NONEMPTY:
            return IR.CheckSliceNonEmpty(ptr=request.operands[0])
        case IR.CHECK_REQUEST_IN_BOUNDS:
            return IR.CheckInBounds(ptr=request.operands[0])
        case IR.CHECK_REQUEST_ELEMENT_ARITH:
            return IR.CheckElementArith(base=request.operands[0], offset=request.operands[1])
        case _:
            raise ValueError(f"unknown check request kind: {request.kind}")


def run(func: IR.Function, ctx: PassContext) -> None:
    """就地替换：标记位置即检查位置（与迁移前的节点序列逐条对应）。

    同时消费"刚分配窗口"标记（§5.6 事实外化）：pass 自己维护一份窗口集合
    （Begin 加入、End 移除），第 2 步起由它独占 `live_known` 判定，届时下降侧不再登记。
    """
    del ctx  # 语义上下文都编码在标记里；checks 句柄在第 2 步才由 pass 使用
    live_known: set[str] = set()
    for block in func.blocks:
        has_marker = any(
            isinstance(stmt, (IR.CheckRequest, IR.LiveKnownBegin, IR.LiveKnownEnd))
            for stmt in block.stmts
        )
        if not has_marker:
            continue
        stmts: list[IR.Stmt] = []
        for stmt in block.stmts:
            match stmt:
                case IR.LiveKnownBegin(root=root) if isinstance(root, IR.Reg):
                    live_known.add(root.name)
                case IR.LiveKnownEnd(root=root) if isinstance(root, IR.Reg):
                    assert root.name in live_known, "live-known window closed without opening"
                    live_known.discard(root.name)
                case IR.CheckRequest():
                    stmts.append(__materialize(stmt))
                case _:
                    stmts.append(stmt)
        block.stmts = stmts
