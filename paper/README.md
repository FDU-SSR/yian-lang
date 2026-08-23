# paper/ — 论文材料汇总

本目录汇总 YIAN 胖指针内存安全机制论文所需的全部材料（评估、数据、图表、引用、待办）。
**不含论文正文**——本目录的定位是「材料备齐，可以动笔」（security-audit-paper-prep
计划 todo 6 / C4）。

## 结论速览

可行性评估（`assessment.md`）：**可以动笔，目标为安全会议**（USENIX Security / CCS /
S&P，梗概级证明 + 实证定位）。性能表述以 `data/performance.csv` 为准：**约半数基准
总开销 ≤1.2×（7/14）**，最差 list 3.46× / towers 1.71× 有明确机制解释；检查成本
≤1.2× 为 10/14。

## 目录索引

| 目录/文件 | 内容 | 一句话说明 |
|---|---|---|
| `assessment.md` | 论文可行性评估（todo 5 产出） | 成果盘点、新颖性分叉、完整性评估、目标场合建议（安全会议）、待补项清单 |
| `data/` | 性能数据冻结快照 | `performance.csv`（权威，14 基准三态成本分解倍率）+ `shootout-results.md`（备查，原始样本与测量协议），均注明来源与冻结日期 2026-08-24 |
| `figures/` | 图表导出脚本 | 4 个 Python（csv+matplotlib）脚本：三态成本分解堆叠图、检查成本 vs 基准、CVE 拦截矩阵（38 CVE × 12 机制）、ASan 对照（当前套件 14 基准）；各可独立运行产出 PNG |
| `references/` | 引用清单副本 | `citations.md`：14 条 verified 引用（排除 3 篇未核实文献：Extensible Metadata / WatchTower / Buddy），附排除说明 |
| `checklist.md` | 待补项清单 | ASan 对比（已完成 retest 2026-08-24）、O-1~O-5 完整证明、图表人工验证、shootout C 移植（已完成）、MECHANISMS 后续维护、数据冻结更新流程 |

## 数据与来源对应

| 材料 | 权威来源 | 备查 |
|---|---|---|
| 性能数字（总/检查成本倍率、ΔRSS） | `data/performance.csv` | `data/shootout-results.md`（2026-08-24 retest 同源快照，与 performance.csv 数据区逐行一致） |
| CVE 拦截归属（25+10+1+2） | `tests/fat_cve/docs/MECHANISMS.md`（adb102a 回填后） | `tests/fat_cve/cve/` 38 目录 |
| 门禁实数 241/75/27/76 + pyright 0（run_tests 门禁 remove-clone-move 后重定基线为 241） | `.omo/evidence/security-audit-paper-prep/task-1.txt` + `.omo/evidence/rebench-sync-paper/task-1.txt`（2026-08-24 基线快照） | `docs/security-code.md` §11.2（2026-08-23 归档门禁记录） |
| ASan 交叉对比（14 基准当前套件，1.58×/0.69×/2.28×） | `data/asan-results.md`（冻结 2026-08-24） | `build/bench/asan-results.md`（实测原始报告）；旧 §10.7 数据（0.35×/0.36×/0.08×，3 负载已移除）仅作先验、标 superseded |
| 引用条目 | `references/citations.md`（排除 3 篇未核实） | `.omo/notes/citations.md`（完整 17 条） |
| 三轮优化收益 | `.omo/evidence/compiler-perf-optimization/` task-4/7 + `assume-inject-perf-regression/task-5.txt` | — |

## 动笔前必读

1. `assessment.md` §5 待补项清单（决定论文哪些数字/论证可以直接引用、哪些留白）。
2. `checklist.md`（动笔到投出之间的任务排布）。
3. `data/README.md`（数据冻结策略：重测后如何更新快照与图）。
4. `references/README.md`（引用排除说明：3 篇未核实文献不得入引用清单）。

## 局限声明（写论文时不得省略）

单线程模型、FFI/ABI 边界、梗概级证明、帧级栈守卫粒度、元数据防伪假设、基准规模下调、
raw 态 binarytree O3 free 消除异常——全部清单见 `assessment.md` 末尾「已知局限」。

## 材料冻结日期

2026-08-24（2026-08-23 theory↔impl 一致性审计 03bf3a1 + MECHANISMS 回填 adb102a
之后；2026-08-24 retest 数据覆写后复核）。
