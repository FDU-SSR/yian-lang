"""CFG pass 管线（形态见 `docs/plan/cfg-builder-pass-split-plan.md`）。

构建器（`CfgBuilder`）只负责 HIR→CFG 下降，下降结束后把 `IR.Function` 交给
`run_pipeline`；后续阶段的检查插入/优化 pass 依次挂在这里。
"""
from __future__ import annotations

from compiler.codegen.cfg import ir as IR
from compiler.codegen.cfg.passes import cleanup
from compiler.codegen.cfg.passes.context import PassContext

__all__ = ["PassContext", "run_pipeline"]


def run_pipeline(func: IR.Function, ctx: PassContext) -> IR.Function:
    """按序运行 CFG pass；当前只有 C1 后处理，后续 pass 追加在它之后。"""
    cleanup.run(func, ctx)
    return func
