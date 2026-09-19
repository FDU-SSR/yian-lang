#!/usr/bin/env python3
"""verify_bench_c.py — 用 C 参考实现校验 .an 基准的语义权威值.

`bench/c/*.c` 是本仓库自写的 C 参考实现, 规模与断言值与 `bench/shootout/*.an`
头部注释对齐; 两边共享的规格 (C 侧 argv 与权威值) 定义在 `scripts/bench_common.py`。
本脚本编译每个 C 参考 (clang -O2 -lm)、各跑 1 次, 断言 (退出码, stdout) 符合权威值,
用来独立确认 .an 基准确实实现了目标算法与规模 (fat/raw 输出一致只说明两态做同一件事)。

用法:
  python3 scripts/verify_bench_c.py            # 全部 19 个
  python3 scripts/verify_bench_c.py --names bounce,sieve

退出码: 0 = 全部通过; 1 = 存在失败 (仍跑完其余基准)。
产物: build/bench/cbin/<name>。
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

from bench_common import C_SPECS

ROOT = Path(__file__).resolve().parent.parent
BENCH_C_DIR = ROOT / "bench" / "c"
BUILD_DIR = ROOT / "build" / "bench" / "cbin"


def compile_one(name: str) -> Path:
    src = BENCH_C_DIR / f"{name}.c"
    dst = BUILD_DIR / name
    BUILD_DIR.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["clang", "-O2", "-lm", str(src), "-o", str(dst)],
        check=True,
        capture_output=True,
        text=True,
    )
    return dst


def run_one(exe: Path, argv: list[str]) -> tuple[int, str]:
    proc = subprocess.run(
        [str(exe), *argv], capture_output=True, text=True, timeout=600, env=_env_plain()
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

    names = sorted(C_SPECS)
    if args.names:
        wanted = [n.strip() for n in args.names.split(",") if n.strip()]
        missing = [n for n in wanted if n not in C_SPECS]
        if missing:
            raise SystemExit(f"[args] 未知基准: {', '.join(missing)}")
        names = wanted

    results: list[tuple[str, bool, str]] = []
    for name in names:
        spec = C_SPECS[name]
        try:
            exe = compile_one(name)
            rc, stdout = run_one(exe, spec.argv)
        except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
            results.append((name, False, f"compile/run error: {exc}"))
            print(f"{name}: FAIL (got=error: {exc}, expected={spec.expect})")
            continue
        got = f"rc={rc}, stdout={stdout.strip()!r}"
        ok = spec.check(rc, stdout)
        results.append((name, ok, got))
        print(f"{name}: {'PASS' if ok else 'FAIL'} (got={got}, expected={spec.expect})")

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
