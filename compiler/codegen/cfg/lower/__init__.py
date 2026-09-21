"""HIR → CFG 下降的各簇（语句 / 惰值 / 聚合 / 调用 / 内存 / 系统内建 / 表达式）。

本目录是**下降期自己的**东西：各下降簇 + 它们共用的句柄（`emitter`：发射原语；
`checks`：指针出处状态；`predicates`：指针族判定）。检查插入与后处理 pass 在
`passes/`，只经 `PassContext` 拿到本目录产出的 `IR.Function`。
"""
