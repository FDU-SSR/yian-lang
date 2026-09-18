#!/usr/bin/env python3
"""verify_bench_c.py — 用 C 参考实现校验 .an 基准的语义权威值.

`bench/c/*.c` 是本仓库自写的 C 参考实现 (不参与 fat/raw 计时), 规模与断言值
与 `bench/shootout/*.an` 头部注释对齐。本脚本编译每个 C 参考 (clang -O2 -lm)、
各跑 1 次, 断言 stdout/退出码符合验证表, 用来独立确认 .an 基准确实实现了目标
算法 (fat/raw 输出一致只说明两态做同一件事)。

用法:
  python3 scripts/verify_bench_c.py            # 全部 19 个
  python3 scripts/verify_bench_c.py --names bounce,sieve

退出码: 0 = 全部通过; 1 = 存在失败 (仍跑完其余基准)。
产物: build/bench/c/<name>。
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path
from typing import Callable

ROOT = Path(__file__).resolve().parent.parent
BENCH_C_DIR = ROOT / "bench" / "c"
BUILD_DIR = ROOT / "build" / "bench" / "c"

Check = Callable[[int, str], bool]

# 基准名 -> (检查函数(rc, stdout), 期望值描述)。断言值取自 bench/shootout/*.an
# 头部注释 (规模对齐后): bounce 368285073 / fann 51 / mand 126 / nbody -1 /
# permute 823059745 / queen 150(仅 assert 内部, rc==0) / revcomp 128 /
# sieve 148933(仅 assert 内部, rc==0) / storage 21523360 /
# binarytree check%256==176 / fasta -71 / list 长度 30(rc==0) /
# spectralnorm res%256==78 / towers 2^30-1(rc==0)
BENCHMARKS: list[tuple[str, Check, str]] = [
    ("binarytree", lambda rc, out: rc == 0 and int(out.strip()) % 256 == 176,
     "rc==0, check%256==176"),
    ("bounce", lambda rc, out: "368285073" in out, 'stdout contains "368285073"'),
    ("cd", lambda rc, out: rc == 0 and out.strip() == "4305",
     'rc==0, stdout=="4305" (100 架 200 帧)'),
    ("deltablue", lambda rc, out: rc == 0 and out.strip() == "4993240000",
     'rc==0, stdout=="4993240000" (N=100 × ITER=14000; 每趟 356660)'),
    ("fann", lambda rc, out: rc == 51 and "51" in out, 'rc==51, stdout contains "51"'),
    ("fasta", lambda rc, out: "-71" in out, 'stdout contains "-71"'),
    ("havlak", lambda rc, out: rc == 0 and out.strip() == "1605 5213",
     'rc==0, stdout=="1605 5213" (loops × nodes)'),
    ("json", lambda rc, out: rc == 0 and "5869103028000" in out,
     'rc==0, stdout contains "5869103028000" (ops/成员/字符数)'),
    ("list", lambda rc, out: rc == 0, "rc==0"),
    ("mand", lambda rc, out: "126" in out, 'stdout contains "126"'),
    ("nbody", lambda rc, out: "-1" in out, 'stdout contains "-1"'),
    ("permute", lambda rc, out: "823059745" in out, 'stdout contains "823059745"'),
    ("queen", lambda rc, out: rc == 0, "rc==0"),
    ("revcomp", lambda rc, out: "128" in out, 'stdout contains "128" (Checksum:)'),
    ("richards", lambda rc, out: rc == 0 and "23246 9297" in out,
     'rc==0, stdout contains "23246 9297" (queue/hold 计数)'),
    ("sieve", lambda rc, out: rc == 0, "rc==0"),
    ("spectralnorm", lambda rc, out: "78" in out, 'stdout contains "78"'),
    ("storage", lambda rc, out: "21523360" in out, 'stdout contains "21523360"'),
    ("towers", lambda rc, out: rc == 0, "rc==0"),
]


def compile_one(name: str) -> Path:
    src = BENCH_C_DIR / f"{name}.c"
    dst = BUILD_DIR / name
    subprocess.run(
        ["clang", "-O2", "-lm", str(src), "-o", str(dst)],
        check=True,
        capture_output=True,
        text=True,
    )
    return dst


def run_one(exe: Path) -> tuple[int, str]:
    proc = subprocess.run(
        [str(exe)], capture_output=True, text=True, timeout=600, env=_env_plain()
    )
    return proc.returncode, proc.stdout


def _env_plain() -> dict[str, str]:
    env = os.environ.copy()
    env["LANG"] = "C"
    return env


def main() -> int:
    parser = argparse.ArgumentParser(description="用 C 参考校验 .an 基准的语义权威值")
    parser.add_argument("--names", help="只校验指定基准 (逗号分隔)")
    args = parser.parse_args()

    specs = BENCHMARKS
    if args.names:
        wanted = [n.strip() for n in args.names.split(",") if n.strip()]
        by_name = {name: (name, check, desc) for name, check, desc in BENCHMARKS}
        missing = [n for n in wanted if n not in by_name]
        if missing:
            raise SystemExit(f"[args] 未知基准: {', '.join(missing)}")
        specs = [by_name[n] for n in wanted]

    BUILD_DIR.mkdir(parents=True, exist_ok=True)
    results: list[tuple[str, bool, str]] = []
    for name, check, desc in specs:
        try:
            exe = compile_one(name)
            rc, stdout = run_one(exe)
        except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
            results.append((name, False, f"compile/run error: {exc}"))
            print(f"{name}: FAIL (got=error: {exc}, expected={desc})")
            continue
        got = f"rc={rc}, stdout={stdout.strip()!r}"
        ok = check(rc, stdout)
        results.append((name, ok, got))
        print(f"{name}: {'PASS' if ok else 'FAIL'} (got={got}, expected={desc})")

    passed = sum(1 for _, ok, _ in results if ok)
    total = len(results)
    print()
    for name, ok, got in results:
        if not ok:
            print(f"FAILED: {name} (got={got})")
    print(f"{passed}/{total} PASS")
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
