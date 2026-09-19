# 评测集来源登记与候选套件调研

本文件登记仓库内评测集的**来源与许可**，并留档已调研但**尚未引入**的候选套件（含许可核实结论、
内容、与 YIAN 的相关性、引入工作量），供后续按需引入。评测集的组织方式与运行方式见
`bench/README.md`；重组与引入的分阶段安排见 `docs/plan/bench-suite-plan.md`。

引入的硬条件（YIAN 三态协议决定）：候选必须有**可用的 C 参考实现**（三态以 C 为权威值）、
**单线程**、规模适中（单次运行秒级）、许可与本仓库（Apache-2.0 OR MIT）兼容。

## 一、已在库内

| 来源 | 目录 | 成员 | 许可 |
| --- | --- | --- | --- |
| Are We Fast Yet（`smarr/are-we-fast-yet` 的 Java 微基准改写：Bounce/CD/DeltaBlue/Havlak/Json/List/Mandelbrot/NBody/Permute/Queens/Richards/Sieve/Storage/Towers） | `bench/AWFY/` | 14 | 混合：Benchmarks Game 部分为 Revised BSD；Richards/DeltaBlue 转自 Mario Wolczko 的 Smalltalk 源，上游 `LICENSE.md` 只给出其归档链接。每个源的头部注释都标注了上游来源与许可状态 |
| The Computer Language Benchmarks Game（binarytrees、fannkuch-redux、fasta、reverse-complement、spectral-norm） | `bench/BG/` | 5 | Revised BSD（`Copyright © 2004-2008 Brent Fulgham, 2005-2025 Isaac Gouy`） |
| 本仓库自写（分配器四类负载） | `bench/ALLOC/` | 4 | 本仓库许可 |
| 本仓库自写（C 参考） | `bench/AWFY/c/`、`bench/BG/c/` | 19 | 本仓库许可；与对应 `.an` 同算法同规模 |

## 二、候选套件（调研结论：许可已核实）

| 候选 | 内容 | 许可（已核实） | 相关性 | 结论 |
| --- | --- | --- | --- | --- |
| **plb2**（`attractivechaos/plb2`） | 每个语言 4 个程序：`matmul`、`nqueen`、`sudoku`、`bedcov`；C 参考齐全 | **CC0-1.0**（公共领域奉献） | 补"数值 + 回溯搜索 + 哈希/区间覆盖"侧重；体量小、单线程 | **建议引入 matmul/nqueen/sudoku**；bedcov 需要文件 I/O，后置 |
| **Benchmarks Game 其余 5 项** | k-nucleotide、mandelbrot、n-body、pidigits、regex-redux | Revised BSD（与已引入部分同源） | k-nucleotide 补"哈希 + 字符串"；mandelbrot/n-body 与 AWFY 的 mand/nbody 重复；pidigits 需大数、regex-redux 需正则引擎 | **只补 k-nucleotide**；其余待语言标准库有大数/正则后再议 |
| **mimalloc-bench**（`daanx/mimalloc-bench`） | 分配器压力测试若干；单线程可用者如 `alloc-test`、`rptest`、`sh6bench`/`sh8bench`、`xmalloc-test`；`larson`/`mstress` 是多线程 | **MIT** | 补"随机尺寸分布"的分配压力（现有 4 件是固定/两类尺寸） | **建议取 1–2 个单线程项** 进 `bench/ALLOC` |
| **PolyBench/C**（经 LLVM test-suite 分发） | ~30 个数值内核（线性代数 / 数据挖掘 / 模板计算 / medley） | LLVM test-suite = Apache-2.0 with LLVM exception；Polybench 目录自带 LICENSE | 与 `numeric` 侧重部分重叠，但补"长数组 + 边界检查密集" | **可选**：先引 3–5 个（gemm、2mm、atax、jacobi-2d、fdtd-2d）作为 `numeric` 补充 |
| **Olden**（`compor/olden`；LLVM test-suite 亦有一份） | 10 个指针/递归密集 C 程序：`bh`、`bisort`、`em3d`、`health`、`mst`、`perimeter`、`power`、`treeadd`、`tsp`、`voronoi` | **不可直接 vendor**：LICENSE 为 LLVM + 原始非商业限制（"不得与商业产品或服务一起分发"） | 与方向 A/B 最相关的侧重（对象图、递归、分配） | **不 vendor**：要么按同样算法**自写** YIAN + C 参考（像现在对 AWFY 做的那样），要么做成 `build/` 下的外部可选套件（不进仓库） |
| **MiBench** | 35 个嵌入式 C 程序（汽车/消费/办公/安全/网络/通信） | 站点声明"free for academic use"，具体条款需人工确认 | 与指针/内存机制相关性低 | **暂缓**（许可与相关性都不占优） |
| **Embench-IoT** | 19 个嵌入式基准 | **GPL-3.0** | 相关度低 | **不引入**：与本仓库 Apache-2.0/MIT 双许可不兼容 |

## 三、明确不引入

| 候选 | 原因 |
| --- | --- |
| SPEC CPU（2017 等） | 授权收费、体量过大、单次运行分钟级 |
| PARSEC / Splash-3 / Rodinia / NPB / BOTS / CLOMP | 面向多线程、OpenMP/MPI/GPU；YIAN 当前是单线程模型 |
| DaCapo、Renaissance、Soot/SOM 套件本体 | JVM/语言运行时基准，与 C 参考三态协议不符（其**基准算法**可以像 AWFY 那样改写，但那是"自写"而不是"引入"） |
| cBench | 许可混杂、与既有套件大量重叠 |
| AnghaBench、CRUST 等大规模语料 | 面向编译器测试而非性能度量 |

## 四、引入流程（每个新套件/基准都走完）

1. **许可与来源**：核对上游许可，把许可文件与来源说明复制进 `bench/<SOURCE>/`，并在本文件登记；
   与本仓库许可不兼容的一律不进仓库。
2. **移植**：写 `.an` 版本（胖态优先；`T[]`/`T*` 在裸态语义不同时加 `<name>.raw.an` 覆盖源），
   写或采用 C 参考（同算法同规模），写 `specs/<name>.json`（`argv`、`check`、`tags`、可选 `scale`）。
3. **规模对齐**：逐项声明与 C 参考的规模关系（同规模 / 调整过并给出理由），登记到 `bench/README.md`；
   若提供 fast 规模，按 `bench/README.md` 的标定方法实测（缩小规模不得改变内部断言语义）。
4. **验收**：三态 stdout 一致、C 权威值通过、`--set <set> --scale fast|full` 两档都能跑完，
   并把规模表写进 `bench/README.md`。

## 五、参考链接

- Benchmarks Game：<https://benchmarksgame-team.pages.debian.net/benchmarksgame/> ·
  许可 <https://benchmarksgame-team.pages.debian.net/benchmarksgame/license.html> ·
  源码仓库 <https://salsa.debian.org/benchmarksgame-team/benchmarksgame>
- AWFY：<https://github.com/smarr/are-we-fast-yet>
- plb2：<https://github.com/attractivechaos/plb2>
- mimalloc-bench：<https://github.com/daanx/mimalloc-bench>
- Olden：<https://github.com/compor/olden> ·
  LLVM test-suite 副本 <https://llvm.googlesource.com/llvm-test-suite/+/refs/heads/main/MultiSource/Benchmarks/Olden/>
- PolyBench（LLVM test-suite）：<https://llvm.googlesource.com/llvm-test-suite/+/refs/heads/main/SingleSource/Benchmarks/Polybench/>
- Embench-IoT：<https://github.com/embench/embench-iot>
- MiBench：<https://web.eecs.umich.edu/mibench/>
