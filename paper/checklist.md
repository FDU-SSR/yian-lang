# checklist.md — 论文动笔前待补项清单

本清单列出从「材料备齐」到「投出」之间仍需完成/确认的事项。材料层（数据、图表、引用、
评估）已完成并冻结于本目录；以下均为论文写作期或后续实验期任务。按 assessment.md
第 5 章待补项整理。

## 1. ASan 对比 — 已完成（retest 2026-08-24，14 基准当前套件）

- **状态**：**已完成**。`bench/c/` 14 个 C 基准已落地（9 个与 `.an` 规模对齐 +
  断言值双清单，commit 01265bb，验证脚本 14/14 PASS），经
  `scripts/bench_fat.py --asan` 全量实测（14 基准 × 4 腿[C plain / C ASan main /
  C ASan sensitivity（仅 binarytree）/ .an check] 同会话紧邻 × 5 次，`--pin 4`
  绑核；漂移自检 14/14 PASS，retest 会话 + perf-baseline 双参考）。
- **数据**：`data/asan-results.md`（冻结 2026-08-24，来源
  `build/bench/asan-results.md`；含 4 腿中位数/IQR/RSS/CV/样本数 + 敏感性行 +
  三口径倍率 + 披露块）。
- **fig4**：已重画为当前套件 14 基准图——`figures/fig4_asan_compare.py` 读取
  `data/asan-results.md`，脚本含断言（14 基准 × 3 腿、逐基准倍率与几何平均
  1.58×/0.69×/2.28× 与数据文件一致），产出 `fig4_asan_compare.png`；旧 §10.7
  3 负载历史数据已从图中移除，仅在图注标注 superseded。
- **主要数字**（时间几何平均，14 基准）：
  - 口径(i) C ASan / C plain = **1.58×**：ASan 自身开销，与 ASan 论文典型 1.5–2×
    量级一致。
  - 口径(ii) C ASan / .an check = **0.69×**：ASan 快于胖指针——属预期，这是
    跨编译器差异（C clang -O2 无胖指针 vs YIAN -O3 40B 胖指针表示），不可单纯
    归因于安全机制。
  - 口径(iii) .an check / C plain = **2.28×**：胖指针全栈（表示 + 检查发射 +
    锁协议）vs 无检测 C 的端到端差距。
- **storage 内存（实测）**：C plain 1862.9 MB / C ASan 2576.2 MB / .an check
  4270.9 MB（C ASan 为 .an check 的 0.60×）；C 侧 RSS 由叶子层小 chunk malloc
  开销主导（~14.3M 次平均 ~88B 分配），.an 侧为 40B 子指针表示（storage.an
  头注释：array_tree 48B = 40B 子指针 + i32）——放大 = 表示体积 + 分配模式
  差异，与检查发射无直接关系。
- **binarytree sensitivity**：quarantine_size_mb=0 时 RSS 648.8→166.6 MB
  （−74.3%）、时间 −12.1%（唯一 quarantine 活跃基准）。
- **旧数据（superseded）**：`docs/security-code.md` §10.7（3 个已移除微负载，
  0.35×/0.36×/0.08×，2026-08-14）为历史归档，仅可作先验量级参考，不得作为
  论文当前数据引用。
- **注意**：ASan 仅空间安全（无时序/UAF 检测）；`-O` 不对称（C 腿 -O2 vs
  .an 腿 -O3）——跨编译器差异不可单纯归因于安全机制。

## 2. O-1~O-5 完整证明（梗概 → 可勾销）

当前论证为梗概级（`docs/security.md` §5.5，定理 5.1）。义务清单见 security.md 表 4，
逐条落地点见审计对照表（`.omo/evidence/security-audit-paper-prep/task-2.txt`
S-01..S-23）：

- **O-1 / O-2a**（纯枚举）：代码审查即可勾销，审计已给出每条落地点，工作量小。
- **O-3a**（定义 10 组合逻辑）：需写清单调计数器或 CSPRNG 碰撞论证，工作量中。
- **O-3b / O-3c**（写入规则枚举 + 归纳）：锁槽写点已枚举为 4 写点（Malloc/Delete/
  帧进入/帧退出，S-21），轻量归纳，工作量中。
- **O-4 / O-5**：论证细化或机械化，工作量中-大。
- 机械化（Lean/Coq）为可选加分项，工作量很大，不影响投出。

## 3. 图表人工验证（本轮仅机器断言）

`figures/` 脚本已产出 PNG 并通过数据断言（14 基准 / 38 CVE 归属分布），但**未经
人工视觉核对**：

- [ ] 目视核对 `fig1_cost_decomposition.png` 堆叠图（log2 刻度、每基准总成本标注）。
- [ ] 目视核对 `fig2_check_cost.png`（检查成本柱状 + 1.2× 阈值线 + 10/14 标注）。
- [ ] 目视核对 `fig3_cve_matrix.png` 热图（38 CVE × 12 机制，主/次级归属颜色区分）。
- [ ] 目视核对 `fig4_asan_compare.png`（14 基准时间倍率/RSS 双面板 + 当前套件
      数据标注；旧 §10.7 历史数据已 superseded，仅在图注保留）。
- [ ] 对照 `data/performance.csv` 抽查图 1/2 数值；对照 `MECHANISMS.md` 明细表
      抽查图 3 归属。

## 4. shootout C 移植 — 已完成

- **已完成**：`bench/c/` 14 个 C 基准（9 个与 `.an` 规模对齐 + 断言值双清单，
  commit 01265bb），验证脚本 `scripts/verify_bench_c.py` 14/14 PASS。
- 用途已兑现：(a) 跨语言公平基线；(b) ASan 对照前置（§1 已完成，数据见
  `data/asan-results.md`）。
- 规模下调须在论文中显式声明（方法学透明），不构成隐藏。

## 5. MECHANISMS 后续维护

- 机制↔CVE 归属表已回填完成（adb102a，38/38 覆盖），论文引用最终状态即可，无需再补。
- 后续新增 CVE 用例时须同步维护 `tests/fat_cve/docs/MECHANISMS.md` 明细表；
  `figures/fig3_cve_matrix.py` 运行时解析该表，重跑即自动反映。

## 6. 数据冻结更新流程（重测后）

- 若重跑 `scripts/bench_fat.py --suite shootout` 刷新了 `docs/performance.csv`，
  须同步重新冻结 `data/performance.csv`（复制 + 更新头注日期）并重跑 fig1/fig2，
  否则论文引用与仓库当前状态脱节。

## 7. 已就绪（无需再补）

- 论文可行性评估：`assessment.md`（性能表述"约半数 ≤1.2×" 7/14，非"多数"）。
- 数据快照：`data/`（performance.csv + shootout-results.md，冻结 2026-08-24）。
- 引用清单：`references/citations.md`（14 条 verified，排除 3 篇未核实文献）。
- 理论与实现一致性：审计 27 条偏差已修复（03bf3a1），`-t ll` 逐字节不变。
