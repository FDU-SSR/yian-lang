#!/usr/bin/env python3
"""ASan cross-comparison: C plain, C ASan, and YIAN fat pointers.

Each benchmark is built three ways over the same source:

  c_plain   CMake bench_app target, clang -O2
  c_asan    CMake bench_app target, clang -O2 -fsanitize=address (ASAN_OPTIONS=detect_leaks=0)
  an_check  anx builds the YIAN package in fat-pointer mode

binarytree additionally gets ``c_asan_sens``: the same ASan binary with
``quarantine_size_mb=0``, which isolates the runtime cost of ASan's quarantine.
All legs run adjacent within one session, one warmup plus N measured runs, so
drift between legs stays small.
BG stdin files are generated and hash-identified before timing; each run reopens
the same input and writes stdout to a binary file. A failed warmup/sample stays
in the observations and makes that benchmark's ratios unavailable.

Outputs, under ``bench/results/``:

  asan.csv            one row per benchmark and leg, with samples
  asan.md             report: per-leg medians, derived ratios, geometric means
  asan-samples.txt    raw samples (same format as the paper's data/)

Usage:

  python bench/bench_asan.py                       # the paper's 14 benchmarks
  python bench/bench_asan.py --bench BG/fasta --runs 1
  python bench/bench_asan.py --set full --runs 5 --pin 4
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import statistics
import subprocess
import sys
import time
from datetime import datetime
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
        return btw.projects.build_c(spec.project, asan=True, output=binary)
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
    runs: int,
    pin: int | None,
) -> tuple[dict[str, dict[str, list[float]]], dict[str, int], dict[str, btw.execution.RunResult], bool]:
    """Warm each leg, then rotate file-backed results without decoding BG stdout."""
    argv = list(spec.run_argv(profile))
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

    for leg, (binary, env) in legs.items():
        result = run_file(leg, binary, env, "warm")
        warm_outputs[leg] = result
        codes[leg] = result.rc
    reference = warm_outputs["c_plain"]
    sample_valid = btw._check_file_result(spec, reference)
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


def geomean(values: list[float]) -> float | None:
    if not values or any(v <= 0 for v in values):
        return None
    return statistics.geometric_mean(values)


def render_md(results: list[dict], fingerprint: dict[str, str], runs: int,
              pin: int | None, set_name: str, profile: str) -> str:
    lines = [
        "# ASan cross-comparison",
        "",
        f"- set: `{set_name}` (scale `{profile}`), benchmarks: {len(results)}",
        f"- protocol: one warmup plus {runs} measured runs per leg, adjacent within one session",
        f"- pin: {'none' if pin is None else f'taskset -c {pin}'}",
        f"- `ASAN_OPTIONS` main: `{ASAN_MAIN}`; sensitivity (`{SENS_BENCH}`): `{ASAN_SENS}`",
        "- BG input/output: input generation and hashing are outside consumer timing; each leg opens the same input file freshly and writes binary stdout to a file.",
        "",
        "## Machine",
        "",
    ]
    for key, value in fingerprint.items():
        if key != "project_metadata":
            lines.append(f"- {key}: `{value}`")
    if results:
        lines += ["", "## Project, input, and oracle identity", ""]
        for row in results:
            metadata = row.get("project_metadata")
            if metadata is not None:
                lines.append(
                    f"- `{row['key']}`: `{json.dumps(metadata, sort_keys=True, separators=(',', ':'))}`"
                )
    lines += [
        "",
        "## Per-leg medians",
        "",
        "| benchmark | leg | median ms | IQR ms | min–max ms | median RSS MB | runs |",
        "| --- | --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    for row in results:
        for leg in LEG_ORDER:
            stats = row["legs"].get(leg)
            if stats is None:
                continue
            med = statistics.median(stats["ms"])
            rss = statistics.median(stats["kb"]) / 1024.0
            iqr = btw._stats(stats["ms"]).iqr
            lo, hi = min(stats["ms"]), max(stats["ms"])
            lines.append(
                f"| {row['key']} | {LEG_LABEL[leg]} | {med:.1f} | {iqr:.1f} "
                f"| {lo:.1f}–{hi:.1f} | {rss:.1f} | {len(stats['ms'])} |"
            )
    lines += [
        "",
        "## Derived ratios",
        "",
        "| benchmark | (i) C ASan / C plain | (ii) C ASan / YIAN check | (iii) YIAN check / C plain |",
        "| --- | ---: | ---: | ---: |",
    ]
    pool = {"i": [], "ii": [], "iii": []}
    for row in results:
        legs = row["legs"]
        if not all(leg in legs for leg in ("c_plain", "c_asan", "an_check")):
            continue
        if not row.get("sample_valid", True):
            lines.append(f"| {row['key']} | n/a | n/a | n/a |")
            continue
        plain = statistics.median(legs["c_plain"]["ms"])
        asan = statistics.median(legs["c_asan"]["ms"])
        an = statistics.median(legs["an_check"]["ms"])
        ratios = {"i": asan / plain, "ii": asan / an, "iii": an / plain}
        for key, value in ratios.items():
            pool[key].append(value)
        lines.append(
            f"| {row['key']} | {ratios['i']:.2f} | {ratios['ii']:.2f} | {ratios['iii']:.2f} |"
        )
    lines += [
        "",
        "Geometric means: "
        + ", ".join(
            f"({key}) {geomean(pool[key]):.2f}"
            for key in ("i", "ii", "iii")
            if pool[key]
        ),
        "",
    ]
    problems = []
    for row in results:
        if not row.get("sample_valid", True):
            problems.append(f"{row['key']}: one or more warmup/sample checks failed; ratios are unavailable")
        if not row["c_check_ok"]:
            problems.append(f"{row['key']}: C baseline fails the authoritative check")
        if not row["asan_match"]:
            problems.append(f"{row['key']}: ASan stdout differs from C plain")
        if not row["yian_ok"]:
            problems.append(f"{row['key']}: YIAN output/exit fails the authoritative check")
    lines += ["## Checks", ""]
    if problems:
        lines += [f"- {item}" for item in problems]
    else:
        lines.append("- C baselines pass, ASan stdout/exit match C plain, YIAN output/exit satisfy the same benchmark spec.")
    lines.append("")
    return "\n".join(lines)


def write_csv(results: list[dict], path: Path) -> None:
    header = "benchmark,source,leg,runs,median_ms,iqr_ms,min_ms,max_ms,median_rss_mb,exit_code\n"
    rows = []
    for row in results:
        for leg in LEG_ORDER:
            stats = row["legs"].get(leg)
            if stats is None:
                continue
            s = btw._stats(stats["ms"])
            rows.append(
                f"{row['name']},{row['source']},{leg},{len(stats['ms'])},"
                f"{s.med:.1f},{s.iqr:.1f},{s.min:.1f},{s.max:.1f},"
                f"{statistics.median(stats['kb']) / 1024.0:.1f},{row['codes'].get(leg, 0)}"
            )
    invalid = [row["key"] for row in results if not row.get("sample_valid", True)]
    metadata = f"# sample_valid=false: {', '.join(invalid)}\n" if invalid else ""
    identity_lines = [
        "# project_metadata: " + json.dumps(row["project_metadata"], sort_keys=True, separators=(",", ":"))
        for row in results if "project_metadata" in row
    ]
    if results and "project_metadata" in results[0]:
        case_meta = results[0]["project_metadata"]
        if isinstance(case_meta, dict) and isinstance(case_meta.get("native_manifest_sha256"), str):
            identity_lines.insert(0, f"# native_manifest_sha256: {case_meta['native_manifest_sha256']}")
    path.write_text(metadata + "\n".join(identity_lines) + ("\n" if identity_lines else "")
                    + header + "\n".join(rows) + "\n", encoding="utf-8")


def emit_samples(results: list[dict], path: Path) -> None:
    """Write samples in the format the paper's data/ files use: ``wall_ms (peak_rss_kb)``."""
    lines = [
        f"C plain / C ASan / YIAN check raw samples, measured {datetime.now():%Y-%m-%d}.",
        "Each pair is wall_ms (peak_rss_kb); one line per benchmark and leg.",
    ]
    invalid = [row["key"] for row in results if not row.get("sample_valid", True)]
    if invalid:
        lines.append(f"# sample_valid=false: {', '.join(invalid)}; failed observations are retained")
    name_counts: dict[str, int] = {}
    for row in results:
        name_counts[row["name"]] = name_counts.get(row["name"], 0) + 1
    for row in sorted(results, key=lambda item: item["name"]):
        label = row["key"] if name_counts[row["name"]] > 1 else row["name"]
        for leg in ("an_check", "c_asan", "c_asan_sens", "c_plain"):
            stats = row["legs"].get(leg)
            if stats is None:
                continue
            pairs = ", ".join(
                f"{ms:.1f} ({kb})" for ms, kb in zip(stats["ms"], stats["kb"])
            )
            lines.append(f"- {label} / {leg} ({len(stats['ms'])} runs): {pairs}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def fingerprint(
    compiler: Path,
    set_name: str,
    profile: str,
    fast: tuple[str, ...] = (),
    *,
    anx: Path | None = None,
    anx_source: str = "n/a",
) -> dict[str, str]:
    def capture(cmd: list[str]) -> str:
        try:
            return subprocess.run(cmd, capture_output=True, text=True, check=False).stdout.splitlines()[0]
        except (OSError, IndexError):
            return "unknown"

    if anx is None:
        anx, anx_source = btw._resolve_anx_selection(None)
    digest = hashlib.sha256(compiler.read_bytes()).hexdigest()[:16]
    return {
        "date": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "git": capture(["git", "-C", str(ROOT), "rev-parse", "--short", "HEAD"]),
        "kernel": platform.release(),
        "cpu": capture(["bash", "-c", "lscpu | awk -F: '/Model name/ {print $2; exit}'"]).strip(),
        "clang": capture([btw.C_COMPILER, "--version"]),
        "yianc": f"{compiler} (sha256:{digest})",
        "anx": str(anx),
        "anx_sha256": btw.file_sha256(anx),
        "anx_source": anx_source,
        "cmake": btw._tool_version("cmake"),
        "project_layout": "2",
        "project_digests": "n/a",
        "python": platform.python_version(),
        "set": f"{set_name}/{profile}",
        "fast_scale": ", ".join(sorted(fast)) if fast else "none",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="C plain / C ASan / YIAN fat performance comparison")
    parser.add_argument("--set", dest="bench_set", default="paper", help="set name (default: paper)")
    parser.add_argument("--scale", choices=btw.SCALE_PROFILES, help="scale profile (default: the set's scale)")
    parser.add_argument("--bench", dest="bench_keys", help="only measure the named benchmarks, comma-separated")
    parser.add_argument("--exclude", help="benchmarks to exclude, comma-separated")
    parser.add_argument("--fast", help="use the fast scale profile for the named benchmarks, comma-separated")
    parser.add_argument("--runs", type=int, help="measured runs per leg (default: the set's runs)")
    parser.add_argument("--pin", type=int, default=4, help="CPU for taskset pinning (default: 4; 0 disables pinning)")
    parser.add_argument("--yianc", metavar="PATH", help="Native compiler (default: YIANC, then PATH)")
    parser.add_argument("--anx", metavar="PATH", help="Native project builder (default: ANX, then PATH)")
    parser.add_argument("--no-sens", action="store_true", help="skip the binarytree ASan sensitivity leg")
    parser.add_argument("--no-compile", action="store_true", help="reuse existing binaries")
    parser.add_argument("--samples", metavar="PATH",
                        help="raw sample output (default: bench/results/asan-samples.txt)")
    args = parser.parse_args()

    specs = btw.discover()
    sets = btw.load_sets()
    if args.bench_set not in sets:
        raise SystemExit(f"[args] unknown set {args.bench_set!r}; have: {', '.join(sorted(sets))}")
    set_def = sets[args.bench_set]
    profile = args.scale or set_def.scale
    runs = args.runs if args.runs is not None else set_def.runs
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
        f"runs {runs} pin {pin} yianc {compiler} ({source}) anx {anx} ({anx_source})",
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

    md_path = RESULTS / "asan.md"
    csv_path = RESULTS / "asan.csv"
    samples_path = Path(args.samples) if args.samples else RESULTS / "asan-samples.txt"

    def dump() -> None:
        """Persist current results so an interrupted session keeps its measurements."""
        RESULTS.mkdir(parents=True, exist_ok=True)
        cases = [row["project_metadata"] for row in results if "project_metadata" in row]
        fp.update(btw._summarize_case_identity(cases))
        native_manifest = btw.BENCH_DIR / "native" / "manifest.json"
        fp["native_manifest_sha256"] = btw.file_sha256(native_manifest) if native_manifest.is_file() else "n/a"
        md_path.write_text(
            render_md(results, fp, runs, pin, set_def.name, profile), encoding="utf-8"
        )
        write_csv(results, csv_path)
        emit_samples(results, samples_path)

    fp = fingerprint(
        compiler, set_def.name, profile, tuple(sorted(fast_set)), anx=anx, anx_source=anx_source
    )
    fp["project_digests"] = ";".join(
        f"{spec.key}={btw.projects.project_digest(spec.project)}" for spec in chosen
    )
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
        samples, codes, outputs, sample_valid = measure(spec, legs, profile_for(spec), runs, pin)
        c_check_ok = btw._check_file_result(spec, outputs["c_plain"]) and codes.get("c_plain", -1) == spec.check.rc
        asan_match = all(
            leg in outputs
            and btw.execution.output_equal(outputs[leg], outputs["c_plain"])
            for leg in ("c_asan", "c_asan_sens")
            if leg in outputs
        )
        # All languages use the same result/exit contract, including fann's 51.
        yian_ok = "an_check" in outputs and btw._check_file_result(spec, outputs["an_check"])
        identity = btw._selected_report_identity(
            [(spec, profile_for(spec))], btw.OUT_DIR / "asan-inputs"
        )
        case_metadata = json.loads(identity["project_metadata"])[0]
        case_metadata["native_manifest_sha256"] = identity["native_manifest_sha256"]
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
                "project_metadata": case_metadata,
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
    print(f"[write] {md_path.relative_to(ROOT)}", file=sys.stderr)
    print(f"[write] {csv_path.relative_to(ROOT)}", file=sys.stderr)
    print(f"[write] {samples_path}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
