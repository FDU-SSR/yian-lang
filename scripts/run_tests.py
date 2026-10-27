#!/usr/bin/env python3
"""Run the YIAN test suites with the installed native compiler.

The runner requires native tools to be installed already and never builds or
refreshes the toolchain:

- ``basic``   — standalone compilation of one or more .an files in the chosen
  fat, raw, or both pointer modes (default: both).
- ``safety``  — fat-pointer safety regressions; raw results are explicitly N/A.
- ``package`` — package-mode fixtures (a ``package.anx`` project directory),
  driven through ``anx build`` / ``anx check``.
- ``bootstrap`` — existing package tests for the bootstrap compiler, JSON,
  TOML, JSON-RPC, and LSP protocol packages, each in fat and raw pointer modes.

Tests without a corresponding .ans file under ``tests/output/<suite>`` are
treated as success tests (expected exit code 0, no output comparison).

Expected results for .an sources use ``<name>.an.ans`` (``<name>.ans`` for
multi-file directories, where the whole directory is one test). Package
fixtures are directories too: ``<dir>.ans`` for success and ``<dir>.err.ans``
for an expected failure whose content is a diagnostic substring.

Test inputs are discovered from ``tests/input/<suite>``:
  - <name>.args  → CLI arguments passed to the executable (whitespace-separated)
  - <name>.stdin → content piped to the executable's stdin
  - <name>.compile.json → optional compiler variants and pointer-mode applicability.
    A top-level ``compiler_args`` array supplies arguments for every selected mode;
    a compile-error test whose arguments include ``-t``/``--target`` is compiled
    with that target instead of ``--check``, so entry-point errors can be tested.

Usage:
    python scripts/run_tests.py                  # basic suite, fat + raw
    python scripts/run_tests.py --suite safety   # fat checks; raw is N/A
    python scripts/run_tests.py --pointer-mode raw --suite basic
    python scripts/run_tests.py --all            # all suites
    python scripts/run_tests.py --implementation native --suite basic
    python scripts/run_tests.py -v               # verbose: show failure details
    python scripts/run_tests.py -q               # quiet: only the summary line
    python scripts/run_tests.py -x               # include <suite>/experimental/
    python scripts/run_tests.py -f call          # only run tests matching "call"
"""

from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
import sys
import time
from dataclasses import dataclass
from dataclasses import field
from dataclasses import replace
from pathlib import Path
from typing import TextIO, cast

from _toolchain.native import NativeToolError, resolve_native

ROOT_DIR = Path(__file__).resolve().parent.parent
TESTS_DIR = ROOT_DIR / "tests"

SUITES = ("basic", "safety", "package", "bootstrap")
BOOTSTRAP_PACKAGES = (
    "yianc",
    "bootstrap/json",
    "bootstrap/toml",
    "bootstrap/jsonrpc",
    "bootstrap/lsp_protocol",
)
_suite_name = "basic"
_source_dir = TESTS_DIR / _suite_name
_output_dir = TESTS_DIR / "output" / _suite_name
_input_dir = TESTS_DIR / "input" / _suite_name
LIB_DIR = ROOT_DIR / "lib" / "src"
BUILD_DIR = ROOT_DIR / "build"


@dataclass(frozen=True)
class Toolchain:
    """One selected compiler and the shared native package manager."""

    name: str
    compiler: tuple[str, ...]
    anx: tuple[str, ...]
    environment: dict[str, str]

# Directories (relative to _source_dir) whose .an files form one test case.
MULTI_FILE_DIRS: set[str] = {"try/cross_module", "trait/qualified_private", "type/alias/imported_scope"}


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


def _empty_string_list() -> list[str]:
    return []


def _empty_compile_variant_list() -> list[CompileVariant]:
    return []


@dataclass
class TestCase:
    """A single test case."""

    name: str
    """Human-readable name, e.g. 'call/func.an'."""

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

    stdin: bytes = b""
    """Bytes to pipe to the executable's stdin without decoding or newline translation."""

    compiler_args: list[str] = field(default_factory=_empty_string_list)
    """Additional compiler arguments for this test or one of its variants."""

    compile_variants: list[CompileVariant] = field(default_factory=_empty_compile_variant_list)
    """Existing compiler-mode variants for this test."""

    pointer_modes: tuple[str, ...] | None = None
    """Modes allowed by a per-test configuration; None uses the suite default."""

    raw_inapplicable_reason: str | None = None
    """Reason raw mode is not selected for this test, when configured."""

    package_root: Path | None = None
    """For package-suite fixtures: the project directory driven through anx."""

    anx_command: str | None = None
    """For package fixtures: the anx subcommand to drive (default build/check)."""

    anx_args: list[str] = field(default_factory=_empty_string_list)
    """For package fixtures: extra anx flags, e.g. --raw-pointers."""

    not_applicable_reason: str | None = None
    """Why this test does not run in one selected pointer mode, if applicable."""


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

    compile_only: bool = False
    """Whether this result came from ``--no-run`` analysis-only execution."""

    not_applicable_reason: str | None = None
    """Why this selected pointer mode does not run this test, if applicable."""

    def passed(self) -> bool:
        if self.not_applicable_reason is not None:
            return False
        if self.test.expect_error:
            if self.exit_code == 0:
                return False
            if self.test.expected_substring:
                return self.test.expected_substring in self.output
            return True
        if self.compile_only:
            return self.exit_code == 0
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

def __parse_ans(file_path: Path, is_error_test: bool = False) -> tuple[bool, int | None, str, str]:
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


def __find_ans(test_rel: str) -> Path | None:
    """Look for a .ans file corresponding to *test_rel*.

    *test_rel* is relative to _source_dir:
      - "call/func.an"                      → …/call/func.an.ans
      - "error/circular_dep1.err.an"        → …/error/circular_dep1.an.ans
      - "import/func"  (multi-file dir)     → …/import/func.ans
    """
    if not test_rel.endswith(".an"):
        p = _output_dir / (test_rel + ".ans")
        return p if p.exists() else None

    if test_rel.endswith(".err.an"):
        ans_name = test_rel[:-7] + ".an.ans"
    else:
        ans_name = test_rel + ".ans"

    p = _output_dir / ans_name
    return p if p.exists() else None


def __find_input(test_rel: str) -> tuple[list[str] | None, bytes]:
    """Look for input files corresponding to *test_rel* in the suite input directory.

    Returns ``(cli_args, stdin)`` where:
      - *cli_args* is a list of whitespace-split args (None if no .args file).
      - *stdin* is the exact .stdin file content (empty bytes if not found).

    *test_rel* follows the same naming convention as ``__find_ans``:
      - "env/args.an"            → …/env/args.args, …/env/args.stdin
      - "error/no_main.err.an"   → …/error/no_main.an.args  (strip .err)
    """
    base = test_rel
    if base.endswith(".err.an"):
        base = base[:-7] + ".an"

    args_path = _input_dir / (base + ".args")
    cli_args: list[str] | None = None
    if args_path.exists():
        text = args_path.read_text().strip()
        cli_args = text.split() if text else []

    stdin = b""
    stdin_path = _input_dir / (base + ".stdin")
    if stdin_path.exists():
        stdin = stdin_path.read_bytes()

    return cli_args, stdin


def __find_compile_config(test_rel: str) -> Path | None:
    """Find the optional compiler-variant metadata for a test."""
    path = _input_dir / (test_rel + ".compile.json")
    return path if path.exists() else None


def __parse_compile_config(
    file_path: Path,
) -> tuple[list[CompileVariant], tuple[str, ...] | None, str | None]:
    """Parse compile variants and optional pointer-mode applicability metadata."""
    raw: object = json.loads(file_path.read_text())
    pointer_modes: tuple[str, ...] | None = None
    raw_reason: str | None = None
    if isinstance(raw, list):
        entries = cast(list[object], raw)
    elif isinstance(raw, dict):
        config = cast(dict[str, object], raw)
        allowed_config = {"variants", "pointer_modes", "compiler_args", "raw_inapplicable_reason"}
        unknown_config = set(config) - allowed_config
        if unknown_config:
            names_text = ", ".join(sorted(str(name) for name in unknown_config))
            raise ValueError(f"{file_path}: unknown configuration fields: {names_text}")
        variants_value = config.get("variants", [])
        if not isinstance(variants_value, list):
            raise ValueError(f"{file_path}: variants must be an array")
        entries = cast(list[object], variants_value)

        modes_value = config.get("pointer_modes")
        if "pointer_modes" in config:
            if not isinstance(modes_value, list) or not modes_value:
                raise ValueError(f"{file_path}: pointer_modes must be a non-empty array")
            if not all(isinstance(mode, str) for mode in cast(list[object], modes_value)):
                raise ValueError(f"{file_path}: pointer_modes must contain only strings")
            modes = cast(list[str], modes_value)
            if any(mode not in {"fat", "raw"} for mode in modes):
                raise ValueError(f"{file_path}: pointer_modes entries must be fat or raw")
            if len(set(modes)) != len(modes):
                raise ValueError(f"{file_path}: pointer_modes entries must be unique")
            if "fat" not in modes:
                raise ValueError(f"{file_path}: pointer_modes must include fat")
            pointer_modes = tuple(modes)

        reason_value = config.get("raw_inapplicable_reason")
        if "raw_inapplicable_reason" in config:
            if not isinstance(reason_value, str) or not reason_value.strip():
                raise ValueError(f"{file_path}: raw_inapplicable_reason must be non-empty text")
            raw_reason = reason_value.strip()
        if pointer_modes is None and raw_reason is not None:
            raise ValueError(
                f"{file_path}: raw_inapplicable_reason requires pointer_modes"
            )
        if pointer_modes is not None:
            raw_allowed = "raw" in pointer_modes
            if not raw_allowed and raw_reason is None:
                raise ValueError(
                    f"{file_path}: excluding raw requires raw_inapplicable_reason"
                )
            if raw_allowed and raw_reason is not None:
                raise ValueError(
                    f"{file_path}: raw_inapplicable_reason conflicts with allowing raw"
                )
    else:
        raise ValueError(f"{file_path}: top level must be an array or object")

    variants: list[CompileVariant] = []
    names: set[str] = set()
    allowed = {
        "name",
        "compiler_args",
        "expect_error_substring",
        "expected_exit_code",
    }
    for index, item in enumerate(entries):
        if not isinstance(item, dict):
            raise ValueError(f"{file_path}: variant {index} must be an object")
        variant = cast(dict[str, object], item)
        unknown = set(variant) - allowed
        if unknown:
            names_text = ", ".join(sorted(str(name) for name in unknown))
            raise ValueError(f"{file_path}: variant {index} has unknown fields: {names_text}")

        name = variant.get("name")
        if not isinstance(name, str) or not name:
            raise ValueError(f"{file_path}: variant {index} name must be a non-empty string")
        if name in names:
            raise ValueError(f"{file_path}: duplicate variant name: {name}")
        names.add(name)

        compiler_args = variant.get("compiler_args")
        if not isinstance(compiler_args, list) or not all(
            isinstance(arg, str) for arg in cast(list[object], compiler_args)
        ):
            raise ValueError(
                f"{file_path}: variant {name} compiler_args must be a string array"
            )

        expected_substring = variant.get("expect_error_substring")
        if expected_substring is not None and (
            not isinstance(expected_substring, str) or not expected_substring
        ):
            raise ValueError(
                f"{file_path}: variant {name} expect_error_substring must be a non-empty string"
            )

        expected_exit_code = variant.get("expected_exit_code")
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
                compiler_args=cast(list[str], compiler_args),
                expected_substring=expected_substring,
                expected_exit_code=expected_exit_code,
            )
        )
    return variants, pointer_modes, raw_reason


def __base_compiler_args(file_path: Path) -> list[str]:
    """Return test-level compiler arguments stored in a compile configuration."""
    raw: object = json.loads(file_path.read_text())
    if not isinstance(raw, dict):
        return []
    value = raw.get("compiler_args", [])
    if not isinstance(value, list) or not all(isinstance(arg, str) for arg in cast(list[object], value)):
        raise ValueError(f"{file_path}: compiler_args must be a string array")
    return cast(list[str], value)


def __parse_compile_variants(file_path: Path) -> list[CompileVariant]:
    """Parse the compile variants, preserving the legacy array helper API."""
    return __parse_compile_config(file_path)[0]


def __find_orphaned_compile_configs() -> list[str]:
    """Return compiler metadata files without a corresponding YIAN source."""
    orphaned: list[str] = []
    for config in sorted(_input_dir.rglob("*.compile.json")):
        rel = config.relative_to(_input_dir)
        source_rel = str(rel)[: -len(".compile.json")]
        if not (_source_dir / source_rel).exists():
            orphaned.append(str(config))
    return orphaned


def discover_tests() -> list[TestCase]:
    """Discover the active suite's test cases.

    Package fixtures are project directories and are discovered separately;
    every other suite is walked for .an files. Tests without a corresponding
    .ans file are treated as success tests (expected exit code 0, no output
    comparison).
    """

    if _suite_name == "package":
        return discover_package_tests()

    seen_multi: set[str] = set()
    tests: list[TestCase] = []
    orphaned: list[str] = []

    for an_file in sorted(_source_dir.rglob("*.an")):
        rel = an_file.relative_to(_source_dir)

        src_rel = str(rel)

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
            source_files = [an_file]
            name = src_rel

        # .err.an files (including those in multi-file dirs) are error tests.
        is_err = name.endswith(".err.an") or any(f.name.endswith(".err.an") for f in source_files)

        # Look up expected output.
        ans_path = __find_ans(name)
        if ans_path is not None:
            expect_error, expected_exit_code, expected_output, expected_substr = __parse_ans(ans_path, is_error_test=is_err)
        else:
            expect_error = is_err
            expected_exit_code = None
            expected_output = ""
            expected_substr = ""

        cli_args, stdin = __find_input(name)
        config_path = __find_compile_config(name)
        if config_path is not None:
            compile_variants, pointer_modes, raw_reason = __parse_compile_config(config_path)
            base_compiler_args = __base_compiler_args(config_path)
        else:
            compile_variants, pointer_modes, raw_reason = [], None, None
            base_compiler_args = []

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
            compiler_args=base_compiler_args,
            pointer_modes=pointer_modes,
            raw_inapplicable_reason=raw_reason,
        ))

    # Report orphaned .ans files (no matching source).
    for ans_file in sorted(_output_dir.rglob("*.ans")):
        ans_rel = ans_file.relative_to(_output_dir)
        if ans_rel.name.endswith(".an.ans"):
            source_name = str(ans_rel.parent / ans_rel.name[:-4])
            if (_source_dir / source_name).exists():
                continue
            err_name = source_name[:-3] + ".err.an"
            if (_source_dir / err_name).exists():
                continue
        else:
            dir_name = str(ans_rel.parent / ans_rel.stem)
            if (_source_dir / dir_name).is_dir():
                continue
        orphaned.append(str(ans_file))

    if orphaned:
        print("⚠  Orphaned .ans files (no matching source):")
        for m in orphaned:
            print(f"   {m}")
        print()

    orphaned_configs = __find_orphaned_compile_configs()
    if orphaned_configs:
        print("⚠  Orphaned compiler variant files (no matching source):")
        for config in orphaned_configs:
            print(f"   {config}")
        print()

    return tests


def __find_package_ans(fixture: str) -> tuple[Path | None, bool]:
    """Expected-result file for a package fixture, given its directory name.

    Directory fixtures use ``<dir>.err.ans`` for an expected failure (content
    is a diagnostic substring) and ``<dir>.ans`` otherwise. Returns
    ``(path, is_error)``; ``(None, False)`` when the fixture ships no
    expectation, which means "build, run, expect exit 0, ignore stdout".
    """
    err = _output_dir / (fixture + ".err.ans")
    if err.exists():
        return err, True
    ok = _output_dir / (fixture + ".ans")
    if ok.exists():
        return ok, False
    return None, False


def __find_package_command(fixture: str) -> list[str] | None:
    """Optional ``tests/input/package/<fixture>.command`` override.

    The file holds the anx command line to drive the fixture with, for example
    ``build``, ``check``, ``test`` or ``graph --json``. Fixtures without it
    build and run, as before.
    """
    path = _input_dir / (fixture + ".command")
    if not path.is_file():
        return None
    tokens = path.read_text().split()
    if not tokens or tokens[0] not in {"build", "check", "test", "graph"}:
        raise ValueError(f"{path}: unsupported anx command {' '.join(tokens)!r}")
    return tokens


def discover_package_tests() -> list[TestCase]:
    """Discover package-mode fixtures: directories holding a ``package.anx``.

    A fixture is a case when it is not nested inside another case and either
    defines an entry (``src/main.an``) or ships an expected-result file. Its
    path dependencies usually have neither, so they are skipped; the nested
    check covers the rest.
    """
    cases: list[Path] = []
    tests: list[TestCase] = []

    for manifest in sorted(_source_dir.rglob("package.anx")):
        base = manifest.parent
        if any(parent in base.parents for parent in cases):
            continue

        name = str(base.relative_to(_source_dir))
        ans_path, ans_is_error = __find_package_ans(name)
        command_tokens = __find_package_command(name)
        anx_command = command_tokens[0] if command_tokens is not None else None
        # A dependency package has no entry, no expectation and no command of
        # its own, so it is not a case; anything else is.
        if (
            not (base / "src" / "main.an").exists()
            and ans_path is None
            and anx_command is None
        ):
            continue
        cases.append(base)

        if ans_path is not None:
            expect_error, expected_exit_code, expected_output, expected_substr = __parse_ans(
                ans_path, is_error_test=ans_is_error
            )
        else:
            expect_error, expected_exit_code, expected_output, expected_substr = False, None, "", ""

        cli_args, stdin = __find_input(name)
        tests.append(TestCase(
            name=name,
            source_files=sorted((base / "src").rglob("*.an")),
            expect_error=expect_error,
            expected_substring=expected_substr,
            expected_exit_code=expected_exit_code,
            expected_output=expected_output,
            cli_args=cli_args,
            stdin=stdin,
            package_root=base,
            anx_command=anx_command,
            anx_args=command_tokens[1:] if command_tokens is not None else [],
        ))

    orphans: list[str] = []
    for ans_file in sorted(_output_dir.rglob("*.ans")):
        rel = ans_file.relative_to(_output_dir)
        suffix = ".err.ans" if rel.name.endswith(".err.ans") else ".ans"
        fixture = str(rel.parent / rel.name[: -len(suffix)])
        if not (_source_dir / fixture / "package.anx").exists():
            orphans.append(str(ans_file))
    if orphans:
        print("⚠  Orphaned package expectations (no matching fixture):")
        for orphan in orphans:
            print(f"   {orphan}")
        print()

    return tests


# ---------------------------------------------------------------------------
# Test execution
# ---------------------------------------------------------------------------

def run_test(
    test: TestCase,
    toolchain: Toolchain,
    dump: bool = False,
    run: bool = False,
    compile_only: bool = False,
) -> TestResult:
    """Compile *test* and return the result.

    If *run* is True and the test is not an error test: compile to executable,
    run it (with CLI args and stdin from tests/input/ if present),
    and capture stdout + exit code.
    """

    if test.package_root is not None:
        return run_package_test(test, toolchain, run=run, compile_only=compile_only)

    cmd = [*toolchain.compiler, str(LIB_DIR)]
    cmd += [str(f) for f in test.source_files]
    cmd += ["-O3"]
    cmd += test.compiler_args
    exe_path: Path | None = None
    if test.expect_error:
        has_target = any(
            arg in {"-t", "--target"} or arg.startswith("--target=")
            for arg in test.compiler_args
        )
        if not has_target:
            cmd += ["--check"]
    elif run:
        exe_path = BUILD_DIR / "test_exe" / _suite_name / test.name
        exe_path.parent.mkdir(parents=True, exist_ok=True)
        cmd += ["-t", "exe", "-o", str(exe_path)]
    else:
        cmd += ["--check"]
    if dump:
        cmd.append("--dump")

    start = time.monotonic()
    proc = subprocess.run(
        cmd,
        cwd=ROOT_DIR,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        env=toolchain.environment,
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
            compile_only=compile_only,
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
            input=test.stdin,
            capture_output=True,
            check=False,
            env=toolchain.environment,
        )
        run_elapsed = (time.monotonic() - run_start) * 1000.0

        return TestResult(
            test=test,
            exit_code=run_proc.returncode,
            elapsed_ms=compile_elapsed + run_elapsed,
            output=compiler_output + __decode_output(run_proc.stderr),
            stdout=__decode_output(run_proc.stdout),
            check_stdout=True,
        )

    return TestResult(
        test=test,
        exit_code=proc.returncode,
        elapsed_ms=compile_elapsed,
        output=compiler_output,
        compile_only=compile_only,
    )


def __decode_output(data: bytes) -> str:
    """Decode captured output with the text-mode subprocess newline convention."""
    return data.decode("utf-8", errors="replace").replace("\r\n", "\n").replace("\r", "\n")


def run_package_test(
    test: TestCase,
    toolchain: Toolchain,
    run: bool,
    compile_only: bool,
) -> TestResult:
    """Drive a package fixture through native anx, then run its executable.

    The subcommand defaults to ``build`` (or ``check`` under ``--no-run``); a
    fixture may override it — for example a ``lib`` root can only be checked,
    and a fixture with project tests is driven through ``anx test``. Only a
    ``build`` fixture produces an executable to run.
    """
    assert test.package_root is not None
    subcommand = test.anx_command or ("check" if compile_only else "build")
    cmd = [*toolchain.anx, subcommand, str(test.package_root), *test.anx_args]

    start = time.monotonic()
    proc = subprocess.run(
        cmd,
        cwd=ROOT_DIR,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        env=toolchain.environment,
    )
    compile_elapsed = (time.monotonic() - start) * 1000.0
    output = proc.stdout + proc.stderr
    produces_exe = subcommand == "build"

    if proc.returncode != 0 or test.expect_error:
        return TestResult(
            test=test,
            exit_code=proc.returncode,
            elapsed_ms=compile_elapsed,
            output=output,
            compile_only=compile_only or not produces_exe,
        )

    if not run or not produces_exe:
        return TestResult(
            test=test,
            exit_code=proc.returncode,
            elapsed_ms=compile_elapsed,
            output=output,
            compile_only=True,
        )

    exe_cmd = [str(test.package_root / "build" / "app")]
    if test.cli_args:
        exe_cmd += test.cli_args
    run_start = time.monotonic()
    run_proc = subprocess.run(
        exe_cmd,
        cwd=ROOT_DIR,
        input=test.stdin,
        capture_output=True,
        check=False,
        env=toolchain.environment,
    )
    run_elapsed = (time.monotonic() - run_start) * 1000.0

    return TestResult(
        test=test,
        exit_code=run_proc.returncode,
        elapsed_ms=compile_elapsed + run_elapsed,
        output=output + __decode_output(run_proc.stderr),
        stdout=__decode_output(run_proc.stdout),
        check_stdout=True,
    )


def __mode_test(test: TestCase, mode: str) -> TestCase:
    """Create the fat or raw execution of a basic-suite test.

    The ordinary suite has one source and one expected result for both pointer
    representations. The retained ``raw_coerce_neg`` metadata is the one
    intentional exception: its raw variant expects a compile-time diagnostic.
    Other compile variants are ignored by the two-mode runner.
    """
    if mode not in {"fat", "raw"}:
        raise ValueError(f"unsupported pointer mode: {mode}")

    variant: CompileVariant | None = None
    if mode == "raw":
        variant = next(
            (candidate for candidate in test.compile_variants if candidate.name == "raw"),
            None,
        )

    expect_error = test.expect_error
    expected_substring = test.expected_substring
    expected_exit_code = test.expected_exit_code
    if variant is not None:
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

    compiler_args = list(test.compiler_args)
    if mode == "raw":
        if variant is not None:
            compiler_args.extend(variant.compiler_args)
        elif "--raw-pointers" not in compiler_args:
            compiler_args.append("--raw-pointers")

    return TestCase(
        name=f"{test.name}@{mode}",
        source_files=test.source_files,
        expect_error=expect_error,
        expected_substring=expected_substring,
        expected_exit_code=expected_exit_code,
        expected_output=test.expected_output,
        cli_args=test.cli_args,
        stdin=test.stdin,
        compiler_args=compiler_args,
    )


def __package_mode_test(test: TestCase, mode: str) -> TestCase:
    """Create the fat or raw execution of a package fixture.

    Package fixtures go through ``anx``, so the pointer representation is a flag
    on the anx command rather than a compiler argument.
    """
    if mode not in {"fat", "raw"}:
        raise ValueError(f"unsupported pointer mode: {mode}")
    anx_args = list(test.anx_args)
    if test.anx_command == "test":
        if "--raw-pointers" in anx_args:
            raise ValueError(
                f"{test.name}: package test fixtures must not set --raw-pointers; "
                "the runner selects one explicit --pointer-mode"
            )
        anx_args.extend(("--pointer-mode", mode))
    elif mode == "raw" and "--raw-pointers" not in anx_args:
        anx_args.append("--raw-pointers")
    return replace(test, name=f"{test.name}@{mode}", anx_args=anx_args)


def __selected_modes(pointer_mode: str) -> tuple[str, ...]:
    if pointer_mode == "both":
        return ("fat", "raw")
    if pointer_mode in {"fat", "raw"}:
        return (pointer_mode,)
    raise ValueError(f"unsupported pointer mode: {pointer_mode}")


def __not_applicable_test(
    test: TestCase, mode: str, reason: str
) -> TestCase:
    return replace(
        test,
        name=f"{test.name}@{mode}",
        not_applicable_reason=reason,
    )


def __test_modes(
    test: TestCase, pointer_mode: str, suite: str
) -> tuple[tuple[str, bool, str | None], ...]:
    selected = __selected_modes(pointer_mode)
    allowed = test.pointer_modes
    default_reason: str | None = None
    if allowed is None:
        if suite == "safety":
            allowed = ("fat",)
            default_reason = (
                "this suite checks fat-pointer runtime safety; raw pointers do not "
                "provide those checks"
            )
        else:
            allowed = ("fat", "raw")

    raw_reason = test.raw_inapplicable_reason or default_reason
    if "raw" in allowed and test.raw_inapplicable_reason is not None:
        raise ValueError(f"{test.name}: raw_inapplicable_reason conflicts with pointer_modes")
    if "raw" not in allowed and raw_reason is None:
        raise ValueError(f"{test.name}: excluding raw requires a reason")

    modes: list[tuple[str, bool, str | None]] = []
    for mode in selected:
        if mode in allowed:
            modes.append((mode, True, None))
        elif mode == "raw":
            modes.append((mode, False, raw_reason))
        else:
            raise ValueError(f"{test.name}: pointer mode {mode} is not available")
    return tuple(modes)


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
    not_applicable = [r for r in results if r.not_applicable_reason is not None]
    passed = [
        r for r in results
        if r.not_applicable_reason is None and r.passed()
    ]
    failed = [
        r for r in results
        if r.not_applicable_reason is None and not r.passed()
    ]

    if show_detail:
        for r in results:
            if r.not_applicable_reason is not None:
                out.write(f"  [N/A] {r.test.name}: {r.not_applicable_reason}\n")
                continue
            if not r.passed():
                out.write(f"  [FAIL] {r.test.name}  ({r.elapsed_ms:.0f} ms)\n")
                __print_failure_detail(r, out)

    out.write("\n")
    out.write(f"{'=' * 60}\n")
    out.write(f"  Total: {len(results)}")
    out.write(f"  |  Passed: {len(passed)}")
    out.write(f"  |  Failed: {len(failed)}")
    out.write(f"  |  N/A: {len(not_applicable)}")
    out.write(f"  |  Time: {total_elapsed_ms / 1000:.2f} s\n")
    out.write(f"{'=' * 60}\n")

    for reason in sorted({r.not_applicable_reason for r in not_applicable if r.not_applicable_reason}):
        out.write(f"  N/A reason: {reason}\n")

    if failed:
        out.write("\nFAILURES:\n")
        for r in failed:
            out.write(f"  ✗ {r.test.name}\n")


def __print_failure_detail(r: TestResult, out: TextIO) -> None:
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
        out.write(__indent(r.test.expected_output, "      "))
        out.write("      --- actual stdout ---\n")
        out.write(__indent(r.stdout, "      "))

    if r.test.expected_substring:
        out.write("      --- expected substring ---\n")
        out.write(__indent(r.test.expected_substring, "      "))
    if r.test.expect_error and r.exit_code == 0:
        out.write("      (compiler succeeded but was expected to fail)\n")
    elif not r.test.expect_error and r.exit_code != 0:
        out.write("      --- compiler output ---\n")
        out.write(__indent(r.output, "      "))
    elif r.test.expect_error and r.test.expected_substring and r.test.expected_substring not in r.output:
        out.write("      --- compiler output ---\n")
        out.write(__indent(r.output, "      "))
    out.write("\n")


def __indent(text: str, prefix: str) -> str:
    if not text:
        return f"{prefix}(empty)\n"
    return "".join(f"{prefix}{line}\n" for line in text.splitlines())


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def __run_bootstrap_suite(args: argparse.Namespace, toolchain: Toolchain) -> int:
    """Run the existing bootstrap package tests through the shared native anx."""
    selected: list[TestCase] = []
    command = "check" if args.no_run else "test"
    for package in BOOTSTRAP_PACKAGES:
        for mode in __selected_modes(args.pointer_mode):
            name = f"{package}@{mode}"
            if args.filter_str and args.filter_str not in name:
                continue
            selected.append(TestCase(
                name=name,
                source_files=[],
                expect_error=False,
                expected_substring="",
                package_root=ROOT_DIR / package,
                anx_command=command,
                anx_args=(
                    ["--pointer-mode", mode]
                    if command == "test"
                    else (["--raw-pointers"] if mode == "raw" else [])
                ),
            ))

    if not selected:
        print(f"No bootstrap package tests match filter '{args.filter_str or ''}'.")
        return 1

    print(f"Running {len(selected)} bootstrap package test(s) [{toolchain.name}]…\n")
    results: list[TestResult] = []
    total_start = time.monotonic()
    for index, test in enumerate(selected, 1):
        result = run_package_test(
            test,
            toolchain,
            run=not args.no_run,
            compile_only=args.no_run,
        )
        results.append(result)
        if not args.quiet:
            marker = "✓" if result.passed() else "✗"
            print(f"  [{index:>{len(str(len(selected)))}}/{len(selected)}] {marker} {test.name}  ({result.elapsed_ms:.0f} ms)")

    total_elapsed = (time.monotonic() - total_start) * 1000.0
    show_detail = args.verbose and not args.quiet
    print_summary(results, total_elapsed, show_detail, sys.stdout)
    failed = [
        result for result in results
        if result.not_applicable_reason is None and not result.passed()
    ]
    if failed:
        log_path = ROOT_DIR / "build" / "test_failures_bootstrap" / f"{toolchain.name}.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with open(log_path, "w", encoding="utf-8") as log:
            log.write(f"Test run at {time.strftime('%Y-%m-%d %H:%M:%S')}\n\n")
            print_summary(results, total_elapsed, show_detail=True, out=log)
        print(f"\nFailure details written to {log_path}")

    applicable = [
        result for result in results if result.not_applicable_reason is None
    ]
    if not applicable:
        print(f"No tests are applicable in {args.pointer_mode} pointer mode.")
        return 1
    return 0 if not failed else 1


def run_suite(suite: str, args: argparse.Namespace, toolchain: Toolchain) -> int:
    """Run one suite and return its exit code."""
    global _suite_name, _source_dir, _output_dir, _input_dir
    if suite == "bootstrap":
        return __run_bootstrap_suite(args, toolchain)

    _suite_name = suite
    _source_dir = TESTS_DIR / suite
    _output_dir = TESTS_DIR / "output" / suite
    _input_dir = TESTS_DIR / "input" / suite

    # Discover.
    all_tests = discover_tests()

    # By default, exclude experimental tests.
    if not args.include_experimental:
        all_tests = [t for t in all_tests if not t.name.startswith("experimental/")]

    if suite == "basic":
        expanded_tests: list[TestCase] = []
        for test in all_tests:
            for mode, applicable, reason in __test_modes(test, args.pointer_mode, suite):
                if applicable:
                    expanded_tests.append(__mode_test(test, mode))
                else:
                    assert reason is not None
                    expanded_tests.append(__not_applicable_test(test, mode, reason))
        all_tests = expanded_tests
    elif suite == "safety":
        expanded_tests = []
        for test in all_tests:
            for mode, applicable, reason in __test_modes(test, args.pointer_mode, suite):
                if applicable:
                    expanded_tests.append(__mode_test(test, mode))
                else:
                    assert reason is not None
                    expanded_tests.append(__not_applicable_test(test, mode, reason))
        all_tests = expanded_tests
    elif suite == "package":
        # `anx test` receives one explicit mode so its default Both mode does
        # not multiply these outer runs. Graph is mode-independent.
        package_tests: list[TestCase] = []
        for test in all_tests:
            if test.anx_command == "graph":
                package_tests.append(test)
                continue
            for mode, applicable, reason in __test_modes(test, args.pointer_mode, suite):
                if applicable:
                    package_tests.append(__package_mode_test(test, mode))
                else:
                    assert reason is not None
                    package_tests.append(__not_applicable_test(test, mode, reason))
        all_tests = package_tests

    if args.filter_str:
        all_tests = [t for t in all_tests if args.filter_str in t.name]
        if not all_tests:
            print(f"No tests match filter '{args.filter_str}'.")
            return 1

    print(
        f"Running {len(all_tests)} test(s) in suite '{suite}' "
        f"[{toolchain.name}, pointer mode: {args.pointer_mode}]…\n"
    )

    # Run.
    results: list[TestResult] = []
    total_start = time.monotonic()

    for i, test in enumerate(all_tests, 1):
        if test.not_applicable_reason is not None:
            result = TestResult(
                test=test,
                exit_code=0,
                elapsed_ms=0.0,
                output="",
                not_applicable_reason=test.not_applicable_reason,
            )
        else:
            result = run_test(
                test,
                toolchain,
                dump=args.dump,
                run=not args.no_run,
                compile_only=args.no_run,
            )
        results.append(result)

        if not args.quiet:
            tag = "N/A" if result.not_applicable_reason is not None else ("✓" if result.passed() else "✗")
            print(f"  [{i:>{len(str(len(all_tests)))}}/{len(all_tests)}] {tag} {test.name}  ({result.elapsed_ms:.0f} ms)")
            if result.not_applicable_reason is not None:
                print(f"      {result.not_applicable_reason}")

    total_elapsed = (time.monotonic() - total_start) * 1000.0

    # Report.
    show_detail = args.verbose and not args.quiet
    print_summary(results, total_elapsed, show_detail, sys.stdout)

    # Write detailed failure log.
    failed = [
        r for r in results
        if r.not_applicable_reason is None and not r.passed()
    ]
    if failed:
        log_path = ROOT_DIR / "build" / f"test_failures_{suite}_{toolchain.name}.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with open(log_path, "w", encoding="utf-8") as log:
            log.write(f"Test run at {time.strftime('%Y-%m-%d %H:%M:%S')}\n\n")
            print_summary(results, total_elapsed, show_detail=True, out=log)
        print(f"\nFailure details written to {log_path}")

    applicable = [r for r in results if r.not_applicable_reason is None]
    if not applicable:
        print(f"No tests are applicable in {args.pointer_mode} pointer mode.")
        return 1
    return 0 if not failed else 1


def __require_native_anx(suites: tuple[str, ...] | list[str], anx_binary: Path) -> bool:
    """Require an existing native anx only for package-oriented suites."""
    if not any(suite in ("package", "bootstrap") for suite in suites):
        return True
    try:
        resolve_native(str(anx_binary))
        return True
    except NativeToolError as error:
        reason = str(error)
    print(
        f"error: package/bootstrap suites require native anx at {anx_binary}: {reason}; "
        f"cold-install native tools with scripts/bootstrap_toolchain.sh --prefix {shlex.quote(str(anx_binary.parent.parent))}, "
        "or refresh an existing compatible native pair with scripts/refresh_toolchain.sh",
        file=sys.stderr,
    )
    return False


def __require_native_yianc(yianc_binary: Path) -> bool:
    """Require an existing executable ELF compiler; never fall back to Python."""
    try:
        resolve_native(str(yianc_binary))
        return True
    except NativeToolError as error:
        reason = str(error)
    print(
        f"error: native compiler is unavailable at {yianc_binary}: {reason}; "
        f"cold-install with scripts/bootstrap_toolchain.sh --prefix {shlex.quote(str(yianc_binary.parent.parent))} "
        "or refresh a compatible installed toolchain with scripts/refresh_toolchain.sh",
        file=sys.stderr,
    )
    return False


def __make_toolchain(native_bin: Path, compiler: Path, anx: Path | None) -> Toolchain:
    environment = os.environ.copy()
    for variable in ("YIANC", "YIAN_ROOT", "YIAN_LIB"):
        environment.pop(variable, None)

    # Give native anx the checkout's explicit source and trust roots.
    environment["YIAN_ROOT"] = str(ROOT_DIR)
    environment["YIAN_LIB"] = str(LIB_DIR)
    environment["YIANC"] = str(compiler)
    environment["PATH"] = str(native_bin) + os.pathsep + environment.get("PATH", "")
    return Toolchain(
        name="native",
        compiler=(str(compiler),),
        anx=(str(anx if anx is not None else native_bin / "anx"),),
        environment=environment,
    )


def main(argv: list[str] | None = None, *, suite: str = "basic") -> int:
    if suite not in SUITES:
        raise ValueError(f"unsupported test suite: {suite}")

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
        help="Include tests under <suite>/experimental/.",
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
    parser.add_argument(
        "--suite",
        choices=SUITES,
        default=None,
        help="Run a single suite (default: the caller's suite, normally 'basic').",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        dest="run_all",
        help=f"Run every suite: {', '.join(SUITES)}.",
    )
    parser.add_argument(
        "--implementation",
        choices=("both", "python", "native"),
        default="native",
        help=("Only native is supported (default). The legacy python/both values are rejected; "
              "prepare tools explicitly with scripts/bootstrap_toolchain.sh or scripts/refresh_toolchain.sh."),
    )
    parser.add_argument(
        "--pointer-mode",
        choices=("both", "fat", "raw"),
        default="both",
        help="Compile selected targets with fat pointers, raw pointers, or both (default: both).",
    )
    parser.add_argument(
        "--prefix",
        type=Path,
        default=Path(os.environ.get("YIAN_INSTALL_PREFIX", Path.home() / ".local")),
        help="Prefix containing the installed native tools used by the selected suites.",
    )
    args = parser.parse_args(argv)

    if args.implementation != "native":
        parser.error(
            "python/both compiler modes are no longer supported; cold recovery is available through "
            "scripts/bootstrap_toolchain.sh, then run with --implementation native"
        )

    if args.run_all:
        suites = list(SUITES)
    elif args.suite is not None:
        suites = [args.suite]
    else:
        suites = [suite]

    prefix = args.prefix.expanduser().resolve()
    native_bin = prefix / "bin"
    if not __require_native_yianc(native_bin / "yianc"):
        return 1
    compiler = resolve_native(str(native_bin / "yianc"))
    if not __require_native_anx(suites, native_bin / "anx"):
        return 1
    anx = (
        resolve_native(str(native_bin / "anx"))
        if any(suite in ("package", "bootstrap") for suite in suites)
        else None
    )

    toolchain = __make_toolchain(native_bin, compiler, anx)
    print(f"\n{'#' * 60}\n# implementation: native\n{'#' * 60}")
    exit_code = 0
    for suite_name in suites:
        if len(suites) > 1:
            print(f"\n{'#' * 60}\n# suite: {suite_name}\n{'#' * 60}")
        exit_code |= run_suite(suite_name, args, toolchain)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
