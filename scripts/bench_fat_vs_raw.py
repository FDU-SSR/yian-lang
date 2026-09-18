#!/usr/bin/env python3
"""bench_fat_vs_raw.py — 胖指针 vs 裸指针两态性能实测 (fat vs raw, 不含 nocheck).

协议 (与 docs/plan/fat-vs-raw-bench-plan.md §3.3 一致):
  - 同一份源码两态共用: fat 态 `-O2`; raw 态 `-O2 --raw-pointers`; 同一 `lib/src`。
    基准目录内 `<name>.raw.an` 存在时, raw 态改用该文件 (如 storage: fat 与 raw
    的语义形式无法共用一份源, 详见 bench/README.md)。
  - 绑核 (`taskset -c <cpu>`, 可选但正式记录必须绑核)。
  - 每基准两态**逐次交替**测量: 两态各 1 次 warmup (不计入样本), 再交替测量
    N 次 (默认 5, 协议要求 ≥5); 正式指标取**最小值**, 同时记录中位数与 CV。指标
    为端到端墙钟时间 (ms) 与峰值常驻内存 (Maximum resident set size, 取各次最大
    值)。逐次交替消除两态之间的跨时段系统状态漂移 (块式测量会把漂移记成态间差异)。
    取最小值而非中位数: 同一二进制重复运行会落在相差约 20% 的两个性能状态上
    (进程级内存布局/频率假象, 与代码无关), 最小值估计无干扰性能, 中位数会被
    落态运气左右 (实测同一二进制 12 次在 1.87 s 与 2.25 s 间交替)。
  - 自适应降次: 若某基准两态 warmup 合计超过 --max-state-sec, 测量次数降到 3。
  - 语义护栏: 两态 warmup 的 stdout 与退出码必须一致, 不一致则报错 (报告列标注)。

用法 (需在已安装 yianc 的环境下运行, 如 yian-env; 编译由 `sys.executable` 驱动,
因此解释器决定 llvmlite/LLVM 版本, 该版本是基线的一部分):

  python3 scripts/bench_fat_vs_raw.py                      # 全部基准
  python3 scripts/bench_fat_vs_raw.py --names list,queen    # 指定基准 (逗号分隔)
  python3 scripts/bench_fat_vs_raw.py --pin 4 --runs 5      # 绑核 + 测量次数
  python3 scripts/bench_fat_vs_raw.py --no-compile          # 复用已有二进制只测量
  python3 scripts/bench_fat_vs_raw.py --compile-only        # 只编译不测量
  python3 scripts/bench_fat_vs_raw.py --max-state-sec 120   # 慢基准降次阈值 (秒)

输出:
  bench/results.md   表格 + 环境指纹 + 协议说明 (人读)
  bench/results.csv  机读基线 (scripts/check_bench_regression.py 的门禁对照)
  --names 部分测量写入 bench/results.partial.{md,csv}, 不覆盖全量基线

独立脚本: 不触碰 scripts/run_tests.py; 不修改基准源码; 二进制与日志写入 build/bench/。
"""

from __future__ import annotations

import argparse
import datetime
import math
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
LIB = ROOT / "lib" / "src"
BENCH_DIR = ROOT / "bench"
SHOOTOUT_DIR = BENCH_DIR / "shootout"
OUT_DIR = ROOT / "build" / "bench"
RESULTS_MD = BENCH_DIR / "results.md"
RESULTS_CSV = BENCH_DIR / "results.csv"
PARTIAL_MD = BENCH_DIR / "results.partial.md"
PARTIAL_CSV = BENCH_DIR / "results.partial.csv"

OPT_LEVEL = "-O2"
TIME_BIN = "/usr/bin/time"
DEFAULT_RUNS = 5
DEFAULT_MAX_STATE_SEC = 120.0
RAW_SUFFIX = ".raw.an"

_RSS_RE = re.compile(r"Maximum resident set size \(kbytes\): (\d+)")


@dataclass(frozen=True)
class BenchSpec:
    """一个基准: 胖态源码 + 裸态源码 (缺省同名, 有 `<name>.raw.an` 则用覆盖源)。"""

    name: str
    fat_src: Path
    raw_src: Path

    @property
    def raw_override(self) -> bool:
        return self.raw_src != self.fat_src


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
    fat_time: Stats
    raw_time: Stats
    fat_rss_mb: float
    raw_rss_mb: float
    runs: int
    stdout_match: bool
    exit_codes: tuple[int, int]


def spec_bin(spec: BenchSpec, raw: bool) -> Path:
    return OUT_DIR / f"{spec.name}.{'raw' if raw else 'fat'}"


def discover() -> list[BenchSpec]:
    """发现 bench/shootout 下的基准; `<name>.raw.an` 作为同名基准的裸态覆盖源。"""
    fat_srcs: dict[str, Path] = {}
    raw_srcs: dict[str, Path] = {}
    for path in sorted(SHOOTOUT_DIR.glob("*.an")):
        if path.name.endswith(RAW_SUFFIX):
            raw_srcs[path.name[: -len(RAW_SUFFIX)]] = path
        else:
            fat_srcs[path.stem] = path
    if not fat_srcs:
        raise SystemExit(f"[discover] {SHOOTOUT_DIR} 下没有 .an 基准")
    orphans = sorted(set(raw_srcs) - set(fat_srcs))
    if orphans:
        raise SystemExit(f"[discover] 裸态覆盖源缺少同名胖态源: {', '.join(orphans)}")
    return [
        BenchSpec(name=name, fat_src=fat_srcs[name], raw_src=raw_srcs.get(name, fat_srcs[name]))
        for name in sorted(fat_srcs)
    ]


def compile_an(spec: BenchSpec, raw: bool) -> Path:
    """编译单态基准; 失败即终止 (编译错误属于基线失效, 不做部分结果)。"""
    binary = spec_bin(spec, raw)
    src = spec.raw_src if raw else spec.fat_src
    cmd = [sys.executable, "-m", "compiler.main", OPT_LEVEL]
    if raw:
        cmd.append("--raw-pointers")
    cmd += [str(LIB), str(src), "-o", str(binary)]
    binary.parent.mkdir(parents=True, exist_ok=True)
    res = subprocess.run(cmd, capture_output=True, text=True, cwd=ROOT)
    if res.returncode != 0:
        state = "raw" if raw else "fat"
        raise SystemExit(
            f"[compile] {spec.name} ({state}) 失败:\n{res.stdout}\n{res.stderr}"
        )
    return binary


def _env_plain() -> dict[str, str]:
    env = os.environ.copy()
    env["LANG"] = "C"
    return env


def _run(binary: Path, pin: int | None) -> tuple[float, int, str, int]:
    """运行一次: 返回 (墙钟 ms, 峰值 RSS KB, stdout, 退出码)。"""
    pre = ["taskset", "-c", str(pin)] if pin is not None else []
    t0 = time.monotonic()
    res = subprocess.run(
        pre + [TIME_BIN, "-v", str(binary)],
        capture_output=True,
        text=True,
        env=_env_plain(),
    )
    wall_ms = (time.monotonic() - t0) * 1000.0
    match = _RSS_RE.search(res.stderr)
    if match is None:
        raise SystemExit(
            f"[run] {binary}: 无法从 /usr/bin/time -v 输出解析 Maximum resident set size:\n"
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
    except statistics.StatisticsError:  # 单样本
        cv = 0.0
    return Stats(
        med=statistics.median(ordered),
        iqr=iqr,
        cv=cv,
        min=ordered[0],
        max=ordered[-1],
        samples=list(samples),
    )


def measure_pair(
    spec: BenchSpec, runs: int, pin: int | None, max_state_sec: float
) -> Row:
    """两态逐次交替测量: warmup 各 1 次 → 交替测量 used_runs 次。"""
    fat_bin = spec_bin(spec, raw=False)
    raw_bin = spec_bin(spec, raw=True)

    t0 = time.monotonic()
    fat_warm = _run(fat_bin, pin)
    raw_warm = _run(raw_bin, pin)
    warm_sec = time.monotonic() - t0

    used_runs = runs
    if warm_sec > max_state_sec and runs > 3:
        used_runs = 3
        print(
            f"[{spec.name}] warmup {warm_sec:.1f}s > {max_state_sec:.0f}s, "
            f"测量次数降到 {used_runs}",
            file=sys.stderr,
        )

    fat_samples: list[float] = []
    raw_samples: list[float] = []
    fat_rss: list[int] = []
    raw_rss: list[int] = []
    for _ in range(used_runs):
        wall, rss, _, _ = _run(fat_bin, pin)
        fat_samples.append(wall)
        fat_rss.append(rss)
        wall, rss, _, _ = _run(raw_bin, pin)
        raw_samples.append(wall)
        raw_rss.append(rss)

    stdout_match = fat_warm[2] == raw_warm[2] and fat_warm[3] == raw_warm[3]
    if not stdout_match:
        print(
            f"[{spec.name}] 警告: 两态 warmup 的 stdout/退出码不一致 "
            f"(fat exit={fat_warm[3]}, raw exit={raw_warm[3]})",
            file=sys.stderr,
        )
    return Row(
        spec=spec,
        fat_time=_stats(fat_samples),
        raw_time=_stats(raw_samples),
        fat_rss_mb=max(fat_rss) / 1024.0,
        raw_rss_mb=max(raw_rss) / 1024.0,
        runs=used_runs,
        stdout_match=stdout_match,
        exit_codes=(fat_warm[3], raw_warm[3]),
    )


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


def _llvmlite_info() -> tuple[str, str]:
    """返回 (llvmlite 版本, LLVM 版本); 环境不可用时返回 ("n/a", "n/a")。"""
    try:
        import llvmlite  # noqa: PLC0415
        import llvmlite.binding as llvm_binding  # noqa: PLC0415
    except ImportError:
        return "n/a", "n/a"
    info = getattr(llvm_binding, "llvm_version_info", None)
    llvm_ver = ".".join(str(part) for part in info) if info else "n/a"
    return str(llvmlite.__version__), llvm_ver


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


def machine_fingerprint(pin: int | None, runs: int) -> dict[str, str]:
    llvmlite_ver, llvm_ver = _llvmlite_info()
    return {
        "hostname": platform.node() or "unknown",
        "machine": platform.machine(),
        "cpu_count": str(os.cpu_count() or 0),
        "clang": _tool_version("clang"),
        "python": platform.python_version(),
        "llvmlite": llvmlite_ver,
        "llvm": llvm_ver,
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


def render_md(rows: list[Row], fingerprint: dict[str, str]) -> str:
    lines: list[str] = []
    lines.append("# 胖指针 vs 裸指针实测结果 (fat vs raw)\n")
    lines.append(
        "由 `scripts/bench_fat_vs_raw.py` 生成; 两态同一份源码, 唯一差异是编译配置"
        " (无检查对照态 `nocheck` 已移除)。\n"
    )
    lines.append("## 环境指纹\n")
    lines.append("| 项 | 值 |")
    lines.append("| --- | --- |")
    lines.append(f"| 主机 | {fingerprint['hostname']} |")
    lines.append(f"| 架构 / 逻辑核 | {fingerprint['machine']} / {fingerprint['cpu_count']} |")
    lines.append(f"| clang | `{fingerprint['clang']}` |")
    lines.append(f"| Python | {fingerprint['python']} |")
    lines.append(
        f"| llvmlite | `{fingerprint['llvmlite']}` (LLVM {fingerprint['llvm']}) |"
    )
    lines.append(f"| 优化级 | `{fingerprint['opt']}` (两态相同) |")
    lines.append(f"| 绑核 | `taskset -c {fingerprint['pin']}` |")
    lines.append(f"| 每态测量次数 | {fingerprint['runs']} (另加 1 次 warmup) |")
    lines.append(f"| commit | `{fingerprint['commit']}` |")
    lines.append(f"| 日期 | {fingerprint['date']} |")
    lines.append("")

    lines.append("## 结果\n")
    lines.append(
        "| 基准 | fat 最小 (ms) | raw 最小 (ms) | 比值 fat/raw | fat 峰值 RSS (MB) "
        "| raw 峰值 RSS (MB) | 裸态源 | stdout 一致 |"
    )
    lines.append("| --- | ---: | ---: | ---: | ---: | ---: | --- | --- |")
    for row in rows:
        ratio = row.fat_time.min / row.raw_time.min if row.raw_time.min else float("nan")
        raw_src = f"`{row.spec.raw_src.name}`" if row.spec.raw_override else "同名"
        lines.append(
            f"| {row.spec.name} | {_fmt_ms(row.fat_time.min)} | {_fmt_ms(row.raw_time.min)} "
            f"| {ratio:.2f}× | {_fmt_mb(row.fat_rss_mb)} | {_fmt_mb(row.raw_rss_mb)} "
            f"| {raw_src} | {'是' if row.stdout_match else '**否**'} |"
        )
    geo = _geomean(
        [
            row.fat_time.min / row.raw_time.min
            for row in rows
            if row.raw_time.min and row.fat_time.min
        ]
    )
    if geo is not None:
        lines.append("")
        lines.append(f"几何平均比值 (fat/raw): **{geo:.2f}×** (n={len(rows)})。")
        fat_total = sum(row.fat_time.min for row in rows) / 1000.0
        raw_total = sum(row.raw_time.min for row in rows) / 1000.0
        lines.append(
            f"单趟全量耗时 (各基准最小值之和): fat **{fat_total:.1f} s** / "
            f"raw **{raw_total:.1f} s**。"
        )
    lines.append("")
    lines.append(
        "比值为最小值之比; 各基准不可加。`--raw-pointers` 关闭全部指针安全检查与"
        "锁槽/帧锁发射, 因此比值即完整胖指针相对裸指针的总开销。\n"
    )

    lines.append("## 重复性\n")
    lines.append(
        "| 基准 | fat 中位 | fat 最小 | fat 最大 | fat CV% | raw 中位 | raw 最小 "
        "| raw 最大 | raw CV% | 次数 |"
    )
    lines.append(
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |"
    )
    for row in rows:
        lines.append(
            f"| {row.spec.name} | {_fmt_ms(row.fat_time.med)} | {_fmt_ms(row.fat_time.min)} "
            f"| {_fmt_ms(row.fat_time.max)} | {row.fat_time.cv:.1f} "
            f"| {_fmt_ms(row.raw_time.med)} | {_fmt_ms(row.raw_time.min)} "
            f"| {_fmt_ms(row.raw_time.max)} | {row.raw_time.cv:.1f} | {row.runs} |"
        )
    lines.append("")
    lines.append(
        "正式指标取**最小值**: 机器处于高压/热态时, 同一二进制重复运行会落在相差约 "
        "20% 的两个性能状态上 (进程级内存布局/频率假象, 与代码无关; 实测同一二进制 "
        "12 次在 1.87 s 与 2.25 s 间交替, 也曾把同一二进制的两态对照差放大到 31%)。"
        "最小值估计无干扰性能, 中位数与 CV 用于判断重复性。\n"
    )

    lines.append("## 协议\n")
    lines.append(f"- 编译: `yianc {OPT_LEVEL} lib/src <src> -o <bin>`; 裸态追加 `--raw-pointers`。")
    lines.append(
        "- 测量: 每基准两态各 1 次 warmup (不计入样本) 后**逐次交替**测量; "
        "正式指标取各态最小值, 并记录中位数/四分位距/CV/峰值 RSS。"
    )
    lines.append(
        f"- 降噪: `taskset -c {fingerprint['pin']}`; 逐次交替消除跨时段漂移。"
    )
    lines.append(
        "- 语义护栏: 两态 stdout 与退出码必须一致, 报告中标 `否` 即基线失效。"
    )
    lines.append(
        "- 与分支 `archive/secl-paper-20260909` 协议的差异: 该分支为三态 "
        "(raw/nocheck/check) 且用 `-O3`、块式分态测量、取中位数; 此处按两态 "
        f"(`-O2`)、逐次交替、取最小值重新基线化, 历史数字不可直接对照。"
    )
    return "\n".join(lines).rstrip("\n") + "\n"


def write_csv(rows: list[Row], fingerprint: dict[str, str], path: Path) -> None:
    header = (
        "# bench/results.csv — fat vs raw 两态性能基线 "
        "(scripts/bench_fat_vs_raw.py 生成)"
    )
    comments = [
        header,
        "# 列: bench, fat_min_ms/raw_min_ms 各态最小值 (正式指标), "
        "fat_med_ms/raw_med_ms 中位数, fat_cv_pct/raw_cv_pct 重复运行变异系数 (%), "
        "ratio = fat_min/raw_min, ratio_med = fat_med/raw_med, "
        "fat_rss_mb/raw_rss_mb 峰值常驻, runs, stdout_match, raw_source",
        f"# fingerprint: hostname={fingerprint['hostname']} machine={fingerprint['machine']} "
        f"cpu_count={fingerprint['cpu_count']} clang={fingerprint['clang']}",
        f"# env: python={fingerprint['python']} llvmlite={fingerprint['llvmlite']} "
        f"llvm={fingerprint['llvm']} opt={fingerprint['opt']} pin={fingerprint['pin']} "
        f"runs={fingerprint['runs']}",
        f"# commit: {fingerprint['commit']}",
        f"# date: {fingerprint['date']}",
    ]
    lines = list(comments)
    lines.append(
        "bench,fat_min_ms,fat_med_ms,fat_cv_pct,raw_min_ms,raw_med_ms,raw_cv_pct,"
        "ratio,ratio_med,fat_rss_mb,raw_rss_mb,runs,stdout_match,raw_source"
    )
    for row in rows:
        ratio = row.fat_time.min / row.raw_time.min if row.raw_time.min else float("nan")
        ratio_med = (
            row.fat_time.med / row.raw_time.med if row.raw_time.med else float("nan")
        )
        raw_src = row.spec.raw_src.name if row.spec.raw_override else row.spec.fat_src.name
        lines.append(
            f"{row.spec.name},{row.fat_time.min:.1f},{row.fat_time.med:.1f},"
            f"{row.fat_time.cv:.2f},{row.raw_time.min:.1f},{row.raw_time.med:.1f},"
            f"{row.raw_time.cv:.2f},{ratio:.3f},{ratio_med:.3f},"
            f"{row.fat_rss_mb:.1f},{row.raw_rss_mb:.1f},{row.runs},"
            f"{'yes' if row.stdout_match else 'no'},{raw_src}"
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="胖指针 vs 裸指针两态性能实测")
    parser.add_argument("--names", help="只测指定基准 (逗号分隔)")
    parser.add_argument("--runs", type=int, default=DEFAULT_RUNS, help="每态测量次数 (默认 5)")
    parser.add_argument("--pin", type=int, help="taskset 绑定的 CPU 编号")
    parser.add_argument("--max-state-sec", type=float, default=DEFAULT_MAX_STATE_SEC,
                        help="两态 warmup 合计超过该秒数则测量次数降到 3")
    parser.add_argument("--no-compile", action="store_true", help="不重新编译, 复用已有二进制")
    parser.add_argument("--compile-only", action="store_true", help="只编译不测量")
    args = parser.parse_args()

    specs = discover()
    if args.names:
        wanted = [name.strip() for name in args.names.split(",") if name.strip()]
        by_name = {spec.name: spec for spec in specs}
        missing = [name for name in wanted if name not in by_name]
        if missing:
            raise SystemExit(f"[args] 未知基准: {', '.join(missing)}")
        specs = [by_name[name] for name in wanted]

    if not args.no_compile:
        for spec in specs:
            compile_an(spec, raw=False)
            compile_an(spec, raw=True)
            print(f"[compile] {spec.name} ok", file=sys.stderr)
    if args.compile_only:
        return 0

    fingerprint = machine_fingerprint(args.pin, args.runs)
    rows: list[Row] = []
    for spec in specs:
        row = measure_pair(spec, args.runs, args.pin, args.max_state_sec)
        rows.append(row)
        ratio = row.fat_time.med / row.raw_time.med if row.raw_time.med else float("nan")
        print(
            f"[measure] {spec.name}: fat {row.fat_time.med:.1f}ms "
            f"raw {row.raw_time.med:.1f}ms ratio {ratio:.2f}x ({row.runs} runs)",
            file=sys.stderr,
        )

    # 部分测量不覆盖全量基线 (基线是门禁的对照数据)
    md_path = PARTIAL_MD if args.names else RESULTS_MD
    csv_path = PARTIAL_CSV if args.names else RESULTS_CSV
    md_path.write_text(render_md(rows, fingerprint), encoding="utf-8")
    write_csv(rows, fingerprint, csv_path)
    print(f"[write] {md_path.relative_to(ROOT)}", file=sys.stderr)
    print(f"[write] {csv_path.relative_to(ROOT)}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
