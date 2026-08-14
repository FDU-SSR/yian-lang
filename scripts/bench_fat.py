#!/usr/bin/env python3
"""bench_fat.py — 胖指针安全性能实测脚本 (fat-perf-eval / raw-pointers-eval task-3).

按 docs/security-code.md §10.3–§10.5 协议, 在同一机器上实测 3 个基准
(ptr_traverse / alloc_dense / mixed) × 三态:
  ① raw         裸 8B 指针 (--raw-pointers, 无锁槽/帧锁/检查) — 零安全基线
  ② nocheck     胖 40B 表示但检查关 (--no-fat-checks, 保留锁槽/帧锁) — 表示成本对照
  ③ check       完整胖指针 (默认, 40B + 全部安全检查) — 完整安全
每态 ≥5 次取中位数 (报告 IQR/min/max), 指标为端到端墙钟时间 (ms) 与峰值常驻
内存 (Maximum resident set size); 并加入手写语义对齐的 ASan C 版对照
(clang -O2 -fsanitize=address)。

成本分解 (维度①表 + 计算):
  表示成本 = ②nocheck − ①raw     (胖 40B 表示相对裸 8B 的开销)
  检查成本 = ③check − ②nocheck   (检查发射相对无检查胖的开销)
  总成本   = ③check − ①raw       (完整胖相对裸指针的总开销)

开/关差异严格限定为检查发射 (--no-fat-checks 保留 40B 表示 / 锁槽 / 帧锁)。
每检查开销 = (开检查中位数时间 − 关中位数时间) / 负载访问次数 (基准注释密度特征估算)。

用法:
  python3 scripts/bench_fat.py                # 编译三态 + ASan 并全部测量, 写 results.md
  python3 scripts/bench_fat.py --runs 7       # 每态运行次数 (默认 5, §10.5 要求 ≥5)
  python3 scripts/bench_fat.py --pin 4        # taskset 绑核降噪
  python3 scripts/bench_fat.py --skip-asan    # 跳过 ASan 对照
  python3 scripts/bench_fat.py --no-compile   # 不重新编译, 仅测量已存在二进制
  python3 scripts/bench_fat.py --raw-only     # 仅编译+测量 raw 态 (增补维度①, 不触碰其他态)

独立脚本: 不触碰 scripts/run_tests.py / run_fat*.py 等测试 runner; 不修改基准源码。
输出: build/bench/results.md (另在控制台打印摘要)。
"""

from __future__ import annotations

import argparse
import datetime
import os
import platform
import re
import shutil
import statistics
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LIB = ROOT / "lib"
BENCH_DIR = ROOT / "bench"
ASAN_DIR = BENCH_DIR / "asan"
OUT_DIR = ROOT / "build" / "bench"
RESULTS_MD = OUT_DIR / "results.md"

TIME_BIN = "/usr/bin/time"
DEFAULT_RUNS = 5

_RSS_RE = re.compile(r"Maximum resident set size \(kbytes\): (\d+)")


@dataclass(frozen=True)
class BenchSpec:
    name: str
    access_count: int
    access_desc: str


@dataclass
class Stats:
    med: float
    iqr: float
    min: float
    max: float
    raw: list[float]


@dataclass
class SampleSummary:
    time: Stats
    rss: Stats


@dataclass
class MeasRow:
    bench: str
    state: str  # "check" | "nocheck" | "raw" | "asan"
    stats: SampleSummary


# 访问/检查次数估算依据 = 基准头部注释的密度特征 (task-2 记录):
#   - ptr_traverse: P×N×4 = 5000×10000×4 = 2×10^8 检查承载访问
#   - alloc_dense:  R1×8 + R2×2 = 220000×8 + 150000×2 ≈ 2×10^6 Malloc/Delete 路径事件
#   - mixed:        M×N = 1600×60000 = 9.6×10^7 元素访问 (pass2 主力, 每元素 ≈3 check)
BENCHES = [
    BenchSpec(
        name="ptr_traverse",
        access_count=200_000_000,
        access_desc=(
            "P×N×4 = 5000×10000×4 = 2×10^8 检查承载访问 (遍历期每结点每趟 "
            "3×CheckSafeAccess + 1×CheckPtrCmp, task-2 记录)"
        ),
    ),
    BenchSpec(
        name="alloc_dense",
        access_count=2_060_000,
        access_desc=(
            "R1×8 + R2×2 = 220000×8 + 150000×2 = 2.06×10^6 ≈ 2×10^6 "
            "Malloc/Delete 路径事件 (Phase A 每轮 4 grow = 4 malloc + 3 free "
            "+ 1 drop free = 4 malloc + 4 free, Phase B 每轮 1 malloc + 1 del, "
            "task-2 记录)"
        ),
    ),
    BenchSpec(
        name="mixed",
        access_count=96_000_000,
        access_desc=(
            "M×N = 1600×60000 = 9.6×10^7 元素访问 (pass2 指针游标主力, 每元素 "
            "≈3 check: CheckPtrCmp + CheckElementArith + CheckSafeAccess, task-2 记录)"
        ),
    ),
]


def _env_asan() -> dict[str, str]:
    env = os.environ.copy()
    env["LANG"] = "C"
    env["ASAN_OPTIONS"] = "detect_leaks=0"
    return env


def _env_plain() -> dict[str, str]:
    env = os.environ.copy()
    env["LANG"] = "C"
    return env


def compile_an(spec: BenchSpec, no_checks: bool, raw: bool = False) -> Path:
    """编译 .an 基准为 native exe; no_checks=True → --no-fat-checks 基线;
    raw=True → --raw-pointers 裸 8B 指针 (零检查/锁槽/帧锁, 维度①)。"""
    if raw:
        bin_path = OUT_DIR / f"{spec.name}_raw"
    elif no_checks:
        bin_path = OUT_DIR / f"{spec.name}_nfc"
    else:
        bin_path = OUT_DIR / spec.name
    cmd = [sys.executable, "-m", "compiler.main", "-O2"]
    if raw:
        cmd.append("--raw-pointers")
    elif no_checks:
        cmd.append("--no-fat-checks")
    cmd += [str(LIB), str(BENCH_DIR / f"{spec.name}.an"), "-o", str(bin_path)]
    res = subprocess.run(cmd, capture_output=True, text=True, cwd=ROOT)
    if res.returncode != 0:
        kind = "raw" if raw else ("no-checks" if no_checks else "checked")
        raise SystemExit(
            f"[compile] {spec.name} ({kind}) failed:"
            f"\n{res.stdout}\n{res.stderr}"
        )
    return bin_path


def compile_asan(spec: BenchSpec) -> Path:
    """编译语义对齐的 ASan C 版。"""
    c_file = ASAN_DIR / f"{spec.name}.c"
    out = OUT_DIR / f"asan_{spec.name}"
    cmd = ["clang", "-O2", "-fsanitize=address", str(c_file), "-o", str(out)]
    res = subprocess.run(cmd, capture_output=True, text=True, cwd=ROOT)
    if res.returncode != 0:
        raise SystemExit(f"[compile] ASan {spec.name} failed:\n{res.stdout}\n{res.stderr}")
    return out


def measure(binary: Path, runs: int, pin: int | None, asan: bool = False) -> list[tuple[float, int]]:
    """运行 runs 次, 返回 [(wall_ms, rss_kb), ...]; 先弃 1 次 warmup。"""
    env = _env_asan() if asan else _env_plain()
    pre: list[str] = []
    if pin is not None:
        pre = ["taskset", "-c", str(pin)]

    samples: list[tuple[float, int]] = []
    # warmup (确认稳定窗口, §10.5 步骤 2), 不计入样本
    subprocess.run(pre + [TIME_BIN, "-v", str(binary)], capture_output=True, text=True, env=env)
    for _ in range(runs):
        cmd = pre + [TIME_BIN, "-v", str(binary)]
        t0 = time.monotonic()
        res = subprocess.run(cmd, capture_output=True, text=True, env=env)
        wall_ms = (time.monotonic() - t0) * 1000.0
        if res.returncode != 0:
            raise SystemExit(f"[run] {binary} exited {res.returncode}:\n{res.stdout}\n{res.stderr}")
        m = _RSS_RE.search(res.stderr)
        if m is None:
            raise SystemExit(
                f"[run] {binary}: 无法从 /usr/bin/time -v 输出解析 Maximum resident set size:\n"
                f"{res.stderr}"
            )
        samples.append((wall_ms, int(m.group(1))))
    return samples


def summarize(samples: list[tuple[float, int]]) -> SampleSummary:
    """中位数 / IQR / min / max / 原始样本。"""
    times = sorted(s[0] for s in samples)
    rss: list[float] = sorted(float(s[1]) for s in samples)

    def q1_q3(vals: list[float]) -> tuple[float, float]:
        try:
            qs = statistics.quantiles(vals, n=4, method="exclusive")
            return qs[0], qs[2]
        except statistics.StatisticsError:  # 样本太少
            return float(vals[0]), float(vals[-1])

    t_q1, t_q3 = q1_q3(times)
    r_q1, r_q3 = q1_q3(rss)
    return SampleSummary(
        time=Stats(
            med=statistics.median(times),
            iqr=t_q3 - t_q1,
            min=times[0],
            max=times[-1],
            raw=[s[0] for s in samples],
        ),
        rss=Stats(
            med=statistics.median(rss),
            iqr=r_q3 - r_q1,
            min=rss[0],
            max=rss[-1],
            raw=[s[1] for s in samples],
        ),
    )


def machine_header() -> list[str]:
    lines = [
        f"- 日期: {datetime.date.today().isoformat()}",
        f"- 机器: {platform.machine()} / {platform.processor()} / {os.cpu_count()} 逻辑核",
        f"- Python: {platform.python_version()} | clang: {shutil.which('clang')}",
    ]
    try:
        res = subprocess.run(
            ["clang", "--version"], capture_output=True, text=True, timeout=10
        )
        lines.append(f"  `{res.stdout.splitlines()[0] if res.stdout else 'n/a'}`")
    except (OSError, subprocess.SubprocessError):
        pass
    return lines


def fmt_ms(v: float) -> str:
    return f"{v:.1f}"


def fmt_rss_mb(kb: float) -> str:
    return f"{kb / 1024.0:.1f}"


def main() -> int:
    ap = argparse.ArgumentParser(description="胖指针安全检查性能实测 (fat-perf-eval / raw-pointers-eval task-3)")
    ap.add_argument("--runs", type=int, default=DEFAULT_RUNS, help=f"每态运行次数 (默认 {DEFAULT_RUNS}, 协议要求 ≥5)")
    ap.add_argument("--pin", type=int, default=None, help="taskset 绑定的 CPU 编号 (降噪)")
    ap.add_argument("--skip-asan", action="store_true", help="跳过 ASan 对照编译与测量")
    ap.add_argument("--no-compile", action="store_true", help="不重新编译, 仅测量已存在二进制")
    ap.add_argument("--raw-only", action="store_true", help="仅编译+测量 raw 态 (增补维度①)")
    args = ap.parse_args()

    if args.runs < 1:
        raise SystemExit("--runs 必须 ≥ 1 (协议建议 ≥5)")
    if not TIME_BIN or not os.path.exists(TIME_BIN):
        raise SystemExit(f"缺少 {TIME_BIN} (GNU time); 需要 -v 输出峰值常驻内存")
    if args.pin is not None and shutil.which("taskset") is None:
        raise SystemExit("--pin 需要 taskset")
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    runs = args.runs
    rows: list[MeasRow] = []
    asan_rows: list[MeasRow] = []

    for spec in BENCHES:
        if not args.no_compile:
            if args.raw_only:
                compile_an(spec, no_checks=False, raw=True)
            else:
                compile_an(spec, no_checks=False)
                compile_an(spec, no_checks=True)
                compile_an(spec, no_checks=False, raw=True)
        if args.raw_only:
            bin_raw = OUT_DIR / f"{spec.name}_raw"
            samples = measure(bin_raw, runs, args.pin)
            rows.append(MeasRow(bench=spec.name, state="raw", stats=summarize(samples)))
            print(f"[done] {spec.name}: raw 态 {runs} 次", file=sys.stderr)
            continue
        bin_on = OUT_DIR / spec.name
        bin_off = OUT_DIR / f"{spec.name}_nfc"
        bin_raw = OUT_DIR / f"{spec.name}_raw"
        for label, binary in (
            ("check", bin_on),
            ("nocheck", bin_off),
            ("raw", bin_raw),
        ):
            samples = measure(binary, runs, args.pin)
            rows.append(MeasRow(bench=spec.name, state=label, stats=summarize(samples)))
        print(f"[done] {spec.name}: 三态 (raw/nocheck/check) 各 {runs} 次", file=sys.stderr)

    if not args.skip_asan:
        for spec in BENCHES:
            if not args.no_compile:
                compile_asan(spec)
            samples = measure(OUT_DIR / f"asan_{spec.name}", runs, args.pin, asan=True)
            asan_rows.append(MeasRow(bench=spec.name, state="asan", stats=summarize(samples)))
            print(f"[done] ASan {spec.name}: {runs} 次", file=sys.stderr)

    # ---- 渲染 markdown ----
    state_label = {"check": "③完整胖", "nocheck": "②胖无检查", "raw": "①裸指针"}
    md: list[str] = []
    md.append(
        "# build/bench/results.md — 胖指针安全性能实测 (fat-perf-eval / raw-pointers-eval task-3)\n"
    )
    md.append("## 0) 环境与协议\n")
    md.append("".join(f"{l}\n" for l in machine_header()))
    md.append(
        "- 三态编译 (`python3 -m compiler.main -O2 lib bench/<name>.an -o build/bench/<name>`"
        " / 无检查加 `--no-fat-checks` / 裸指针加 `--raw-pointers`), 每态运行 "
        f"{runs} 次取中位数, 报告 IQR/min/max (docs/security-code.md §10.5, 同一机器同一负载)。\n"
    )
    md.append(
        "- 三态定义: ①raw = 裸 8B 指针 (--raw-pointers, 无锁槽/帧锁/检查, 零安全基线); "
        "②nocheck = 胖 40B 表示但检查关 (--no-fat-checks, 保留锁槽/帧锁); "
        "③check = 完整胖指针 (40B + 全部安全检查)。\n"
    )
    md.append(
        "- 指标: 端到端墙钟时间 (ms, `time.monotonic()` 包住 `/usr/bin/time -v` 执行) + "
        "峰值常驻内存 (Maximum resident set size, KB)。\n"
    )
    md.append(
        "- 开/关差异严格限定为检查发射 (--no-fat-checks 保留 40B 胖指针表示 / 锁槽 / 帧锁, "
        "task-1 记录); ASan 对照为手写语义对齐 C 版, `clang -O2 -fsanitize=address`, "
        "`ASAN_OPTIONS=detect_leaks=0`。\n"
    )
    if args.pin is not None:
        md.append(f"- 降噪: `taskset -c {args.pin}` 绑定单核。\n")
    md.append("\n## 1) 每检查开销估算方法\n")
    md.append(
        "访问计数取自各基准头部注释的密度特征 (task-2 记录), 每检查开销 = "
        "「开检查中位数时间 − 关中位数时间」/ 访问计数:\n"
    )
    md.append("| 基准 | 访问计数 (估算) | 估算来源 |\n|---|---|---|\n")
    for spec in BENCHES:
        md.append(f"| {spec.name} | {spec.access_count:,} | {spec.access_desc} |\n")

    md.append("\n## 2) 三维实测时间结果 (维度①: 裸 / 胖无检查 / 完整胖)\n")
    md.append("| 基准 | 态 | 时间中位数 (ms) | IQR (ms) | min–max (ms) | 峰值 RSS 中位数 (MB) | RSS IQR (MB) |\n")
    md.append("|---|---|---|---|---|---|---|\n")
    for r in rows:
        md.append(
            f"| {r.bench} | {state_label[r.state]} "
            f"| {fmt_ms(r.stats.time.med)} | {fmt_ms(r.stats.time.iqr)} "
            f"| {fmt_ms(r.stats.time.min)}–{fmt_ms(r.stats.time.max)} "
            f"| {fmt_rss_mb(r.stats.rss.med)} | {fmt_rss_mb(r.stats.rss.iqr)} |\n"
        )

    md.append("\n## 3) 成本分解: 表示成本 / 检查成本 / 总成本\n")
    md.append(
        "以维度①裸指针为基准: 表示成本 = ②nocheck − ①raw (胖 40B 表示相对裸 8B); "
        "检查成本 = ③check − ②nocheck (检查发射); 总成本 = ③check − ①raw。\n"
    )
    md.append("| 基准 | 表示成本 Δms (②−①) | 检查成本 Δms (③−②) | 总成本 Δms (③−①) | 表示成本 ΔRSS (MB) | 总成本 ΔRSS (MB) |\n")
    md.append("|---|---|---|---|---|---|\n")
    for spec in BENCHES:
        on = next(r for r in rows if r.bench == spec.name and r.state == "check")
        off = next(r for r in rows if r.bench == spec.name and r.state == "nocheck")
        raw = next(r for r in rows if r.bench == spec.name and r.state == "raw")
        rep_ms = off.stats.time.med - raw.stats.time.med
        chk_ms = on.stats.time.med - off.stats.time.med
        tot_ms = on.stats.time.med - raw.stats.time.med
        rep_rss = (off.stats.rss.med - raw.stats.rss.med) / 1024.0
        tot_rss = (on.stats.rss.med - raw.stats.rss.med) / 1024.0
        md.append(
            f"| {spec.name} | {rep_ms:+.1f} | {chk_ms:+.1f} | {tot_ms:+.1f} "
            f"| {rep_rss:+.2f} | {tot_rss:+.2f} |\n"
        )
    md.append(
        "\n注: 表示成本含胖指针 5 字段读写 / 分配块锁槽头 / 帧锁保留带来的访存与占用; "
        "检查成本含 CheckSafeAccess/CheckInBounds/CheckElementArith/CheckPtrCmp/GenKey/锁槽写等。\n"
    )

    md.append("\n## 4) 每检查开销与内存开销\n")
    md.append("| 基准 | Δ时间 (检查开−关, ms) | 访问计数 | 每检查/每访问 (ns) | Δ峰值 RSS (开−关, MB) |\n")
    md.append("|---|---|---|---|---|\n")
    for spec in BENCHES:
        on = next(r for r in rows if r.bench == spec.name and r.state == "check")
        off = next(r for r in rows if r.bench == spec.name and r.state == "nocheck")
        delta_ms = on.stats.time.med - off.stats.time.med
        per_check_ns = delta_ms * 1e6 / spec.access_count
        delta_rss_mb = (on.stats.rss.med - off.stats.rss.med) / 1024.0
        md.append(
            f"| {spec.name} | {delta_ms:+.1f} | {spec.access_count:,} "
            f"| {per_check_ns:+.2f} | {delta_rss_mb:+.2f} |\n"
        )
    md.append(
        "\n注: 每检查开销 = Δ时间/访问计数。alloc_dense 的计数为 Malloc/Delete 路径事件"
        " (每事件内含 GenKey/锁槽写/free 校验多项操作); mixed 计数为元素访问"
        " (pass2 每元素 ≈3 个不同 check 类型), 故该两行是「每访问事件」开销而非单条检查指令。\n"
    )

    md.append("\n## 5) ASan 对照\n")
    md.append("| 基准 | 态 | 时间中位数 (ms) | IQR (ms) | min–max (ms) | 峰值 RSS 中位数 (MB) |\n")
    md.append("|---|---|---|---|---|---|\n")
    for r in rows:
        md.append(
            f"| {r.bench} | {state_label[r.state]} "
            f"| {fmt_ms(r.stats.time.med)} | {fmt_ms(r.stats.time.iqr)} "
            f"| {fmt_ms(r.stats.time.min)}–{fmt_ms(r.stats.time.max)} "
            f"| {fmt_rss_mb(r.stats.rss.med)} |\n"
        )
    for a in asan_rows:
        md.append(
            f"| {a.bench} | ASan (C, clang -O2) "
            f"| {fmt_ms(a.stats.time.med)} | {fmt_ms(a.stats.time.iqr)} "
            f"| {fmt_ms(a.stats.time.min)}–{fmt_ms(a.stats.time.max)} "
            f"| {fmt_rss_mb(a.stats.rss.med)} |\n"
        )

    md.append("\n## 6) 原始样本 (附录)\n")
    md.append("格式: 每样本 `wall_ms` (rss_kb)。\n")
    for r in rows:
        md.append(
            f"- {r.bench} / {state_label[r.state]}: "
            f"{', '.join(f'{t:.1f} ({rss})' for t, rss in zip(r.stats.time.raw, r.stats.rss.raw))}\n"
        )
    for a in asan_rows:
        md.append(
            f"- {a.bench} / ASan: "
            f"{', '.join(f'{t:.1f} ({rss})' for t, rss in zip(a.stats.time.raw, a.stats.rss.raw))}\n"
        )

    RESULTS_MD.write_text("".join(md), encoding="utf-8")
    print(f"\n结果写入 {RESULTS_MD}\n")

    # ---- 控制台摘要 ----
    print("基准         态          时间中位数(ms)  峰值RSS(MB)  每检查(ns)")
    if args.raw_only:
        for spec in BENCHES:
            raw = next(r for r in rows if r.bench == spec.name and r.state == "raw")
            print(
                f"{spec.name:<12} ①裸指针    {fmt_ms(raw.stats.time.med):>10}   "
                f"{fmt_rss_mb(raw.stats.rss.med):>8}"
            )
        return 0
    for spec in BENCHES:
        on = next(r for r in rows if r.bench == spec.name and r.state == "check")
        off = next(r for r in rows if r.bench == spec.name and r.state == "nocheck")
        raw = next(r for r in rows if r.bench == spec.name and r.state == "raw")
        delta_ms = on.stats.time.med - off.stats.time.med
        per_ns = delta_ms * 1e6 / spec.access_count
        print(
            f"{spec.name:<12} ③完整胖    {fmt_ms(on.stats.time.med):>10}   "
            f"{fmt_rss_mb(on.stats.rss.med):>8}    {per_ns:+.2f}"
        )
        print(
            f"{'':12} ②胖无检查  {fmt_ms(off.stats.time.med):>10}   "
            f"{fmt_rss_mb(off.stats.rss.med):>8}"
        )
        print(
            f"{'':12} ①裸指针    {fmt_ms(raw.stats.time.med):>10}   "
            f"{fmt_rss_mb(raw.stats.rss.med):>8}"
        )
        a = next((x for x in asan_rows if x.bench == spec.name), None)
        if a is not None:
            print(
                f"{'':12} ASan      {fmt_ms(a.stats.time.med):>10}   "
                f"{fmt_rss_mb(a.stats.rss.med):>8}"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
