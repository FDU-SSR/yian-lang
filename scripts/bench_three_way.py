#!/usr/bin/env python3
"""bench_three_way.py — C / raw / fat 三态性能实测 (以 C 参考为基线).

协议:
  - 三态同一算法、同一规模 (对齐核对见 bench/README.md「算法与规模对齐」):
    C 态 `clang -O2 -lm bench/c/<name>.c` (argv 见 scripts/bench_common.py);
    raw 态 `yianc -O2 --raw-pointers lib/src bench/shootout/<name>.an`;
    fat 态 `yianc -O2 lib/src bench/shootout/<name>.an`;
    `<name>.raw.an` 存在时 raw 态改用该文件 (如 storage: 两态的语义形式无法共用一份源)。
  - 产物同目录、等长路径 (`build/bench/{cbin,yraw,yfat}/<name>`): 分配密集型基准的
    绝对值对 argv[0] 长度→栈/mmap 布局敏感, 等长路径消除该项偏差。
  - 绑核 (`taskset -c <cpu>`, 可选但正式记录必须绑核)。
  - 每基准三态**逐次轮转**测量: 三态各 1 次 warmup (不计入样本), 再按 C→raw→fat
    轮转测量 N 次 (默认 5, 协议要求 ≥5); 正式指标取**最小值**, 同时记录中位数与 CV。
    指标为端到端墙钟时间 (ms) 与峰值常驻内存 (Maximum resident set size, 取各次最大
    值)。逐次轮转消除三态之间的跨时段系统状态漂移。取最小值而非中位数: 同一二进制
    重复运行会落在相差约 20% 的两个性能状态上 (进程级内存布局/频率假象, 与代码无关),
    最小值估计无干扰性能, 中位数会被落态运气左右。
  - 自适应降次: 三态 warmup 合计超过 --max-state-sec 时测量次数降到 3。
  - 语义护栏: ① C warmup 的 (退出码, stdout) 必须满足 scripts/bench_common.py 记录的
    权威值, 否则 C 基线失效; ② raw 与 fat 的 warmup stdout/退出码必须逐字节一致;
    ③ 两态退出码必须为 0。三者任一不成立, 该基准的比值不可用 (报告列标注)。

用法 (需在已安装 yianc 的环境下运行, 如 yian-env; YIAN 侧编译由 `sys.executable`
驱动, 因此解释器决定 llvmlite/LLVM 版本, 该版本是基线的一部分):

  python3 scripts/bench_three_way.py                      # 全部基准
  python3 scripts/bench_three_way.py --names list,queen    # 指定基准 (逗号分隔)
  python3 scripts/bench_three_way.py --pin 4 --runs 5      # 绑核 + 测量次数
  python3 scripts/bench_three_way.py --no-compile          # 复用已有二进制只测量
  python3 scripts/bench_three_way.py --compile-only        # 只编译不测量
  python3 scripts/bench_three_way.py --max-state-sec 120   # 慢基准降次阈值 (秒)

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
import subprocess
import statistics
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from bench_common import C_SPECS

ROOT = Path(__file__).resolve().parent.parent
LIB = ROOT / "lib" / "src"
BENCH_DIR = ROOT / "bench"
SHOOTOUT_DIR = BENCH_DIR / "shootout"
BENCH_C_DIR = BENCH_DIR / "c"
OUT_DIR = ROOT / "build" / "bench"
RESULTS_MD = BENCH_DIR / "results.md"
RESULTS_CSV = BENCH_DIR / "results.csv"
PARTIAL_MD = BENCH_DIR / "results.partial.md"
PARTIAL_CSV = BENCH_DIR / "results.partial.csv"

OPT_LEVEL = "-O2"
C_COMPILER = "clang"
C_FLAGS = ["-O2", "-lm"]
TIME_BIN = "/usr/bin/time"
DEFAULT_RUNS = 5
DEFAULT_MAX_STATE_SEC = 120.0
RAW_SUFFIX = ".raw.an"
CSV_FORMAT = "three-way-1"

STATES = ("c", "raw", "fat")
STATE_DIR = {"c": "cbin", "raw": "yraw", "fat": "yfat"}

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

    @property
    def c_src(self) -> Path:
        return BENCH_C_DIR / f"{self.name}.c"


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
    runs: int
    yian_stdout_match: bool
    c_check_ok: bool
    exit_codes: dict[str, int]


def spec_bin(spec: BenchSpec, state: str) -> Path:
    return OUT_DIR / STATE_DIR[state] / spec.name


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
    missing_c = sorted(name for name in fat_srcs if not (BENCH_C_DIR / f"{name}.c").exists())
    if missing_c:
        raise SystemExit(f"[discover] 缺少 C 参考: {', '.join(missing_c)}")
    missing_spec = sorted(set(fat_srcs) - set(C_SPECS))
    if missing_spec:
        raise SystemExit(f"[discover] scripts/bench_common.py 缺少规格: {', '.join(missing_spec)}")
    return [
        BenchSpec(name=name, fat_src=fat_srcs[name], raw_src=raw_srcs.get(name, fat_srcs[name]))
        for name in sorted(fat_srcs)
    ]


def compile_c(spec: BenchSpec) -> Path:
    """编译 C 参考 (clang -O2 -lm); 失败即终止 (C 基线失效, 不做部分结果)。"""
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
            f"[compile] {spec.name} (c) 失败 ({C_COMPILER} {' '.join(C_FLAGS)}):\n{res.stdout}\n{res.stderr}"
        )
    return binary


def compile_an(spec: BenchSpec, raw: bool) -> Path:
    """编译单态 YIAN 基准; 失败即终止 (编译错误属于基线失效, 不做部分结果)。"""
    state = "raw" if raw else "fat"
    binary = spec_bin(spec, state)
    src = spec.raw_src if raw else spec.fat_src
    cmd = [sys.executable, "-m", "compiler.main", OPT_LEVEL]
    if raw:
        cmd.append("--raw-pointers")
    cmd += [str(LIB), str(src), "-o", str(binary)]
    binary.parent.mkdir(parents=True, exist_ok=True)
    res = subprocess.run(cmd, capture_output=True, text=True, cwd=ROOT)
    if res.returncode != 0:
        raise SystemExit(
            f"[compile] {spec.name} ({state}) 失败:\n{res.stdout}\n{res.stderr}"
        )
    return binary


def _env_plain() -> dict[str, str]:
    env = os.environ.copy()
    env["LANG"] = "C"
    return env


def _run(binary: Path, argv: list[str], pin: int | None) -> tuple[float, int, str, int]:
    """运行一次: 返回 (墙钟 ms, 峰值 RSS KB, stdout, 退出码)。"""
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


def measure_triple(
    spec: BenchSpec, runs: int, pin: int | None, max_state_sec: float
) -> Row:
    """三态逐次轮转测量: warmup 各 1 次 → 按 C→raw→fat 轮转 used_runs 次。"""
    argv_c = C_SPECS[spec.name].argv
    bins = {state: spec_bin(spec, state) for state in STATES}

    t0 = time.monotonic()
    warm = {
        "c": _run(bins["c"], argv_c, pin),
        "raw": _run(bins["raw"], [], pin),
        "fat": _run(bins["fat"], [], pin),
    }
    warm_sec = time.monotonic() - t0

    c_check_ok = C_SPECS[spec.name].check(warm["c"][3], warm["c"][2])
    if not c_check_ok:
        print(
            f"[{spec.name}] 警告: C 基线未通过权威值校验 "
            f"(exit={warm['c'][3]}, stdout={warm['c'][2].strip()!r}); 该基准的 C 比值不可用",
            file=sys.stderr,
        )
    yian_stdout_match = warm["raw"][2] == warm["fat"][2] and warm["raw"][3] == warm["fat"][3]
    if not yian_stdout_match:
        print(
            f"[{spec.name}] 警告: 两态 warmup 的 stdout/退出码不一致 "
            f"(raw exit={warm['raw'][3]}, fat exit={warm['fat'][3]})",
            file=sys.stderr,
        )
    for state in ("raw", "fat"):
        if warm[state][3] != 0:
            print(
                f"[{spec.name}] 警告: {state} 态 warmup 退出码 {warm[state][3]} (应为 0)",
                file=sys.stderr,
            )

    used_runs = runs
    if warm_sec > max_state_sec and runs > 3:
        used_runs = 3
        print(
            f"[{spec.name}] warmup {warm_sec:.1f}s > {max_state_sec:.0f}s, "
            f"测量次数降到 {used_runs}",
            file=sys.stderr,
        )

    samples: dict[str, list[float]] = {state: [] for state in STATES}
    rss: dict[str, list[int]] = {state: [] for state in STATES}
    for _ in range(used_runs):
        for state in STATES:
            wall, peak, _, _ = _run(bins[state], argv_c if state == "c" else [], pin)
            samples[state].append(wall)
            rss[state].append(peak)

    return Row(
        spec=spec,
        times={state: _stats(samples[state]) for state in STATES},
        rss_mb={state: max(rss[state]) / 1024.0 for state in STATES},
        runs=used_runs,
        yian_stdout_match=yian_stdout_match,
        c_check_ok=c_check_ok,
        exit_codes={state: warm[state][3] for state in STATES},
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
        "clang": _tool_version(C_COMPILER),
        "cflags": " ".join(C_FLAGS),
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


def _ratio(row: Row, num: str, den: str) -> float:
    denom = row.times[den].min
    return row.times[num].min / denom if denom else float("nan")


def _ratio_usable(row: Row) -> bool:
    """C 基线校验与两态语义护栏都通过时, 该基准的 C 比值才可用。"""
    return row.c_check_ok and row.yian_stdout_match


def render_md(rows: list[Row], fingerprint: dict[str, str]) -> str:
    lines: list[str] = []
    lines.append("# C / raw / fat 三态实测结果 (以 C 参考为基线)\n")
    lines.append(
        "由 `scripts/bench_three_way.py` 生成; 三态同一算法与规模, 差异只在实现与指针表示"
        " (C 为 clang `-O2` 参考实现, YIAN 无检查对照态 `nocheck` 已移除)。\n"
    )
    lines.append("## 环境指纹\n")
    lines.append("| 项 | 值 |")
    lines.append("| --- | --- |")
    lines.append(f"| 主机 | {fingerprint['hostname']} |")
    lines.append(f"| 架构 / 逻辑核 | {fingerprint['machine']} / {fingerprint['cpu_count']} |")
    lines.append(f"| C 编译器 | `{fingerprint['clang']}` (`{fingerprint['cflags']}`) |")
    lines.append(f"| Python | {fingerprint['python']} |")
    lines.append(
        f"| llvmlite | `{fingerprint['llvmlite']}` (LLVM {fingerprint['llvm']}) |"
    )
    lines.append(f"| YIAN 优化级 | `{fingerprint['opt']}` (两态相同) |")
    lines.append(f"| 绑核 | `taskset -c {fingerprint['pin']}` |")
    lines.append(f"| 每态测量次数 | {fingerprint['runs']} (另加 1 次 warmup) |")
    lines.append(f"| commit | `{fingerprint['commit']}` |")
    lines.append(f"| 日期 | {fingerprint['date']} |")
    lines.append("")

    lines.append("## 结果\n")
    lines.append(
        "| 基准 | C 最小 (ms) | raw 最小 (ms) | fat 最小 (ms) | raw/C | fat/C | fat/raw "
        "| C 峰值 RSS (MB) | raw 峰值 RSS (MB) | fat 峰值 RSS (MB) | 裸态源 | raw/fat stdout 一致 | C 校验 |"
    )
    lines.append("| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- | --- | --- |")
    for row in rows:
        ratio_rc = _ratio(row, "raw", "c")
        ratio_fc = _ratio(row, "fat", "c")
        ratio_fr = _ratio(row, "fat", "raw")
        raw_src = f"`{row.spec.raw_src.name}`" if row.spec.raw_override else "同名"
        c_flag = "是" if row.c_check_ok else "**否**"
        if not _ratio_usable(row):
            ratio_rc = ratio_fc = float("nan")
        lines.append(
            f"| {row.spec.name} | {_fmt_ms(row.times['c'].min)} | {_fmt_ms(row.times['raw'].min)} "
            f"| {_fmt_ms(row.times['fat'].min)} | {ratio_rc:.2f}× | {ratio_fc:.2f}× | {ratio_fr:.2f}× "
            f"| {_fmt_mb(row.rss_mb['c'])} | {_fmt_mb(row.rss_mb['raw'])} | {_fmt_mb(row.rss_mb['fat'])} "
            f"| {raw_src} | {'是' if row.yian_stdout_match else '**否**'} | {c_flag} |"
        )
    usable = [row for row in rows if _ratio_usable(row)]
    geo_raw = _geomean([_ratio(row, "raw", "c") for row in usable])
    geo_fat = _geomean([_ratio(row, "fat", "c") for row in usable])
    if geo_raw is not None and geo_fat is not None:
        lines.append("")
        lines.append(
            f"几何平均比值 (C 为基线, n={len(usable)}): raw/C **{geo_raw:.2f}×** / "
            f"fat/C **{geo_fat:.2f}×** (比值可用 = C 校验通过且 raw/fat 语义一致)。"
        )
        totals = {state: sum(row.times[state].min for row in rows) / 1000.0 for state in STATES}
        lines.append(
            f"单趟全量耗时 (各基准最小值之和): C **{totals['c']:.1f} s** / "
            f"raw **{totals['raw']:.1f} s** / fat **{totals['fat']:.1f} s**。"
        )
    lines.append("")
    lines.append(
        "比值均为最小值之比; 各基准不可加。`--raw-pointers` 关闭全部指针安全检查与锁槽/帧锁"
        "发射, 因此 raw/C 是裸态实现相对 C 的开销, fat/raw 是完整胖指针相对裸指针的总开销。\n"
    )

    lines.append("## 重复性\n")
    lines.append(
        "| 基准 | C 中位 | C 最小 | C 最大 | C CV% | raw 中位 | raw 最小 | raw 最大 | raw CV% "
        "| fat 中位 | fat 最小 | fat 最大 | fat CV% | 次数 |"
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
        lines.append(f"| {row.spec.name} | {cells} | {row.runs} |")
    lines.append("")
    lines.append(
        "正式指标取**最小值**: 机器处于高压/热态时, 同一二进制重复运行会落在相差约 20% 的"
        "两个性能状态上 (进程级内存布局/频率假象, 与代码无关)。最小值估计无干扰性能, "
        "中位数与 CV 用于判断重复性。\n"
    )

    lines.append("## 协议\n")
    lines.append(
        f"- 编译: C `{C_COMPILER} {' '.join(C_FLAGS)} bench/c/<name>.c`; "
        f"YIAN `yianc {OPT_LEVEL} lib/src <src>`, 裸态追加 `--raw-pointers`。"
    )
    lines.append(
        "- C 侧 argv: 见 `scripts/bench_common.py` 的 `argv` (`cd 100 80`、`richards 2400`, "
        "其余无参), 保证 C 与 `.an` 的迭代次数一致。"
    )
    lines.append(
        "- 测量: 每基准三态各 1 次 warmup (不计入样本) 后按 C→raw→fat **逐次轮转**测量; "
        "正式指标取各态最小值, 并记录中位数/四分位距/CV/峰值 RSS。"
    )
    lines.append(
        f"- 降噪: `taskset -c {fingerprint['pin']}`; 逐次轮转消除跨时段漂移; 三态产物同目录、"
        "等长路径 (argv[0] 长度影响分配密集型基准的进程布局)。"
    )
    lines.append(
        "- 语义护栏: C warmup 必须通过 `scripts/bench_common.py` 的权威值校验; "
        "raw 与 fat 的 stdout/退出码必须逐字节一致。报告中标 `否` 即该基准比值不可用。"
    )
    return "\n".join(lines).rstrip("\n") + "\n"


def write_csv(rows: list[Row], fingerprint: dict[str, str], path: Path) -> None:
    header = (
        "# bench/results.csv — C / raw / fat 三态性能基线 "
        "(scripts/bench_three_way.py 生成)"
    )
    comments = [
        header,
        f"# format: {CSV_FORMAT}",
        "# 列: bench, <state>_min_ms/<state>_med_ms/<state>_cv_pct 各态最小值(正式指标)/中位数/"
        "变异系数, state ∈ {c,raw,fat}; ratio_raw_c = raw_min/c_min, ratio_fat_c = fat_min/c_min, "
        "ratio_fat_raw = fat_min/raw_min; <state>_rss_mb 峰值常驻; runs, stdout_match (raw/fat), "
        "c_check (C 权威值), c_argv, raw_source",
        f"# fingerprint: hostname={fingerprint['hostname']} machine={fingerprint['machine']} "
        f"cpu_count={fingerprint['cpu_count']} clang={fingerprint['clang']} cflags={fingerprint['cflags']}",
        f"# env: python={fingerprint['python']} llvmlite={fingerprint['llvmlite']} "
        f"llvm={fingerprint['llvm']} opt={fingerprint['opt']} pin={fingerprint['pin']} "
        f"runs={fingerprint['runs']}",
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
        argv = " ".join(C_SPECS[row.spec.name].argv)
        fields = [row.spec.name]
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
    parser = argparse.ArgumentParser(description="C / raw / fat 三态性能实测 (以 C 为基线)")
    parser.add_argument("--names", help="只测指定基准 (逗号分隔)")
    parser.add_argument("--runs", type=int, default=DEFAULT_RUNS, help="每态测量次数 (默认 5)")
    parser.add_argument("--pin", type=int, help="taskset 绑定的 CPU 编号")
    parser.add_argument("--max-state-sec", type=float, default=DEFAULT_MAX_STATE_SEC,
                        help="三态 warmup 合计超过该秒数则测量次数降到 3")
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
            compile_c(spec)
            compile_an(spec, raw=False)
            compile_an(spec, raw=True)
            print(f"[compile] {spec.name} ok", file=sys.stderr)
    if args.compile_only:
        return 0

    fingerprint = machine_fingerprint(args.pin, args.runs)
    rows: list[Row] = []
    for spec in specs:
        row = measure_triple(spec, args.runs, args.pin, args.max_state_sec)
        rows.append(row)
        print(
            f"[measure] {spec.name}: C {row.times['c'].min:.1f}ms "
            f"raw {row.times['raw'].min:.1f}ms fat {row.times['fat'].min:.1f}ms "
            f"raw/C {_ratio(row, 'raw', 'c'):.2f}x fat/C {_ratio(row, 'fat', 'c'):.2f}x "
            f"({row.runs} runs)",
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
