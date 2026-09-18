# bench — 胖指针 vs 裸指针性能基准

本目录是 **两态**性能对照：同一份 YIAN 源码分别用胖指针（默认）与裸指针
（`--raw-pointers`）编译，比较端到端墙钟时间与峰值内存。**没有 `nocheck` 态**：
编译器已移除关闭检查的开关，比较对象就是完整胖指针实现相对裸指针实现的总开销。

## 一条命令

```bash
# 编译两态 + 校验两态输出一致 + 测量 + 生成结果 (建议绑核, 正式记录必须绑核)
python3 scripts/bench_fat_vs_raw.py --pin 4

# 与已提交基线对照 (单侧 ±20%: 只拦变慢)
python3 scripts/check_bench_regression.py
```

产物：

- `bench/shootout/*.an` — 基准源码（单一源，两态共用；`<name>.raw.an` 为裸态覆盖源）；
- `bench/c/*.c` — 同规模的自写 C 参考，只做跨语言正确性对照；
- `bench/results.md` — 表格 + 环境指纹 + 协议说明（人读）；
- `bench/results.csv` — 机读基线，由 `scripts/check_bench_regression.py` 对照。

`results.{md,csv}` 是**生成物**，每次运行覆盖；`results.csv` 需要提交，它是回归门禁的基线。
二进制与中间日志写入 `build/bench/`（不提交）。

## 协议

| 项 | 取值 |
| --- | --- |
| 源码 | 每个基准一份源，两态共用 |
| 编译 | 胖态 `yianc -O2 lib/src <src> -o <bin>`；裸态追加 `--raw-pointers`；两者同一 `lib/src` |
| 采样 | 每态 1 次 warmup（不计入样本）+ 5 次测量取中位数；峰值 RSS 取各次最大值 |
| 交替 | 同一基准的**两态逐次交替**测量（fat, raw, fat, raw, …），消除跨时段频率/温度漂移 |
| 降噪 | `taskset -c <cpu>` 绑核；结果文件记录绑核编号 |
| 产物命名 | 两态二进制同目录、等长文件名（`<name>.fat` / `<name>.raw`），避免路径长度影响进程布局（分配密集型基准的绝对值对布局敏感） |
| 语义护栏 | 两态 warmup 的 stdout 与退出码必须一致；不一致即基线失效，脚本报错并在报告标注 |
| 自适应降次 | 两态 warmup 合计超过 `--max-state-sec`（默认 120 s）时测量次数降到 3 |

与来源分支 `archive/secl-paper-20260909` 的协议差异：该分支为三态
（`raw` / `nocheck` / `check`）、块式分态测量、`-O3`；此处按两态、逐次交替、`-O2`
重新基线化。**分支的历史数字不可直接对照**（LLVM 已换代）。

## 结果解读

- **比值 = fat 中位数 / raw 中位数**，即完整胖指针相对裸指针的总开销（含检查、
  锁槽/帧锁、胖表示本身）。比值是基准内的相对量，**不可跨基准相加**；报告里的
  “几何平均比值”只作总览。
- 比值 ≈ 1.0 表示两态性能相当（该基准的检查被优化器基本消除，或本来就不是检查
  敏感型）；比值高表示该基准的访问模式触发大量运行时检查，是胖指针开销的主要来源。
- 基线对照是**单侧**的：只拦“变慢”与“比值变大”，不拦变快。环境指纹
  （hostname / machine / cpu_count / clang）不一致时门禁拒绝判定（退出码 2），
  因为绝对时间跨机不可比；跨机对照须显式 `--allow-env-mismatch`，结论仅供参考。

## 基准与来源

`bench/shootout/` 共 14 个基准 + 1 个裸态覆盖源：

| 基准 | 内容 | 上游 |
| --- | --- | --- |
| binarytree | 二叉树构建/遍历 | Benchmarks Game |
| bounce | 球体弹跳模拟（LCG 随机数） | AWFY |
| fann | 浮点神经网络前向计算 | Benchmarks Game |
| fasta | 随机 DNA/氨基酸序列生成 | Benchmarks Game |
| list | 链表构建与遍历 | AWFY |
| mand | Mandelbrot 集合 | AWFY（Mandelbrot） |
| nbody | N 体积分（浮点密集） | AWFY |
| permute | 排列枚举 | AWFY |
| queen | N 皇后 | AWFY（Queens） |
| revcomp | 序列反向互补 | Benchmarks Game |
| sieve | 埃拉托斯特尼筛法 | AWFY |
| spectralnorm | 谱范数幂迭代 | Benchmarks Game |
| storage | 树形结构的分配/回收 | AWFY |
| towers | 汉诺塔 | AWFY |

`bench/c/` 是**同规模的自写 C 参考实现**（只用于跨语言正确性对照，不参与 fat/raw
计时），语义权威值与各 `.an` 头部注释对齐，一条命令校验：

```bash
python3 scripts/verify_bench_c.py      # 编译 clang -O2 -lm 并断言校验和, 14/14 PASS
```

来源与许可：

- 性能测评代码移植自本仓库分支 `archive/secl-paper-20260909`（`bench/shootout`、
  `bench/shootout_raw`、`scripts/bench_fat.py`，最近提交 `70db192`）；
- 9 项改写自 **AWFY**（[are-we-fast-yet](https://github.com/smarr/are-we-fast-yet)，
  `benchmarks/Java/src/`）：Bounce、List、Mandelbrot、NBody、Permute、Queens、Sieve、
  Storage、Towers。AWFY 另有 5 个宏基准（CD、Havlak、Richards、DeltaBlue、Json），
  本目录的补齐进度见 `docs/plan/fat-vs-raw-bench-plan.md` §5 P5；
- 5 项改写自 **Benchmarks Game**（shootout）：binarytree、fann、fasta、revcomp、
  spectralnorm；
- 每个 `.an` 头部标注上游来源与许可状态，并保留**语义权威源**（`bench/c/<name>.c`）
  与规模调整说明；规模在两态相同，只按可接受的运行时长做过折中；
- 许可：AWFY 的 `LICENSE.md` 说明 Richards、DeltaBlue 源自 Mario Wolczko 的 Smalltalk
  版本（许可指向已归档的 Sun Labs 页面），其 Benchmarks Game 部分为 Revised BSD
  （Copyright 2008-2012 Isaac Gouy）；CD、Havlak、Json 的逐文件许可未在该文件列明。
  `bench/c/*.c` 为本仓库自写、无上游许可头。本目录引入的都是**本仓库的改写版**，
  按本仓库许可发布。

## 单一源的合并规则

14 个基准默认**每个只有一份源**，两态共用。合并按以下实测规则进行
（详见 `docs/plan/fat-vs-raw-bench-plan.md` §3）：

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
  报告的比值（按值 4.46×，按指针 4.69×），故在此留档。

- **`storage`（保留两份模式专源：`storage.an` + `storage.raw.an`）**：该基准的
  函数把分配结果作为 `Option<array_tree[]>` 返回。胖模式下可返回的切片必须是**堆
  owner**的（`let arr: array_tree[] = dyn[n] array_tree;` 直接返回）；若按合并配方
  在函数内构造局部视图再返回，视图的 owner 是**当前帧锁**，函数返回后帧锁槽被置
  `SENTINEL`，返回的切片即失效。裸模式没有帧锁机制，只能用 `arr[0u64..n]` 形式。
  两模式没有既合法又等价的共同写法，故按 §3.4 规则 3 保留两份专源（各自取该模式的
  分支原形式）。

- **`sieve`（单一源，胖态 +6%）**：合并源用范围视图 `flags[0u64..SIEVE_SIZE]` 同时
  承担填充与传参，是单源下的最优形式：填充走视图比走 `dyn` 变量（`Index` trait 派发）
  快约 9%。但视图 owner 是帧锁，胖态下填充循环里的 `live` 检查（`load lock; icmp key`）
  无法被优化器外提，而同机分支胖源（`let flags: bool[] = dyn[...]`，堆 owner）可以外提，
  因此合并源胖态仍比分支源慢约 6%（约 3.17 s vs 2.98 s）。该差异只影响胖态绝对值、
  不改变算法，已在此记录（判定数据见 `docs/plan/fat-vs-raw-bench-plan.md` §3.4）。

## 与分支胖源的性能对照

合并源是**第三种程序**（分支的胖源与裸源各是一版），因此与论文所用的分支胖源之间会有
小幅性能差异。同机绑核（`taskset -c 4`）两态逐次交替、各 5 次取最小值：

| 基准 | 分支胖源 (s) | 合并源 (s) | Δ |
| --- | ---: | ---: | ---: |
| bounce | 2.879 | 3.005 | +4.4% |
| fasta | 2.346 | 2.219 | −5.4% |
| nbody | 10.506 | 10.455 | −0.5% |
| permute | 3.353 | 3.366 | +0.4% |
| queen | 7.439 | 7.451 | +0.2% |
| sieve | 2.930 | 3.125 | +6.7% |
| spectralnorm | 2.063 | 2.064 | +0.0% |
| storage（**同一二进制**的对照行） | 2.172 | 1.840 | −15.3% |

- bounce / sieve 的 +4%～+7% 同因：合并源只能用**帧锁 owner** 的视图访问数组，
  胖态下 `live` 检查无法外提（分支胖源用 `let x: T[] = dyn[...]` 拿到堆 owner）。
- fasta 的 −5.4% 来自合并源用切片视图替代分支胖源的 `&iub[0]` 指针传参形式。
- storage 两侧是**逐字节相同的二进制**（`cmp` 相同），差值纯粹是**路径/进程布局**
  造成的测量假象（±15%～30%，分配密集型基准对 argv 长度→栈/mmap 布局尤其敏感）。
  这条对照行是协议的一部分：它说明为什么两态产物必须**同目录、等长文件名**
  （`<name>.fat` / `<name>.raw`），也说明分配密集型基准的绝对值只在同一路径口径内可比。
- 论文里的胖态绝对值不能与本表直接对照（LLVM 15 → 22 换代，加上上述口径差异）。

## 不包含的内容

- `nocheck` 对照态：编译器已无该开关；
- C/C++/Rust 参考基线计时、ASan 交叉对比、三态成本分解表：属于分支的论文实验口径，
  与 fat/raw 两态对照无关；
- `bak/` 下的历史基准与 `docs/` 下的历史结果表。
