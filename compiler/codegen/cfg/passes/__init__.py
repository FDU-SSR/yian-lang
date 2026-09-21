"""CFG pass 管线：检查插入 → 后处理（去死块 / RPO 排序 / 终结保护）。

本包只放 pass 本身（`insert_checks` / `cleanup`）与它们的东西：共享上下文
`context`、以及这个管线驱动（`insert_checks` 内部的出处重放 `_Provenance` 不外露）。
下降期的发射句柄与指针状态在 `lower/`——构建器（`CfgBuilder`）先完成 HIR→CFG 下降，
再把 `IR.Function` 交给 `run_pipeline`；后续的检查优化 pass 追加在检查插入与后处理之间。
"""
from __future__ import annotations

from compiler.codegen.cfg import ir as IR
from compiler.codegen.cfg.passes import cleanup, insert_checks
from compiler.codegen.cfg.passes.context import PassContext

__all__ = ["PassContext", "run_pipeline"]


def run_pipeline(func: IR.Function, ctx: PassContext) -> IR.Function:
    """按序运行 CFG pass：检查插入（标记物化 + 状态机重放）→ 后处理。"""
    insert_checks.run(func, ctx)
    cleanup.run(func, ctx)
    return func
