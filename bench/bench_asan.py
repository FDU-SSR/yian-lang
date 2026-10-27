#!/usr/bin/env python3
"""ASan cross-comparison: C plain, C ASan, and YIAN fat pointers.

Each benchmark is built three ways over the same source:

  c_plain   clang -O2
  c_asan    clang -O2 -fsanitize=address   (ASAN_OPTIONS=detect_leaks=0)
  an_check  the YIAN compiler in fat-pointer mode

binarytree additionally gets ``c_asan_sens``: the same ASan binary with
``quarantine_size_mb=0``, which isolates the runtime cost of ASan's quarantine.
All legs run adjacent within one session, one warmup plus N measured runs, so
drift between legs stays small.

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
    """Compile the C reference with AddressSanitizer; stop on failure."""
    binary = ASAN_DIR / f"{spec.source}_{spec.name}"
    binary.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        btw.C_COMPILER,
        "-O2",
        "-ffp-contract=off",
        "-fsanitize=address",
        "-fno-omit-frame-pointer",
        str(spec.c_src),
        "-o",
        str(binary),
        "-lm",
    ]
    res = subprocess.run(cmd, capture_output=True, text=True, cwd=ROOT)
    if res.returncode != 0:
        raise SystemExit(f"[compile] {spec.key} (c_asan) failed:\n{res.stdout}\n{res.stderr}")
    return binary


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
) -> tuple[dict[str, dict[str, list[float]]], dict[str, int], bool]:
    """Warm up every leg, then rotate the legs for `runs` rounds."""
    argv = list(spec.run_argv(profile))
    samples = {leg: {"ms": [], "kb": []} for leg in legs}
    codes: dict[str, int] = {}
    outputs: dict[str, str] = {}
    for leg, (binary, env) in legs.items():
        _, _, outputs[leg], codes[leg] = run_once(binary, argv, pin, env)
    for _ in range(runs):
        for leg, (binary, env) in legs.items():
            wall_ms, rss_kb, out, codes[leg] = run_once(binary, argv, pin, env)
            samples[leg]["ms"].append(wall_ms)
            samples[leg]["kb"].append(rss_kb)
            outputs[leg] = out
    return samples, codes, outputs


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
        "",
        "## Machine",
        "",
    ]
    for key, value in fingerprint.items():
        lines.append(f"- {key}: `{value}`")
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
        if not row["c_check_ok"]:
            problems.append(f"{row['key']}: C baseline fails the authoritative check")
        if not row["asan_match"]:
            problems.append(f"{row['key']}: ASan stdout differs from C plain")
        if not row["yian_ok"]:
            problems.append(f"{row['key']}: YIAN exit code differs from C")
    lines += ["## Checks", ""]
    if problems:
        lines += [f"- {item}" for item in problems]
    else:
        lines.append("- C baselines pass, ASan stdout/exit match C plain, every YIAN leg exits 0.")
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
    path.write_text(header + "\n".join(rows) + "\n", encoding="utf-8")


def emit_samples(results: list[dict], path: Path) -> None:
    """Write samples in the format the paper's data/ files use: ``wall_ms (peak_rss_kb)``."""
    lines = [
        f"C plain / C ASan / YIAN check raw samples, measured {datetime.now():%Y-%m-%d}.",
        "Each pair is wall_ms (peak_rss_kb); one line per benchmark and leg.",
    ]
    for row in sorted(results, key=lambda item: item["name"]):
        for leg in ("an_check", "c_asan", "c_asan_sens", "c_plain"):
            stats = row["legs"].get(leg)
            if stats is None:
                continue
            pairs = ", ".join(
                f"{ms:.1f} ({kb})" for ms, kb in zip(stats["ms"], stats["kb"])
            )
            lines.append(f"- {row['name']} / {leg} ({len(stats['ms'])} runs): {pairs}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def fingerprint(compiler: Path, set_name: str, profile: str, fast: tuple[str, ...] = ()) -> dict[str, str]:
    def capture(cmd: list[str]) -> str:
        try:
            return subprocess.run(cmd, capture_output=True, text=True, check=False).stdout.splitlines()[0]
        except (OSError, IndexError):
            return "unknown"

    digest = hashlib.sha256(compiler.read_bytes()).hexdigest()[:16]
    return {
        "date": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "git": capture(["git", "-C", str(ROOT), "rev-parse", "--short", "HEAD"]),
        "kernel": platform.release(),
        "cpu": capture(["bash", "-c", "lscpu | awk -F: '/Model name/ {print $2; exit}'"]).strip(),
        "clang": capture([btw.C_COMPILER, "--version"]),
        "yianc": f"{compiler} (sha256:{digest})",
        "python": platform.python_version(),
        "set": f"{set_name}/{profile}",
        "fast_scale": ", ".join(sorted(fast)) if fast else "none",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="C plain / C ASan / YIAN fat 性能对比")
    parser.add_argument("--set", dest="bench_set", default="paper", help="集合名 (默认 paper)")
    parser.add_argument("--scale", choices=btw.SCALE_PROFILES, help="规模档 (默认取集合的 scale)")
    parser.add_argument("--bench", dest="bench_keys", help="只测指定基准, 逗号分隔")
    parser.add_argument("--exclude", help="排除的基准, 逗号分隔")
    parser.add_argument("--fast", help="对指定基准改用 fast 规模档, 逗号分隔")
    parser.add_argument("--runs", type=int, help="每腿测量次数 (默认取集合的 runs)")
    parser.add_argument("--pin", type=int, default=4, help="taskset 绑定的 CPU (默认 4; 0 表示不绑核)")
    parser.add_argument("--yianc", metavar="PATH", help="Native compiler (默认 YIANC, 再退回 PATH)")
    parser.add_argument("--no-sens", action="store_true", help="跳过 binarytree 的 ASan 敏感性腿")
    parser.add_argument("--no-compile", action="store_true", help="复用已有二进制")
    parser.add_argument("--samples", metavar="PATH",
                        help="原始样本输出 (默认 bench/results/asan-samples.txt)")
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
    except ValueError as error:
        parser.error(str(error))

    print(
        f"[asan] set {set_def.name} scale {profile} benchmarks {len(chosen)} "
        f"runs {runs} pin {pin} compiler {compiler} ({source})",
        file=sys.stderr,
    )

    fast_set = {item.strip() for item in (args.fast or "").split(",") if item.strip()}

    def profile_for(spec: btw.BenchSpec) -> str:
        return "fast" if spec.name in fast_set else profile

    if not args.no_compile:
        for spec in chosen:
            btw.compile_c(spec)
            compile_asan_ref(spec)
            btw.compile_an(spec, profile_for(spec), raw=False, compiler=compiler)
            print(f"[compile] {spec.key} ok", file=sys.stderr)

    md_path = RESULTS / "asan.md"
    csv_path = RESULTS / "asan.csv"
    samples_path = Path(args.samples) if args.samples else RESULTS / "asan-samples.txt"

    def dump() -> None:
        """Persist current results so an interrupted session keeps its measurements."""
        RESULTS.mkdir(parents=True, exist_ok=True)
        md_path.write_text(
            render_md(results, fp, runs, pin, set_def.name, profile), encoding="utf-8"
        )
        write_csv(results, csv_path)
        emit_samples(results, samples_path)

    fp = fingerprint(compiler, set_def.name, profile, tuple(sorted(fast_set)))
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
        samples, codes, outputs = measure(spec, legs, profile_for(spec), runs, pin)
        c_check_ok = spec.check.ok(codes.get("c_plain", -1), outputs.get("c_plain", ""))
        asan_match = all(
            outputs.get(leg) == outputs.get("c_plain")
            and codes.get(leg) == codes.get("c_plain")
            for leg in ("c_asan", "c_asan_sens")
            if leg in outputs
        )
        # YIAN programs assert internally and exit 0; the C reference may use a
        # nonzero status as its result (fann exits with the checksum).
        yian_ok = codes.get("an_check", -1) == 0
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
            flags.append("YIAN exit code differs from C")
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
