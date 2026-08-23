#!/usr/bin/env python3
"""bench_fat.py — 胖指针安全性能实测脚本 (fat-perf-eval / raw-pointers-eval task-3;
bench-rerun 扩展).

按 docs/security-code.md §10.3–§10.5 协议, 在同一机器上实测基准 × 三态:
  ① raw         裸 8B 指针 (--raw-pointers, 无锁槽/帧锁/检查) — 零安全基线
  ② nocheck     胖 40B 表示但检查关 (--no-fat-checks, 保留锁槽/帧锁) — 表示成本对照
  ③ check       完整胖指针 (默认, 40B + 全部安全检查) — 完整安全
每态 ≥5 次取中位数 (报告 IQR/min/max), 指标为端到端墙钟时间 (ms) 与峰值常驻
内存 (Maximum resident set size)。

成本分解 (维度①表 + 计算):
  表示成本 = ②nocheck − ①raw     (胖 40B 表示相对裸 8B 的开销)
  检查成本 = ③check − ②nocheck   (检查发射相对无检查胖的开销)
  总成本   = ③check − ①raw       (完整胖相对裸指针的总开销)
  ①raw 数据跨套件取自 shootout_raw 套件 (同基准, 同 runs/pin 协议)

套件:
  shootout  胖指针专用: 自动发现 bench/shootout/*.an (14 基准), 每基准三态紧邻
            编译/测量 check→nocheck→raw: raw 态从 bench/shootout_raw 同基准以
            --raw-pointers 编译 (胖套件源不编 raw 态: 裸模式下隐式 T*→T[] coerce
            被拒, expr_checker.py L351-355); 三态同一时间窗口内紧邻执行, 消除跨
            会话系统状态漂移; 附 c/cpp/rust 参考基线可选
  raw       裸指针专用: 自动发现 bench/shootout_raw/*.an (14 基准, 维度① raw 语义适配套件:
            T*→T[] 显式用 from_raw_parts, 因裸模式下隐式 coerce 被拒); 以
            --raw-pointers 编译单态测量, 写 shootout-raw-results.md

三态由每基准内三态紧邻组合: ②/③ 来自 shootout 套件 (check/nocheck), ① raw 来自 shootout_raw 套件。

用法:
  python3 scripts/bench_fat.py --suite shootout         # shootout 套件, 写 shootout-results.md
  python3 scripts/bench_fat.py --suite raw              # raw 套件, 写 shootout-raw-results.md
  python3 scripts/bench_fat.py --names binarytree,list  # 只测指定基准 (逗号分隔)
  python3 scripts/bench_fat.py --ref                    # 附加编译运行 c/cpp/rust 参考基线
  python3 scripts/bench_fat.py --runs 7                 # 每态运行次数 (默认 5, 协议要求 ≥5)
  python3 scripts/bench_fat.py --pin 4                  # taskset 绑核降噪
  python3 scripts/bench_fat.py --no-compile             # 不重新编译, 仅测量已存在二进制
  python3 scripts/bench_fat.py --raw-only               # 仅编译+测量 raw 态 (shootout 套件从 bench/shootout_raw 源)
  python3 scripts/bench_fat.py --max-state-sec 120      # 单态 warmup 超限则测量次数降到 3
  python3 scripts/bench_fat.py --compile-only           # 只编译不测量 (编译验证)
  python3 scripts/bench_fat.py --asan --names binarytree --runs 3 --pin 4
                                                        # ASan 交叉对比 (4 腿紧邻), 写 asan-results.md

独立脚本: 不触碰 scripts/run_tests.py / run_fat*.py 等测试 runner; 不修改基准源码。
输出: build/bench/shootout-results.md (shootout); build/bench/asan-results.md (--asan)。
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
LIB = ROOT / "lib"
BENCH_DIR = ROOT / "bench"
OUT_DIR = ROOT / "build" / "bench"
RESULTS_SHOOTOUT = OUT_DIR / "shootout-results.md"
RESULTS_SHOOTOUT_RAW = OUT_DIR / "shootout-raw-results.md"
RESULTS_ASAN = OUT_DIR / "asan-results.md"
SHOOTOUT_DIR = BENCH_DIR / "shootout"
SHOOTOUT_RAW_DIR = BENCH_DIR / "shootout_raw"
REF_ROOT = ROOT / "bak" / "old_exp" / "performance"
REF_OUT_DIR = OUT_DIR / "ref"
BENCH_C_DIR = BENCH_DIR / "c"

# 金标准基线: 性能回归门禁 (scripts/check_perf_regression.py) 的对照数据, 由 --sync-baseline 写入
BASELINE_CSV = ROOT / "docs" / "perf-baseline.csv"
BASELINE_CSV_COMMENTS = (
    "# docs/perf-baseline.csv — 性能回归门禁金标准基线 (由 --sync-baseline 写入)",
    "# 每基准每态 (check/nocheck/raw) 绝对时间中位数 (ms); 判据 = check 态单侧 ±20% (仅拦变慢)",
    "# fingerprint 字段 (hostname/machine/cpu_count/clang) 与 HEAD commit 由 check_perf_regression.py 环境护栏校验",
)

# 跟踪表: shootout 三态成本分解 (§3) 镜像, 每次 shootout 运行后由 _sync_performance_csv 合并同步
PERFORMANCE_CSV = ROOT / "docs" / "performance.csv"
# 列名中文镜像 §3, 倍率列数值 1.40 即 1.40× (无 × 后缀), ΔRSS 保留符号 (相对 ①raw 绝对 MB)
PERFORMANCE_CSV_HEADER = (
    "基准,表示成本倍率(②/①),检查成本倍率(③/②),总成本倍率(③/①),"
    "表示成本ΔRSS(MB),总成本ΔRSS(MB)"
)
PERFORMANCE_CSV_COMMENTS = (
    "# docs/performance.csv — shootout 三态成本分解跟踪表 (镜像 docs/shootout-results.md §3)",
    "# 倍率 = 同基准内相对中位数: 表示 = ②/①, 检查 = ③/②, 总 = ③/①; ΔRSS = 相对 ①raw 绝对 MB",
    "# 每次 `python3 scripts/bench_fat.py --suite shootout` 运行后自动同步 (合并更新, 部分重跑只刷新被测基准)",
    # 种子数据日期为运行时生成 (@SEED_DATE@ 由 _sync_performance_csv 写入时替换为会话日期)
    "# 种子数据: @SEED_DATE@ 全量会话 (docs/shootout-results.md)",
)

TIME_BIN = "/usr/bin/time"
DEFAULT_RUNS = 5
DEFAULT_MAX_STATE_SEC = 120.0

_RSS_RE = re.compile(r"Maximum resident set size \(kbytes\): (\d+)")

# c/cpp/rust 参考基线编译命令: lang → (源码子目录, 扩展名, 编译命令前缀)
# - C/C++ 需要 -lm (nbody/spectralnorm 用 sqrt)
# - Rust 用 rustc -O (opt-level=2, 与 C/C++ 的 -O2 对齐)
# - asan: subdir="" → 平铺源码目录 (bench/c/), clang ASan 插桩; 产物 <name>_asan
REF_LANG_CMD: dict[str, tuple[str, str, list[str]]] = {
    "c": ("c", ".c", ["clang", "-O2", "-lm"]),
    "cpp": ("cpp", ".cpp", ["clang++", "-O2", "-lm"]),
    "rust": ("rust", ".rs", ["rustc", "-O"]),
    "asan": ("", ".c", ["clang", "-O2", "-fsanitize=address", "-lm"]),
}


@dataclass(frozen=True)
class BenchSpec:
    name: str
    subdir: str = "shootout"  # 相对 bench/ 的子目录


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
    state: str  # "check" | "nocheck" | "raw"
    stats: SampleSummary
    used_runs: int = 0


@dataclass
class RefRow:
    bench: str
    lang: str  # "c" | "cpp" | "rust"
    stats: SampleSummary


@dataclass
class AsanRow:
    bench: str
    leg: str  # "c_plain" | "c_asan" | "c_asan_sens" | "an_check"
    stats: SampleSummary
    used_runs: int = 0


LEG_LABEL: dict[str, str] = {
    "c_plain": "C plain",
    "c_asan": "C ASan main",
    "c_asan_sens": "C ASan sens",
    "an_check": ".an check",
}

# ASAN_OPTIONS: 主表关闭 leak 检测 (其余默认, quarantine 256MB 活跃); 敏感性行再加
# quarantine_size_mb=0 (quarantine 失效) — 仅 binarytree 是 quarantine 活跃基准。
ASAN_ENV_MAIN = "detect_leaks=0"
ASAN_ENV_SENS = "detect_leaks=0:quarantine_size_mb=0"


def spec_src(spec: BenchSpec) -> Path:
    return BENCH_DIR / spec.subdir / f"{spec.name}.an"


def spec_bin(spec: BenchSpec, tag: str = "") -> Path:
    d = OUT_DIR / spec.subdir if spec.subdir else OUT_DIR
    return d / f"{spec.name}{tag}"


def discover_shootout() -> list[BenchSpec]:
    files = sorted(SHOOTOUT_DIR.glob("*.an"))
    if not files:
        raise SystemExit(f"[suite] {SHOOTOUT_DIR} 无 .an 基准")
    return [BenchSpec(name=f.stem, subdir="shootout") for f in files]


def discover_shootout_raw() -> list[BenchSpec]:
    files = sorted(SHOOTOUT_RAW_DIR.glob("*.an"))
    if not files:
        raise SystemExit(f"[suite] {SHOOTOUT_RAW_DIR} 无 .an 基准")
    return [BenchSpec(name=f.stem, subdir="shootout_raw") for f in files]


def _env_plain() -> dict[str, str]:
    env = os.environ.copy()
    env["LANG"] = "C"
    return env


def compile_an(spec: BenchSpec, no_checks: bool, raw: bool = False) -> Path:
    """编译 .an 基准为 native exe; no_checks=True → --no-fat-checks 基线;
    raw=True → --raw-pointers 裸 8B 指针 (零检查/锁槽/帧锁, 维度①)。"""
    if raw:
        bin_path = spec_bin(spec, "_raw")
    elif no_checks:
        bin_path = spec_bin(spec, "_nfc")
    else:
        bin_path = spec_bin(spec)
    cmd = [sys.executable, "-m", "compiler.main", "-O3"]
    if raw:
        cmd.append("--raw-pointers")
    elif no_checks:
        cmd.append("--no-fat-checks")
    cmd += [str(LIB), str(spec_src(spec)), "-o", str(bin_path)]
    bin_path.parent.mkdir(parents=True, exist_ok=True)
    res = subprocess.run(cmd, capture_output=True, text=True, cwd=ROOT)
    if res.returncode != 0:
        kind = "raw" if raw else ("no-checks" if no_checks else "checked")
        raise SystemExit(
            f"[compile] {spec.name} ({kind}) failed:"
            f"\n{res.stdout}\n{res.stderr}"
        )
    return bin_path


def compile_ref(name: str, lang: str, source_dir: Path = REF_ROOT) -> Path | None:
    """编译 <source_dir>/<subdir>/<name><ext> 参考基线; 源不存在返回 None。

    source_dir 默认 REF_ROOT (bak/old_exp/performance) 保持 --ref 通道行为不变;
    asan 表项 subdir="" → source_dir/<name>.c 平铺文件 (bench/c/)。
    """
    subdir, ext, base = REF_LANG_CMD[lang]
    src = source_dir / subdir / f"{name}{ext}"
    if not src.exists():
        return None
    out = REF_OUT_DIR / f"{name}_{lang}"
    cmd = list(base) + [str(src), "-o", str(out)]
    out.parent.mkdir(parents=True, exist_ok=True)
    res = subprocess.run(cmd, capture_output=True, text=True, cwd=ROOT)
    if res.returncode != 0:
        raise SystemExit(
            f"[compile] ref {name} ({lang}) failed:\n{res.stdout}\n{res.stderr}"
        )
    return out


def measure(
    binary: Path,
    runs: int,
    pin: int | None,
    allow_nonzero: bool = False,
    max_state_sec: float = DEFAULT_MAX_STATE_SEC,
    env: dict[str, str] | None = None,
) -> tuple[list[tuple[float, int]], int, float]:
    """运行 runs 次, 返回 (样本, 实际次数, warmup 秒数)。

    先 1 次 warmup (计时确认稳定窗口, §10.5 步骤 2; 不计入样本)。若 warmup 超过
    max_state_sec (默认 120s) 且 runs>3, 实际测量次数降到 3 (shootout-perf-eval 策略)。
    allow_nonzero=True 时容忍非零退出码 (参考基线 fann 等以退出码传结果)。
    env 覆盖子进程环境 (默认 os.environ + LANG=C); ASan 会话注入 ASAN_OPTIONS。
    """
    env = env if env is not None else _env_plain()
    pre: list[str] = []
    if pin is not None:
        pre = ["taskset", "-c", str(pin)]

    t0 = time.monotonic()
    subprocess.run(pre + [TIME_BIN, "-v", str(binary)], capture_output=True, text=True, env=env)
    warmup_sec = time.monotonic() - t0

    used_runs = runs
    if warmup_sec > max_state_sec and runs > 3:
        used_runs = 3

    samples: list[tuple[float, int]] = []
    for _ in range(used_runs):
        cmd = pre + [TIME_BIN, "-v", str(binary)]
        t0 = time.monotonic()
        res = subprocess.run(cmd, capture_output=True, text=True, env=env)
        wall_ms = (time.monotonic() - t0) * 1000.0
        if res.returncode != 0 and not allow_nonzero:
            raise SystemExit(f"[run] {binary} exited {res.returncode}:\n{res.stdout}\n{res.stderr}")
        m = _RSS_RE.search(res.stderr)
        if m is None:
            raise SystemExit(
                f"[run] {binary}: 无法从 /usr/bin/time -v 输出解析 Maximum resident set size:\n"
                f"{res.stderr}"
            )
        samples.append((wall_ms, int(m.group(1))))
    return samples, used_runs, warmup_sec


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
    for tool in ("clang", "rustc"):
        try:
            res = subprocess.run([tool, "--version"], capture_output=True, text=True, timeout=10)
            if res.returncode == 0:
                first = res.stdout.splitlines()[0] if res.stdout else "n/a"
                lines.append(f"  `{first}`")
        except (OSError, subprocess.SubprocessError):
            pass
    return lines


def machine_fingerprint() -> dict[str, str]:
    """稳定机器指纹字段 (性能回归门禁环境比对用): hostname/机器架构/逻辑核数/clang 版本首行。"""
    fp: dict[str, str] = {}
    fp["hostname"] = platform.node() or "unknown"
    fp["machine"] = platform.machine()
    fp["cpu_count"] = str(os.cpu_count() or 0)
    fp["clang"] = "n/a"
    try:
        res = subprocess.run(["clang", "--version"], capture_output=True, text=True, timeout=10)
        if res.returncode == 0 and res.stdout:
            fp["clang"] = res.stdout.splitlines()[0].strip()
    except (OSError, subprocess.SubprocessError):
        pass
    return fp


def fmt_ms(v: float) -> str:
    return f"{v:.1f}"


def fmt_rss_mb(kb: float) -> str:
    return f"{kb / 1024.0:.1f}"


def scale_note(spec: BenchSpec) -> str:
    """从基准头部注释提取规模调整说明 (供报告测量说明)。"""
    try:
        lines = spec_src(spec).read_text(encoding="utf-8").splitlines()[:14]
    except OSError:
        return ""
    return " | ".join(
        ln.strip().lstrip("//").strip()
        for ln in lines
        if "规模" in ln or "原规模" in ln or "采用" in ln
    )


def run_suite(
    specs: list[BenchSpec],
    args: argparse.Namespace,
    do_ref: bool,
) -> tuple[list[MeasRow], list[RefRow], list[str]]:
    """编译+测量套件基准, 每基准三态紧邻 (check→nocheck→raw): 同一时间窗口内连续
    编译+测量三态, 消除跨会话系统状态漂移对维度①比较的假象。raw 态从
    bench/shootout_raw/<name>.an 以 --raw-pointers 编译 (胖套件源不编 raw 态:
    裸模式下隐式 T*→T[] coerce 被拒); 缺同名基准 → 警告并跳过 (报告标注 维度①未测)。"""
    rows: list[MeasRow] = []
    notes: list[str] = []
    raw_map = _raw_suite_map()

    for spec in specs:
        raw_spec = raw_map.get(spec.name)
        if not args.no_compile:
            compile_an(spec, no_checks=False)
            compile_an(spec, no_checks=True)
            if raw_spec is not None:
                compile_an(raw_spec, no_checks=False, raw=True)
        states: list[tuple[str, Path]] = [
            ("check", spec_bin(spec)),
            ("nocheck", spec_bin(spec, "_nfc")),
        ]
        if raw_spec is not None:
            states.append(("raw", spec_bin(raw_spec, "_raw")))
        else:
            msg = f"{spec.name}: 维度①未测 (bench/shootout_raw 缺同名基准)"
            print(f"[warn] {msg}", file=sys.stderr)
            notes.append(msg)
        for label, binp in states:
            samples, used, warm = measure(binp, args.runs, args.pin)
            if used < args.runs:
                notes.append(
                    f"{spec.name}/{label}: warmup {warm:.1f}s > {args.max_state_sec:.0f}s, "
                    f"测量次数降为 {used}"
                )
            rows.append(MeasRow(bench=spec.name, state=label, stats=summarize(samples), used_runs=used))
        print(f"[done] {spec.name}: 三态紧邻 (check/nocheck/raw) 各 {args.runs} 次", file=sys.stderr)

    ref_rows: list[RefRow] = []
    if do_ref:
        for lang in ("c", "cpp", "rust"):
            for spec in specs:
                binp = compile_ref(spec.name, lang)
                if binp is None:
                    continue
                # 参考基线允许非零退出码 (fann 等以退出码返回结果), warmup 同样适用
                samples, _, _ = measure(binp, args.runs, args.pin, allow_nonzero=True)
                ref_rows.append(RefRow(bench=spec.name, lang=lang, stats=summarize(samples)))
                print(f"[done] ref {spec.name} ({lang}): {args.runs} 次", file=sys.stderr)

    return rows, ref_rows, notes


def _raw_suite_map() -> dict[str, BenchSpec]:
    return {s.name: s for s in discover_shootout_raw()}


def _raw_specs_for(specs: list[BenchSpec]) -> list[BenchSpec]:
    """把胖套件基准映射到 raw 套件同名基准 (缺失者忽略)。"""
    raw_map = _raw_suite_map()
    return [raw_map[s.name] for s in specs if s.name in raw_map]


def complement_raw(
    specs: list[BenchSpec],
    args: argparse.Namespace,
) -> tuple[list[MeasRow], list[str]]:
    """跨套件补齐维度①: 对每个胖套件基准, 从 bench/shootout_raw/<name>.an 以
    --raw-pointers 编译并测量 raw 态 (同 runs/pin 协议)。raw 套件缺同名基准 →
    警告并跳过 (报告标注 维度①未测)。"""
    rows: list[MeasRow] = []
    notes: list[str] = []
    raw_map = _raw_suite_map()
    for spec in specs:
        raw_spec = raw_map.get(spec.name)
        if raw_spec is None:
            msg = f"{spec.name}: 维度①未测 (bench/shootout_raw 缺同名基准)"
            print(f"[warn] {msg}", file=sys.stderr)
            notes.append(msg)
            continue
        if not args.no_compile:
            compile_an(raw_spec, no_checks=False, raw=True)
        samples, used, warm = measure(spec_bin(raw_spec, "_raw"), args.runs, args.pin)
        if used < args.runs:
            notes.append(
                f"{spec.name}/raw: warmup {warm:.1f}s > {args.max_state_sec:.0f}s, "
                f"测量次数降为 {used}"
            )
        rows.append(MeasRow(bench=spec.name, state="raw", stats=summarize(samples), used_runs=used))
        print(f"[done] {spec.name}: raw 态 (跨套件 bench/shootout_raw) {args.runs} 次", file=sys.stderr)
    return rows, notes


def _matrix_table(
    rows: list[MeasRow],
    state_label: dict[str, str],
) -> list[str]:
    # 倍率基准 = 各基准自身 raw 态时间中位数 (同基准内相对, 不受机器负载影响)。
    raw_med: dict[str, float] = {r.bench: r.stats.time.med for r in rows if r.state == "raw"}
    md: list[str] = []
    md.append(
        "| 基准 | 态 | 时间中位数 (ms) | 倍率 (vs ①raw) | IQR (ms) | min–max (ms) "
        "| 峰值 RSS 中位数 (MB) | RSS IQR (MB) |\n"
    )
    md.append("|---|---|---|---|---|---|---|---|\n")
    for r in rows:
        base = raw_med.get(r.bench)
        if base is None or base == 0.0:
            ratio = "—"
        else:
            ratio = f"{r.stats.time.med / base:.2f}×"
        md.append(
            f"| {r.bench} | {state_label[r.state]} "
            f"| {fmt_ms(r.stats.time.med)} | {ratio} "
            f"| {fmt_ms(r.stats.time.iqr)} "
            f"| {fmt_ms(r.stats.time.min)}–{fmt_ms(r.stats.time.max)} "
            f"| {fmt_rss_mb(r.stats.rss.med)} | {fmt_rss_mb(r.stats.rss.iqr)} |\n"
        )
    return md


def _attribution_table(
    specs: list[BenchSpec],
    rows: list[MeasRow],
) -> list[str]:
    md: list[str] = []
    md.append(
        "以维度①裸指针为基准: 表示成本倍率 = ②nocheck 中位 / ①raw 中位 "
        "(胖 40B 表示相对裸 8B); 检查成本倍率 = ③check 中位 / ②nocheck 中位 (检查发射); "
        "总成本倍率 = ③check 中位 / ①raw 中位。\n"
    )
    md.append(
        "维度① (raw) 数据来自 bench/shootout_raw 套件 (跨套件补齐, --raw-pointers 编译, "
        "同 runs/pin 协议)。\n"
    )
    md.append(
        "| 基准 | 表示成本倍率 (②/①) | 检查成本倍率 (③/②) | 总成本倍率 (③/①) "
        "| 表示成本 ΔRSS (MB) | 总成本 ΔRSS (MB) |\n"
    )
    md.append("|---|---|---|---|---|---|\n")
    for spec in specs:
        on = next(r for r in rows if r.bench == spec.name and r.state == "check")
        off = next(r for r in rows if r.bench == spec.name and r.state == "nocheck")
        raw = next((r for r in rows if r.bench == spec.name and r.state == "raw"), None)
        if raw is None:
            md.append(f"| {spec.name} (维度①未测) | — | — | — | — | — |\n")
            continue
        raw_med = raw.stats.time.med
        off_med = off.stats.time.med
        on_med = on.stats.time.med
        rep_x = f"{off_med / raw_med:.2f}×" if raw_med else "—"
        chk_x = f"{on_med / off_med:.2f}×" if off_med else "—"
        tot_x = f"{on_med / raw_med:.2f}×" if raw_med else "—"
        rep_rss = (off.stats.rss.med - raw.stats.rss.med) / 1024.0
        tot_rss = (on.stats.rss.med - raw.stats.rss.med) / 1024.0
        md.append(
            f"| {spec.name} | {rep_x} | {chk_x} | {tot_x} "
            f"| {rep_rss:+.2f} | {tot_rss:+.2f} |\n"
        )
    return md


def _sync_performance_csv(
    specs: list[BenchSpec],
    rows: list[MeasRow],
) -> None:
    """同步 docs/performance.csv (shootout 三态成本分解跟踪表)。

    与 _attribution_table 同款计算: 对每个 spec 取 check/nocheck/raw 三态中位数,
    算 表示 = ②/①、检查 = ③/②、总 = ③/① (2 位小数无 ×), ΔRSS = (该态 rss 中位 - raw
    rss 中位) / 1024 (2 位小数保留符号)。raw 缺失 (维度①未测) 的基准跳过。
    合并语义: 文件已存在则读现有行, 本次测量到的基准替换, 未测量的原样保留;
    注释头与表头保持固定 (以本脚本常量为准), 文件不存在时写注释头 + 表头 + 本次全部行。
    """
    existing: dict[str, str] = {}
    if PERFORMANCE_CSV.exists():
        for line in PERFORMANCE_CSV.read_text(encoding="utf-8").splitlines():
            if not line or line.startswith("#") or line.startswith("基准,"):
                continue
            existing[line.split(",", 1)[0]] = line + "\n"
    for spec in specs:
        on = next((r for r in rows if r.bench == spec.name and r.state == "check"), None)
        off = next((r for r in rows if r.bench == spec.name and r.state == "nocheck"), None)
        raw = next((r for r in rows if r.bench == spec.name and r.state == "raw"), None)
        if on is None or off is None or raw is None:
            continue
        raw_med = raw.stats.time.med
        off_med = off.stats.time.med
        on_med = on.stats.time.med
        if not raw_med or not off_med:
            continue
        rep_rss = (off.stats.rss.med - raw.stats.rss.med) / 1024.0
        tot_rss = (on.stats.rss.med - raw.stats.rss.med) / 1024.0
        existing[spec.name] = (
            f"{spec.name},{off_med / raw_med:.2f},{on_med / off_med:.2f},{on_med / raw_med:.2f},"
            f"{rep_rss:.2f},{tot_rss:.2f}\n"
        )
    out = [
        c.replace("@SEED_DATE@", datetime.date.today().isoformat()) + "\n"
        for c in PERFORMANCE_CSV_COMMENTS
    ]
    out.append(PERFORMANCE_CSV_HEADER + "\n")
    for name in sorted(existing):
        out.append(existing[name])
    PERFORMANCE_CSV.write_text("".join(out), encoding="utf-8")
    print(f"成本分解跟踪表同步 {PERFORMANCE_CSV} ({len(existing)} 基准)")


def _sync_baseline_csv(specs: list[BenchSpec], rows: list[MeasRow]) -> None:
    """写 docs/perf-baseline.csv (金标准基线, 由 --sync-baseline 写入)。

    每基准每态 (check/nocheck/raw) 绝对时间中位数 + 机器指纹字段 + HEAD commit;
    整文件覆盖 (重跑金标准即重建)。供 scripts/check_perf_regression.py 作 check 态
    单侧 ±20% 判据与环境护栏比对。
    """
    try:
        res = subprocess.run(
            ["git", "rev-parse", "HEAD"], capture_output=True, text=True, cwd=ROOT, timeout=10
        )
        commit = res.stdout.strip() if res.returncode == 0 else "unknown"
    except (OSError, subprocess.SubprocessError):
        commit = "unknown"
    fp = machine_fingerprint()
    out = [c + "\n" for c in BASELINE_CSV_COMMENTS]
    out.append("# fingerprint: " + ",".join(f"{k}={v}" for k, v in fp.items()) + "\n")
    out.append(f"# commit: {commit}\n")
    out.append("基准,态,时间中位数(ms)\n")
    n = 0
    for spec in specs:
        for state in ("check", "nocheck", "raw"):
            r = next((x for x in rows if x.bench == spec.name and x.state == state), None)
            if r is None:
                continue
            out.append(f"{spec.name},{state},{r.stats.time.med:.3f}\n")
            n += 1
    BASELINE_CSV.write_text("".join(out), encoding="utf-8")
    print(f"金标准基线写入 {BASELINE_CSV} ({n} 行, commit {commit[:12]})")


def render_shootout(
    specs: list[BenchSpec],
    rows: list[MeasRow],
    ref_rows: list[RefRow],
    notes: list[str],
    args: argparse.Namespace,
) -> None:
    state_label = {"check": "③完整胖", "nocheck": "②胖无检查", "raw": "①裸指针"}
    md: list[str] = []
    md.append(
        "# build/bench/shootout-results.md — shootout 基准三态性能实测 (bench-rerun)\n"
    )
    md.append("## 0) 环境与协议\n")
    md.append("".join(f"{l}\n" for l in machine_header()))
    md.append(
        "- 编译: ②/③ 态 `python3 -m compiler.main -O3 lib bench/shootout/<name>.an`"
        " (nocheck 加 `--no-fat-checks`); ①raw 态跨套件编译 `bench/shootout_raw/<name>.an`"
        " 加 `--raw-pointers`, 每态运行 "
        f"{args.runs} 次取中位数, 报告 IQR/min/max (docs/security-code.md §10.5, 同一机器同一负载)。\n"
    )
    md.append(
        "- 三态定义: ①raw = 裸 8B 指针 (--raw-pointers, 无锁槽/帧锁/检查, 零安全基线); "
        "②nocheck = 胖 40B 表示但检查关 (--no-fat-checks, 保留锁槽/帧锁); "
        "③check = 完整胖指针 (40B + 全部安全检查)。\n"
    )
    md.append(
        "- 维度① (raw) 来源: `bench/shootout_raw/` 套件 (T*→T[] 显式 `from_raw_parts`, "
        "因裸模式下隐式 coerce 被拒); 与 ②/③ 同基准、同 runs/pin 协议, 跨套件组合成三态。\n"
    )
    md.append(
        "- 源码分叉声明 (c1/c3, bench-ptr-to-view): raw 态源码 (shootout_raw/) 数组局部"
        "保持 T* 并显式 `from_raw_parts` 视图, fat 态源码 (shootout/) 直绑 `T[] = dyn[n] T`"
        "整块视图; 二者逻辑等价 (同规模同断言), 差异来源已知并接受"
        "(raw 下隐式 T*→T[] coerce 被拒, expr_checker L349-353), 不构成安全/语义差异。\n"
    )
    md.append(
        "- 指标: 端到端墙钟时间 (ms, `time.monotonic()` 包住 `/usr/bin/time -v` 执行) + "
        "峰值常驻内存 (Maximum resident set size, KB)。\n"
    )
    md.append(
        "- 倍率基准: 各态倍率 = 该态时间中位数 / 该基准 ①raw 态时间中位数 "
        "(同基准内相对, 不受机器负载影响); §2 矩阵与 §3 成本分解均以倍率呈现, "
        "ΔRSS 保持绝对 MB (内存非相对时间范畴)。\n"
    )
    md.append(
        "- 基准源: `bench/shootout/*.an` (14 基准, 当前胖指针语法, 迁移自 "
        "`bak/old_exp/performance`; 规模调整记录见各基准头部注释与 t1/t2 证据)。\n"
    )
    if args.pin is not None:
        md.append(f"- 降噪: `taskset -c {args.pin}` 绑定单核。\n")

    md.append("\n## 1) 基准清单与规模 (14)\n")
    md.append("| 基准 | 规模 / 调整说明 |\n|---|---|\n")
    for spec in specs:
        note = scale_note(spec) or "—"
        md.append(f"| {spec.name} | {note} |\n")

    md.append("\n## 2) 14×3 实测时间矩阵 (倍率 vs ①raw)\n")
    md += _matrix_table(rows, state_label)

    md.append("\n## 3) 成本分解 (倍率 vs ①raw): 表示成本 / 检查成本 / 总成本\n")
    md += _attribution_table(specs, rows)
    md.append(
        "\n注: 倍率 = 各态时间中位数相对 ①raw (或前态) 的比值; 表示成本倍率含胖指针 5 字段读写 "
        "/ 分配块锁槽头 / 帧锁保留带来的访存与占用; "
        "检查成本倍率含 CheckSafeAccess/CheckInBounds/CheckElementArith/CheckPtrCmp/GenKey/锁槽写等。\n"
    )

    if ref_rows:
        md.append("\n## 4) c/cpp/rust 参考基线 (仅记录参考数字, 不写对照结论)\n")
        md.append(
            "- 参考源: `bak/old_exp/performance/{c,cpp,rust}/` 对应基准。\n"
            "- 编译: C/C++ `clang/clang++ -O2 -lm`; Rust `rustc -O`。\n"
            "- 注意: 参考源采用**原规模参数** (未经 t1/t2 的规模缩减, 如 fann n=12、"
            "sieve 5000 趟、queen 1000 次等), 与 .an 迁移版的规模不同, 故数字仅作跨语言参考, "
            "不作三态/跨语言对照结论。\n"
        )
        md.append("| 基准 | C 中位 (ms) | C++ 中位 (ms) | Rust 中位 (ms) | C RSS (MB) | C++ RSS (MB) | Rust RSS (MB) |\n")
        md.append("|---|---|---|---|---|---|---|\n")
        for spec in specs:
            cells: list[str] = []
            rss_cells: list[str] = []
            for lang in ("c", "cpp", "rust"):
                rr = next((x for x in ref_rows if x.bench == spec.name and x.lang == lang), None)
                if rr is None:
                    cells.append("—")
                    rss_cells.append("—")
                else:
                    cells.append(f"{fmt_ms(rr.stats.time.med)}")
                    rss_cells.append(fmt_rss_mb(rr.stats.rss.med))
            md.append(
                f"| {spec.name} | {cells[0]} | {cells[1]} | {cells[2]} "
                f"| {rss_cells[0]} | {rss_cells[1]} | {rss_cells[2]} |\n"
            )

    md.append("\n## 5) 测量说明\n")
    md.append(
        f"- 每态先 1 次 warmup (确认稳定窗口, 不计入样本), 再测 {args.runs} 次取中位数。\n"
        f"- 自适应降次: 若某态 warmup 超过 {args.max_state_sec:.0f}s, 该态测量次数降到 3。\n"
    )
    md.append(
        "- 维度① raw 与 `--suite raw` 套件共用同一批二进制 "
        "`build/bench/shootout_raw/<name>_raw`, 数据一致。\n"
    )
    md.append("- 分轮执行: 全量按批次后台运行, 每轮独立落盘, 最终单次全量会话重新生成本文件 (数据一致)。\n")
    if notes:
        md.append("- 触发记录:\n")
        for n in notes:
            md.append(f"  - {n}\n")

    md.append("\n## 6) 原始样本 (附录)\n")
    md.append("格式: 每样本 `wall_ms` (rss_kb)。\n")
    for r in rows:
        md.append(
            f"- {r.bench} / {state_label[r.state]}: "
            f"{', '.join(f'{t:.1f} ({rss})' for t, rss in zip(r.stats.time.raw, r.stats.rss.raw))}\n"
        )

    RESULTS_SHOOTOUT.write_text("".join(md), encoding="utf-8")
    print(f"\n结果写入 {RESULTS_SHOOTOUT}\n")

    _sync_performance_csv(specs, rows)

    if args.sync_baseline:
        _sync_baseline_csv(specs, rows)


def render_raw(
    specs: list[BenchSpec],
    rows: list[MeasRow],
    ref_rows: list[RefRow],
    notes: list[str],
    args: argparse.Namespace,
) -> None:
    """渲染 raw 适配套件 (shootout_raw) 报告: 维度① raw 语义单态测量, §0-§6 结构。"""
    state_label = {"raw": "①裸指针"}
    md: list[str] = []
    md.append(
        "# build/bench/shootout-raw-results.md — shootout_raw 基准 raw 态性能实测 (bench-two-suite task-1)\n"
    )
    md.append("## 0) 环境与协议\n")
    md.append("".join(f"{l}\n" for l in machine_header()))
    md.append(
        "- 编译: `python3 -m compiler.main -O3 --raw-pointers lib bench/shootout_raw/<name>.an`, "
        f"每基准运行 {args.runs} 次取中位数, 报告 IQR/min/max (docs/security-code.md §10.5, "
        "同一机器同一负载)。\n"
    )
    md.append(
        "- 本套件 = 三态评测的维度① (raw) 语义: T*→T[] 显式用 `from_raw_parts` "
        "(裸模式下隐式 coerce 被拒, expr_checker.py L351-355); 全裸 8B 指针 "
        "(--raw-pointers, 无锁槽/帧锁/检查), 零安全基线。\n"
    )
    md.append(
        "- 基准源: `bench/shootout_raw/*.an` (14 基准, 与 `bench/shootout/` 语义一致, "
        "仅 T*→T[] 构造方式不同; 规模调整记录见各基准头部注释)。\n"
    )
    if args.pin is not None:
        md.append(f"- 降噪: `taskset -c {args.pin}` 绑定单核。\n")

    md.append("\n## 1) 基准清单与规模 (14)\n")
    md.append("| 基准 | 规模 / 调整说明 |\n|---|---|\n")
    for spec in specs:
        note = scale_note(spec) or "—"
        md.append(f"| {spec.name} | {note} |\n")

    md.append("\n## 2) 14 基准 raw 态实测时间矩阵 (维度①: 裸指针)\n")
    md += _matrix_table(rows, state_label)

    md.append("\n## 3) 成本分解\n")
    md.append(
        "- 本套件为单态 (维度① raw) 测量, 不做三态成本分解; 表示/检查/总成本归因见 "
        "`shootout-results.md` (胖套件三态报告)。\n"
    )

    if ref_rows:
        md.append("\n## 4) c/cpp/rust 参考基线 (仅记录参考数字, 不写对照结论)\n")
        md.append(
            "- 参考源: `bak/old_exp/performance/{c,cpp,rust}/` 对应基准。\n"
            "- 编译: C/C++ `clang/clang++ -O2 -lm`; Rust `rustc -O`。\n"
            "- 注意: 参考源采用**原规模参数** (未经 t1/t2 的规模缩减, 如 fann n=12、"
            "sieve 5000 趟、queen 1000 次等), 与 .an 迁移版的规模不同, 故数字仅作跨语言参考, "
            "不作三态/跨语言对照结论。\n"
        )
        md.append("| 基准 | C 中位 (ms) | C++ 中位 (ms) | Rust 中位 (ms) | C RSS (MB) | C++ RSS (MB) | Rust RSS (MB) |\n")
        md.append("|---|---|---|---|---|---|---|\n")
        for spec in specs:
            cells: list[str] = []
            rss_cells: list[str] = []
            for lang in ("c", "cpp", "rust"):
                rr = next((x for x in ref_rows if x.bench == spec.name and x.lang == lang), None)
                if rr is None:
                    cells.append("—")
                    rss_cells.append("—")
                else:
                    cells.append(f"{fmt_ms(rr.stats.time.med)}")
                    rss_cells.append(fmt_rss_mb(rr.stats.rss.med))
            md.append(
                f"| {spec.name} | {cells[0]} | {cells[1]} | {cells[2]} "
                f"| {rss_cells[0]} | {rss_cells[1]} | {rss_cells[2]} |\n"
            )

    md.append("\n## 5) 测量说明\n")
    md.append(
        f"- 每基准先 1 次 warmup (确认稳定窗口, 不计入样本), 再测 {args.runs} 次取中位数。\n"
        f"- 自适应降次: 若 warmup 超过 {args.max_state_sec:.0f}s, 测量次数降到 3。\n"
    )
    if notes:
        md.append("- 触发记录:\n")
        for n in notes:
            md.append(f"  - {n}\n")

    md.append("\n## 6) 原始样本 (附录)\n")
    md.append("格式: 每样本 `wall_ms` (rss_kb)。\n")
    for r in rows:
        md.append(
            f"- {r.bench} / {state_label[r.state]}: "
            f"{', '.join(f'{t:.1f} ({rss})' for t, rss in zip(r.stats.time.raw, r.stats.rss.raw))}\n"
        )

    RESULTS_SHOOTOUT_RAW.write_text("".join(md), encoding="utf-8")
    print(f"\n结果写入 {RESULTS_SHOOTOUT_RAW}\n")


def _env_asan(options: str) -> dict[str, str]:
    env = _env_plain()
    env["ASAN_OPTIONS"] = options
    return env


def _ref_bin_path(name: str, lang: str) -> Path:
    return REF_OUT_DIR / f"{name}_{lang}"


def _compile_asan_ref(
    name: str,
    lang: str,
    source_dir: Path,
    args: argparse.Namespace,
    notes: list[str],
) -> Path | None:
    """ASan 会话 C 腿编译 (source_dir 组合 REF_LANG_CMD subdir 得平铺源:
    c 用 BENCH_DIR → bench/c/<name>.c; asan subdir="" 用 BENCH_C_DIR → 同文件);
    --no-compile 复用已存在二进制。编译失败 → 标注并返回 None, 不中断会话。"""
    if args.no_compile:
        p = _ref_bin_path(name, lang)
        if not p.exists():
            notes.append(f"{name}/{lang}: --no-compile 但二进制缺失, 跳过")
            return None
        return p
    try:
        return compile_ref(name, lang, source_dir=source_dir)
    except (OSError, SystemExit) as e:
        notes.append(f"{name}/{lang}: 编译失败跳过 ({e})")
        print(f"[warn] {name}/{lang}: 编译失败: {e}", file=sys.stderr)
        return None


def _compile_an_leg(
    spec: BenchSpec,
    args: argparse.Namespace,
    notes: list[str],
) -> Path | None:
    """ASan 会话 .an check 腿编译 (现编, -O3); --no-compile 复用已存在二进制。"""
    if args.no_compile:
        p = spec_bin(spec)
        if not p.exists():
            notes.append(f"{spec.name}/an_check: --no-compile 但二进制缺失, 跳过")
            return None
        return p
    try:
        return compile_an(spec, no_checks=False)
    except (OSError, SystemExit) as e:
        notes.append(f"{spec.name}/an_check: 编译失败跳过 ({e})")
        print(f"[warn] {spec.name}/an_check: 编译失败: {e}", file=sys.stderr)
        return None


def _measure_asan_leg(
    bench: str,
    leg: str,
    binary: Path | None,
    env: dict[str, str] | None,
    args: argparse.Namespace,
    allow_nonzero: bool,
    notes: list[str],
) -> AsanRow | None:
    """单腿测量 (measure 现协议); 失败/崩溃 → 标注并返回 None, 不中断会话。"""
    if binary is None:
        return None
    try:
        samples, used, warm = measure(
            binary,
            args.runs,
            args.pin,
            allow_nonzero=allow_nonzero,
            max_state_sec=args.max_state_sec,
            env=env,
        )
    except (OSError, SystemExit, subprocess.TimeoutExpired) as e:
        notes.append(f"{bench}/{leg}: 测量失败跳过 ({e})")
        print(f"[warn] {bench}/{leg}: {e}", file=sys.stderr)
        return None
    if used < args.runs:
        notes.append(
            f"{bench}/{leg}: warmup {warm:.1f}s > {args.max_state_sec:.0f}s, "
            f"测量次数降为 {used}"
        )
    return AsanRow(bench=bench, leg=leg, stats=summarize(samples), used_runs=used)


def run_asan_suite(
    specs: list[BenchSpec],
    args: argparse.Namespace,
) -> tuple[list[AsanRow], list[str]]:
    """ASan 交叉对比测量: 每基准 4 腿紧邻 [C plain, C ASan main,
    C ASan sensitivity(binarytree only), .an check]。

    - C 腿源 bench/c/ (平铺, 规模与 bench/shootout/ 对齐): C plain `clang -O2 -lm`,
      C ASan `clang -O2 -fsanitize=address -lm` (同一 ASan 二进制复用给敏感性腿);
    - .an check 腿 compile_an 现编 (-O3, 完整胖指针检查), 无 ASAN_OPTIONS;
    - allow_nonzero 仅 fann (C 版以 rc=51 传结果), 其余 False 防崩溃被静默容忍;
    - per-leg 兜底: 失败跳过 + 标注, 绝不中断整个会话。
    """
    rows: list[AsanRow] = []
    notes: list[str] = []
    for spec in specs:
        allow = spec.name == "fann"
        an_spec = BenchSpec(name=spec.name, subdir="shootout")
        print(f"[asan] {spec.name}: 4 腿紧邻开始 (allow_nonzero={allow})", file=sys.stderr)

        bin_plain = _compile_asan_ref(spec.name, "c", BENCH_DIR, args, notes)
        if bin_plain is None:
            notes.append(f"{spec.name}/c_plain: 跳过 (C 源缺失或编译失败)")
        row = _measure_asan_leg(spec.name, "c_plain", bin_plain, None, args, allow, notes)
        if row is not None:
            rows.append(row)

        bin_asan = _compile_asan_ref(spec.name, "asan", BENCH_C_DIR, args, notes)
        if bin_asan is None:
            notes.append(f"{spec.name}/c_asan: 跳过 (C 源缺失或编译失败)")
        row = _measure_asan_leg(
            spec.name, "c_asan", bin_asan, _env_asan(ASAN_ENV_MAIN), args, allow, notes
        )
        if row is not None:
            rows.append(row)

        if spec.name == "binarytree":
            row = _measure_asan_leg(
                spec.name,
                "c_asan_sens",
                bin_asan,
                _env_asan(ASAN_ENV_SENS),
                args,
                allow,
                notes,
            )
            if row is not None:
                rows.append(row)

        bin_an = _compile_an_leg(an_spec, args, notes)
        if bin_an is None:
            notes.append(f"{spec.name}/an_check: 跳过 (编译失败或 --no-compile 无二进制)")
        row = _measure_asan_leg(spec.name, "an_check", bin_an, None, args, allow, notes)
        if row is not None:
            rows.append(row)

        print(f"[done] {spec.name}: 4 腿紧邻完成", file=sys.stderr)
    return rows, notes


def _geo_mean(values: list[float]) -> float | None:
    """独立几何平均 (exp(mean(ln))); 空或含非正 → None。"""
    if not values or any(v <= 0.0 for v in values):
        return None
    return math.exp(sum(math.log(v) for v in values) / len(values))


def _ratio_str(num: float | None, den: float | None) -> str:
    if num is None or den is None or den == 0.0:
        return "—"
    return f"{num / den:.2f}×"


def _cv_str(vals: list[float]) -> str:
    if len(vals) < 2:
        return "—"
    try:
        mean = sum(vals) / len(vals)
        return f"{statistics.stdev(vals) / mean:.3f}"
    except statistics.StatisticsError:
        return "—"


_ASAN_SAMPLE_RE = re.compile(r"^- (\S+) / (\w+) \((\d+) runs\): (.+)$")
_ASAN_SAMPLE_PART_RE = re.compile(r"^(-?\d+(?:\.\d+)?) \((-?\d+)\)$")


def _parse_asan_legs(
    path: Path,
) -> dict[tuple[str, str], tuple[list[tuple[float, int]], int]]:
    """解析已有 asan-results.md §5 原始样本 → {(bench, leg): (样本, used_runs)}。
    供 --names 分批合并: 未测基准的腿原样保留。"""
    out: dict[tuple[str, str], tuple[list[tuple[float, int]], int]] = {}
    if not path.exists():
        return out
    for line in path.read_text(encoding="utf-8").splitlines():
        m = _ASAN_SAMPLE_RE.match(line.strip())
        if m is None:
            continue
        bench = m.group(1)
        leg = m.group(2)
        runs_s = m.group(3)
        rest = m.group(4)
        if bench is None or leg is None or runs_s is None or rest is None:
            continue
        samples: list[tuple[float, int]] = []
        for part in rest.split(","):
            pm = _ASAN_SAMPLE_PART_RE.match(part.strip())
            if pm is None:
                continue
            t_s = pm.group(1)
            r_s = pm.group(2)
            if t_s is None or r_s is None:
                continue
            samples.append((float(t_s), int(r_s)))
        out[(bench, leg)] = (samples, int(runs_s))
    return out


def render_asan(
    specs: list[BenchSpec],
    rows: list[AsanRow],
    notes: list[str],
    args: argparse.Namespace,
) -> None:
    """渲染 build/bench/asan-results.md (ASan 交叉对比报告)。

    合并语义: 读已有文件 §5 原始样本, 本次测到的 (基准, 腿) 替换, 未测的
    原样保留 (类似 _sync_performance_csv 合并, 但不写 docs/performance.csv)。
    """
    merged: dict[tuple[str, str], tuple[list[tuple[float, int]], int]] = _parse_asan_legs(
        RESULTS_ASAN
    )
    measured: set[str] = set()
    for r in rows:
        samples = [(t, int(rss)) for t, rss in zip(r.stats.time.raw, r.stats.rss.raw)]
        merged[(r.bench, r.leg)] = (samples, r.used_runs)
        measured.add(r.bench)

    all_rows: list[AsanRow] = []
    for (bench, leg), (samples, used) in sorted(merged.items()):
        all_rows.append(
            AsanRow(bench=bench, leg=leg, stats=summarize(samples), used_runs=used)
        )

    bench_names = sorted({s.name for s in specs} | {b for b, _ in merged})
    preserved = sorted({b for b, _ in merged if b not in measured})
    leg_med = {(r.bench, r.leg): r.stats.time.med for r in all_rows}
    leg_rss = {(r.bench, r.leg): r.stats.rss.med for r in all_rows}
    used_map = {(r.bench, r.leg): r.used_runs for r in all_rows}

    md: list[str] = []
    md.append(
        "# build/bench/asan-results.md — ASan 交叉对比实测 (asan-cross-compare)\n"
    )

    md.append("## 0) 环境与协议\n")
    md.append("".join(f"{l}\n" for l in machine_header()))
    md.append(
        "- 编译命令:\n"
        "  - C plain: `clang -O2 -lm bench/c/<name>.c`\n"
        "  - C ASan: `clang -O2 -fsanitize=address -lm bench/c/<name>.c`\n"
        "  - .an check: `python3 -m compiler.main -O3 lib bench/shootout/<name>.an`\n"
    )
    md.append(
        f"- ASAN_OPTIONS 主表 (C ASan main 腿) 全文: `{ASAN_ENV_MAIN}` "
        "(其余为 ASan 运行时默认, 含 quarantine_size_mb=256)。\n"
    )
    md.append(
        f"- ASAN_OPTIONS 敏感性行 (C ASan sens 腿, 仅 binarytree) 全文: `{ASAN_ENV_SENS}` "
        "(quarantine 失效, 供 RSS 敏感性对照)。\n"
    )
    md.append(
        "- **-O 不对称声明**: C 腿 `-O2`, .an check 腿 `-O3` (两编译器各自常规优化档; "
        "跨编译器时间/内存差异不可单纯归因于安全机制)。\n"
    )
    if args.pin is not None:
        md.append(f"- 绑核策略: `taskset -c {args.pin}` 绑定单核。\n")
    else:
        md.append("- 绑核策略: 未绑核 (默认)。\n")
    md.append(
        "- 规模对齐声明: `bench/c/` 14 个 C 基准与 `bench/shootout/` 同规模 "
        "(9 个对齐: bounce/fann/mand/nbody/permute/queen/revcomp/sieve/storage; "
        "5 个原一致: binarytree/fasta/list/spectralnorm/towers)。\n"
    )
    md.append(
        "- 腿序声明: 每基准固定 [C plain → C ASan main → C ASan sensitivity "
        "(仅 binarytree) → .an check] 紧邻执行; 同会话紧邻消除跨会话系统状态漂移, "
        "使 4 腿可互相比较。\n"
    )
    md.append(
        f"- 本次运行: 基准 `{', '.join(sorted(measured)) if measured else '—'}`; "
        f"--runs {args.runs}; --pin {args.pin if args.pin is not None else '无'}。\n"
    )

    md.append(f"\n## 1) 基准 4 腿实测表 ({len(bench_names)} 基准)\n")
    md.append(
        "格式: 时间中位数/IQR/min–max (ms), CV = 时间样本 std/mean, RSS 中位数 (MB), 样本数。\n"
    )
    md.append(
        "| 基准 | 腿 | 时间中位(ms) | IQR(ms) | min–max(ms) | CV "
        "| RSS中位(MB) | RSS IQR(MB) | 样本数 |\n"
    )
    md.append("|---|---|---|---|---|---|---|---|---|\n")
    for r in all_rows:
        t = r.stats.time
        rss = r.stats.rss
        md.append(
            f"| {r.bench} | {LEG_LABEL.get(r.leg, r.leg)} "
            f"| {fmt_ms(t.med)} | {fmt_ms(t.iqr)} | {fmt_ms(t.min)}–{fmt_ms(t.max)} "
            f"| {_cv_str(t.raw)} | {fmt_rss_mb(rss.med)} | {fmt_rss_mb(rss.iqr)} "
            f"| {r.used_runs} |\n"
        )

    md.append("\n## 2) 三口径倍率\n")
    md.append(
        "- 口径(i) ASan 自身开销 = C ASan main 中位 / C plain 中位\n"
        "- 口径(ii) ASan vs 胖指针 = C ASan main 中位 / .an check 中位\n"
        "- 口径(iii) .an check vs C plain = .an check 中位 / C plain 中位\n"
        "- 每口径独立几何平均 = exp(mean(ln(倍率))), 不跨口径混聚。\n"
    )
    md.append(
        "| 基准 | 口径(i) C ASan/C plain | 口径(ii) C ASan/.an check "
        "| 口径(iii) .an check/C plain |\n"
    )
    md.append("|---|---|---|---|\n")
    ratios: dict[str, list[float]] = {"i": [], "ii": [], "iii": []}
    for name in bench_names:
        p = leg_med.get((name, "c_plain"))
        a = leg_med.get((name, "c_asan"))
        y = leg_med.get((name, "an_check"))
        if a is not None and p is not None and p > 0.0:
            ratios["i"].append(a / p)
        if a is not None and y is not None and y > 0.0:
            ratios["ii"].append(a / y)
        if y is not None and p is not None and p > 0.0:
            ratios["iii"].append(y / p)
        md.append(
            f"| {name} | {_ratio_str(a, p)} | {_ratio_str(a, y)} | {_ratio_str(y, p)} |\n"
        )
    geo_cells: list[str] = []
    for k in ("i", "ii", "iii"):
        g = _geo_mean(ratios[k])
        geo_cells.append(f"{g:.2f}×" if g is not None else "—")
    md.append(
        f"| **几何平均** ({', '.join(str(len(v)) for v in ratios.values())} 基准) "
        f"| {geo_cells[0]} | {geo_cells[1]} | {geo_cells[2]} |\n"
    )

    md.append("\n## 3) 敏感性行 (binarytree only)\n")
    md.append(
        f"- C ASan main: `ASAN_OPTIONS={ASAN_ENV_MAIN}` (quarantine 默认活跃)\n"
        f"- C ASan sens: `ASAN_OPTIONS={ASAN_ENV_SENS}` (quarantine 失效)\n"
        "- binarytree 是唯一 quarantine 活跃基准 (DeleteTree 逐迭代交错建删); "
        "sieve 栈数组零 malloc、storage/list 峰值在释放前, quarantine 差异≈0 不可作证明。\n"
    )
    if ("binarytree", "c_asan") in leg_med and ("binarytree", "c_asan_sens") in leg_med:
        mt = leg_med[("binarytree", "c_asan")]
        st = leg_med[("binarytree", "c_asan_sens")]
        mr = leg_rss[("binarytree", "c_asan")]
        sr = leg_rss[("binarytree", "c_asan_sens")]
        md.append("| 腿 | 时间中位(ms) | RSS中位(MB) | 样本数 |\n")
        md.append("|---|---|---|---|\n")
        md.append(
            f"| C ASan main (detect_leaks=0) | {fmt_ms(mt)} | {fmt_rss_mb(mr)} "
            f"| {used_map.get(('binarytree', 'c_asan'), 0)} |\n"
        )
        md.append(
            f"| C ASan sens (quarantine=0) | {fmt_ms(st)} | {fmt_rss_mb(sr)} "
            f"| {used_map.get(('binarytree', 'c_asan_sens'), 0)} |\n"
        )
        dt = (st - mt) / mt * 100.0 if mt else 0.0
        dr = (sr - mr) / mr * 100.0 if mr else 0.0
        md.append(f"- 差异: 时间 {dt:+.1f}%, RSS {dr:+.1f}% (sens − main)。\n")
    else:
        md.append("- binarytree 敏感性腿未测量 (跳过或尚未运行)。\n")

    md.append("\n## 4) 触发记录\n")
    if notes:
        for n in notes:
            md.append(f"- {n}\n")
    else:
        md.append("- 无。\n")
    if preserved:
        md.append(
            f"- 合并说明: 本次未测、从先前运行保留的基准: {', '.join(preserved)}。\n"
        )

    md.append("\n## 5) 原始样本 (附录)\n")
    md.append("格式: 每样本 `wall_ms (rss_kb)`; 行尾 `(N runs)` 为该腿实际测量次数。\n")
    for r in all_rows:
        samples = ", ".join(
            f"{t:.1f} ({rss})" for t, rss in zip(r.stats.time.raw, r.stats.rss.raw)
        )
        md.append(f"- {r.bench} / {r.leg} ({r.used_runs} runs): {samples}\n")

    RESULTS_ASAN.write_text("".join(md), encoding="utf-8")
    print(f"\n结果写入 {RESULTS_ASAN} (合并 {len(all_rows)} 条腿数据, 本次 {len(rows)} 条)\n")


def main() -> int:
    ap = argparse.ArgumentParser(
        description="胖指针安全检查性能实测 (fat-perf-eval / raw-pointers-eval task-3; bench-rerun)"
    )
    ap.add_argument(
        "--suite",
        choices=("shootout", "raw"),
        default="shootout",
        help="基准套件: shootout=自动发现 bench/shootout/ 编译/测量 check+nocheck, "
        "维度① raw 跨套件从 bench/shootout_raw 补齐 (默认); "
        "raw=自动发现 bench/shootout_raw/ (维度① raw 语义适配套件), 以 --raw-pointers 单态测量",
    )
    ap.add_argument(
        "--names",
        default="",
        help="只测指定基准, 逗号分隔 (如 'binarytree,list'); 空=全部",
    )
    ap.add_argument(
        "--runs",
        type=int,
        default=DEFAULT_RUNS,
        help=f"每态运行次数 (默认 {DEFAULT_RUNS}, 协议要求 ≥5)",
    )
    ap.add_argument("--pin", type=int, default=None, help="taskset 绑定的 CPU 编号 (降噪)")
    ap.add_argument("--no-compile", action="store_true", help="不重新编译, 仅测量已存在二进制")
    ap.add_argument("--raw-only", action="store_true", help="仅编译+测量 raw 态 (增补维度①; shootout 套件从 bench/shootout_raw 源编译)")
    ap.add_argument(
        "--ref",
        action="store_true",
        help="编译并运行 bak/old_exp/performance/{c,cpp,rust}/ 参考基线 (记录参考数字)",
    )
    ap.add_argument(
        "--max-state-sec",
        type=float,
        default=DEFAULT_MAX_STATE_SEC,
        help=f"单态 warmup 超限阈值 (秒), 超限则测量次数降到 3 (默认 {DEFAULT_MAX_STATE_SEC:.0f})",
    )
    ap.add_argument(
        "--compile-only",
        action="store_true",
        help="只编译不测量: 编译所选基准三态后退出 (编译验证)",
    )
    ap.add_argument(
        "--sync-baseline",
        action="store_true",
        help="同步写 docs/perf-baseline.csv (金标准基线: 每基准每态绝对中位数 + 机器指纹 + HEAD commit)",
    )
    ap.add_argument(
        "--asan",
        action="store_true",
        help="ASan 交叉对比模式: 每基准 4 腿紧邻 [C plain, C ASan, C ASan sensitivity(binarytree only), .an check], 渲染 build/bench/asan-results.md",
    )
    args = ap.parse_args()
    if args.compile_only and args.no_compile:
        raise SystemExit("--compile-only 与 --no-compile 互斥")
    if args.asan and (args.suite == "raw" or args.raw_only):
        raise SystemExit("--asan 与 --suite raw / --raw-only 互斥 (ASan 对比的 .an 侧为 bench/shootout)")

    if args.runs < 1:
        raise SystemExit("--runs 必须 ≥ 1 (协议建议 ≥5)")
    if not args.compile_only and (not TIME_BIN or not os.path.exists(TIME_BIN)):
        raise SystemExit(f"缺少 {TIME_BIN} (GNU time); 需要 -v 输出峰值常驻内存")
    if args.pin is not None and shutil.which("taskset") is None:
        raise SystemExit("--pin 需要 taskset")
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    name_filter = {n.strip() for n in args.names.split(",") if n.strip()}

    def _filter(specs: list[BenchSpec]) -> list[BenchSpec]:
        if not name_filter:
            return specs
        missing = sorted(name_filter - {s.name for s in specs})
        if missing:
            print(f"[warn] 未找到基准: {', '.join(missing)}", file=sys.stderr)
        return [s for s in specs if s.name in name_filter]

    specs = _filter(discover_shootout_raw() if args.suite == "raw" else discover_shootout())
    if not specs:
        return 0

    is_raw_suite = args.suite == "raw"

    if args.compile_only:
        if args.asan:
            for spec in specs:
                ok_c = compile_ref(spec.name, "c", source_dir=BENCH_DIR)
                ok_a = compile_ref(spec.name, "asan", source_dir=BENCH_C_DIR)
                compile_an(BenchSpec(name=spec.name, subdir="shootout"), no_checks=False)
                legs: list[str] = []
                if ok_c is not None:
                    legs.append("C plain")
                if ok_a is not None:
                    legs.append("C ASan")
                legs.append("an check")
                print(f"[compile-only] {spec.name}: {'/'.join(legs)} OK", file=sys.stderr)
            return 0
        if is_raw_suite or args.raw_only:
            # raw 态统一从 raw 套件源编译 (胖套件不编 raw)
            raw_specs = specs if is_raw_suite else _raw_specs_for(specs)
            for spec in raw_specs:
                compile_an(spec, no_checks=False, raw=True)
                print(f"[compile-only] {spec.name}: raw OK", file=sys.stderr)
        else:
            raw_map = _raw_suite_map()
            for spec in specs:
                compile_an(spec, no_checks=False)
                compile_an(spec, no_checks=True)
                raw_spec = raw_map.get(spec.name)
                if raw_spec is None:
                    print(f"[warn] {spec.name}: 维度①未测 (bench/shootout_raw 缺同名基准)", file=sys.stderr)
                    print(f"[compile-only] {spec.name}: check/nocheck OK", file=sys.stderr)
                    continue
                compile_an(raw_spec, no_checks=False, raw=True)
                print(f"[compile-only] {spec.name}: check/nocheck/raw OK", file=sys.stderr)
        return 0

    if is_raw_suite:
        rows: list[MeasRow] = []
        for spec in specs:
            if not args.no_compile:
                compile_an(spec, no_checks=False, raw=True)
            samples, used, warm = measure(spec_bin(spec, "_raw"), args.runs, args.pin)
            if used < args.runs:
                print(
                    f"[warn] {spec.name}: warmup {warm:.1f}s > {args.max_state_sec:.0f}s, "
                    f"测量次数降为 {used}",
                    file=sys.stderr,
                )
            rows.append(MeasRow(bench=spec.name, state="raw", stats=summarize(samples), used_runs=used))
            print(f"[done] {spec.name}: raw 态 {args.runs} 次", file=sys.stderr)

        render_raw(specs, rows, [], [], args)
        print("基准             态          时间中位数(ms)  峰值RSS(MB)")
        for spec in specs:
            r = next(x for x in rows if x.bench == spec.name and x.state == "raw")
            print(
                f"{spec.name:<16} raw     {fmt_ms(r.stats.time.med):>10}   "
                f"{fmt_rss_mb(r.stats.rss.med):>8}"
            )
        return 0

    if args.raw_only:
        raw_rows, _ = complement_raw(specs, args)
        for spec in specs:
            r = next((x for x in raw_rows if x.bench == spec.name), None)
            if r is None:
                print(f"{spec.name:<16} ①裸指针    维度①未测")
                continue
            print(
                f"{spec.name:<16} ①裸指针    {fmt_ms(r.stats.time.med):>10}   "
                f"{fmt_rss_mb(r.stats.rss.med):>8}"
            )
        return 0

    if args.asan:
        asan_rows, asan_notes = run_asan_suite(specs, args)
        render_asan(specs, asan_rows, asan_notes, args)
        print("基准             腿              时间中位数(ms)  峰值RSS(MB)")
        for r in asan_rows:
            print(
                f"{r.bench:<16} {LEG_LABEL.get(r.leg, r.leg):<16}  "
                f"{fmt_ms(r.stats.time.med):>10}   {fmt_rss_mb(r.stats.rss.med):>8}"
            )
        return 0

    rows, ref_rows, notes = run_suite(specs, args, do_ref=args.ref)

    render_shootout(specs, rows, ref_rows, notes, args)
    print("基准             态          时间中位数(ms)  峰值RSS(MB)")
    for spec in specs:
        for state in ("check", "nocheck", "raw"):
            r = next((x for x in rows if x.bench == spec.name and x.state == state), None)
            if r is None:
                print(f"{spec.name:<16} {state:<8}  维度①未测")
                continue
            print(
                f"{spec.name:<16} {state:<8}  {fmt_ms(r.stats.time.med):>10}   "
                f"{fmt_rss_mb(r.stats.rss.med):>8}"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
