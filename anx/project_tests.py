"""Run a project's own tests: ``anx test`` (docs/plan/anx-design.md §8.5).

Layout, mirroring the repository runner's conventions:

    <project>/tests/foo.an                    test source
    <project>/tests/output/foo.an.ans         expected stdout,
                                              optionally headed by "Exit code <N>"
    <project>/tests/error/bar.err.an          expected compile failure
    <project>/tests/output/error/bar.an.ans   required diagnostic substring
    <project>/tests/input/foo.args|.stdin     program arguments / standard input

Tests are compiled in Standalone mode with the standard library on the command
line, so ``from std.core.io import print`` works but importing the project's own
packages does not: a package-mode test root is still an open gap.
"""

from __future__ import annotations

import subprocess
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TextIO

TESTS_DIR_NAME = "tests"


@dataclass(frozen=True)
class ProjectTest:
    """One discovered test source plus its expectation."""

    name: str  # path relative to <project>/tests
    source: Path
    expect_error: bool
    expected_substring: str
    expected_output: str
    expected_exit_code: int | None
    cli_args: tuple[str, ...] | None
    stdin: str


def discover(project_root: Path) -> tuple[ProjectTest, ...]:
    """Discover test sources under ``<project>/tests`` in a stable order."""
    source_dir = project_root / TESTS_DIR_NAME
    if not source_dir.is_dir():
        return ()

    output_dir = source_dir / "output"
    input_dir = source_dir / "input"
    tests: list[ProjectTest] = []
    for source in sorted(source_dir.rglob("*.an")):
        rel = source.relative_to(source_dir).as_posix()
        expectation = __expectation(output_dir, rel)
        base = __base_name(rel)
        args_path = input_dir / f"{base}.args"
        stdin_path = input_dir / f"{base}.stdin"
        tests.append(
            ProjectTest(
                name=rel,
                source=source,
                expect_error=expectation[0],
                expected_exit_code=expectation[1],
                expected_output=expectation[2],
                expected_substring=expectation[3],
                cli_args=tuple(args_path.read_text(encoding="utf-8").split())
                if args_path.is_file()
                else None,
                stdin=stdin_path.read_text(encoding="utf-8") if stdin_path.is_file() else "",
            )
        )
    return tuple(tests)


def run(
    project_root: Path,
    compiler: list[str],
    std_root: Path,
    out: TextIO | None = None,
    flags: Sequence[str] = (),
    env: Mapping[str, str] | None = None,
) -> int:
    """Compile and run every test of *project_root*; return a process exit code."""
    stream = out if out is not None else sys.stdout
    tests = discover(project_root)
    if not tests:
        print(f"No tests found under {project_root / TESTS_DIR_NAME}", file=stream)
        return 1

    build_dir = project_root / "build" / "test"
    build_dir.mkdir(parents=True, exist_ok=True)

    failures: list[tuple[str, str]] = []
    for test in tests:
        detail = __run_one(test, compiler, std_root, build_dir, flags, env)
        tag = "ok" if detail is None else "FAIL"
        print(f"  [{tag}] {test.name}", file=stream)
        if detail is not None:
            failures.append((test.name, detail))

    print(f"\n{len(tests) - len(failures)} passed, {len(failures)} failed, {len(tests)} total", file=stream)
    for name, detail in failures:
        print(f"\n--- {name} ---\n{detail}", file=stream)
    return 1 if failures else 0


def __run_one(
    test: ProjectTest,
    compiler: list[str],
    std_root: Path,
    build_dir: Path,
    flags: Sequence[str],
    env: Mapping[str, str] | None,
) -> str | None:
    """Run one test. Returns ``None`` on success, otherwise failure details."""
    exe = build_dir / (test.name.replace("/", "_").removesuffix(".an") + ".out")
    command = [*compiler, *flags, str(std_root), str(test.source)]
    if test.expect_error:
        command += ["-t", "none"]
    else:
        command += ["-t", "exe", "-o", str(exe)]

    compiled = subprocess.run(
        command,
        cwd=str(test.source.parent),
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )
    compiler_output = compiled.stdout + compiled.stderr

    if test.expect_error:
        if compiled.returncode == 0:
            return "expected a compile error, but compilation succeeded"
        if test.expected_substring and test.expected_substring not in compiler_output:
            return (
                f"expected the diagnostic substring:\n  {test.expected_substring}\n"
                f"compiler output:\n{compiler_output}"
            )
        return None

    if compiled.returncode != 0:
        return f"compilation failed:\n{compiler_output}"

    executed = subprocess.run(
        [str(exe), *(test.cli_args or ())],
        cwd=str(test.source.parent),
        input=test.stdin or None,
        capture_output=True,
        text=True,
        check=False,
    )
    expected = test.expected_output.strip()
    if expected:
        # Runtime failures (a panic, an assertion) go to stderr; accept either
        # stream matching the expectation, as the repository runner does.
        actual = (executed.stdout + executed.stderr).strip()
        if actual != expected:
            return f"expected output:\n{expected!r}\ngot:\n{actual!r}"
    if test.expected_exit_code is None:
        if executed.returncode != 0:
            return f"expected exit code 0, got {executed.returncode}\n{executed.stderr}"
    elif executed.returncode != test.expected_exit_code:
        return f"expected exit code {test.expected_exit_code}, got {executed.returncode}"
    return None


def __base_name(rel: str) -> str:
    """Map a test source path to its expectation/input base name."""
    if rel.endswith(".err.an"):
        return rel[: -len(".err.an")]
    if rel.endswith(".an"):
        return rel[: -len(".an")]
    return rel


def __expectation(output_dir: Path, rel: str) -> tuple[bool, int | None, str, str]:
    """Read the expectation for *rel*; returns expect_error/code/stdout/substring."""
    is_error = rel.endswith(".err.an")
    ans = output_dir / (f"{__base_name(rel)}.an.ans" if is_error else f"{rel}.ans")
    if not ans.is_file():
        return is_error, None, "", ""

    raw = ans.read_text(encoding="utf-8").rstrip("\n")
    if not raw.startswith("Exit code "):
        if is_error:
            return True, None, "", raw
        return False, None, raw, ""

    newline = raw.find("\n")
    header = raw if newline == -1 else raw[:newline]
    rest = "" if newline == -1 else raw[newline + 1 :]
    try:
        code = int(header[len("Exit code ") :].strip())
    except ValueError:
        code = 0
    if is_error:
        # Error tests do not check the exact compiler exit code.
        return True, None, "", rest
    return False, code, rest, ""
