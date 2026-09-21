"""下降 pass 的内部实现（`passes/translator.py` 那一重活都在这里）。

不是 pass 入口：`passes/translator.py::CfgTranslator` 负责编排整批函数，这里的
`builder.CfgBuilder` 负责单个函数体的下降，其余模块是各下降簇与三者共用的句柄——
`emitter`（发射原语）、`checks`（指针出处状态）、`predicates`（指针族判定）。
与中端 `analysis/passes/` ∥ `analysis/lowering/` 的分工一致。
"""