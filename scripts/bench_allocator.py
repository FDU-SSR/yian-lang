#!/usr/bin/env python3
"""bench_allocator.py — 堆分配器基准 (fat vs raw).

`bench/alloc/` 下的三个基准各自用胖指针与裸指针编译, 运行 `--runs` 次取最小墙钟与
最大峰值 RSS:

  churn_single  同尺寸 churn: 4,000,000 轮 dyn[4] i32 分配/释放
  churn_mixed   混合尺寸 churn: 256 个常驻小块 + 200,000 轮"分配大块/先释放大块再释放小块"
  grow_free     增长-释放循环: 40 轮 × 4096 个 4 KiB 块 (每轮 16 MiB)

裸态用 libc malloc/free, 作为同一份源码的对照口径. 数字用于比较分配器实现、观察退化,
不是严谨实验.

用法:
  python3 scripts/bench_allocator.py                     # 全部
  python3 scripts/bench_allocator.py --names churn_mixed
  python3 scripts/bench_allocator.py --runs 3 --pin 4
  python3 scripts/bench_allocator.py --compiler-root /tmp/yian_old   # 换一份源码树编译
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BENCH_DIR = ROOT / "bench" / "alloc"
BUILD_DIR = ROOT / "build" / "bench" / "alloc"
TIME_BIN = "/usr/bin/time"
OPT_LEVEL = "-O2"

_RSS_RE = re.compile(r"Maximum resident set size \(kbytes\): (\d+)")


def discover(names: str | None) -> list[str]:
    found = sorted(path.stem for path in BENCH_DIR.glob("*.an") if not path.name.endswith(".raw.an"))
    if not found:
        raise SystemExit(f"[discover] {BENCH_DIR} 下没有基准")
    if names:
        wanted = [name.strip() for name in names.split(",") if name.strip()]
        missing = [name for name in wanted if name not in found]
        if missing:
            raise SystemExit(f"[args] 未知基准: {', '.join(missing)}")
        return wanted
    return found


def source_path(name: str, raw: bool) -> Path:
    """裸态优先用 `<name>.raw.an` 覆盖源 (元素类型在两种模式下不同)."""
    override = BENCH_DIR / f"{name}.raw.an"
    if raw and override.exists():
        return override
    return BENCH_DIR / f"{name}.an"


def compile_one(name: str, raw: bool, compiler_root: Path) -> Path:
    binary = BUILD_DIR / f"{name}.{'raw' if raw else 'fat'}"
    binary.parent.mkdir(parents=True, exist_ok=True)
    cmd = [sys.executable, "-m", "compiler.main", OPT_LEVEL]
    if raw:
        cmd.append("--raw-pointers")
    cmd += [str(compiler_root / "lib" / "src"), str(source_path(name, raw)), "-o", str(binary)]
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


def main() -> int:
    parser = argparse.ArgumentParser(description="堆分配器基准 (fat vs raw)")
    parser.add_argument("--names", help="只跑指定基准 (逗号分隔)")
    parser.add_argument("--runs", type=int, default=3, help="每个二进制的测量次数 (默认 3)")
    parser.add_argument("--pin", type=int, default=None, help="taskset 绑定的 CPU 编号")
    parser.add_argument("--compiler-root", type=Path, default=ROOT, help="用哪份源码树编译 (默认本仓库)")
    args = parser.parse_args()

    root = args.compiler_root.resolve()
    names = discover(args.names)
    print(f"编译器源码树: {root}")
    print(f"采样: 每态 {args.runs} 次取最小墙钟 / 最大峰值 RSS; 绑核: {args.pin if args.pin is not None else '否'}")
    print("")
    print("| 基准 | fat 最小 (ms) | raw 最小 (ms) | fat/raw | fat 峰值 RSS (MB) | raw 峰值 RSS (MB) | 输出一致 |")
    print("| --- | ---: | ---: | ---: | ---: | ---: | --- |")
    for name in names:
        results: dict[str, tuple[float, float, str]] = {}
        for raw in (False, True):
            binary = compile_one(name, raw, root)
            samples = [run_one(binary, args.pin) for _ in range(args.runs)]
            results["raw" if raw else "fat"] = (
                min(sample[0] for sample in samples),
                max(sample[1] for sample in samples),
                samples[0][2],
            )
        fat, raw_result = results["fat"], results["raw"]
        ratio = fat[0] / raw_result[0] if raw_result[0] else float("nan")
        print(
            f"| {name} | {fat[0]:.1f} | {raw_result[0]:.1f} | {ratio:.2f}× "
            f"| {fat[1]:.1f} | {raw_result[1]:.1f} | {'是' if fat[2] == raw_result[2] else '**否**'} |"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
