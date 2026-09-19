# 胖指针性能优化计划

## 1. 目标与范围

### 1.1 目标

胖指针（安全模式）机制的正确性验证已告一段落，本阶段解决它的**运行开销与内存占用**：给出可复现的
度量，按收益/风险依次消除主要开销。安全语义不变——任何优化都必须保持
`docs/security.md` 描述的检查语义，`tests/safety/` 与两个指针模式下的三套件必须保持全绿。

已经落地的两项（检查合并、堆池与块头）见 §5 的 P1/P3；本计划剩下的主要工作是**表示的 ABI 评估**、
**检查提升**与**代码规模度量**。

### 1.2 第一版不做

- 不改变语言语义与 ABI 承诺（`T&` 在两种表示下的关系、one-past 规则、释放协议）；
- 不引入并发/多线程支持（当前模型假设单线程，见 `security.md` §10）；
- 不做编译期逃逸分析之外的跨过程优化（本阶段只做机制层与局部优化）；
- 不把安全检查改为"可选关闭"：raw 模式已经存在，安全模式不做逐项开关；
- 不设性能回归门槛：性能试验只作参考（见 §3.3）。

## 2. 现状

### 2.1 机制构成

| 组成 | 位置 | 现状 |
| --- | --- | --- |
| 胖指针表示 | `compiler/codegen/llvm/types.py`、`builder.py::__build_fat` | 指针 5 字段 ⟨data, lock, key, index, size⟩ **40 B**；切片/字符串 4 字段 **32 B**；引用 3 字段 **24 B**；raw 模式 8 B；指针在 LLVM IR 里是 opaque pointer |
| 机制常量 | `compiler/codegen/cfg/lockmech.py` | `KeyGen`（堆键最高位 1、栈键 0，单调 63 位）、`SENTINEL`、`BlockHeader` **16 B**（`lock@0`、`active_size@8`）、`FrameLock` + 影子栈 2^20 槽 × 8 B |
| 检查插入 | `compiler/codegen/cfg/builder.py`（22 处 `IR.Check*`） | `CheckSafeAccess`、`CheckViewAccess`、`CheckInBounds`、`CheckElementArith`、`CheckElementAccess`（合取）、`CheckSliceNonEmpty`、`CheckRefAccess`、`CheckRawBounds`、`CheckPtrDiff`、`CheckPtrCmp`、`CheckDelete` |
| 检查去重与合并 | 同文件 | `(base, offset)` 二元检查去重；相邻的良构 + `in_bounds` + `live` 三重检查合并为单个 `CheckElementAccess`；派生链失效时补发挂起义务 |
| 检查发射 | `compiler/codegen/llvm/builder.py::__emit_check` | 每个检查**分裂基本块**：条件分支到 `ok`/`fail`；`fail` 调运行时库的 `__yian_runtime_fail(ptr, len)`（`cold noreturn`）后 `unreachable`。消息表只在编译器侧（`compiler/runtime_error.py`） |
| 堆池 | `runtime/src/alloc.c`（C 运行时库） | 单线程尺寸类 arena：36 档 16 B…49152 B（约 1.25×）+ 大对象 chunk；64 KiB slab（1 MiB 成批映射后切成区域）、类内空闲链 + 正在填充 slab 的 bump 游标；大对象 8 MiB 缓存、超额度用 `madvise(MADV_DONTNEED)` 退页、从不 `munmap` 用户块。编译器只发外部声明，尺寸编译期已知时直接传类号 |
| 帧锁 | `builder.py::acquire_frame_lock`、`lockmech.py::FrameLockArena` | 每帧一个活动锁槽，进入 re-key、返回写 `SENTINEL`；槽位在进程生命期内固定 |

### 2.2 实测基线

当前基线是 `bench/results.csv`（由 `scripts/bench_three_way.py --pin 4` 生成，表头记录 commit 与
工具链指纹；19 个 shootout 基准三态轮转、各 5 次取最小）：

| 负载形态 | 代表基准 | raw (ms) | fat (ms) | fat/raw | fat 峰值 RSS |
| --- | --- | ---: | ---: | ---: | ---: |
| 同尺寸分配密集 | binarytree | 3303.6 | 3125.2 | **0.95** | 321.6 MB |
| 整数/浮点为主 | mand / fann / spectralnorm | — | — | 1.00 | ≤1.3 MB |
| 混合尺寸长跑 | havlak | 75.6 | 175.8 | 2.33 | 9.6 MB |
| 大量小对象 + 视图构造 | json | 680.2 | 1759.4 | 2.59 | 2.2 MB |
| 链表 + 频繁视图 | list | 6839.7 | 30906.5 | 4.52 | 1.1 MB |
| 分支/对象密集 | queen | 1449.1 | 6976.9 | 4.81 | 2.8 MB |
| 大活集合 | storage | 1193.8 | 1766.2 | 1.48 | 4394.5 MB |

全部 19 项：fat/raw 几何平均 **1.64**、中位 1.30、最差 4.81；fat 峰值 RSS 全部 ≤ raw（storage 4394.5 MB
是活集合本身）。

分配器专项（`scripts/bench_allocator.py --runs 7 --pin 4`，fat 最小 / raw 最小）：

| 基准 | fat | raw | fat 峰值 RSS |
| --- | ---: | ---: | ---: |
| churn_mixed（混合尺寸周转） | 19.9 ms | 57.0 ms | 1.1 MB |
| churn_single（4M 次 16 B 周转） | 16.5 ms | 45.3 ms | 1.3 MB |
| grow_free（40 轮 × 4096 个 4 KiB） | 6.5 ms | 128.6 ms | 17.3 MB |
| grow_varied（逐轮变大 1…32 MiB） | 115.0 ms | 108.6 ms | 64.2 MB |

三条读数：

1. **分配/释放路径不再是开销来源**：arena 的同尺寸周转比 libc `malloc`/`free` 快（churn_single
   16.5 vs 45.3 ms），binarytree 这类 3 亿次分配/释放的负载反而比 raw 快 5%。旧表里 `-O0` 3.5× 的
   池开销已经不成立。
2. **剩余开销集中在逐次访问的检查、40/32 B 胖值的搬运与视图构造**：fat/raw 最差的几项（queen、
   list、richards、json、deltablue）都是"小对象 + 高频访问 + 频繁指针↔切片↔引用转换"的形态。
3. **RSS 与活集合绑定**：storage 的 4.4 GB 是全部对象同时存活，任何分配器都改不动；分配器退页只对
   大对象生效（grow_varied 峰值 64 MB），小对象 slab 页常驻（见 §6 的取舍）。

### 2.3 当前缺口

- **检查提升未做**：时序检查（锁/键比较）在 `-O2` 下靠 LLVM 提升为循环不变量；`-O0` 下每次访问
  仍走完整的 `live ∧ in_bounds`。改动后没有复测 `-O0`，也没有"CFG 层就消掉检查"的实现。
- **表示与 ABI 未动**：40/32/24 B 的表示、按值传参走内存、`index`/`size` 的 64 位宽度都保持原样。
- **代码规模无数据**：检查带来的基本块数量、`-t ll` 行数、目标文件体积与 `-O2` 编译时间没有记录。
- **小对象 slab 页不回收**：长跑程序在峰值后不会把空闲 slab 的物理页还给内核（延迟回收的代价已测，
  见 §6）。
- **帧锁影子栈未观测**：2^20 槽的页触达与"同时活动的取址帧数"上限在真实程序里的表现没有测量。
- **视图转换开销无专项基准**：`ptr()`/`as_slice()`/`T[] → T&` 往返的开销混在 shootout 里，没有独立读数。

## 3. 度量方法

### 3.1 已交付的基准与运行器

- `bench/shootout/*.an` + `bench/c/*.c`：19 个基准的 YIAN 源与同算法同规模的 C 参考；
  `scripts/bench_three_way.py` 逐次轮转测三态，写 `bench/results.md`（人读）与 `bench/results.csv`
  （机读，含环境指纹与 commit）；`--names` 的部分测量写 `results.partial.*`，不覆盖全量基线。
- `bench/alloc/*.an` + `scripts/bench_allocator.py`：分配器四类负载（同尺寸 churn、混合尺寸 churn、
  增长-释放、变尺寸增长-释放），fat 与 raw 两态，裸态用 `<name>.raw.an` 覆盖源。
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

## 4. 优化方向

### A. 检查的合并、提升与冷路径

- **合并相邻检查：已做**。相邻派生链上的良构 + `in_bounds` + `live` 合并为单个 `CheckElementAccess`，
  `(base, offset)` 二元检查去重，派生链失效时补发挂起义务（`cfg/builder.py`）。
- **提升与消解：待做**。把"同一指针在循环内的重复时序检查"提升为循环不变量、把归纳变量可证的
  边界检查在 CFG 层消掉（`-O0` 也能受益），而不是留给 LLVM。
- **冷路径：部分**。失败块已经是 `cold noreturn` 的运行时调用，但仍逐检查分裂基本块；评估
  `-O0` 下减少块分裂的形态（例如检查通过时直接落到同一块的下一句）。

### B. 指针表示与 ABI（未动，候选中预期收益最高）

- **从 40 B 收缩**：`size` 与 `index` 相关（剩余长度 = 块头 `active_size` − index），评估"指针只带
  index、剩余长度按需从块头读"在"指针拷贝密集"与"边界检查密集"两类负载上的取舍。
- **位宽压缩**：`index`/`size` 是否可用 32 位（对象上限需单独定义）；key 与 index 打包进一个字。
- **传参 ABI**：40 B 聚合按值传参走内存；评估降为 2 个寄存器可传的值（如 ⟨data, 句柄⟩）。
- 任何表示改动都要同步 `lockmech.py` 的字段下标与 `docs/security.md`。

### C. 堆池与锁槽

- **分配器：已做**。尺寸类 arena + 16 B 块头 + 大对象缓存与退页 + 成批映射 slab（§2.1）。实测：
  havlak 1752 → 176 ms（旧池首适配的病态）、grow_varied 峰值 RSS 529 → 64 MB、storage 系统调用
  14 万 → 4 千、19 项 shootout 峰值 RSS 全部不升。
- **小对象 slab 页回收：未做**，保留为待决策项。加回回收要在 `free` 上做 slab 存活计数，并处理
  "整块变空后从类空闲链里摘除"的代价；带这类簿记的版本实测让 churn_single 慢 20%、binarytree 慢
  一倍，因此当时选择让 slab 页常驻。若某个场景确实需要"小对象峰值后掉 RSS"，再按延迟回收重做。
- **帧锁影子栈：未观测**。评估槽位复用的 LIFO 语义在深递归下的页触达，并测"同时活动的取址帧数"上限。

### D. 编译期与生成代码

- 记录检查产生的**基本块数量**（大函数块数、`-t ll` 行数、目标文件体积）与 `-O2` 编译时间随检查数
  的增长，避免检查把优化器分析成本推高。
- 失败路径与消息表可以合并（同一 `RuntimeErrorCode` 共享失败块或共享消息指针）。

## 5. 阶段与验收

### P0：基准集与基线 —— 已完成

**实际交付**：`bench/shootout/` + `bench/c/` + `scripts/bench_three_way.py`（三态、绑核、min-of-N、
RSS、stdout 护栏、`-t ll` 不参与）、`bench/alloc/` + `scripts/bench_allocator.py`（分配器四类负载），
基线固化在 `bench/results.{md,csv}`（记录 commit 与工具链指纹）。没有 JSON 中间产物，也不设门槛
（§3.3）——机器可读的部分由 CSV 承担。

**验收**：一条命令产出完整报告；换机器/改代码后能复现可比数字；三套件两模式全绿。均已满足。

### P1：检查的合并与提升 —— 部分完成

**已做**：相邻检查合并 + 二元检查去重 + 义务补发。
**待做**：循环不变量提升与归纳变量消解；`-O0` 复测。
**验收（更新）**：`-O0` 的 traversal 类负载比值明显下降（不依赖 `-O2`）；`-O2` 与 shootout 基线不退化；
三套件全绿；失败语义逐条对照 `security.md` §5 复核。

### P2：表示与 ABI 评估 —— 待做

**工作内容**：方向 B。先做测量型原型（例如 "size 从块头读"），用基准判断"指针拷贝密集"（shootout 的
list/json/richards）与"边界检查密集"（queen/deltablue）两类负载的取舍；再决定是否收缩表示、压缩位宽。
**验收**：给出取舍结论与数据；若采纳，两种模式三套件全绿，且 `bench/results.csv` 的 fat/raw 与 RSS
不退化。

### P3：池与锁槽 —— 已完成（回收策略留待决策）

**已做**：尺寸类 arena、16 B 块头、大对象退页、成批映射 slab（§4C）。
**待决策**：小对象 slab 页回收；帧锁影子栈的页触达观测。
**验收**：混合尺寸 churn 稳态 RSS 有明确上界（grow_varied 64 MB、churn 1.1–1.3 MB）；长跑 RSS 平稳；
单尺寸 churn 不退化（churn_single 16.5 ms，快于 raw 的 45.3 ms）。已满足；回收策略若要做，按
"`free` 上簿记 ≤ 噪声" 与 "峰值后 RSS 可回落" 两个指标单独验收。

### P4：收口 —— 部分完成

**已做**：块头与堆池描述同步进 `docs/security.md` §6 与 `docs/compile_script.md` §5
（运行时库的构建、链接、符号表与尺寸类 ABI）；`docs/manual/12`、`14` 补运行时边界与链接说明。
**未做**：代码规模数据（方向 D）；把基准纳入日常检查流程（按 §3.3 只保留"改动后重跑一次"的惯例，
不设自动门槛）。
**验收**：文档与实测一致；基准脚本一条命令可复核。前者已满足，后者取决于方向 D 是否要做。

## 6. 风险与开放问题

- **安全回退**是最大的风险：任何检查合并/提升都必须给出"检查集合等价"的论证，而不是靠测试通过。
  safety 套件 + 手工边界用例（one-past、内部指针释放、视图转换、整数回绕）是底线。
- **小对象 slab 页常驻**：进程峰值后 RSS 不回落。这是有意取舍（回收簿记实测让分配密集负载慢 20% 以上），
  若改回回收，必须重新测 churn 与 binarytree。
- **`-O0` 与 `-O2` 结论可能相反**：表示收缩会减少拷贝但增加加载，需分别测量；当前只有 `-O2` 基线。
- **帧锁粒度**是整个函数帧（`security.md` §7 已说明是设计限制），细化为词法作用域是语义改动，不在本阶段。
- **单线程假设**：arena 与影子栈都不加锁；引入并发需要重新设计，本阶段只保证不把这条路堵死。
- **键与槽位耗尽**：当前是确定性终止（不回绕）。长时间运行程序的键消耗速率需要在基准里观察，
  确认 2^63 量级不会成为实际限制。
- **表示改动的 ABI 同步面**：`lockmech.py` 字段下标、`security.md`、`runtime/` 的块头常量与
  `runtime/build.py --check` 的断言必须一起改。

## 7. 参考

- 机制语义：[`docs/security.md`](../security.md)（三形态、锁与键、空间检查、堆/栈保护、可信边界）
- 机制常量与布局：`compiler/codegen/cfg/lockmech.py`（`BlockHeader`、`KeyGen`、`FrameLockArena`、字段下标）
- 检查插入、合并与发射：`compiler/codegen/cfg/builder.py`、`compiler/codegen/cfg/ir.py`、
  `compiler/codegen/llvm/builder.py`
- 运行时库：`runtime/include/yian_rt.h`、`runtime/src/alloc.c`、`runtime/build.py`、
  `docs/compile_script.md` §5
- 运行期错误码与消息：`compiler/runtime_error.py`
- 基准与基线：`scripts/bench_three_way.py`、`bench/shootout/`、`bench/c/`、`bench/results.{md,csv}`、
  `scripts/bench_allocator.py`、`bench/alloc/`
- 回归语料：`tests/safety/`、`tests/basic/std/tiered_*`
