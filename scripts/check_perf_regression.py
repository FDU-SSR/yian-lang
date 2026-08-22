#!/usr/bin/env python3
"""check_perf_regression.py — 性能回归门禁 (assume-inject-perf-regression C3).

复用 scripts/bench_fat.py 的测量函数 (compile_an/measure/summarize/run_suite/
discover_shootout/machine_fingerprint), 对 4 个基准 (fasta/revcomp/towers/permute)
三态紧邻 (check→nocheck→raw) 各 5 次取中位数, 判据 = **check 态绝对时间中位数,
单侧 (仅拦变慢) ±20%** vs docs/perf-baseline.csv (金标准基线, 由
`bench_fat.py --suite shootout --sync-baseline` 写入)。

判据: check 中位 > 基线 × 1.2 → FAIL。倍率 (check/nocheck、check/raw) 仅打印
作信息, 不作判据。

环境护栏 (不符 → exit 2, 不判 FAIL):
  - 无 docs/perf-baseline.csv → 提示先跑金标准
  - 机器指纹 (hostname/machine/cpu_count/clang) 与基线文件不符 → 环境不可信
  - 运行前 loadavg (1min) 超过阈值 → 环境不可信

退出码: 0 = PASS / 1 = FAIL / 2 = 环境不可信或无法执行。

用法:
    python3 scripts/check_perf_regression.py
    python3 scripts/check_perf_regression.py --runs 5 --pin 4
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import bench_fat

ROOT = bench_fat.ROOT
BASELINE_CSV = ROOT / "docs" / "perf-baseline.csv"

BENCH_NAMES = ("fasta", "revcomp", "towers", "permute")
DEFAULT_RUNS = 5
SLOWDOWN_TOLERANCE = 0.20
LOADAVG_THRESHOLD = 4.0
GOLDEN_HINT = "请先运行金标准基线: bench_fat.py --suite shootout --sync-baseline"


def _parse_baseline(
    path: Path,
) -> tuple[dict[str, str], str, dict[tuple[str, str], float]] | None:
    """解析基线文件 → (指纹 dict, commit, {(基准, 态): 中位 ms}); 缺失/损坏 → None。"""
    if not path.exists():
        return None
    fp: dict[str, str] = {}
    commit = "unknown"
    data: dict[tuple[str, str], float] = {}
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                if line.startswith("# fingerprint:"):
                    for part in line[len("# fingerprint:"):].strip().split(","):
                        if "=" in part:
                            k, v = part.split("=", 1)
                            fp[k.strip()] = v.strip()
                elif line.startswith("# commit:"):
                    commit = line[len("# commit:"):].strip()
                continue
            parts = line.split(",")
            if len(parts) != 3 or parts[0] == "基准":
                continue
            try:
                data[(parts[0].strip(), parts[1].strip())] = float(parts[2])
            except ValueError:
                return None
    except OSError:
        return None
    if not fp or not data:
        return None
    return fp, commit, data


def _check_env(fp: dict[str, str]) -> tuple[bool, str]:
    """环境护栏: 机器指纹比对 + loadavg 检查。返回 (可信?, 原因)。"""
    cur = bench_fat.machine_fingerprint()
    bad = [f"{k}={fp[k]}→{cur.get(k, '?')}" for k in fp if cur.get(k) != fp[k]]
    if bad:
        return False, "机器指纹不符: " + ", ".join(bad)
    try:
        with open("/proc/loadavg", encoding="utf-8") as f:
            load1 = float(f.read().split()[0])
    except (OSError, ValueError, IndexError):
        return False, "无法读取 /proc/loadavg"
    if load1 > LOADAVG_THRESHOLD:
        return False, f"loadavg(1min)={load1:.2f} > {LOADAVG_THRESHOLD:.1f}"
    return True, ""


def judge_check(
    medians: dict[str, float],
    baseline: dict[tuple[str, str], float],
) -> list[tuple[str, bool, float, float]]:
    """判据: check 态绝对中位数单侧 ±20% (仅拦变慢)。返回 [(基准, PASS?, 当前, 基线)]。"""
    out: list[tuple[str, bool, float, float]] = []
    for name in BENCH_NAMES:
        base = baseline.get((name, "check"))
        cur = medians.get(name)
        if base is None or cur is None:
            continue
        out.append((name, cur <= base * (1.0 + SLOWDOWN_TOLERANCE), cur, base))
    return out


def main() -> int:
    ap = argparse.ArgumentParser(
        description="性能回归门禁: check 态绝对中位数单侧 ±20% vs 金标准基线"
    )
    ap.add_argument("--runs", type=int, default=DEFAULT_RUNS, help=f"每态运行次数 (默认 {DEFAULT_RUNS})")
    ap.add_argument("--pin", type=int, default=None, help="taskset 绑定的 CPU 编号 (降噪)")
    ap.add_argument("--baseline", default=None, help="基线文件路径 (默认 docs/perf-baseline.csv)")
    ap.add_argument("--no-compile", action="store_true", help="不重新编译, 仅测量已存在二进制 (测试用)")
    args = ap.parse_args()
    if args.runs < 1:
        print("--runs 必须 ≥ 1")
        return 2

    baseline_path = Path(args.baseline) if args.baseline else BASELINE_CSV
    parsed = _parse_baseline(baseline_path)
    if parsed is None:
        if not baseline_path.exists():
            print(f"性能回归门禁: 无金标准基线, {GOLDEN_HINT}")
        else:
            print(f"性能回归门禁: 基线文件无法解析: {baseline_path}")
        return 2
    fp, commit, base = parsed

    ok, reason = _check_env(fp)
    if not ok:
        print(f"性能回归门禁: 环境不可信 ({reason}), 不判 FAIL")
        return 2

    if not os.path.exists(bench_fat.TIME_BIN):
        print(f"性能回归门禁: 缺少 {bench_fat.TIME_BIN} (GNU time), 无法测量")
        return 2

    all_specs = {s.name: s for s in bench_fat.discover_shootout()}
    missing = [n for n in BENCH_NAMES if n not in all_specs]
    if missing:
        print(f"性能回归门禁: 基准缺失: {', '.join(missing)}")
        return 2
    specs = [all_specs[n] for n in BENCH_NAMES]

    ns = argparse.Namespace(
        no_compile=args.no_compile,
        runs=args.runs,
        pin=args.pin,
        max_state_sec=bench_fat.DEFAULT_MAX_STATE_SEC,
    )
    rows, _, _ = bench_fat.run_suite(specs, ns, do_ref=False)

    medians = {r.bench: r.stats.time.med for r in rows if r.state == "check"}
    nocheck = {r.bench: r.stats.time.med for r in rows if r.state == "nocheck"}
    raw = {r.bench: r.stats.time.med for r in rows if r.state == "raw"}
    results = judge_check(medians, base)

    print(f"环境指纹: {', '.join(f'{k}={v}' for k, v in fp.items())} (基线 commit {commit[:12]})")
    print(f"{'基准':<10} {'check中位(ms)':>12} {'基线(ms)':>10} {'相对基线':>8} "
          f"{'check/nocheck':>13} {'check/raw':>10} 判定")
    failed = False
    for name, ok_, cur, base_v in results:
        rel = cur / base_v if base_v else 0.0
        cn = cur / nocheck[name] if name in nocheck and nocheck[name] else 0.0
        cr = cur / raw[name] if name in raw and raw[name] else 0.0
        verdict = "PASS" if ok_ else "FAIL"
        if not ok_:
            failed = True
        print(f"{name:<10} {cur:>12.1f} {base_v:>10.1f} {rel:>7.2f}× "
              f"{cn:>11.2f}× {cr:>9.2f}×  {verdict}")

    if failed:
        print(f"性能回归门禁 FAILED: 存在 check 态中位数超过基线 {1.0 + SLOWDOWN_TOLERANCE:.1f}× 的基准")
        return 1
    print("性能回归门禁 PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
