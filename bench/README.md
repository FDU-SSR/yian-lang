# bench/ — 评测集

评测集按**来源**分目录、按**侧重点**分集合、按**规模**分快速/完全两档。每个集合都跑三态
（C / raw / fat），C 参考实现是三态的权威值。

## 目录

```text
bench/
├── AWFY/            来源: Are We Fast Yet (smarr/are-we-fast-yet 的 Java 微基准改写)
│   ├── an/*.an        胖态源; <name>.raw.an 为裸态覆盖源 (同目录)
│   ├── c/*.c          同算法同规模的 C 参考 (三态权威值)
│   └── specs/*.json   argv / 权威校验 (check) / 侧重 tag / 规模档
├── BG/              来源: The Computer Language Benchmarks Game (Revised BSD)
├── ALLOC/           分配器专项 (两态: fat vs libc malloc/free, 无 C 参考)
│   ├── an/  specs/
├── fatptr/          机制专项微基准 (方向 B: 表示尺寸/拷贝/追逐, 本仓库自写)
│   ├── an/  c/  specs/
├── sets/*.json      集合清单: 成员 + 默认规模档 + 测量次数
├── results/         生成物: <set>.{md,csv}、alloc.{md,csv}、partial.{md,csv}
├── bench_three_way.py 三态运行器 (C / raw / fat)
├── bench_allocator.py 分配器运行器 (fat / raw 两态)
├── SUITES.md        来源/许可登记与候选套件调研
└── README.md
```

来源与许可登记见 `bench/SUITES.md`（含尚未引入的候选套件调研结论）。

## 集合

```bash
python3 bench/bench_three_way.py --list-sets           # 列出集合、规模档、成员数
python3 bench/bench_three_way.py --set full            # 完全档: 全部 21 项 (正式记录)
python3 bench/bench_three_way.py --set fast --pin 4    # 快速档: 代表性子集 + 缩小规模
python3 bench/bench_three_way.py --set ptr             # 侧重: 对象/指针图
python3 bench/bench_three_way.py --set numeric         # 侧重: 数值/循环
python3 bench/bench_three_way.py --set string          # 侧重: 字符串/IO
python3 bench/bench_three_way.py --set micro           # 机制专项: 表示尺寸/拷贝/追逐微基准
python3 bench/bench_three_way.py --bench AWFY/queen,BG/fasta --scale full
python3 bench/bench_three_way.py --source AWFY --set fast
```

| 集合 | 内容 | 默认规模档 | 用途 |
| --- | --- | --- | --- |
| `full` | 全部三态基准 | `full` | 正式基线与对外引用 |
| `fast` | 8 项代表性基准 | `fast` | 开发中频繁跑（含编译 ≤2 分钟） |
| `ptr` | tag `ptr` | `full` | 指针/对象图改动（方向 A/B） |
| `numeric` | tag `numeric` | `full` | 数值/循环改动 |
| `string` | tag `string` | `full` | 字符串/IO 改动 |
| `alloc-heavy` | tag `alloc` + storage/json | `full` | 分配密集但仍走三态 |
| `micro` | tag `micro`（fatptr 两项） | `full` | 表示与 ABI 改动（方向 B）的尺寸/检查读数 |

AWFY 与 BG 的成员：AWFY 14 项（bounce、cd、deltablue、havlak、json、list、mand、nbody、permute、
queen、richards、sieve、storage、towers），BG 5 项（binarytree、fann、fasta、revcomp、spectralnorm），
fatptr 2 项（copy_struct、chase）。

集合只在 `bench/sets/` 里定义：新增集合要说明它覆盖哪个机制侧重（`ptr`/`numeric`/`string`/`alloc`/
`control`；`control` 目前只有 tag 还没有集合），避免来源与侧重的组合膨胀。

## 两档规模

- **full**（默认，正式记录）：源内默认规模，每态 1 次 warmup + 5 次测量取最小值。
- **fast**（开发中频繁跑）：对 `specs/<name>.json` 里声明了 `scale.fast` 的基准，把源里
  `// bench-scale` 标记行的数字替换成 fast 值，在 `build/bench/src/fast/<SOURCE>/` 下生成构建
  副本再编译；同一个值作为 argv 传给 C 参考，保证三态工作量一致。其余基准两档同规模。
  fast 档不触发降次，每态 1 次 warmup + 3 次测量取最小值。

| 基准 | 标记的规模量 | full | fast |
| --- | --- | ---: | ---: |
| AWFY/queen | `SOLVES` | 150 | 10 |
| AWFY/sieve | `ITER`（SIEVE_SIZE 与素数断言不变） | 500 | 25 |
| ALLOC/churn_single | `rounds` | 1000 | 100 |
| ALLOC/churn_mixed | `rounds` | 20000 | 2000 |
| ALLOC/grow_free | `cycles` | 40 | 8 |
| ALLOC/grow_varied | `cycles` | 32 | 8 |
| fatptr/copy_struct | `ROUNDS` | 1024 | 128 |
| fatptr/chase | `NODES` | 4000000 | 2000000 |

其余基准（list、towers、binarytree、deltablue、json、richards、……）的 fast 规模尚未标定：
它们目前两档同规模，快速档通过"子集 + 上述 6 项缩规模"控制时长。标定的做法是给源加
`// bench-scale` 标记行、在 spec 里填 `scale.fast`，再实测一遍：目标是把该基准的 fat 态压到
0.2–0.5 s 量级，单项超过 1 s 就说明规模还没调够（storage 在 fast 档仍是 1.7 s，是最该标定的一项）。
缩小规模不得改变基准内部断言的语义：像 sieve 那样缩放重复次数最安全；若必须缩放问题规模，
断言与权威值都要跟着写成规模的函数。

两档结论冲突时以完全档为准，并把该基准记进"已知规模敏感性"（到目前为止没有出现冲突：两档在
`ptr`/`alloc`/`numeric`/`string` 四类上的方向一致）。

## 三态协议

| 态 | 编译/运行 |
| --- | --- |
| C | `clang -O2 -lm bench/<SOURCE>/c/<name>.c`（argv 见 spec） |
| raw | `yianc -O2 --raw-pointers lib/src <src>` + libc `malloc`/`free` |
| fat | `yianc -O2 lib/src <src>`（胖指针为默认） |

- 产物：`build/bench/{cbin,yraw,yfat}/<SOURCE>_<name>`（三态**等长路径**——分配密集型基准的
  绝对值对 argv[0] 长度 → 进程布局敏感）。
- 采样：每态 1 次 warmup（不计入样本）后按 C→raw→fat **逐次轮转**测量 N 次；正式指标取最小值，
  同时记录中位数/四分位距/CV/峰值 RSS（`/usr/bin/time -v`）。
- 降噪：`taskset -c <cpu>` 绑核；逐次轮转消除跨时段频率/温度漂移。
- 语义护栏：C warmup 必须通过 spec 的 `check`（`rc` / `stdout_eq` / `stdout_contains` /
  `stdout_int_mod`）；raw 与 fat 的 warmup stdout 与退出码必须逐字节一致，且退出码为 0。
  报告中标 `否` 即该基准的比值不可用。
- **不设回归门槛**：性能试验只作参考，不写阈值脚本；本机同一份代码两次全量跑的逐项差可达 ±2%，
  单项变化小于这个量级时只当噪声，结论取 min-of-N 且要看多项同向。

## 分配器专项

```bash
python3 bench/bench_allocator.py --scale full --pin 4    # 完全档: 每态 7 次
python3 bench/bench_allocator.py --scale fast --pin 4    # 快速档: 每态 3 次 + 缩小规模
```

`bench/ALLOC` 的四项没有 C 参考，对照态是 libc `malloc`/`free`（raw 态），因此是两态比较：
同尺寸 churn、混合尺寸 churn、增长-释放、变尺寸增长-释放。结果写 `bench/results/alloc.{md,csv}`。

## 结果留档

`bench/results/` 里只有完全档与分配器记录（`full.{md,csv}`、`alloc.{md,csv}`）进版本历史；
其余集合（`ptr`/`numeric`/`string`/`alloc-heavy`/`micro`）与临时子集（`partial.*`）是开发过程中的
读数，已在 `.gitignore` 里排除。

## 新增一个基准

1. 放到对应来源目录（新来源就新建 `bench/<SOURCE>/{an,c,specs}`）；
2. `an/<name>.an`（裸态语义不同则加 `<name>.raw.an`）、`c/<name>.c`（同算法同规模）、
   `specs/<name>.json`（`argv`、`check`、`tags`、可选 `scale`）；
3. 在 `bench/README.md` 登记来源与规模对齐关系；三态跑通并确认 C 权威值。

引入现成套件的调研结论（哪些能引、许可是什么、工作量如何）见 `bench/SUITES.md`。
