"""CFG 的 pass 入口：下降（HIR → CFG IR）→ 检查插入 → 后处理。

三段各一个文件、各一个入口类，由 `compiler/main.py` 按序编排（与中端
`analysis/passes/` 同形）：

- `translator.py::CfgTranslator`：HIR → CFG IR。它的内部实现较重，拆在兄弟目录
  `cfg/lower/`（`builder.CfgBuilder` 逐函数编排 + 各下降簇 + 共用句柄）；
- `insert_checks.py::InsertChecks`：物化下降侧发的语义标记，并重放检查状态机决定
  访问类检查（`CheckRefAccess`/`CheckSafeAccess`/…）的形态与位置；
- `cleanup.py::Cleanup`：去死块 → RPO 排序 → 终结保护。

`context.py` 的 `PassContext` 是后两段共享的只读事实（由下降段随函数一并交出）。
后续若要加检查优化 pass，直接在 `main` 的编排里插一段。
"""