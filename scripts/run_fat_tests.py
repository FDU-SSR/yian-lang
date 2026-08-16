#!/usr/bin/env python3
"""Batch test runner for the YIAN fat-pointer verification suite (tests/fat/).

Discovers .an source files under tests/fat/ and compiles each one with the
standard library, reusing the discovery/parsing/execution logic from
run_tests.py. Expected outputs live in tests/output/fat/.

Usage:
    python3 scripts/run_fat_tests.py          # run all fat tests
    python3 scripts/run_fat_tests.py -v       # verbose: show failure details
    python3 scripts/run_fat_tests.py -q       # quiet: only the summary line
    python3 scripts/run_fat_tests.py -f uaf   # only run tests matching "uaf"
    python3 scripts/run_fat_tests.py --no-run # analysis only, no executables
"""

from __future__ import annotations

import sys
import time

# Reuse run_tests.py helpers so discovery/parsing semantics stay in sync.
from run_tests import (
    ROOT_DIR,
    TESTS_DIR,
    TestCase,
    TestResult,
    _find_ans,  # pyright: ignore[reportPrivateUsage]
    _find_input,  # pyright: ignore[reportPrivateUsage]
    _parse_ans,  # pyright: ignore[reportPrivateUsage]
    print_summary,
    run_test,
)

FAT_DIR = TESTS_DIR / "fat"
ALLOWED_TOP = ("positive", "negative", "compile_err")


def discover_fat_tests() -> list[TestCase]:
    """Walk tests/fat/ for .an files and build a TestCase for each."""
    tests: list[TestCase] = []
    for an_file in sorted(FAT_DIR.rglob("*.an")):
        rel = an_file.relative_to(FAT_DIR)
        if rel.parts[0] in ("output", "input"):
            continue
        if rel.parts[0] not in ALLOWED_TOP:
            continue

        src_rel = str(rel)

        fat_rel = f"fat/{src_rel}"
        is_err = src_rel.endswith(".err.an")

        ans_path = _find_ans(fat_rel)
        if ans_path is not None:
            expect_error, expected_exit_code, expected_output, expected_substr = _parse_ans(
                ans_path, is_error_test=is_err
            )
        else:
            expect_error = is_err
            expected_exit_code = None
            expected_output = ""
            expected_substr = ""

        cli_args, stdin = _find_input(fat_rel)

        tests.append(TestCase(
            name=src_rel,
            source_files=[an_file],
            expect_error=expect_error,
            expected_substring=expected_substr,
            expected_exit_code=expected_exit_code,
            expected_output=expected_output,
            cli_args=cli_args,
            stdin=stdin,
        ))
    return tests


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(
        description="Batch test runner for the YIAN fat-pointer suite.",
    )
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Show failure details for each failed test.",
    )
    parser.add_argument(
        "-q", "--quiet",
        action="store_true",
        help="Only print the summary line (no progress, no details).",
    )
    parser.add_argument(
        "-f", "--filter",
        metavar="SUBSTRING",
        dest="filter_str",
        default=None,
        help="Only run tests whose name contains SUBSTRING.",
    )
    parser.add_argument(
        "--no-run",
        action="store_true",
        help="Analysis only: do not compile and run executables.",
    )
    args = parser.parse_args(argv)

    all_tests = discover_fat_tests()

    if args.filter_str:
        all_tests = [t for t in all_tests if args.filter_str in t.name]
        if not all_tests:
            print(f"No tests match filter '{args.filter_str}'.")
            return 1

    print(f"Running {len(all_tests)} test(s)…\n")

    results: list[TestResult] = []
    total_start = time.monotonic()

    for i, test in enumerate(all_tests, 1):
        result = run_test(test, dump=False, run=not args.no_run)
        results.append(result)

        if not args.quiet:
            tag = "✓" if result.passed() else "✗"
            print(f"  [{i:>{len(str(len(all_tests)))}}/{len(all_tests)}] {tag} {test.name}  ({result.elapsed_ms:.0f} ms)")

    total_elapsed = (time.monotonic() - total_start) * 1000.0

    show_detail = args.verbose and not args.quiet
    print_summary(results, total_elapsed, show_detail, sys.stdout)

    failed = [r for r in results if not r.passed()]
    if failed:
        log_path = ROOT_DIR / "build" / "test_failures_fat.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with open(log_path, "w", encoding="utf-8") as log:
            log.write(f"Test run at {time.strftime('%Y-%m-%d %H:%M:%S')}\n\n")
            print_summary(results, total_elapsed, show_detail=True, out=log)
        print(f"\nFailure details written to {log_path}")

    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
