"""Run a project's own tests: ``anx test`` (docs/plan/anx-design.md §8.5).

Layout, mirroring the repository runner's conventions:

    <project>/tests/foo.an                    test source
    <project>/tests/output/foo.an.ans         expected stdout,
                                              optionally headed by "Exit code <N>"
    <project>/tests/error/bar.err.an          expected compile failure
    <project>/tests/output/error/bar.an.ans   required diagnostic substring
    <project>/tests/input/foo.args|.stdin     program arguments / standard input

Each test is compiled in **package mode**: the test file is registered as a
synthetic package rooted at ``<project>/tests`` whose entry is that file and
whose dependencies are the root package plus the root package's direct
dependencies.  A test therefore imports project code exactly the way project
code imports it (``from <package>.<module> import ...``), while the visibility
rule (§4.3) still holds: a transitive dependency is *not* visible to a test
unless the root package declares it.
"""

from __future__ import annotations

import json
import subprocess
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TextIO

from anx.project import PackageKind, Project

TESTS_DIR_NAME = "tests"

#: Package name under which the test file is compiled.  A project that declares
#: this name itself is rejected rather than silently shadowed.
TEST_PACKAGE = "__anx_test"


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
    project: Project,
    compiler: list[str],
    out: TextIO | None = None,
    flags: Sequence[str] = (),
    env: Mapping[str, str] | None = None,
) -> int:
    """Compile and run every test of *project*; return a process exit code."""
    stream = out if out is not None else sys.stdout
    project_root = project.packages[project.root_package].root
    tests = discover(project_root)
    if not tests:
        print(f"No tests found under {project_root / TESTS_DIR_NAME}", file=stream)
        return 1

    if TEST_PACKAGE in project.package_specs():
        print(
            f"error: the project already declares a package named '{TEST_PACKAGE}', "
            "which `anx test` needs for its own test root",
            file=stream,
        )
        return 1

    build_dir = project_root / "build" / "test"
    build_dir.mkdir(parents=True, exist_ok=True)

    failures: list[tuple[str, str]] = []
    for test in tests:
        detail = __run_one(test, project, compiler, build_dir, flags, env)
        tag = "ok" if detail is None else "FAIL"
        print(f"  [{tag}] {test.name}", file=stream)
        if detail is not None:
            failures.append((test.name, detail))

    print(f"\n{len(tests) - len(failures)} passed, {len(failures)} failed, {len(tests)} total", file=stream)
    for name, detail in failures:
        print(f"\n--- {name} ---\n{detail}", file=stream)
    return 1 if failures else 0


def __test_package_map(project: Project, test: ProjectTest) -> dict[str, object]:
    """The v2 package map for one test: the project plus a synthetic test package.

    The test package is rooted at ``<project>/tests`` so the test file is its
    only module, and it depends on the root package plus the root package's
    direct dependencies — the same set the root package itself can see.
    """
    root = project.root_package
    root_package = project.packages[root]
    tests_dir = root_package.root / TESTS_DIR_NAME
    packages = project.package_specs()
    packages[TEST_PACKAGE] = {
        "sourceRoot": str(tests_dir.resolve()),
        "kind": "bin",
        "entry": str(test.source.resolve()),
        # Cargo's model: integration tests link the package's *library* (so a
        # bin-only root has no library interface to import), plus the package's
        # dependencies and its dev-dependencies.
        "dependencies": [
            *([root] if root_package.kind is not PackageKind.BIN else []),
            *(dependency.name for dependency in root_package.dependencies),
            *(dependency.name for dependency in root_package.dev_dependencies),
        ],
    }
    return {"format": 2, "root": TEST_PACKAGE, "packages": packages}


def __run_one(
    test: ProjectTest,
    project: Project,
    compiler: list[str],
    build_dir: Path,
    flags: Sequence[str],
    env: Mapping[str, str] | None,
) -> str | None:
    """Run one test. Returns ``None`` on success, otherwise failure details."""
    pkg_json = build_dir / "pkg.json"
    pkg_json.write_text(json.dumps(__test_package_map(project, test), indent=2), encoding="utf-8")

    files = [str(test.source.resolve()), *(str(path) for path in project.files)]
    exe = build_dir / (test.name.replace("/", "_").removesuffix(".an") + ".out")
    command = [*compiler, "--packages", str(pkg_json), *flags, *files]
    if test.expect_error:
        command += ["-t", "none"]
    else:
        command += ["-t", "exe", "-o", str(exe)]

    compiled = subprocess.run(
        command,
        cwd=str(project.packages[project.root_package].root),
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )
    compiler_output = compiled.stdout + compiled.stderr
    hint = __bin_root_hint(project, compiler_output)

    if test.expect_error:
        if compiled.returncode == 0:
            return "expected a compile error, but compilation succeeded"
        if test.expected_substring and test.expected_substring not in compiler_output:
            return (
                f"expected the diagnostic substring:\n  {test.expected_substring}\n"
                f"compiler output:\n{compiler_output}{hint}"
            )
        return None

    if compiled.returncode != 0:
        return f"compilation failed:\n{compiler_output}{hint}"

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


def __bin_root_hint(project: Project, compiler_output: str) -> str:
    """Explain the one surprising consequence of the Cargo-aligned rule.

    A ``bin`` root has no library interface, so its own tests cannot import it;
    that surfaces as an ordinary ``AX009`` from the synthetic test package,
    which reads oddly without this note.
    """
    root = project.root_package
    if project.packages[root].kind is not PackageKind.BIN:
        return ""
    if "AX009" not in compiler_output or f"'{root}'" not in compiler_output:
        return ""
    return (
        f"\nhint: package '{root}' has kind 'bin', so its modules are not visible to tests.\n"
        f'      Declare kind = "hybrid" to expose a library interface, or move the\n'
        f"      reusable code into a lib dependency.\n"
    )


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
