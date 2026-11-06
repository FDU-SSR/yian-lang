#!/usr/bin/env python3
"""ASan cross-comparison: C plain, C ASan, and YIAN fat pointers.

Each benchmark is built three ways over the same source:

  c_plain   CMake bench_app target, clang -O3
  c_asan    CMake bench_app target, clang -O3 -fsanitize=address (ASAN_OPTIONS=detect_leaks=0)
  an_check  anx builds the YIAN package in fat-pointer mode

binarytree additionally gets ``c_asan_sens``: the same ASan binary with
``quarantine_size_mb=0``, which isolates the runtime cost of ASan's quarantine.
All legs run adjacent within one session, using the spec's fixed warmups and
measured runs, so drift between legs stays small.
BG stdin files are generated before timing; each run reopens
the same input and writes stdout to a binary file. A failed warmup/sample stops export of that measurement.

Outputs, under ``bench/results/``:

  asan.csv            one row per benchmark and leg for the paper set
  asan-<set>.csv      the same table for other sets

Usage:

  python bench/bench_asan.py                       # the paper's 24 AWFY/BG benchmarks; LIBYAML is measured separately by real
  python bench/bench_asan.py --bench BG/fasta
  python bench/bench_asan.py --set full --pin 4
"""

from __future__ import annotations

import argparse
import os
import statistics
import subprocess
import shutil
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import bench_three_way as btw  # noqa: E402

ROOT = btw.ROOT
RESULTS = btw.RESULTS_DIR
ASAN_DIR = btw.OUT_DIR / "asan"
ASAN_MAIN = "detect_leaks=0"
ASAN_SENS = "detect_leaks=0:quarantine_size_mb=0"
SENS_BENCH = "binarytree"
LEG_ORDER = ("c_plain", "c_asan", "c_asan_sens", "an_check")
LEG_LABEL = {
    "c_plain": "C plain",
    "c_asan": "C ASan",
    "c_asan_sens": "C ASan sens",
    "an_check": "YIAN check",
}


def compile_asan_ref(spec: btw.BenchSpec) -> Path:
    """Build the project's CMake target with AddressSanitizer; stop on failure."""
    binary = ASAN_DIR / f"{spec.source}_{spec.name}"
    try:
        return btw.projects.build_c(spec.project, asan=True, output=binary, optimization=btw.OPT_LEVEL)
    except (ValueError, RuntimeError) as error:
        raise SystemExit(f"[compile] {spec.key} (c_asan) failed: {error}") from error


def run_once(
    binary: Path,
    argv: list[str],
    pin: int | None,
    env_extra: dict[str, str] | None = None,
) -> tuple[float, int, str, int]:
    """Run once; return (wall ms, peak RSS KB, stdout, exit code)."""
    env = os.environ.copy()
    env["LANG"] = "C"
    if env_extra:
        env.update(env_extra)
    pre = ["taskset", "-c", str(pin)] if pin is not None else []
    start = time.monotonic()
    res = subprocess.run(
        pre + [btw.TIME_BIN, "-v", str(binary), *argv],
        capture_output=True,
        text=True,
        env=env,
    )
    wall_ms = (time.monotonic() - start) * 1000.0
    match = btw._RSS_RE.search(res.stderr)
    if match is None:
        raise SystemExit(
            f"[run] {binary}: cannot parse Maximum resident set size:\n{res.stderr}"
        )
    return wall_ms, int(match.group(1)), res.stdout, res.returncode


def measure(
    spec: btw.BenchSpec,
    legs: dict[str, tuple[Path, dict[str, str] | None]],
    profile: str,
    pin: int | None,
) -> tuple[dict[str, dict[str, list[float]]], dict[str, int], dict[str, btw.execution.RunResult], bool]:
    """Run the spec's fixed warmups, then rotate file-backed results without decoding BG stdout."""
    argv = list(spec.run_argv(profile))
    warmups = spec.measurement.warmups
    runs = spec.measurement.runs
    samples = {leg: {"ms": [], "kb": []} for leg in legs}
    codes: dict[str, int] = {}
    warm_outputs: dict[str, btw.execution.RunResult] = {}
    outputs: dict[str, btw.execution.RunResult] = {}
    stdin_path = btw.execution.prepare_input(spec, profile, btw.OUT_DIR / "asan-inputs")
    output_root = btw.OUT_DIR / "file-runs" / "asan" / spec.source / spec.name / profile
    output_root.mkdir(parents=True, exist_ok=True)

    def run_file(leg: str, binary: Path, env: dict[str, str] | None, phase: str) -> btw.execution.RunResult:
        return btw.execution.run_file(
            binary,
            tuple(argv),
            stdin_path,
            output_root / f"{phase}-{leg}.stdout",
            pin,
            env,
        )

    sample_valid = True
    for _ in range(warmups):
        for leg, (binary, env) in legs.items():
            result = run_file(leg, binary, env, "warm")
            warm_outputs[leg] = result
            codes[leg] = result.rc
        reference = warm_outputs["c_plain"]
        sample_valid = sample_valid and btw._check_file_result(spec, reference)
        for leg, result in warm_outputs.items():
            sample_valid = sample_valid and btw._check_file_result(spec, result)
            sample_valid = sample_valid and btw.execution.output_equal(result, reference)
            outputs[leg] = result
    for _ in range(runs):
        for leg, (binary, env) in legs.items():
            result = run_file(leg, binary, env, "sample")
            samples[leg]["ms"].append(result.wall_ms)
            samples[leg]["kb"].append(result.rss_kb)
            codes[leg] = result.rc
            outputs[leg] = result
            sample_valid = sample_valid and btw._check_file_result(spec, result)
            sample_valid = sample_valid and btw.execution.output_equal(result, reference)
    # Retain C plain's same-scale warmup as the stable reference; all other
    # entries retain the final measured output for report checks.
    outputs["c_plain"] = reference
    return samples, codes, outputs, sample_valid


def write_csv(results: list[dict], path: Path) -> None:
    if any(not row.get("sample_valid", True) or not row["c_check_ok"] or not row["asan_match"] or not row["yian_ok"] for row in results):
        raise ValueError("benchmark execution failed its checks; performance CSV was not replaced")
    records = [btw.results_io.make_record(row["key"], leg, row["legs"][leg]["ms"], row["legs"][leg]["kb"])
               for row in results for leg in LEG_ORDER if leg in row["legs"]]
    btw.results_io.write_csv(path, records)


def __run() -> int:
    parser = argparse.ArgumentParser(description="C plain / C ASan / YIAN fat performance comparison")
    parser.add_argument("--set", dest="bench_set", default="paper", help="set name (default: paper)")
    parser.add_argument("--scale", choices=btw.SCALE_PROFILES, help="scale profile (default: the set's scale)")
    parser.add_argument("--bench", dest="bench_keys", help="only measure the named benchmarks, comma-separated")
    parser.add_argument("--exclude", help="benchmarks to exclude, comma-separated")
    parser.add_argument("--fast", help="use the fast scale profile for the named benchmarks, comma-separated")
    parser.add_argument("--pin", type=int, default=4, help="CPU for taskset pinning (default: 4; 0 disables pinning)")
    parser.add_argument("--yianc", metavar="PATH", help="Native compiler (default: YIANC, then PATH)")
    parser.add_argument("--anx", metavar="PATH", help="Native project builder (default: ANX, then PATH)")
    parser.add_argument("--no-sens", action="store_true", help="skip the binarytree ASan sensitivity leg")
    parser.add_argument("--no-compile", action="store_true", help="reuse existing binaries")
    args = parser.parse_args()

    specs = btw.discover()
    sets = btw.load_sets()
    if args.bench_set not in sets:
        raise SystemExit(f"[args] unknown set {args.bench_set!r}; have: {', '.join(sorted(sets))}")
    set_def = sets[args.bench_set]
    profile = args.scale or set_def.scale
    bench_filter = tuple(item.strip() for item in (args.bench_keys or "").split(",") if item.strip())
    chosen = btw.resolve_set(specs, set_def, bench_filter)
    excluded = {item.strip() for item in (args.exclude or "").split(",") if item.strip()}
    if excluded:
        chosen = [spec for spec in chosen if spec.key not in excluded and spec.name not in excluded]
        if not chosen:
            raise SystemExit("[args] --exclude removed every benchmark")
    pin = None if args.pin == 0 else args.pin

    try:
        compiler, source = btw.resolve_native_selection(
            explicit=args.yianc, configured=os.environ.get("YIANC"), default="yianc"
        )
        anx, anx_source = btw._resolve_anx_selection(args.anx)
    except ValueError as error:
        parser.error(str(error))

    print(
        f"[asan] set {set_def.name} scale {profile} benchmarks {len(chosen)} "
        f"states C plain / C ASan / YIAN",
        file=sys.stderr,
    )

    fast_set = {item.strip() for item in (args.fast or "").split(",") if item.strip()}

    def profile_for(spec: btw.BenchSpec) -> str:
        return "fast" if spec.name in fast_set else profile

    if not args.no_compile:
        for spec in chosen:
            btw.compile_c(spec)
            compile_asan_ref(spec)
            btw.compile_an(spec, profile_for(spec), raw=False, compiler=compiler, anx=anx)
            print(f"[compile] {spec.key} ok", file=sys.stderr)

    csv_path = RESULTS / ("asan.csv" if set_def.name == "paper" else f"asan-{set_def.name}.csv")

    def dump() -> None:
        """Persist current results so an interrupted session keeps its measurements."""
        RESULTS.mkdir(parents=True, exist_ok=True)
        write_csv(results, csv_path)

    results = []
    for spec in chosen:
        legs: dict[str, tuple[Path, dict[str, str] | None]] = {
            "c_plain": (btw.spec_bin(spec, "c"), None),
            "c_asan": (ASAN_DIR / f"{spec.source}_{spec.name}", {"ASAN_OPTIONS": ASAN_MAIN}),
            "an_check": (btw.spec_bin(spec, "fat"), None),
        }
        if not args.no_sens and spec.name == SENS_BENCH:
            legs["c_asan_sens"] = (
                ASAN_DIR / f"{spec.source}_{spec.name}",
                {"ASAN_OPTIONS": ASAN_SENS},
            )
        samples, codes, outputs, sample_valid = measure(spec, legs, profile_for(spec), pin)
        c_check_ok = btw._check_file_result(spec, outputs["c_plain"]) and codes.get("c_plain", -1) == spec.check.rc
        asan_match = all(
            leg in outputs
            and btw.execution.output_equal(outputs[leg], outputs["c_plain"])
            for leg in ("c_asan", "c_asan_sens")
            if leg in outputs
        )
        # All states use the same benchmark output/exit contract.
        yian_ok = "an_check" in outputs and btw._check_file_result(spec, outputs["an_check"])
        results.append(
            {
                "key": spec.key,
                "name": spec.name,
                "source": spec.source,
                "legs": samples,
                "codes": codes,
                "c_check_ok": c_check_ok,
                "asan_match": asan_match,
                "yian_ok": yian_ok,
                "sample_valid": sample_valid,
            }
        )
        med = {leg: statistics.median(s["ms"]) for leg, s in samples.items()}
        text = " ".join(f"{LEG_LABEL[leg]} {value:.1f}ms" for leg, value in med.items())
        flags = []
        if not c_check_ok:
            flags.append("C baseline fails the authoritative check")
        if not asan_match:
            flags.append("ASan stdout differs from C plain")
        if not yian_ok:
            flags.append("YIAN output/exit fails the authoritative check")
        if not sample_valid:
            flags.append("one or more warmup/sample checks failed; ratios are unavailable")
        extra = " (" + "; ".join(flags) + ")" if flags else ""
        print(f"[measure] {spec.key}: {text}{extra}", file=sys.stderr)
        dump()

    dump()
    print(f"[write] {csv_path.relative_to(ROOT)}", file=sys.stderr)
    return 0


def main() -> int:
    try:
        return __run()
    finally:
        shutil.rmtree(btw.OUT_DIR / "file-runs" / "asan", ignore_errors=True)
        shutil.rmtree(btw.OUT_DIR / "asan-inputs", ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
