#!/usr/bin/env python3
"""bench_allocator.py: heap allocator benchmarks (fat versus raw, fast/full scales).

Directories and artifacts:

  bench/ALLOC/<name>/spec.json   Focus tags and scales
  bench/ALLOC/<name>/yian/       Allocator package; optional raw/ overlay
  bench/results/alloc.{md,csv}    Generated results with environment fingerprint and commit

Modes and protocol: allocator benchmarks have no C reference. Raw mode (--raw-pointers) uses libc malloc/free.
  Compare fat/raw, taking minimum wall time and maximum peak RSS across --runs samples per mode;
  verify identical stdout.

Scales:
  fast substitutes spec.json scale.fast into the staged YIAN package; full uses the declared default.
  Assertions are functions of scale, so reducing scale preserves assertion semantics.

Usage:
  python3 bench/bench_allocator.py                       # All benchmarks, full scale
  python3 bench/bench_allocator.py --scale fast          # Fast scale
  python3 bench/bench_allocator.py --names churn_single --runs 5 --pin 4
  python3 bench/bench_allocator.py --compiler-root /tmp/yian-copy  # Compile a selected source tree
  python3 bench/bench_allocator.py --yianc /path/to/yianc # Select the native compiler explicitly
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from _toolchain.native import file_sha256, resolve_native_selection

try:
    from . import projects
except ImportError:  # Direct script execution puts bench/ on sys.path.
    import projects

ROOT = Path(__file__).resolve().parent.parent
BENCH_ROOT = ROOT / "bench"
BENCH_DIR = BENCH_ROOT / "ALLOC"
BUILD_DIR = ROOT / "build" / "bench" / "alloc"
RESULTS_DIR = ROOT / "bench" / "results"
TIME_BIN = "/usr/bin/time"
OPT_LEVEL = "-O2"
PROFILES = ("fast", "full")
DEFAULT_RUNS = {"fast": 3, "full": 7}

_RSS_RE = re.compile(r"Maximum resident set size \(kbytes\): (\d+)")


def _tool_version(tool: str) -> str:
    try:
        result = subprocess.run([tool, "--version"], capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return "n/a"
    if result.returncode != 0 or not result.stdout:
        return "n/a"
    return result.stdout.splitlines()[0].strip()


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


def load_spec(name: str) -> dict[str, object]:
    project = _allocator_projects().get(name)
    if project is None:
        raise SystemExit(f"[spec] unknown allocator project ALLOC/{name}")
    path = project.spec_path
    if not path.exists():
        raise SystemExit(f"[spec] missing {path}")
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or data.get("name") != name:
        raise SystemExit(f"[spec] {path}: name field should be {name!r}")
    return data


def scale_value(spec: dict[str, object], profile: str) -> int | None:
    scale = spec.get("scale")
    if not isinstance(scale, dict) or profile != "fast":
        return None
    fast = scale.get("fast")
    return int(fast) if fast is not None else None


def compile_one(
    name: str,
    raw: bool,
    compiler_root: Path,
    spec: dict[str, object],
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
            scale_value(spec, profile),
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


def _git_head() -> str:
    proc = subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True, cwd=ROOT)
    return proc.stdout.strip() if proc.returncode == 0 else "n/a"


def main() -> int:
    parser = argparse.ArgumentParser(description="Heap allocator benchmarks (fat vs raw)")
    parser.add_argument("--names", help="only run the named benchmarks (comma-separated)")
    parser.add_argument("--scale", choices=PROFILES, default="full", help="scale profile (default: full)")
    parser.add_argument("--runs", type=int, default=None, help="measured runs per binary (default: fast=3 / full=7)")
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
    compiler_hash = file_sha256(compiler)
    runs = args.runs if args.runs is not None else DEFAULT_RUNS[args.scale]
    names = discover(args.names)
    allocator_projects = _allocator_projects()
    print(f"Standard library/input source tree: {root}")
    print(f"Native yianc: {compiler}  SHA-256: {compiler_hash}  source: {compiler_source}")
    anx_hash = file_sha256(anx)
    print(f"Native anx: {anx}  SHA-256: {anx_hash}  source: {anx_source}")
    print(f"Project layout: 2; CMake: {_tool_version('cmake')}")
    print("Project digests: " + ";".join(
        f"ALLOC/{name}={projects.project_digest(allocator_projects[name])}" for name in names
    ))
    print(f"Set: ALLOC  scale: {args.scale}  sampling: {runs} runs per state, minimum wall time / maximum peak RSS; pinning: {args.pin if args.pin is not None else 'none'}")
    print("")
    header = "| benchmark | fat min (ms) | raw min (ms) | fat/raw | fat peak RSS (MB) | raw peak RSS (MB) | stdout match | scale |"
    print(header)
    print("| --- | ---: | ---: | ---: | ---: | ---: | --- | ---: |")
    csv_rows: list[str] = []
    for name in names:
        spec = load_spec(name)
        value = scale_value(spec, args.scale)
        results: dict[str, tuple[float, float, str]] = {}
        for raw in (False, True):
            binary = compile_one(name, raw, root, spec, args.scale, compiler, anx=anx)
            samples = [run_one(binary, args.pin) for _ in range(runs)]
            results["raw" if raw else "fat"] = (
                min(sample[0] for sample in samples),
                max(sample[1] for sample in samples),
                samples[0][2],
            )
        fat, raw_result = results["fat"], results["raw"]
        ratio = fat[0] / raw_result[0] if raw_result[0] else float("nan")
        match = "yes" if fat[2] == raw_result[2] else "**no**"
        scale_text = str(value) if value is not None else "default"
        print(
            f"| {name} | {fat[0]:.1f} | {raw_result[0]:.1f} | {ratio:.2f}× "
            f"| {fat[1]:.1f} | {raw_result[1]:.1f} | {match} | {scale_text} |"
        )
        csv_rows.append(
            f"{name},{fat[0]:.1f},{raw_result[0]:.1f},{ratio:.3f},{fat[1]:.1f},{raw_result[1]:.1f},"
            f"{'yes' if fat[2] == raw_result[2] else 'no'},{scale_text}"
        )

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    native_manifest = BENCH_ROOT / "native" / "manifest.json"
    native_manifest_sha = file_sha256(native_manifest) if native_manifest.is_file() else "n/a"
    md = [
        f"# Allocator benchmarks (fat vs raw) - scale `{args.scale}`",
        "",
        "Generated by `bench/bench_allocator.py`; allocator benchmarks have no C reference, and the comparison state is libc `malloc`/`free`.",
        f"Sampling: {runs} runs per state, minimum wall time / maximum peak RSS; pinning: {args.pin if args.pin is not None else 'none'}; "
        f"commit: `{_git_head()}`; date: {datetime.date.today().isoformat()}",
        f"Native yianc: `{compiler}` (SHA-256 `{compiler_hash}`, source `{compiler_source}`).",
        f"Native anx: `{anx}` (SHA-256 `{anx_hash}`, source `{anx_source}`).",
        "Project layout: `2`; CMake: `" + _tool_version("cmake") + "`.",
        f"Native dependency manifest SHA-256: `{native_manifest_sha}`; these ALLOC projects select no packaged static archive.",
        "External-library instrumentation: not applicable; the allocator comparison uses libc malloc/free.",
        "Project digests: " + ";".join(
            f"ALLOC/{name}={projects.project_digest(allocator_projects[name])}" for name in names
        ) + ".",
        "",
        header,
        "| --- | ---: | ---: | ---: | ---: | ---: | --- | ---: |",
        *csv_rows_to_md(csv_rows),
        "",
    ]
    (RESULTS_DIR / "alloc.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    (RESULTS_DIR / "alloc.csv").write_text(
        "# bench/results/alloc.csv - allocator benchmarks (fat vs raw)\n"
        f"# scale: {args.scale}  runs: {runs}  pin: {args.pin if args.pin is not None else 'none'}"
        f"  commit: {_git_head()}  date: {datetime.date.today().isoformat()}\n"
        f"# yianc: {compiler}  sha256: {compiler_hash}  source: {compiler_source}\n"
        f"# anx: {anx}  sha256: {anx_hash}  source: {anx_source}\n"
        f"# project_layout: 2  cmake: {_tool_version('cmake')}\n"
        f"# native_manifest_sha256: {native_manifest_sha}\n"
        "# external_instrumentation: not applicable; ALLOC links no packaged static archive\n"
        "# project_digests: "
        + ";".join(f"ALLOC/{name}={projects.project_digest(allocator_projects[name])}" for name in names)
        + "\n"
        "name,fat_min_ms,raw_min_ms,ratio_fat_raw,fat_rss_mb,raw_rss_mb,stdout_match,scale\n"
        + "\n".join(csv_rows)
        + "\n",
        encoding="utf-8",
    )
    print(f"\n[write] bench/results/alloc.md")
    print(f"[write] bench/results/alloc.csv")
    return 0


def csv_rows_to_md(rows: list[str]) -> list[str]:
    """Convert a CSV row to a Markdown table row in header-column order."""
    return ["| " + " | ".join(row.split(",")) + " |" for row in rows]


if __name__ == "__main__":
    sys.exit(main())
