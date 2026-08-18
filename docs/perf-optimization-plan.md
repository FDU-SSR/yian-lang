# 胖指针性能优化方案

> 状态: 实施级蓝图第 1 稿(8 条目 5 字段已全部填充)。本文档将 `docs/performance-analysis.md` 的三层瓶颈分析结论转化为按优先级组织的优化方案,供后续实施计划直接引用。已包含: 前言与数据来源、编号映射表、已完成优化清单、四方向章节(8 条目 × 5 字段)、数据缺口声明;各条目预期收益以「可消除成本上界 / 现状分解值」口径表述,③④ 方向标注「估计 / 待测」。

## 1. 前言与数据来源

### 1.1 文档目的

本文档是胖指针内存安全开销的**实施级优化蓝图**: 基于 analysis 的瓶颈归因,按优先级列出四类优化方向,每项优化以统一 5 字段(原理 / 挂载点 / 预期收益 / 风险与安全影响 / 验证方法)描述,可作为后续实施计划的直接输入。本文档只做规划,不包含实现代码或改动 diff。

### 1.2 数据来源

本文档的数值与结论以以下三份材料为准:

- `docs/performance-analysis.md` — 三层瓶颈分析正式报告(三态 IR 差分、反汇编对比、perf 采样),含四类开销归因(§2)与三基准瓶颈定位(§3);
- `docs/shootout-results.md`(三态紧邻重测版)— 14 基准 × 3 态实测时间矩阵、倍率与 RSS;
- `.omo/evidence/fat-perf-bottleneck/report.md` — 三层分析完整归因报告(IR / asm / perf 原始数据与命令摘要见同目录 `task-1.txt`)。

### 1.3 三态口径

| 态 | 名称 | 表示 | 检查 |
|---|---|---|---|
| ①raw | 裸指针 | 8B 单指针 | 无(零安全基线) |
| ②nocheck | 胖无检查 | 40B 五字段 | 关闭(保留锁槽 / 帧锁) |
| ③check | 完整胖指针 | 40B 五字段 | 全部开启 |

三态两两差分把总开销拆成两部分: **③−② = 纯检查成本,②−① = 纯表示成本**。①raw 由 `bench/shootout_raw/` 套件配合 `--raw-pointers` 编译;②nocheck 用 `--no-fat-checks` 关闭检查发射但保留 40B 表示与锁槽 / 帧锁。成本分解以倍率为主(同基准内相对 raw),辅以毫秒绝对值。详见 performance-analysis.md「背景」节。

### 1.4 跨会话测量假象与修复摘要

2026-08-18 的 O3 全量重测报告曾出现 **9 个基准"胖快于裸"**(check 态中位数 ≤ raw 态中位数),与三态语义直接矛盾(check 为 raw 的指令超集),经判定为测量假象而非性能事实。根因: 旧测量流程先跑完全部基准的胖形态再跑全部 raw 形态,两段时间窗系统状态不同,raw 段恰好系统慢 **~2.1×**(同一二进制在假象会话 raw 段比 O2 时代慢 2.11–2.28×,而 check 态同会话相位正常)。修复采用方案 A: 每基准内三态紧邻(check → nocheck → raw)连续测量,同基准三态共享同一系统状态窗口。重测确认: **14/14 基准 raw ≤ check(check/raw = 1.00–2.84×)**,0.47× 级假象消失。完整论证见 performance-analysis.md §0。

### 1.5 与历史报告的关系

本方案中的收益口径为「可消除成本上界 / 现状分解值」,即各项优化对应的分析归因成本,而非实施后的承诺值;③④ 方向因无量化依据,预期收益标注「估计 / 待测」。跨会话假象时代的数值(9 基准"胖快于裸"、0.47× 级)仅作历史教训引用,不作为性能事实。

## 2. 编号映射表

本文档涉及三套互不相同的 ①~④ 编号(三态、成本分类、优化方向),为避免歧义在此先行固化。后续正文一律按「三态名 / 成本 N / 方向 N」表述。

### 2.1 三态编号(performance-analysis.md「背景」节)

| 编号 | 态 | 表示 | 检查 |
|---|---|---|---|
| ① | raw | 8B 裸指针 | 无 |
| ② | nocheck | 40B 五字段 | 关闭 |
| ③ | check | 40B 五字段 | 全部开启 |

### 2.2 成本分类编号(performance-analysis.md §2)

| 编号 | 成本类 | 对应差分 | 说明 |
|---|---|---|---|
| ① | 检查发射 | ③−② | icmp + trap 分支 / CheckSafeAccess 等检查节点 |
| ② | 胖表示 | ②−① | extractvalue / 40B 聚合 |
| ③ | 锁槽帧锁 | 含在 ②/① | lock slot 内存读 / cache-miss,无独立分解 |
| ④ | 元数据传递 | 含在 ② | 函数签名 40B 按值传 |

### 2.3 优化方向编号(本文档 §4)

| 编号 | 方向 | 对应成本 | 优先级 |
|---|---|---|---|
| ① | 表示优化(聚合 / 引用化 / 零拷贝) | 成本② + 成本④ | 第一优先 |
| ② | 动态频率型检查合并 | 成本①(动态场景) | 第二优先 |
| ③ | 检查分级 / 静态消除 | 成本① | 第三优先 |
| ④ | 锁槽比较读合并 | 成本③ | 第四优先 |

### 2.4 使用约定

编号 ①~④ 在三处语境含义不同,正文一律以下述写法区分:

- **三态**: 写作「raw 态」「②nocheck」「③check」;
- **成本分类**: 写作「成本①」「成本②」,如「方向① 对应成本②+④」;
- **优化方向**: 写作「方向①」,如「方向① = 表示优化」。

即「方向①」指表示优化,不指 raw 态,也不指检查发射成本。

## 3. 已完成优化(不重复实施)

以下 4 项优化已实现并合入当前树,§4 不再将其列为新条目;后续条目如触及同一挂载点,应在既有实现基础上增量推进,并保持其安全语义。

| 项 | 内容 | 代码注释口径 |
|---|---|---|
| method-self-ref | 方法体 self 由 40B 胖指针降为 24B 引用(ref 3 字段) | `compiler/codegen/cfg/builder.py` L796-798 注释: 折算后 receiver 仅 CheckRefAccess(live) |
| lazy-lvalue-fat | 未取址左值裸取址 + 裸数组越界检查 | IR.CheckRawBounds,`builder.py` L975 |
| bench-optimize-ptr | 5 基准 T*→T& / T[] 两套(shootout / shootout_raw)同步 | `.omo/evidence/bench-optimize-ptr/task-1.txt` |
| array-index-inline-gep | T[N] 整数索引内联 GEP + 越界 trap | `compiler/analysis/lowering/op_builder.py` L446-457 |

## 4. 优化方向(按优先级)

优先级依据 performance-analysis.md §4: 表示成本为当前多数基准的主导开销,故方向① 第一优先;动态频率型检查成本次之(list / towers / fasta,方向②);检查密度型静态潜力(方向③);锁槽比较读合并为残余优化空间(方向④)。

每条目统一为 5 字段结构(已填充,字段含义如下):

- **原理**: 该项优化的机制与所针对的成本;
- **挂载点**: 具体文件:行或 IR 节点名(行号以当前树实测为准);
- **预期收益**: 量化并标注来源,或以「估计 / 待测」标注;
- **风险与安全影响**: 对 fat 安全属性(fat 70 / fat_cve 80)的影响;
- **验证方法**: 引用既有验证设施。

### 4.1 方向①: 表示优化(聚合 / 引用化 / 零拷贝)【第一优先】

对应成本②(胖表示)+ 成本④(元数据传递)。本方向含 3 个条目。

#### 4.1.1 字段访问合并(一次 load 全 5 字段)

- **原理**: 40B 胖指针聚合(data / lock_ptr / key / index / size 5×8B)在热路径被反复字段提取,LLVM IR 中表现为大量 `extractvalue`(每字段一条)。三层分析第 1 层 IR 差分显示,表示差分的绝对主体即 extractvalue: nfc−raw 增量 99–164,为全部单项差分最大(analysis §1.1 / §2②)。字段链被拆散为多次独立取值,阻碍 LLVM 将 5 字段聚合视为整体优化。
- **挂载点**: `compiler/codegen/llvm/builder.py` — `__extract_fat_field`(定义 L91)全部调用点(当前树实测 43 处),代表性集合: L127 / L155,163 / L239-253 / L404 / L482-514 / L543-549 / L635-671 / L714-726 / L835-937 / L1135-1136(覆盖 data / lock_ptr / key / index / size 全部字段提取;其中 L239-253、L482-514、L543-549、L635-671 为 2–5 字段成组提取,即「一次 load 全 5 字段」的合并目标)。LLVM 类型侧: `compiler/codegen/llvm/types.py` L39-41(`__fat_pointer` 5 字段 / `__ref_pointer` 3 字段 / `__str_ll_type`)。
- **预期收益**: 现状表示成本为可消除上界: queen 23493ms(占总成本 96.3%,绝对冠军)、②−① 倍率 1.00–2.44×(多数基准 >1.3×)(analysis §2②)。本项针对其中字段提取/聚合搬运部分,不承诺全量消除;实际收益以实施后三态紧邻重测为准。
- **风险与安全影响**: LLVM 值语义约束: extractvalue 链合并须保持字段顺序与类型(data / lock_ptr / key / index / size),任一字段错位即破坏 live / in_bounds 检查所用元数据;合并只改聚合形态,不改检查发射逻辑。必须保持 fat 70 / fat_cve 80 不回归(检查节点数与 trap 行为不变)。
- **验证方法**: 第 1 层 IR 差分复现(analysis 附录 B: `-t ll` 三态编译 + grep 计数 extractvalue / icmp / trap)+ 三态紧邻重测(`python3 scripts/bench_fat.py --suite shootout --pin 4`);回归 `python3 scripts/run_tests.py`、`python3 scripts/run_fat_tests.py`、`python3 scripts/run_fat_cve.py`。

#### 4.1.2 栈上胖指针引用化 / 零拷贝传递

- **原理**: 40B 聚合按值传参(函数签名 5 字段超出 4 个整数参数寄存器),prologue 大量栈搬运。list `is_shorter_than`(87.67% 自占热点)prologue 20+ mov 把两个 40B 胖指针参数读入再全部 spill(analysis §1.2 / §2④)。method-self-ref 先例已把 self 由 40B 降为 24B(3 字段 ref)并获收益,本项为其向一般 T* 参数与局部传递的推广。
- **挂载点**: 函数签名生成 `compiler/codegen/llvm/types.py` `__build_function_type`(L194-209,普通参数当前按 40B 聚合下推、receiver 已按 24B T& 下推);调用侧折算 `compiler/codegen/cfg/builder.py` `__build_cast`(L1230)的 T*→T& 分支(40B→24B 收缩)。
- **预期收益**: 消除元数据传递成本(成本④,含在表示内 ②−①,无独立分解);标注「待测」: 无独立量化,参考 method-self-ref 先例(40B→24B 已获收益),实际收益以实施后三态紧邻重测为准。
- **风险与安全影响**: receiver 折算已有先例风险: 折算删除 index / size 字段后 in_bounds 语义丢失(cfg/builder.py L796-804 注释,t4→F2 修复在折算前发射 `CheckInBounds(p,1)` 恢复 one-past-end 语义)。推广到一般参数时须保持等价检查发射(live 与 in_bounds 检查不得因引用化而缺失),fat 70 / fat_cve 80 不回归。
- **验证方法**: 第 2 层反汇编对比(analysis 附录 B: objdump -d)统计 prologue mov 计数(优化前后对比)+ 三态紧邻重测 `python3 scripts/bench_fat.py --suite shootout --pin 4`;回归 run_tests / fat / fat_cve。

#### 4.1.3 扁平化存储布局(回溯数组 / 链表)

- **原理**: queen 回溯热路径(is_safe 33.43% + index 27.90% 自占)频繁构造/析构 40B 胖指针(update_board_state / place_queen),棋盘数组与回溯栈的胖指针元素反复聚合/提取。扁平化布局(数组只存裸 data、元数据单份保存)可消除每元素 40B 的构造/析构与字段提取开销。
- **挂载点**: 基准源码级而非编译器: `bench/shootout/queen.an`(回溯数组)、`bench/shootout/list.an`(链表节点);对应 raw 套件 `bench/shootout_raw/` 需同步修改。
- **预期收益**: 估计 — queen 表示成本 23493ms(占总成本 96.3%,analysis §2②)的部分消除;本项为源码级变体,收益以实施后三态紧邻重测为准。
- **风险与安全影响**: 基准语义保持(须与 raw 套件同步,del 语义不破坏);扁平化布局不可绕过安全检查——仅减少聚合构造开销,live / in_bounds 检查语义仍须保持;不影响编译产物安全属性。
- **验证方法**: 三态紧邻重测 `python3 scripts/bench_fat.py --suite shootout --pin 4`(对比 queen / list 的 ③−① 倍率变化)+ 全量回归 `python3 scripts/run_tests.py`、`python3 scripts/run_fat_tests.py`、`python3 scripts/run_fat_cve.py` 确认基准等价。

### 4.2 方向②: 动态频率型检查合并【第二优先】

对应成本①(检查发射)的动态场景: 静态检查密度不高、但海量动态执行使检查成本绝对显著(list / towers / fasta)。本方向含 2 个条目。

#### 4.2.1 热循环检查合并 + 字段复用

- **原理**: list `is_shorter_than` 87.67% 自占,链表 O(n²) 两两比较,每步对胖指针取字段(data / size)并做检查(锁 + 越界);静态检查密度三人组最低(cfg 18 节点)但海量动态执行使绝对检查成本最高(14551ms)。热循环内相邻访问共享同一胖指针时,锁检查与字段提取可合并、复用。
- **挂载点**: CFG 检查发射的 Load / Store 对 `compiler/codegen/cfg/builder.py` L1095-1133(检查节点发射点: CheckRefAccess L1095 / L1115 / L1130、CheckInBounds L1098、CheckSafeAccess L1118 / L1133)+ 检查节点族 `compiler/codegen/cfg/ir.py` L216 起(CheckSafeAccess / CheckInBounds / CheckElementArith / CheckRefAccess / CheckRawBounds)。
- **预期收益**: list 检查成本 14551ms(占总成本 58.3%,全部基准最高,analysis §2①)为可消除上界;本项针对动态频率型检查的合并/复用,不承诺全量消除,实际收益以实施后重测为准。
- **风险与安全影响**: 合并后须保持「每访问」语义等价: 相邻访问共享同一检查的前提是可证明同一指针处于同一有效窗口;任何错误合并即放宽 live / in_bounds 属性。fat 70 / fat_cve 80 不回归。
- **验证方法**: `python3 scripts/run_tests.py`(回归)+ `python3 scripts/run_fat_tests.py`(fat 70)+ `python3 scripts/run_fat_cve.py`(fat_cve 80)+ 三态紧邻重测 `python3 scripts/bench_fat.py --suite shootout --pin 4`(对比 list ③−② 倍率,现状 1.46×)。

#### 4.2.2 towers / fasta 挂载热点

- **原理**: towers 检查成本 4028ms(占总成本 44.5%)、fasta 895ms(48.4%),检查占比显著(≥44%),属动态频率型;两基准无三层归因(仅三态时间),瓶颈形态为外推: 每步取字段 + 检查。
- **挂载点**: 基准源码级热点: `bench/shootout/towers.an`、`bench/shootout/fasta.an`(每步取字段处;对应 raw 套件 `bench/shootout_raw/` 同步)。
- **预期收益**: 估计 — 无 perf 归因,仅三态时间;现状检查成本(towers 4028ms / fasta 895ms,analysis §2①)为可消除上界。
- **风险与安全影响**: 同 4.2.1: 检查合并 / 字段复用须保持每访问语义等价,不得放宽 live / in_bounds 检查;fat 70 / fat_cve 80 不回归。
- **验证方法**: 三态紧邻重测 `python3 scripts/bench_fat.py --suite shootout --pin 4`,对比 towers / fasta 的 ③−② 倍率(现状检查占比 44.5% / 48.4%)+ 回归 run_fat_tests / run_fat_cve。

### 4.3 方向③: 检查分级 / 静态消除【第三优先】

对应成本①(检查发射)的静态场景: 固定形状循环中可静态证明的索引检查提升或合并。本方向含 2 个条目。

#### 4.3.1 固定形状矩阵循环索引提升

- **原理**: spectralnorm 静态检查密度最高(cfg 27 节点、icmp 检查差分 +81)但检查绝对成本小(652ms / 8.7%);固定形状矩阵循环中 `0 ≤ i < size` 可静态证明,索引检查可提升到循环外或合并为每行 / 每矩阵一次。
- **挂载点**: 检查发射插入点 `compiler/codegen/cfg/builder.py` L1095-1133(Load / Store 检查发射)+ 检查节点族(`compiler/codegen/cfg/ir.py` CheckInBounds / CheckElementArith);提升逻辑落于检查发射的条件化,或由 LLVM 端 loop-invariant 化完成。
- **预期收益**: 估计 — 分析无预测;现状检查成本 652ms(占 spectralnorm 总成本 8.7%,analysis §2①)为可消除上界,收益空间受限于检查绝对成本已小。
- **风险与安全影响**: 提升越界可能放宽安全属性: 索引提升的前提是边界可静态证明(固定形状、无别名写),任何证明缺口即取消提升;必须保持 trap 语义(fat_cve 80 全绿为安全闸门)。
- **验证方法**: `python3 scripts/run_fat_cve.py`(fat_cve 80 全绿)+ 三态紧邻重测 `python3 scripts/bench_fat.py --suite shootout --pin 4`(spectralnorm ③−② 现状 1.04×)。

#### 4.3.2 index 内联 + 字段标量替换

- **原理**: 编译器生成的胖指针辅助函数 `index`(索引访问)check 态约 90 条指令(analysis §1.2 反汇编标本: 栈上 40B 加载 mov×10 + 锁检查×2 + 越界检查×1 + 数据访问),每次热循环索引都经调用;内联 + 字段标量替换可消除调用与栈搬运开销。
- **挂载点**: LLVM 内联(辅助函数内联到调用点)+ 检查节点合并;检查序列为锁检查 2 次 + 越界检查 1 次,每检查 ≈3 icmp(代表基准反推: spectralnorm 81/27=3.0、queen 89/26=3.4、list 52/18=2.9,非通用常量)。
- **预期收益**: 估计 — 无独立量化;现状为 index 调用路径的检查 + 聚合搬运开销(含在 spectralnorm / queen 的表示与检查成本中),内联后收益以重测为准。
- **风险与安全影响**: 同 4.3.1: 字段标量替换不得跳过检查(live / in_bounds 每访问语义保持);trap 语义不变。
- **验证方法**: 第 2 层反汇编对比(analysis 附录 B: objdump -d)统计 index 指令数(现状约 90 条 vs 内联后)+ 回归 `python3 scripts/run_tests.py`、`python3 scripts/run_fat_tests.py`、`python3 scripts/run_fat_cve.py` + 三态紧邻重测。

### 4.4 方向④: 锁槽比较读合并【第四优先】

对应成本③(锁槽帧锁): 每次指针访问的锁槽内存比较读。本方向含 1 个条目。

#### 4.4.1 一次锁槽读服务多次访问

- **原理**: 锁检查 = 对锁槽的内存比较读 `cmp %rcx,(%rdx)`(analysis §1.2 检查序列);`index` 每次索引 2 次锁槽读(两字段锁检查各一次)。同一指针在短窗口内多次访问时锁槽值不变,比较读可合并为一次。
- **挂载点**: 检查发射的 live 检查部分(CheckSafeAccess / CheckRefAccess 对应锁比较读的发射点,cfg/builder.py L1095 / L1115 / L1118 / L1130 / L1133 + `compiler/codegen/cfg/ir.py` 检查节点族)。
- **预期收益**: 待测 — 锁槽成本含在 ②−①,无独立分解(analysis §2③);本项收益含在方向②/方向①的合并收益内,不单独承诺,以实施后三态紧邻重测为准。
- **风险与安全影响**: 锁槽读合并须保持 live 语义(每次访问前有效): 合并窗口内不得跨越任何可能修改锁槽的操作(重新分配 / 移动 / 析构);fat_cve 80 不回归。
- **验证方法**: 第 1 层 IR 差分复现(analysis 附录 B)+ 三态紧邻重测 `python3 scripts/bench_fat.py --suite shootout --pin 4` + `python3 scripts/run_fat_cve.py`(fat_cve 80)。

## 5. 数据缺口与局限

- **三层归因覆盖有限**: 仅 spectralnorm / queen / list 三个代表基准有 IR 差分、反汇编、perf 采样三层归因;其余 11 个基准仅有三态紧邻实测时间(倍率),无逐层 IR / asm / perf 归因,其瓶颈形态与收益空间为外推结论,涉及这些基准的条目预期收益需标注「估计 / 待测」。
- **锁槽成本无独立分解**: 锁槽帧锁(成本③)与元数据传递(成本④)均含在 ②−① 中,无法单独量化;perf 硬件事件在 WSL2 不可用,硬件 cache 缺失数据以 mem 指令计数增量作为下界代理。方向④ 与方向①中涉及成本④的条目,预期收益只能以「估计 / 待测」标注,待实施后以三态紧邻重测实证。
- **本文档不做现测补数**: 数据缺口不通过运行基准补齐(全量三态重测耗时长),后续条目填充时严格引用既有数据来源,缺什么标什么。
