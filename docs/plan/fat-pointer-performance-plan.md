# 胖指针性能优化计划

> **临时材料**:本文件记录尚未确定的工作方向,不属于仓库文档,不描述当前实现,
> 也不得被其他文档、代码注释或提交信息引用。仓库当前状态的权威描述见 `docs/security.md`、
> `docs/grammar/` 与 `docs/manual/`。

## 1. 目标与范围

### 1.1 目标

胖指针（安全模式）机制的正确性验证已告一段落，本阶段解决它的**运行开销与内存占用**：给出可复现的
度量，按收益/风险依次消除主要开销。安全语义不变——任何优化都必须保持 `docs/security.md` 描述的检查
语义，`tests/safety/` 与两个指针模式下的三套件必须保持全绿。

已经落地的机制（相邻检查合并与去重、恒真时序项消解与失败分支冷权重、堆池与块头、32 位
`index`/`size` 与统一锁表）不在这里保留方案文本，数据在 `bench/results/` 与提交历史里。剩下的工作：

- **循环不变量外提**（配循环携带 guard）——剩余收益最大的机制项；
- **堆指针的逃逸/过程间分析**——消解指针追逐上的 `live`；
- **归纳变量可证的边界检查消解**——优先级低（空间检查只占 4–13%）；
- **代码规模与编译期度量**；**`-O0` 复测**。

表示（布局）类方向已关闭：16 B 打包字、8 B 字两变体、按标量传参的调用约定都已实测否决，更小的
指针（地址反查）与末端地址在今天的布局下已无尺寸收益，不再列为计划。

### 1.2 第一版不做

- 不改变语言语义与 ABI 承诺（`T&` 在两种表示下的关系、one-past 规则、释放协议）；
- 不引入并发/多线程支持（当前模型假设单线程，见 `security.md` §10）；
- 不做跨过程优化；唯一的例外是消解堆指针 `live` 所需的逃逸/过程间分析（§4A），其余工作都在机制层与局部；
- 不把安全检查改为"可选关闭"：raw 模式已经存在，安全模式不做逐项开关；
- 不设性能回归门槛：性能试验只作参考（见 §3.3）。

## 2. 现状

### 2.1 机制构成

| 组成 | 位置 | 现状 |
| --- | --- | --- |
| 胖指针表示 | `compiler/codegen/llvm/types.py`、`builder.py::__build_fat` | 指针 4 字段 ⟨data, word, index:u32, size:u32⟩ **24 B**；切片/字符串 3 字段 ⟨data, word, size⟩ **24 B**；引用 2 字段 ⟨data, word⟩ **16 B**；raw 模式 8 B；指针在 LLVM IR 里是 opaque pointer |
| 机制常量 | `compiler/codegen/cfg/lockmech.py` | `word = ⟨key:32 \| lock:32⟩`、`SENTINEL`、`BlockHeader` **8 B**（`extent@0`、`pad@4`）、全局锁表 `LockEntry`（`{key:u32@0, anchor_lo32@4}`；帧段 2^20 槽 + 字面量/环境常量槽 + 堆段）、`FrameLockArena` 影子栈 2^20 槽 × 8 B |
| 检查插入 | `compiler/codegen/cfg/passes/insert_checks.py` + `compiler/codegen/cfg/lower/`（`IR.Check*` 节点族） | `CheckSafeAccess`、`CheckViewAccess`、`CheckInBounds`、`CheckElementArith`、`CheckElementAccess`（合取）、`CheckSliceNonEmpty`、`CheckRefAccess`、`CheckRawBounds`、`CheckPtrDiff`、`CheckPtrCmp`、`CheckDelete` |
| 检查去重与合并 | 同文件 | `(base, offset)` 二元检查去重；相邻的良构 + `in_bounds` + `live` 三重检查合并为单个 `CheckElementAccess`；派生链失效时补发挂起义务 |
| 时序检查消解 | 同文件 | 帧内指针（锁字段取自当前函数帧锁槽：取址、帧内 `Alloca` 及其派生与 `T[]→T&` 退化）的 `live` 恒真，访问点只发空间项（`CheckSafeAccess(live=False)`，仍报 S002），`T&`/视图的 live-only 检查整个省掉（仍 S003）；同一出处（`__fat_root`）的指针共享 lock/key，同块内只检查一次 |
| 检查发射 | `compiler/codegen/llvm/builder.py::__emit_check` | 每个检查**分裂基本块**：条件分支到 `ok`/`fail`，该分支带 `!prof` 权重（2000:1）把 fail 块移出热路径直线；`fail` 调运行时库的 `__yian_runtime_fail(ptr, len)`（`cold noreturn`）后 `unreachable`。消息表只在编译器侧（`compiler/runtime_error.py`） |
| 堆池 | `runtime/src/alloc.c`（C 运行时库） | 单线程尺寸类 arena：36 档 16 B…49152 B（约 1.25×）+ 大对象 chunk；64 KiB slab（1 MiB 成批映射后切成区域）、类内空闲链 + 正在填充 slab 的 bump 游标；大对象 8 MiB 缓存、超额度用 `madvise(MADV_DONTNEED)` 退页、从不 `munmap` 用户块。编译器只发外部声明，尺寸编译期已知时直接传类号 |
| 帧锁 | `builder.py::acquire_frame_lock`、`lockmech.py::FrameLockArena` | 每帧一个活动锁槽，进入 re-key、返回写 `SENTINEL`；槽位在进程生命期内固定 |

### 2.2 实测基线

基线取 `bench/results/full.csv`（由 `bench/bench_three_way.py --pin 4` 生成，表头记录 commit 与
工具链指纹；19 个 shootout 基准三态轮转、各 5 次取最小）。下表是计划写就时的代表性读数：

| 负载形态 | 代表基准 | raw (ms) | fat (ms) | fat/raw | fat 峰值 RSS |
| --- | --- | ---: | ---: | ---: | ---: |
| 同尺寸分配密集 | binarytree | 3302.2 | 2928.2 | **0.89** | 321.6 MB |
| 整数/浮点为主 | mand / fann / spectralnorm | — | — | 0.98–1.00 | ≤1.3 MB |
| 大活集合 | storage | 1121.4 | 1749.8 | 1.56 | 4394.5 MB |
| 数组/视图算术 | permute / towers | 1477.7 / 2034.6 | 2427.9 / 4427.6 | 1.64 / 2.18 | 1.1 MB |
| 混合尺寸长跑 | havlak | 67.9 | 161.4 | 2.38 | 9.6 MB |
| 大量小对象 + 视图构造 | json | 669.6 | 1701.9 | 2.54 | 2.2 MB |
| 对象/分支密集 | deltablue / richards | 629.3 / 673.4 | 1626.7 / 1844.1 | 2.58 / 2.74 | 1.3 / 5.0 MB |
| 链表 + 频繁视图 | list | 6753.1 | 30329.8 | 4.49 | 1.1 MB |
| 分支/对象密集 | queen | 1434.1 | 6873.1 | 4.79 | 2.8 MB |

全部 19 项：fat/raw 几何平均 **1.63**、中位 1.33、最差 4.79；fat 峰值 RSS 全部 ≤ raw（storage 4394.5 MB
是活集合本身）。同一份代码两次全量跑的逐项差可达 ±2%（`fasta`/`cd`/`storage` 最明显），所以单项变化
小于这个量级时只当作噪声。

分配器专项（`bench/bench_allocator.py --runs 7 --pin 4`，fat 最小 / raw 最小）：

| 基准 | fat | raw | fat 峰值 RSS |
| --- | ---: | ---: | ---: |
| churn_mixed（混合尺寸周转） | 19.0 ms | 56.9 ms | 1.1 MB |
| churn_single（4M 次 16 B 周转） | 16.0 ms | 45.9 ms | 1.3 MB |
| grow_free（40 轮 × 4096 个 4 KiB） | 6.9 ms | 133.4 ms | 17.3 MB |
| grow_varied（逐轮变大 1…32 MiB） | 117.1 ms | 110.0 ms | 64.2 MB |

三条读数：

1. **分配/释放路径不再是开销来源**：arena 的同尺寸周转比 libc `malloc`/`free` 快（churn_single
   16.0 vs 45.9 ms），binarytree 这类 3 亿次分配/释放的负载比 raw 快 11%。旧表里 "`-O0` 3.5×、
   `-O2` 与 raw 持平" 的池开销结论已经不成立。
2. **逐次访问的检查仍是主要开销，但时序项已经消解掉一部分**：帧内指针与同出处指针的 `live` 已经
   不再发射，因此 fat/raw 最差的几项仍是"小对象 + 指针追逐 + 频繁指针↔切片↔引用转换"的形态
   （queen 4.79、list 4.49、richards 2.74、deltablue 2.58、json 2.54）。
3. **RSS 与活集合绑定**：storage 的 4.4 GB 是全部对象同时存活，任何分配器都改不动；分配器退页只对
   大对象生效（grow_varied 峰值 64 MB），小对象 slab 页常驻（见 §6 的取舍）。

### 2.3 当前缺口

- **循环不变量外提未做**：`live` 目前只在"恒真"时被消掉（帧内指针、同出处去重）；把循环内反复执行
  的时序检查提升到循环外，需要"循环携带 guard"（只在首次访问时检查）才能保持"检查只在访问点发生"
  的语义，否则循环体一次都没访问也会报错。
- **堆指针的逃逸/过程间分析未做**：从内存读出的指针（指针追逐）无法在局部证明目标块没被释放，是
  剩余 `live` 开销的大头（上限测量见 §4A）。
- **空间检查的归纳变量消解未做**：`for i in 0..N` 内的 `p[i]` 仍逐次比较 `i < size`（CFG 层没有
  归纳变量分析；`-O2` 下靠 LLVM 折叠一部分）。空间检查实测只占 4–13%，优先级低于上面两项。
- **`-O0` 未复测**：改动后没有重新测 `-O0`，不知道消解在低优化级下的收益。
- **代码规模无数据**：检查带来的基本块数量、`-t ll` 行数、目标文件体积与 `-O2` 编译时间没有记录。
- **小对象 slab 页不回收**：长跑程序在峰值后不会把空闲 slab 的物理页还给内核（延迟回收的代价已测，
  见 §6）。
- **帧锁影子栈未观测**：2^20 槽的页触达与"同时活动的取址帧数"上限在真实程序里的表现没有测量。
- **视图转换开销无独立读数**：`ptr()`/`as_slice()`/`T[] → T&` 往返的开销混在 shootout 里；这一项不改变布局，将来若要做代码形状优化，先补一个专项基准。

## 3. 度量方法

### 3.1 基准与运行器

评测集的组织（按来源分目录、按侧重分集合、快速/完全两档）、三态协议与运行命令见 `bench/README.md`；
来源与许可登记、候选套件的调研结论见 `bench/SUITES.md`；后续引入顺序见 `docs/proposal.md` 的
"评测集与性能度量"。

- `bench/<SOURCE>/an/*.an` + `bench/<SOURCE>/c/*.c`（SOURCE ∈ {AWFY, BG}）：19 个基准的 YIAN 源与
  同算法同规模的 C 参考，每项另有 `bench/<SOURCE>/specs/<name>.json`（argv / 权威校验 / tag / 规模档）；
  `bench/bench_three_way.py --set full|fast|ptr|numeric|string` 逐次轮转测三态，命名集合写
  `bench/results/<set>.{md,csv}`（含环境指纹与 commit），`--bench/--source` 的临时子集写
  `bench/results/partial.{md,csv}`，不覆盖集合结果。
- `bench/ALLOC/an/*.an` + `bench/bench_allocator.py`：分配器四类负载（同尺寸 churn、混合尺寸 churn、
  增长-释放、变尺寸增长-释放），fat 与 raw 两态，裸态用 `<name>.raw.an` 覆盖源。
- `bench/fatptr/an/*.an` + `bench/fatptr/c/*.c` + `bench/fatptr/specs/*.json`：三件机制微基准
  （`copy_struct` 拷贝/传参、`call_abi` 每次调用的检查开销、`chase` 节点尺寸→cache 足迹），
  用 `--set micro` 跑，只读量级。
- 语义护栏：C 态 warmup 必须满足脚本内记录的权威 `(退出码, stdout)`；raw 与 fat 的 warmup 输出必须
  逐字节一致；退出码必须为 0。

### 3.2 指标与协议

墙钟（`-O2`、`taskset -c 4` 绑核、min-of-N）、峰值 RSS（`/usr/bin/time -v` 的
`Maximum resident set size`）、fat/raw 与 fat/C 比值、stdout 一致性。机器噪声在这台机器上可达 ±10%
（同一二进制的 min 与中位能差 20%），因此结论只在"多次轮转 + 取最小"的口径下比较，并优先看
min 与中位是否同向。

### 3.3 不设门槛

性能试验只作参考，不写回归阈值脚本：改动后重跑 `bench_three_way.py` 与 `bench_allocator.py`，看
`results.csv` 的 min/中位与 RSS 是否掉出噪声即可。安全门槛不变：`tests/safety/` 与 basic/package
两套件在两种指针模式下全绿是每次改动的硬门槛；运行时改动另需 `python3 runtime/build.py --check --asan`。

## 4. 余下的优化方向

### A. 检查的循环外提与堆指针消解

- **循环不变量外提（配循环携带 guard）**：把循环内反复执行的 `live` 提到循环外，必须配"循环携带
  guard"——首次访问时检查一次、之后复用，这样"循环体一次都没访问"的程序仍然不会报错；朴素的
  preheader 外提会改变失败时机。
- **让 LLVM 自己提升不行**：负载 store 与锁槽 load 都是 opaque pointer 且可能别名，LICM 无法证明
  无别名；用 TBAA 区分锁槽与负载可以补上别名信息，但锁槽 load 的"可解引用性"证明仍不成立。
- **堆指针的逃逸/过程间分析**：从内存读出的指针（指针追逐）无法在局部证明目标块没被释放，是剩余
  `live` 开销的大头。
- **归纳变量可证的边界检查消解**（低优先级）：`for i in 0..N` 内的 `p[i]` 仍逐次比较 `i < size`
  （CFG 层没有归纳变量分析；`-O2` 下靠 LLVM 折叠一部分）。

**上限参考**（临时变体，只用于量收益，已还原）：单独去掉 `live` 项的墙钟收益是 list −34%、
deltablue/richards −25%、havlak −23%、json −17%、storage −13%、queen/binarytree −9%。
已落地的消解吃掉了其中一部分，剩下的集中在堆指针一项上。

### B. 编译期与生成代码

- 记录检查产生的**基本块数量**（大函数块数、`-t ll` 行数、目标文件体积）与 `-O2` 编译时间随检查数
  的增长，避免检查把优化器分析成本推高。
- 失败路径与消息表可以合并（同一 `RuntimeErrorCode` 共享失败块或共享消息指针）。

### C. 帧锁的观测项

- **帧锁影子栈未观测**：评估槽位复用的 LIFO 语义在深递归下的页触达，并测"同时活动的取址帧数"上限。

## 5. 余下的阶段与验收

### 检查消解（第二轮）

**工作**：循环不变量外提（配循环携带 guard）；堆指针的逃逸/过程间分析；归纳变量边界消解；`-O0` 复测。
**验收**：`-O0` 的遍历类负载比值明显下降（不依赖 `-O2`）；每次消解都给出"检查集合等价"的论证
（只去掉恒真项，错误码不变）；三套件两模式全绿，且 `bench/results/full.csv` 的 fat/raw 与 RSS 不退化。

### 收口

**待做**：代码规模数据（§4B）；把基准纳入日常检查流程（按 §3.3 只保留"改动后重跑一次"的惯例，
不设自动门槛）。
**验收**：文档与实测一致；基准脚本一条命令可复核。

## 6. 风险与开放问题

- **安全回退**是最大的风险：任何检查合并/提升都必须给出"检查集合等价"的论证，而不是靠测试通过。
  safety 套件 + 手工边界用例（one-past、内部指针释放、视图转换、整数回绕）是底线。
- **循环外提会改变失败时机**：把 `live` 提到循环外，等于对"循环体从未访问"的路径也做检查；必须用
  循环携带 guard（首次访问才检查）才能保持语义，这是实现时要盯住的点。
- **剩余 `live` 开销在堆指针上**：指针追逐负载每访问一个对象就检查一次它的锁槽，局部无法证明该块
  未被释放，只能靠逃逸/过程间分析。
- **小对象 slab 页常驻**：进程峰值后 RSS 不回落。这是当前的取舍（回收簿记实测让分配密集负载慢 20%
  以上）；若某个场景确实需要峰值后回落，按"`free` 上簿记 ≤ 噪声"与"峰值后 RSS 可回落"重新验收。
- **`-O0` 与 `-O2` 的结论可能相反**：消解在低优化级下的表现要单独测；当前只有 `-O2` 基线。
- **帧锁粒度**是整个函数帧（`security.md` §7 已说明是设计限制），细化为词法作用域是语义改动，不在本阶段。
- **单线程假设**：arena 与影子栈都不加锁；引入并发需要重新设计，本阶段只保证不把这条路堵死。
- **键与槽位耗尽**：帧键 32 位不回绕、堆表项键按换代 +1、锁表槽位 2^26，耗尽都是确定性终止（R003）；
  长跑程序的消耗速率需要在基准里观察。

## 7. 参考

- 机制语义：[`docs/security.md`](../security.md)（三形态、锁与键、空间检查、堆/栈保护、可信边界）
- 机制常量与布局：`compiler/codegen/cfg/lockmech.py`（`BlockHeader`、`LockEntry`、`FrameLockArena`、字段下标与谓词）
- 检查插入、合并与发射：`compiler/codegen/cfg/passes/insert_checks.py`（物化）、`compiler/codegen/cfg/lower/`（下降）、`compiler/codegen/cfg/ir.py`、
  `compiler/codegen/llvm/builder.py`
- 运行时库：`runtime/include/yian_rt.h`、`runtime/src/alloc.c`、`runtime/build.py`、
  `docs/compile_script.md` §5
- 运行期错误码与消息：`compiler/runtime_error.py`
- 基准与基线：`bench/bench_three_way.py`、`bench/bench_allocator.py`、`bench/{AWFY,BG,ALLOC}/{an,c,specs}/`、
  `bench/sets/`、`bench/results/{full,alloc}.{md,csv}`、`bench/README.md`、`bench/SUITES.md`
- 回归语料：`tests/safety/`、`tests/basic/std/tiered_*`
