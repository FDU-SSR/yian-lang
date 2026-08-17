# 胖指针性能瓶颈分析

本文档汇总 fat-perf-bottleneck 计划的三层瓶颈分析（三态 IR 差分、反汇编对比、perf 采样），将 `.omo/evidence/fat-perf-bottleneck/report.md` 的分析结论整理为正式的性能报告。文中全部数值以 report.md 与 task-1.txt 为准，基准运行数据引用 `docs/shootout-results.md`。分析日期 2026-08-17，纯只读进行，未改动任何源码与基准。

## 背景: 胖指针表示与三态

YIAN 的胖指针为 5 字段 40B 值，字段依次为 data（有效数据地址）、lock_ptr（锁槽指针）、key（键值）、index（元素下标）、size（元素个数）。内存安全机制围绕该表示展开: 每次指针访问前检查 `live`（锁槽键比较）与 `in_bounds`（下标越界），失败经 `llvm.trap` 终止程序。为量化该机制的开销，基准评测采用三态编译:

| 态 | 名称 | 表示 | 检查 |
|---|---|---|---|
| ①raw | 裸指针 | 8B 单指针 | 无（零安全基线） |
| ②nocheck | 胖无检查 | 40B 五字段 | 关闭（保留锁槽/帧锁） |
| ③check | 完整胖指针 | 40B 五字段 | 全部开启 |

三态两两差分把总开销拆成两部分: ③−② 为纯检查成本，②−① 为纯表示成本。①raw 由 `bench/shootout_raw/` 套件配合 `--raw-pointers` 编译（裸模式下隐式 coerce 被拒，需显式 `from_raw_parts`）；②nocheck 用 `--no-fat-checks` 关闭检查发射但保留 40B 表示与锁槽/帧锁，故 ②−① 反映纯表示成本。数值口径: 历史成本分解以毫秒绝对值为单位（fat-perf-remaster/task-2.txt §2），倍率口径见 `docs/shootout-results.md` §2-3。

报告目的: 定位胖指针内存安全开销的落点（检查发射、胖表示、锁槽帧锁、元数据传递），按基准给出瓶颈形态与优化方向，为后续优化提供数据基线与可复现命令。

## 1. 三层分析方法

分析针对三个代表性基准: spectralnorm（检查占比冠军 77.2%）、queen（表示成本绝对冠军 28570ms）、list（总成本绝对冠军 46720.8ms，兼绝对检查成本冠军）。三人组覆盖检查最密、表示最重、综合最贵三种形态，选取依据为历史成本分解（表 1）。

表 1: TOP 基准选取（历史成本分解, ms）

| 基准 | 表示(②−①) | 检查(③−②) | 总(③−①) | 检查占% | 表示占% | 选取理由 |
|---|---|---|---|---|---|---|
| **spectralnorm** | +3401.0 | +11495.2 | +14896.2 | **77.2%** | 22.8% | 检查成本占比冠军（建议组 fann 57.8% 与 spectralnorm 77.2% 中最高；mand 100.2% 系负表示成本 −20.2ms 的假象，剔除） |
| **queen** | **+28570.3** | +10125.0 | +38695.3 | 26.2% | **73.8%** | 表示成本绝对冠军（28570ms，占其总成本 73.8%；storage 占比 74.7% 略高但绝对值仅 1969ms） |
| **list** | +10510.1 | **+36210.7** | **+46720.8** | **77.5%** | 22.5% | 总成本绝对冠军 + 绝对检查成本绝对冠军（36211ms），兼最高检查占比 |

### 1.1 第 1 层: 三态 IR 差分

方法: 对同一基准分别以三态编译出 LLVM IR（`-t ll`），统计 icmp / extractvalue / getelementptr / trap / define / IR 行数，以及 CFG 检查节点数（`build/cfg.txt` 中的 `check_safe_access` / `check_in_bounds` / `check_element_arith`）。检查差分 = ③−②，表示差分 = ②−①。

生成命令（每态编译后立即保存 `build/cfg.txt` 副本）:

```
python3 -m compiler.main -t ll lib bench/shootout/<name>.an -o /tmp/fpb/<name>_check.ll
python3 -m compiler.main --no-fat-checks -t ll lib bench/shootout/<name>.an -o /tmp/fpb/<name>_nfc.ll
python3 -m compiler.main --raw-pointers -t ll lib bench/shootout_raw/<name>.an -o /tmp/fpb/<name>_raw.ll
```

统计命令: `grep -cE "icmp|extractvalue|getelementptr|define"` 分别计指令，`grep -cE "llvm\.trap|call.*trap"` 计 trap，`wc -l` 计行数；cfg 检查节点用 `grep -cE "check_safe_access|check_in_bounds|check_element_arith|check_raw_bounds" build/cfg.txt`。

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

方法: 以 `-O2` 重编译 check 与 raw 二进制到 `/tmp/fpb/`，`objdump -d` 反汇编，统计 asm 行数、分支指令、call、mov 内存访问、ud2（trap 点），并逐函数识别检查序列。

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

方法: 软件事件降级（详见附录 A）。`perf stat` 用软件事件（task-clock / page-faults / context-switches / cpu-migrations）测墙钟时间与内存足迹代理；`perf record` 用 cpu-clock 对 check 版采样热点函数。

命令:

```
PERF=/usr/lib/linux-tools-5.15.0-187/perf
taskset -c 4 $PERF stat -e task-clock,page-faults,context-switches,cpu-migrations ./<bin>
taskset -c 4 $PERF record -e cpu-clock -F 1000 -g -o <name>_check.perf ./<name>
$PERF report -i <name>_check.perf --stdio
```

表 5: perf stat（软件事件，单次采样，taskset -c 4）

| 基准 | 态 | elapsed | task-clock | page-faults | check/raw 倍率 |
|---|---|---|---|---|---|
| spectralnorm | ③check | 16.75 s | 17.97 s | 96 | **1.90×** |
| spectralnorm | ①raw | 8.83 s | 9.43 s | 96 | |
| queen | ③check | 42.29 s | 45.29 s | 753 | **2.49×** |
| queen | ①raw | 17.01 s | 18.23 s | 408 | |
| list | ③check | 47.76 s | 51.16 s | 53 | **2.20×** |
| list | ①raw | 21.69 s | 23.22 s | 48 | |

倍率低于 `fat-perf-remaster/task-2.txt §3` 历史中位数（3.22/3.64/3.42×）。原因: 本轮为单次采样而非 5 次中位数协议，且当前树已含 lazy-lvalue-fat / bench-optimize-ptr 改动，IR 新生成，绝对时间不对齐属预期（§5 cross-check）。

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
- 历史映射: ③−② = spectralnorm 11495ms / queen 10125ms / list 36211ms。

### ② 胖表示（extractvalue / 40B 聚合）→ 对应 ②−①

- IR: **extractvalue 为最大单项差分**（nfc−raw 增量 99~164）；gep 增量 20~40。
- 机器码: check 态 mem 内存访问 386~472 vs raw 86~196，增量 190~323；list 相对增幅 4.7×、queen 绝对增幅 323。
- `is_shorter_than` prologue 20 余条 mov 搬运 40B×2。
- 历史映射: ②−① = queen **28570ms**（绝对冠军）、list 10510ms、spectralnorm 3401ms。

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

| 开销类 | 对应差分 | IR 指纹 | 机器码形态 | 历史成本 |
|---|---|---|---|---|
| ① 检查发射 | ③−② | icmp +52~89、trap +21~35、cfg 节点 18~27 | 比较 + 分支 → ud2，分支增量 77~131 | 11495 / 10125 / 36211 ms |
| ② 胖表示 | ②−① | extractvalue +99~164（最大单项）、gep +20~40 | mem 访问增量 190~323（list 4.7×、queen +323） | 3401 / 28570 / 10510 ms |
| ③ 锁槽帧锁 | 含在 ②/① | lock_ptr / key 提取在 nfc 态即存在 | `cmp %rcx,(%rdx)` 每次索引 2 次 | 无独立分解 |
| ④ 元数据传递 | 含在 ② | 40B 聚合按值传参 | prologue 20+ mov 栈搬运 | method-self-ref 先例 |

## 3. 三基准瓶颈定位

### 3.1 spectralnorm: 检查发射主导（静态密度型）

三层证据: 历史检查占比 77.2%（11495ms）；本轮 cfg 检查节点 27（三人组最高）、icmp 检查差分 +81、asm 分支 160（最高）；热点 `index` / `mult_Av` / `mult_Atv` 全为矩阵乘内逐元素访问。

瓶颈: 每次矩阵元素访问（热循环内）都走完整锁 + 越界检查；`index` 调用本身约 90 条指令。

### 3.2 queen: 胖表示 + 元数据传递主导（表示型）

三层证据: 历史表示成本 28570ms（73.8%，绝对冠军）；本轮 asm 增幅最大（2.7×，+1123 行）、mem 内存访问绝对增幅最大（+323）；热点 `is_safe` / `index` / `place_queen` 为回溯热路径，40B 胖指针频繁构造/析构（update_board_state / place_queen）。

瓶颈: 回溯状态下标与棋盘数组的胖表示存取；`is_safe` 的 33.43% 自占 + `index` 27.90%。

### 3.3 list: 总成本 + 绝对检查成本最高（动态频率型）

三层证据: 历史总成本 46720.8ms、绝对检查 36211ms（均为全部基准最高）；但**静态检查密度三人组最低**（cfg 18 节点、icmp 检查差分 +52、asm 分支 107）。瓶颈不在密度而在**执行频率**: `is_shorter_than` 87.67% 自占，链表 O(n²) 两两比较，每步都对胖指针取字段并做检查。

定位: 静态 IR 密度 × 海量动态执行次数 = 最大绝对成本，与 spectralnorm / queen 的"密度主导"形成对照。

表 8: 三基准瓶颈对照

| 基准 | 瓶颈形态 | 关键证据 | 热点（Self%） |
|---|---|---|---|
| spectralnorm | 静态密度型（检查） | 检查占比 77.2%、cfg 节点 27、asm 分支 160 | `index` 24.18% |
| queen | 表示型（表示 + 元数据传递） | 表示 28570ms（73.8%）、asm 2.7×、mem +323 | `is_safe` 33.43% |
| list | 动态频率型（检查） | 总成本 46720.8ms、绝对检查 36211ms、静态密度最低 | `is_shorter_than` 87.67% |

## 4. 优化方向

- **检查最密（spectralnorm）: 检查分级 / 静态消除与循环内索引提升**。固定形状矩阵循环中可证明 `0 ≤ i < size` 的索引，检查提升到循环外或合并为每行一次；`index` 内联 + 字段标量替换，避免栈搬运。
- **表示最重（queen）: 聚合引用化 / 零拷贝**。栈上胖指针零拷贝传递（引用化）、字段访问合并（一次 load 全部 5 字段）、回溯数组的扁平化存储布局。
- **动态频率型（list）: 热循环检查合并 + 字段复用**。`is_shorter_than` 内单次取 data / size 复用，避免逐字段 extractvalue；链表节点用邻接/连续表示减少索引路径；检查在已被证明非空的循环中下沉。
- **锁槽（③）: 惰性化已实现，可再探比较读合并**（一次锁槽读服务多次访问）。

优先级排序: 检查最密 → 分级/静态消除与循环内索引提升；表示最重 → 聚合/引用化/零拷贝；锁槽 → 比较读合并。本文档只做瓶颈定位，不展开优化实现。

## 5. 数据引用与 cross-check

数据来源:

- 基准运行数据（三态时间矩阵、倍率、RSS）: `docs/shootout-results.md`
- 三层分析完整归因: `.omo/evidence/fat-perf-bottleneck/report.md`
- 命令与输出摘要: `.omo/evidence/fat-perf-bottleneck/task-1.txt`
- IR / asm / perf 原始产物: `/tmp/fpb/`（工作区外）

与 fat-perf-remaster/task-2.txt 历史成本分解 cross-check:

| 项 | 历史（task-2.txt §2） | 本轮三层数据 | 一致性 |
|---|---|---|---|
| 检查成本排序 | list 36211 > spectralnorm 11495 > queen 10125 | 静态密度 spectralnorm≈queen > list（cfg 27/26/18）；list 靠 is_shorter_than 87.67% 动态频率 | **不冲突**: 静态密度与动态频率双维度，list 属后者主导 |
| 表示成本 | queen 28570 绝对冠军 | queen asm 增幅 2.7× 最大、mem +323 绝对最大 | 一致 |
| 检查占比冠军 | spectralnorm 77.2%（比例） | spectralnorm cfg 节点 27 最高、asm 分支 160 最高 | 一致 |
| 总成本冠军 | list 46720 | list 绝对检查成本 + 热点集中度（87.67% 单函数）最高 | 一致 |
| 倍率（③/①） | 3.22/3.64/3.42×（中位数） | 本轮单次 1.90/2.49/2.20× | **不冲突**: 本轮为单次非中位数；当前树已含 lazy-lvalue-fat / bench-optimize-ptr 改动，IR 新生成，绝对时间不对齐属预期 |

无数值冲突；绝对时间差异由「单次采样 vs 5 次中位数协议」与「当前树改动」双重解释。

## 结论

1. **检查发射**（比较 + 分支 → trap）与**胖表示**（extractvalue / 40B 聚合）为两大主导开销，方向与历史 ③−② / ②−① 分解一致；锁槽帧锁与元数据传递为含在表示内的次级项。
2. 三类基准呈现两种瓶颈形态: **静态密度型**（spectralnorm / queen: 检查点或表示字段密度高，热点在胖指针辅助函数 index / ptr / as_struct）与**动态频率型**（list: 密度低但 is_shorter_than 87.67% 海量执行）。
3. 优化优先级: 检查最密 → 分级/静态消除与循环内索引提升；表示最重 → 聚合/引用化/零拷贝；锁槽 → 惰性化已实现，可再探比较读合并。

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
