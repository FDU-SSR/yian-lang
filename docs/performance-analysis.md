# 胖指针性能瓶颈分析

本文档汇总 fat-perf-bottleneck 计划的三层瓶颈分析（三态 IR 差分、反汇编对比、perf 采样）与 bench-per-bench-3state 计划的跨会话测量假象修复，将 `.omo/evidence/fat-perf-bottleneck/report.md` 的分析结论整理为正式性能报告。文中数值以 `.omo/evidence/fat-perf-bottleneck/` 与 `.omo/evidence/bench-per-bench-3state/` 为准，基准运行数据引用 `docs/shootout-results.md`。本版（2026-08-21 bench-rerun）在 2026-08-18 三态紧邻重测重写的基础上，由 2026-08-21 bench-rerun 全量重测（c8）刷新当前态数值：修复了 O3 报告中"胖快于裸"的跨会话假象，纳入 CVE O3 通过性说明，并新增 Fix B / del-view / T[]/T& 直绑优化后的实测结论（证据: bench-ptr-to-view task-1..5）。**注（2026-08-24 retest）**：本文档 c8 会话性能数据已随 2026-08-24 retest 全量覆写——`docs/performance.csv` / `docs/shootout-results.md` 已同步为 retest 数据（commit 4f53313），冻结快照见 `paper/data/`；文中 c8 数值保留为历史记录，历史数据见 git 历史。

## 背景: 胖指针表示与三态

YIAN 的胖指针为 5 字段 40B 值，字段依次为 data（有效数据地址）、lock_ptr（锁槽指针）、key（键值）、index（元素下标）、size（元素个数）。内存安全机制围绕该表示展开: 每次指针访问前检查 `live`（锁槽键比较）与 `in_bounds`（下标越界），失败经 `llvm.trap` 终止程序。为量化该机制的开销，基准评测采用三态编译:

| 态 | 名称 | 表示 | 检查 |
|---|---|---|---|
| ①raw | 裸指针 | 8B 单指针 | 无（零安全基线） |
| ②nocheck | 胖无检查 | 40B 五字段 | 关闭（保留锁槽/帧锁） |
| ③check | 完整胖指针 | 40B 五字段 | 全部开启 |

三态两两差分把总开销拆成两部分: ③−② 为纯检查成本，②−① 为纯表示成本。①raw 由 `bench/shootout_raw/` 套件配合 `--raw-pointers` 编译（裸模式下隐式 coerce 被拒，需显式 `from_raw_parts`）；②nocheck 用 `--no-fat-checks` 关闭检查发射但保留 40B 表示与锁槽/帧锁，故 ②−① 反映纯表示成本。数值口径: 成本分解以倍率为主（同基准内相对 raw，不受机器负载影响），辅以毫秒绝对值。

报告目的: 定位胖指针内存安全开销的落点（检查发射、胖表示、锁槽帧锁、元数据传递），按基准给出瓶颈形态与优化方向，为后续优化提供数据基线与可复现命令。

**本版更新（bench-per-bench-3state）**: 旧测量流程先跑完全部 14 基准的两种胖形态（~44 分钟）再跑全部裸指针形态（~26 分钟），两段时间窗系统状态不同，raw 段恰好系统慢 ~2.1×，导致 O3 报告中出现 9 个基准"胖快于裸"的假象（详见 §0 跨会话假象分析）。本版将测量改为每基准内三态紧邻（check→nocheck→raw，方案 A），同基准三态共享同一系统状态窗口，还原真实关系。

## 0. 跨会话测量假象与修复（bench-per-bench-3state）

### 0.1 假象表现: O3 报告 9 个"胖快于裸"

2026-08-18 的 O3 全量重测报告（`docs/shootout-results.md` 4230dc0 版本）中，14 个基准有 **9 个** check 态中位数 ≤ raw 态中位数（倍率 ≤ 1.00×）: bounce 0.90×、fann 0.47×、fasta 0.72×、list 1.00×、mand 0.46×、nbody 0.62×、permute 0.87×、revcomp 0.96×、spectralnorm 0.94×。nocheck 态更多（10 个 < 1.00×）。这与三态语义直接矛盾: check 态 = raw 态代码 + 40B 表示 + 全部检查（指令超集），静态分析（§1.1）与反汇编（§1.2）均显示 check 二进制显著更大、分支更多，不可能系统性快于 raw。属于测量假象，而非性能事实。

### 0.2 根因: 跨会话系统状态差异

旧测量结构（bench_fat.py 原 main L802-805）: `run_suite` 先完成全部基准的 check/nocheck（~44 分钟），`complement_raw` 再完成全部 raw（~26 分钟）。两段运行分属不同时间窗，系统状态（负载/频率/后台任务）不同。定量证据:

**证据 1 — 二进制逐字节相同，时间却慢 ~2.18×**: 对同一 raw 基准，`-O2` 与 `-O3` 编译产物**逐字节相同**（list_raw: `cmp` 一致，20632 字节，`-O3` 对 YIAN IR 不产生新代码优化）。但同一二进制在 O3 会话 raw 段的测量比 O2 时代（2026-08-17 会话）慢 **2.11–2.28×（均值 2.18×）**，fann/nbody/mand 恰为 2.177×:

| 基准 | raw O2 时代中位 (ms) | raw O3 会话中位 (ms) | O3/O2 比率 |
|---|---|---|---|
| binarytree | 3654.5 | 8203.4 | 2.24× |
| bounce | 9422.7 | 20555.3 | 2.18× |
| fann | 2864.9 | 6238.5 | **2.177×** |
| fasta | 3003.6 | 6551.8 | 2.18× |
| list | 19818.4 | 43276.3 | 2.18× |
| mand | 4219.5 | 9189.6 | **2.177×** |
| nbody | 18521.6 | 40324.0 | **2.177×** |
| permute | 11927.2 | 25638.4 | 2.15× |
| queen | 15055.2 | 33059.3 | 2.20× |
| revcomp | 3404.5 | 7559.5 | 2.22× |
| sieve | 11181.3 | 23582.5 | 2.11× |
| spectralnorm | 7941.5 | 17120.4 | 2.16× |
| storage | 1342.0 | 3057.5 | 2.28× |
| towers | 4597.0 | 9911.3 | 2.16× |

**证据 2 — 同会话 check 相位正常**: 同一 O3 会话的 check 态（与 O2 时代同为正常系统窗口）比率仅 0.97–1.15×（均值 1.05×）——check 与 O2 时代基本同速。慢 2.18× 仅发生在 raw 段，指向系统状态而非代码。

**证据 3 — 同会话紧邻测量还原 raw ≤ check**: O2 时代的同会话三态测量（2026-08-14 `build/bench/fat-session.log`，每基准内三态紧邻）14 基准全部 raw ≤ check（check/raw = 1.47–4.34×），从未出现"胖快于裸"。历史成本分解（fat-perf-remaster/task-2.txt: spectralnorm 3.22× / queen 3.64× / list 3.42×）同样一致。

**证据 4 — check/nocheck 关系在假象会话与紧邻会话一致**: 两态胖形态在旧结构中本就紧邻，未受污染——spectralnorm check/nocheck 假象会话 1.044× vs 本轮紧邻 1.043×、list 1.451× vs 1.458×，逐一吻合。假象仅污染 raw 相位。

### 0.3 修复（方案 A）: 每基准三态紧邻

`scripts/bench_fat.py` `run_suite` 改造: 每基准循环内按 check → nocheck → raw 紧邻编译并测量（同一基准三态在 ~2–5 分钟内连续完成，`[done]` 输出"三态紧邻 (check/nocheck/raw) 各 N 次"）；main 改为单次 `run_suite` 调用，删除全会话后的 `complement_raw` 补测。系统负载波动对同基准三态影响一致，倍率（相对 raw）不再被跨会话偏差污染。未采用方案 C（多次交替测量，耗时 ×3）——同会话紧邻已消除主导偏差。

### 0.4 修复确认: 三态真实关系

重测后（数据见 `docs/shootout-results.md` 新表）: 假象消失。2026-08-18 三态紧邻重测 14/14 基准 raw ≤ check（check/raw = 1.00–2.84×；2026-08-19 Option<T&> 迁移后重测 1.01–2.48×），raw 绝对值回到 O2 时代水平（list raw 43276→21347ms、fann 6239→3076ms），确认假象会话 raw 段整体慢 ~2.1×。2026-08-21 bench-rerun 全量重测（c8，无 --pin）: check/raw 倍率 0.96–2.32×，nbody 0.96×、mand 0.99× 再现 <1.00× 但偏差 ≤4%、落在各态 IQR 噪声带内——**所有 <1.00× 倍率落在 IQR 噪声带内（无系统性胖快于裸）**，0.47× 级（50%+ 差距）系统性倒置未再现。注: 跨会话绝对毫秒不可直接比较（§0.2 教训，如 list raw 2026-08-18 21347ms → c8 218941ms），倍率（同基准相对）为稳定口径。

## 1. 三层分析方法

分析针对三个代表性基准: spectralnorm（静态检查密度冠军）、queen（表示成本占比冠军）、list（总成本绝对冠军）。选取依据为同会话成本分解（表 1）。

表 1: TOP 基准选取（三态紧邻重测成本分解, 倍率 vs ①raw）

| 基准 | 表示(②/①) | 检查(③/②) | 总(③/①) | 选取理由 |
|---|---|---|---|---|
| **spectralnorm** | 1.02× | 1.01× | 1.02× | 静态检查密度冠军（cfg 27 节点、icmp 检查差分 +81；c8 三态倍率噪声级，检查占比 30.7%，raw 绝对值跨会话偏高致倍率收窄） |
| **queen** | 2.05× | 1.01× | 2.08× | 表示成本占比冠军（7514.3ms，占总成本 97.5%） |
| **list** | 1.85× | 1.15× | 2.13× | 总成本绝对冠军（465723.6ms）+ 绝对检查成本冠军（60866.6ms）+ 绝对表示成本冠军（185915.5ms）；表示占比 75.3%（检查占比 24.7%） |

注: 表内倍率为 2026-08-21 bench-rerun 重测（c8）; §1–3 的 IR 差分 / asm / perf 采样基于迁移前树，分析方法与结论方向（表示成本为多数大成本基准主导开销）不变。**（2026-08-24 retest 后: c8 数值已被 retest 覆写, 历史见 git 历史, 分析方法与结论方向不变。）**

### 1.1 第 1 层: 三态 IR 差分

方法: 对同一基准分别以三态编译出 LLVM IR（`-t ll`），统计 icmp / extractvalue / getelementptr / trap / define / IR 行数，以及 CFG 检查节点数（`build/cfg.txt` 中的 `check_safe_access` / `check_in_bounds` / `check_element_arith`）。检查差分 = ③−②，表示差分 = ②−①。IR 统计与优化级别无关（-O2/-O3 产物逐字节相同），数值沿用 fat-perf-bottleneck 会话（当前树生成）。

表 2: 原始统计（当前树新生成）

| 基准 | 态 | icmp | extractvalue | getelementptr | trap | define | IR 行数 | cfg 检查节点 |
|---|---|---|---|---|---|---|---|---|
| spectralnorm | ③check | 109 | 277 | 45 | 37 | 14 | 1799 | **27**（safe14+inb11+earith2） |
| spectralnorm | ②nfc | 28 | 171 | 45 | 4 | 14 | 1219 | 0 |
| spectralnorm | ①raw | 15 | 7 | 5 | 4 | 15 | 591 | 0 |
| queen | ③check | 116 | 230 | 32 | 40 | 16 | 1687 | **26** |
| queen | ②nfc | 27 | 118 | 32 | 5 | 16 | 1053 | 0 |
| queen | ①raw | 10 | 10 | 6 | 5 | 16 | 506 | 0 |
| list | ③check | 75 | 173 | 32 | 23 | 11 | 958 | **18** |
| list | ②nfc | 23 | 103 | 32 | 2 | 11 | 588 | 0 |
| list | ①raw | 12 | 4 | 12 | 2 | 11 | 342 | 0 |

表 3: 差分（纯检查 = ③−②; 纯表示 = ②−①）

| 基准 | 检查差分 icmp / extractvalue / trap / lines | 表示差分 icmp / extractvalue / gep / lines |
|---|---|---|
| spectralnorm | **+81 / +106 / +33 / +580** | +13 / **+164** / +40 / **+628** |
| queen | **+89 / +112 / +35 / +634** | +17 / +108 / +26 / +547 |
| list | +52 / +70 / +21 / +370 | +11 / +99 / +20 / +246 |

要点:

- **检查节点密度**: 每 cfg 检查节点约 3 个 icmp（spectralnorm 81/27=3.0、queen 89/26=3.4、list 52/18=2.9），对应锁匹配比较 + 越界比较 + 溢出检查的复合结构；每个检查以 `br → trap 块`（`llvm.trap`）终结。
- **表示差分的绝对主体是 extractvalue**（增量 99~164，为全部单项差分最大）: 40B 胖指针（data/lock_ptr/key/index/size 5×8B）在循环中被反复字段提取，正是"胖表示"的 IR 指纹。
- trap 增量（21~35）全部来自检查（③−②）；nfc 与 raw 的残余 trap（2~5）为除零、负数取模等非指针防御。

### 1.2 第 2 层: 反汇编对比

方法: 编译 check 与 raw 二进制（-O2 与 -O3 产物逐字节相同），`objdump -d` 反汇编，统计 asm 行数、分支指令、call、mov 内存访问、ud2（trap 点），并逐函数识别检查序列。

命令:

```
python3 -m compiler.main -O2 lib bench/shootout/<name>.an -o /tmp/fpb/<name>
python3 -m compiler.main --raw-pointers -O2 lib bench/shootout_raw/<name>.an -o /tmp/fpb/<name>_raw
objdump -d /tmp/fpb/<name>
objdump -d /tmp/fpb/<name>_raw
```

表 4: 反汇编统计

| 基准 | 态 | asm 行 | 分支指令 | call | mov 内存访问 | ud2（trap 点） |
|---|---|---|---|---|---|---|
| spectralnorm | ③check | 1655 | 160 | 40 | 386 | 8 |
| spectralnorm | ①raw | 778 | 39 | 43 | 196 | 1 |
| **Δ** | | +877 | **+121** | −3 | +190 | +7 |
| queen | ③check | 1799 | 165 | 41 | 472 | 9 |
| queen | ①raw | 676 | 34 | 41 | 149 | 1 |
| **Δ** | | **+1123（2.7×）** | +131 | 0 | **+323** | +8 |
| list | ③check | 1244 | 107 | 29 | 407 | 6 |
| list | ①raw | 466 | 30 | 29 | 86 | 1 |
| **Δ** | | +778 | +77 | 0 | **+321（4.7×）** | +5 |

检查序列识别（以 spectralnorm 热函数 `index` 为标本）:

- **check 态 `index`**（约 0x1890~0x1954，约 90 条指令）: 栈上加载 40B 胖指针字段（mov ×10）→ **锁检查**（`test %rdx,%rdx; je` + `cmp %rcx,(%rdx); sete; test; jne`，对 lock slot 的一次内存读）→ 第二字段锁检查 → **越界检查**（`cmp size,%rax; jae`）→ 才执行真正数据访问 `mov (%rcx,%rax,8),%rax`。错误路径统一 `jmp 1955 → ud2`。
- **raw 态 `index`**（约 0x1490~0x14a4，3 条指令）: `mov (%rsi),%rax; shl $3; add (%rdi),%rax; ret`。**无分支、无检查**。
- 每个检查 = 比较 + 条件分支，错误路径共享 ud2 块（IR 37 个 trap → 二进制 8 个 ud2，LLVM 合并）。check 态 ud2 6~9 个 vs raw 1 个，增量 5~8 全为检查 trap。
- **list `is_shorter_than` prologue**（87.67% 自占热点）: 函数入口把两个 40B 胖指针参数从栈读入再全部 spill（`-0x80~-0x18(%rsp)`），20 余条 mov。40B 聚合按值传递的机器码形态（元数据传递 + 胖表示）。

### 1.3 第 3 层: perf 采样

方法: 软件事件降级（详见附录 A）。`perf stat` 用软件事件（task-clock / page-faults / context-switches / cpu-migrations）测墙钟时间与内存足迹代理；`perf record` 用 cpu-clock 对 check 版采样热点函数。perf 层以 -O2 二进制采样（与 -O3 逐字节相同，结论不变），且 check/raw 同会话连续执行，不受跨会话假象影响。

命令:

```
PERF=/usr/lib/linux-tools-5.15.0-187/perf
taskset -c 4 $PERF stat -e task-clock,page-faults,context-switches,cpu-migrations ./<bin>
taskset -c 4 $PERF record -e cpu-clock -F 1000 -g -o <name>_check.perf ./<name>
$PERF report -i <name>_check.perf --stdio
```

表 5: perf stat（软件事件，单次采样，taskset -c 4）与三态紧邻中位数对照

| 基准 | 态 | elapsed | task-clock | perf check/raw | 紧邻中位数 check/raw |
|---|---|---|---|---|---|
| spectralnorm | ③check | 16.75 s | 17.97 s | **1.90×** | **1.89×** |
| spectralnorm | ①raw | 8.83 s | 9.43 s | | |
| queen | ③check | 42.29 s | 45.29 s | **2.49×** | **2.49×** |
| queen | ①raw | 17.01 s | 18.23 s | | |
| list | ③check | 47.76 s | 51.16 s | **2.20×** | **2.17×** |
| list | ①raw | 21.69 s | 23.22 s | | |

perf 层单次同会话采样与三态紧邻 5 次中位数高度吻合（1.90/2.49/2.20 vs 1.89/2.49/2.17），相互印证——perf 层本就同会话执行，未受假象影响。注: perf 采样为迁移前树（2026-08-17）；2026-08-19 Option<T&> 迁移后重测 list check/raw 中位 2.06×（见 shootout-results.md）。

表 6: perf record 热点（check 版，共 106,591 samples）

| 基准 | samples | 热点函数（Self%） |
|---|---|---|
| spectralnorm | 16,594 | `index` 24.18% · `mult_Av` 21.13% · `mult_Atv` 21.05% · `ptr` 13.70% · `as_struct` 13.35% · `A` 6.59% |
| queen | 42,456 | `is_safe` 33.43% · `index` 27.90% · `as_struct` 15.51% · `ptr` 12.88% · `place_queen` 10.14% |
| list | 47,541 | **`is_shorter_than` 87.67%** · `tail` 12.33% |

`index` / `ptr` / `as_struct` 为编译器生成的胖指针辅助函数（索引、字段访问，内含检查）。

## 2. 四类开销归因

### ① 检查发射（icmp + trap 分支 / CheckSafeAccess 等节点）→ 对应 ③−②

- IR: check 态 icmp 增量 52~89、trap 增量 21~35、cfg 检查节点 18~27（仅 check 态存在，safe / inbounds / element_arith 三型）。
- 机器码: 每检查 = 比较 + 条件分支 → ud2；`index` 函数 5 分支、2 锁比较 + 1 越界比较；二进制分支指令 check 107~165 vs raw 30~39，增量 77~131；ud2 点增量 5~8。
- **每个检查节点约 3 icmp**（锁匹配 + 越界 + 溢出）。
- 同会话映射: ③−② 倍率 **0.97–1.64×**（14 基准中 6 个 ≤ 1.10×；permute 1.64×、revcomp 1.37× 显著）；绝对成本显著者 list（60866.6ms，占比 24.7%）、towers（7582.4ms，27.1%），次之 permute 1925.1ms、binarytree 1361.8ms、sieve 1217.6ms、bounce 1121.3ms，其余 ≤ 684ms。与 O2 时代旧树（spectralnorm 2.14×、list 2.22×）相比，lazy-lvalue-fat / bench-optimize-ptr 后检查成本大幅下降；Option<T&> 迁移后进一步下降（原 1.00–1.46×、list 58.3%）。注: fasta 检查成本 684.1ms（③−②）按绝对 ms 呈现——2026-08-19 定向重测（genelist 恢复 T*，check 5053.5 / nocheck 4140.7 / raw 3341.8，当时检查成本占比过半）保留为历史；Fix B（T[] 整数索引内建路径 SliceAccess，commit 956d072）后 genelist 重回 T[] 化，check 3550.2 / nocheck 2866.1 / raw 2919.5（c8），较 T* 基线 check 5160.2ms 快 −27.9%（证据: bench-ptr-to-view task-4）；②<① 再现噪声倒置（−1.8%，nocheck IQR 200.8ms 带内），占比公式 (③−②)/(③−①) 失效（>100%），故不套用；原 T[] 化 1.68× 超限数据（trait 三层调用链未修复）已废弃。

### ② 胖表示（extractvalue / 40B 聚合）→ 对应 ②−①

- IR: **extractvalue 为最大单项差分**（nfc−raw 增量 99~164）；gep 增量 20~40。
- 机器码: check 态 mem 内存访问 386~472 vs raw 86~196，增量 190~323；list 相对增幅 4.7×、queen 绝对增幅 323。
- `is_shorter_than` prologue 20 余条 mov 搬运 40B×2。
- 同会话映射: ②−① 倍率 **0.88–2.05×**（queen/storage 2.05× 最高）；绝对成本 list **185915.5ms**（表示占比 75.3%）为绝对冠军，queen **7514.3ms**（占比 97.5%）为占比冠军，其次 towers 20370.6ms、binarytree 4258.1ms、storage 1690.8ms。**表示成本为多数大成本基准（list/queen/storage/towers）的主导开销**；小成本基准（bounce/revcomp/sieve/permute 等）检查成本反超表示（见 §2①）。

### ③ 锁槽帧锁（lock slot 内存读 / cache-miss）→ 含在 ②/①

- IR: lock_ptr / key 的 extractvalue 在 nfc 态即存在（属表示）；check 态额外增加对 `(%rdx)` 锁槽的**内存比较读**（`cmp %rcx,(%rdx)`）。
- 机器码: `index` 每次索引 2 次锁槽读；帧锁惰性化后栈上不重复初始化，但比较读不可避免。
- perf 硬件 cache 事件不可用（WSL2），无法直接量化；以 mem 指令计数增量（190~323）为其下界代理。
- 历史: 含在 ②−①，无独立分解；page-faults 差异（queen 753 vs 408）反映 queen 更大的数据足迹。

### ④ 元数据传递（函数签名 40B 按值传）→ 含在 ②

- IR: 40B 聚合按值传入/传出；机器码为 prologue 大量栈搬运（5 字段超过 4 个整数参数寄存器）。
- 证据: `is_shorter_than` 参数完全经栈（`0x30~0x48(%rsp)` 读入再 spill）；`index` 5 字段加载后重 spill。
- 历史: 含在 ②−①；method-self-ref（self 40B 降 24B）即此类的历史收益来源。

表 7: 四类开销归属汇总

| 开销类 | 对应差分 | IR 指纹 | 机器码形态 | 同会话成本（本轮） |
|---|---|---|---|---|
| ① 检查发射 | ③−② | icmp +52~89、trap +21~35、cfg 节点 18~27 | 比较 + 分支 → ud2，分支增量 77~131 | 0.97–1.64×；绝对仅 list 60866.6ms 显著（towers 7582.4ms、permute 1925.1ms 次之） |
| ② 胖表示 | ②−① | extractvalue +99~164（最大单项）、gep +20~40 | mem 访问增量 190~323（list 4.7×、queen +323） | 0.88–2.05×；list 185915.5ms 绝对冠军（queen 7514.3ms 占比冠军 97.5%） |
| ③ 锁槽帧锁 | 含在 ②/① | lock_ptr / key 提取在 nfc 态即存在 | `cmp %rcx,(%rdx)` 每次索引 2 次 | 无独立分解 |
| ④ 元数据传递 | 含在 ② | 40B 聚合按值传参 | prologue 20+ mov 栈搬运 | method-self-ref 先例 |

## 3. 三基准瓶颈定位

### 3.1 spectralnorm: 静态密度型（检查密度最高，c8 三态倍率噪声级）

三层证据: 静态检查密度三人组最高（cfg 27 节点、icmp 检查差分 +81、asm 分支 160）；热点 `index` / `mult_Av` / `mult_Atv` 全为矩阵乘内逐元素访问。c8 同会话测量显示检查占比 **30.7%**（③−② = 1.01×，55.5ms）、表示 1.02×（125.2ms）、总成本 1.02×（180.7ms）——三态倍率落入噪声级；较 2026-08-19 的 1.87×（7778ms）大幅收窄，主因 raw 绝对值跨会话偏高（7391ms vs 2026-08-19 ~4160ms，无 pin 下系统状态漂移，§0.2 教训；跨会话对比见 `.omo/evidence/bench-rerun/comparison.md`）。

瓶颈: 静态检查密度仍为三人组最高，是未来检查消除优化的静态潜力所在（lazy-lvalue-fat / bench-optimize-ptr 已使检查成本 collapse，`index` 调用仍约 90 条指令）；c8 下实际三态耗时差异已落入噪声级（总 1.02×），表示/检查成本不再构成实测瓶颈。

### 3.2 queen: 胖表示 + 元数据传递主导（表示型）

三层证据: c8 同会话表示成本 **2.05×**（7514.3ms，占总成本 97.5%，占比冠军）；asm 增幅最大（2.7×，+1123 行）、mem 内存访问绝对增幅最大（+323）；热点 `is_safe` / `index` / `place_queen` 为回溯热路径，40B 胖指针频繁构造/析构（update_board_state / place_queen）。检查成本仅 1.01×（188.9ms）。

瓶颈: 回溯状态下标与棋盘数组的胖表示存取；`is_safe` 的 33.43% 自占 + `index` 27.90%。

### 3.3 list: 总成本 + 绝对检查成本最高（动态频率型）

三层证据: c8 同会话总成本 **465723.6ms**（绝对冠军）、绝对检查成本 **60866.6ms**（占比 24.7%）；但**静态检查密度三人组最低**（cfg 18 节点、icmp 检查差分 +52、asm 分支 107）。瓶颈不在密度而在**执行频率**: `is_shorter_than` 87.67% 自占，链表 O(n²) 两两比较，每步都对胖指针取字段并做检查。Option<T&> 迁移（节点 Option 40B→24B）后表示占比 75.3%。

定位: 静态 IR 密度 × 海量动态执行次数 = 最大绝对成本；c8 检查/表示倍率 1.15× vs 1.85×，表示仍占主导，与 queen 的"表示主导"形态一致。

表 8: 三基准瓶颈对照

| 基准 | 瓶颈形态 | 关键证据 | 热点（Self%） |
|---|---|---|---|
| spectralnorm | 静态密度型（检查密度最高，c8 三态倍率噪声级） | 检查占比 30.7%、cfg 节点 27、表示 1.02×（raw 绝对值偏高致倍率收窄） | `index` 24.18% |
| queen | 表示型（表示 + 元数据传递） | 表示 7514.3ms（97.5%）、asm 2.7×、mem +323 | `is_safe` 33.43% |
| list | 动态频率型（表示主导） | 总成本 465723.6ms、绝对检查 60866.6ms（24.7%）、静态密度最低 | `is_shorter_than` 87.67% |

## 4. 优化方向

- **表示最重（queen / storage / list / towers 等大成本基准的主导开销）: 聚合引用化 / 零拷贝**。栈上胖指针零拷贝传递（引用化）、字段访问合并（提取合并/往返消除）、回溯数组与链表的扁平化存储布局。表示成本占这些基准总成本 72.9–97.5%，是当前第一优先。
- **检查最密（spectralnorm）: 检查分级 / 静态消除与循环内索引提升**。固定形状矩阵循环中可证明 `0 ≤ i < size` 的索引，检查提升到循环外或合并为每行一次；`index` 内联 + 字段标量替换，避免栈搬运。c8 下检查绝对成本已很小（55.5ms，三态倍率噪声级），收益空间在于密度×频率场景。
- **动态频率型（list / towers / fasta，检查占比 24.7–27.1%）: 热循环检查合并 + 字段复用**。`is_shorter_than` 内单次取 data / size 复用，避免逐字段 extractvalue；链表节点用邻接/连续表示减少索引路径；检查在已被证明非空的循环中下沉。（fasta 因 ②<① 噪声倒置不适用占比公式，检查成本 684.1ms 按绝对 ms 呈现，见 §2①。）
- **锁槽（③）: 惰性化已实现，可再探比较读合并**（一次锁槽读服务多次访问）。

优先级排序: 表示最重 → 聚合/引用化/零拷贝（全局第一优先，list/queen/storage/towers 表示占比 72.9–97.5%）；检查占比反超的小成本基准（bounce/revcomp/sieve/permute，③−② 倍率 1.15–1.64×）→ 检查消除/热循环检查合并与字段复用（list/towers/fasta）；检查最密 → 分级/静态消除与循环内索引提升；锁槽 → 比较读合并。本文档只做瓶颈定位，不展开优化实现。

## 5. 数据引用与 cross-check

数据来源:

- 基准运行数据（三态时间矩阵、倍率、RSS）: `docs/shootout-results.md`（三态紧邻重测版）
- 三层分析完整归因: `.omo/evidence/fat-perf-bottleneck/report.md`
- 命令与输出摘要: `.omo/evidence/fat-perf-bottleneck/task-1.txt`
- 三态紧邻改造与重测记录: `.omo/evidence/bench-per-bench-3state/task-1.txt`、`task-1a.txt`
- IR / asm / perf 原始产物: `/tmp/fpb/`（工作区外）

cross-check:

| 项 | O2 时代同会话（fat-session.log 2026-08-14, 旧树） | 本轮三态紧邻（当前树） | 一致性 |
|---|---|---|---|
| 三态关系 | 14/14 raw ≤ check（check/raw 1.47–4.34×） | 2026-08-18: 14/14 raw ≤ check（1.00–2.84×）；2026-08-21 c8: check/raw 0.96–2.32×，nbody 0.96×、mand 0.99× <1.00× 落在 IQR 噪声带内 | 一致（假象会话的 9 个 ≤1.00× 消失；c8 仅余 ≤4% 噪声级倒置，无系统性胖快于裸） |
| 检查成本排序 | list 绝对冠军（旧树 36211ms） | list 仍绝对冠军（60866.6ms，占比 24.7%） | 一致；占比稳定（21.2%→24.7%） |
| 表示成本 | queen 绝对冠军（旧树 28570ms） | list 185915.5ms 现绝对冠军；queen 7514.3ms（97.5%）占比冠军 | 迁移后 list 表示成本反超 queen，占比冠军保持 queen |
| 倍率（③/①） | 3.22 / 3.64 / 3.42×（中位数） | c8: 1.02 / 2.08 / 2.13×（spectralnorm/queen/list） | 下降主因: 当前树 lazy-lvalue-fat / bench-optimize-ptr 使检查成本 collapse（③−② 由 ~2.1× 降至 c8 0.97–1.64×）；c8 spectralnorm 1.02× 主因 raw 绝对值跨会话偏高（无 pin 协议，§0.2 教训），跨会话对比见 `.omo/evidence/bench-rerun/comparison.md` |
| perf 单次同会话 | 1.90 / 2.49 / 2.20×（2026-08-17） | 紧邻中位数 1.89 / 2.49 / 2.17×（迁移前）；迁移后 1.87 / 2.48 / 2.06× | 高度吻合（perf 层本就同会话，未受假象影响；perf 采样为迁移前树，bench-rerun 未重测 perf 层） |

无数值冲突；O3 报告（4230dc0）的 0.47× 级"胖快于裸"已被 §0 判定为跨会话测量假象，本版数据不再包含该偏差。2026-08-21 bench-rerun 重测（c8）已同步进 `docs/shootout-results.md`（三态矩阵、成本分解、RSS 全部按新数据更新；§1–3 的 IR / asm / perf 采样仍为迁移前树证据，方向性结论不变）。c8 与 c7 的复现性及 pre-bench-ptr-to-view 净效应对比见 `.omo/evidence/bench-rerun/comparison.md`。

## 6. CVE O3 通过性

测试套件恒用 `-O3` 编译（`scripts/run_tests.py` L327: `cmd += ["-O3"]`），与 shootout 基准评测的编译参数一致。O2→O3 对 YIAN IR 不产生代码差异（§0.2 证据 1: list_raw 二进制逐字节相同），安全机制（锁检查、越界检查、trap）的发射与 O2 时代完全一致，因此:

- **fat_cve 套件 76/76 通过**: 38 个 CVE 复刻用例（每个含 `<CVE-ID>.an` 漏洞复刻 + `<CVE-ID>-fixed.an` 修复版）在 -O3 下全部符合预期——复刻版运行期 trap（`Exit code -4`），修复版正常退出（成功用例）。
- fat 套件 70/70 通过（positive 30 + negative 20 期望 trap + compile_err 5），同样 -O3 编译。
- 主回归 run_tests 255/255 通过。
- 结论: 评测参数 O2→O3 不影响安全属性；性能报告的倍率关系（同基准内相对）不受优化级别差异影响。

## 结论

1. **测量层面**: O3 报告中的"胖快于裸"（9 个基准 ≤ 1.00×）为跨会话测量假象——raw 独立会话期间系统慢 ~2.1×（同一二进制 O2/O3 逐字节相同，时间却慢 2.11–2.28×，fann/nbody/mand 恰 2.177×）。三态紧邻重测（方案 A）后还原真实关系；2026-08-21 bench-rerun（c8）重测确认: **所有 <1.00× 倍率落在 IQR 噪声带内（无系统性胖快于裸）**——check/raw 0.96–2.32×，仅 nbody 0.96×、mand 0.99× 两个 <1.00× 且偏差 ≤4%（IQR 同量级），与假象会话 0.47×（50%+ 差距）级系统性倒置有本质区别。跨会话绝对毫秒不可直接比较（list raw 2026-08-18 21347ms → c8 218941ms），倍率（同基准相对）为稳定口径。
2. **开销层面**: **胖表示**（extractvalue / 40B 聚合）为多数大成本基准主导开销（②−① = 0.88–2.05×；list 185915.5ms / 75.3%、queen 7514.3ms / 97.5%、storage 2.05×、towers 1.76×）；**检查发射**（比较 + 分支 → trap）次之（③−② = 0.97–1.64×，list 60866.6ms / 24.7%、towers 7582.4ms / 27.1%、permute 1.64× / revcomp 1.37× 显著；fasta ②<① 噪声倒置，检查成本 684.1ms 按绝对 ms 呈现）；锁槽帧锁与元数据传递为含在表示内的次级项。小成本基准（bounce/revcomp/sieve/permute）检查成本反超表示。与 O2 时代旧树相比，检查成本经 lazy-lvalue-fat / bench-optimize-ptr 大幅下降。
3. 三类基准的瓶颈形态（c8）: **表示主导型**（queen / storage / towers / list: 表示成本占比 72.9–97.5%）与**检查主导型**（bounce / revcomp / sieve / permute: ③−② 倍率 1.15–1.64×）并存；fann / mand / nbody / spectralnorm 四基准三态倍率落入噪声级（spectralnorm 静态检查密度仍为三人组最高，c8 倍率收窄主因 raw 绝对值跨会话偏高）。
4. 优化优先级: 表示最重 → 聚合/引用化/零拷贝（全局第一优先）；动态频率型 → 热循环检查合并与字段复用（list/towers/fasta）；检查最密 → 分级/静态消除与循环内索引提升；锁槽 → 惰性化已实现，可再探比较读合并。
5. **bench-ptr-to-view 优化实测**（证据: `.omo/evidence/bench-ptr-to-view/task-{1..5}.txt` + c8 报告）: **Fix B**（T[] 整数索引内建路径 SliceAccess，commit 956d072）使 fasta genelist T[] 化后 check 较 T* 基线快 **−27.9%**（5160.2 → 3521.4~3749.7ms，task-4；c8 全量重测 check 3550.2ms 复现），消除 index→ptr→as_struct 三层调用链，T[] 反超 T*；**del-view**（commit ee41327）允许 `del T[]/T&` 整块释放，解除"局部保持 T* 以便 del"历史约束，6 基准数组局部 T*→T[] 直绑 + towers/list/storage 单对象 T*→T& 直连（commit 859de29/791fb7c）后语义等价（task-1/task-2）；storage Option<T[]> 候选实测 check +12.5%、RSS +876MB，超 1.05× 噪声门 → 保留现状 Option<T&>（task-3）。
6. **安全性**: 测试恒用 -O3，fat_cve 76/76、fat 70/70、回归 255/255 全绿；O2→O3 无代码差异，安全属性不受影响。

## 附录 A: perf 降级说明

perf 5.15.209（`/usr/lib/linux-tools-5.15.0-187/perf`）运行于 WSL2 内核 6.6.87.2-microsoft-standard-WSL2: **硬件事件（cycles / instructions / branches / branch-misses / cache-misses / LLC-load-misses）全部 `<not supported>`**（虚拟化环境计数器受限）。降级方案:

1. `perf stat` 用软件事件（task-clock / page-faults / context-switches / cpu-migrations），以 wall-time 倍率与内存足迹作代理；
2. `perf record -e cpu-clock -F 1000 -g` 做热点函数采样（成功，3 基准 check 版共 106,591 samples）；
3. IPC / 分支预测率 / cache-miss 无法直接测得，以第 2 层反汇编的分支与内存指令静态计数代理，并注明。

## 附录 B: 命令复现清单

第 1 层 IR 差分:

```
python3 -m compiler.main -t ll lib bench/shootout/<name>.an -o /tmp/fpb/<name>_check.ll
python3 -m compiler.main --no-fat-checks -t ll lib bench/shootout/<name>.an -o /tmp/fpb/<name>_nfc.ll
python3 -m compiler.main --raw-pointers -t ll lib bench/shootout_raw/<name>.an -o /tmp/fpb/<name>_raw.ll
```

统计: `grep -cE "icmp|extractvalue|getelementptr|define"` 分别计指令数，`grep -cE "llvm\.trap|call.*trap"` 计 trap，`wc -l` 计 IR 行数；cfg 检查节点: `grep -cE "check_safe_access|check_in_bounds|check_element_arith|check_raw_bounds" build/cfg.txt`（每次编译后立即保存 `build/cfg.txt` 副本到 `/tmp/fpb/*.cfg`）。

第 2 层 反汇编对比:

```
python3 -m compiler.main -O2 lib bench/shootout/<name>.an -o /tmp/fpb/<name>
python3 -m compiler.main --raw-pointers -O2 lib bench/shootout_raw/<name>.an -o /tmp/fpb/<name>_raw
objdump -d /tmp/fpb/<name>
objdump -d /tmp/fpb/<name>_raw
```

第 3 层 perf 采样:

```
PERF=/usr/lib/linux-tools-5.15.0-187/perf
taskset -c 4 $PERF stat -e task-clock,page-faults,context-switches,cpu-migrations ./<bin>
taskset -c 4 $PERF record -e cpu-clock -F 1000 -g -o <name>_check.perf ./<name>
$PERF report -i <name>_check.perf --stdio
```

跨会话假象复现（O2/O3 二进制逐字节相同验证）:

```
python3 -m compiler.main -O2 lib bench/shootout_raw/list.an -o /tmp/list_raw_O2
python3 -m compiler.main -O3 lib bench/shootout_raw/list.an -o /tmp/list_raw_O3
cmp /tmp/list_raw_O2 /tmp/list_raw_O3   # 逐字节相同
```

三态紧邻全量重测:

```
python3 scripts/bench_fat.py --suite shootout --pin 4
# 日志每基准一条: [done] <name>: 三态紧邻 (check/nocheck/raw) 各 5 次
```
