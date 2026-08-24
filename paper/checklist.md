# SecL 论文重写验收清单

## 已完成

- [x] 冻结注册标题，不修改标题。
- [x] 修正 T*->T[] 剩余切片、one-past/空切片转引用。
- [x] 实现固定 32B 块头的单线程稳定堆池。
- [x] 保留单调键生成，排除 SENTINEL，耗尽时 trap。
- [x] 补齐转换、UAF、双释放、复用容量和键边界回归。
- [x] 重写威胁模型、机器状态、小步语义和 O-1--O-5 完整纸面证明。
- [x] 重写真实编译流水线、CFG 对应、堆池和三模式实现说明。
- [x] 将帧锁移入固定地址的独立影子栈，并补充递归、复用与优化 IR 回归。
- [x] 回归：241/241、84/84、27/27、76/76，合计 428/428。
- [x] 拉取并校验远程指定机器的 2026-08-25 重测快照：14×3，CPU 4，warmup 1，正式样本 5，三态紧邻。
- [x] 拉取同一机器的 C plain/C ASan/SecL check 快照：43 条腿，每腿 5 个样本。
- [x] 同步 paper/data 冻结快照、正文输入和图表断言；当前数据对应帧锁改动后的实现。

## 图表

- [x] 用重测数据生成三态成本分解 PDF，并核对 14 行断言。
- [x] 生成 38-CVE 机制汇总图；完整矩阵只作为候选附录图。
- [x] 生成 ASan 候选图并核对重测报告中的三口径几何平均。
- [x] 重画设计流程图：三级指针、堆池、帧锁、CFG→LLVM 检查链。
- [x] 人工目视检查候选图。
- [x] 在 LaTeX 实际版面核对字号和标签可读性。

## 论文

- [x] 重写摘要、引言、贡献和 Problem Statement。
- [x] 重写 Methodology、Design and Implementation 与完整证明。
- [x] 重写 Related Work，只使用已核实引用。
- [x] 重写 Experiments、Discussion、Conclusion 和 Appendix。
- [x] 清除旧实现/实验残留。
- [x] latexmk 完整构建并检查引用、图表、公式和版面。
- [x] 逐字确认标题仍为 Toward Language-Level Memory Safety Using Unified Fat Pointers。
- [x] 分别提交 yian 与外层论文仓库，外层不包含嵌套 yian 仓库。
