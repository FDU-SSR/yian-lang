# 评测集重组计划（来源分组 · 侧重分组 · 快速/完全两档）

本计划把现在的单一评测集拆成"按来源分目录、按侧重点分集合"的小评测集，每个集合都跑 C/raw/fat
三态，并给每个集合加"快速 / 完全"两档规模：快速档用于开发过程中的频繁回归，完全档用于正式记录。

## 0. 状态

| 阶段 | 状态 |
| --- | --- |
| **S1 目录与集合** | **已完成**。`bench/{AWFY,BG}/{an,c,specs}` + `bench/ALLOC/{an,specs}` + `bench/sets/*.json` + `bench/results/*`;`C_SPECS` 外置成声明式 `spec.json`（`rc`/`stdout_eq`/`stdout_contains`/`stdout_int_mod`）;运行器新 CLI `--set/--scale/--source/--bench/--list-sets`,`bench_allocator.py` 跟随 `bench/ALLOC`。等价校验:同一工具链（yian-env）下搬目录前后的 `full` 基线逐项对比,几何平均 1.010、中位 +1.7%、最大 \|3.8%\|,峰值 RSS 全项不变——落在机器噪声内 |
| **S2 快速档** | **机制已完成**。规模由源里 `// bench-scale` 标记行 + `specs/*.json` 的 `scale` 描述;fast 档在 `build/bench/src/fast/` 下生成替换过规模的构建副本,同一个值作为 argv 传给 C 参考,三态同参;fast 档不触发降次。已标定 6 项:queen 150→10、sieve ITER 500→25、ALLOC 四项 1000/20000/40/32 → 100/2000/8/8。**待做**:list、towers、binarytree、deltablue、json、richards 等其余基准的 fast 规模标定（缩放时不得改变内部断言语义,见 §4.1） |
| **S3–S5 引入新套件** | 未开始;已登记到 `docs/proposal.md` 的"评测集与性能度量",调研结论留档在 `bench/SUITES.md` |

快速档实测:8 项代表性子集含编译 **1 分 42 秒**（目标 ≤2 分钟）;分配器快速档四项各 2.7–8.9 ms。
生成物策略:完全档与分配器记录（`bench/results/full.*`、`bench/results/alloc.*`）进版本历史;
快速档与临时子集（`bench/results/fast.*`、`partial.*`）已加入 `.gitignore`。

## 1. 现状与问题

### 1.1 现在的目录

```text
bench/shootout/*.an        19 个基准（14 项来自 AWFY + 5 项来自 Benchmarks Game）
bench/shootout/storage.raw.an   storage 的裸态覆盖源
bench/c/*.c                19 个 C 参考（三态的权威值）
bench/alloc/*.an (+ .raw.an)   分配器专项 4 件
bench/results.{md,csv}     生成物：三态基线（记录 commit 与工具链指纹）
scripts/bench_three_way.py 三态运行器（C_SPECS 内置每基准 argv 与权威校验）
scripts/bench_allocator.py 分配器运行器（fat/raw 两态）
```

来源核对（各基准头注释）：**AWFY** = bounce、cd、deltablue、havlak、json、list、mand、nbody、permute、
queen、richards、sieve、storage、towers（14）；**Benchmarks Game** = binarytree、fann、fasta、revcomp、
spectralnorm（5）。两类混在同一个目录里，`bench/c` 也混着放，看不出侧重，也没法按侧重挑选运行。

### 1.2 规模与耗时（当前机器实测）

| 一轮三态合计 | C | raw | fat | 完整跑（1 warmup + 5 测量） |
| --- | ---: | ---: | ---: | ---: |
| 134 s | 31 s | 33 s | 71 s | ≈ 13 分钟（另加编译约 3–5 分钟） |

fat 最慢 6 项：list 30.3 s、queen 6.9 s、towers 4.4 s、binarytree 2.9 s、bounce 2.8 s、sieve 2.8 s。
一次完整跑约 15–20 分钟，不适合每次改动都跑。

### 1.3 现在的规模参数化方式

规模**写死在 `.an` 源里**（如 binarytree 的 `N=20`、storage 的 `depth=16`），C 参考则用 argv
（`cd 100 80`、`richards 2400`），`C_SPECS` 里记录 argv 与权威 stdout。两边的规模靠人工保持一致
（注释里写明"N=100 × ITER=14000"），没有统一的规模档位，因此无法只改一个开关就跑小规模。

## 2. 目标与非目标

**目标**

1. 目录按**来源**分组（一个基准的代码只属于一个来源目录），集合按**侧重点**组合（一个基准可以进多个集合）。
2. 每个集合都能跑三态（沿用现有协议：C 为权威、raw/fat 输出必须一致、绑核、min-of-N、峰值 RSS）。
3. 每个集合有**快速档**（开发中频繁跑，目标 ≤ 2 分钟含编译）与**完全档**（正式记录，当前规模）。
4. 引入更多现成评测集：先做许可与相关性评估，再按优先级逐个引入。

**非目标**

- 不改三态协议与语义护栏（C 权威值、raw/fat stdout 逐字节一致、退出码 0）。
- 不设性能回归门槛（性能只作参考，见主计划 §3.3）。
- 不引入多线程/多进程评测（YIAN 当前是单线程模型）。
- 不改编译器与运行时；本计划只动 `bench/`、`scripts/bench_*.py` 与相关文档。

## 3. 目录与集合设计

### 3.1 三个正交的轴

| 轴 | 取值 | 落点 |
| --- | --- | --- |
| **来源** | AWFY、BG（Benchmarks Game）、PLB2、ALLOC、MICRO、后续 OLDEN/… | 目录：`bench/<SOURCE>/{an,c,specs}` |
| **侧重** | `ptr`（对象/指针图）、`numeric`（数值/循环）、`string`（字符串/IO）、`alloc`（分配/释放）、`control`（分支/搜索）、`micro`（机制专项） | 每个基准的 `spec.json` 里打 tag；集合由集合清单引用 |
| **规模档** | `fast`、`full` | `spec.json` 的 `scale` 段，运行器 `--scale` 选择 |

### 3.2 目录布局

```text
bench/
├── AWFY/
│   ├── an/{bounce,cd,deltablue,havlak,json,list,mand,nbody,permute,queen,richards,sieve,storage,towers}.an
│   │   └── storage.raw.an            # 裸态覆盖源与胖态同目录
│   ├── c/{同名}.c                    # C 参考（三态权威）
│   └── specs/{同名}.json             # argv / 权威校验 / 侧重 tag / 规模档
├── BG/{an,c,specs}/…                 # binarytree, fann, fasta, revcomp, spectralnorm
├── ALLOC/{an,c,specs}/…              # 现有 bench/alloc 四件迁入
├── MICRO/{an,c,specs}/…              # 机制专项（方向 A/B 的指针/调用/拷贝微基准）
├── PLB2/{an,c,specs}/…               # 引入后（见 §5）
├── sets/{fast,full,ptr,numeric,string,alloc,micro}.json
├── results/<set>.{md,csv}            # 生成物（含指纹与 commit）
└── README.md
```

`bench/shootout` 与 `bench/c` 迁入 `bench/AWFY` 与 `bench/BG`（用户的示例形状：来源名大写 + `an/`、`c/`，
来源是专名缩写，路径里更显眼；若要全小写，全局替换即可）。`bench/alloc` 迁成 `bench/ALLOC`（保住
"分配器专项"这个侧重）；裸态覆盖源 `<name>.raw.an` 与胖态同目录（沿用现有发现规则）。

### 3.3 集合清单

集合是**引用列表**（可写死，也可按 tag 匹配），运行时由运行器展开成 `(source, name)` 列表：

| 集合 | 内容 | 用途 |
| --- | --- | --- |
| `full` | 所有来源、所有基准 | 正式基线，写入 `results/full.{md,csv}` |
| `fast` | 代表性 8–10 项 × `scale=fast` | 开发中每次改动跑 |
| `ptr` | tag 含 `ptr` 的基准（list/deltablue/richards/havlak/towers/binarytree/json…） | 方向 A/B 的指针与表示改动 |
| `numeric` | tag 含 `numeric`（fann/mand/nbody/permute/sieve/spectralnorm/fasta…） | 数值/循环相关的改动 |
| `string` | tag 含 `string`（fasta/revcomp/json，引入后加 k-nucleotide） | 字符串/IO 相关 |
| `alloc` | `ALLOC` 全部 + 引入的分配压力测试 | 分配器改动 |
| `micro` | `MICRO` 全部（拷贝/传参/追逐三件） | 表示与 ABI 改动 |

集合清单格式（示例）：

```json
{
  "name": "fast",
  "scale": "fast",
  "runs": 3,
  "include": ["AWFY/list", "AWFY/havlak", "AWFY/deltablue", "AWFY/binarytree",
              "AWFY/json", "AWFY/queen", "AWFY/fasta", "AWFY/storage",
              "BG/spectralnorm", "ALLOC/churn_single"],
  "exclude": []
}
```

`full.json` 用 `"include": ["*"]`（或按来源展开），保证新增基准自动进入完全档；`fast.json` 显式列名单，
避免自动膨胀。

### 3.4 运行器改造

`scripts/bench_three_way.py`：

- 发现规则：扫描 `bench/*/an/*.an`（跳过 `*.raw.an`），基准名 = `<SOURCE>/<name>`；`*.raw.an` 作为同名的
  裸态覆盖源；缺 `specs/<name>.json` 或 C 参考时**报错退出**（沿用现在"缺 C_SPECS 就报错"的严格性）。
- 规格外置：把现在的 `C_SPECS`（Python dict + lambda）搬进 `bench/<SOURCE>/specs/<name>.json`，
  用**声明式校验**表达现有全部断言：`rc`、`stdout_eq`、`stdout_contains`（全部包含）、
  `stdout_int_mod`（binarytree 的 `%256==176`）。这样新增基准不必改运行器代码。
- 规模档：`spec.json` 的 `scale` 段给 `fast`/`full` 两组的 argv（见 §4），运行器把它同时传给三态。
- CLI：`--set <name>`（默认 `full`）、`--scale fast|full`（覆盖集合默认）、`--source AWFY,BG`、
  `--bench AWFY/list`、`--list-sets`；已有的 `--runs/--pin/--max-state-sec/--names/--no-compile` 保留
  （`--names` 变为 `--bench` 的别名或直接废弃，迁移时统一）。
- 输出：`bench/results/<set>.{md,csv}`；`full` 另外维持一份 `bench/results.csv` 兼容链接/副本，
  或直接把现有文件迁成 `bench/results/full.csv` 并更新所有引用（推荐后者，一次改干净）。
- 构建路径：`build/bench/<set>/<state>/<source>_<name>`，`<state>` 三个目录名仍等长（`cbin/yraw/yfat`），
  保持"分配密集基准对 argv[0] 长度敏感"的既有约束。

`scripts/bench_allocator.py`：跟随 `bench/ALLOC` 的路径与新 spec（argv/规模档），并把结果写进
`bench/results/alloc.{md,csv}`，作为 `alloc` 集合的一部分（或保留独立入口、由集合清单调用）。

## 4. 快速档与完全档

### 4.1 规模参数化：argv 驱动

给每个基准定义**一个整数规模参数**，由 argv 传入，三态用同一份 argv：

- YIAN 侧：`std.core.env.args()` 已经包装了 `@argc()`/`@arg_bytes(i)`，`str.parse<u64>()` 可以把参数字符串
  转成整数；基准开头读第一个参数，缺省值 = 当前规模（完全档）。这样**同一批二进制**可以在两档规模下
  跑，快速档不需要重新编译。
- C 侧：现有 C 参考里 `cd`/`richards` 已经读 argv，其余补上同样的"读 argv、缺省=当前规模"。
- 一致性：运行器把 `spec.scale[profile]` 的 argv 同时传给 C/raw/fat——**现在只传给 C**
  （`argv_c if state == "c" else []`），迁移时要改成三态同参；小规模下的权威值在实现时实测填入 spec，
  校验逻辑不变。
- 不适用 argv 的基准（如纯内存布局的 `MICRO` 微基准）在 `spec.json` 里标 `"scaleable": false`，
  两档用同一规模，只由集合决定是否进入快速档。

### 4.2 两档协议

| 项 | fast | full |
| --- | --- | --- |
| 规模 | 每项 ~0.2–0.5 s（fat 态） | 现状规模（fat 合计 71 s） |
| 测量次数 | 1 warmup + 3 测量（取 min） | 1 warmup + 5 测量（取 min，记中位与 CV） |
| 自适应降次 | 关闭（规模已经很小） | 保留（`--max-state-sec`） |
| 输出 | `bench/results/<set>.md`（单表 + 一行指纹） | `bench/results/<set>.{md,csv}`（含协议说明） |
| 预算目标 | 含编译 ≤ 2 分钟；稳态一轮 ≤ 15 s | ≈ 13 分钟 + 编译，用于正式记录与对外引用 |

快速档的规模不是"随手改小"，而是**按 fat 态 0.2–0.5 s 反推**：实现时用 `--scale fast` 跑一遍，
把每项实测贴进 README 的表格；若某次改动让某项在快速档超出 1 s，说明规模该调（或该基准不适合快速档）。

### 4.3 结论可信度规则

- **完全档是唯一用于对外记录与结论的档位**；快速档只用于"有没有退化"的方向性判断。
- 快速档与完全档结论冲突时以完全档为准，并把冲突记进 `bench/README.md` 的"已知规模敏感性"表
  （哪些基准在小规模下与大规模不同向，例如分配器行为、缓存层级跨越）。
- 快速档必须覆盖每个侧重至少一项（`ptr`/`numeric`/`alloc`/`string`），否则退化成"只测某一类"。

## 5. 引入更多评测集：调研与评估

调研只考虑**有 C 参考、单线程、规模适中、许可清楚**的套件（YIAN 的三态要求 C 权威值）。
许可结论已按下表逐项核实（链接见 §9）。

| 候选 | 内容 | 许可（已核实） | 与本项目的相关性 | 结论 |
| --- | --- | --- | --- | --- |
| **AWFY**（smarr/are-we-fast-yet） | 14 个面向对象微基准（Java/SOM 上游） | 混合：Benchmarks Game 部分为 Revised BSD；Richards/DeltaBlue 转自 Wolczko 的 Smalltalk 源，其 LICENSE.md 只给出上游链接 | 已全部引入（14/14），对象/指针图覆盖最好 | **保持**；补一份来源与许可说明；引前对照一次上游 `benchmarks/Java/src` 是否有遗漏 |
| **Benchmarks Game**（官方站/ salsa 仓库） | 10 个程序：binarytrees、fannkuch-redux、fasta、k-nucleotide、mandelbrot、n-body、pidigits、regex-redux、reverse-complement、spectral-norm | Revised BSD（已核实） | 已引入 5 个；剩余 5 个中 mandelbrot/n-body 与 AWFY 重复，pidigits 需大数、regex-redux 需正则引擎 | **只补 k-nucleotide**（哈希 + 字符串）；pidigits/regex-redux 若语言以后有对应库再议 |
| **plb2**（attractivechaos/plb2） | 每语言 4 个程序：`matmul.c`、`nqueen.c`、`sudoku.c`、`bedcov.c`（C 参考齐全） | **CC0-1.0**（已核实） | 补"数值 + 回溯搜索 + 哈希/区间覆盖"侧重；体量小、单线程 | **建议引入 matmul/nqueen/sudoku**；bedcov 需要文件 I/O，后置 |
| **PolyBench/C**（经 LLVM test-suite 分发） | ~30 个数值内核（线性代数/数据挖掘/模板计算/medley） | LLVM test-suite = Apache-2.0 with LLVM exceptions（已核实；Polybench 目录自带 LICENSE） | 与 `numeric` 侧重重叠，但能补"长数组 + 边界检查密集"的形态 | **可选**：先引 3–5 个（如 gemm、2mm、atax、jacobi-2d、fdtd-2d）作为 `numeric` 补充 |
| **mimalloc-bench**（daanx/mimalloc-bench） | 分配器压力测试若干（`alloc-test`、`rptest`、`sh6bench`/`sh8bench`、`xmalloc-test`、`cache-scratch`、`larson`/`mstress` 等） | **MIT**（已核实） | 多为多线程；单线程子集能补"随机尺寸分布"的分配压力（我们现有 4 件是固定/两类尺寸） | **建议引入 1–2 个单线程项**进 `ALLOC` |
| **Olden**（compor/olden；LLVM test-suite 也有一份） | 10 个指针/递归密集 C 程序：bh、bisort、em3d、health、mst、perimeter、power、treeadd、tsp、voronoi | **不可直接 vendor**：LICENSE 为 LLVM + "不得与商业产品或服务一起分发"的原始限制（已核实） | 与方向 A/B 最相关的侧重（对象图、递归、分配） | **不 vendor**：要么按同样算法**自写 YIAN + C 参考**（像现在对 AWFY 做的那样），要么作为"外部可选套件"（`scripts/fetch_bench_suite.py` 下载到 `build/`，不进仓库） |
| **MiBench** | 35 个嵌入式 C 程序（汽车/消费/办公/安全/网络/通信） | "free for academic use"（需人工核对具体条款） | 与指针/内存机制相关性低 | **暂缓**（许可与相关性都不占优） |
| **Embench-IoT** | 19 个嵌入式基准 | **GPL-3.0**（已核实） | 相关度低 | **不引入**：与本仓库 Apache-2.0/MIT 双许可不兼容 |
| SPEC CPU、PARSEC/Splash-3、NPB、Rodinia、DaCapo、cBench | — | 授权收费 / 多线程 / JVM / 许可混杂 | 低 | **不引入**（单线程模型与许可都不合适） |

### 5.1 引入流程（每个新套件都要走完）

1. **许可与来源**：把上游仓库、commit、许可文件复制进 `bench/<SOURCE>/LICENSE.<ext>`，在 `bench/README.md`
   登记来源与许可；许可与 Apache-2.0/MIT 不兼容的**不进仓库**（改自写或外部下载）。
2. **移植**：写 `.an` 版本（fat 优先；`T[]`/`T*` 在 raw 下语义不同时加 `<name>.raw.an` 覆盖源）、
   写 C 参考（同算法同规模）、在 `specs/<name>.json` 填 argv、权威校验（先实测 C 的 stdout/退出码再填）、
   侧重 tag、规模档。
3. **对齐核对**：按现有 `bench/README.md` 的"算法与规模对齐"要求在 README 里声明每个基准与 C 参考的
   规模关系（同规模 / 调整过并给出理由）。
4. **验收**：三态 stdout 一致、C 权威值通过、`--set <source> --scale fast/full` 两档都能跑完、
   快速档实测规模写入 README。

## 6. 迁移步骤

1. **搬目录**（`git mv`，保留历史）：`bench/shootout/*.an` → `bench/AWFY/an` 与 `bench/BG/an`（按来源拆）；
   `bench/c/*.c` → 对应 `bench/<SOURCE>/c`；`bench/alloc/*` → `bench/ALLOC/{an,c}`。
2. **抽 spec**：把 `C_SPECS` 的 argv + lambda 校验翻译成 `bench/<SOURCE>/specs/<name>.json` 的声明式字段；
   逐项核对翻译后的校验与原来等价（对每个基准跑一次 warmup 比对）。
3. **改运行器**：发现规则、spec 加载、`--set/--scale/--source/--bench`、输出路径、构建路径（保持等长）、
   `bench_allocator.py` 跟随新路径。
4. **改文档与引用**：`bench/README.md`（协议、集合清单、规模档、来源与许可）、`AGENTS.md`
   （如果提到 `bench/` 的具体路径）、`docs/plan/fat-pointer-performance-plan.md`、
   `docs/plan/fat-pointer-representation-plan.md` 里引用 `bench/results.csv`、`bench/shootout`、
   `bench/c` 的位置；基准源文件头注释里的"语义权威源: bench/c/<name>.c"路径。
5. **一次等价校验**：搬完后在**同一台机器、同一个 commit、同一工具链**上跑 `--set full`，
   与搬之前的 `bench/results.csv` 逐项对比：所有基准必须落在噪声内（±2%），stdout 校验全通过；
   若某一项差异超出噪声，先查是不是路径长度/argv/规模变了。
6. **建快速档**：实现 argv 规模参数（§4.1），为每项实测快速档规模，写入 spec 与 README。

## 7. 分阶段

| 阶段 | 工作 | 验收 |
| --- | --- | --- |
| **S1 目录与集合**（先做） | §6 的 1–4：来源分目录、spec 外置、集合清单、运行器 CLI、文档同步 | `--set full` 与搬前结果噪声内一致；`--list-sets` 列出全部集合；缺 spec/C 参考时报错清晰 |
| **S2 快速档** | §4：argv 规模参数、两档协议、`fast` 集合与规模标定 | 快速档含编译 ≤ 2 分钟；快速/完全两档对同一改动的方向一致（至少在 `ptr`/`alloc` 两类上） |
| **S3 引入 plb2** | matmul/nqueen/sudoku 三个（CC0、有 C 参考） | 三态 stdout 一致；进入 `numeric`/`control`/`ptr` 集合；快速档规模记录在 README |
| **S4 引入 mimalloc 单品** | 选 1–2 个单线程分配压力测试进 `ALLOC` | 与现有 4 件一起进 `alloc` 集合；随机尺寸分布有读数 |
| **S5 可选** | PolyBench 子集（numerical）；Olden 的**自写版**（若确实需要指针密集补充） | 自写版必须有自己的 C 参考与权威值，且不复制受限代码 |

每阶段独立可交付：S1 不做也可以先只做 S2（规模档），但两者一起做才不用二次搬目录，建议按顺序。

## 8. 风险与开放问题

- **许可**：Olden 与 Embench 已明确不能 vendor；MiBench 的"academic use"条款需要人工确认；AWFY 的
  Richards/DeltaBlue 上游来源许可只在 AWFY 的 LICENSE.md 里给了链接，属于既有状况（现有 14 项已如此），
  本计划只做登记不做改变。
- **移植成本**：每个新基准 = 一份 `.an` + 一份 C 参考 + 一个 spec + 规模标定，按经验是半天到一天一项；
  引入顺序按"能覆盖新侧重"而不是"数量多"。
- **规模敏感性**：小规模可能改变结论（缓存层级、分配器行为、JIT/预热之类没有，但内存驻留形态会变）；
  快速档必须标"只作方向性判断"，并把已知不同向的基准列进 README。
- **集合爆炸**：来源 × 侧重 × 规模容易变成十几套命令；约束是"集合清单只有一个目录 `bench/sets/`，
  新增集合必须说明它覆盖哪个机制轴"。
- **路径长度**：`build/bench/<set>/<state>/<source>_<name>` 的三态目录名等长，但基准名部分也要等长
  （`<source>_<name>` 在三态之间是同名，天然等长）；迁移后要复测一次分配密集基准（binarytree/churn），
  确认没有因为路径变化引入布局偏差。
- **`bench/results.csv` 的连续性**：现有引用（文档、计划）指向它；迁移时要么保持该文件名作为
  `full` 的输出，要么一次性改掉所有引用——建议后者。

## 9. 参考

- Benchmarks Game：程序清单与 Revised BSD 许可
  <https://benchmarksgame-team.pages.debian.net/benchmarksgame/>、
  <https://benchmarksgame-team.pages.debian.net/benchmarksgame/license.html>；源码仓库
  <https://salsa.debian.org/benchmarksgame-team/benchmarksgame>
- AWFY：<https://github.com/smarr/are-we-fast-yet>（`LICENSE.md` 说明各基准的不同来源）
- plb2：<https://github.com/attractivechaos/plb2>（`LICENSE` = CC0-1.0；`src/c/` 四个 C 程序）
- mimalloc-bench：<https://github.com/daanx/mimalloc-bench>（`LICENSE` = MIT）
- Olden：<https://github.com/compor/olden>（`LICENSE.TXT` = LLVM + 非商业限制）；
  LLVM test-suite 副本 <https://llvm.googlesource.com/llvm-test-suite/+/refs/heads/main/MultiSource/Benchmarks/Olden/>
- PolyBench（经 LLVM test-suite）：<https://llvm.googlesource.com/llvm-test-suite/+/refs/heads/main/SingleSource/Benchmarks/Polybench/>
- Embench-IoT：<https://github.com/embench/embench-iot>（`COPYING` = GPL-3.0）
- MiBench：<https://web.eecs.umich.edu/mibench/>
- 现有协议与基线：`bench/README.md`、`scripts/bench_three_way.py`、`bench/results.{md,csv}`、
  `scripts/bench_allocator.py`
