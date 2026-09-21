"""CFG 的 pass 入口：下降（HIR → CFG IR）→ 检查插入 → 后处理。

三段各一个文件、各一个入口类，由 `compiler/main.py` 按序编排（与中端
`analysis/passes/` 同形）：

- `translator.py::CfgTranslator`：HIR → CFG IR（逐函数装配是文件内的私有 helper
  `_CfgBuilder`）；各下降簇与共用句柄这些机器拆在兄弟目录 `cfg/lower/`；
- `insert_checks.py::InsertChecks`：物化下降侧发的语义标记，并重放检查状态机决定
  访问类检查（`CheckRefAccess`/`CheckSafeAccess`/…）的形态与位置；
- `cleanup.py::Cleanup`：去死块 → RPO 排序 → 终结保护。

三段共享同一个 `CfgCtx`（在 `lower/cfg_ctx.py`，形态对齐中端 `analysis/lowering/sem_ctx.py`）：
session 级资源构造即定，函数级事实由下降段 `begin_def()` 写入，之后三段读的是同一份。
后续若要加检查优化 pass，直接在 `main` 的编排里插一段并接同一个 ctx。
"""