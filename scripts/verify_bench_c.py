#!/usr/bin/env python3
"""bench/c 基准验证脚本: 编译每个 bench/c/*.c (clang -O2 -lm), 各跑 1 次,
断言 stdout/rc == 验证表(规模与断言值对齐 bench/shootout/*.an 头部注释)。

用法: python3 scripts/verify_bench_c.py
退出码: 0 = 全部通过; 1 = 存在失败(但仍跑完其余基准)。
"""

import os
import subprocess
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BENCH_C_DIR = os.path.join(REPO_ROOT, "bench", "c")
BUILD_DIR = os.path.join(REPO_ROOT, "build", "bench", "c")

# 基准名 -> (检查函数(rc, stdout) -> bool, 期望值描述)
# 断言值全部取自 bench/shootout/*.an 头部注释(规模对齐后):
#   bounce 368285073 / fann 51 / mand 126 / nbody -1 / permute 823059745
#   queen 150(仅 assert 内部, rc==0) / revcomp 128 / sieve 148933(仅 assert 内部, rc==0)
#   storage 21523360 / binarytree check%256==176 / fasta -71 / list 长度 30(rc==0)
#   spectralnorm res%256==78 / towers 2^30-1(rc==0)
BENCHMARKS = [
    ("binarytree", lambda rc, out: rc == 0 and int(out.strip()) % 256 == 176, "rc==0, check%256==176"),
    ("bounce", lambda rc, out: "368285073" in out, 'stdout contains "368285073"'),
    ("fann", lambda rc, out: rc == 51 and "51" in out, "rc==51, stdout contains \"51\""),
    ("fasta", lambda rc, out: "-71" in out, 'stdout contains "-71"'),
    ("list", lambda rc, out: rc == 0, "rc==0"),
    ("mand", lambda rc, out: "126" in out, 'stdout contains "126"'),
    ("nbody", lambda rc, out: "-1" in out, 'stdout contains "-1"'),
    ("permute", lambda rc, out: "823059745" in out, 'stdout contains "823059745"'),
    ("queen", lambda rc, out: rc == 0, "rc==0"),
    ("revcomp", lambda rc, out: "128" in out, 'stdout contains "128" (Checksum:)'),
    ("sieve", lambda rc, out: rc == 0, "rc==0"),
    ("spectralnorm", lambda rc, out: "78" in out, 'stdout contains "78"'),
    ("storage", lambda rc, out: "21523360" in out, 'stdout contains "21523360"'),
    ("towers", lambda rc, out: rc == 0, "rc==0"),
]


def compile_one(name: str) -> None:
    src = os.path.join(BENCH_C_DIR, name + ".c")
    dst = os.path.join(BUILD_DIR, name)
    subprocess.run(
        ["clang", "-O2", "-lm", src, "-o", dst],
        check=True,
        capture_output=True,
        text=True,
    )


def run_one(name: str) -> tuple[int, str]:
    exe = os.path.join(BUILD_DIR, name)
    proc = subprocess.run([exe], capture_output=True, text=True, timeout=600)
    return proc.returncode, proc.stdout


def main() -> int:
    os.makedirs(BUILD_DIR, exist_ok=True)

    results: list[tuple[str, bool, str]] = []
    for name, check, expected_desc in BENCHMARKS:
        try:
            compile_one(name)
            rc, stdout = run_one(name)
        except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
            results.append((name, False, f"compile/run error: {exc}"))
            print(f"{name}: FAIL (got=error: {exc}, expected={expected_desc})")
            continue

        got = f"rc={rc}, stdout={stdout.strip()!r}"
        ok = check(rc, stdout)
        results.append((name, ok, got))
        print(f"{name}: {'PASS' if ok else 'FAIL'} (got={got}, expected={expected_desc})")

    passed = sum(1 for _, ok, _ in results if ok)
    total = len(results)
    print()
    if passed == total:
        print(f"{total}/{total} PASS")
        return 0
    for name, ok, got in results:
        if not ok:
            print(f"FAILED: {name} (got={got})")
    print(f"{passed}/{total} PASS")
    return 1


if __name__ == "__main__":
    sys.exit(main())
