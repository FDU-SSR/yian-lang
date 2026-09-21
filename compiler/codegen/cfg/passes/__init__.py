"""CFG 的 pass 管线：下降（HIR → CFG IR）→ 检查插入 → 后处理。

三段各占本包的一项：

- `lower/`：下降 pass（入口 `lower.translator.CfgTranslator`，逐函数由 `lower.builder.CfgBuilder` 编排）；
- `insert_checks.py`：物化下降侧发的语义标记，并重放检查状态机决定访问类检查的形态；
- `cleanup.py`：去死块 / RPO 排序 / 终结保护。

`context.py` 的 `PassContext` 是后两段共享的只读事实；`run_pipeline` 只驱动后两段
（下降在 `CfgBuilder.build()` 里完成后自己调用它）。后续的检查优化 pass 追加在
检查插入与后处理之间。
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
