#!/usr/bin/env python3
"""bench_allocator.py: heap allocator benchmarks (fat versus raw, fast/full scales).

Directories and artifacts:

  bench/ALLOC/<name>/spec.json   Focus tags and scales
  bench/ALLOC/<name>/yian/       Allocator package; optional raw/ overlay
  bench/results/alloc.csv    Performance samples and statistics

Modes and protocol: allocator benchmarks have no C reference. Raw mode (--raw-pointers) uses libc malloc/free.
  Compare fat/raw, taking minimum wall time and maximum peak RSS across each spec's
  fixed warmup and sample counts; verify identical stdout.

Scales:
  fast substitutes spec.json scale.fast into the staged YIAN package; full uses the declared default.
  Assertions are functions of scale, so reducing scale preserves assertion semantics.

Usage:
  python3 bench/bench_allocator.py                       # All benchmarks, full scale
  python3 bench/bench_allocator.py --scale fast          # Fast scale
  python3 bench/bench_allocator.py --names churn_single --pin 4
  python3 bench/bench_allocator.py --compiler-root /tmp/yian-copy  # Compile a selected source tree
  python3 bench/bench_allocator.py --yianc /path/to/yianc # Select the native compiler explicitly
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from _toolchain.native import resolve_native_selection

try:
    from . import projects, results_io, spec
except ImportError:  # Direct script execution puts bench/ on sys.path.
    import projects
    import results_io
    import spec

ROOT = Path(__file__).resolve().parent.parent
BENCH_ROOT = ROOT / "bench"
BENCH_DIR = BENCH_ROOT / "ALLOC"
BUILD_DIR = ROOT / "build" / "bench" / "alloc"
RESULTS_DIR = ROOT / "bench" / "results"
TIME_BIN = "/usr/bin/time"
OPT_LEVEL = "-O2"
PROFILES = ("fast", "full")

_RSS_RE = re.compile(r"Maximum resident set size \(kbytes\): (\d+)")


def _resolve_anx_selection(
    explicit: str | None, *, search_path: str | None = None
) -> tuple[Path, str]:
    selected, source = resolve_native_selection(
        explicit=explicit, configured=os.environ.get("ANX"), default="anx", search_path=search_path
    )
    return selected, "ANX" if source == "YIANC" else source


def _allocator_projects() -> dict[str, projects.BenchmarkProject]:
    try:
        found = projects.discover_projects(BENCH_ROOT)
    except ValueError as error:
        raise SystemExit(f"[discover] {error}") from error
    return {
        item.name: item
        for item in found
        if item.source == "ALLOC" and item.c is None
    }


def discover(names: str | None) -> list[str]:
    found = sorted(_allocator_projects())
    if not found:
        raise SystemExit(f"[discover] no benchmarks under {BENCH_DIR}")
    if names:
        wanted = [name.strip() for name in names.split(",") if name.strip()]
        missing = [name for name in wanted if name not in found]
        if missing:
            raise SystemExit(f"[args] unknown benchmark(s): {', '.join(missing)}")
        return wanted
    return found


def compile_one(
    name: str,
    raw: bool,
    compiler_root: Path,
    parsed: spec.BenchSpec,
    profile: str,
    compiler: Path,
    *,
    anx: Path | None = None,
) -> Path:
    project = _allocator_projects().get(name)
    if project is None:
        raise SystemExit(f"[compile] unknown allocator project ALLOC/{name}")
    binary = BUILD_DIR / f"{name}.{'raw' if raw else 'fat'}"
    try:
        anx_path = anx
        if anx_path is None:
            anx_path, _ = _resolve_anx_selection(None)
        return projects.build_yian(
            project,
            profile,
            raw,
            parsed.scale_value(profile),
            compiler,
            anx_path,
            compiler_root,
            binary,
        )
    except (ValueError, RuntimeError) as error:
        raise SystemExit(f"[compile] {name} ({'raw' if raw else 'fat'}) failed: {error}") from error


def run_one(binary: Path, pin: int | None) -> tuple[float, float, str]:
    """Return (wall time in ms, peak RSS in MB, stdout)."""
    pre = ["taskset", "-c", str(pin)] if pin is not None else []
    env = {"LANG": "C", "PATH": "/usr/bin:/bin"}
    start = time.monotonic()
    proc = subprocess.run(
        pre + [TIME_BIN, "-v", str(binary)], capture_output=True, text=True, env=env, check=False
    )
    wall_ms = (time.monotonic() - start) * 1000.0
    match = _RSS_RE.search(proc.stderr)
    if proc.returncode != 0 or match is None:
        raise SystemExit(f"[run] {binary} failed (exit code {proc.returncode}):\n{proc.stdout}\n{proc.stderr}")
    return wall_ms, int(match.group(1)) / 1024.0, proc.stdout


def main() -> int:
    parser = argparse.ArgumentParser(description="Heap allocator benchmarks (fat vs raw)")
    parser.add_argument("--names", help="only run the named benchmarks (comma-separated)")
    parser.add_argument("--scale", choices=PROFILES, default="full", help="scale profile (default: full)")
    parser.add_argument("--pin", type=int, default=None, help="CPU number for taskset pinning")
    parser.add_argument("--compiler-root", type=Path, default=ROOT, help="source tree containing the standard library and inputs (default: this repository)")
    parser.add_argument("--yianc", metavar="PATH", help="Native compiler (default: nonempty YIANC, then PATH)")
    parser.add_argument("--anx", metavar="PATH", help="Native project builder (default: nonempty ANX, then PATH)")
    args = parser.parse_args()

    root = args.compiler_root.resolve()
    try:
        compiler, compiler_source = resolve_native_selection(
            explicit=args.yianc,
            configured=os.environ.get("YIANC"),
            default="yianc",
        )
        anx, anx_source = _resolve_anx_selection(args.anx)
    except ValueError as error:
        parser.error(str(error))
    names = discover(args.names)
    projects_by_name = _allocator_projects()
    records = []
    for name in names:
        project = projects_by_name[name]
        parsed = spec.load_spec(project.spec_path, project)
        policy = parsed.measurement
        samples_by_state = {}
        for raw in (False, True):
            binary = compile_one(name, raw, root, parsed, args.scale, compiler, anx=anx)
            for _ in range(policy.warmups):
                run_one(binary, args.pin)
            samples_by_state["raw" if raw else "fat"] = [
                run_one(binary, args.pin) for _ in range(policy.runs)
            ]
        reference = samples_by_state["fat"][0][2]
        valid = all(sample[2] == reference for samples in samples_by_state.values() for sample in samples)
        raw_min = min(sample[0] for sample in samples_by_state["raw"])
        for state, samples in samples_by_state.items():
            if not valid:
                raise ValueError("benchmark output mismatch; performance CSV was not replaced")
            records.append(results_io.make_record(
                f"ALLOC/{name}", state, [sample[0] for sample in samples],
                [sample[1] * 1024 for sample in samples],
            ))
        print(f"[measure] ALLOC/{name}: fat {min(sample[0] for sample in samples_by_state['fat']):.1f}ms raw {raw_min:.1f}ms")
    path = RESULTS_DIR / "alloc.csv"
    results_io.write_csv(path, records)
    print(f"[write] {path.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
