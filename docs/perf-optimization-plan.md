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

2026-08-18 的 O3 全量重测报告曾出现 **9 个基准"胖快于裸"**(check 态中位数 ≤ raw 态中位数),与三态语义直接矛盾(check 为 raw 的指令超集),经判定为测量假象而非性能事实。根因: 旧测量流程先跑完全部基准的胖形态再跑全部 raw 形态,两段时间窗系统状态不同,raw 段恰好系统慢 **~2.1×**(同一二进制在假象会话 raw 段比 O2 时代慢 2.11–2.28×,而 check 态同会话相位正常)。修复采用方案 A: 每基准内三态紧邻(check → nocheck → raw)连续测量,同基准三态共享同一系统状态窗口。重测确认: **14/14 基准 raw ≤ check(check/raw = 1.00–2.84×)**,0.47× 级假象消失。完整论证见 performance-analysis.md §0。2026-08-19 Option<T&> 迁移后重测: check/raw = 1.01–2.48×(list 2.06×、towers 2.08×、binarytree 1.57×、storage 2.11× 为迁移重测值;fasta 原 1.68× 超限,genelist 回退 T*(AminoAcid* )表示后定向重测 1.51× 达标),见 `docs/shootout-results.md`。

2026-08-21 bench-rerun 全量三态重测(c8;每基准三态紧邻、无 --pin,同树同协议)与此前同日 01:30 的 c7(bench-ptr-to-view 收尾,无 --pin)构成两次独立复现测量,时间标签区分如下: c8 全 14 基准 check/raw = 0.96–2.32×(端点: nbody 0.96× / storage 2.32×;12/14 ≥ 1.00×,<1.00× 仅 mand 0.99×、nbody 0.96×,均落在 IQR 噪声带内,无系统性胖快于裸);fasta 三态中位数(c7 → c8): check 3628.1 → 3550.2ms、nocheck 2831.7 → 2866.1ms、raw 2904.8 → 2919.5ms,check/raw 1.25× → 1.22×,两轮 ②nocheck 均略小于 ①raw(c7 差 2.5% / c8 差 1.8%,IQR 内噪声倒置),Fix B 后 genelist T[] 化的 -27.9%(快于 T*)接受态保持。原始数据见 `build/bench/shootout-results.md`(c8)与 `.omo/evidence/bench-rerun/c7-shootout-results.md`(c7 快照)。

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

以下 8 项优化已实现并合入当前树,§4 不再将其列为新条目;后续条目如触及同一挂载点,应在既有实现基础上增量推进,并保持其安全语义。

| 项 | 内容 | 代码注释口径 |
|---|---|---|
| method-self-ref | 方法体 self 由 40B 胖指针降为 24B 引用(ref 3 字段) | `compiler/codegen/cfg/builder.py` L796-798 注释: 折算后 receiver 仅 CheckRefAccess(live) |
| lazy-lvalue-fat | 未取址左值裸取址 + 裸数组越界检查 | IR.CheckRawBounds,`builder.py` L975 |
| bench-optimize-ptr | 5 基准 T*→T& / T[] 两套(shootout / shootout_raw)同步 | `.omo/evidence/bench-optimize-ptr/task-1.txt` |
| array-index-inline-gep | T[N] 整数索引内联 GEP + 越界 trap | `compiler/analysis/lowering/op_builder.py` L446-457 |
| del-view | 允许 del T[]/T& 整块释放 | commit ee41327 |
| bench-ptr-to-view | T[]/T& 直绑消除不必要 T*(两套) | commit 859de29/791fb7c |
| slice-index-builtin | T[] 整数索引内建路径 SliceAccess(Fix B) | commit 956d072 |
| field-access-merge | 字段提取合并/往返消除 | commit 243cc49/d61e98f |

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

#### 4.1.1 字段访问合并(提取合并/往返消除)

> **已实现 (commit 243cc49/d61e98f)**: 本条目保留为历史蓝图,实施细节见 §3。

- **原理**: 40B 胖指针聚合(data / lock_ptr / key / index / size 5×8B)在热路径被反复字段提取,LLVM IR 中表现为大量 `extractvalue`(每字段一条)。三层分析第 1 层 IR 差分显示,表示差分的绝对主体即 extractvalue: nfc−raw 增量 99–164,为全部单项差分最大(analysis §1.1 / §2②)。字段链被拆散为多次独立取值,阻碍 LLVM 将 5 字段聚合视为整体优化。
- **挂载点**: `compiler/codegen/llvm/builder.py` — `__extract_fat_field`(定义 L91)全部调用点(当前树实测 43 处),代表性集合: L127 / L155,163 / L239-253 / L404 / L482-514 / L543-549 / L635-671 / L714-726 / L835-937 / L1135-1136(覆盖 data / lock_ptr / key / index / size 全部字段提取;其中 L239-253、L482-514、L543-549、L635-671 为 2–5 字段成组提取,即「提取合并/往返消除」的合并目标——探索结论: LLVM `extractvalue` 多索引仅作用于嵌套路径,扁平 5 字段结构不可一次多取,故「一次 load 全 5 字段」不可实现;落地形态为成组提取合并(bundle,如 element_ptr 单 insert_value / check_safe_access bundle)+ 重复提取消除(往返消除,如 check_delete 的 live 检查去重))。LLVM 类型侧: `compiler/codegen/llvm/types.py` L39-41(`__fat_pointer` 5 字段 / `__ref_pointer` 3 字段 / `__str_ll_type`)。
- **预期收益**: 现状表示成本为可消除上界: list 191559ms(占总成本 78.8%,绝对冠军)、queen 23588ms(占总成本 93.3%,占比冠军)、②−① 倍率 0.99–1.99×(多数基准 >1.3×)(analysis §2②)。本项针对其中字段提取/聚合搬运部分,不承诺全量消除;实际收益以实施后三态紧邻重测为准。
- **风险与安全影响**: LLVM 值语义约束: extractvalue 链合并须保持字段顺序与类型(data / lock_ptr / key / index / size),任一字段错位即破坏 live / in_bounds 检查所用元数据;合并只改聚合形态,不改检查发射逻辑。必须保持 fat 65 / fat_cve 76 不回归(检查节点数与 trap 行为不变)。
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
- **预期收益**: 估计 — queen 表示成本 23588ms(占总成本 93.3%,analysis §2②)的部分消除;本项为源码级变体,收益以实施后三态紧邻重测为准。
- **风险与安全影响**: 基准语义保持(须与 raw 套件同步,del 语义不破坏);扁平化布局不可绕过安全检查——仅减少聚合构造开销,live / in_bounds 检查语义仍须保持;不影响编译产物安全属性。
- **验证方法**: 三态紧邻重测 `python3 scripts/bench_fat.py --suite shootout --pin 4`(对比 queen / list 的 ③−① 倍率变化)+ 全量回归 `python3 scripts/run_tests.py`、`python3 scripts/run_fat_tests.py`、`python3 scripts/run_fat_cve.py` 确认基准等价。

### 4.2 方向②: 动态频率型检查合并【第二优先】

对应成本①(检查发射)的动态场景: 静态检查密度不高、但海量动态执行使检查成本绝对显著(list / towers / fasta)。本方向含 2 个条目。

#### 4.2.1 热循环检查合并 + 字段复用

- **原理**: list `is_shorter_than` 87.67% 自占,链表 O(n²) 两两比较,每步对胖指针取字段(data / size)并做检查(锁 + 越界);静态检查密度三人组最低(cfg 18 节点)但海量动态执行使绝对检查成本最高(51500ms,占比 21.2%)。热循环内相邻访问共享同一胖指针时,锁检查与字段提取可合并、复用。
- **挂载点**: CFG 检查发射的 Load / Store 对 `compiler/codegen/cfg/builder.py` L1095-1133(检查节点发射点: CheckRefAccess L1095 / L1115 / L1130、CheckInBounds L1098、CheckSafeAccess L1118 / L1133)+ 检查节点族 `compiler/codegen/cfg/ir.py` L216 起(CheckSafeAccess / CheckInBounds / CheckElementArith / CheckRefAccess / CheckRawBounds)。
- **预期收益**: list 检查成本 51500ms(占总成本 21.2%,仍为全部基准绝对最高,analysis §2①)为可消除上界;本项针对动态频率型检查的合并/复用,不承诺全量消除,实际收益以实施后重测为准。
- **风险与安全影响**: 合并后须保持「每访问」语义等价: 相邻访问共享同一检查的前提是可证明同一指针处于同一有效窗口;任何错误合并即放宽 live / in_bounds 属性。fat 70 / fat_cve 80 不回归。
- **验证方法**: `python3 scripts/run_tests.py`(回归)+ `python3 scripts/run_fat_tests.py`(fat 70)+ `python3 scripts/run_fat_cve.py`(fat_cve 80)+ 三态紧邻重测 `python3 scripts/bench_fat.py --suite shootout --pin 4`(对比 list ③−② 倍率,现状 1.12×)。

#### 4.2.2 towers / fasta 挂载热点

- **原理**: towers 检查成本 4028ms(占总成本 44.5%)、fasta 检查成本 684ms,检查占比显著(≥44%),属动态频率型;两基准无三层归因(仅三态时间),瓶颈形态为外推: 每步取字段 + 检查。(fasta 数值为 Fix B(T[] 整数索引内建)后 genelist T[] 化定向结论 -27.9%(快于 T*,bench-ptr-to-view task-4)+ 2026-08-21 bench-rerun c8 全量重测: ③−② = 3550.2−2866.1 = 684ms;②nocheck 2866.1 < ①raw 2919.5 呈 IQR 内噪声倒置,占比按绝对 ms 呈现,不套 (③−②)/(③−①) 公式。)
- **挂载点**: 基准源码级热点: `bench/shootout/towers.an`、`bench/shootout/fasta.an`(每步取字段处;对应 raw 套件 `bench/shootout_raw/` 同步)。
- **预期收益**: 估计 — 无 perf 归因,仅三态时间;现状检查成本(towers 4028ms,analysis §2① / fasta 684ms,bench-rerun c8)为可消除上界。
- **风险与安全影响**: 同 4.2.1: 检查合并 / 字段复用须保持每访问语义等价,不得放宽 live / in_bounds 检查;fat 70 / fat_cve 80 不回归。
- **验证方法**: 三态紧邻重测 `python3 scripts/bench_fat.py --suite shootout --pin 4`,对比 towers / fasta 的 ③−② 倍率(现状检查占比 towers 44.5% / fasta 按绝对 ms 684ms 呈现)+ 回归 run_fat_tests / run_fat_cve。

### 4.3 方向③: 检查分级 / 静态消除【第三优先】

对应成本①(检查发射)的静态场景: 固定形状循环中可静态证明的索引检查提升或合并。本方向含 2 个条目。

#### 4.3.1 固定形状矩阵循环索引提升

- **原理**: spectralnorm 静态检查密度最高(cfg 27 节点、icmp 检查差分 +81)但检查绝对成本小(888ms / 11.4%);固定形状矩阵循环中 `0 ≤ i < size` 可静态证明,索引检查可提升到循环外或合并为每行 / 每矩阵一次。
- **挂载点**: 检查发射插入点 `compiler/codegen/cfg/builder.py` L1095-1133(Load / Store 检查发射)+ 检查节点族(`compiler/codegen/cfg/ir.py` CheckInBounds / CheckElementArith);提升逻辑落于检查发射的条件化,或由 LLVM 端 loop-invariant 化完成。
- **预期收益**: 估计 — 分析无预测;现状检查成本 888ms(占 spectralnorm 总成本 11.4%,analysis §2①)为可消除上界,收益空间受限于检查绝对成本已小。
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

## 6. benchmark 写法合理化策略(2026-08-22)

### 6.1 引言: 数据来源与公平性约束

数据来源: 本节以 `docs/shootout-results.md`(2026-08-22 全量三态实测,commit b2cb369 更新后)§2 实测矩阵与 §3 成本分解为准,逐基准策略分析见 `.omo/evidence/perf-bench-strategy/task-1.txt`。成本分解口径(同 shootout-results.md §3 L81): 表示 = ②nocheck 中位 / ①raw 中位、检查 = ③check 中位 / ②nocheck 中位、总 = ③check 中位 / ①raw 中位。

**公平性约束声明**(用户原话精神): 允许改写 benchmark 来提升性能,但仅限于让 benchmark 的写法更合理。禁止在 benchmark 中使用过于技巧性的手段来规避性能开销(如 `bitcast` / `from_raw_parts` / 手动内存管理 / unsafe 语义伪造等 restricted_ops 禁止操作),这对于实验来说是有失公平的;benchmark 的代码必须是生产环境中可以写出的常见代码。

**白名单声明**: 本节合理化建议仅限闭集 {match 化, 直接解引用, T[]/range 整数索引, 值传递, 保持现状};任一基准的建议必须落在此闭集内,不含任何技巧性规避手段。

### 6.2 6 基准策略表

策略对象为总成本倍率最高的 6 个基准(总成本倍率 ≥1.4×);数据行号均指向 shootout-results.md §3。

| 基准 | 总成本倍率(表示/检查) | 现状写法 | 合理化建议 | 预期收益(标注来源) | 风险 | 公平性 |
|---|---|---|---|---|---|---|
| list | 2.05×(1.84×/1.12×,§3 L89) | opt_copy/opt_next(L41-58)为绕 Option<Element&> 禁止简单复制,以取址参数 + `is_none()` + `unwrap()[0]` 每次重建;is_shorter_than/tail 双重判断(grep is_some/unwrap 28 行 fat / 27 行 raw,task-1 §3.1) | **形态①** opt_copy/opt_next 体内 match 化(消除 is_none/is_some + unwrap 运行时 helper),两套(fat + raw)同步。形态② 调用点内联为备选;选形态①理由: 改动最小、与 towers 先例同构、保留函数边界 | 参考 towers-match-style 先例: 4 函数 match 化后 IR 2709→1529 行、0 个 Option helper,nocheck/raw 倍率 -23.7%(表示成本侧),check 仅 -3.0%(learnings task-2);list 表示成本 1.84× 主导同侧,类比成立。口径: 实际以重测为准 | 解析器不支持限定前缀模式,须用裸模式 Some/None;`None => {}` 需块分支;漏改 raw 套件会致三态口径不一致 | 通过: 消除冗余 helper 属写法更合理,非技巧规避 |
| binarytree | 1.54×(1.40×/1.10×,§3 L85) | ItemCheck(L47-58)用 `is_some` + `unwrap` 双重判断读子节点,左右各一组;DeleteTree 同款(grep 10/10 两套,task-1 §3.2) | ItemCheck 内 match 化: is_some+unwrap 双重判断改为单次 match 解包;DeleteTree 同款可选(仅一次释放,热影响低);两套同步 | 参考 towers-match-style 先例(match 消除 helper 的收益,nocheck/raw -23.7%);binarytree 表示成本 1.40× 为主,有类比空间;每递归层仅 2 次判定,收益可能小于 towers。口径: 参考先例,以重测为准 | ItemCheck 是递归热路径(约 2^(maxDepth+1) 量级),须确认 match 解包不引入额外分支开销 | 通过: 同 towers/list 论证 |
| towers | 2.37×(1.62×/1.46×,§3 L98) | 已全量 match 化(commit 98c4c27,fat + raw 两套 0 处 is_some/unwrap,task-1 §3.3/§5) | **已实施**。写法已合理,瓶颈在编译器侧(表示 1.62× + 检查 1.46× 同为胖指针 5 字段读写/锁槽头/帧锁的固有开销),无进一步写法建议 | 不适用(已达成) | 无 | 通过 |
| queen | 2.09×(2.07×/1.01×,§3 L93) | 已 T[] 化 + 参数化(bool[]/i32[] 块访问语义 32B 表示),is_some/unwrap = 0(task-1 §3.4) | **已实施,无需改写**。表示成本 2.07× 主导,为编译器侧数组表示/访存开销 | 不适用 | 无 | 通过 |
| permute | 1.73×(1.07×/1.61×,§3 L92) | 已 T[] 化(i32[] 块访问语义),is_some/unwrap = 0(task-1 §3.5) | **已实施,无需改写**。检查成本 1.61× 主导,未归因,按编译器侧检查发射处理 | 不适用 | 无 | 通过 |
| storage | 1.44×(1.29×/1.11×,§3 L97;ΔRSS +1751.75MB) | 已 T[] 化 + 值传递;free_tree 残余 Option 访问(is_none/unwrap/is_some,grep fat 3 行含注释 1 / raw 2 行)位于释放阶段非热路径(task-1 §3.6) | **已实施,表示成本为主**;free_tree 残余 Option 访问在释放路径,热影响低,可选改不优先 | 不适用(ΔRSS +1751.75MB 为编译器侧 48B struct 堆表示问题,写法已简化到位) | 无 | 通过 |

### 6.3 排除理由

fasta / revcomp 未入选: 总成本倍率 <1.4× 且瓶颈在检查成本(编译器侧),写法无法改进。

- fasta: 总 1.23×(表示 0.98× / 检查 1.25×,§3 L88),瓶颈在编译器侧检查发射;
- revcomp: 总 1.33×(表示 0.96× / 检查 1.38×,§3 L94),同构。

二者 grep is_some/unwrap 均为 0,本无 Option 写法成分,无改写对象(task-1 §4/§5)。

### 6.4 与 §4.1.3 / §4.2.2 的划界说明

§4.1.3(扁平化存储布局: 数组只存裸 data、元数据单份保存)与 §4.2.2(towers / fasta 挂载热点)是**编译器侧 / 表示侧蓝图**,属未来编译器优化方向(涉及表示布局与检查发射),不是本节的写法策略。本节仅建议生产环境常见写法(match 化、直接解引用、T[]/range 整数索引、值传递、保持现状),不重复 §4 内容,仅在此引用其存在。凡瓶颈归因为编译器侧(表示 / 检查发射 / 堆表示)的基准,本节一律不提出写法建议,标注「已实施」并指向编译器侧原因。

### 6.5 结尾声明

本次仅策略分析与建议,**不改写任何 benchmark 代码**;改写(实施时须 fat + raw 两套同步)留待后续实施计划。

## 7. LLVM IR 级优化启用(C1)与 trap 健全性论证

> 本章记录 `-O` 映射到 LLVM IR pass pipeline 的实施(C1)及其对 fat 指针 trap 语义的健全性论证。实施对应 `.omo/plans/compiler-perf-optimization.md` todo 1(spike)+ todo 2(落地)。

### 7.1 零 IR pass 发现(现状事实修正)

改动前,`compiler/codegen/llvm/emit.py` 的发射路径为: parse_assembly → verify → 直接 emit_object/emit_assembly,**从不运行任何 LLVM IR 级优化 pass**;`create_target_machine(reloc="pic")` 未传 `opt`,走后端默认 `opt=2`。即所有 YIAN 程序实际以「**零 IR pass + 后端 O2**」运行。`docs/performance-analysis.md` §6 L277-283 的「O2→O3 对 YIAN IR 不产生代码差异」论断即由此而来: 该结论是「O2 与 O3 的后端差异」的实测,并非「IR 优化已生效」;IR 级优化(mem2reg/SROA/GVN 等)此前从未运行,是本次 C1 关闭的最大缺口。spike(todo 1)实测: O3 pipeline 在真实 YIAN IR(fat 指针 40B 聚合 + llvm.trap)上 verify() 通过,extractvalue 计数 44→18 / 42→18。

### 7.2 -O × (IR pass, backend opt) 映射矩阵

| -O | IR pass pipeline(PassManagerBuilder) | backend(`create_target_machine`) | 链接 clang |
|---|---|---|---|
| 0 | 不运行(直接发射) | `opt=0` | `clang -O0` |
| 1 | `opt_level=1` + `populate(pm)` + `pm.run(mod)` | `opt=1` | `clang -O1` |
| 2 | `opt_level=2` + `populate(pm)` + `pm.run(mod)` | `opt=2` | `clang -O2` |
| 3 | `opt_level=3` + `populate(pm)` + `pm.run(mod)` | `opt=3` | `clang -O3` |

- 接线: `-O` 值经 `compiler/main.py` 传入 `Emitter.emit_module(..., opt_level=args.O)`;IR pass 仅对非 `-t ll` 目标(bc/obj/asm/exe)生效;`-t ll` 路径直接 emit_ll,输出与改动前逐字节一致(零优化保留,供调试)。注意 `-t bc` 导出的 bitcode 为优化后 IR(非源码级),见 `docs/compile_script.md` §2.4。
- llvmlite 0.44 API 形态(spike 验证): `pm = binding.create_module_pass_manager(); pmb = binding.PassManagerBuilder(); pmb.opt_level = N; pmb.populate(pm); pm.run(mod); mod.verify()` —— `PassManagerBuilder(opt_level=N)` 构造器 kwargs 会 TypeError,`opt_level` 是 setter 属性。

### 7.3 trap 健全性论证(Exit -4 语义不变)

**检查的 IR 形态**(`compiler/codegen/llvm/builder.py` `__emit_check`,L193-211): 每项 fat 检查 = 条件分支 `br i1 <cond>, ok, trap`;trap 块 = `call void @llvm.trap()` + `unreachable`。`llvm.trap` 是声明为 noreturn 的副作用调用(intrinsic,无 readnone/readonly 属性),优化器视为具有不可观察副作用、不可自由移动或删除。

**论证结构**: LLVM 优化器对条件分支只有三类变换,每一类都保持「源语义下条件为真 → ok,为假 → trap」:

1. **条件可证恒真** → 分支折叠为无条件跳到 ok;trap 块成为不可达,可被 dead-block elimination 删除。这类检查在源语义下**从不 trap**(否则条件不可能恒真),消除后仍从不 trap。
2. **条件可证恒假** → 分支折叠为无条件跳到 trap 块,继续 trap。源语义下必 trap 的检查在优化后仍然 trap。
3. **条件不可证** → 分支原样保留,检查完整存在。

因此优化器消除的检查 = 条件可证恒真的检查(永不 trap 的检查),被折叠进 trap 块的检查 = 条件可证恒假的检查(必 trap 的检查);**trap 发生的 (程序, 输入) 集合逐位不变**,退出码 -4 语义不变。

**不存在「删除 trap 调用本身」的变换**: `llvm.trap` 是带副作用的 noreturn 调用,不是 dead code,不会被 DCE 删除;`unreachable` 被 trap 调用支配——若删除 trap 调用,后续 reachable 的 `unreachable` 成为 UB,而优化器不主动引入 UB(且无任何 pass 有此动机)。LLVM 官方亦保证: 对 noreturn 调用的调用点不删除、不可达代码分析以其为终止边界。

**检查条件不受篡改的前提**: 检查条件由 icmp 等整数值运算构成,优化器对其变换保持整数值语义;YIAN 的检查全部在指针访问**之前**发射且访问被检查保护,不存在「优化器把检查视作 UB 前提而删除」的路径(fat 指针无 null 解引用、无越界解引用裸状态,锁槽/帧锁检查条件均为良定义整型比较)。指针算术/比较变换在 LLVM 内存模型内保持语义。

**护栏(负向约束,与论证配套)**: 禁止给释放/写锁槽函数添加 readonly/readnone 属性、禁止将锁槽 load 标记为 invariant——否则 LICM 可把 live 检查跨调用提升或消除,漏报 UAF,破坏 -4 语义。当前实现未添加任何此类属性。

### 7.4 实施中发现: DSE 删除帧退出 SENTINEL 写(volatile 护栏)

全量门禁首跑在 `tests/fat_cve/cve/CVE-2023-26463/`(悬垂返回指针 + 帧退出哨兵机制,规则 3.7.2)上暴露真实破坏: 该用例在 -O3 下期望 `Exit code -4`,实测退出 0——trap 丢失。基线(stash 还原、无 IR pass)实测退出 132(SIGILL,即 -4),确认破坏由 C1 的优化 pipeline 引入。

**根因**: 帧退出 SENTINEL 写 `store i64 18446744073709551615, i64* %e_f` 的**唯一读者**是调用方经悬垂指针(指向已退出帧的锁槽)的 live 检查读。LLVM 内存模型把"经悬垂指针读已退出帧的内存"视为 UB,即该读"永不执行"→ DSE 判定 SENTINEL 写为死存储并删除;锁槽残留入口键值,调用方 live 检查 `槽 == 键` 误通过 → 继续读取已失效帧 → 退出 0。trap 块与检查分支本身均未被删除(优化后 IR 中 live 检查完好,仅哨兵写消失)。

**修复(emit.py,仅优化路径)**: 对非 `-t ll` 目标且 `opt_level > 0` 时,在 parse_assembly 前把 IR 文本中的 SENTINEL 写改写为 `store volatile`(正则匹配 `store i64 18446744073709551615`)。volatile store 不可被 DSE 删除、不可与 volatile 操作重排;调用方 live 检查跨不可见调用(函数边界)必读到 SENTINEL → `SENTINEL != k_f` → trap → Exit -4。`-t ll` 输出不受影响(零优化路径不做任何改写,输出逐字节不变)。语义等价: volatile 只禁止删除/重排,不改变存储的值;合法程序从不读 SENTINEL(其 live 检查恒通过),故正确路径行为零变化。

**为何不采用替代方案**: (a) builder.py 层直接发射 volatile store 会改变 `-t ll` 输出,违反零优化保留约束;(b) 禁用 DSE 等 pass 配置回退会摧毁 C1 的主要收益(mem2reg/SROA/GVN 同样依赖该管道),且 DSE 在 O1+ 全等级存在,无等级可退;(c) 给释放/锁槽写函数加属性属被禁护栏。volatile 标记是唯一"保持 -t ll 不变 + 保持优化收益 + 语义逐位保持"的落点。

**残余风险与覆盖**: 帧锁入口键写(非常量值)未被标记,实测在 CVE-2023-26463 形态(帧槽仅被悬垂读)下 DSE 保留(所有读者与其间的写可证别名关系不同);若未来 LLVM 的 AA 强化将其删除,表现为悬垂访问不再 trap——由 fat_cve 负例闸门持续监控。堆锁槽的 SENTINEL 写发生在 stdlib 受限函数内部(不透明调用),LLVM 无法透视,天然免疫。

**验证闸门**: 全量回归中 fat 负例(期望 `Exit code -4`)在 -O3(回归套件固定编译等级)下全部保持 -4;`-O0` 冒烟(编译 + 运行 Exit 0)补齐零优化路径覆盖。spike 亦实测负例 `fat_uaf.an` 经 O3 pipeline 后运行仍 Exit -4。
