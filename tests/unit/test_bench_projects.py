"""Focused tests for shared benchmark project staging and build contracts."""

from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
from subprocess import CompletedProcess
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from bench import projects


class BenchmarkProjectTests(unittest.TestCase):
    def _fixture(self, root: Path) -> tuple[Path, Path, Path]:
        bench = root / "bench with spaces"
        project = bench / "AWFY" / "probe"
        yian = project / "yian"
        (yian / "src").mkdir(parents=True)
        (project / "c" / "src").mkdir(parents=True)
        (project / "fixtures").mkdir()
        helper = bench / "native" / "helper"
        (helper / "src").mkdir(parents=True)

        (project / "spec.json").write_text(
            json.dumps({"source": "AWFY", "name": "probe"}), encoding="utf-8"
        )
        (yian / "package.anx").write_text(
            '[package]\nname = "probe"\nkind = "bin"\n\n'
            '[dependencies]\nhelper = { path = "../../../native/helper" }\n',
            encoding="utf-8",
        )
        (yian / "src" / "main.an").write_text(
            "let N: u64 = 4;  // bench-scale\nfn main() {}\n", encoding="utf-8"
        )
        (yian / "src" / "extra.an").write_text("fn extra() {}\n", encoding="utf-8")
        (yian / "src" / "obsolete.an").write_text("fn obsolete() {}\n", encoding="utf-8")
        (project / "raw" / "src").mkdir(parents=True)
        (project / "raw" / "src" / "main.an").write_text(
            "let N: u64 = 8;  // bench-scale\nfn main() {}\n", encoding="utf-8"
        )
        (project / "c" / "CMakeLists.txt").write_text(
            "cmake_minimum_required(VERSION 3.16)\n"
            "project(probe C)\n"
            "option(BENCH_ASAN \"Build with AddressSanitizer\" OFF)\n"
            "add_executable(bench_app src/main.c)\n"
            "if(BENCH_ASAN)\n"
            "  target_compile_options(bench_app PRIVATE -fsanitize=address -fno-omit-frame-pointer)\n"
            "  target_link_options(bench_app PRIVATE -fsanitize=address)\n"
            "endif()\n",
            encoding="utf-8",
        )
        (project / "c" / "src" / "main.c").write_text("int main(void) { return 0; }\n", encoding="utf-8")
        (project / "fixtures" / "bytes.bin").write_bytes(b"\x00\xfffixture")
        (helper / "package.anx").write_text(
            '[package]\nname = "helper"\nkind = "lib"\n', encoding="utf-8"
        )
        (helper / "src" / "math.an").write_text("fn answer() -> i64 { 42 }\n", encoding="utf-8")
        return bench, project, helper

    @staticmethod
    def _tree_digest(root: Path) -> str:
        digest = hashlib.sha256()
        for path in sorted(item for item in root.rglob("*") if item.is_file()):
            digest.update(path.relative_to(root).as_posix().encode())
            digest.update(path.read_bytes())
        return digest.hexdigest()

    def test_project_tree_and_scale_isolation(self) -> None:
        with tempfile.TemporaryDirectory(prefix="project fixture with spaces ") as temporary:
            root = Path(temporary)
            bench, project_root, helper_root = self._fixture(root)
            before = self._tree_digest(bench)
            selected, = projects.discover_projects(bench)
            self.assertEqual(selected.key, "AWFY/probe")
            self.assertEqual(selected.raw_overlay, project_root / "raw")
            initial_digest = projects.project_digest(selected)

            build_root = root / "build output with spaces"
            staged = projects.materialize_yian(selected, "fast", True, 11, build_root)
            self.assertEqual((staged / "src" / "main.an").read_text(), "let N: u64 = 11;  // bench-scale\nfn main() {}\n")
            self.assertTrue((staged / "src" / "extra.an").is_file())
            self.assertTrue((staged / "src" / "obsolete.an").is_file())
            staged_bench = staged.parents[2]
            self.assertTrue((staged_bench / "native" / "helper" / "package.anx").is_file())
            self.assertEqual(self._tree_digest(bench), before)
            self.assertEqual(projects.project_digest(selected), initial_digest)

            (helper_root / "src" / "math.an").write_text("fn answer() -> i64 { 43 }\n", encoding="utf-8")
            self.assertNotEqual(projects.project_digest(selected), initial_digest)
            (project_root / "yian" / "src" / "obsolete.an").unlink()
            staged_stale = staged / "src" / "stale.an"
            staged_stale.write_text("old stage", encoding="utf-8")
            rebuilt_stage = projects.materialize_yian(selected, "fast", True, 11, build_root)
            self.assertFalse((rebuilt_stage / "src" / "obsolete.an").exists())
            self.assertFalse(staged_stale.exists())

            manifest = project_root / "yian" / "package.anx"
            valid_manifest = manifest.read_text(encoding="utf-8")
            outside = root / "outside package"
            (outside / "src").mkdir(parents=True)
            (outside / "package.anx").write_text('[package]\nname = "outside"\nkind = "lib"\n', encoding="utf-8")
            manifest.write_text(valid_manifest.replace("../../../native/helper", "../../../../outside package"), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "AWFY/probe.*escapes benchmark root"):
                projects.materialize_yian(selected, "fast", True, 11, build_root)
            manifest.write_text(valid_manifest, encoding="utf-8")

            (project_root / "raw" / "src" / "escape.an").symlink_to(outside / "package.anx")
            with self.assertRaisesRegex(ValueError, "escapes benchmark root"):
                projects.materialize_yian(selected, "fast", True, 11, build_root)
            (project_root / "raw" / "src" / "escape.an").unlink()

            source = project_root / "yian" / "src" / "main.an"
            valid_source = source.read_text(encoding="utf-8")
            source.write_text(valid_source.replace("// bench-scale", "// no scale marker"), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "expected exactly one.*found 0"):
                projects.materialize_yian(selected, "fast", False, 11, build_root)

    def test_native_project_build_contract(self) -> None:
        with tempfile.TemporaryDirectory(prefix="native project fixture with spaces ") as temporary:
            root = Path(temporary)
            bench, _, _ = self._fixture(root)
            project, = projects.discover_projects(bench)
            tool_dir = root / "tool path with spaces"
            tool_dir.mkdir()
            clang = tool_dir / "clang"
            cmake = tool_dir / "cmake"
            yianc = tool_dir / "yianc"
            anx = tool_dir / "anx"
            for tool in (clang, cmake, yianc, anx):
                tool.write_text("", encoding="utf-8")
            compiler_root = root / "compiler source"
            (compiler_root / "lib" / "src").mkdir(parents=True)
            output_root = root / "build artifacts with spaces"
            output_root.mkdir()
            calls: list[tuple[list[str], dict[str, object]]] = []

            def run(command: list[str], **kwargs: object) -> CompletedProcess[str]:
                calls.append((command, kwargs))
                if command[0] == str(anx) and command[1] == "build":
                    app = Path(kwargs["cwd"]) / "build" / "app"
                    app.parent.mkdir(parents=True, exist_ok=True)
                    app.write_bytes(Path(kwargs["cwd"]).parents[3].name.encode("utf-8"))
                elif command[0] == str(cmake) and command[1] == "--build":
                    app = Path(command[2]) / "bench_app"
                    app.parent.mkdir(parents=True, exist_ok=True)
                    app.write_bytes(b"c-app")
                return CompletedProcess(command, 0, "", "")

            def which(name: str) -> str | None:
                return str(cmake if name == "cmake" else clang)

            with patch.object(projects.shutil, "which", side_effect=which), patch.object(projects.subprocess, "run", side_effect=run):
                fat_output = projects.build_yian(project, "fast", False, 5, yianc, anx, compiler_root, output_root / "fat")
                raw_output = projects.build_yian(project, "fast", True, 5, yianc, anx, compiler_root, output_root / "raw")
                plain_output = projects.build_c(project, False, output_root / "c-plain")
                asan_output = projects.build_c(project, True, output_root / "c-asan")

            self.assertEqual(fat_output.read_bytes(), b"fat")
            self.assertEqual(raw_output.read_bytes(), b"raw")
            self.assertEqual(plain_output.read_bytes(), b"c-app")
            self.assertEqual(asan_output.read_bytes(), b"c-app")
            yian_calls = [(command, options) for command, options in calls if command[0] == str(anx)]
            self.assertEqual(len(yian_calls), 2)
            self.assertIn("-O2", yian_calls[0][0])
            self.assertNotIn("--raw-pointers", yian_calls[0][0])
            self.assertIn("--raw-pointers", yian_calls[1][0])
            self.assertNotEqual(yian_calls[0][1]["cwd"], yian_calls[1][1]["cwd"])
            for _, options in calls:
                environment = options["env"]
                self.assertNotIn("CFLAGS", environment)
                self.assertNotIn("CPPFLAGS", environment)
                self.assertNotIn("LDFLAGS", environment)
            for command, options in yian_calls:
                self.assertEqual(options["env"]["YIANC"], str(yianc.resolve()))
                self.assertEqual(options["env"]["YIAN_LIB"], str((compiler_root / "lib" / "src").resolve()))
                self.assertEqual(options["env"]["YIAN_CC"], str(clang.resolve()))
            configure_calls = [command for command, _ in calls if command[0] == str(cmake) and command[1] == "-S"]
            self.assertEqual(len(configure_calls), 2)
            self.assertTrue(all(f"-DCMAKE_C_COMPILER={clang.resolve()}" in command for command in configure_calls))
            self.assertTrue(all("-O2 -ffp-contract=off" in next(arg for arg in command if arg.startswith("-DCMAKE_C_FLAGS=")) for command in configure_calls))
            self.assertTrue(all("-O3" not in " ".join(command) and "-DNDEBUG" not in " ".join(command) and "-flto" not in " ".join(command) for command in configure_calls))
            self.assertIn("-DBENCH_ASAN=OFF", configure_calls[0])
            self.assertIn("-DBENCH_ASAN=ON", configure_calls[1])
            cmake_text = (project.c / "CMakeLists.txt").read_text(encoding="utf-8")
            self.assertIn("target_compile_options(bench_app PRIVATE -fsanitize=address -fno-omit-frame-pointer)", cmake_text)
            self.assertIn("target_link_options(bench_app PRIVATE -fsanitize=address)", cmake_text)
            self.assertTrue(all("--target" in command and "bench_app" in command for command, _ in calls if command[0] == str(cmake) and command[1] == "--build"))
            self.assertNotEqual(
                next(Path(command[2]) for command, _ in calls if command[0] == str(cmake) and command[1] == "--build"),
                next(Path(command[2]) for command, _ in reversed(calls) if command[0] == str(cmake) and command[1] == "--build"),
            )

            stale_yian = output_root / "failed-yian"
            stale_yian.write_bytes(b"old app")
            stale_c = output_root / "failed-c"
            stale_c.write_bytes(b"old app")
            failure = CompletedProcess(["failed"], 9, "mock stdout", "mock stderr")
            with patch.object(projects.shutil, "which", side_effect=which), patch.object(projects.subprocess, "run", return_value=failure):
                with self.assertRaises(RuntimeError) as yian_error:
                    projects.build_yian(project, "fast", False, 5, yianc, anx, compiler_root, stale_yian)
                self.assertIn("exit 9", str(yian_error.exception))
                self.assertIn("mock stdout", str(yian_error.exception))
                self.assertIn("mock stderr", str(yian_error.exception))
                with self.assertRaises(RuntimeError) as c_error:
                    projects.build_c(project, False, stale_c)
                self.assertIn("exit 9", str(c_error.exception))
                self.assertIn("mock stdout", str(c_error.exception))
                self.assertIn("mock stderr", str(c_error.exception))
            self.assertFalse(stale_yian.exists())
            self.assertFalse(stale_c.exists())


if __name__ == "__main__":
    unittest.main()
