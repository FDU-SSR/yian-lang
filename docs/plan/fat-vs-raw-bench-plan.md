# fat vs raw 性能测评计划（移植自 archive/secl-paper-20260909）

## 1. 目标与范围

### 1.1 目标

把 `archive/secl-paper-20260909` 分支上的 shootout 性能测评资产移植到当前仓库，只保留 **fat（默认，带检查）** 与 **raw（`--raw-pointers`）** 两态：

- **14 个基准各一份源码**（默认），同一份源码用两套编译参数分别构建 fat 与 raw；若某基准的两态最优形式无法统一，则该基准保留两份模式专源（见 §3.4）；
- 一条命令产出时间/RSS 对比表（`bench/results.csv` + `bench/results.md`）；
- 带回归门槛的检查脚本；
- 记录测量协议与来源，结果可外部复核。

### 1.2 不做

- **`nocheck` 态**：分支第三态由 `--no-fat-checks` 产生，该开关在当前编译器里**已不存在**；分支的"表示成本/检查成本"分解（②/①、③/②）随之取消，只保留总成本（fat/raw）。
- **两份平行源码**：分支用 `bench/shootout/`（fat）与 `bench/shootout_raw/`（raw）两份源码；本计划合并为每基准一份（见 §3）。原 `bench/shootout_raw/` 目录不再存在。
- 论文资产（`paper/`）、`scripts/rerun_paper_experiments.sh`、ASAN 对比、C 跨语言计时、LLVM 15 时代的历史结果表（`docs/performance.csv`、`docs/perf-baseline.csv`）。

## 2. 分支资产与取舍

| 资产 | 规模 | 取舍 |
| --- | --- | --- |
| `bench/shootout/*.an` + `bench/shootout_raw/*.an` | 14 + 14 | **合并为 14 个单一源**（以 raw 版为基线，见 §3.2） |
| `bench/c/*.c` | 14 | 可选（仅作正确性参考；无许可头，若拉入需在 README 标注来源） |
| `scripts/bench_fat.py` | 1417 行 | **裁剪**：去掉 nocheck、ASAN、C 参考、baseline csv、成本分解表；保留测量内核（绑核、warmup、runs、中位数、RSS、环境指纹） |
| `scripts/check_perf_regression.py` | 185 行 | **裁剪**：门槛改为 fat/raw 比值 + fat 绝对时间 |
| `scripts/verify_bench_c.py` | 90 行 | 可选（C 交叉验证） |
| `scripts/rerun_paper_experiments.sh` | 223 行 | 不拉 |
| `paper/**`、`docs/shootout-results.md` | — | 不拉（结果格式可参考） |

新仓库布局：

```text
bench/
  shootout/        # 14 个单一源基准 (fat/raw 共用; 来自分支两目录的合并)
  README.md        # 测量协议 + 来源与许可说明
  results.md       # 生成：可读对比表 + 环境/协议
  results.csv      # 生成：机器可读
scripts/
  bench_fat_vs_raw.py        # 生成：裁剪后的测量脚手架
  check_bench_regression.py  # 生成：回归门槛
```

## 3. 单一源的写法（已实测确认）

### 3.1 当前语言的四条规则（实测）

| 问题 | 实测结论 |
| --- | --- |
| `dyn[N] T` 的类型 | fat 下是 `T[]`，raw 下是 `T*`；**两个方向都不能隐式互转**（fat 下 slice→`T*` 报 E401，raw 下 `T*`→slice 报 E499） |
| 范围切片 `x[0u64..n]` | **两种模式都合法**（fat 下走切片索引，raw 下走 `T*` 的 `Index<Range<u64>, T[]>`）；fat 模式下内部的指针算术仍做越界检查 |
| `@slice_from_parts` | 是受限内建，只允许 `lib/src`、`tests/basic/std/`、`tests/safety/std/` 三个确切路径；`bench/` 这类路径调用会报 **E501**（受限操作）。没有放开该限制的 CLI 开关 |
| `comptime if` | **条目级不支持**（E202）；语句/表达式级**两个分支都会被类型检查**（raw 模式下若 else 分支含 `T*`→`T[]` 隐式转换会报 E499）→ 只能用于**值级**差异（如尺寸/布局），不能表达类型分歧 |

### 3.2 合并配方（在 queen、list 上验证：两模式均编译并运行通过）

以 raw 版为基线做三件事：

1. 删除 `from std.core.slice import from_raw_parts;`；
2. `from_raw_parts(x, n)` → `x[0u64..nu64]`（公开范围视图；两模式通用，fat 下仍受检查）；
3. 去掉 `dyn` 的显式标注（`: T[]` / `: T*` → 类型推断）；**视图变量**保留显式切片类型（两模式下都是 `T[]`）。

签名分歧按"两模式都接受的形式"统一：例如 `list` 的 `Option<Element&>` 参数，raw 版用指针形式 `Option<Element&>*`，该形式在 fat 下同样合法（已实测），因此统一采用指针形式。

`comptime if` 只在确实需要"同类型、不同值"时使用（例如按模式断言尺寸），与 `tests/basic/enum/niche_negative.an` 的既有用法一致。

### 3.3 规模与运行时长（实测）

- 分支基准的规模硬编码在源码注释里（含与 C 版对照说明），两态同规模；
- fat 单趟 14 个基准合计约 **83 s**：`list` 32.5 s、`nbody` 10.6 s、`queen` 7.5 s、`towers` 5.0 s、`binarytree` 3.8 s、`permute` 3.4 s、`storage` 3.0 s、`bounce` 2.9 s、`sieve` 2.9 s、`mand` 2.6 s、`fasta` 2.3 s、`spectralnorm` 2.1 s、`fann` 1.6 s、`revcomp` 0.6 s；
- 按分支协议（warmup 1 + runs 5，即每基准每态 6 次）全量一轮约 **10–12 分钟**；`list` 单基准占近四成，可用 `--max-state-sec` 之类的限时把慢基准的 runs 降到 3。

### 3.4 性能优先原则（硬约束）

这是性能测评，**基准自身的实现必须是该模式下的最优形式**；为统一源码而引入的任何开销都会直接污染 fat/raw 的比值。合并时必须满足：

1. **算法、数据布局、分配次数、拷贝次数与分支版本一致**；不重构、不"顺手优化"、不为可读性改变循环结构。
2. **热路径不得新增构造/转换/拷贝**：视图构造（`x[0u64..n]`、切片创建）必须放在循环外或每次运行一次；不得在循环体内为统一写法引入范围视图、类型转换或临时对象。
3. **统一形式改变调用约定或引入额外检查时，必须用同机计时判定**：候选形式在同一模式下计时差异落在噪声内才允许统一；否则该基准**保留两份模式专源**（各自取该模式最优形式），并在 README 记录判定依据与数据。
4. **合并 diff 逐条 review**：只允许出现"去掉 `dyn` 类型标注""视图构造替换""为统一而必要的签名等价形式"三类改动；出现第四类改动即回退重做。
5. 编译参数属于协议而非基准选择：两态同一优化级（默认 `-O2`）、同一 `lib/src`。

已实测的候选案例 `list`：fat 版全程按值传 `Option<Element&>`，raw 版全程改为传指针（且 fat 版在 raw 下也能编译，说明指针形式是作者的选择，不是语言强制）。同机计时（`-O2`，两次取最好）：

| 模式 | 按值传 | 按指针传 |
| --- | --- | --- |
| fat | **32.47 s** | 34.71 s |
| raw | **7.29 s** | 7.40 s |

按值传在 fat 下快约 6.9%，在 raw 下也不慢，因此该基准**统一采用按值传的单一源**（不需要两份专源）。判定数据记入 `bench/README.md`；该选择同时决定报告的比值（按值为 4.46×，按指针为 4.69×），故必须留档。

已实测的候选案例 `sieve`：分支 fat 源 `let flags: bool[] = dyn[...]`（数组 owner 是堆块），raw 源用 `from_raw_parts(flags, n)`；两模式共同的写法只有去掉标注 + 范围视图。同机绑核逐次交替（`taskset -c 4`，3 轮中位数，ms）：

| 形式 | fat |
| --- | --- |
| 分支 fat 源（对照） | 2983 |
| 合并：填充走 `flags`（dyn 变量）+ `sieve` 传视图 | 3462 |
| 合并：填充与传参都走视图 | 3172 |

填充走视图比走 `dyn` 变量快约 8.4%：`dyn` 变量的整数下标在 fat 下走 `Index` trait 派发，而切片下标有内建路径。残余 +6.3% 来自视图 owner 是**帧锁**（无类型标注就无法像分支那样拿到堆 owner），胖态填充循环里的 `live` 检查（`load lock; icmp key`）因此无法被优化器外提。取"都走视图"为单一源形式，该差异只影响胖态绝对值、不改变算法，记入 `bench/README.md`。按规则 4 归类，`flags_view[i] = true` 属于第三类"为统一而必要的等价形式"：分支源里被填充的 `flags` 本身就是切片，合并源里这个角色由视图承担，且已计时验证（否则该基准按规则 3 就要保留两份专源）。

已实测的候选案例 `storage`：函数返回 `Option<array_tree[]>`，胖模式可返回的切片必须是堆 owner（`let arr: array_tree[] = dyn[n] array_tree;` 直接返回）；按合并配方在函数内构造局部视图再返回，owner 是当前帧锁，函数返回后锁槽置 `SENTINEL`，返回的切片即失效；裸模式无帧锁，只能用 `arr[0u64..n]`。两模式没有既合法又等价的共同写法，按规则 3 保留两份模式专源（`storage.an` + `storage.raw.an`）。

## 4. 测量协议（沿用分支）

- **绑核**：`taskset -c <cpu>`（可选；正式记录需绑核）；
- **采样**：每基准每态 1 次 warmup + 5 次测量取**中位数**，RSS 取最大值；
- **交替**：同一基准两态紧邻测量，避免频率/温度漂移；
- **编译**：两态同一优化级（默认 `-O2`）、同一 `lib/src`；raw 用 `--raw-pointers`；源码为同一份；
- **环境指纹**：CPU 型号/核数、内存、内核、`llvmlite` 的 LLVM 版本、clang 版本、优化级，写入结果文件头条；
- **正确性判据**：同一基准的 fat 与 raw 输出必须**逐字节一致**；不一致即移植/编译问题，测量作废。

## 5. 阶段与验收

> 状态（2026-09-18）：P1–P4 均已落地。基准与结果见 `bench/`（`README.md`、`results.md`、
> `results.csv`），测量与门禁见 `scripts/bench_fat_vs_raw.py`、`scripts/check_bench_regression.py`。
> 未做的只有 P4 的可选项（`bench/c/*.c` 跨语言参考源）。

### P1 拉取并合并基准

- 拉入分支的 28 个源作为合并素材，按 §3.2 产出 `bench/shootout/` 下的 **14 个单一源**（保留源码注释里的来源与规模说明，补充"由两版合并"的说明）；无法满足 §3.4 性能优先约束的基准，按该基准保留两份模式专源；
- 验收：14 个源在两模式下全部 `-O2` 编译通过、运行通过、stdout 逐字节一致；§3.4 的 diff review 通过；需要计时判定的基准给出数据与结论；记录单趟耗时。

### P2 裁剪测量脚手架

- 从分支 `scripts/bench_fat.py` 裁剪出 `scripts/bench_fat_vs_raw.py`：保留测量内核与 §4 协议，去掉 nocheck/ASAN/C 参考/baseline/成本分解；由于两态同源，编译步骤简化为"同一源、两套参数"；
- 输出 `bench/results.csv`（基准、fat 中位数、raw 中位数、比值、fat/raw RSS）与 `bench/results.md`（表格 + 环境指纹 + 协议）；
- 验收：一条命令跑完全套并生成两份结果；记录重复运行的 CV。

### P3 回归门槛

- 从 `scripts/check_perf_regression.py` 裁剪出 `scripts/check_bench_regression.py`：门槛为 fat/raw 比值与各基准 fat 绝对时间的单侧 ±20%（可配置），保留慢基准限时；
- 验收：当前树上通过；人为放大某基准规模能触发失败。

### P4 收口

- `bench/README.md`：协议、运行方式、结果解读、**来源与许可**（移植自 `archive/secl-paper-20260909`；shootout 基准源自经典 Benchmarks Game 的改写）；
- 可选：拉入 `bench/c/*.c` 与 `scripts/verify_bench_c.py` 作跨语言正确性参考（不参与 fat/raw 计时）；
- 验收：README 与脚本行为一致；一条命令完成"编译两态 + 校验输出一致 + 测量 + 生成结果"。

## 6. 风险与开放问题

- **统一造成的性能漂移**：为单一源而改变调用约定或引入构造，会直接改变被测比值。§3.4 的计时判定与"两份模式专源"例外是这条的兜底；`list` 是已识别的候选案例。
- **单一源写法的边界**：若某个基准存在"两模式没有共同合法写法"的分歧（目前 14 个里只有视图构造与一个签名分歧，均有共同写法），需要单独处理并记录。
- **语言仍在演进**：基准来自 2026-08/09；若后续再动标准库或指针语义，需重跑 P1 验收（§3.1 的四条规则是判断依据）。
- **`list` 主导时长**：占全量约 40%，可对慢基准降低 runs（协议注明）或缩小规模（两态同规模）。
- **测量噪声**：仅绑核 + 交替 + 中位数可比；LLVM 15 → 22 已换代，分支的历史数字不能直接对照。
- **正确性判据边界**：fat/raw 输出一致只证明两态做同一件事，不证明基准实现了目标算法（C 交叉验证可补，P4 可选）。
- **许可**：`bench/c/*.c` 无许可头；若拉入为参考需在 README 注明来源与许可状态。

## 7. 参考

- 来源分支：`archive/secl-paper-20260909`（最近提交 `70db192`）；关键文件：`scripts/bench_fat.py`、`scripts/check_perf_regression.py`、`bench/shootout*/`、`docs/shootout-results.md`
- 当前仓库对应物：`lib/src/core/pointer.an`（`T*` 的 `Index<Range<u64>, T[]>`）、`lib/src/core/range.an`、`lib/src/core/slice.an`、`compiler/analysis/source_provenance.py`（受限操作的信任路径）
- 工具链基线：`docs/plan/runtime-rework-plan.md` §1.3（LLVM 22 下的 fat/raw 比值）
