#!/usr/bin/env python3
"""Three-state runner for the raw-pointer / slice-ref dual-mode suite.

The same programs must behave identically (exit 0) under three compilation
states, selected by ``--state`` (default: all three in sequence):

  fat     ``[]``                  default fat-pointer representation + checks
  raw     ``--raw-pointers``      bare 8-byte pointers, no checks
  nocheck ``--no-fat-checks``     fat representation, checks skipped

Test lists are explicit (no directory scan) so each state controls exactly
which cases apply:

  RAW_TESTS       must pass (exit 0) in every state.
  RAW_NEG_TESTS   must FAIL to compile in the raw state; the error output must
                  contain "from_raw_parts" (the t2 coerce guard) — enforced as
                  a hard assert via TestCase.expected_substring, not just a
                  non-zero exit.  In the fat state these are success tests
                  (compile + run, exit 0).
  FAT_ONLY        only run in the fat state (they rely on tiered coerce being
                  legal there, which raw mode deliberately rejects).

Expected-output files (.ans) are shared with the fat suite: the fat state
validated them, and the raw/nocheck states reuse the same expectation.

Usage:
    python3 scripts/run_raw_tests.py              # fat + raw + nocheck
    python3 scripts/run_raw_tests.py --state raw  # single state
    python3 scripts/run_raw_tests.py -v           # verbose: failure details
    python3 scripts/run_raw_tests.py -q           # quiet: summary only
    python3 scripts/run_raw_tests.py -f alloc     # only tests matching "alloc"
    python3 scripts/run_raw_tests.py --no-run     # analysis only
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

# --- Explicit test lists (paths relative to TESTS_DIR, without ".an") ---

# Must pass (exit 0) in every state: fat, raw and nocheck.
RAW_TESTS = [
    "fat/positive/raw_compat_alloc",
    "fat/positive/raw_compat_arith",
    "fat/positive/raw_compat_del",
    "fat/positive/raw_compat_field",
    "fat/positive/raw_compat_mixed",
    "std/raw_slice_ref",
    "std/tiered_ref_receiver",
    "std/del_slice_from_parts",
]

# Must FAIL to compile in the raw state with an error mentioning
# "from_raw_parts" (the raw coerce guard). In the fat state they are
# success tests (compile + run, exit 0, no output).
RAW_NEG_TESTS = [
    "std/raw_coerce_neg",
]

# Only run in the fat state: rely on the tiered coerce that raw mode
# deliberately rejects.
FAT_ONLY = [
    "fat/positive/tiered_types",
]

# --- States ---

FAT_ROUND = "fat"
RAW_ROUND = "raw"
NOCHECK_ROUND = "nocheck"

ROUND_ARGS: dict[str, list[str]] = {
    FAT_ROUND: [],
    RAW_ROUND: ["--raw-pointers"],
    NOCHECK_ROUND: ["--no-fat-checks"],
}

ROUND_ORDER = [FAT_ROUND, RAW_ROUND, NOCHECK_ROUND]

NEG_SUBSTRING = "from_raw_parts"


def _build_testcase(rel: str) -> TestCase:
    """Build a success TestCase from *rel* (relative to TESTS_DIR, no ".an")."""
    an_rel = rel + ".an"
    an_file = TESTS_DIR / an_rel
    if not an_file.exists():
        raise FileNotFoundError(f"test source not found: {an_file}")

    ans_path = _find_ans(an_rel)
    if ans_path is not None:
        expect_error, expected_exit_code, expected_output, expected_substr = _parse_ans(
            ans_path, is_error_test=False
        )
    else:
        expect_error = False
        expected_exit_code = None
        expected_output = ""
        expected_substr = ""

    cli_args, stdin = _find_input(an_rel)

    return TestCase(
        name=an_rel,
        source_files=[an_file],
        expect_error=expect_error,
        expected_substring=expected_substr,
        expected_exit_code=expected_exit_code,
        expected_output=expected_output,
        cli_args=cli_args,
        stdin=stdin,
    )


def _as_error_test(test: TestCase) -> TestCase:
    """Relabel *test* as a compile-error case that must report NEG_SUBSTRING.

    This is the hard assert: ``run_test`` marks the case failed unless the
    compiler fails AND its output contains ``from_raw_parts`` — an unrelated
    compile error (or a successful compile) can not pass.
    """
    return TestCase(
        name=test.name,
        source_files=test.source_files,
        expect_error=True,
        expected_substring=NEG_SUBSTRING,
        expected_exit_code=None,
        expected_output="",
        cli_args=test.cli_args,
        stdin=test.stdin,
    )


def _round_tests(state: str) -> list[TestCase]:
    """The TestCase list for one state (fat/raw/nocheck)."""
    if state == FAT_ROUND:
        rels = RAW_TESTS + RAW_NEG_TESTS + FAT_ONLY
        return [_build_testcase(rel) for rel in rels]
    if state == RAW_ROUND:
        return [_build_testcase(rel) for rel in RAW_TESTS] + [
            _as_error_test(_build_testcase(rel)) for rel in RAW_NEG_TESTS
        ]
    if state == NOCHECK_ROUND:
        return [_build_testcase(rel) for rel in RAW_TESTS]
    raise ValueError(f"unknown state: {state}")


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(
        description="Three-state runner for the raw-pointer dual-mode suite.",
    )
    parser.add_argument(
        "--state",
        choices=[FAT_ROUND, RAW_ROUND, NOCHECK_ROUND],
        default=None,
        help="Run only this state (default: all three).",
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

    states = [args.state] if args.state is not None else list(ROUND_ORDER)

    results: list[TestResult] = []
    total_start = time.monotonic()

    for state in states:
        tests = _round_tests(state)
        if args.filter_str:
            tests = [t for t in tests if args.filter_str in t.name]
        if not tests:
            continue

        state_args = ROUND_ARGS[state]
        print(f"\n=== state: {state}  (compiler args: {state_args or '(none)'}) ===")
        print(f"Running {len(tests)} test(s)…")

        for i, test in enumerate(tests, 1):
            result = run_test(test, dump=False, run=not args.no_run, extra_args=state_args)
            results.append(result)

            if not args.quiet:
                tag = "✓" if result.passed() else "✗"
                print(f"  [{i:>{len(str(len(tests)))}}/{len(tests)}] {tag} [{state}] {test.name}  ({result.elapsed_ms:.0f} ms)")

    total_elapsed = (time.monotonic() - total_start) * 1000.0

    show_detail = args.verbose and not args.quiet
    print_summary(results, total_elapsed, show_detail, sys.stdout)

    failed = [r for r in results if not r.passed()]
    if failed:
        log_path = ROOT_DIR / "build" / "test_failures_raw.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with open(log_path, "w", encoding="utf-8") as log:
            log.write(f"Test run at {time.strftime('%Y-%m-%d %H:%M:%S')}\n\n")
            print_summary(results, total_elapsed, show_detail=True, out=log)
        print(f"\nFailure details written to {log_path}")

    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
