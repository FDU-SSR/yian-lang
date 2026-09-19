# bench — C / raw / fat 三态性能基准

本目录是**三态**性能对照：同一算法、同一规模的三个实现, 以 **C 参考实现为基线**——

| 态 | 实现 | 编译 |
| --- | --- | --- |
| `c` | `bench/c/<name>.c` | `clang -O2 -lm` |
| `raw` | `bench/shootout/<name>.an` | `yianc -O2 --raw-pointers` |
| `fat` | `bench/shootout/<name>.an` | `yianc -O2` (胖指针为默认) |

**没有 `nocheck` 态**：编译器已移除关闭检查的开关, 因此 `raw/C` 是裸指针实现相对 C
的开销, `fat/raw` 是完整胖指针相对裸指针的总开销。这些数字用于观察量级和趋势,
不是严谨的实验结论。

## 一条命令

```bash
# 编译三态 + 校验 C 权威值与两态语义 + 轮转测量 + 生成结果 (建议绑核)
python3 scripts/bench_three_way.py --pin 4
```

`--names a,b` 只测指定基准（写入 `bench/results.partial.{md,csv}`，不覆盖全量结果）；
`--compile-only` 只编译；`--no-compile` 复用已有二进制；`--runs`/`--max-state-sec` 调整
测量次数与降次阈值。

产物：

- `bench/shootout/*.an` — YIAN 基准源码（两态共用；`<name>.raw.an` 为裸态覆盖源）；
- `bench/c/*.c` — 同算法同规模的 C 参考实现；
- `bench/results.md` — 结果表格 + 环境指纹 + 协议说明（人读）；
- `bench/results.csv` — 机读结果（表头注释记录列含义与环境指纹）。

`results.{md,csv}` 是**生成物**，每次全量运行覆盖。二进制写入 `build/bench/{cbin,yfat,yraw}/`
（不提交）。

## 协议

| 项 | 取值 |
| --- | --- |
| 源码 | YIAN 两态共用一份源；C 参考独立一份 |
| C 侧 argv | 脚本内 `C_SPECS` 的 `argv`（`cd 100 80`、`richards 2400`，其余无参），保证与 `.an` 迭代次数一致 |
| 采样 | 每态 1 次 warmup（不计入样本）+ 5 次测量取最小值（同时记录中位数与 CV）；峰值 RSS 取各次最大值 |
| 轮转 | 同一基准的**三态逐次轮转**测量（C, raw, fat, C, raw, fat, …），消除跨时段频率/温度漂移 |
| 降噪 | `taskset -c <cpu>` 绑核；结果文件记录绑核编号 |
| 产物命名 | 三态二进制同目录、**等长路径**（`build/bench/<state>/<name>`，state 目录名等长），避免 argv[0] 长度影响进程布局（分配密集型基准的绝对值对布局敏感） |
| 语义护栏 | ① C warmup 的 `(退出码, stdout)` 必须满足脚本内 `C_SPECS` 的权威值；② raw 与 fat 的 warmup stdout/退出码必须逐字节一致；③ 两态退出码必须为 0。任一不成立即该基准的比值不可用（报告标 `否`） |
| 自适应降次 | 三态 warmup 合计超过 `--max-state-sec`（默认 120 s）时测量次数降到 3 |

## 结果解读

- 三个比值都取自**最小值**：`raw/C`、`fat/C`（以 C 为基线）与 `fat/raw`。比值是基准内的
  相对量, **不可跨基准相加**；报告里的“几何平均比值”只作总览。
- `raw/C` ≈ 1.0 表示裸指针实现的抽象开销被优化器基本消除；`fat/C` 是完整胖指针（含检查、
  锁槽/帧锁、胖表示本身）相对 C 的总开销。
- 同一二进制重复运行会落在相差约 20% 的两个性能状态上（进程级内存布局/频率假象）, 所以
  正式指标取最小值、测量轮转、绑核；中位数与 CV 只用于判断重复性。绝对时间只在同一台
  机器、同一时段内可比。

## 基准与来源

`bench/shootout/` 共 19 个基准 + 1 个裸态覆盖源：

| 基准 | 内容 | 上游 |
| --- | --- | --- |
| binarytree | 二叉树构建/遍历 | Benchmarks Game |
| bounce | 球体弹跳模拟（LCG 随机数） | AWFY |
| cd | 碰撞检测（红黑树 + 体素归并） | AWFY（CD） |
| deltablue | 约束求解器（DeltaBlue） | AWFY（DeltaBlue） |
| fann | 浮点神经网络前向计算（fannkuchredux） | Benchmarks Game |
| fasta | 随机 DNA/氨基酸序列生成 | Benchmarks Game |
| havlak | 循环识别（Havlak，图算法） | AWFY（Havlak） |
| json | JSON 解析与序列化 | AWFY（Json） |
| list | 链表构建与遍历 | AWFY |
| mand | Mandelbrot 集合 | AWFY（Mandelbrot） |
| nbody | N 体积分（浮点密集） | AWFY |
| permute | 排列枚举 | AWFY |
| queen | N 皇后 | AWFY（Queens） |
| revcomp | 序列反向互补 | Benchmarks Game |
| richards | 任务调度器模拟（Richards） | AWFY（Richards） |
| sieve | 埃拉托斯特尼筛法 | AWFY |
| spectralnorm | 谱范数幂迭代 | Benchmarks Game |
| storage | 树形结构的分配/回收 | AWFY |
| towers | 汉诺塔 | AWFY |

来源与许可：

- YIAN 侧代码为本仓库改写版，逐文件在头部标注上游来源与许可状态；`bench/c/*.c` 为本仓库
  自写的 C 参考实现，无上游许可头；
- 14 项改写自 **AWFY**（[are-we-fast-yet](https://github.com/smarr/are-we-fast-yet)，
  `benchmarks/Java/src/`）：Bounce、CD、DeltaBlue、Havlak、Json、List、Mandelbrot、NBody、
  Permute、Queens、Richards、Sieve、Storage、Towers；
- 5 项改写自 **Benchmarks Game**（shootout）：binarytree、fann、fasta、revcomp、spectralnorm；
- 许可：AWFY 的 `LICENSE.md` 说明 Richards、DeltaBlue 源自 Mario Wolczko 的 Smalltalk 版本
  （许可指向已归档的 Sun Labs 页面）；其 Benchmarks Game 部分为 Revised BSD（Copyright
  2008-2012 Isaac Gouy）。AWFY 的 Java 文件本身带 MIT 头（Copyright (c) 2001-2016 Stefan
  Marr；Json 为 2015，部分文件另含 EclipseSource 版权），`havlak/HavlakLoopFinder.java` 为
  Google 的 Apache-2.0（Copyright 2011 Google Inc.）；`cd/*.java` 无逐文件许可头。

## 算法与规模对齐

C 要当基线, 前提是两侧跑的是同一算法、同一规模。19 项逐一核对过（输入规模、迭代次数、
数据结构、断言值），结论与处理如下：

- **17 项同算法同规模**：binarytree、bounce、deltablue、fann、fasta、havlak、json、list、
  mand、nbody、permute、queen、revcomp、sieve、spectralnorm、storage、towers。差异只在
  表示与内存管理（见下），迭代次数与每步工作量一致。
- **cd / richards：C 参考的默认迭代次数与 `.an` 不同**——`cd.c` 默认 1 轮而 `cd.an` 跑 80 轮,
  `richards.c` 默认 1 轮而 `richards.an` 跑 2400 轮。两个 C 参考都支持 argv 覆盖, 因此由
  测量脚本显式传参（`100 80` / `2400`）对齐, 并用对齐后的权威值校验。
- **nbody / cd / spectralnorm：平方根原语**——这三处原先在 YIAN 侧手写牛顿迭代（语言此前没有
  平方根内建），与 C 的 libm `sqrt` 每处相差数十倍工作量。语言现提供 `@sqrt` 内建与标准库
  `std.num.f64.sqrt`（`llvm.sqrt.f64` → `sqrtsd`），三处都改用该函数, 两侧热循环原语相同,
  断言值不变。
- **storage：C 的 LCG 状态原为 32 位、`.an` 为 64 位**，叶子容量抽样因此不同（叶子元素总量
  差约 0.003%, 计数不变）。已把 C 参考的 `Random` 状态改为 64 位, 两侧抽样一致。
- **cd：C 参考原先不释放每帧的体素/`seen` 结构**，`./cd 100 80` 峰值 RSS 约 1.6 GB, 而 YIAN
  侧约 2-4 MB。已按 `.an` 的做法逐帧释放（体素树与 `seen` 树、`MotionList`、被替换的
  `Vector3D`）, 两侧内存行为一致。

已知的**常量级**差异（不改变迭代次数与每步算法, 保留为各语言的惯用写法）：json 的 YIAN 每轮
新建 `Parser` 而 C 复用同一个；cd 的 YIAN 在树中按值存 `Vector3D` 而 C 存指针, 且 `sin`/`cos`
用 fdlibm 内核多项式实现（C 用 libm, 精度差 <1ulp; 只在每帧初始化飞机位置时调用, 不在碰撞
热路径上）；queen 的自由列标记 YIAN 用 `bool[]`、C 用 `int[]`；havlak 的并查集 YIAN 用下标、
C 用自指针。这些影响的是常数因子而不是工作量, 读比值时按“语言实现差异”理解。

## 单一源的合并规则

19 个基准里 18 个**每个只有一份源**（`storage` 有 `.raw.an` 覆盖源），YIAN 两态共用。合并
按以下实测规则进行：

1. `dyn[N] T` 在胖模式下是 `T[]`、裸模式下是 `T*`，且**两个方向都不能隐式互转**
   （胖下 slice→`T*` 报 E401，裸下 `T*`→切片报 E499）→ 合并时去掉 `dyn` 的显式
   类型标注，交给类型推断；
2. 需要切片处用**范围视图** `x[0u64..n]`：两种模式都合法（裸态下是 `T*` 的
   `Index<Range<u64>, T[]>` 实现），且视图变量显式标注 `T[]`；
3. `@slice_from_parts` 等受限内建只允许标准库与 `tests/*/std/` 路径，`bench/` 调用
   报 E501，因此基准不得使用；
4. `comptime if` 只能表达**值级**差异（两个分支都会被类型检查），不能表达类型分歧。

**性能优先约束**：这是性能测评，为统一源码引入的任何开销都会直接污染比值。合并
只允许三类改动——去掉 `dyn` 标注、视图构造替换、为统一而必要的签名等价形式；
热路径不得新增构造/转换/拷贝，视图构造必须提到循环外（每次运行一次）。若统一形式
改变调用约定或引入额外检查，必须同机计时判定，噪声内才允许统一，否则保留该基准的
两份模式专源。

### 计时判定与例外

- **`list`（统一采用按值传）**：分支的胖版全程按值传 `Option<Element&>`，裸版改为
  传指针。两种形式在两模式下都合法，同机计时（`-O2`，两次取最好）：

  | 模式 | 按值传 | 按指针传 |
  | --- | --- | --- |
  | fat | **32.47 s** | 34.71 s |
  | raw | **7.29 s** | 7.40 s |

  按值传在胖下快约 6.9%、裸下也不慢，因此统一采用按值传的单一源。该选择直接决定
  报告的 `fat/raw`, 故在此留档。

- **`storage`（保留两份模式专源：`storage.an` + `storage.raw.an`）**：该基准的
  函数把分配结果作为 `Option<array_tree[]>` 返回。胖模式下可返回的切片必须是**堆
  owner**的（`let arr: array_tree[] = dyn[n] array_tree;` 直接返回）；若按合并配方
  在函数内构造局部视图再返回，视图的 owner 是**当前帧锁**，函数返回后帧锁槽被置
  `SENTINEL`，返回的切片即失效。裸模式没有帧锁机制，只能用 `arr[0u64..n]` 形式。
  两模式没有既合法又等价的共同写法，故保留两份专源（各自取该模式的分支原形式）。

- **`sieve`（单一源，胖态 +6%）**：合并源用范围视图 `flags[0u64..SIEVE_SIZE]` 同时
  承担填充与传参，是单源下的最优形式：填充走视图比走 `dyn` 变量（`Index` trait 派发）
  快约 9%。但视图 owner 是帧锁，胖态下填充循环里的 `live` 检查（`load lock; icmp key`）
  无法被优化器外提，而同机分支胖源（`let flags: bool[] = dyn[...]`，堆 owner）可以外提，
  因此合并源胖态仍比分支源慢约 6%（约 3.17 s vs 2.98 s）。该差异只影响胖态绝对值、
  不改变算法。

## 影响当前测量的问题

- **fat 运行时分配器的 free-list 退化（O(N²)）**：分配大块时会顺序跳过 free-list 上的小块,
  “先释放大块再释放小块”的分配模式下, 每个大块分配都要扫过所有小块。DeltaBlue 在上游默认
  规模（N=7000）下 fat 6.53 s / raw 0.027 s（240×）, 分配计数器显示两态调用次数逐个数字
  完全相同, 即工作量一致、无模式相关复杂度差异；纯分配微基准独立复现了 O(N²)。因此
  `deltablue.an` 与 C 参考都用 N=100 + 14000 次外循环（每趟 356660）。该问题尚未修复。

## 不包含的内容

- `nocheck` 对照态：编译器已无该开关；
- `bak/` 下的历史基准与 `docs/` 下的历史结果表。
