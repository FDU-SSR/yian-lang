#!/usr/bin/env python3
"""check_bench_regression.py — C / raw / fat 三态基线的性能回归门禁.

判据 (单侧, 只拦变慢):
  - 各基准 `raw/C` 比值比基线大超过 `--ratio-tolerance`% (默认 20) → 失败;
  - 各基准 `fat/C` 比值比基线大超过 `--ratio-tolerance`% (默认 20) → 失败;
  - 任一行 `stdout_match=no` (raw/fat 语义不一致) 或 `c_check=fail` (C 基线未跑出
    权威值) → 失败 (该基准的比值不可用);
  - 各基准 C 绝对时间比基线慢超过 `--c-tolerance`% (默认 20) → 仅警告: C 由 clang 编译,
    不受 YIAN 代码生成影响, 它变慢说明机器/工具链状态变化, 而比值仍然可对照;
  - 环境指纹 (hostname/machine/cpu_count/clang/cflags/llvmlite/llvm/opt) 与基线不一致
    → 拒绝判定, 退出码 2 (跨机/跨工具链的绝对时间不可比), 除非显式 `--allow-env-mismatch`;
  - 基线中缺失的基准 (新增基准) 只提示, 不判失败; 当前结果缺失的基线基准只提示;
  - 基线 csv 缺少三态格式标记 (旧的两态基线) → 拒绝判定, 退出码 2。

默认基线 = git HEAD 中的 `bench/results.csv` (即已提交的基线), 默认当前结果 =
工作区的 `bench/results.csv` (由 scripts/bench_three_way.py 写入)。

用法:
  python3 scripts/bench_three_way.py --pin 4             # 先测量
  python3 scripts/check_bench_regression.py              # 再判定
  python3 scripts/check_bench_regression.py --baseline-ref origin/main
  python3 scripts/check_bench_regression.py --ratio-tolerance 30 --c-tolerance 30
  python3 scripts/check_bench_regression.py --allow-env-mismatch

退出码: 0 = 通过; 1 = 有回归; 2 = 环境不可比或输入错误。
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CURRENT_CSV = ROOT / "bench" / "results.csv"
BASELINE_PATH_IN_GIT = "bench/results.csv"
CSV_FORMAT = "three-way-1"

ENV_KEYS = ("hostname", "machine", "cpu_count", "clang", "cflags", "llvmlite", "llvm", "opt")
REQUIRED_COLUMNS = (
    "bench", "c_min_ms", "raw_min_ms", "fat_min_ms",
    "ratio_raw_c", "ratio_fat_c", "stdout_match", "c_check",
)


class InputError(Exception):
    """输入或基线格式错误: 拒绝判定 (退出码 2)。"""


@dataclass(frozen=True)
class Baseline:
    rows: dict[str, dict[str, str]]
    env: dict[str, str]
    fmt: str
    origin: str


def _split_comment(line: str) -> tuple[str, str] | None:
    """解析 `# key: value` / `# fingerprint: k=v k=v` 注释行。"""
    body = line.lstrip("#").strip()
    if ":" not in body:
        return None
    key, _, value = body.partition(":")
    return key.strip(), value.strip()


def load_csv(path: Path, origin: str) -> Baseline:
    if not path.exists():
        raise InputError(f"[load] 文件不存在: {path}")
    lines = path.read_text(encoding="utf-8").splitlines()
    env: dict[str, str] = {}
    fmt = ""
    header: list[str] | None = None
    rows: dict[str, dict[str, str]] = {}
    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("#"):
            parsed = _split_comment(stripped)
            if parsed is None:
                continue
            key, value = parsed
            if key == "format":
                fmt = value
            elif key == "fingerprint":
                for pair in value.split():
                    name, _, val = pair.partition("=")
                    if name in ENV_KEYS:
                        env[name] = val
            elif key == "env":
                for pair in value.split():
                    name, _, val = pair.partition("=")
                    if name in ENV_KEYS or name in ("opt", "pin", "runs"):
                        env[name] = val
            continue
        fields = stripped.split(",")
        if header is None:
            if fmt != CSV_FORMAT:
                raise InputError(
                    f"[load] {path} 的基线格式是 {fmt or '未知'!r}, 当前门禁需要 {CSV_FORMAT!r} "
                    f"(两态基线已停用); 先跑 scripts/bench_three_way.py 重新生成基线。"
                )
            header = fields
            missing = [name for name in REQUIRED_COLUMNS if name not in header]
            if missing:
                raise InputError(f"[load] {path} 缺少列: {', '.join(missing)}")
            continue
        record = dict(zip(header, fields))
        rows[record["bench"]] = record
    if header is None or not rows:
        raise InputError(f"[load] {path} 没有数据行")
    return Baseline(rows=rows, env=env, fmt=fmt, origin=origin)


def load_baseline_from_git(ref: str) -> Baseline:
    try:
        res = subprocess.run(
            ["git", "show", f"{ref}:{BASELINE_PATH_IN_GIT}"],
            capture_output=True,
            text=True,
            cwd=ROOT,
        )
    except OSError as exc:
        raise InputError(f"[baseline] 无法执行 git: {exc}") from exc
    if res.returncode != 0:
        raise InputError(
            f"[baseline] {ref}:{BASELINE_PATH_IN_GIT} 不存在; "
            f"先用 --baseline 指定文件, 或先提交基线。\n{res.stderr.strip()}"
        )
    tmp = ROOT / "build" / "bench" / "baseline_from_git.csv"
    tmp.parent.mkdir(parents=True, exist_ok=True)
    tmp.write_text(res.stdout, encoding="utf-8")
    return load_csv(tmp, origin=f"{ref}:{BASELINE_PATH_IN_GIT}")


def _pct_change(current: float, baseline: float) -> float:
    if baseline <= 0:
        return float("nan")
    return (current - baseline) / baseline * 100.0


def check(current: Baseline, baseline: Baseline, ratio_tol: float, c_tol: float) -> int:
    regressions: list[str] = []
    notes: list[str] = []
    warnings: list[str] = []

    print(f"基线: {baseline.origin}")
    print(f"判据: raw/C 与 fat/C 比值 +{ratio_tol:.0f}% (单侧; C 绝对时间 +{c_tol:.0f}% 仅警告)")
    print("")
    print(
        "| 基准 | raw/C 基线 | raw/C 当前 | Δ% | fat/C 基线 | fat/C 当前 | Δ% "
        "| C 基线 min (ms) | C 当前 min (ms) | ΔC% | 判定 |"
    )
    print("| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |")

    for name in sorted(current.rows):
        row = current.rows[name]
        if row.get("stdout_match") != "yes":
            regressions.append(f"{name}: stdout_match={row.get('stdout_match')} (raw/fat 语义不一致)")
        if row.get("c_check") != "pass":
            regressions.append(f"{name}: c_check={row.get('c_check')} (C 基线未跑出权威值)")
        base = baseline.rows.get(name)
        if base is None:
            notes.append(f"{name}: 基线缺失 (新增基准), 未判定")
            continue
        cur_raw = float(row["ratio_raw_c"])
        cur_fat = float(row["ratio_fat_c"])
        base_raw = float(base["ratio_raw_c"])
        base_fat = float(base["ratio_fat_c"])
        d_raw = _pct_change(cur_raw, base_raw)
        d_fat = _pct_change(cur_fat, base_fat)
        cur_c = float(row["c_min_ms"])
        base_c = float(base["c_min_ms"])
        d_c = _pct_change(cur_c, base_c)
        verdict = "ok"
        if d_raw > ratio_tol:
            verdict = "**raw 变差**"
            regressions.append(
                f"{name}: raw/C {base_raw:.2f} → {cur_raw:.2f} ({d_raw:+.1f}%)"
            )
        if d_fat > ratio_tol:
            verdict = "**fat 变差**"
            regressions.append(
                f"{name}: fat/C {base_fat:.2f} → {cur_fat:.2f} ({d_fat:+.1f}%)"
            )
        if d_c > c_tol:
            warnings.append(
                f"{name}: C 绝对时间 {base_c:.1f} → {cur_c:.1f} ms ({d_c:+.1f}%): "
                f"机器/工具链状态变化 (比值仍可对照)"
            )
        print(
            f"| {name} | {base_raw:.2f} | {cur_raw:.2f} | {d_raw:+.1f} | "
            f"{base_fat:.2f} | {cur_fat:.2f} | {d_fat:+.1f} | "
            f"{base_c:.1f} | {cur_c:.1f} | {d_c:+.1f} | {verdict} |"
        )

    missing = sorted(set(baseline.rows) - set(current.rows))
    for name in missing:
        notes.append(f"{name}: 当前结果缺失 (未测量), 未判定")

    print("")
    for note in notes:
        print(f"提示: {note}")
    for item in warnings:
        print(f"警告: {item}")

    if regressions:
        print("")
        for item in regressions:
            print(f"FAIL: {item}")
        print(f"\n结果: 失败 ({len(regressions)} 项)")
        return 1
    print("\n结果: 通过")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="C / raw / fat 三态性能基线回归门禁")
    parser.add_argument("--current", type=Path, default=CURRENT_CSV,
                        help=f"当前测量结果 (默认 {CURRENT_CSV.relative_to(ROOT)})")
    parser.add_argument("--baseline", type=Path, help="基线 csv 文件 (默认取 git HEAD)")
    parser.add_argument("--baseline-ref", default="HEAD", help="取基线的 git 版本 (默认 HEAD)")
    parser.add_argument("--ratio-tolerance", type=float, default=20.0,
                        help="raw/C 与 fat/C 比值允许变大的百分比 (默认 20)")
    parser.add_argument("--c-tolerance", type=float, default=20.0,
                        help="C 绝对时间允许变慢的百分比 (仅警告, 默认 20)")
    parser.add_argument("--allow-env-mismatch", action="store_true",
                        help="环境指纹不一致时仍判定 (跨机比较, 结果仅供参考)")
    args = parser.parse_args()

    try:
        current = load_csv(args.current, origin=str(args.current))
        baseline = (
            load_csv(args.baseline, origin=str(args.baseline))
            if args.baseline is not None
            else load_baseline_from_git(args.baseline_ref)
        )
    except InputError as exc:
        print(exc, file=sys.stderr)
        return 2

    mismatched = [
        f"{key}: 基线 {baseline.env.get(key, '?')} / 当前 {current.env.get(key, '?')}"
        for key in ENV_KEYS
        if baseline.env.get(key) and current.env.get(key) != baseline.env.get(key)
    ]
    if mismatched and not args.allow_env_mismatch:
        print("[env] 环境指纹不一致, 拒绝判定 (跨机绝对时间不可比):")
        for item in mismatched:
            print(f"  - {item}")
        print("如需强制对照, 加 --allow-env-mismatch (结论仅供参考)。")
        return 2
    for item in mismatched:
        print(f"[env] 警告: 指纹不一致但仍判定 — {item}")

    return check(current, baseline, args.ratio_tolerance, args.c_tolerance)


if __name__ == "__main__":
    sys.exit(main())
