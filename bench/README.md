# Benchmark projects

Projects are organized by source and selected through set manifests. Each
three-state project has a CMake C reference and an anx YIAN package; the runner
builds C, raw YIAN, and fat YIAN from the same project inputs. The four
`ALLOC` projects instead compare fat YIAN with libc allocation and have no C
reference.

## Layout

```text
bench/
├── AWFY/<name>/       14 Are We Fast Yet projects
├── BG/<name>/         10 official Benchmarks Game projects, source and oracle provenance
├── fatptr/<name>/      3 representation/call/copy projects written here
├── ALLOC/<name>/       4 allocator projects, fat/raw only
├── native/             fixed GMP and PCRE2 packages, source, licenses, and rebuild material
├── sets/*.json         full (27), bg (10), paper (24), fast (8), and focus selections
├── results/            plain numeric performance CSV files
├── projects.py         project discovery, dependency staging, build, and digest
├── execution.py        controlled file-based stdin/stdout and output identity
├── bench_three_way.py  C / raw / fat runner
├── bench_asan.py       C plain / C ASan / YIAN fat runner
├── bench_allocator.py  fat / libc allocator runner
└── SUITES.md           source and license registry
```

Each paired project contains `spec.json`, `yian/package.anx` and
`yian/src/main.an`, plus `c/CMakeLists.txt` and C source. Optional raw-mode
overrides live under `raw/`. The ten BG projects also contain official-source
archives, licenses, fixture files, and `provenance.json`. CMake and anx select
the same project-local static archive for pidigits (GMP) and regexredux
(PCRE2); those libraries are shared by all states but are not ASan-instrumented
or covered by YIAN guards. See [`native/README.md`](native/README.md).

The suite contains 31 projects: 27 C/YIAN pairs (14 AWFY, 10 BG, and 3 fatptr)
plus 4 two-state allocator projects. `full` selects all 27 pairs. `bg` selects
the ten official BG tasks; `paper` selects all 14 AWFY and all ten BG projects,
and `fast` remains eight IDs. Set membership and task scales
are in `sets/*.json` and each project's `spec.json`.

The paired projects are reviewed against the
[C → YIAN translation specification](../docs/c-to-yian.md). AWFY retains its
documented state and output-parameter rewrites. Each BG project records the
mapping of every upstream C function,
source/license hashes, representation differences, and any small C portability
patch. `call_abi` still has an -O2 inlining issue, so it is not evidence of the
three-state cost of the call boundary.

The frozen paper data records the measurements used by the manuscript.
Updating benchmark sources does not refresh those measurements.

These commands compile, run, and measure selected benchmarks and are not part of
default local acceptance. Before running repository validation, check the set,
benchmark count, scale, and sample count; performance measurement requires
explicit authorization.

## Sets

```bash
python3 bench/bench_three_way.py --list-sets           # List sets, scales, and member counts.
python3 bench/bench_three_way.py --set full            # Full scale: all 27 paired projects.
python3 bench/bench_three_way.py --set bg              # All ten official BG tasks.
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
| `full` | All 27 C/YIAN pairs | `full` | Complete paired-project selection |
| `bg` | All 10 BG tasks | `full` | Official Benchmarksgame project selection |
| `fast` | 8 representative benchmarks | `fast` | Representative subset at reduced scale; duration depends on the machine |
| `ptr` | tag `ptr` | `full` | Pointers and object graphs |
| `numeric` | tag `numeric` | `full` | Numeric and loop changes |
| `string` | tag `string` | `full` | String and I/O changes |
| `alloc-heavy` | tag `alloc` + storage/json | `full` | Allocation-heavy but still three-state |
| `micro` | tag `micro` (three fatptr benchmarks) | `full` | Representation and ABI size/check readings |

AWFY has 14 projects (bounce, cd, deltablue, havlak, json, list, mand, nbody,
permute, queen, richards, sieve, storage, towers). BG has 10 (binarytree, fann,
fasta, knucleotide, mandelbrot, nbody, pidigits, regexredux, revcomp,
spectralnorm). `BG/nbody` and `AWFY/nbody` have distinct project IDs. The three
fatptr projects are call_abi, copy_struct, and chase.

Sets are defined only in `bench/sets/`. A new set must state which mechanism
focus it covers (`ptr`/`numeric`/`string`/`alloc`/`control`; `control` is
tag-only and has no set), avoiding a combinatorial expansion of sources and
focuses.

## Scales

- **full** (default, formal records): the sources' default scale, one warmup plus
  five measured runs per state, taking the minimum.
- **fast** (frequent development runs): for projects that declare `scale.fast`
  in `bench/<SOURCE>/<name>/spec.json`, stage an isolated copy under
  `build/bench/projects/fast/<SOURCE>/<name>/<state>/` and replace the root
  YIAN `// bench-scale` marker. Numeric BG projects receive the same selected
  dimension once as C argv. Other projects use their declared full dimension.
  The fast profile does not trigger run
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
| C | Project CMake build, `clang -O2 -ffp-contract=off`, target `bench_app` |
| raw | anx project build with native yianc `-O2 --raw-pointers` |
| fat | anx project build with native yianc `-O2` (fat pointers are the default) |

The runners select native `anx` through `--anx PATH`, nonempty `ANX`, then `PATH`;
anx selects native `yianc` through `--yianc PATH`, nonempty `YIANC`, then `PATH`.
Both reject scripts, directories, non-executable files, and non-ELF files.
`bench_allocator.py --compiler-root PATH` only sets the
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
  must match byte for byte, and each must pass the same spec's output/exit checks.
  A failed check stops export of that measurement.
- **No regression thresholds**: performance experiments are reference only; no
  threshold scripts are written. Noise magnitude depends on the sampling
  environment; judging changes requires repeated measurements and the sample
  distribution.
- BG input files are generated once before the consumer legs; each run opens a
  fresh copy of the same input and writes binary output to a file. File hashing
  and byte comparison are outside the measured interval.
- Translation follows the
  [C → YIAN syntax rewrite rules](../docs/c-to-yian.md); each BG README maps its
  C functions to YIAN and records representation differences. A small oracle
  probe and C comparison do not prove equivalence at every scale.
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

GMP and PCRE2 are the same fixed project-local static libraries in all legs.
They are not ASan-instrumented; allocations intercepted by ASan do not make
library code instrumented, and library operations are outside YIAN guard coverage.

```bash
python3 bench/bench_asan.py --set paper --runs 5 --pin 4
python3 bench/bench_asan.py --bench BG/fasta --runs 1 --pin 0
```

Results are written to `results/asan.csv`, including every wall-time/RSS pair,
per-leg statistics, derived ratios and geometric means. `--set paper` selects the paper's
24 benchmarks; `--set full` selects all 27 paired projects. The semantic guard
requires C warmup to pass the spec's `check`, ASan stdout/exit codes to match C
plain, and the YIAN leg to pass the same spec's output/exit checks.

`BG/binarytree` declares its scale in `bench/BG/binarytree/spec.json`;
`--fast` can lower projects that declare a `scale.fast` value. ASan result files
from previous source revisions are historical and do not cover the ten current
BG projects as a batch.

## Allocator suite

```bash
python3 bench/bench_allocator.py --scale full --pin 4    # Full scale: seven samples per mode.
python3 bench/bench_allocator.py --scale fast --pin 4    # Fast scale: three samples per mode at reduced scale.
python3 bench/bench_allocator.py --scale fast --yianc "$HOME/.local/bin/yianc"
```

The four `bench/ALLOC` benchmarks have no C reference; the comparison state is
libc `malloc`/`free` (raw), so this is a two-state comparison: same-size churn,
mixed-size churn, grow-free, and varied-size grow-free. Results are written to
`bench/results/alloc.csv`.

## CSV data format

CSV files contain performance data only. Each workload/state row has the
benchmark name, state, number of samples, minimum/median/IQR/maximum time,
coefficient of variation, peak/median RSS, and performance ratios. Individual
observations are ordinary numbered columns: `time_ms_1`, `rss_kb_1`, and so on.
Times use milliseconds and RSS uses KiB. Raw values retain their precision;
derived statistics are displayed with up to six decimal places.

`min_over_c` and `min_over_raw` compare minimum times for the three-state or
allocator run. `median_over_c` and `median_over_yian` compare median times for
the ASan run. Rows named `geomean` contain aggregate ratios; their sample and
absolute-time/RSS cells are empty. Inapplicable ratios are empty cells.

Output files have no JSON cells, comments, source/tool/environment information,
parameters, hashes, validation records or report archives. Runtime output checks
remain in memory and failed measurements stop export. Temporary program output
and generated stdin files are cleaned when each benchmark runner exits.
The aggregate script writes no logs or backups.

`projects.py` builds projects, `execution.py` performs timed file I/O, and
`results_io.py` writes these tables. The current paper comparison is in
`results/paper.csv` and `results/asan.csv`; other selections write their own
latest performance CSV when explicitly run.

The entire `bench/results/` directory is Git-ignored. Results remain local to
the machine that produced them. The paper checkout's `data/` copies are also
Git-ignored; populate them from local measurements with `refresh_data.py`.

## Paper reproduction

`scripts/rerun_paper_experiments.sh` runs `scripts/run_cve.py --all` (38 YIAN
vulnerability projects), three-state performance (`--set paper`), and the ASan
comparison (`--set paper`) in order, with output matching the individual
runners.

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

1. Create `bench/<SOURCE>/<name>/` with `spec.json`, `yian/package.anx`, and
   `yian/src/main.an`; add `raw/src/main.an` only when raw mode needs an overlay.
2. Add `c/CMakeLists.txt` and the C reference for paired workloads. Add fixtures,
   source/license hashes, full function mapping, and translation differences for
   upstream workloads.
3. Register membership in any relevant set and document source/license details
   in `bench/SUITES.md`. Define an authorized local oracle probe before making
   broader execution or measurement claims.

The source and license registry is in `bench/SUITES.md`.
