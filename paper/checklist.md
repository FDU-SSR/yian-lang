# checklist.md — 论文动笔前待补项清单

本清单列出从「材料备齐」到「投出」之间仍需完成/确认的事项。材料层（数据、图表、引用、
评估）已完成并冻结于本目录；以下均为论文写作期或后续实验期任务。按 assessment.md
第 5 章待补项整理。

## 1. ASan 对比 — 非阻塞推荐项（不阻塞动笔）

- **状态**：推荐项，非阻塞。当前 shootout 套件无 C 移植，无法跑 ASan 对照。
- **已有**：`docs/security-code.md` §10.7 旧基准数据（3 个已移除微负载，
  2026-08-14），已固化为 `figures/fig4_asan_compare.py`（图注显著标注"历史基准，
  非当前套件"）。
- **注意**：该对照在已移除的微负载上，且 ASan 仅空间安全（无时序/UAF 检测），数字
  不可直接外推。论文中如引用须标注负载已移除、仅作量级参考；正文不承诺 ASan 数字。
- **动笔建议**：正文以「未来工作」显式提及；待 §4（C 移植）完成后补跑并用当前套件
  重画 fig4。

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
- [ ] 目视核对 `fig4_asan_compare.png`（时间/RSS 双面板 + 历史基准警告标注）。
- [ ] 对照 `data/performance.csv` 抽查图 1/2 数值；对照 `MECHANISMS.md` 明细表
      抽查图 3 归属。

## 4. shootout C 移植

- 当前 14 基准为 YIAN 源码（规模部分下调，见 `data/shootout-results.md` §1）。
- C 移植用于：(a) 跨语言公平基线；(b) ASan / SoftBound / CETS 对照的前置。
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
- 数据快照：`data/`（performance.csv + shootout-results.md，冻结 2026-08-23）。
- 引用清单：`references/citations.md`（14 条 verified，排除 3 篇未核实文献）。
- 理论与实现一致性：审计 27 条偏差已修复（03bf3a1），`-t ll` 逐字节不变。
