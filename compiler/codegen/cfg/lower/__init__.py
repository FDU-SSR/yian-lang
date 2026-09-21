"""下降 pass 的内部机器（`passes/translator.py` 的活都在这几个模块里）。

不是 pass 入口：入口与逐函数装配（`CfgTranslator` / `_CfgBuilder`）在 `passes/translator.py`。
这里是各下降簇（`stmts` / `values` / `exprs` / `memory` / `calls` / `sys`）与它们共用的
句柄——`emitter`（发射原语）、`checks`（指针出处状态）、`predicates`（指针族判定），
外加三段 pass 共享的上下文 `cfg_ctx.CfgCtx`。与中端 `analysis/passes/` ∥
`analysis/lowering/` 的分工一致。
"""