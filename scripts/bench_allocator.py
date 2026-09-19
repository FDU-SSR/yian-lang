#!/usr/bin/env python3
"""bench_allocator.py — 堆分配器基准 (fat vs raw, 快速/完全两档)。

目录与产物:

  bench/ALLOC/an/<name>.an        分配器基准源 (裸态覆盖源 `<name>.raw.an` 同目录)
  bench/ALLOC/specs/<name>.json   侧重 tag 与规模档 (源里 `// bench-scale` 标记行)
  bench/results/alloc.{md,csv}    生成物 (含环境指纹与 commit)

两态与协议:
  分配器基准没有 C 参考, 对照态是 libc `malloc`/`free` (即 `--raw-pointers`), 因此这里是
  fat/raw 两态比较; 每态 `--runs` 次取最小墙钟与最大峰值 RSS, 并校验两态 stdout 一致。

规模档:
  fast 档把 `specs/<name>.json` 里 `scale.fast` 的值写进源里 `// bench-scale` 标记行,
  在 `build/bench/src/fast/ALLOC/` 下生成构建副本再编译; 完全档直接用仓库里的源。
  基准内部断言都写成规模的函数, 因此缩小规模不改变断言语义。

用法:
  python3 scripts/bench_allocator.py                     # 全部基准 (完全档)
  python3 scripts/bench_allocator.py --scale fast        # 快速档
  python3 scripts/bench_allocator.py --names churn_single --runs 5 --pin 4
  python3 scripts/bench_allocator.py --compiler-root /tmp/yian_old   # 换一份源码树编译
"""

from __future__ import annotations

import argparse
import datetime
import json
import re
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BENCH_DIR = ROOT / "bench" / "ALLOC"
AN_DIR = BENCH_DIR / "an"
SPEC_DIR = BENCH_DIR / "specs"
BUILD_DIR = ROOT / "build" / "bench" / "alloc"
SRC_BUILD_DIR = ROOT / "build" / "bench" / "src"
RESULTS_DIR = ROOT / "bench" / "results"
TIME_BIN = "/usr/bin/time"
OPT_LEVEL = "-O2"
RAW_SUFFIX = ".raw.an"
SCALE_MARKER = "// bench-scale"
PROFILES = ("fast", "full")
DEFAULT_RUNS = {"fast": 3, "full": 7}

_RSS_RE = re.compile(r"Maximum resident set size \(kbytes\): (\d+)")
_SCALE_LINE_RE = re.compile(
    r"^(?P<head>\s*let\s+[A-Za-z_]\w*\s*:\s*[A-Za-z_]\w*\s*=\s*)(?P<value>\d+)(?P<tail>;\s*"
    + re.escape(SCALE_MARKER)
    + r".*)$",
    re.MULTILINE,
)


def discover(names: str | None) -> list[str]:
    found = sorted(path.stem for path in AN_DIR.glob("*.an") if not path.name.endswith(RAW_SUFFIX))
    if not found:
        raise SystemExit(f"[discover] {AN_DIR} 下没有基准")
    if names:
        wanted = [name.strip() for name in names.split(",") if name.strip()]
        missing = [name for name in wanted if name not in found]
        if missing:
            raise SystemExit(f"[args] 未知基准: {', '.join(missing)}")
        return wanted
    return found


def load_spec(name: str) -> dict[str, object]:
    path = SPEC_DIR / f"{name}.json"
    if not path.exists():
        raise SystemExit(f"[spec] 缺少 {path}")
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or data.get("name") != name:
        raise SystemExit(f"[spec] {path}: name 字段应为 {name!r}")
    return data


def scale_value(spec: dict[str, object], profile: str) -> int | None:
    scale = spec.get("scale")
    if not isinstance(scale, dict) or profile != "fast":
        return None
    fast = scale.get("fast")
    return int(fast) if fast is not None else None


def source_path(name: str, raw: bool, spec: dict[str, object], profile: str) -> Path:
    """裸态优先用 `<name>.raw.an` 覆盖源; fast 档生成替换过规模的构建副本。"""
    value = scale_value(spec, profile)
    if value is None:
        path = AN_DIR / f"{name}.raw.an" if raw and (AN_DIR / f"{name}.raw.an").exists() else AN_DIR / f"{name}.an"
        return path
    out_dir = SRC_BUILD_DIR / profile / "ALLOC"
    out_dir.mkdir(parents=True, exist_ok=True)
    for candidate in (AN_DIR / f"{name}.an", AN_DIR / f"{name}.raw.an"):
        if not candidate.exists():
            continue
        if candidate.name.endswith(RAW_SUFFIX) and not raw:
            continue
        if not candidate.name.endswith(RAW_SUFFIX) and raw and (AN_DIR / f"{name}.raw.an").exists():
            continue
        text = candidate.read_text(encoding="utf-8")
        patched, count = _SCALE_LINE_RE.subn(
            lambda match: f"{match.group('head')}{value}{match.group('tail')}", text
        )
        if count != 1:
            raise SystemExit(f"[scale] {candidate}: 需要恰好 1 行 `{SCALE_MARKER}` 标记, 实际 {count}")
        dst = out_dir / candidate.name
        dst.write_text(patched, encoding="utf-8")
        return dst
    raise SystemExit(f"[src] {name}: 找不到合适的源")


def compile_one(name: str, raw: bool, compiler_root: Path, spec: dict[str, object], profile: str) -> Path:
    binary = BUILD_DIR / f"{name}.{'raw' if raw else 'fat'}"
    binary.parent.mkdir(parents=True, exist_ok=True)
    src = source_path(name, raw, spec, profile)
    cmd = [sys.executable, "-m", "compiler.main", OPT_LEVEL]
    if raw:
        cmd.append("--raw-pointers")
    cmd += [str(compiler_root / "lib" / "src"), str(src), "-o", str(binary)]
    proc = subprocess.run(cmd, capture_output=True, text=True, cwd=compiler_root)
    if proc.returncode != 0:
        raise SystemExit(f"[compile] {name} ({'raw' if raw else 'fat'}) 失败:\n{proc.stdout}\n{proc.stderr}")
    return binary


def run_one(binary: Path, pin: int | None) -> tuple[float, float, str]:
    """返回 (墙钟 ms, 峰值 RSS MB, stdout)。"""
    pre = ["taskset", "-c", str(pin)] if pin is not None else []
    env = {"LANG": "C", "PATH": "/usr/bin:/bin"}
    start = time.monotonic()
    proc = subprocess.run(
        pre + [TIME_BIN, "-v", str(binary)], capture_output=True, text=True, env=env, check=False
    )
    wall_ms = (time.monotonic() - start) * 1000.0
    match = _RSS_RE.search(proc.stderr)
    if proc.returncode != 0 or match is None:
        raise SystemExit(f"[run] {binary} 失败 (退出码 {proc.returncode}):\n{proc.stdout}\n{proc.stderr}")
    return wall_ms, int(match.group(1)) / 1024.0, proc.stdout


def _git_head() -> str:
    proc = subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True, cwd=ROOT)
    return proc.stdout.strip() if proc.returncode == 0 else "n/a"


def main() -> int:
    parser = argparse.ArgumentParser(description="堆分配器基准 (fat vs raw)")
    parser.add_argument("--names", help="只跑指定基准 (逗号分隔)")
    parser.add_argument("--scale", choices=PROFILES, default="full", help="规模档 (默认 full)")
    parser.add_argument("--runs", type=int, default=None, help="每个二进制的测量次数 (默认 fast=3 / full=7)")
    parser.add_argument("--pin", type=int, default=None, help="taskset 绑定的 CPU 编号")
    parser.add_argument("--compiler-root", type=Path, default=ROOT, help="用哪份源码树编译 (默认本仓库)")
    args = parser.parse_args()

    root = args.compiler_root.resolve()
    runs = args.runs if args.runs is not None else DEFAULT_RUNS[args.scale]
    names = discover(args.names)
    print(f"编译器源码树: {root}")
    print(f"集合: ALLOC  规模档: {args.scale}  采样: 每态 {runs} 次取最小墙钟 / 最大峰值 RSS; 绑核: {args.pin if args.pin is not None else '否'}")
    print("")
    header = "| 基准 | fat 最小 (ms) | raw 最小 (ms) | fat/raw | fat 峰值 RSS (MB) | raw 峰值 RSS (MB) | 输出一致 | 规模 |"
    print(header)
    print("| --- | ---: | ---: | ---: | ---: | ---: | --- | ---: |")
    csv_rows: list[str] = []
    for name in names:
        spec = load_spec(name)
        value = scale_value(spec, args.scale)
        results: dict[str, tuple[float, float, str]] = {}
        for raw in (False, True):
            binary = compile_one(name, raw, root, spec, args.scale)
            samples = [run_one(binary, args.pin) for _ in range(runs)]
            results["raw" if raw else "fat"] = (
                min(sample[0] for sample in samples),
                max(sample[1] for sample in samples),
                samples[0][2],
            )
        fat, raw_result = results["fat"], results["raw"]
        ratio = fat[0] / raw_result[0] if raw_result[0] else float("nan")
        match = "是" if fat[2] == raw_result[2] else "**否**"
        scale_text = str(value) if value is not None else "默认"
        print(
            f"| {name} | {fat[0]:.1f} | {raw_result[0]:.1f} | {ratio:.2f}× "
            f"| {fat[1]:.1f} | {raw_result[1]:.1f} | {match} | {scale_text} |"
        )
        csv_rows.append(
            f"{name},{fat[0]:.1f},{raw_result[0]:.1f},{ratio:.3f},{fat[1]:.1f},{raw_result[1]:.1f},"
            f"{'yes' if fat[2] == raw_result[2] else 'no'},{scale_text}"
        )

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    md = [
        f"# 分配器基准 (fat vs raw) — 规模档 `{args.scale}`",
        "",
        "由 `scripts/bench_allocator.py` 生成; 分配器基准没有 C 参考, 对照态是 libc `malloc`/`free`。",
        f"采样: 每态 {runs} 次取最小墙钟 / 最大峰值 RSS; 绑核: {args.pin if args.pin is not None else '否'}; "
        f"commit: `{_git_head()}`; 日期: {datetime.date.today().isoformat()}",
        "",
        header,
        "| --- | ---: | ---: | ---: | ---: | ---: | --- | ---: |",
        *csv_rows_to_md(csv_rows),
        "",
    ]
    (RESULTS_DIR / "alloc.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    (RESULTS_DIR / "alloc.csv").write_text(
        "# bench/results/alloc.csv — 分配器基准 (fat vs raw)\n"
        f"# scale: {args.scale}  runs: {runs}  pin: {args.pin if args.pin is not None else 'none'}"
        f"  commit: {_git_head()}  date: {datetime.date.today().isoformat()}\n"
        "name,fat_min_ms,raw_min_ms,ratio_fat_raw,fat_rss_mb,raw_rss_mb,stdout_match,scale\n"
        + "\n".join(csv_rows)
        + "\n",
        encoding="utf-8",
    )
    print(f"\n[write] bench/results/alloc.md")
    print(f"[write] bench/results/alloc.csv")
    return 0


def csv_rows_to_md(rows: list[str]) -> list[str]:
    """把 CSV 行转成 markdown 表格行 (列顺序与表头一致)。"""
    return ["| " + " | ".join(row.split(",")) + " |" for row in rows]


if __name__ == "__main__":
    sys.exit(main())
