#!/usr/bin/env python3
"""bench_three_way.py: measure C/raw/fat performance with the C reference as baseline.

Benchmarks are organized by source, focus set, and fast/full scale:

  bench/<SOURCE>/<name>/spec.json   Arguments, checks, tags, and scales
  bench/<SOURCE>/<name>/yian/       YIAN package; raw/ is an optional source overlay
  bench/<SOURCE>/<name>/c/          Authoritative CMake project
  bench/sets/<set>.json             Set members and default scale
  bench/results/<set>.csv      Performance samples and statistics

Protocol:
  - CMake builds the C target with C11, clang -O3, and -ffp-contract=off; anx builds staged
    YIAN package roots with yianc -O3, adding --raw-pointers in raw mode.
  - fast substitutes declared scale.fast values in an isolated YIAN package copy. BG numeric tasks pass the
    selected full/fast dimension to C as one argv value; BG stdin tasks use the selected FASTA input file.
  - Artifacts use equal-length paths under build/bench/{cbin,yraw,yfat}/<SOURCE>_<name>.
  - CPU affinity via taskset -c <cpu> is optional, but required for formal records.
  - Warm up each mode the spec's fixed number of times, then rotate C/raw/fat
    for its fixed measured rounds. Report minimum time, median, interquartile
    range, CV, and peak RSS per mode.
  - Semantic guards: every C warmup must pass its rc check. BG warmups and every
    measured sample must also match the C warmup's complete stdout bytes and
    expected rc. A failed sample stops export of that measurement.

Usage:
  python3 bench/bench_three_way.py --list-sets          # List sets, scales, and member counts
  python3 bench/bench_three_way.py --set full           # Default: full scale, formal records
  python3 bench/bench_three_way.py --set fast           # Fast development scale
  python3 bench/bench_three_way.py --set ptr --scale full
  python3 bench/bench_three_way.py --bench AWFY/queen,BG/fasta
  python3 bench/bench_three_way.py --source AWFY --set fast
  python3 bench/bench_three_way.py --set fast --yianc /path/to/yianc
  python3 bench/bench_three_way.py --set fast --anx /path/to/anx
  python3 bench/bench_three_way.py --compile-only | --no-compile | --pin 4

Output:
  bench/results/<set>.csv   Named-set performance data
  bench/results/partial.csv Temporary --bench/--source subsets, leaving set results intact

Standalone script: does not alter scripts/run_tests.py or sources; fast copies stay under build/.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import shutil
import statistics
import sys
import time
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from _toolchain.native import resolve_native_selection

try:
    from . import execution, projects, results_io
    from .spec import BenchSpec, SCALE_PROFILES, load_spec
except ImportError:  # Direct script execution puts bench/ on sys.path.
    import execution
    import projects
    import results_io
    from spec import BenchSpec, SCALE_PROFILES, load_spec

ROOT = Path(__file__).resolve().parent.parent
BENCH_DIR = ROOT / "bench"
SETS_DIR = BENCH_DIR / "sets"
RESULTS_DIR = BENCH_DIR / "results"
OUT_DIR = ROOT / "build" / "bench"
PARTIAL_CSV = RESULTS_DIR / "partial.csv"

DEFAULT_SET = "full"

@dataclass(frozen=True)
class SetDef:
    """Set: member patterns (exact key / SOURCE/* / tag:x / *) with the default scale."""

    name: str
    scale: str
    include: tuple[str, ...]
    exclude: tuple[str, ...]
    note: str


@dataclass(frozen=True)
class Stats:
    med: float
    iqr: float
    cv: float
    min: float
    max: float
    samples: list[float]


@dataclass
class Row:
    spec: BenchSpec
    times: dict[str, Stats]
    rss_mb: dict[str, float]
    rss_samples: dict[str, list[int]]
    runs: int
    yian_stdout_match: bool
    c_check_ok: bool
    exit_codes: dict[str, int]


def _load_json(path: Path, what: str) -> dict[str, object]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"[{what}] failed to read {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise SystemExit(f"[{what}] {path}: top level must be a JSON object")
    return data


def _as_str_list(value: object, where: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise SystemExit(f"[spec] {where}: expected a string array, got {value!r}")
    return tuple(value)


def discover() -> list[BenchSpec]:
    """Discover paired projects under bench/<SOURCE>/<name>.

    Sources without c/, such as allocator-only bench/ALLOC, are handled by bench_allocator.py.
    """
    try:
        projects_found = projects.discover_projects(BENCH_DIR)
    except ValueError as error:
        raise SystemExit(f"[discover] {error}") from error
    specs = [
        load_spec(project.spec_path, project)
        for project in projects_found
        if project.c is not None
    ]
    if not specs:
        raise SystemExit(f"[discover] no benchmarks with a C reference and spec under {BENCH_DIR}")
    return specs


def load_sets() -> dict[str, SetDef]:
    """Read bench/sets/*.json."""
    if not SETS_DIR.is_dir():
        raise SystemExit(f"[sets] missing directory {SETS_DIR}")
    sets: dict[str, SetDef] = {}
    for path in sorted(SETS_DIR.glob("*.json")):
        data = _load_json(path, "sets")
        name = str(data.get("name", path.stem))
        scale = str(data.get("scale", "full"))
        if scale not in SCALE_PROFILES:
            raise SystemExit(f"[sets] {path}: scale must be one of {'/'.join(SCALE_PROFILES)}")
        sets[name] = SetDef(
            name=name,
            scale=scale,
            include=_as_str_list(data.get("include", ["*"]), f"{path}: include"),
            exclude=_as_str_list(data.get("exclude"), f"{path}: exclude"),
            note=str(data.get("note", "")),
        )
    if DEFAULT_SET not in sets:
        raise SystemExit(f"[sets] missing default set {DEFAULT_SET}")
    return sets


def _match(pattern: str, spec: BenchSpec) -> bool:
    if pattern == "*":
        return True
    if pattern.startswith("tag:"):
        return pattern[4:] in spec.tags
    if pattern.endswith("/*"):
        return spec.source == pattern[:-2]
    return pattern == spec.key or pattern == spec.name


def resolve_set(
    specs: list[BenchSpec],
    set_def: SetDef,
    bench_filter: tuple[str, ...] = (),
    source_filter: tuple[str, ...] = (),
) -> list[BenchSpec]:
    """Expand set patterns into benchmarks; reject patterns matching nothing to catch misspellings."""
    for pattern in (*set_def.include, *set_def.exclude):
        if not any(_match(pattern, spec) for spec in specs):
            raise SystemExit(f"[select] set {set_def.name} member {pattern!r} matched no benchmark")
    chosen = [
        spec
        for spec in specs
        if any(_match(pattern, spec) for pattern in set_def.include)
        and not any(_match(pattern, spec) for pattern in set_def.exclude)
    ]
    if source_filter:
        chosen = [spec for spec in chosen if spec.source in source_filter]
    if bench_filter:
        wanted = set(bench_filter)
        unknown = sorted(item for item in wanted if not any(_match(item, spec) for spec in specs))
        if unknown:
            raise SystemExit(f"[select] unknown benchmark(s): {', '.join(unknown)}")
        chosen = [spec for spec in chosen if spec.key in wanted or spec.name in wanted]
    if not chosen:
        raise SystemExit("[select] selected no benchmarks (check --set/--scale/--source/--bench)")
    return sorted(chosen, key=lambda spec: spec.key)


def spec_bin(spec: BenchSpec, state: str) -> Path:
    return OUT_DIR / STATE_DIR[state] / f"{spec.source}_{spec.name}"


def compile_c(spec: BenchSpec) -> Path:
    """Build the project's plain CMake target; stop on failure."""
    binary = spec_bin(spec, "c")
    try:
        return projects.build_c(spec.project, asan=False, output=binary, optimization=OPT_LEVEL)
    except (ValueError, RuntimeError) as error:
        raise SystemExit(f"[compile] {spec.key} (c) failed: {error}") from error


def compile_an(
    spec: BenchSpec, profile: str, raw: bool, compiler: Path, *, anx: Path | None = None
) -> Path:
    """Build one isolated YIAN package mode through the shared project builder."""
    state = "raw" if raw else "fat"
    binary = spec_bin(spec, state)
    try:
        anx_path = anx
        if anx_path is None:
            anx_path, _ = _resolve_anx_selection(None)
        return projects.build_yian(
            spec.project, profile, raw, spec.scale_value(profile), compiler, anx_path, ROOT, binary,
            optimization=OPT_LEVEL,
        )
    except (ValueError, RuntimeError) as error:
        raise SystemExit(f"[compile] {spec.key} ({state}) failed: {error}") from error


OPT_LEVEL = "-O3"
C_COMPILER = "clang"
C_FLAGS = ["-O3", "-ffp-contract=off"]
TIME_BIN = "/usr/bin/time"
STATES = ("c", "raw", "fat")
STATE_DIR = {"c": "cbin", "raw": "yraw", "fat": "yfat"}

_RSS_RE = re.compile(r"Maximum resident set size \(kbytes\): (\d+)")


def _resolve_anx_selection(
    explicit: str | None, *, search_path: str | None = None
) -> tuple[Path, str]:
    selected, source = resolve_native_selection(
        explicit=explicit, configured=os.environ.get("ANX"), default="anx", search_path=search_path
    )
    return selected, "ANX" if source == "YIANC" else source


def _env_plain() -> dict[str, str]:
    env = os.environ.copy()
    env["LANG"] = "C"
    return env


def _run(binary: Path, argv: list[str], pin: int | None) -> tuple[float, int, str, int]:
    """Run once and return (wall time ms,peak RSS KB,stdout,exit code)."""
    pre = ["taskset", "-c", str(pin)] if pin is not None else []
    t0 = time.monotonic()
    res = subprocess.run(
        pre + [TIME_BIN, "-v", str(binary), *argv],
        capture_output=True,
        text=True,
        env=_env_plain(),
    )
    wall_ms = (time.monotonic() - t0) * 1000.0
    match = _RSS_RE.search(res.stderr)
    if match is None:
        raise SystemExit(
            f"[run] {binary}: cannot parse Maximum resident set size from /usr/bin/time -v output:\n"
            f"{res.stderr}"
        )
    return wall_ms, int(match.group(1)), res.stdout, res.returncode


def _check_file_result(spec: BenchSpec, result: execution.RunResult) -> bool:
    """Check a file result without decoding BG's complete stdout."""
    if spec.source == "BG" and spec.io is not None:
        return result.rc == spec.check.rc
    try:
        output = result.stdout.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return False
    return spec.check.ok(result.rc, output)


def _stats(samples: list[float]) -> Stats:
    ordered = sorted(samples)
    try:
        qs = statistics.quantiles(ordered, n=4, method="exclusive")
        iqr = qs[2] - qs[0]
    except statistics.StatisticsError:
        iqr = ordered[-1] - ordered[0]
    mean = statistics.fmean(ordered)
    try:
        cv = statistics.stdev(ordered) / mean * 100.0 if mean > 0 else 0.0
    except statistics.StatisticsError:  # Single sample.
        cv = 0.0
    return Stats(
        med=statistics.median(ordered),
        iqr=iqr,
        cv=cv,
        min=ordered[0],
        max=ordered[-1],
        samples=list(samples),
    )


def measure_triple(spec: BenchSpec, pin: int | None, profile: str) -> Row:
    """Run the spec's fixed warmups, then rotate C/raw/fat for its measured runs.

    All modes share argv, including the optional workload scale. Sampling counts
    come from spec.json; no command-line or set-level override is accepted.
    """
    argv = list(spec.run_argv(profile))
    bins = {state: spec_bin(spec, state) for state in STATES}
    warmups = spec.measurement.warmups
    runs = spec.measurement.runs

    if spec.source == "BG" and spec.io is not None:
        # Prepare or validate the FASTA input before the consumer timing window.
        stdin_path = execution.prepare_input(spec, profile, OUT_DIR / "inputs")
        output_root = OUT_DIR / "file-runs" / "three-way" / spec.source / spec.name / profile
        output_root.mkdir(parents=True, exist_ok=True)

        def run_file(state: str, kind: str) -> execution.RunResult:
            output = output_root / f"{kind}-{state}.stdout"
            return execution.run_file(bins[state], tuple(argv), stdin_path, output, pin)

        c_check_ok = True
        yian_stdout_match = True
        warm_files: dict[str, execution.RunResult] = {}
        for _ in range(warmups):
            warm_files = {state: run_file(state, "warm") for state in STATES}
            c_reference = warm_files["c"]
            c_check_ok = c_check_ok and _check_file_result(spec, c_reference)
            yian_stdout_match = yian_stdout_match and all(
                _check_file_result(spec, warm_files[state])
                and execution.output_equal(warm_files[state], c_reference)
                for state in ("raw", "fat")
            )

        samples: dict[str, list[float]] = {state: [] for state in STATES}
        rss: dict[str, list[int]] = {state: [] for state in STATES}
        for _ in range(runs):
            for state in STATES:
                result = run_file(state, "sample")
                samples[state].append(result.wall_ms)
                rss[state].append(result.rss_kb)
                sample_ok = _check_file_result(spec, result) and execution.output_equal(result, c_reference)
                if state == "c":
                    c_check_ok = c_check_ok and sample_ok
                else:
                    yian_stdout_match = yian_stdout_match and sample_ok

        return Row(
            spec=spec,
            times={state: _stats(samples[state]) for state in STATES},
            rss_mb={state: max(rss[state]) / 1024.0 for state in STATES},
            rss_samples=rss,
            runs=runs,
            yian_stdout_match=yian_stdout_match,
            c_check_ok=c_check_ok,
            exit_codes={state: warm_files[state].rc for state in STATES},
        )

    c_check_ok = True
    yian_stdout_match = True
    warm: dict[str, tuple[float, int, str, int]] = {}
    for _ in range(warmups):
        warm = {state: _run(bins[state], argv, pin) for state in STATES}
        c_check_ok = c_check_ok and spec.check.ok(warm["c"][3], warm["c"][2])
        yian_stdout_match = yian_stdout_match and (
            warm["raw"][2] == warm["fat"][2]
            and warm["raw"][3] == warm["fat"][3]
            and all(spec.check.ok(warm[state][3], warm[state][2]) for state in ("raw", "fat"))
        )

    if not spec.check.ok(warm["c"][3], warm["c"][2]):
        print(
            f"[{spec.key}] warning: C baseline failed the authoritative check "
            f"(exit={warm['c'][3]}, stdout={warm['c'][2].strip()!r}); C ratios for this benchmark are unusable",
            file=sys.stderr,
        )
    if not yian_stdout_match:
        print(
            f"[{spec.key}] warning: the two YIAN states' warmup results disagree or failed spec.check "
            f"(raw exit={warm['raw'][3]}, fat exit={warm['fat'][3]})",
            file=sys.stderr,
        )
    for state in ("raw", "fat"):
        if warm[state][3] != spec.check.rc:
            print(
                f"[{spec.key}] warning: {state} warmup exit code {warm[state][3]} (expected {spec.check.rc})",
                file=sys.stderr,
            )

    samples: dict[str, list[float]] = {state: [] for state in STATES}
    rss: dict[str, list[int]] = {state: [] for state in STATES}
    for _ in range(runs):
        for state in STATES:
            wall, peak, _, _ = _run(bins[state], argv, pin)
            samples[state].append(wall)
            rss[state].append(peak)

    return Row(
        spec=spec,
        times={state: _stats(samples[state]) for state in STATES},
        rss_mb={state: max(rss[state]) / 1024.0 for state in STATES},
        rss_samples=rss,
        runs=runs,
        yian_stdout_match=yian_stdout_match,
        c_check_ok=c_check_ok,
        exit_codes={state: warm[state][3] for state in STATES},
    )

def _ratio(row: Row, num: str, den: str) -> float:
    denom = row.times[den].min
    return row.times[num].min / denom if denom else float("nan")


def _ratio_usable(row: Row) -> bool:
    """Ratios require the recorded C/YIAN semantic checks to pass."""
    return row.c_check_ok and row.yian_stdout_match


def write_csv(rows: list[Row], path: Path) -> None:
    if any(not _ratio_usable(row) for row in rows):
        raise ValueError("benchmark execution failed its checks; performance CSV was not replaced")
    records = [results_io.make_record(row.spec.key, state, row.times[state].samples, row.rss_samples[state])
               for row in rows for state in STATES]
    results_io.write_csv(path, records)


def __run() -> int:
    parser = argparse.ArgumentParser(description="C / raw / fat three-state performance measurement (C as baseline)")
    parser.add_argument("--set", dest="bench_set", default=DEFAULT_SET,
                        help=f"set name (default {DEFAULT_SET}; see --list-sets)")
    parser.add_argument("--scale", choices=SCALE_PROFILES,
                        help="scale profile (default: the set's scale)")
    parser.add_argument("--source", help="only measure the named sources (comma-separated, e.g. AWFY,BG)")
    parser.add_argument("--bench", dest="bench_keys",
                        help="only measure the named benchmarks (SOURCE/name or name, comma-separated)")
    parser.add_argument("--names", dest="bench_keys", help=argparse.SUPPRESS)  # Compatibility option aliases.
    parser.add_argument("--list-sets", action="store_true", help="list sets and member counts, then exit")
    parser.add_argument("--pin", type=int, help="CPU number for taskset pinning")
    parser.add_argument("--yianc", metavar="PATH", help="Native compiler (default: nonempty YIANC, then PATH)")
    parser.add_argument("--anx", metavar="PATH", help="Native project builder (default: nonempty ANX, then PATH)")
    parser.add_argument("--no-compile", action="store_true", help="reuse existing binaries without recompiling")
    parser.add_argument("--compile-only", action="store_true", help="compile only, do not measure")
    args = parser.parse_args()

    specs = discover()
    sets = load_sets()

    if args.list_sets:
        for set_def in sorted(sets.values(), key=lambda item: item.name):
            members = resolve_set(specs, set_def)
            print(
                f"{set_def.name:12s} scale={set_def.scale:4s} members={len(members):2d}  {set_def.note}"
            )
        return 0

    try:
        compiler, compiler_source = resolve_native_selection(
            explicit=args.yianc,
            configured=os.environ.get("YIANC"),
            default="yianc",
        )
        anx, anx_source = _resolve_anx_selection(args.anx)
    except ValueError as error:
        parser.error(str(error))

    if args.bench_set not in sets:
        raise SystemExit(
            f"[args] unknown set {args.bench_set!r}; available: {', '.join(sorted(sets))}"
        )
    set_def = sets[args.bench_set]
    profile = args.scale or set_def.scale
    bench_filter = tuple(item.strip() for item in (args.bench_keys or "").split(",") if item.strip())
    source_filter = tuple(item.strip() for item in (args.source or "").split(",") if item.strip())
    chosen = resolve_set(specs, set_def, bench_filter, source_filter)
    print(
        f"[bench] set {set_def.name} scale {profile} benchmarks {len(chosen)} "
        f"(per-spec warmups/runs)",
        file=sys.stderr,
    )
    if not args.no_compile:
        for spec in chosen:
            compile_c(spec)
            compile_an(spec, profile, raw=False, compiler=compiler, anx=anx)
            compile_an(spec, profile, raw=True, compiler=compiler, anx=anx)
            scale_value = spec.scale_value(profile)
            scale_note = f" scale={scale_value}" if scale_value is not None else ""
            print(f"[compile] {spec.key} ok{scale_note}", file=sys.stderr)
    if args.compile_only:
        return 0

    rows: list[Row] = []
    for spec in chosen:
        row = measure_triple(spec, args.pin, profile)
        rows.append(row)
        scale_value = spec.scale_value(profile)
        scale_note = f" scale={scale_value}" if scale_value is not None else ""
        ratio_note = (
            f"raw/C {_ratio(row, 'raw', 'c'):.2f}x fat/C {_ratio(row, 'fat', 'c'):.2f}x"
            if _ratio_usable(row)
            else "ratios unavailable (semantic check failed)"
        )
        print(
            f"[measure] {spec.key}{scale_note}: C {row.times['c'].min:.1f}ms "
            f"raw {row.times['raw'].min:.1f}ms fat {row.times['fat'].min:.1f}ms "
            f"{ratio_note} "
            f"({row.runs} runs, {spec.measurement.warmups} warmups)",
            file=sys.stderr,
        )

    csv_path = PARTIAL_CSV if bench_filter or source_filter else RESULTS_DIR / f"{set_def.name}.csv"
    write_csv(rows, csv_path)
    print(f"[write] {csv_path.relative_to(ROOT)}", file=sys.stderr)
    return 0


def main() -> int:
    try:
        return __run()
    finally:
        shutil.rmtree(OUT_DIR / "file-runs" / "three-way", ignore_errors=True)
        shutil.rmtree(OUT_DIR / "inputs", ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
