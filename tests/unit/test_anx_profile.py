"""Focused native integration probes for anx/compiler profiling reports."""

from __future__ import annotations

import copy
import json
import os
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


class AnxProfileTests(unittest.TestCase):
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

    def make_project(self, *, main: str, with_test: bool = False) -> Path:
        project = self.root / "project"
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
        return subprocess.run(
            [str(self.anx), *arguments],
            cwd=project,
            env=self.environment,
            text=True,
            capture_output=True,
            check=False,
            timeout=180,
        )

    def profile_runs(self, project: Path) -> list[Path]:
        profile_root = project / "build" / "profile"
        if not profile_root.exists():
            return []
        return sorted(path for path in profile_root.iterdir() if path.is_dir())

    def test_run_and_disabled_build(self) -> None:
        project = self.make_project(
            main='from std.core.env import exit;\nfn main() { print("profile-run\\n"); exit(7); }\n'
        )
        run = self.run_anx(project, "run", "--profile", "-O1")
        self.assertEqual(run.returncode, 7, run.stderr)
        self.assertEqual(run.stdout, "profile-run\n")
        runs = self.profile_runs(project)
        self.assertEqual(len(runs), 1)
        report_path = runs[0] / "profile.json"
        summary_path = runs[0] / "summary.txt"
        self.assertTrue(report_path.is_file())
        self.assertTrue(summary_path.is_file())
        report = _read_json(report_path)
        self.assertEqual(report["format"], 1)
        self.assertEqual(report["kind"], "anx-build-profile")
        self.assertEqual(report["command"], "run")
        self.assertEqual(report["build_outcome"], "ok")
        self.assertEqual(report["command_exit_code"], 7)
        self.assertGreater(report["build_total_ns"], 0)
        self.assertIn("build total:", summary_path.read_text(encoding="utf-8"))
        self.assertEqual(len(report["invocations"]), 1)
        invocation = report["invocations"][0]
        self.assertEqual(invocation["compiler_status"], 0)
        self.assertEqual(invocation["report_status"], "ok")
        compiler_report_path = Path(invocation["profile_file"])
        self.assertTrue(compiler_report_path.is_file())
        compiler_report = _read_json(compiler_report_path)
        compiler_passes = _by_pass(compiler_report["timings"])
        self.assertGreater(compiler_passes["llvm.optimize"]["calls"], 0)
        self.assertGreater(compiler_passes["llvm.emit_native"]["calls"], 0)
        self.assertGreater(compiler_passes["native.link"]["calls"], 0)

        previous_report = report_path.read_bytes()
        previous_summary = summary_path.read_bytes()
        build = self.run_anx(project, "build", "-O1")
        self.assertEqual(build.returncode, 0, build.stderr)
        self.assertTrue((project / "build" / "app").is_file())
        self.assertEqual(self.profile_runs(project), runs)
        self.assertEqual(report_path.read_bytes(), previous_report)
        self.assertEqual(summary_path.read_bytes(), previous_summary)

    def test_check_failure(self) -> None:
        project = self.make_project(main='fn main() { let value: i32 = "bad"; }\n')
        checked = self.run_anx(project, "check", "--profile")
        self.assertEqual(checked.returncode, 1)
        self.assertIn("Expected type 'i32' but got 'str'", checked.stderr)
        runs = self.profile_runs(project)
        self.assertEqual(len(runs), 1)
        report = _read_json(runs[0] / "profile.json")
        self.assertEqual(report["build_outcome"], "failed")
        self.assertEqual(report["command_exit_code"], 1)
        self.assertEqual(len(report["invocations"]), 1)
        invocation = report["invocations"][0]
        self.assertNotEqual(invocation["compiler_status"], 0)
        self.assertEqual(invocation["report_status"], "ok")
        compiler_report = _read_json(Path(invocation["profile_file"]))
        self.assertFalse(compiler_report["ok"])
        self.assertFalse(
            any(item["pass_id"] == "llvm.emit_native" and item["calls"] for item in compiler_report["timings"])
        )

    def test_expected_error_both_modes(self) -> None:
        project = self.make_project(
            main='fn main() {}\n', with_test=True
        )
        tested = self.run_anx(project, "test", "--profile", "--pointer-mode", "both")
        self.assertEqual(tested.returncode, 0, tested.stdout + tested.stderr)
        self.assertIn("[ok] error/bad.err.an", tested.stdout)
        runs = self.profile_runs(project)
        self.assertEqual(len(runs), 1)
        report = _read_json(runs[0] / "profile.json")
        self.assertEqual(report["command"], "test")
        self.assertEqual(report["build_outcome"], "ok")
        self.assertEqual(report["command_exit_code"], 0)
        self.assertEqual(len(report["invocations"]), 2)
        self.assertEqual(
            {item["pointer_mode"] for item in report["invocations"]}, {"fat", "raw"}
        )
        self.assertEqual(
            len({item["profile_file"] for item in report["invocations"]}), 2
        )
        for invocation in report["invocations"]:
            self.assertEqual(invocation["test_path"], "error/bad.err.an")
            self.assertNotEqual(invocation["compiler_status"], 0)
            self.assertEqual(invocation["report_status"], "ok")
            compiler_report = _read_json(Path(invocation["profile_file"]))
            self.assertFalse(compiler_report["ok"])
            self.assertFalse(
                any(item["pass_id"] == "native.link" and item["calls"] for item in compiler_report["timings"])
            )

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


if __name__ == "__main__":
    unittest.main()
