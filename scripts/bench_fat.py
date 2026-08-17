#!/usr/bin/env python3
"""bench_fat.py — 胖指针安全性能实测脚本 (fat-perf-eval / raw-pointers-eval task-3;
shootout-perf-eval task-3 扩展).

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

套件:
  shootout  自动发现 bench/shootout/*.an (14 基准); 附 c/cpp/rust 参考基线可选
  raw       自动发现 bench/shootout_raw/*.an (14 基准, 维度① raw 语义适配套件:
            T*→T[] 显式用 from_raw_parts, 因裸模式下隐式 coerce 被拒); 以
            --raw-pointers 编译单态测量, 写 shootout-raw-results.md

用法:
  python3 scripts/bench_fat.py --suite shootout         # shootout 套件, 写 shootout-results.md
  python3 scripts/bench_fat.py --suite raw              # raw 套件, 写 shootout-raw-results.md
  python3 scripts/bench_fat.py --names binarytree,list  # 只测指定基准 (逗号分隔)
  python3 scripts/bench_fat.py --ref                    # 附加编译运行 c/cpp/rust 参考基线
  python3 scripts/bench_fat.py --runs 7                 # 每态运行次数 (默认 5, 协议要求 ≥5)
  python3 scripts/bench_fat.py --pin 4                  # taskset 绑核降噪
  python3 scripts/bench_fat.py --no-compile             # 不重新编译, 仅测量已存在二进制
  python3 scripts/bench_fat.py --raw-only               # 仅编译+测量 raw 态
  python3 scripts/bench_fat.py --max-state-sec 120      # 单态 warmup 超限则测量次数降到 3
  python3 scripts/bench_fat.py --compile-only           # 只编译不测量 (编译验证)

独立脚本: 不触碰 scripts/run_tests.py / run_fat*.py 等测试 runner; 不修改基准源码。
输出: build/bench/shootout-results.md (shootout)。
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
OUT_DIR = ROOT / "build" / "bench"
RESULTS_SHOOTOUT = OUT_DIR / "shootout-results.md"
RESULTS_SHOOTOUT_RAW = OUT_DIR / "shootout-raw-results.md"
SHOOTOUT_DIR = BENCH_DIR / "shootout"
SHOOTOUT_RAW_DIR = BENCH_DIR / "shootout_raw"
REF_ROOT = ROOT / "bak" / "old_exp" / "performance"
REF_OUT_DIR = OUT_DIR / "ref"

TIME_BIN = "/usr/bin/time"
DEFAULT_RUNS = 5
DEFAULT_MAX_STATE_SEC = 120.0

_RSS_RE = re.compile(r"Maximum resident set size \(kbytes\): (\d+)")

# c/cpp/rust 参考基线编译命令: lang → (源码子目录, 扩展名, 编译命令前缀)
# - C/C++ 需要 -lm (nbody/spectralnorm 用 sqrt)
# - Rust 用 rustc -O (opt-level=2, 与 C/C++ 的 -O2 对齐)
REF_LANG_CMD: dict[str, tuple[str, str, list[str]]] = {
    "c": ("c", ".c", ["clang", "-O2", "-lm"]),
    "cpp": ("cpp", ".cpp", ["clang++", "-O2", "-lm"]),
    "rust": ("rust", ".rs", ["rustc", "-O"]),
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
    cmd = [sys.executable, "-m", "compiler.main", "-O2"]
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


def compile_ref(name: str, lang: str) -> Path | None:
    """编译 bak/old_exp/performance/<lang>/<name> 参考基线; 源不存在返回 None。"""
    subdir, ext, base = REF_LANG_CMD[lang]
    src = REF_ROOT / subdir / f"{name}{ext}"
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
) -> tuple[list[tuple[float, int]], int, float]:
    """运行 runs 次, 返回 (样本, 实际次数, warmup 秒数)。

    先 1 次 warmup (计时确认稳定窗口, §10.5 步骤 2; 不计入样本)。若 warmup 超过
    max_state_sec (默认 120s) 且 runs>3, 实际测量次数降到 3 (shootout-perf-eval 策略)。
    allow_nonzero=True 时容忍非零退出码 (参考基线 fann 等以退出码传结果)。
    """
    env = _env_plain()
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
    rows: list[MeasRow] = []
    notes: list[str] = []

    for spec in specs:
        if not args.no_compile:
            compile_an(spec, no_checks=False)
            compile_an(spec, no_checks=True)
            compile_an(spec, no_checks=False, raw=True)
        for label, binp in (
            ("check", spec_bin(spec)),
            ("nocheck", spec_bin(spec, "_nfc")),
            ("raw", spec_bin(spec, "_raw")),
        ):
            samples, used, warm = measure(binp, args.runs, args.pin)
            if used < args.runs:
                notes.append(
                    f"{spec.name}/{label}: warmup {warm:.1f}s > {args.max_state_sec:.0f}s, "
                    f"测量次数降为 {used}"
                )
            rows.append(MeasRow(bench=spec.name, state=label, stats=summarize(samples), used_runs=used))
        print(f"[done] {spec.name}: 三态 (raw/nocheck/check) 各 {args.runs} 次", file=sys.stderr)

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


def _matrix_table(
    rows: list[MeasRow],
    state_label: dict[str, str],
) -> list[str]:
    md: list[str] = []
    md.append("| 基准 | 态 | 时间中位数 (ms) | IQR (ms) | min–max (ms) | 峰值 RSS 中位数 (MB) | RSS IQR (MB) |\n")
    md.append("|---|---|---|---|---|---|---|\n")
    for r in rows:
        md.append(
            f"| {r.bench} | {state_label[r.state]} "
            f"| {fmt_ms(r.stats.time.med)} | {fmt_ms(r.stats.time.iqr)} "
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
        "以维度①裸指针为基准: 表示成本 = ②nocheck − ①raw (胖 40B 表示相对裸 8B); "
        "检查成本 = ③check − ②nocheck (检查发射); 总成本 = ③check − ①raw。\n"
    )
    md.append("| 基准 | 表示成本 Δms (②−①) | 检查成本 Δms (③−②) | 总成本 Δms (③−①) | 表示成本 ΔRSS (MB) | 总成本 ΔRSS (MB) |\n")
    md.append("|---|---|---|---|---|---|\n")
    for spec in specs:
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
    return md


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
        "# build/bench/shootout-results.md — shootout 基准三态性能实测 (shootout-perf-eval task-3)\n"
    )
    md.append("## 0) 环境与协议\n")
    md.append("".join(f"{l}\n" for l in machine_header()))
    md.append(
        "- 三态编译 (`python3 -m compiler.main -O2 lib bench/shootout/<name>.an`"
        " / 无检查加 `--no-fat-checks` / 裸指针加 `--raw-pointers`), 每态运行 "
        f"{args.runs} 次取中位数, 报告 IQR/min/max (docs/security-code.md §10.5, 同一机器同一负载)。\n"
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

    md.append("\n## 2) 14×3 实测时间矩阵 (维度①: 裸 / 胖无检查 / 完整胖)\n")
    md += _matrix_table(rows, state_label)

    md.append("\n## 3) 成本分解: 表示成本 / 检查成本 / 总成本\n")
    md += _attribution_table(specs, rows)
    md.append(
        "\n注: 表示成本含胖指针 5 字段读写 / 分配块锁槽头 / 帧锁保留带来的访存与占用; "
        "检查成本含 CheckSafeAccess/CheckInBounds/CheckElementArith/CheckPtrCmp/GenKey/锁槽写等。\n"
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
        "- 编译: `python3 -m compiler.main -O2 --raw-pointers lib bench/shootout_raw/<name>.an`, "
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


def main() -> int:
    ap = argparse.ArgumentParser(
        description="胖指针安全检查性能实测 (fat-perf-eval / raw-pointers-eval task-3; shootout-perf-eval task-3)"
    )
    ap.add_argument(
        "--suite",
        choices=("shootout", "raw"),
        default="shootout",
        help="基准套件: shootout=自动发现 bench/shootout/ 三态 (默认); "
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
    ap.add_argument("--raw-only", action="store_true", help="仅编译+测量 raw 态 (增补维度①)")
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
    args = ap.parse_args()
    if args.compile_only and args.no_compile:
        raise SystemExit("--compile-only 与 --no-compile 互斥")

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
        for spec in specs:
            if is_raw_suite or args.raw_only:
                compile_an(spec, no_checks=False, raw=True)
                print(f"[compile-only] {spec.name}: raw OK", file=sys.stderr)
            else:
                compile_an(spec, no_checks=False)
                compile_an(spec, no_checks=True)
                compile_an(spec, no_checks=False, raw=True)
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
        rows: list[MeasRow] = []
        for spec in specs:
            if not args.no_compile:
                compile_an(spec, no_checks=False, raw=True)
            samples, _, _ = measure(spec_bin(spec, "_raw"), args.runs, args.pin)
            rows.append(MeasRow(bench=spec.name, state="raw", stats=summarize(samples)))
        for spec in specs:
            raw = next(r for r in rows if r.bench == spec.name)
            print(
                f"{spec.name:<16} ①裸指针    {fmt_ms(raw.stats.time.med):>10}   "
                f"{fmt_rss_mb(raw.stats.rss.med):>8}"
            )
        return 0

    rows, ref_rows, notes = run_suite(specs, args, do_ref=args.ref)

    render_shootout(specs, rows, ref_rows, notes, args)
    print("基准             态          时间中位数(ms)  峰值RSS(MB)")
    for spec in specs:
        for state in ("check", "nocheck", "raw"):
            r = next(x for x in rows if x.bench == spec.name and x.state == state)
            print(
                f"{spec.name:<16} {state:<8}  {fmt_ms(r.stats.time.med):>10}   "
                f"{fmt_rss_mb(r.stats.rss.med):>8}"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
