"""下降 pass（HIR → CFG IR）：管线第一段。

入口是 `translator.CfgTranslator`（模块级：按 `DefPoint` 逐个下降并汇总）；每个函数体由
`builder.CfgBuilder` 降成一条控制流图，下降结束后调用 `run_pipeline` 把 `IR.Function`
交给后两段（检查插入 → 后处理）。

本包内部：`builder` 是编排者与 pass 入口；各下降簇（`stmts` / `values` / `exprs` /
`memory` / `calls` / `sys`）承担具体下降；三者共用的句柄在 `emitter`（发射原语）、
`checks`（指针出处状态）、`predicates`（指针族判定）。
"""
