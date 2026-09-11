#!/usr/bin/env python3
"""Batch test runner for the YIAN compiler.

Discovers .an source files under tests/ and compiles each one with the
standard library. Tests without a corresponding .ans file in tests/output/
are treated as success tests (expected exit code 0, no output comparison).

Test inputs are discovered from tests/input/:
  - <name>.args  → CLI arguments passed to the executable (whitespace-separated)
  - <name>.stdin → content piped to the executable's stdin

Usage:
    python scripts/run_tests.py                  # run all tests
    python scripts/run_tests.py -v               # verbose: show failure details
    python scripts/run_tests.py -q               # quiet: only the summary line
    python scripts/run_tests.py -x               # include experimental/ tests
    python scripts/run_tests.py -f call          # only run tests matching "call"
"""

from __future__ import annotations

import subprocess
import sys
import time
from dataclasses import dataclass
from dataclasses import field
import json
from pathlib import Path
from typing import TextIO

ROOT_DIR = Path(__file__).resolve().parent.parent
TESTS_DIR = ROOT_DIR / "tests"
OUTPUT_DIR = TESTS_DIR / "output"
INPUT_DIR = TESTS_DIR / "input"
LIB_DIR = ROOT_DIR / "lib"
BUILD_DIR = ROOT_DIR / "build"

# ---------------------------------------------------------------------------
# Multi-file test configuration
# ---------------------------------------------------------------------------
# Test source file (relative to TESTS_DIR) → extra .an files to compile alongside.
EXTRA_SOURCES: dict[str, list[str]] = {
}

# Directories (relative to TESTS_DIR) whose .an files are compiled together.
MULTI_FILE_DIRS: set[str] = set()


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------

@dataclass
class CompileVariant:
    """A compiler-mode variant attached to an existing YIAN test."""

    name: str
    compiler_args: list[str]
    expected_substring: str | None = None
    expected_exit_code: int | None = None


@dataclass
class TestCase:
    """A single test case."""

    name: str
    """Human-readable name, e.g. 'call/variant_construct.an'."""

    source_files: list[Path]
    """.an files to compile (absolute paths)."""

    expect_error: bool
    """True if this test is expected to fail compilation."""

    expected_substring: str
    """For error tests: substring that should appear in the compiler output."""

    expected_exit_code: int | None = None
    """Expected runtime exit code (None = don't check, default 0)."""

    expected_output: str = ""
    """Expected stdout content (empty = no output check)."""

    cli_args: list[str] | None = None
    """CLI arguments to pass to the compiled executable."""

    stdin: str = ""
    """Content to pipe to the executable's stdin."""

    compiler_args: list[str] = field(default_factory=list)
    """Additional compiler arguments for this test or one of its variants."""

    compile_variants: list[CompileVariant] = field(default_factory=list)
    """Existing compiler-mode variants for this test."""


@dataclass
class TestResult:
    """Outcome of running a single test."""

    test: TestCase
    exit_code: int
    elapsed_ms: float
    output: str
    """Captured combined stdout+stderr (compiler output for analysis, or stderr for --run)."""

    stdout: str = ""
    """Captured stdout from running the compiled executable (--run mode only)."""

    check_stdout: bool = False
    """Whether to compare expected_output with captured stdout."""

    def passed(self) -> bool:
        if self.test.expect_error:
            if self.exit_code == 0:
                return False
            if self.test.expected_substring:
                return self.test.expected_substring in self.output
            return True
        if self.test.expected_exit_code is not None:
            if self.exit_code != self.test.expected_exit_code:
                return False
        elif self.exit_code != 0:
            return False
        if self.check_stdout and self.test.expected_output:
            expected = self.test.expected_output.strip()
            if self.stdout.strip() == expected:
                return True
            # Runtime errors (e.g. panics) are printed to stderr, which is
            # captured in self.output (compiler_output is empty on success).
            if self.output.strip() == expected:
                return True
            return False
        return True


# ---------------------------------------------------------------------------
# Test discovery
# ---------------------------------------------------------------------------

def _parse_ans(file_path: Path, is_error_test: bool = False) -> tuple[bool, int | None, str, str]:
    """Parse an .ans file into (expect_error, expected_exit_code, expected_output, expected_substring).

    Convention for .ans files:

    **Error tests** (.err.an) — the entire file is the expected compiler error
    substring.  Compilation must fail (any non-zero exit).  No ``Exit code``
    header is used because the exact compiler exit code is not checked.

    **Runtime tests** (normal .an) — the convention is:

    - If ``main()`` is expected to return **non-zero**, the first line MUST be
      ``Exit code <N>``.  The remainder (if any) is the expected combined
      stdout+stderr output.
    - If ``main()`` is expected to return **0**, the ``Exit code`` header is
      omitted and the entire file is the expected stdout output.
    """
    raw = file_path.read_text().rstrip("\n")
    if raw.startswith("Exit code "):
        nl = raw.find("\n")
        if nl == -1:
            exit_code = int(raw[len("Exit code "):].strip())
            rest = ""
        else:
            exit_code = int(raw[len("Exit code "):nl].strip())
            rest = raw[nl + 1:]
        if is_error_test:
            # Error tests: Exit code is informational only; passed() does not
            # check the exact value — any non-zero compiler exit is accepted.
            return True, None, "", rest
        return False, exit_code, rest, ""
    if is_error_test:
        return True, None, "", raw
    return False, 0, raw, ""


def _find_ans(test_rel: str) -> Path | None:
    """Look for a .ans file corresponding to *test_rel*.

    *test_rel* is relative to TESTS_DIR:
      - "call/variant_construct.an"         → …/call/variant_construct.an.ans
      - "error/circular_dep1.err.an"        → …/error/circular_dep1.an.ans
      - "import/func"  (multi-file dir)     → …/import/func.ans
    """
    if not test_rel.endswith(".an"):
        p = OUTPUT_DIR / (test_rel + ".ans")
        return p if p.exists() else None

    if test_rel.endswith(".err.an"):
        ans_name = test_rel[:-7] + ".an.ans"
    else:
        ans_name = test_rel + ".ans"

    p = OUTPUT_DIR / ans_name
    return p if p.exists() else None


def _find_input(test_rel: str) -> tuple[list[str] | None, str]:
    """Look for input files corresponding to *test_rel* in tests/input/.

    Returns ``(cli_args, stdin)`` where:
      - *cli_args* is a list of whitespace-split args (None if no .args file).
      - *stdin* is the content of the .stdin file (empty string if not found).

    *test_rel* follows the same naming convention as ``_find_ans``:
      - "env/args.an"            → …/env/args.args, …/env/args.stdin
      - "error/no_main.err.an"   → …/error/no_main.an.args  (strip .err)
    """
    base = test_rel
    if base.endswith(".err.an"):
        base = base[:-7] + ".an"

    args_path = INPUT_DIR / (base + ".args")
    cli_args: list[str] | None = None
    if args_path.exists():
        text = args_path.read_text().strip()
        cli_args = text.split() if text else []

    stdin = ""
    stdin_path = INPUT_DIR / (base + ".stdin")
    if stdin_path.exists():
        stdin = stdin_path.read_text()

    return cli_args, stdin


def _find_compile_config(test_rel: str) -> Path | None:
    """Find the optional compiler-variant metadata for a test."""
    path = INPUT_DIR / (test_rel + ".compile.json")
    return path if path.exists() else None


def _parse_compile_variants(file_path: Path) -> list[CompileVariant]:
    """Parse strict compiler-variant metadata from *file_path*."""
    raw = json.loads(file_path.read_text())
    if not isinstance(raw, list):
        raise ValueError(f"{file_path}: top level must be an array")

    variants: list[CompileVariant] = []
    names: set[str] = set()
    allowed = {
        "name",
        "compiler_args",
        "expect_error_substring",
        "expected_exit_code",
    }
    for index, item in enumerate(raw):
        if not isinstance(item, dict):
            raise ValueError(f"{file_path}: variant {index} must be an object")
        unknown = set(item) - allowed
        if unknown:
            names_text = ", ".join(sorted(str(name) for name in unknown))
            raise ValueError(f"{file_path}: variant {index} has unknown fields: {names_text}")

        name = item.get("name")
        if not isinstance(name, str) or not name:
            raise ValueError(f"{file_path}: variant {index} name must be a non-empty string")
        if name in names:
            raise ValueError(f"{file_path}: duplicate variant name: {name}")
        names.add(name)

        compiler_args = item.get("compiler_args")
        if not isinstance(compiler_args, list) or not all(
            isinstance(arg, str) for arg in compiler_args
        ):
            raise ValueError(
                f"{file_path}: variant {name} compiler_args must be a string array"
            )

        expected_substring = item.get("expect_error_substring")
        if expected_substring is not None and (
            not isinstance(expected_substring, str) or not expected_substring
        ):
            raise ValueError(
                f"{file_path}: variant {name} expect_error_substring must be a non-empty string"
            )

        expected_exit_code = item.get("expected_exit_code")
        if expected_exit_code is not None and (
            isinstance(expected_exit_code, bool) or not isinstance(expected_exit_code, int)
        ):
            raise ValueError(
                f"{file_path}: variant {name} expected_exit_code must be an integer"
            )
        if expected_substring is not None and expected_exit_code is not None:
            raise ValueError(
                f"{file_path}: variant {name} cannot set both compile-error and runtime expectations"
            )

        variants.append(
            CompileVariant(
                name=name,
                compiler_args=list(compiler_args),
                expected_substring=expected_substring,
                expected_exit_code=expected_exit_code,
            )
        )
    return variants


def _find_orphaned_compile_configs() -> list[str]:
    """Return compiler metadata files without a corresponding YIAN source."""
    orphaned: list[str] = []
    for config in sorted(INPUT_DIR.rglob("*.compile.json")):
        rel = config.relative_to(INPUT_DIR)
        source_rel = str(rel)[: -len(".compile.json")]
        if not (TESTS_DIR / source_rel).exists():
            orphaned.append(str(config))
    return orphaned


def discover_tests() -> list[TestCase]:
    """Walk tests/ for .an files and build a TestCase for each.

    Tests without a corresponding .ans file are treated as success tests
    (expected exit code 0, no output comparison).
    """

    helpers: set[str] = {h for v in EXTRA_SOURCES.values() for h in v}
    seen_multi: set[str] = set()
    tests: list[TestCase] = []
    orphaned: list[str] = []

    for an_file in sorted(TESTS_DIR.rglob("*.an")):
        rel = an_file.relative_to(TESTS_DIR)

        # Exclude tests/output/ and tests/input/.
        if rel.parts[0] in ("output", "input"):
            continue

        src_rel = str(rel)

        # Skip known helper files (e.g. private/private.an).
        if src_rel in helpers:
            continue

        # --- Multi-file directory test ---
        parent_rel = str(rel.parent)
        if parent_rel in MULTI_FILE_DIRS:
            if parent_rel in seen_multi:
                continue
            seen_multi.add(parent_rel)

            source_files = sorted(an_file.parent.glob("*.an"))
            name = parent_rel

        # --- Standalone test ---
        else:
            extra = EXTRA_SOURCES.get(src_rel, [])
            source_files = [an_file] + [TESTS_DIR / e for e in extra]
            name = src_rel

        # .err.an files (including those in multi-file dirs) are error tests.
        is_err = name.endswith(".err.an") or any(f.name.endswith(".err.an") for f in source_files)

        # Look up expected output.
        ans_path = _find_ans(name)
        if ans_path is not None:
            expect_error, expected_exit_code, expected_output, expected_substr = _parse_ans(ans_path, is_error_test=is_err)
        else:
            expect_error = is_err
            expected_exit_code = None
            expected_output = ""
            expected_substr = ""

        cli_args, stdin = _find_input(name)
        config_path = _find_compile_config(name)
        compile_variants = (
            _parse_compile_variants(config_path) if config_path is not None else []
        )

        tests.append(TestCase(
            name=name,
            source_files=source_files,
            expect_error=expect_error,
            expected_substring=expected_substr,
            expected_exit_code=expected_exit_code,
            expected_output=expected_output,
            cli_args=cli_args,
            stdin=stdin,
            compile_variants=compile_variants,
        ))

    # Report orphaned .ans files (no matching source).
    for ans_file in sorted(OUTPUT_DIR.rglob("*.ans")):
        ans_rel = ans_file.relative_to(OUTPUT_DIR)
        if ans_rel.name.endswith(".an.ans"):
            source_name = str(ans_rel.parent / ans_rel.name[:-4])
            if (TESTS_DIR / source_name).exists():
                continue
            err_name = source_name[:-3] + ".err.an"
            if (TESTS_DIR / err_name).exists():
                continue
        else:
            dir_name = str(ans_rel.parent / ans_rel.stem)
            if (TESTS_DIR / dir_name).is_dir():
                continue
        orphaned.append(str(ans_file))

    if orphaned:
        print("⚠  Orphaned .ans files (no matching source):")
        for m in orphaned:
            print(f"   {m}")
        print()

    orphaned_configs = _find_orphaned_compile_configs()
    if orphaned_configs:
        print("⚠  Orphaned compiler variant files (no matching source):")
        for config in orphaned_configs:
            print(f"   {config}")
        print()

    return tests


# ---------------------------------------------------------------------------
# Test execution
# ---------------------------------------------------------------------------

def run_test(test: TestCase, dump: bool = False, run: bool = False) -> TestResult:
    """Compile *test* and return the result.

    If *run* is True and the test is not an error test: compile to executable,
    run it (with CLI args and stdin from tests/input/ if present),
    and capture stdout + exit code.
    """

    cmd = [sys.executable, "-m", "compiler.main", str(LIB_DIR)]
    cmd += [str(f) for f in test.source_files]
    cmd += ["-O3"]
    cmd += test.compiler_args
    exe_path: Path | None = None
    if test.expect_error:
        cmd += ["-t", "none"]
    elif run:
        exe_path = BUILD_DIR / "test_exe" / test.name
        exe_path.parent.mkdir(parents=True, exist_ok=True)
        cmd += ["-t", "exe", "-o", str(exe_path)]
    else:
        cmd += ["-t", "none"]
    if dump:
        cmd += [
            "--token", str(BUILD_DIR / "token.txt"),
            "--ast", str(BUILD_DIR / "ast.txt"),
            "--hir", str(BUILD_DIR / "hir.txt"),
            "--cfg", str(BUILD_DIR / "cfg.txt"),
        ]

    start = time.monotonic()
    proc = subprocess.run(
        cmd,
        cwd=ROOT_DIR,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    compile_elapsed = (time.monotonic() - start) * 1000.0

    compiler_output = proc.stdout + proc.stderr

    # If compilation failed, return immediately (for error tests this is expected).
    if proc.returncode != 0 or test.expect_error:
        return TestResult(
            test=test,
            exit_code=proc.returncode,
            elapsed_ms=compile_elapsed,
            output=compiler_output,
        )

    # --run mode: execute the compiled binary
    if run and not test.expect_error:
        assert exe_path is not None
        run_start = time.monotonic()
        exe_cmd = [str(exe_path)]
        if test.cli_args:
            exe_cmd += test.cli_args
        run_proc = subprocess.run(
            exe_cmd,
            cwd=ROOT_DIR,
            input=test.stdin or None,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        run_elapsed = (time.monotonic() - run_start) * 1000.0

        return TestResult(
            test=test,
            exit_code=run_proc.returncode,
            elapsed_ms=compile_elapsed + run_elapsed,
            output=compiler_output + run_proc.stderr,
            stdout=run_proc.stdout,
            check_stdout=True,
        )

    return TestResult(
        test=test,
        exit_code=proc.returncode,
        elapsed_ms=compile_elapsed,
        output=compiler_output,
    )


def _variant_test(test: TestCase, variant: CompileVariant) -> TestCase:
    """Create an executable test case for one existing compiler variant."""
    expect_error = test.expect_error
    expected_substring = test.expected_substring
    expected_exit_code = test.expected_exit_code
    if variant.expected_substring is not None:
        expect_error = True
        expected_substring = variant.expected_substring
        expected_exit_code = None
    elif variant.expected_exit_code is not None:
        if expect_error:
            raise ValueError(
                f"{test.name}: runtime exit override is invalid for a compile-error test"
            )
        expected_exit_code = variant.expected_exit_code

    return TestCase(
        name=f"{test.name}@{variant.name}",
        source_files=test.source_files,
        expect_error=expect_error,
        expected_substring=expected_substring,
        expected_exit_code=expected_exit_code,
        expected_output=test.expected_output,
        cli_args=test.cli_args,
        stdin=test.stdin,
        compiler_args=variant.compiler_args,
    )


def discover_python_tests() -> list[TestCase]:
    """Discover existing Python unit and IR checks under tests/unit."""
    tests: list[TestCase] = []
    for source in sorted((TESTS_DIR / "unit").rglob("test_*.py")):
        tests.append(
            TestCase(
                name=str(source.relative_to(TESTS_DIR)),
                source_files=[],
                expect_error=False,
                expected_substring="",
                expected_exit_code=0,
            )
        )
    return tests


def run_python_test(test: TestCase) -> TestResult:
    """Run one existing Python unit or IR check as a subprocess."""
    source = TESTS_DIR / test.name
    start = time.monotonic()
    proc = subprocess.run(
        [sys.executable, str(source)],
        cwd=ROOT_DIR,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    elapsed = (time.monotonic() - start) * 1000.0
    return TestResult(
        test=test,
        exit_code=proc.returncode,
        elapsed_ms=elapsed,
        output=proc.stdout + proc.stderr,
    )


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------

def print_summary(
    results: list[TestResult],
    total_elapsed_ms: float,
    show_detail: bool,
    out: TextIO,
) -> None:
    """Print test result summary to *out*.

    *show_detail* controls whether per-test failure details are printed.
    """
    passed = [r for r in results if r.passed()]
    failed = [r for r in results if not r.passed()]

    if show_detail:
        for r in results:
            if not r.passed():
                status = "PASS" if r.passed() else "FAIL"
                out.write(f"  [{status}] {r.test.name}  ({r.elapsed_ms:.0f} ms)\n")
                _print_failure_detail(r, out)

    out.write("\n")
    out.write(f"{'=' * 60}\n")
    out.write(f"  Total: {len(results)}")
    out.write(f"  |  Passed: {len(passed)}")
    out.write(f"  |  Failed: {len(failed)}")
    out.write(f"  |  Time: {total_elapsed_ms / 1000:.2f} s\n")
    out.write(f"{'=' * 60}\n")

    if failed:
        out.write("\nFAILURES:\n")
        for r in failed:
            out.write(f"  ✗ {r.test.name}\n")


def _print_failure_detail(r: TestResult, out: TextIO) -> None:
    """Print why a single test failed."""
    if r.test.expect_error:
        expect_kind = "error"
        expected_exit = "non-zero"
    elif r.test.expected_exit_code is not None:
        expect_kind = "success"
        expected_exit = str(r.test.expected_exit_code)
    else:
        expect_kind = "success"
        expected_exit = "0"
    out.write(f"      expected {expect_kind} (exit {expected_exit}), got exit code {r.exit_code}\n")

    if r.test.expected_output and r.stdout:
        out.write("      --- expected stdout ---\n")
        out.write(_indent(r.test.expected_output, "      "))
        out.write("      --- actual stdout ---\n")
        out.write(_indent(r.stdout, "      "))

    if r.test.expected_substring:
        out.write("      --- expected substring ---\n")
        out.write(_indent(r.test.expected_substring, "      "))
    if r.test.expect_error and r.exit_code == 0:
        out.write("      (compiler succeeded but was expected to fail)\n")
    elif not r.test.expect_error and r.exit_code != 0:
        out.write("      --- compiler output ---\n")
        out.write(_indent(r.output, "      "))
    elif r.test.expect_error and r.test.expected_substring and r.test.expected_substring not in r.output:
        out.write("      --- compiler output ---\n")
        out.write(_indent(r.output, "      "))
    out.write("\n")


def _indent(text: str, prefix: str) -> str:
    if not text:
        return f"{prefix}(empty)\n"
    return "".join(f"{prefix}{line}\n" for line in text.splitlines())


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(
        description="Batch test runner for the YIAN compiler.",
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
        "-x", "--experimental",
        action="store_true",
        dest="include_experimental",
        help="Include tests under tests/experimental/.",
    )
    parser.add_argument(
        "-f", "--filter",
        metavar="SUBSTRING",
        dest="filter_str",
        default=None,
        help="Only run tests whose name contains SUBSTRING.",
    )
    parser.add_argument(
        "--dump",
        action="store_true",
        help="Enable compiler intermediate output (token, AST, HIR, CFG).",
    )
    parser.add_argument(
        "--no-run",
        action="store_true",
        help="Analysis only: do not compile and run executables.",
    )
    args = parser.parse_args(argv)

    # Discover.
    all_tests = discover_tests()

    # By default, exclude experimental tests.
    if not args.include_experimental:
        all_tests = [t for t in all_tests if not t.name.startswith("experimental/")]

    expanded_tests: list[TestCase] = []
    for test in all_tests:
        expanded_tests.append(test)
        for variant in test.compile_variants:
            expanded_tests.append(_variant_test(test, variant))
    all_tests = expanded_tests

    python_tests = [] if args.no_run else discover_python_tests()
    all_tests += python_tests

    if args.filter_str:
        all_tests = [t for t in all_tests if args.filter_str in t.name]
        if not all_tests:
            print(f"No tests match filter '{args.filter_str}'.")
            return 1

    print(f"Running {len(all_tests)} test(s)…\n")

    # Run.
    results: list[TestResult] = []
    total_start = time.monotonic()

    for i, test in enumerate(all_tests, 1):
        if test.name.startswith("unit/"):
            result = run_python_test(test)
        else:
            result = run_test(test, dump=args.dump, run=not args.no_run)
        results.append(result)

        if not args.quiet:
            tag = "✓" if result.passed() else "✗"
            print(f"  [{i:>{len(str(len(all_tests)))}}/{len(all_tests)}] {tag} {test.name}  ({result.elapsed_ms:.0f} ms)")

    total_elapsed = (time.monotonic() - total_start) * 1000.0

    # Report.
    show_detail = args.verbose and not args.quiet
    print_summary(results, total_elapsed, show_detail, sys.stdout)

    # Write detailed failure log.
    failed = [r for r in results if not r.passed()]
    if failed:
        log_path = ROOT_DIR / "build" / "test_failures.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with open(log_path, "w", encoding="utf-8") as log:
            log.write(f"Test run at {time.strftime('%Y-%m-%d %H:%M:%S')}\n\n")
            print_summary(results, total_elapsed, show_detail=True, out=log)
        print(f"\nFailure details written to {log_path}")

    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
