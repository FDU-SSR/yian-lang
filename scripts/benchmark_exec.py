#!/usr/bin/env python3

import argparse
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path


@dataclass
class BenchmarkResult:
    program: str
    iterations: int
    total_seconds: float
    timed_out: bool
    failed: bool
    fail_code: int | None
    fail_iteration: int | None


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Benchmark multiple executables by running each program for a fixed "
            "number of iterations and comparing total runtime."
        )
    )
    parser.add_argument(
        "programs",
        nargs="+",
        help="One or more executable files to benchmark.",
    )
    parser.add_argument(
        "-n",
        "--iterations",
        type=int,
        required=True,
        help="How many times to run each program.",
    )
    parser.add_argument(
        "--limit",
        type=float,
        default=None,
        help=(
            "Execution time limit in seconds for one program over all iterations. "
            "If exceeded, that program is marked as timeout."
        ),
    )
    return parser.parse_args()


def validate_inputs(programs: list[str], iterations: int, limit: float | None) -> None:
    if iterations <= 0:
        raise ValueError("--iterations must be greater than 0")

    if limit is not None and limit <= 0:
        raise ValueError("--limit must be greater than 0")

    for prog in programs:
        p = Path(prog)
        if not p.exists():
            raise ValueError(f"Program not found: {prog}")
        if p.is_dir():
            raise ValueError(f"Program is a directory, expected executable file: {prog}")


def run_single_program(program: str, iterations: int, limit: float | None) -> BenchmarkResult:
    program_path = Path(program)
    executable = str(program_path.resolve()) if program_path.exists() else program

    started = time.perf_counter()
    timed_out = False
    failed = False
    fail_code = None
    fail_iteration = None
    completed = 0

    for idx in range(iterations):
        if limit is not None:
            elapsed = time.perf_counter() - started
            remaining = limit - elapsed
            if remaining <= 0:
                timed_out = True
                break
        else:
            remaining = None

        try:
            subprocess.run([executable], check=True, timeout=remaining)
            completed += 1
        except subprocess.TimeoutExpired:
            timed_out = True
            break
        except FileNotFoundError:
            failed = True
            fail_code = 127
            fail_iteration = idx + 1
            break
        except subprocess.CalledProcessError as exc:
            failed = True
            fail_code = exc.returncode
            fail_iteration = idx + 1
            break

    total_seconds = time.perf_counter() - started

    return BenchmarkResult(
        program=program,
        iterations=completed,
        total_seconds=total_seconds,
        timed_out=timed_out,
        failed=failed,
        fail_code=fail_code,
        fail_iteration=fail_iteration,
    )


def print_report(results: list[BenchmarkResult], requested_iterations: int) -> None:
    if not results:
        print("No benchmark results.")
        return

    completed_results = [r for r in results if not r.timed_out and not r.failed and r.iterations == requested_iterations]
    fastest = min((r.total_seconds for r in completed_results), default=None)

    header = (
        f"{'Program':<38} {'Done/Total':>10} {'Total(s)':>12} {'Avg(s)':>10} {'Ratio':>10} {'Status':>14}"
    )
    print("\nBenchmark results:")
    print(header)
    print("-" * len(header))

    for r in sorted(results, key=lambda x: x.total_seconds):
        avg = r.total_seconds / r.iterations if r.iterations > 0 else float("nan")

        if r.timed_out:
            status = "TIMEOUT"
        elif r.failed:
            status = f"FAILED({r.fail_code})"
        elif r.iterations < requested_iterations:
            status = "INCOMPLETE"
        else:
            status = "OK"

        if fastest is not None and status == "OK":
            ratio = f"{r.total_seconds / fastest:.2f}x"
        else:
            ratio = "-"

        print(
            f"{r.program:<38} "
            f"{r.iterations:>4}/{requested_iterations:<5} "
            f"{r.total_seconds:>12.6f} "
            f"{avg:>10.6f} "
            f"{ratio:>10} "
            f"{status:>14}"
        )


def main() -> int:
    args = parse_args()

    try:
        validate_inputs(args.programs, args.iterations, args.limit)
    except ValueError as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 2

    results: list[BenchmarkResult] = []
    for prog in args.programs:
        print(f"Running: {prog}")
        results.append(run_single_program(prog, args.iterations, args.limit))

    print_report(results, args.iterations)

    if any(r.timed_out or r.failed for r in results):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
