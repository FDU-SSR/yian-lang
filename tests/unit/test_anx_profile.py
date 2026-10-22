"""Focused native integration probes for anx/compiler profiling reports."""

from __future__ import annotations

import copy
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from _toolchain.bootstrap_anx import build_recipe


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _by_pass(timings: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {item["pass_id"]: item for item in timings}


class _ProfileFixture(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        prefix_value = os.environ.get("YIAN_PROFILE_TEST_PREFIX")
        if not prefix_value:
            raise AssertionError("YIAN_PROFILE_TEST_PREFIX must name the authorized native prefix")
        cls.prefix = Path(prefix_value).resolve()
        cls.anx = cls.prefix / "bin" / "anx"
        cls.yianc = cls.prefix / "bin" / "yianc"
        if not cls.anx.is_file() or not os.access(cls.anx, os.X_OK):
            raise AssertionError(f"anx is missing from authorized prefix: {cls.anx}")
        if not cls.yianc.is_file() or not os.access(cls.yianc, os.X_OK):
            raise AssertionError(f"yianc is missing from authorized prefix: {cls.yianc}")

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="anx profile probe ")
        self.root = Path(self.temporary.name)
        self.environment = os.environ.copy()
        self.environment["YIANC"] = str(self.yianc)
        self.environment["YIAN_ROOT"] = str(ROOT)
        self.environment["YIAN_LIB"] = str(ROOT / "lib" / "src")
        self.environment["PATH"] = str(self.prefix / "bin") + os.pathsep + self.environment.get("PATH", "")

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def make_project(self, *, main: str, with_test: bool = False, name: str = "project") -> Path:
        project = self.root / name
        (project / "src").mkdir(parents=True)
        (project / "package.anx").write_text(
            '[package]\nname = "profile_fixture"\nkind = "hybrid"\nversion = "0.1.0"\n',
            encoding="utf-8",
        )
        (project / "src" / "main.an").write_text(main, encoding="utf-8")
        if with_test:
            (project / "tests" / "error").mkdir(parents=True)
            (project / "tests" / "output" / "error").mkdir(parents=True)
            (project / "tests" / "error" / "bad.err.an").write_text(
                'fn main() { let value: i32 = "bad"; }\n', encoding="utf-8"
            )
            (project / "tests" / "output" / "error" / "bad.an.ans").write_text(
                "Expected type 'i32' but got 'str'\n", encoding="utf-8"
            )
        return project

    def run_anx(self, project: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
        return self.run_anx_in(project, *arguments)

    def run_anx_in(self, directory: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [str(self.anx), *arguments],
            cwd=directory,
            env=self.environment,
            text=True,
            capture_output=True,
            check=False,
            timeout=180,
        )

    def run_anx_input(self, directory: Path, input_text: str,
                      *arguments: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [str(self.anx), *arguments],
            cwd=directory,
            env=self.environment,
            text=True,
            input=input_text,
            capture_output=True,
            check=False,
            timeout=180,
        )

    def profile_runs(self, project: Path) -> list[Path]:
        profile_root = project / "build" / "profile"
        if not profile_root.exists():
            return []
        return sorted(path for path in profile_root.iterdir() if path.is_dir())

    def runtime_profile_runs(self, project: Path) -> list[Path]:
        profile_root = project / "build" / "runtime-profile"
        if not profile_root.exists():
            return []
        return sorted(path for path in profile_root.iterdir() if path.is_dir())

class AnxProfileTests(_ProfileFixture):

    def test_build_profile_cli_migration(self) -> None:
        project = self.make_project(main='fn main() { print("business-marker\n"); }\n')
        nested = project / "nested"
        nested.mkdir()

        built = self.run_anx_in(nested, "build", "profile", "--profile-dir", "reports/compile", "-O1")
        self.assertEqual(built.returncode, 0, built.stdout + built.stderr)
        self.assertEqual(built.stdout, "")
        self.assertIn(str(project / "reports" / "compile"), built.stderr)
        self.assertTrue((project / "build" / "app").is_file())
        self.assertTrue((project / "reports" / "compile").is_dir())
        self.assertFalse((nested / "reports").exists())
        self.assertFalse((project / "build" / "runtime-profile").exists())

        runs = sorted((project / "reports" / "compile").iterdir())
        self.assertEqual(len(runs), 1)
        report_path = runs[0] / "profile.json"
        summary_path = runs[0] / "summary.txt"
        self.assertTrue(report_path.is_file())
        self.assertTrue(summary_path.is_file())
        self.assertEqual(len(list((runs[0] / "compiler").glob("*.json"))), 1)
        report = _read_json(report_path)
        self.assertEqual(report["format"], 1)
        self.assertEqual(report["kind"], "anx-build-profile")
        self.assertEqual(report["command"], "build")
        self.assertEqual(report["build_outcome"], "ok")
        self.assertEqual(report["command_exit_code"], 0)
        self.assertGreater(report["build_total_ns"], 0)
        self.assertIn("build total:", summary_path.read_text(encoding="utf-8"))
        self.assertEqual(len(report["invocations"]), 1)
        invocation = report["invocations"][0]
        self.assertEqual(invocation["compiler_status"], 0)
        self.assertEqual(invocation["report_status"], "ok")
        compiler_report = _read_json(Path(invocation["profile_file"]))
        compiler_passes = _by_pass(compiler_report["timings"])
        self.assertGreater(compiler_passes["llvm.optimize"]["calls"], 0)
        self.assertGreater(compiler_passes["llvm.emit_native"]["calls"], 0)
        self.assertGreater(compiler_passes["native.link"]["calls"], 0)

        failed_project = self.make_project(
            name="failed-project", main='fn main() { let value: i32 = "bad"; }\n'
        )
        failed = self.run_anx(failed_project, "build", "profile")
        self.assertEqual(failed.returncode, 1)
        self.assertNotIn("business-marker", failed.stdout)
        self.assertIn("Expected type 'i32' but got 'str'", failed.stderr)
        failed_runs = self.profile_runs(failed_project)
        self.assertEqual(len(failed_runs), 1)
        failed_report = _read_json(failed_runs[0] / "profile.json")
        self.assertEqual(failed_report["build_outcome"], "failed")
        self.assertEqual(failed_report["command_exit_code"], 1)
        self.assertEqual(len(failed_report["invocations"]), 1)
        failed_invocation = failed_report["invocations"][0]
        self.assertNotEqual(failed_invocation["compiler_status"], 0)
        self.assertEqual(failed_invocation["report_status"], "ok")
        failed_compiler_report = _read_json(Path(failed_invocation["profile_file"]))
        self.assertFalse(failed_compiler_report["ok"])
        self.assertFalse(
            any(item["pass_id"] == "native.link" and item["calls"] for item in failed_compiler_report["timings"])
        )
        self.assertFalse((failed_project / "build" / "app").exists())

        empty = self.root / "empty"
        empty.mkdir()
        for command in ("build", "run", "check", "test"):
            rejected = self.run_anx_in(empty, command, "--profile")
            self.assertEqual(rejected.returncode, 2, command)
            self.assertIn("--profile was removed", rejected.stderr)
            self.assertIn("anx build profile", rejected.stderr)
            self.assertNotIn("no package.anx", rejected.stderr)
            self.assertFalse((empty / "build").exists())

        empty_dir = self.run_anx(project, "build", "profile", "--profile-dir", "")
        self.assertEqual(empty_dir.returncode, 2)
        self.assertIn("--profile-dir requires a non-empty path", empty_dir.stderr)
        missing_value = self.run_anx(project, "build", "profile", "--profile-dir")
        self.assertEqual(missing_value.returncode, 2)
        self.assertEqual(sorted((project / "reports" / "compile").iterdir()), runs)

    def test_expected_error_without_profile(self) -> None:
        project = self.make_project(main='fn main() {}\n', with_test=True)
        tested = self.run_anx(project, "test", "--pointer-mode", "both")
        self.assertEqual(tested.returncode, 0, tested.stdout + tested.stderr)
        self.assertIn("[ok] error/bad.err.an", tested.stdout)
        self.assertFalse((project / "build" / "profile").exists())

    def test_deterministic_accounting_and_report_io_failure(self) -> None:
        project = self.root / "deterministic-project"
        project.mkdir()
        probe_source_root = self.root / "probe" / "src"
        probe_source_root.mkdir(parents=True)
        blocked_root = self.root / "blocked-project"
        blocked_root.mkdir()
        (blocked_root / "build").write_text("not a directory\n", encoding="utf-8")
        source = probe_source_root / "main.an"
        report_io_source = self.root / "report-io-failure.an"
        report_io_source.write_text("fn main() {}\n", encoding="utf-8")
        compiler_output = self.root / "profile-output-is-a-directory"
        compiler_output.mkdir()

        compiler_parent_report = self.root / "compiler-parent.json"
        compiler_failure_report = self.root / "compiler-failure.json"
        compiler_clock_report = self.root / "compiler-clock.json"
        project_literal = json.dumps(str(project))
        blocked_literal = json.dumps(str(blocked_root))
        parent_report_literal = json.dumps(str(compiler_parent_report))
        failure_report_literal = json.dumps(str(compiler_failure_report))
        clock_report_literal = json.dumps(str(compiler_clock_report))
        source.write_text(
            f'''from anx.driver.profile import BuildProfile;
from std.core.env import exit;
from yianc.driver.profile import ProfileKind, ProfileRecorder;

fn main() {{
    let recorder = ProfileRecorder.new();
    let parent = recorder.begin_at("fixture.parent", ProfileKind.Stage, Option<i64>.Some(100i64));
    let child = recorder.begin_at("fixture.child", ProfileKind.Pass, Option<i64>.Some(130i64));
    recorder.end_at(child, true, Option<i64>.Some(160i64));
    recorder.end_at(parent, true, Option<i64>.Some(200i64));
    match recorder.write_report({parent_report_literal}, true, "check", 0u8, false) {{
        Ok(_) => {{}}, Err(problem) => {{ problem.drop(); exit(31i32); }},
    }}
    recorder.drop();

    let failed = ProfileRecorder.new();
    let failed_span = failed.begin_at("fixture.failed", ProfileKind.Pass, Option<i64>.Some(500i64));
    failed.end_at(failed_span, false, Option<i64>.Some(510i64));
    match failed.write_report({failure_report_literal}, false, "check", 0u8, false) {{
        Ok(_) => {{}}, Err(problem) => {{ problem.drop(); exit(32i32); }},
    }}
    failed.drop();

    let clock = ProfileRecorder.new();
    let clock_span = clock.begin_at("fixture.clock", ProfileKind.Pass, Option<i64>.None);
    clock.end_at(clock_span, false, Option<i64>.None);
    match clock.write_report({clock_report_literal}, false, "check", 0u8, false) {{
        Ok(_) => {{}}, Err(problem) => {{ problem.drop(); exit(33i32); }},
    }}
    clock.drop();

    let first = BuildProfile.new("test", "check", 0u8, "fat");
    first.set_project_root({project_literal});
    let outer = first.begin_at("anx.project_load", Option<i64>.Some(10i64));
    let nested = first.begin_at("anx.test_discovery", Option<i64>.Some(40i64));
    first.finish_at(nested, true, Option<i64>.Some(70i64));
    first.finish_at(outer, true, Option<i64>.Some(110i64));
    let wait = first.begin_at("anx.compiler_wait", Option<i64>.Some(2010i64));
    first.finish_at(wait, true, Option<i64>.Some(2030i64));
    first.finalize(true, 0i32);
    first.drop();

    let second = BuildProfile.new("test", "check", 0u8, "fat");
    second.set_project_root({project_literal});
    second.finalize(true, 0i32);
    second.drop();

    let blocked = BuildProfile.new("test", "check", 0u8, "fat");
    blocked.set_project_root({blocked_literal});
    blocked.finalize(true, 0i32);
    blocked.drop();
}}
''',
            encoding="utf-8",
        )

        recipe = build_recipe(ROOT, root_package="anx")
        packages = copy.deepcopy(recipe.package_map["packages"])
        packages["probe"] = {
            "sourceRoot": str(probe_source_root.resolve()),
            "kind": "bin",
            "entry": str(source.resolve()),
            "dependencies": ["anx", "yianc"],
            "ffi": False,
        }
        package_map = self.root / "packages.json"
        package_map.write_text(
            json.dumps({"format": 2, "root": "probe", "packages": packages}),
            encoding="utf-8",
        )
        input_sources = [
            path
            for path in recipe.source_files
            if path.is_relative_to(ROOT / "lib" / "src")
            or path.is_relative_to(ROOT / "bootstrap" / "json" / "src")
            or path in {ROOT / "anx" / "src" / "driver" / "profile.an", ROOT / "yianc" / "src" / "driver" / "profile.an"}
        ]
        input_sources.append(source)
        executable = self.root / "profile probe"
        compile_result = subprocess.run(
            [
                str(self.yianc), "--compiler-root", str(ROOT), "--packages", str(package_map),
                "-t", "exe", "-O0", "-o", str(executable),
                *(str(path) for path in input_sources),
            ],
            cwd=ROOT,
            env=self.environment,
            text=True,
            capture_output=True,
            check=False,
            timeout=300,
        )
        self.assertEqual(compile_result.returncode, 0, compile_result.stdout + compile_result.stderr)
        self.assertTrue(executable.is_file())

        execution = subprocess.run(
            [str(executable)],
            cwd=project,
            env=self.environment,
            text=True,
            capture_output=True,
            check=False,
            timeout=30,
        )
        self.assertEqual(execution.returncode, 0, execution.stdout + execution.stderr)
        self.assertIn("[profile] warning:", execution.stderr)

        report_io_failure = subprocess.run(
            [
                str(self.yianc), "--compiler-root", str(ROOT), "--check", "--profile-output", str(compiler_output),
                str(ROOT / "lib" / "src"), str(report_io_source),
            ],
            cwd=ROOT,
            env=self.environment,
            text=True,
            capture_output=True,
            check=False,
            timeout=180,
        )
        self.assertEqual(
            report_io_failure.returncode, 0,
            report_io_failure.stdout + report_io_failure.stderr,
        )
        self.assertIn("[profile] warning:", report_io_failure.stderr)

        parent = _read_json(compiler_parent_report)
        parent_passes = _by_pass(parent["timings"])
        self.assertEqual(parent["profiling_state"], "complete", json.dumps(parent, sort_keys=True))
        self.assertIsInstance(parent["work_finished_at_ns"], int)
        self.assertGreaterEqual(parent["work_finished_at_ns"], 0)
        self.assertEqual(parent_passes["fixture.parent"]["duration_ns"], 100)
        self.assertEqual(parent_passes["fixture.parent"]["self_ns"], 70)
        self.assertEqual(parent_passes["fixture.child"]["duration_ns"], 30)

        failed = _read_json(compiler_failure_report)
        failed_pass = _by_pass(failed["timings"])["fixture.failed"]
        self.assertEqual(failed_pass["failed_calls"], 1)
        self.assertEqual(failed_pass["duration_ns"], 10)
        clock = _read_json(compiler_clock_report)
        self.assertEqual(clock["profiling_state"], "unavailable")
        self.assertTrue(_by_pass(clock["timings"])["fixture.clock"]["incomplete"])

        build_runs = self.profile_runs(project)
        self.assertEqual(len(build_runs), 2)
        build_reports = [_read_json(path / "profile.json") for path in build_runs]
        self.assertEqual(len({item["run_id"] for item in build_reports}), 2)
        measured = next(item for item in build_reports if item["build_total_ns"] == 120)
        self.assertEqual(measured["profiling_state"], "complete")
        spans = _by_pass(measured["anx_timings"])
        self.assertEqual(spans["anx.project_load"]["duration_ns"], 100)
        self.assertEqual(spans["anx.project_load"]["self_ns"], 70)
        self.assertEqual(spans["anx.test_discovery"]["duration_ns"], 30)
        self.assertEqual(spans["anx.compiler_wait"]["duration_ns"], 20)

class RuntimeProfileTests(_ProfileFixture):
    def runtime_source(self) -> str:
        return '''from std.core.env import args;
from std.core.fs import current_dir;
from std.core.io import Stdin, BufReader;
from std.core.ops import Index;

fn release_args(values: Vec<String>&) {
    let index = 0u64;
    while index < values.len() { values[index].drop(); index += 1u64; }
    values.drop();
}

fn main() {
    let argv = args();
    defer release_args(&argv);
    let input = Stdin.new();
    let reader = BufReader<Stdin>.new(&input);
    defer reader.drop();
    let line = match reader.read_line() {
        Ok(Some(value)) => value,
        Ok(None) => String.new(),
        Err(problem) => { problem.drop(); @panic("stdin failed"); },
    };
    defer line.drop();
    let cwd = match current_dir() {
        Ok(value) => value,
        Err(problem) => { problem.drop(); @panic("cwd unavailable"); },
    };
    defer cwd.drop();
    print("business:");
    print(argv[1].as_str());
    print(":");
    print(line.as_str().trim());
    print(":");
    print(cwd.as_str());
    print("\\n");
}
'''

    def write_file(self, path: Path, text: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def identity_project(self) -> Path:
        project = self.make_project(main="fn main() {}\n", name="identity-project")
        self.write_file(
            project / "package.anx",
            '[package]\nname = "profile_fixture"\nkind = "bin"\nversion = "0.1.0"\n\n'
            '[dependencies]\ndep = { path = "dep" }\n',
        )
        self.write_file(
            project / "dep" / "package.anx",
            '[package]\nname = "dep"\nkind = "lib"\nversion = "0.1.0"\n',
        )
        self.write_file(project / "dep" / "src" / "leaf.an", "pub fn dep_leaf() -> i32 { return 5; }\n")
        self.write_file(project / "src" / "other.an", "pub fn same_name() -> i32 { return 4; }\n")
        self.write_file(project / "src" / "main.an", '''from profile_fixture.other import same_name as other_same_name;
from dep.leaf import dep_leaf;

fn leaf() -> i32 { return 1; }

fn path_a() -> i32 { return leaf() + leaf(); }

fn path_b() -> i32 { return leaf(); }

fn rec(n: i32) -> i32 {
    if n <= 0 { return 0; }
    return rec(n - 1) + 1;
}

fn is_even(n: i32) -> bool {
    if n == 0 { return true; }
    return is_odd(n - 1);
}

fn is_odd(n: i32) -> bool {
    if n == 0 { return false; }
    return is_even(n - 1);
}

fn pick<T>(value: T) -> T { return value; }

struct Counter { pub value: i32 }

impl Counter {
    static pub fn new(value: i32) -> Counter { return Counter(value); }
    pub fn bump(delta: i32) -> i32 { return self.value + delta; }
}

trait Step { pub fn step(delta: i32) -> i32; }

impl Step for Counter {
    pub fn step(delta: i32) -> i32 { return self.value + delta; }
}

fn same_name() -> i32 { return 3; }

fn never_called() -> i32 { return 99; }

fn main() {
    let counter = Counter.new(10);
    let bumped = counter.bump(5);
    let stepper: Step& = &counter;
    let stepped = stepper.step(7);
    let add = |base = counter.value| (x: i32) -> i32 { base + x };
    let closed = add(3);
    let f: fn() -> i32 = &leaf;
    let pointer = f();
    let first = pick<i32>(2);
    let second = pick<i64>(4i64);
    let a = path_a();
    let b = path_b();
    let r = rec(2);
    let even = is_even(2);
    let other = same_name();
    let other_module = other_same_name();
    let from_dep = dep_leaf();
    let items = Vec<i32>.new();
    items.push(first);
    assert items.len() == 1;
    assert bumped == 15;
    assert stepped == 17;
    assert closed == 13;
    assert pointer == 1;
    assert first == 2;
    assert second == 4i64;
    assert a == 2;
    assert b == 1;
    assert r == 2;
    assert even;
    assert other == 3;
    assert other_module == 4;
    assert from_dep == 5;
    let total = a + b + pointer + r + other + other_module + from_dep + bumped + stepped + closed;
    assert total == 2 + 1 + 1 + 2 + 3 + 4 + 5 + 15 + 17 + 13;
}
''')
        return project

    def report_directory(self, stderr: str) -> Path:
        matches = re.findall(r"report written to (.+)", stderr)
        self.assertEqual(len(matches), 1, stderr)
        return Path(matches[0].strip())

    def test_runtime_profile_workflow(self) -> None:
        project = self.make_project(main=self.runtime_source(), name="runtime-project")
        (project / "build").mkdir()
        (project / "build" / "app").write_text("KEEP", encoding="utf-8")
        nested = project / "nested"
        nested.mkdir()

        first = self.run_anx_input(nested, "hello\n", "run", "profile", "-O1", "--", "alpha")
        self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
        expected = f"business:alpha:hello:{nested.resolve()}\n"
        self.assertEqual(first.stdout, expected)
        first_dir = self.report_directory(first.stderr)
        self.assertTrue(first_dir.is_absolute())
        self.assertEqual(first_dir.parent, (project / "build" / "runtime-profile").resolve())
        self.assertEqual((project / "reports").exists(), False)
        report_path = first_dir / "profile.json"
        summary_path = first_dir / "summary.txt"
        self.assertTrue(report_path.is_file())
        self.assertTrue(summary_path.is_file())
        report = _read_json(report_path)
        self.assertEqual(report["format"], 1)
        self.assertEqual(report["kind"], "yian-runtime-profile")
        self.assertEqual(report["abi_version"], 1)
        self.assertEqual(report["run"]["id"], first_dir.name)
        self.assertEqual(report["run"]["report_dir"], str(first_dir))
        self.assertEqual(report["run"]["pointer_mode"], "fat")
        self.assertEqual(report["run"]["optimization"], 1)
        program = Path(report["run"]["program"])
        self.assertTrue(program.is_absolute())
        self.assertTrue(program.is_file())
        self.assertNotEqual(program, project / "build" / "app")
        self.assertEqual(report["measurement"]["clock"], "CLOCK_MONOTONIC")
        self.assertEqual(report["measurement"]["unit"], "ns")
        self.assertIsInstance(report["measurement"]["resolution_ns"], int)
        self.assertEqual(report["measurement"]["method"], "instrumentation_elapsed")
        self.assertFalse(report["measurement"]["overhead_compensated"])
        main_functions = [
            item for item in report["functions"]
            if item["package"] == "profile_fixture" and item["display_name"] == "main"
        ]
        self.assertEqual(len(main_functions), 1)
        main_function = main_functions[0]
        self.assertGreaterEqual(main_function["calls"], 1)
        self.assertGreaterEqual(main_function["inclusive_ns"], main_function["self_ns"])
        roots = [item for item in report["call_tree"] if item["parent_id"] == 0]
        self.assertEqual(len(roots), 1)
        self.assertEqual(roots[0]["function_id"], main_function["function_id"])
        summary = summary_path.read_text(encoding="utf-8")
        self.assertIn("unit: ns", summary)
        self.assertIn("resolution_ns:", summary)
        self.assertIn("instrumentation_elapsed", summary)
        self.assertIn("overhead_compensated: false", summary)
        self.assertIn("profile_fixture::main", summary)
        self.assertIn(str(main_function["self_ns"]), summary)
        self.assertIn(str(main_function["inclusive_ns"]), summary)
        self.assertFalse((project / "build" / "profile").exists())
        self.assertEqual((project / "build" / "app").read_text(encoding="utf-8"), "KEEP")

        first_report = report_path.read_bytes()
        first_summary = summary_path.read_bytes()
        second = self.run_anx_input(nested, "hello\n", "run", "profile", "--profile-dir", "reports/runtime", "--", "alpha")
        self.assertEqual(second.returncode, 0, second.stdout + second.stderr)
        self.assertEqual(second.stdout, expected)
        second_dir = self.report_directory(second.stderr)
        self.assertEqual(second_dir.parent, (project / "reports" / "runtime").resolve())
        self.assertNotEqual(second_dir, first_dir)
        self.assertEqual(report_path.read_bytes(), first_report)
        self.assertEqual(summary_path.read_bytes(), first_summary)
        self.assertFalse((nested / "reports").exists())
        second_report = _read_json(second_dir / "profile.json")
        second_program = Path(second_report["run"]["program"])
        self.assertNotEqual(second_program, program)
        self.assertTrue(second_program.is_file())
        self.assertTrue(program.is_file())
        self.assertEqual(second_report["run"]["id"], second_dir.name)
        self.assertEqual(second_report["run"]["pointer_mode"], "fat")
        self.assertEqual(second_report["run"]["optimization"], 0)
        artifacts = sorted((project / "build" / "runtime-profile-artifacts").iterdir())
        self.assertEqual(len(artifacts), 2)
        self.assertEqual({item.name for item in artifacts}, {program.parent.name, second_program.parent.name})
        self.assertEqual((project / "build" / "app").read_text(encoding="utf-8"), "KEEP")

    def test_runtime_profile_identity_paths(self) -> None:
        project = self.identity_project()
        result = self.run_anx(project, "run", "profile", "--release", "--raw-pointers")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        runs = self.runtime_profile_runs(project)
        self.assertEqual(len(runs), 1)
        report = _read_json(runs[0] / "profile.json")
        self.assertEqual(report["run"]["optimization"], 3)
        self.assertEqual(report["run"]["pointer_mode"], "raw")
        functions = report["functions"]
        identifiers = [item["function_id"] for item in functions]
        self.assertEqual(len(identifiers), len(set(identifiers)))
        self.assertTrue(all(item["calls"] > 0 for item in functions))

        def find(display: str, module: str | None = None, package: str = "profile_fixture") -> list[dict[str, Any]]:
            return [
                item for item in functions
                if item["display_name"] == display
                and item["package"] == package
                and (module is None or item["module"] == module)
            ]

        leaf = find("leaf", "main.an")
        self.assertEqual(len(leaf), 1)
        leaf_function = leaf[0]
        self.assertEqual(leaf_function["calls"], 4)
        self.assertEqual(leaf_function["line"], 4)
        self.assertGreaterEqual(leaf_function["column"], 1)
        self.assertEqual(leaf_function["kind"], "function")
        self.assertEqual(find("path_a", "main.an")[0]["calls"], 1)
        self.assertEqual(find("path_b", "main.an")[0]["calls"], 1)
        same_names = find("same_name")
        self.assertEqual(len(same_names), 2)
        self.assertEqual({item["module"] for item in same_names}, {"main.an", "other.an"})
        self.assertEqual({item["calls"] for item in same_names}, {1})
        picks = find("pick", "main.an")
        self.assertEqual(len(picks), 2)
        self.assertEqual({item["concrete_arguments"] for item in picks}, {"i32", "i64"})
        self.assertEqual({item["calls"] for item in picks}, {1})
        bumps = [item for item in functions if item["display_name"].endswith(".bump") and item["module"] == "main.an"]
        self.assertEqual(len(bumps), 1)
        self.assertEqual(bumps[0]["kind"], "method")
        self.assertIn("Counter", bumps[0]["owner_type"])
        self.assertEqual(bumps[0]["calls"], 1)
        steps = [item for item in functions if item["display_name"].endswith(".step") and item["module"] == "main.an"]
        self.assertEqual(len(steps), 1)
        self.assertEqual(steps[0]["kind"], "method")
        self.assertTrue(steps[0]["owner_type"])
        self.assertEqual(steps[0]["calls"], 1)
        closures = [item for item in functions if item["kind"] == "closure" and item["package"] == "profile_fixture"]
        self.assertEqual(len(closures), 1)
        self.assertEqual(closures[0]["origin"], "closure literal")
        self.assertEqual(closures[0]["module"], "main.an")
        self.assertGreater(closures[0]["line"], 0)
        self.assertEqual(closures[0]["calls"], 1)
        dep_leaves = find("dep_leaf", "leaf.an", package="dep")
        self.assertEqual(len(dep_leaves), 1)
        self.assertEqual(dep_leaves[0]["calls"], 1)
        self.assertEqual(find("rec", "main.an")[0]["calls"], 3)
        self.assertEqual(find("is_even", "main.an")[0]["calls"], 2)
        self.assertEqual(find("is_odd", "main.an")[0]["calls"], 1)
        self.assertEqual(find("never_called"), [])
        self.assertTrue(any(item["package"] == "std" for item in functions))

        by_id = {item["function_id"]: item for item in functions}
        totals: dict[int, int] = {}
        for node in report["call_tree"]:
            totals[node["function_id"]] = totals.get(node["function_id"], 0) + node["calls"]
        for function_id, item in by_id.items():
            self.assertEqual(totals[function_id], item["calls"], item["display_name"])
        path_a_id = find("path_a", "main.an")[0]["function_id"]
        path_b_id = find("path_b", "main.an")[0]["function_id"]
        main_id = find("main", "main.an")[0]["function_id"]
        main_node = next(node for node in report["call_tree"] if node["function_id"] == main_id)
        path_a_node = next(
            node for node in report["call_tree"]
            if node["function_id"] == path_a_id and node["parent_id"] == main_node["node_id"]
        )
        path_b_node = next(
            node for node in report["call_tree"]
            if node["function_id"] == path_b_id and node["parent_id"] == main_node["node_id"]
        )
        leaf_nodes = [node for node in report["call_tree"] if node["function_id"] == leaf_function["function_id"]]
        leaf_by_parent = {node["parent_id"]: node["calls"] for node in leaf_nodes}
        self.assertEqual(leaf_by_parent[path_a_node["node_id"]], 2)
        self.assertEqual(leaf_by_parent[path_b_node["node_id"]], 1)
        self.assertEqual(leaf_by_parent[main_node["node_id"]], 1)

        nodes_by_id = {node["node_id"]: node for node in report["call_tree"]}

        def ancestor_chain(node: dict[str, Any]) -> list[int]:
            chain = [node["function_id"]]
            while node["parent_id"] != 0:
                node = nodes_by_id[node["parent_id"]]
                chain.append(node["function_id"])
            return chain

        rec_nodes = [node for node in report["call_tree"] if node["function_id"] == find("rec", "main.an")[0]["function_id"]]
        self.assertEqual(len(rec_nodes), 3)
        self.assertEqual(sorted(len(ancestor_chain(node)) for node in rec_nodes), [2, 3, 4])
        even_id = find("is_even", "main.an")[0]["function_id"]
        odd_id = find("is_odd", "main.an")[0]["function_id"]
        even_nodes = [node for node in report["call_tree"] if node["function_id"] == even_id]
        odd_nodes = [node for node in report["call_tree"] if node["function_id"] == odd_id]
        self.assertEqual(len(even_nodes), 2)
        self.assertEqual(len(odd_nodes), 1)
        self.assertEqual([ancestor_chain(node) for node in even_nodes], [[even_id, main_id], [even_id, odd_id, even_id, main_id]])
        self.assertEqual([ancestor_chain(node) for node in odd_nodes], [[odd_id, even_id, main_id]])

    def test_runtime_profile_accounting(self) -> None:
        source = self.root / "accounting"
        source.mkdir()
        harness = source / "harness.c"
        harness.write_text('''#include "yian_profile.h"
#include <time.h>
#include <stdint.h>

static const uint64_t times[] = {0, 10, 40, 50, 70, 80, 90, 100, 110, 150};
static size_t next_time = 0;
int yian_test_clock_gettime(clockid_t clock, struct timespec *value) {
    (void)clock;
    if (next_time >= sizeof(times) / sizeof(times[0])) { return -1; }
    uint64_t ns = times[next_time++];
    value->tv_sec = (time_t)(ns / 1000000000u);
    value->tv_nsec = (long)(ns % 1000000000u);
    return 0;
}

#define S(text) {(const uint8_t *)(text), sizeof(text) - 1u}
static const YianProfileFunction functions[] = {
    {1u, S("A"), S("probe"), S("harness.c"), S("/tmp/harness.c"), 1u, 1u, 0u, 0u, S(""), S(""), S("")},
    {2u, S("B"), S("probe"), S("harness.c"), S("/tmp/harness.c"), 2u, 1u, 0u, 0u, S(""), S(""), S("")},
    {3u, S("unused"), S("probe"), S("harness.c"), S("/tmp/harness.c"), 3u, 1u, 0u, 0u, S(""), S(""), S("")}
};
const YianProfileConfig __yian_profile_config = {
    YIAN_PROFILE_ABI_VERSION,
    S("."), S("probe-run"), S("/tmp/accounting/app"), S("fat"),
    1u, 3u, functions
};

int main(void) {
    __yian_profile_init();
    __yian_profile_enter(1u);
    __yian_profile_enter(2u);
    __yian_profile_leave(2u);
    __yian_profile_enter(2u);
    __yian_profile_leave(2u);
    __yian_profile_enter(1u);
    __yian_profile_enter(2u);
    __yian_profile_leave(2u);
    __yian_profile_leave(1u);
    __yian_profile_leave(1u);
    __yian_profile_finish();
    return next_time == 10u ? 0 : 2;
}
''', encoding="utf-8")
        collector = source / "profile.o"
        harness_object = source / "harness.o"
        executable = source / "probe"
        compiler = self.cc_driver()
        first = subprocess.run(
            [compiler, "-O2", "-I", str(ROOT / "runtime" / "include"), "-DYIAN_RUNTIME_PROFILE",
             "-Dclock_gettime=yian_test_clock_gettime", "-c", str(ROOT / "runtime" / "src" / "profile.c"),
             "-o", str(collector)],
            text=True, capture_output=True, check=False, timeout=120,
        )
        self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
        second = subprocess.run(
            [compiler, "-O2", "-I", str(ROOT / "runtime" / "include"), "-c", str(harness), "-o", str(harness_object)],
            text=True, capture_output=True, check=False, timeout=120,
        )
        self.assertEqual(second.returncode, 0, second.stdout + second.stderr)
        linked = subprocess.run(
            [compiler, str(collector), str(harness_object), "-o", str(executable)],
            text=True, capture_output=True, check=False, timeout=120,
        )
        self.assertEqual(linked.returncode, 0, linked.stdout + linked.stderr)
        executed = subprocess.run(
            [str(executable)], cwd=source, text=True, capture_output=True, check=False, timeout=60,
        )
        self.assertEqual(executed.returncode, 0, executed.stdout + executed.stderr)
        report = _read_json(source / "profile.json")
        self.assertEqual(report["kind"], "yian-runtime-profile")
        self.assertEqual(report["measurement"]["unit"], "ns")
        self.assertIsInstance(report["measurement"]["resolution_ns"], int)
        self.assertFalse(report["measurement"]["overhead_compensated"])
        functions = {item["display_name"]: item for item in report["functions"]}
        self.assertEqual(set(functions), {"A", "B"})
        self.assertEqual((functions["A"]["calls"], functions["A"]["inclusive_ns"], functions["A"]["self_ns"]), (2, 180, 90))
        self.assertEqual((functions["B"]["calls"], functions["B"]["inclusive_ns"], functions["B"]["self_ns"]), (3, 60, 60))
        nodes = report["call_tree"]
        self.assertEqual(len(nodes), 4)
        root_a = next(item for item in nodes if item["parent_id"] == 0)
        self.assertEqual((root_a["function_id"], root_a["calls"], root_a["inclusive_ns"], root_a["self_ns"]), (1, 1, 150, 70))
        direct_b = next(item for item in nodes if item["parent_id"] == root_a["node_id"] and item["function_id"] == 2)
        self.assertEqual((direct_b["calls"], direct_b["inclusive_ns"], direct_b["self_ns"]), (2, 50, 50))
        recursive_a = next(item for item in nodes if item["parent_id"] == root_a["node_id"] and item["function_id"] == 1)
        self.assertEqual((recursive_a["calls"], recursive_a["inclusive_ns"], recursive_a["self_ns"]), (1, 30, 20))
        recursive_b = next(item for item in nodes if item["parent_id"] == recursive_a["node_id"] and item["function_id"] == 2)
        self.assertEqual((recursive_b["calls"], recursive_b["inclusive_ns"], recursive_b["self_ns"]), (1, 10, 10))
        summary = (source / "summary.txt").read_text(encoding="utf-8")
        for value in ("self_ns=90", "inclusive_ns=180", "self_ns=70", "inclusive_ns=150", "inclusive_ns=30", "inclusive_ns=10"):
            self.assertIn(value, summary)
        self.assertIn("overhead_compensated: false", summary)
        self.assertIn("resolution_ns:", summary)

    def test_runtime_profile_boundaries(self) -> None:
        probe = self.root / "boundary"
        probe.mkdir()
        source = probe / "main.an"
        source.write_text("fn main() {}\n", encoding="utf-8")
        ordinary = probe / "ordinary.ll"
        compiled = subprocess.run(
            [str(self.yianc), "--compiler-root", str(ROOT), "-t", "ll", "-o", str(ordinary),
             str(ROOT / "lib" / "src"), str(source)],
            cwd=ROOT, env=self.environment, text=True, capture_output=True, check=False, timeout=180,
        )
        self.assertEqual(compiled.returncode, 0, compiled.stdout + compiled.stderr)
        ir = ordinary.read_text(encoding="utf-8")
        self.assertNotIn("__yian_profile", ir)
        self.assertNotIn("disable-tail-calls", ir)
        self.assertNotIn("nomerge", ir)

        compiler = self.cc_driver()
        runtime_object = probe / "runtime.o"
        profile_object = probe / "profile.o"
        normal_runtime = subprocess.run(
            [compiler, "-O2", "-fno-strict-aliasing", "-I", str(ROOT / "runtime" / "include"),
             "-c", str(ROOT / "runtime" / "src" / "runtime.c"), "-o", str(runtime_object)],
            text=True, capture_output=True, check=False, timeout=120,
        )
        self.assertEqual(normal_runtime.returncode, 0, normal_runtime.stdout + normal_runtime.stderr)
        normal_profile = subprocess.run(
            [compiler, "-O2", "-I", str(ROOT / "runtime" / "include"),
             "-c", str(ROOT / "runtime" / "src" / "profile.c"), "-o", str(profile_object)],
            text=True, capture_output=True, check=False, timeout=120,
        )
        self.assertEqual(normal_profile.returncode, 0, normal_profile.stdout + normal_profile.stderr)
        symbols = shutil.which("llvm-nm") or shutil.which("nm")
        self.assertIsNotNone(symbols)
        runtime_symbols = subprocess.run(
            [str(symbols), str(runtime_object)], text=True, capture_output=True, check=False, timeout=60,
        )
        self.assertEqual(runtime_symbols.returncode, 0, runtime_symbols.stdout + runtime_symbols.stderr)
        self.assertNotIn("__yian_profile", runtime_symbols.stdout)
        profile_symbols = subprocess.run(
            [str(symbols), str(profile_object)], text=True, capture_output=True, check=False, timeout=60,
        )
        self.assertEqual(profile_symbols.returncode, 0, profile_symbols.stdout + profile_symbols.stderr)
        self.assertNotIn("__yian_profile", profile_symbols.stdout)

        for arguments in (
            ["--check", "--runtime-profile-dir", str(probe)],
            ["-t", "ll", "--runtime-profile-dir", str(probe)],
            ["--runtime-profile-dir", "relative/path"],
            ["--runtime-profile-dir", ""],
        ):
            rejected = subprocess.run(
                [str(self.yianc), *arguments, str(ROOT / "lib" / "src"), str(source)],
                cwd=probe, env=self.environment, text=True, capture_output=True, check=False, timeout=120,
            )
            self.assertNotEqual(rejected.returncode, 0, arguments)
            self.assertIn("--runtime-profile-dir", rejected.stderr, arguments)

        lowerer = (ROOT / "yianc" / "src" / "codegen" / "llvm" / "profile.an").read_text(encoding="utf-8")
        self.assertIn('add_attribute("noinline")', lowerer)
        self.assertIn('add_attribute("nomerge")', lowerer)
        self.assertIn('add_string_attribute("disable-tail-calls", "true")', lowerer)
        session = (ROOT / "yianc" / "src" / "codegen" / "session.an").read_text(encoding="utf-8")
        self.assertIn("set_merge_functions(false)", session)
        runner = (ROOT / "runtime" / "src" / "runtime.c").read_text(encoding="utf-8")
        self.assertIn("#ifdef YIAN_RUNTIME_PROFILE", runner)
        guarded = runner.split("#ifdef YIAN_RUNTIME_PROFILE", 1)[1]
        self.assertIn("__yian_profile_init();", guarded)
        self.assertIn("__yian_main();", guarded)
        self.assertIn("__yian_profile_finish();", guarded)
        self.assertLess(guarded.index("__yian_main();"), guarded.index("__yian_profile_finish();"))

    def cc_driver(self) -> str:
        override = os.environ.get("YIAN_CC")
        if override:
            return override
        for name in ("clang", "cc"):
            found = shutil.which(name)
            if found:
                return found
        raise AssertionError("no C compiler is available for the runtime profile probe")



if __name__ == "__main__":
    unittest.main()
