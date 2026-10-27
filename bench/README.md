# bench/ — 评测集

评测集按**来源**分目录、按**侧重点**分集合、按**规模**分快速/完全两档。每个集合都跑三态
（C / raw / fat），C 参考实现提供结果校验的参考值。分配器专项使用 fat/raw 两态。

## 目录

```text
bench/
├── AWFY/            来源: Are We Fast Yet (smarr/are-we-fast-yet 的 Java 微基准改写)
│   ├── an/*.an        胖态源; <name>.raw.an 为裸态覆盖源 (同目录)
│   ├── c/*.c          C 参考实现（结果校验）
│   └── specs/*.json   argv / 权威校验 (check) / 侧重 tag / 规模档
├── BG/              来源: The Computer Language Benchmarks Game (Revised BSD)
├── ALLOC/           分配器专项 (两态: fat vs libc malloc/free, 无 C 参考)
│   ├── an/  specs/
├── fatptr/          机制专项微基准 (表示尺寸/拷贝/追逐, 本仓库自写)
│   ├── an/  c/  specs/
├── sets/*.json      集合清单: 成员 + 默认规模档 + 测量次数 (paper.json = 论文 14 基准)
├── results/         生成物: <set>.{md,csv}、asan.{md,csv}、asan-samples.txt、alloc/partial
├── bench_three_way.py 三态运行器 (C / raw / fat)
├── bench_asan.py    ASan 交叉对比运行器 (C plain / C ASan / YIAN fat)
├── bench_allocator.py 分配器运行器 (fat / raw 两态)
├── SUITES.md        来源与许可登记
└── README.md
```

当前评测来源与许可登记见 `bench/SUITES.md`。

这些命令用于编译、运行和测量所选基准，不属于默认局部验收。执行仓库验证前应检查集合、基准数、规模和采样次数；性能测量需要明确授权。

## 集合

```bash
python3 bench/bench_three_way.py --list-sets           # List sets, scales, and member counts.
python3 bench/bench_three_way.py --set full            # Full scale: all 22 benchmarks, for formal records.
python3 bench/bench_three_way.py --set fast --pin 4    # Fast: representative subset at reduced scale.
python3 bench/bench_three_way.py --set ptr             # Focus: object/pointer graphs.
python3 bench/bench_three_way.py --set numeric         # Focus: numeric calculations/loops.
python3 bench/bench_three_way.py --set string          # Focus: strings/I/O.
python3 bench/bench_three_way.py --set micro           # Mechanism microbenchmarks: representation size/copying/pointer chasing.
python3 bench/bench_three_way.py --bench AWFY/queen,BG/fasta --scale full
python3 bench/bench_three_way.py --source AWFY --set fast
python3 bench/bench_three_way.py --set fast --yianc "$HOME/.local/bin/yianc"
```

| 集合 | 内容 | 默认规模档 | 用途 |
| --- | --- | --- | --- |
| `full` | 全部三态基准 | `full` | 正式基线与对外引用 |
| `fast` | 8 项代表性基准 | `fast` | 代表性子集与缩小规模，耗时取决于机器 |
| `ptr` | tag `ptr` | `full` | 指针/对象图 |
| `numeric` | tag `numeric` | `full` | 数值/循环改动 |
| `string` | tag `string` | `full` | 字符串/IO 改动 |
| `alloc-heavy` | tag `alloc` + storage/json | `full` | 分配密集但仍走三态 |
| `micro` | tag `micro`（fatptr 三项） | `full` | 表示与 ABI 的尺寸/检查读数 |

AWFY 与 BG 的成员：AWFY 14 项（bounce、cd、deltablue、havlak、json、list、mand、nbody、permute、
queen、richards、sieve、storage、towers），BG 5 项（binarytree、fann、fasta、revcomp、spectralnorm），
fatptr 3 项（call_abi、copy_struct、chase）。

集合只在 `bench/sets/` 里定义：新增集合要说明它覆盖哪个机制侧重（`ptr`/`numeric`/`string`/`alloc`/
`control`；`control` 只有 tag，没有对应集合），避免来源与侧重的组合膨胀。

## 两档规模

- **full**（默认，正式记录）：源内默认规模，每态 1 次 warmup + 5 次测量取最小值。
- **fast**（开发中频繁跑）：对 `specs/<name>.json` 里声明了 `scale.fast` 的基准，把源里
  `// bench-scale` 标记行的数字替换成 fast 值，在 `build/bench/src/fast/<SOURCE>/` 下生成构建
  副本再编译；同一个值作为 argv 传给 C 参考，保证三态工作量一致。其余基准两档同规模。
  fast 档不触发降次，每态 1 次 warmup + 3 次测量取最小值。

| 基准 | 标记的规模量 | full | fast |
| --- | --- | ---: | ---: |
| AWFY/queen | `ITER`（每轮 10 次 8 皇后求解） | 276923 | 65000 |
| AWFY/sieve | `ITER`（每轮执行完整筛法） | 268657 | 59000 |
| ALLOC/churn_single | `rounds` | 1000 | 100 |
| ALLOC/churn_mixed | `rounds` | 20000 | 2000 |
| ALLOC/grow_free | `cycles` | 40 | 8 |
| ALLOC/grow_varied | `cycles` | 32 | 8 |
| fatptr/call_abi | `CALLS`（每轮调用次数） | 4096 | 1024 |
| fatptr/copy_struct | `ROUNDS` | 1024 | 128 |
| fatptr/chase | `NODES` | 4000000 | 2000000 |

未列出的基准两档同规模，快速档通过代表性子集和这些规模覆盖控制时长。标定一项的做法是给源加
`// bench-scale` 标记行、在 spec 里填 `scale.fast`；fast 档的量级口径是 fat 态每项 0.2–0.5 s，
超过 1 s 的项按 full 规模运行。缩小规模不得改变基准内部断言的语义：像 sieve 那样缩放重复次数
最安全；若必须缩放问题规模，断言与权威值都要跟着写成规模的函数。

不同规模下的性能结论需分别解释；跨规模比较应核对工作量、缓存行为与采样条件。

## 三态协议

| 态 | 编译/运行 |
| --- | --- |
| C | `clang -O2 -ffp-contract=off bench/<SOURCE>/c/<name>.c -lm`（argv 见 spec） |
| raw | `yianc -O2 --raw-pointers lib/src <src>` + libc `malloc`/`free` |
| fat | `yianc -O2 lib/src <src>`（胖指针为默认） |

两个 runner 都只调用 native `yianc`，按 `--yianc PATH`、非空 `YIANC`、`PATH` 的顺序选择，并拒绝
脚本、目录、不可执行文件和非 ELF 文件。报告记录规范化 compiler 路径、SHA-256 与选择来源。
`bench_allocator.py --compiler-root PATH` 只指定标准库和输入源码根，不选择编译器。

- 产物：`build/bench/{cbin,yraw,yfat}/<SOURCE>_<name>`（三态**等长路径**——分配密集型基准的
  绝对值对 argv[0] 长度 → 进程布局敏感）。
- 采样：每态 1 次 warmup（不计入样本）后按 C→raw→fat **逐次轮转**测量 N 次；正式指标取最小值，
  同时记录中位数/四分位距/CV/峰值 RSS（`/usr/bin/time -v`）。
- 降噪：`taskset -c <cpu>` 绑核；逐次轮转减轻跨时段频率/温度漂移。
- 语义护栏：C warmup 必须通过 spec 的 `check`（`rc` / `stdout_eq` / `stdout_contains` /
  `stdout_int_mod`）；raw 与 fat 的 warmup stdout 与退出码必须逐字节一致，且退出码为 0。
  报告中标 `否` 即该基准的比值不可用。
- **不设回归门槛**：性能试验只作参考，不写阈值脚本；噪声量级取决于采样环境，判断变化需结合重复测量与样本分布。
- **算法等价需独立核对**：退出码与输出一致只能说明结果校验通过，不能证明搜索路径、读写恢复、分配或错误路径一致。
  解释比值前须核对 C/raw/fat 的算法与工作量；未经核对的条目不能作为表示开销的证据。
- **运行范围需先审查**：bench 命令会编译并运行所选负载，含 warmup 和多次采样；benchmark 是性能测量，不是默认局部测试。仓库任务中执行前需明确选择和测量范围并取得相应授权。

## ASan 交叉对比

`bench_asan.py` 在同一会话内相邻测量 C plain、C ASan（`ASAN_OPTIONS=detect_leaks=0`）和 YIAN
fat 三条腿；`binarytree` 另有 `quarantine_size_mb=0` 的敏感性腿。每腿 1 次 warmup 加 N 次测量，
输出中位数、IQR、min–max、峰值 RSS，以及三条口径的逐基准比值与几何平均：

- (i) C ASan / C plain（ASan 自身开销）
- (ii) C ASan / YIAN fat（跨工具链）
- (iii) YIAN fat / C plain（完整栈对未插桩 C）

```bash
python3 bench/bench_asan.py --set paper --runs 5 --pin 4
python3 bench/bench_asan.py --bench BG/fasta --runs 1 --pin 0
python3 bench/bench_asan.py --set paper --runs 5 --pin 4
```

结果写入 `results/asan.md`、`results/asan.csv` 和 `results/asan-samples.txt`（原始
`wall_ms (peak_rss_kb)` 样本，可与论文 data/ 下的冻结样本对照）。`--set paper` 是论文使用的
14 个基准；语义护栏要求 C warmup 通过 spec 的 `check`，ASan 的 stdout/退出码与 C plain
一致，YIAN 腿退出码为 0。

`BG/binarytree` 固定 `N=16`：ASan 约 2.5 s、峰值 RSS 约 0.7 GB。N=20 的 ASan 配置峰值 RSS
超过 9 GB、单次运行超过 4 分钟，不再使用。`--fast` 仍可对声明了 `scale.fast` 的基准单独降档。

## 分配器专项

```bash
python3 bench/bench_allocator.py --scale full --pin 4    # Full scale: seven samples per mode.
python3 bench/bench_allocator.py --scale fast --pin 4    # Fast scale: three samples per mode at reduced scale.
python3 bench/bench_allocator.py --scale fast --yianc "$HOME/.local/bin/yianc"
```

`bench/ALLOC` 的四项没有 C 参考，对照态是 libc `malloc`/`free`（raw 态），因此是两态比较：
同尺寸 churn、混合尺寸 churn、增长-释放、变尺寸增长-释放。结果写 `bench/results/alloc.{md,csv}`。

## 结果留档

`bench/results/` 里只有完全档与分配器记录（`full.{md,csv}`、`alloc.{md,csv}`）进版本历史；
其余集合（`ptr`/`numeric`/`string`/`alloc-heavy`/`micro`）与临时子集（`partial.*`）由 `.gitignore` 排除。
报告中的日期、提交与环境指纹定义该次测量的范围，不能视为当前源码性能。

## 论文整体复现

`scripts/rerun_paper_experiments.sh` 依次执行 safety 套件（含 38 对 CVE）、三态性能
（`--set paper`）和 ASan 对比（`--set paper`），输出与单独运行各 runner
一致；命令输出同时写入 `build/paper-rerun/<时间戳>/run.log`。

```bash
scripts/rerun_paper_experiments.sh                       # runs=5, pin=4
scripts/rerun_paper_experiments.sh --runs 3 --pin 0      # 不绑核, 每腿 3 次
scripts/rerun_paper_experiments.sh --skip-cve --bench BG/fasta --runs 1
scripts/rerun_paper_experiments.sh --refresh-data        # 结束后调用上级仓库的 refresh_data.py
```

`--bench` 同时限制两个 runner；`--skip-cve`/`--skip-perf`/`--skip-asan` 跳过对应阶段；
`SECL_RUNS`、`SECL_PIN` 与 `YIAN_PYTHON` 环境变量可覆盖默认值。

## 新增一个基准

1. 放到对应来源目录（新来源就新建 `bench/<SOURCE>/{an,c,specs}`）；
2. `an/<name>.an`（裸态语义不同则加 `<name>.raw.an`）、`c/<name>.c`（同算法同规模）、
   `specs/<name>.json`（`argv`、`check`、`tags`、可选 `scale`）；
3. 在 `bench/README.md` 登记来源与规模对齐关系；三态跑通并确认 C 权威值。

评测源的来源与许可见 `bench/SUITES.md`。
