#!/usr/bin/env python3
"""bench_three_way.py: measure C/raw/fat performance with the C reference as baseline.

Benchmarks are organized by source, focus set, and fast/full scale:

  bench/<SOURCE>/an/<name>.an       Fat source; <name>.raw.an is an optional raw override
  bench/<SOURCE>/c/<name>.c         Authoritative C reference
  bench/<SOURCE>/specs/<name>.json  Arguments, authoritative checks, focus tags, and scales
  bench/sets/<set>.json             Set members, default scale, and sample count
  bench/results/<set>.{md,csv}      Generated results with environment fingerprint and commit

Protocol:
  - Same algorithm/scale: C uses clang -O2 -ffp-contract=off -lm bench/<SOURCE>/c/<name>.c;
    raw uses yianc -O2 --raw-pointers lib/src <src>; fat uses yianc -O2 lib/src <src>.
  - fast substitutes declared scale.fast values at // bench-scale source markers, generating
    build/bench/src/<profile>/<SOURCE>/<name>.an. Pass the same scale as C argv when
    scale.c == 'argv'. Benchmarks without a fast declaration use the same scale in both profiles.
  - Artifacts use equal-length paths under build/bench/{cbin,yraw,yfat}/<SOURCE>_<name>.
  - CPU affinity via taskset -c <cpu> is optional, but required for formal records.
  - Warm up each mode once, then rotate C/raw/fat for N measured rounds. Report minimum time,
    median, interquartile range, CV, and peak RSS per mode.
  - Semantic guards: C warmup must pass authoritative spec checks; raw/fat warmup stdout and exit
    codes must match byte-for-byte and both exits must be zero. Otherwise ratios are unavailable.

Usage:
  python3 bench/bench_three_way.py --list-sets          # List sets, scales, and member counts
  python3 bench/bench_three_way.py --set full           # Default: full scale, formal records
  python3 bench/bench_three_way.py --set fast           # Fast development scale
  python3 bench/bench_three_way.py --set ptr --scale full
  python3 bench/bench_three_way.py --bench AWFY/queen,BG/fasta
  python3 bench/bench_three_way.py --source AWFY --set fast
  python3 bench/bench_three_way.py --set fast --yianc /path/to/yianc
  python3 bench/bench_three_way.py --compile-only | --no-compile | --runs N | --pin 4

Output:
  bench/results/<set>.{md,csv}   Named-set results with fingerprint/protocol/commit
  bench/results/partial.{md,csv} Temporary --bench/--source subsets, leaving set results intact

Standalone script: does not alter scripts/run_tests.py or sources; fast copies stay under build/.
"""

from __future__ import annotations

import argparse
import datetime
import json
import math
import os
import platform
import re
import subprocess
import statistics
import sys
import time
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from _toolchain.native import file_sha256, resolve_native_selection

ROOT = Path(__file__).resolve().parent.parent
LIB = ROOT / "lib" / "src"
BENCH_DIR = ROOT / "bench"
SETS_DIR = BENCH_DIR / "sets"
RESULTS_DIR = BENCH_DIR / "results"
OUT_DIR = ROOT / "build" / "bench"
SRC_BUILD_DIR = OUT_DIR / "src"
PARTIAL_MD = RESULTS_DIR / "partial.md"
PARTIAL_CSV = RESULTS_DIR / "partial.csv"

RESERVED_DIRS = {"sets", "results"}
DEFAULT_SET = "full"
SCALE_PROFILES = ("fast", "full")
SCALE_MARKER = "// bench-scale"


@dataclass(frozen=True)
class Check:
    """Authoritative C result declared in bench/<SOURCE>/specs/<name>.json."""

    rc: int = 0
    stdout_eq: str | None = None
    stdout_contains: tuple[str, ...] = ()
    stdout_int_mod: tuple[int, int] | None = None

    def ok(self, rc: int, out: str) -> bool:
        if rc != self.rc:
            return False
        text = out.strip()
        if self.stdout_eq is not None and text != self.stdout_eq:
            return False
        for needle in self.stdout_contains:
            if needle not in out:
                return False
        if self.stdout_int_mod is not None:
            modulus, expected = self.stdout_int_mod
            try:
                value = int(text)
            except ValueError:
                return False
            if value % modulus != expected:
                return False
        return True

    def describe(self) -> str:
        parts = [f"rc=={self.rc}"]
        if self.stdout_eq is not None:
            parts.append(f"stdout=={self.stdout_eq!r}")
        for needle in self.stdout_contains:
            parts.append(f"stdout contains {needle!r}")
        if self.stdout_int_mod is not None:
            parts.append(f"stdout%{self.stdout_int_mod[0]}=={self.stdout_int_mod[1]}")
        return ", ".join(parts)


@dataclass(frozen=True)
class Scale:
    """Scale profile: default/fast source // bench-scale values and how C receives them."""

    default: int
    fast: int | None = None
    c: str = "none"  # 'argv' passes scale to the C reference; 'none' means C does not support scaling.


@dataclass(frozen=True)
class BenchSpec:
    """One three-mode benchmark: YIAN source, C reference, and argv/check/tag/scale specification."""

    name: str
    source: str
    fat_src: Path
    raw_src: Path
    c_src: Path
    argv: tuple[str, ...]
    check: Check
    tags: tuple[str, ...]
    note: str
    scale: Scale | None = None

    @property
    def key(self) -> str:
        return f"{self.source}/{self.name}"

    @property
    def raw_override(self) -> bool:
        return self.raw_src != self.fat_src

    def scale_value(self, profile: str) -> int | None:
        """Scale substituted in this profile's source copy; None uses the default (always None for full)."""
        if self.scale is None or profile != "fast":
            return None
        return self.scale.fast

    def run_argv(self, profile: str) -> tuple[str, ...]:
        """Runtime arguments shared by all modes, including fast scale passed to C."""
        value = self.scale_value(profile)
        if value is not None and self.scale is not None and self.scale.c == "argv":
            return (*self.argv, str(value))
        return self.argv


@dataclass(frozen=True)
class SetDef:
    """Set: member patterns (exact key / SOURCE/* / tag:x / *) with default scale and sample count."""

    name: str
    scale: str
    runs: int
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


def load_spec(
    spec_path: Path, source: str, name: str, fat_src: Path, raw_src: Path, c_src: Path
) -> BenchSpec:
    data = _load_json(spec_path, "spec")
    if data.get("name") != name:
        raise SystemExit(f"[spec] {spec_path}: name should be {name!r}, got {data.get('name')!r}")
    if data.get("source") != source:
        raise SystemExit(f"[spec] {spec_path}: source should be {source!r}, got {data.get('source')!r}")
    check_raw = data.get("check")
    if not isinstance(check_raw, dict):
        raise SystemExit(f"[spec] {spec_path}: missing check object")
    int_mod_raw = check_raw.get("stdout_int_mod")
    int_mod: tuple[int, int] | None = None
    if int_mod_raw is not None:
        if not isinstance(int_mod_raw, list) or len(int_mod_raw) != 2 or any(
            not isinstance(item, int) for item in int_mod_raw
        ):
            raise SystemExit(f"[spec] {spec_path}: stdout_int_mod expects [modulus, expected remainder]")
        int_mod = (int(int_mod_raw[0]), int(int_mod_raw[1]))
    eq_raw = check_raw.get("stdout_eq")
    check = Check(
        rc=int(check_raw.get("rc", 0)),
        stdout_eq=str(eq_raw) if eq_raw is not None else None,
        stdout_contains=_as_str_list(check_raw.get("stdout_contains"), f"{spec_path}: stdout_contains"),
        stdout_int_mod=int_mod,
    )
    scale_raw = data.get("scale")
    scale: Scale | None = None
    if scale_raw is not None:
        if not isinstance(scale_raw, dict) or "default" not in scale_raw:
            raise SystemExit(f"[spec] {spec_path}: scale requires default (fast profile value optional)")
        fast_raw = scale_raw.get("fast")
        scale = Scale(
            default=int(scale_raw["default"]),
            fast=int(fast_raw) if fast_raw is not None else None,
            c=str(scale_raw.get("c", "none")),
        )
        if scale.c not in ("argv", "none"):
            raise SystemExit(f"[spec] {spec_path}: scale.c must be \"argv\" or \"none\"")
    return BenchSpec(
        name=name,
        source=source,
        fat_src=fat_src,
        raw_src=raw_src,
        c_src=c_src,
        argv=_as_str_list(data.get("argv"), f"{spec_path}: argv"),
        check=check,
        tags=_as_str_list(data.get("tags"), f"{spec_path}: tags"),
        note=str(data.get("note", "")),
        scale=scale,
    )


def discover() -> list[BenchSpec]:
    """Discover three-mode benchmarks under bench/<SOURCE>/{an,c,specs}.

    Sources without c/, such as allocator-only bench/ALLOC, are handled by bench_allocator.py.
    """
    specs: list[BenchSpec] = []
    for source_dir in sorted(
        path for path in BENCH_DIR.iterdir() if path.is_dir() and path.name not in RESERVED_DIRS
    ):
        an_dir, c_dir, spec_dir = source_dir / "an", source_dir / "c", source_dir / "specs"
        if not an_dir.is_dir() or not c_dir.is_dir():
            continue
        fat: dict[str, Path] = {}
        raw: dict[str, Path] = {}
        for path in sorted(an_dir.glob("*.an")):
            if path.name.endswith(RAW_SUFFIX):
                raw[path.name[: -len(RAW_SUFFIX)]] = path
            else:
                fat[path.stem] = path
        if not fat:
            raise SystemExit(f"[discover] no .an benchmarks under {an_dir}")
        orphans = sorted(set(raw) - set(fat))
        if orphans:
            raise SystemExit(f"[discover] raw override sources have no same-name fat source: {', '.join(orphans)}")
        for name in sorted(fat):
            c_src = c_dir / f"{name}.c"
            if not c_src.exists():
                raise SystemExit(f"[discover] {source_dir.name}/{name} missing C reference: {c_src}")
            spec_path = spec_dir / f"{name}.json"
            if not spec_path.exists():
                raise SystemExit(f"[discover] {source_dir.name}/{name} missing spec: {spec_path}")
            specs.append(
                load_spec(spec_path, source_dir.name, name, fat[name], raw.get(name, fat[name]), c_src)
            )
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
            runs=int(data.get("runs", DEFAULT_RUNS)),
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


_SCALE_LINE_RE = re.compile(
    r"^(?P<head>\s*let\s+[A-Za-z_]\w*\s*:\s*[A-Za-z_]\w*\s*=\s*)(?P<value>\d+)(?P<tail>;\s*"
    + re.escape(SCALE_MARKER)
    + r".*)$",
    re.MULTILINE,
)


def materialize(spec: BenchSpec, profile: str) -> tuple[Path, Path]:
    """Return (fat source,raw source) for the scale profile.

    When fast and scale.fast is declared, replace // bench-scale numbers and generate
    build/bench/src/<profile>/<SOURCE>/<name>.an; otherwise use repository sources.
    """
    value = spec.scale_value(profile)
    if value is None:
        return spec.fat_src, spec.raw_src
    out_dir = SRC_BUILD_DIR / profile / spec.source
    out_dir.mkdir(parents=True, exist_ok=True)

    def emit(src: Path) -> Path:
        text = src.read_text(encoding="utf-8")
        patched, count = _SCALE_LINE_RE.subn(
            lambda match: f"{match.group('head')}{value}{match.group('tail')}", text
        )
        if count != 1:
            raise SystemExit(
                f"[scale] {src}: expected exactly 1 `{SCALE_MARKER}` marker line, got {count}"
            )
        dst = out_dir / src.name
        dst.write_text(patched, encoding="utf-8")
        return dst

    fat = emit(spec.fat_src)
    raw = emit(spec.raw_src) if spec.raw_override else fat
    return fat, raw


def spec_bin(spec: BenchSpec, state: str) -> Path:
    return OUT_DIR / STATE_DIR[state] / f"{spec.source}_{spec.name}"


def compile_c(spec: BenchSpec) -> Path:
    """Compile C with clang -O2 -ffp-contract=off -lm; stop on failure."""
    binary = spec_bin(spec, "c")
    binary.parent.mkdir(parents=True, exist_ok=True)
    res = subprocess.run(
        [C_COMPILER, *C_FLAGS, str(spec.c_src), "-o", str(binary)],
        capture_output=True,
        text=True,
        cwd=ROOT,
    )
    if res.returncode != 0:
        raise SystemExit(
            f"[compile] {spec.key} (c) failed ({C_COMPILER} {' '.join(C_FLAGS)}):\n{res.stdout}\n{res.stderr}"
        )
    return binary


def compile_an(spec: BenchSpec, profile: str, raw: bool, compiler: Path) -> Path:
    """Compile one YIAN mode; stop on failure because compile errors invalidate the baseline."""
    state = "raw" if raw else "fat"
    binary = spec_bin(spec, state)
    fat_src, raw_src = materialize(spec, profile)
    src = raw_src if raw else fat_src
    cmd = [str(compiler), OPT_LEVEL]
    if raw:
        cmd.append("--raw-pointers")
    cmd += [str(LIB), str(src), "-o", str(binary)]
    binary.parent.mkdir(parents=True, exist_ok=True)
    environment = os.environ.copy()
    res = subprocess.run(cmd, capture_output=True, text=True, cwd=ROOT, env=environment)
    if res.returncode != 0:
        raise SystemExit(f"[compile] {spec.key} ({state}) failed:\n{res.stdout}\n{res.stderr}")
    return binary


OPT_LEVEL = "-O2"
C_COMPILER = "clang"
C_FLAGS = ["-O2", "-ffp-contract=off", "-lm"]
TIME_BIN = "/usr/bin/time"
DEFAULT_RUNS = 5
DEFAULT_MAX_STATE_SEC = 120.0
RAW_SUFFIX = ".raw.an"

STATES = ("c", "raw", "fat")
STATE_DIR = {"c": "cbin", "raw": "yraw", "fat": "yfat"}

_RSS_RE = re.compile(r"Maximum resident set size \(kbytes\): (\d+)")


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


def measure_triple(
    spec: BenchSpec, runs: int, pin: int | None, max_state_sec: float, profile: str
) -> Row:
    """Warm up each mode, then rotate C/raw/fat for used_runs rounds.

    All modes share argv, including the optional workload scale. Source markers
    retain the default scale when the command line supplies no override.
    """
    argv = list(spec.run_argv(profile))
    bins = {state: spec_bin(spec, state) for state in STATES}

    t0 = time.monotonic()
    warm = {state: _run(bins[state], argv, pin) for state in STATES}
    warm_sec = time.monotonic() - t0

    c_check_ok = spec.check.ok(warm["c"][3], warm["c"][2])
    if not c_check_ok:
        print(
            f"[{spec.key}] warning: C baseline failed the authoritative check "
            f"(exit={warm['c'][3]}, stdout={warm['c'][2].strip()!r}); C ratios for this benchmark are unusable",
            file=sys.stderr,
        )
    yian_stdout_match = (
        warm["raw"][2] == warm["fat"][2]
        and warm["raw"][3] == warm["fat"][3]
        and all(spec.check.ok(warm[state][3], warm[state][2]) for state in ("raw", "fat"))
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

    used_runs = runs
    if warm_sec > max_state_sec and runs > 3:
        used_runs = 3
        print(
            f"[{spec.key}] warmup {warm_sec:.1f}s > {max_state_sec:.0f}s, "
            f"measurement runs reduced to {used_runs}",
            file=sys.stderr,
        )

    samples: dict[str, list[float]] = {state: [] for state in STATES}
    rss: dict[str, list[int]] = {state: [] for state in STATES}
    for _ in range(used_runs):
        for state in STATES:
            wall, peak, _, _ = _run(bins[state], argv, pin)
            samples[state].append(wall)
            rss[state].append(peak)

    return Row(
        spec=spec,
        times={state: _stats(samples[state]) for state in STATES},
        rss_mb={state: max(rss[state]) / 1024.0 for state in STATES},
        rss_samples=rss,
        runs=used_runs,
        yian_stdout_match=yian_stdout_match,
        c_check_ok=c_check_ok,
        exit_codes={state: warm[state][3] for state in STATES},
    )


def write_samples(rows: list[Row], path: Path) -> None:
    """Write per-run samples in the format the paper's data/ files use."""
    lines = [
        f"C / raw / fat raw samples, measured {datetime.date.today():%Y-%m-%d}.",
        "Each pair is wall_ms (peak_rss_kb); one line per benchmark and state.",
    ]
    for row in sorted(rows, key=lambda item: item.spec.name):
        for state in STATES:
            stats = row.times[state]
            pairs = ", ".join(
                f"{wall:.1f} ({peak})"
                for wall, peak in zip(stats.samples, row.rss_samples[state])
            )
            lines.append(
                f"- {row.spec.name} / {state} ({len(stats.samples)} runs): {pairs}"
            )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _tool_version(tool: str) -> str:
    try:
        res = subprocess.run(
            [tool, "--version"], capture_output=True, text=True, timeout=10
        )
    except (OSError, subprocess.SubprocessError):
        return "n/a"
    if res.returncode != 0 or not res.stdout:
        return "n/a"
    return res.stdout.splitlines()[0].strip()


def _git_head() -> str:
    try:
        res = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            timeout=10,
            cwd=ROOT,
        )
    except (OSError, subprocess.SubprocessError):
        return "n/a"
    return res.stdout.strip() if res.returncode == 0 else "n/a"


def machine_fingerprint(
    pin: int | None,
    runs: int,
    bench_set: str,
    profile: str,
    compiler: Path,
    compiler_source: str,
    compiler_used: bool,
) -> dict[str, str]:
    return {
        "set": bench_set,
        "scale": profile,
        "hostname": platform.node() or "unknown",
        "machine": platform.machine(),
        "cpu_count": str(os.cpu_count() or 0),
        "clang": _tool_version(C_COMPILER),
        "cflags": " ".join(C_FLAGS),
        "python": platform.python_version(),
        "yianc": str(compiler),
        "yianc_sha256": file_sha256(compiler),
        "yianc_source": compiler_source,
        "yianc_used": "yes" if compiler_used else "no (--no-compile; existing artifacts reused)",
        "opt": OPT_LEVEL,
        "pin": str(pin) if pin is not None else "none",
        "runs": str(runs),
        "commit": _git_head(),
        "date": datetime.date.today().isoformat(),
    }


def _fmt_ms(value: float) -> str:
    return f"{value:.1f}"


def _fmt_mb(kb_mb: float) -> str:
    return f"{kb_mb:.1f}"


def _geomean(values: list[float]) -> float | None:
    if not values:
        return None
    return math.exp(sum(math.log(v) for v in values) / len(values))


def _ratio(row: Row, num: str, den: str) -> float:
    denom = row.times[den].min
    return row.times[num].min / denom if denom else float("nan")


def _ratio_usable(row: Row) -> bool:
    """C ratios are usable only when the C baseline and both mode guards pass."""
    return row.c_check_ok and row.yian_stdout_match


def render_md(rows: list[Row], fingerprint: dict[str, str]) -> str:
    lines: list[str] = []
    lines.append(
        f"# C / raw / fat three-state measurements - set `{fingerprint['set']}` (scale `{fingerprint['scale']}`)\n"
    )
    lines.append(
        "Generated by `bench/bench_three_way.py`; all three states use the same algorithm and scale, "
        "differing only in implementation and pointer representation (C is the clang `-O2` reference). "
        "Set members and scale profiles are in `bench/sets/<set>.json` and each benchmark's "
        "`specs/<name>.json`.\n"
    )
    lines.append("## Environment fingerprint\n")
    lines.append("| item | value |")
    lines.append("| --- | --- |")
    lines.append(f"| host | {fingerprint['hostname']} |")
    lines.append(f"| architecture / logical cores | {fingerprint['machine']} / {fingerprint['cpu_count']} |")
    lines.append(f"| C compiler | `{fingerprint['clang']}` (`{fingerprint['cflags']}`) |")
    lines.append(f"| Python | {fingerprint['python']} |")
    lines.append(
        f"| YIAN compiler | `{fingerprint['yianc']}` (SHA-256 `{fingerprint['yianc_sha256']}`, "
        f"source `{fingerprint['yianc_source']}`, used this run: {fingerprint['yianc_used']}) |"
    )
    lines.append(f"| YIAN opt level | `{fingerprint['opt']}` (same for both modes) |")
    lines.append(f"| pinning | `taskset -c {fingerprint['pin']}` |")
    lines.append(f"| runs per mode | {fingerprint['runs']} (plus 1 warmup) |")
    lines.append(f"| set / scale | `{fingerprint['set']}` / `{fingerprint['scale']}` |")
    lines.append(f"| commit | `{fingerprint['commit']}` |")
    lines.append(f"| date | {fingerprint['date']} |")
    lines.append("")

    lines.append("## Results\n")
    lines.append(
        "| benchmark | C min (ms) | raw min (ms) | fat min (ms) | raw/C | fat/C | fat/raw "
        "| C peak RSS (MB) | raw peak RSS (MB) | fat peak RSS (MB) | raw source | raw/fat stdout match | C check |"
    )
    lines.append("| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- | --- | --- |")
    for row in rows:
        ratio_rc = _ratio(row, "raw", "c")
        ratio_fc = _ratio(row, "fat", "c")
        ratio_fr = _ratio(row, "fat", "raw")
        raw_src = f"`{row.spec.raw_src.name}`" if row.spec.raw_override else "same"
        c_flag = "yes" if row.c_check_ok else "**no**"
        if not _ratio_usable(row):
            ratio_rc = ratio_fc = float("nan")
        lines.append(
            f"| {row.spec.key} | {_fmt_ms(row.times['c'].min)} | {_fmt_ms(row.times['raw'].min)} "
            f"| {_fmt_ms(row.times['fat'].min)} | {ratio_rc:.2f}× | {ratio_fc:.2f}× | {ratio_fr:.2f}× "
            f"| {_fmt_mb(row.rss_mb['c'])} | {_fmt_mb(row.rss_mb['raw'])} | {_fmt_mb(row.rss_mb['fat'])} "
            f"| {raw_src} | {'yes' if row.yian_stdout_match else '**no**'} | {c_flag} |"
        )
    usable = [row for row in rows if _ratio_usable(row)]
    geo_raw = _geomean([_ratio(row, "raw", "c") for row in usable])
    geo_fat = _geomean([_ratio(row, "fat", "c") for row in usable])
    if geo_raw is not None and geo_fat is not None:
        lines.append("")
        lines.append(
            f"Geometric mean ratio (C baseline, n={len(usable)}): raw/C **{geo_raw:.2f}×** / "
            f"fat/C **{geo_fat:.2f}×** (ratio usable = C check passes and raw/fat semantics match)."
        )
        totals = {state: sum(row.times[state].min for row in rows) / 1000.0 for state in STATES}
        lines.append(
            f"Single full pass (sum of per-benchmark minima): C **{totals['c']:.1f} s** / "
            f"raw **{totals['raw']:.1f} s** / fat **{totals['fat']:.1f} s**."
        )
    lines.append("")
    lines.append(
        "All ratios are ratios of minima; benchmarks are not additive. `--raw-pointers` disables all pointer "
        "safety checks and lock-slot/frame-lock emission, so raw/C is the raw implementation's overhead relative "
        "to C, and fat/raw is the total overhead of full fat pointers over raw pointers.\n"
    )

    lines.append("## Repeatability\n")
    lines.append(
        "| benchmark | C median | C min | C max | C CV% | raw median | raw min | raw max | raw CV% "
        "| fat median | fat min | fat max | fat CV% | runs |"
    )
    lines.append(
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |"
    )
    for row in rows:
        cells = " | ".join(
            f"{_fmt_ms(row.times[state].med)} | {_fmt_ms(row.times[state].min)} "
            f"| {_fmt_ms(row.times[state].max)} | {row.times[state].cv:.1f}"
            for state in STATES
        )
        lines.append(f"| {row.spec.key} | {cells} | {row.runs} |")
    lines.append("")
    lines.append(
        "Formal metrics use the **minimum**: when the machine is under load or hot, repeated runs of the same "
        "binary fall into two performance states about 20% apart (a process-level memory-layout/frequency artifact "
        "unrelated to the code). The minimum estimates interference-free performance, and the median and CV judge "
        "repeatability.\n"
    )

    lines.append("## Protocol\n")
    lines.append(
        f"- Compilation: C `{C_COMPILER} {' '.join(C_FLAGS)} bench/<SOURCE>/c/<name>.c`; "
        f"YIAN `yianc {OPT_LEVEL} lib/src <src>`, with `--raw-pointers` added in raw mode."
    )
    lines.append(
        "- Scale and argv: see `scale` in each benchmark's `specs/<name>.json`; argv only covers the C outer "
        "repetition count, consistent with `scale`; the fast profile generates a reduced-scale build copy for "
        "benchmarks that declare `scale.fast`, and passes the same value to the C reference as argv, keeping the "
        "three states' workloads equal."
    )
    lines.append(
        "- Measurement: after one warmup per state per benchmark (excluded from samples), measure in "
        "**round-robin** order C->raw->fat; formal metrics use each state's minimum and record median/IQR/CV/peak RSS."
    )
    lines.append(
        f"- Noise reduction: `taskset -c {fingerprint['pin']}`; round-robin measurement reduces drift across time; "
        "all three artifacts use the same directory and equal-length paths (argv[0] length affects process layout "
        "for allocation-heavy benchmarks)."
    )
    lines.append(
        "- Semantic guard: C warmup must pass the spec's authoritative check (see `check` in "
        "`specs/<name>.json`); raw and fat stdout/exit codes must match byte for byte. A `no` in the report makes "
        "that benchmark's ratios unusable."
    )
    return "\n".join(lines).rstrip("\n") + "\n"


def write_csv(rows: list[Row], fingerprint: dict[str, str], path: Path) -> None:
    header = (
        f"# bench/results/{path.name} - C / raw / fat three-state performance results "
        "(generated by bench/bench_three_way.py)"
    )
    comments = [
        header,
        "# columns: bench is <SOURCE>/<name>; <state>_min_ms/<state>_med_ms/<state>_cv_pct are each state's "
        "minimum (formal metric)/median/coefficient of variation, state ∈ {c,raw,fat}; ratio_raw_c = raw_min/c_min, "
        "ratio_fat_c = fat_min/c_min, ratio_fat_raw = fat_min/raw_min; <state>_rss_mb is peak resident memory; "
        "runs, stdout_match (raw/fat), c_check (C authoritative value), c_argv, raw_source",
        f"# fingerprint: hostname={fingerprint['hostname']} machine={fingerprint['machine']} "
        f"cpu_count={fingerprint['cpu_count']} clang={fingerprint['clang']} cflags={fingerprint['cflags']}",
        f"# env: python={fingerprint['python']} yianc={fingerprint['yianc']} "
        f"yianc_sha256={fingerprint['yianc_sha256']} yianc_source={fingerprint['yianc_source']} "
        f"yianc_used={fingerprint['yianc_used']} opt={fingerprint['opt']} pin={fingerprint['pin']} "
        f"runs={fingerprint['runs']}",
        f"# set: {fingerprint['set']}  scale: {fingerprint['scale']}",
        f"# commit: {fingerprint['commit']}",
        f"# date: {fingerprint['date']}",
    ]
    lines = list(comments)
    lines.append(
        "bench,"
        + ",".join(f"{state}_min_ms,{state}_med_ms,{state}_cv_pct" for state in STATES)
        + ",ratio_raw_c,ratio_fat_c,ratio_fat_raw,"
        + ",".join(f"{state}_rss_mb" for state in STATES)
        + ",runs,stdout_match,c_check,c_argv,raw_source"
    )
    for row in rows:
        raw_src = row.spec.raw_src.name if row.spec.raw_override else row.spec.fat_src.name
        argv = " ".join(row.spec.run_argv(fingerprint["scale"]))
        fields = [row.spec.key]
        for state in STATES:
            stats = row.times[state]
            fields += [f"{stats.min:.1f}", f"{stats.med:.1f}", f"{stats.cv:.2f}"]
        fields += [
            f"{_ratio(row, 'raw', 'c'):.3f}",
            f"{_ratio(row, 'fat', 'c'):.3f}",
            f"{_ratio(row, 'fat', 'raw'):.3f}",
        ]
        fields += [f"{row.rss_mb[state]:.1f}" for state in STATES]
        fields += [
            str(row.runs),
            "yes" if row.yian_stdout_match else "no",
            "pass" if row.c_check_ok else "fail",
            argv,
            raw_src,
        ]
        lines.append(",".join(fields))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
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
    parser.add_argument("--runs", type=int, default=None,
                        help="measured runs per state (default: the set's runs)")
    parser.add_argument("--pin", type=int, help="CPU number for taskset pinning")
    parser.add_argument("--yianc", metavar="PATH", help="Native compiler (default: nonempty YIANC, then PATH)")
    parser.add_argument("--max-state-sec", type=float, default=DEFAULT_MAX_STATE_SEC,
                        help="reduce measured runs to 3 when the combined three-state warmup exceeds this many seconds (fast profile never reduces)")
    parser.add_argument("--no-compile", action="store_true", help="reuse existing binaries without recompiling")
    parser.add_argument("--compile-only", action="store_true", help="compile only, do not measure")
    args = parser.parse_args()

    specs = discover()
    sets = load_sets()

    if args.list_sets:
        for set_def in sorted(sets.values(), key=lambda item: item.name):
            members = resolve_set(specs, set_def)
            print(
                f"{set_def.name:12s} scale={set_def.scale:4s} runs={set_def.runs} "
                f"members={len(members):2d}  {set_def.note}"
            )
        return 0

    try:
        compiler, compiler_source = resolve_native_selection(
            explicit=args.yianc,
            configured=os.environ.get("YIANC"),
            default="yianc",
        )
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
    runs = args.runs if args.runs is not None else set_def.runs

    print(
        f"[bench] set {set_def.name} scale {profile} benchmarks {len(chosen)} "
        f"measuring {runs} runs (fast profile never reduces)",
        file=sys.stderr,
    )
    if not args.no_compile:
        for spec in chosen:
            compile_c(spec)
            compile_an(spec, profile, raw=False, compiler=compiler)
            compile_an(spec, profile, raw=True, compiler=compiler)
            scale_value = spec.scale_value(profile)
            scale_note = f" scale={scale_value}" if scale_value is not None else ""
            print(f"[compile] {spec.key} ok{scale_note}", file=sys.stderr)
    if args.compile_only:
        return 0

    fingerprint = machine_fingerprint(
        args.pin, runs, set_def.name, profile, compiler, compiler_source, not args.no_compile
    )
    max_state_sec = float("inf") if profile == "fast" else args.max_state_sec
    rows: list[Row] = []
    for spec in chosen:
        row = measure_triple(spec, runs, args.pin, max_state_sec, profile)
        rows.append(row)
        scale_value = spec.scale_value(profile)
        scale_note = f" scale={scale_value}" if scale_value is not None else ""
        print(
            f"[measure] {spec.key}{scale_note}: C {row.times['c'].min:.1f}ms "
            f"raw {row.times['raw'].min:.1f}ms fat {row.times['fat'].min:.1f}ms "
            f"raw/C {_ratio(row, 'raw', 'c'):.2f}x fat/C {_ratio(row, 'fat', 'c'):.2f}x "
            f"({row.runs} runs)",
            file=sys.stderr,
        )

    # Temporary --bench/--source subsets leave set results intact; named sets write results/<set>.{md,csv}.
    partial = bool(bench_filter or source_filter)
    if partial:
        md_path, csv_path = PARTIAL_MD, PARTIAL_CSV
    else:
        md_path = RESULTS_DIR / f"{set_def.name}.md"
        csv_path = RESULTS_DIR / f"{set_def.name}.csv"
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    md_path.write_text(render_md(rows, fingerprint), encoding="utf-8")
    write_csv(rows, fingerprint, csv_path)
    samples_path = RESULTS_DIR / "three-way-samples.txt"
    write_samples(rows, samples_path)
    print(f"[write] {md_path.relative_to(ROOT)}", file=sys.stderr)
    print(f"[write] {csv_path.relative_to(ROOT)}", file=sys.stderr)
    print(f"[write] {samples_path.relative_to(ROOT)}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
