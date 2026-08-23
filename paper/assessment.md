# SecL 论文材料评估（2026-08-24 重测版）

## 结论

当前材料已足以支撑以完整语言实现为对象的安全会议论文：实现、威胁模型、三级胖指针语义、
O-1--O-5 完整纸面证明、38 个真实 CVE 成对实验、14 个 benchmark 的三态成本分解和 ASan
端到端对照均已闭合。论文不得继续使用早期“证明梗概”、8B 块头、立即 `free` 或旧性能数字。

## 实现事实

- 流水线：`.an -> Tokens -> AST -> HIR -> CFG IR -> LLVM IR -> native`。
- 表示：`T*` 40B、`T[]/str` 32B、`T&` 24B。
- 堆生命周期：固定 32B 块头的单线程 first-fit 池；释放写 `SENTINEL` 后留池；复用写新键。
- 键：仅单调计数器；堆键排除全 1 哨兵；63 位空间耗尽时确定性 trap。
- 引用：空间安全由构造点来源不变量建立，而非把 `in_bounds` 定义成恒真。
- 模式：check 与 nocheck 使用相同表示、堆池和帧锁；raw 使用裸指针及 libc malloc/free。

## 正确性验证

2026-08-24 在提交 `afd67de` 后的现场结果：

| 套件 | 结果 |
| --- | ---: |
| general | 241/241 |
| fat | 83/83 |
| raw 三态 | 27/27 |
| CVE | 76/76（38 vulnerable + 38 fixed） |
| 键边界脚本 | 通过 |

新增回归覆盖非零偏移指针转剩余切片、one-past 转引用、空切片转引用、释放与复用后的 UAF/
双重释放、容量不等块复用、逻辑边界以及键/哨兵边界。

## 三态性能结果

权威数据为 `data/performance.csv`，完整样本为 `data/shootout-results.md`。协议为 CPU 4 绑核，
每态 1 次 warmup + 5 次正式样本；每个 benchmark 内 check→nocheck→raw 紧邻执行。

| 口径 | 14 项几何平均 | 范围 | ≤1.2× |
| --- | ---: | ---: | ---: |
| 表示/池/锁成本 `nocheck/raw` | 1.13× | 0.67--2.85× | 9/14 |
| 检查发射成本 `check/nocheck` | 1.13× | 0.96--1.63× | 10/14 |
| 完整机制 `check/raw` | 1.27× | 0.81--3.79× | 8/14 |

最差总时间倍率为 `list` 3.79×，其中表示/池成本 2.85×、检查成本 1.33×；`queen`、
`revcomp` 和 `towers` 分别为 1.79×、1.64× 和 1.58×。小于 1 的倍率只应解释为代码生成、
源码适配和测量噪声的组合，不能声称安全机制带来加速。

RSS 的主要异常是 `binarytree` +256.08MB 和 `storage` +2189.53MB。前者受树节点表示和不同
分配路径影响；后者直接暴露 40B 子指针表示、块头以及池驻留成本。其他基准的 RSS 差异很小。

## ASan 对照

`data/asan-results.md` 冻结 14 个 benchmark 的 43 条有效腿，每腿 5 个样本：

- C ASan / C plain 几何平均 1.59×；
- C ASan / SecL check 几何平均 0.73×；
- SecL check / C plain 几何平均 2.19×。

这些是端到端跨编译器结果：C plain/ASan 用 clang `-O2`，SecL 用自身前端和 LLVM/clang
`-O3`，源代码表示也不同。因此只有第一项可近似解释为 ASan 在同一 C 程序上的插桩成本；
后两项不得全部归因于检查机制。

`binarytree` 在默认 quarantine 下的 ASan RSS 为 650.0MB；关闭 quarantine 后为 168.9MB，
下降 74.0%，时间下降 7.2%。SecL 稳定池和 ASan quarantine 解决的问题与释放策略不同，不应
用单一 RSS 数字推导覆盖能力。

## CVE 证据

`tests/fat_cve/` 包含 38 个 CVE，每个有 vulnerable 与 fixed 版本。主机制归属为：

- `in_bounds`：25；
- `live`：10；
- `is_heap`：1；
- 帧 re-key：2。

正文优先使用类别/机制汇总；38×12 完整矩阵放附录或复现材料，避免正文密集不可读。

## 论文必须披露的边界

- 单线程模型与非并发堆池；
- FFI/ABI 不透明且胖指针改变调用约定；
- 标准库、编译器、LLVM 和堆池属于 TCB；
- 元数据防伪依赖攻击者尚无任意元数据写能力；
- 栈失效粒度为函数帧，不覆盖同帧词法内层作用域退出；
- 堆池进程期驻留、first-fit 线性查找和内部碎片；
- benchmark 中部分规模被下调，且 raw 源使用显式 `from_raw_parts` 适配；
- 证明是完整纸面证明，但尚未机械化。

## 权威材料

| 声明 | 来源 |
| --- | --- |
| 形式语义与 O-1--O-5 | `docs/security.md` |
| 编译器/运行时映射 | `docs/security-code.md` |
| 三态数据 | `paper/data/performance.csv`, `paper/data/shootout-results.md` |
| ASan 数据 | `paper/data/asan-results.md` |
| CVE 归属 | `tests/fat_cve/docs/MECHANISMS.md` |
| 已核实引用 | `paper/references/citations.md` |
