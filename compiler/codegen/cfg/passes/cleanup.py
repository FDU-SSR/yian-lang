"""后处理 pass（管线的第 3 段）：去死块 → RPO 排序 → 终结保护。

原先是构建器末尾的三个过程，整体搬出后判定与顺序保持逐字一致；它们只读写
`IR.Function` 自身，下降期状态一个都不用（终结保护只看 `CfgCtx` 的返回类型
与 void 占位通道）。入口是 `Cleanup`，由 `compiler/main.py` 在检查插入之后调用。
"""
from __future__ import annotations

from compiler.analysis.ty.context import TypeCtx
from compiler.codegen.cfg import ir as IR
from compiler.codegen.cfg.lower.cfg_ctx import CfgCtx
from compiler.codegen.error import CodegenError


def _eliminate_dead_code(func: IR.Function) -> None:
    """Remove blocks that are not reachable from the entry block.

    Performs a BFS from the entry block following all forward edges
    (terminator targets), then filters ``func.blocks`` to only
    include reachable blocks.  Phi nodes in surviving blocks are
    cleaned up to remove incoming entries from deleted blocks.
    """
    # ── collect reachable blocks via BFS ──
    # Block is an unhashable dataclass, so track via id(…).
    reachable_ids: set[int] = set()
    worklist = [func.entry]

    while worklist:
        block = worklist.pop()
        if id(block) in reachable_ids:
            continue
        reachable_ids.add(id(block))

        if block.terminator is None:
            continue

        term = block.terminator
        match term:
            case IR.Br():
                worklist.append(term.target)
            case IR.CondBr():
                worklist.append(term.then_block)
                worklist.append(term.else_block)
            case IR.Match():
                for arm in term.arms:
                    worklist.append(arm.body)
                if term.default is not None:
                    worklist.append(term.default)
            case IR.Ret() | IR.Panic() | IR.RuntimeFail() | IR.ProcessExit():
                pass

    # ── filter blocks ──
    func.blocks = [b for b in func.blocks if id(b) in reachable_ids]

    # ── clean up phi nodes ──
    for block in func.blocks:
        surviving_phis: list[IR.Phi] = []
        for phi in block.phis:
            phi.incoming = [
                (pred, val) for pred, val in phi.incoming if id(pred) in reachable_ids
            ]
            if phi.incoming:
                surviving_phis.append(phi)
        block.phis = surviving_phis


def _sort_blocks_rpo(func: IR.Function) -> None:
    """Reorder ``func.blocks`` in reverse post-order.

    Reverse post-order guarantees that for every forward edge
    A -> B in the CFG, block A appears before block B in the
    ordered list.  This ensures that when the LLVM translator
    iterates blocks in list order, every phi node's predecessor
    values have already been registered.

    Back edges (edges that form cycles, e.g. loop back edges)
    are detected via an ``in_progress`` set and are skipped.
    This is safe because loop headers in the current lowering
    do not carry phi nodes that depend on back-edge values.
    """
    # ── build successor map (same pattern as _eliminate_dead_code) ──
    successors: dict[int, list[IR.Block]] = {}
    for block in func.blocks:
        succs: list[IR.Block] = []
        if block.terminator is not None:
            match block.terminator:
                case IR.Br(target=target):
                    succs.append(target)
                case IR.CondBr(then_block=then, else_block=else_):
                    succs.append(then)
                    succs.append(else_)
                case IR.Match(arms=arms, default=default):
                    for arm in arms:
                        succs.append(arm.body)
                    if default is not None:
                        succs.append(default)
                case IR.Ret() | IR.Panic() | IR.RuntimeFail() | IR.ProcessExit():
                    pass
        successors[id(block)] = succs

    # ── add phi incoming edges ──
    # If block B has a phi with incoming from block P, P must appear
    # before B.  Add B as a successor of P so the DFS visits P first.
    for block in func.blocks:
        for phi in block.phis:
            for pred, _ in phi.incoming:
                successors.setdefault(id(pred), []).append(block)

    # ── DFS from entry, collecting postorder ──
    visited: set[int] = set()
    in_progress: set[int] = set()
    postorder: list[IR.Block] = []

    def dfs(block: IR.Block) -> None:
        bid = id(block)
        if bid in visited:
            return
        if bid in in_progress:
            return  # back edge — block already on the DFS stack, skip
        in_progress.add(bid)
        for succ in successors.get(bid, []):
            dfs(succ)
        in_progress.discard(bid)
        visited.add(bid)
        postorder.append(block)

    dfs(func.entry)

    # RPO = reverse of postorder
    # After DCE every block is reachable from entry, so |rpo| == |blocks|
    func.blocks = list(reversed(postorder))


def _guard_termination(func: IR.Function, ctx: CfgCtx) -> None:
    """Ensure every block has a terminator.

    - void-returning functions: patch unterminated blocks with ``Ret(void_reg)``.
    - non-void-returning functions: raise ``CodegenError`` if any block is unterminated.
    """
    for block in func.blocks:
        if block.terminator is not None:
            continue
        if ctx.return_type == TypeCtx.void_id:
            block.terminator = IR.Ret(ctx.new_void_value())
        else:
            raise CodegenError(
                f"Function '{func.name}' has unterminated block '{block.label}'; "
                f"non-void functions must have explicit return in all control paths.",
                ctx.span,
            )


class Cleanup:
    """后处理 pass（管线第 3 段）：逐函数 去死块 → RPO 排序 → 终结保护。

    顺序与搬移前逐字一致；编排在 `main`。
    """

    def __init__(self, contexts: dict[int, CfgCtx]) -> None:
        self.__contexts = contexts

    def run(self) -> None:
        for ctx in self.__contexts.values():
            func = ctx.function
            _eliminate_dead_code(func)
            _sort_blocks_rpo(func)
            _guard_termination(func, ctx)
