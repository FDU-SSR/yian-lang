# bench/ - benchmark suite

The suite organizes benchmarks by **source** directory, by **focus** set, and by
**scale** profile (fast/full). Every set runs three states (C / raw / fat), with
the C reference providing the authoritative result values. The allocator suite
uses the two YIAN states, fat and raw.

## Layout

```text
bench/
├── AWFY/            Source: Are We Fast Yet (Java microbenchmark rewrites from smarr/are-we-fast-yet)
│   ├── an/*.an        Fat-mode sources; <name>.raw.an is the raw-mode override (same directory)
│   ├── c/*.c          C reference implementations (result checks)
│   └── specs/*.json   argv / authoritative check / focus tags / scale profile
├── BG/              Source: The Computer Language Benchmarks Game (Revised BSD)
├── ALLOC/           Allocator suite (two states: fat vs libc malloc/free, no C reference)
│   ├── an/  specs/
├── fatptr/          Mechanism microbenchmarks (representation size/copy/chase, written in this repository)
│   ├── an/  c/  specs/
├── sets/*.json      Set manifests: members + default scale + run count (paper.json = the paper's 14 benchmarks)
├── results/         Generated: <set>.{md,csv}, asan.{md,csv}, asan-samples.txt, alloc/partial
├── bench_three_way.py Three-state runner (C / raw / fat)
├── bench_asan.py    ASan cross-comparison runner (C plain / C ASan / YIAN fat)
├── bench_allocator.py Allocator runner (fat / raw)
├── SUITES.md        Source and license registry
└── README.md
```

The current sources and licenses are registered in `bench/SUITES.md`.

The 22 C/YIAN pairs are reviewed against the
[translation specification](../docs/c-to-yian.md): 17 programs with scale
parameters read argv, the five large AWFY business functions are ordinary
functions, and BG uses the same output and exit codes as C. Static mutable state
is held in heap objects owned by the entry point: permute/storage counters are
reused across work units; fasta's seed/checksum/probability tables are on the
heap; CD shares two address markers across all frames; spectralnorm uses three
heap arrays. CD's motion_find_intersection returns 0/1 and writes the collision
point through out; DeltaBlue's Constraint_satisfy returns bool and writes a
nullable constraint through overridden_out, preserving the successful case where
no constraint was replaced. The [review checklist](../docs/c-to-yian-review.md)
lists only situations the rules do not cover. call_abi's call boundary is an
experimental requirement already covered by the specification; its native -O2
inlining issue remains, so it is not evidence of the actual three-state call
cost. The existing results/ and paper samples are historical measurements and
have not been rerun against the current rewritten sources; all 22 pairs compile
raw and fat from the same .an sources.

These commands compile, run, and measure selected benchmarks and are not part of
default local acceptance. Before running repository validation, check the set,
benchmark count, scale, and sample count; performance measurement requires
explicit authorization.

## Sets

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

| Set | Contents | Default scale | Purpose |
| --- | --- | --- | --- |
| `full` | All three-state benchmarks | `full` | Formal baseline and external reference |
| `fast` | 8 representative benchmarks | `fast` | Representative subset at reduced scale; duration depends on the machine |
| `ptr` | tag `ptr` | `full` | Pointers and object graphs |
| `numeric` | tag `numeric` | `full` | Numeric and loop changes |
| `string` | tag `string` | `full` | String and I/O changes |
| `alloc-heavy` | tag `alloc` + storage/json | `full` | Allocation-heavy but still three-state |
| `micro` | tag `micro` (three fatptr benchmarks) | `full` | Representation and ABI size/check readings |

AWFY and BG members: AWFY has 14 benchmarks (bounce, cd, deltablue, havlak, json,
list, mand, nbody, permute, queen, richards, sieve, storage, towers); BG has 5
(binarytree, fann, fasta, revcomp, spectralnorm); fatptr has 3 (call_abi,
copy_struct, chase).

Sets are defined only in `bench/sets/`. A new set must state which mechanism
focus it covers (`ptr`/`numeric`/`string`/`alloc`/`control`; `control` is
tag-only and has no set), avoiding a combinatorial expansion of sources and
focuses.

## Scales

- **full** (default, formal records): the sources' default scale, one warmup plus
  five measured runs per state, taking the minimum.
- **fast** (frequent development runs): for benchmarks that declare `scale.fast`
  in `specs/<name>.json`, replace the number on the `// bench-scale` marker line
  with the fast value, generate a build copy under
  `build/bench/src/fast/<SOURCE>/`, and compile that copy; pass the same value to
  C, raw, and fat as argv so all three states do equal work. Other benchmarks use
  the same scale in both profiles. The fast profile does not trigger run
  reduction: one warmup plus three measured runs per state, taking the minimum.

| Benchmark | Marked scale value | full | fast |
| --- | --- | ---: | ---: |
| AWFY/queen | `ITER` (10 consecutive 8-queens solves per round) | 276923 | 65000 |
| AWFY/sieve | `ITER` (one full sieve per round) | 268657 | 59000 |
| ALLOC/churn_single | `rounds` | 1000 | 100 |
| ALLOC/churn_mixed | `rounds` | 20000 | 2000 |
| ALLOC/grow_free | `cycles` | 40 | 8 |
| ALLOC/grow_varied | `cycles` | 32 | 8 |
| fatptr/call_abi | `CALLS` (calls per round) | 4096 | 1024 |
| fatptr/copy_struct | `ROUNDS` | 1024 | 128 |
| fatptr/chase | `NODES` | 4000000 | 2000000 |

Unlisted benchmarks use the same scale in both profiles; the fast profile
controls duration through the representative subset and these scale overrides.
To calibrate a benchmark, add a `// bench-scale` marker line to the source and set
`scale.fast` in its spec; the fast target is 0.2–0.5 s per fat-mode benchmark, and
items over 1 s run at full scale. Reducing scale must not change the semantics of
internal assertions: scaling iteration counts as sieve does is safest; when the
problem size must be scaled, assertions and authoritative values must also become
functions of the scale.

Conclusions at different scales must be interpreted separately; cross-scale
comparisons should verify workload, cache behavior, and sampling conditions.

## Three-state protocol

| State | Compile/run |
| --- | --- |
| C | `clang -O2 -ffp-contract=off bench/<SOURCE>/c/<name>.c -lm` (argv in the spec) |
| raw | `yianc -O2 --raw-pointers lib/src <src>` + libc `malloc`/`free` |
| fat | `yianc -O2 lib/src <src>` (fat pointers are the default) |

Both runners invoke only native `yianc`, selecting in the order `--yianc PATH`,
nonempty `YIANC`, then `PATH`, and rejecting scripts, directories, non-executable
files, and non-ELF files. Reports record the canonical compiler path, SHA-256, and
selection source. `bench_allocator.py --compiler-root PATH` only sets the
standard-library and input source root; it does not select the compiler.

- Artifacts: `build/bench/{cbin,yraw,yfat}/<SOURCE>_<name>` (all three states use
  equal-length paths; absolute values for allocation-heavy benchmarks are
  sensitive to argv[0] length and process layout).
- Sampling: one warmup per state (excluded from samples), then N measured rounds
  rotating C->raw->fat; formal metrics use each state's minimum, while recording
  median/IQR/CV/peak RSS (`/usr/bin/time -v`).
- Noise reduction: `taskset -c <cpu>` pinning; round-robin measurement reduces
  frequency/thermal drift across time.
- Semantic guard: C warmup must pass the spec's `check` (`rc` / `stdout_eq` /
  `stdout_contains` / `stdout_int_mod`); raw and fat warmup stdout and exit codes
  must match byte for byte, and each must pass the same spec's output/exit checks
  (for example fann is 51). A `no` in the report makes that benchmark's ratios
  unusable.
- **No regression thresholds**: performance experiments are reference only; no
  threshold scripts are written. Noise magnitude depends on the sampling
  environment; judging changes requires repeated measurements and the sample
  distribution.
- Translation follows the
  [C → YIAN syntax rewrite rules](../docs/c-to-yian.md); workload review is part
  of that specification. Existing result tables are runner observations and do
  not mean the draft has passed item-by-item review.
- **Review the run scope first**: bench commands compile and run the selected
  workloads, including warmup and repeated sampling; benchmarks measure
  performance and are not default local tests. Repository tasks must define the
  selection and measurement scope and obtain authorization before running.

## ASan cross-comparison

`bench_asan.py` measures the C plain, C ASan
(`ASAN_OPTIONS=detect_leaks=0`) and YIAN fat legs adjacently in one session;
`binarytree` also has a `quarantine_size_mb=0` sensitivity leg. Each leg has one
warmup plus N measured runs; output includes medians, IQR, min–max, peak RSS,
per-benchmark ratios for the three comparisons, and geometric means:

- (i) C ASan / C plain (ASan's own overhead)
- (ii) C ASan / YIAN fat (cross-toolchain)
- (iii) YIAN fat / C plain (full stack versus uninstrumented C)

```bash
python3 bench/bench_asan.py --set paper --runs 5 --pin 4
python3 bench/bench_asan.py --bench BG/fasta --runs 1 --pin 0
python3 bench/bench_asan.py --set paper --runs 5 --pin 4
```

Results are written to `results/asan.md`, `results/asan.csv`, and
`results/asan-samples.txt` (raw `wall_ms (peak_rss_kb)` samples, comparable with
the frozen samples under the paper's data/). `--set paper` selects the paper's 14
benchmarks; the semantic guard requires C warmup to pass the spec's `check`, ASan
stdout/exit codes to match C plain, and the YIAN leg to pass the same spec's
output/exit checks.

`BG/binarytree` fixes `N=16`: ASan takes about 2.5 s with peak RSS about 0.7 GB.
The N=20 ASan configuration exceeds 9 GB peak RSS and 4 minutes per run, so it is
not used. `--fast` can still lower individual benchmarks that declare
`scale.fast`.

## Allocator suite

```bash
python3 bench/bench_allocator.py --scale full --pin 4    # Full scale: seven samples per mode.
python3 bench/bench_allocator.py --scale fast --pin 4    # Fast scale: three samples per mode at reduced scale.
python3 bench/bench_allocator.py --scale fast --yianc "$HOME/.local/bin/yianc"
```

The four `bench/ALLOC` benchmarks have no C reference; the comparison state is
libc `malloc`/`free` (raw), so this is a two-state comparison: same-size churn,
mixed-size churn, grow-free, and varied-size grow-free. Results are written to
`bench/results/alloc.{md,csv}`.

## Result archive

Only the full profile and allocator records (`full.{md,csv}`, `alloc.{md,csv}`)
are versioned under `bench/results/`; other sets
(`ptr`/`numeric`/`string`/`alloc-heavy`/`micro`) and temporary subsets
(`partial.*`) are excluded by `.gitignore`. The dates, commits, and environment
fingerprints in a report define the scope of that measurement and do not
represent current source performance.

## Paper reproduction

`scripts/rerun_paper_experiments.sh` runs `scripts/run_cve.py --all` (38 YIAN
vulnerability projects), three-state performance (`--set paper`), and the ASan
comparison (`--set paper`) in order, with output matching the individual
runners; command output is also written to
`build/paper-rerun/<timestamp>/run.log`.

CVE projects live in [`cve/`](../cve/README.md) and are independent of the
compiler safety regressions in `tests/safety/`. C witnesses use separate CMake
projects; the C ASan comparison must explicitly select `--language c` or `both`
and is not counted among the paper's 38 YIAN vulnerability runs.

```bash
scripts/rerun_paper_experiments.sh                       # runs=5, pin=4
scripts/rerun_paper_experiments.sh --runs 3 --pin 0      # no pinning, 3 runs per leg
scripts/rerun_paper_experiments.sh --skip-cve --bench BG/fasta --runs 1
scripts/rerun_paper_experiments.sh --refresh-data        # call the parent repository's refresh_data.py at the end
```

`--bench` restricts both runners; `--skip-cve`/`--skip-perf`/`--skip-asan` skip
the corresponding phase. The `SECL_RUNS`, `SECL_PIN`, and `YIAN_PYTHON`
environment variables can override the defaults.

## Adding a benchmark

1. Put it in the matching source directory (create
   `bench/<SOURCE>/{an,c,specs}` for a new source);
2. `an/<name>.an` (add `<name>.raw.an` when raw-mode semantics differ),
   `c/<name>.c` (review against the specification above), and
   `specs/<name>.json` (`argv`, `check`, `tags`, optional `scale`);
3. Register the source and scale alignment in `bench/README.md`; run all three
   states and confirm the C authoritative value.

The source and license registry is in `bench/SUITES.md`.
