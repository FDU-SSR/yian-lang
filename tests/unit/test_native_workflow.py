"""Script-only tests for strict native tool selection and daily entrypoints."""

from __future__ import annotations

import hashlib
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

import check_lsp
import run_tests
from _toolchain.native import NativeToolError, file_sha256, resolve_native, resolve_native_selection
from bench import bench_allocator, bench_three_way


class NativeWorkflowTests(unittest.TestCase):
    def test_native_compiler_resolution(self) -> None:
        system_elf = Path(sys.executable).resolve(strict=True)
        with tempfile.TemporaryDirectory(prefix="native tool path with spaces ") as temporary:
            root = Path(temporary)
            link = root / "compiler link with spaces"
            link.symlink_to(system_elf)
            self.assertEqual(resolve_native(str(link)), system_elf)

            search_dir = root / "search path"
            search_dir.mkdir()
            (search_dir / "yianc").symlink_to(system_elf)
            self.assertEqual(
                resolve_native("yianc", search_path=str(search_dir)), system_elf
            )

            marker = root / "executed sentinel"
            script = root / "compiler script"
            script.write_text(
                f"#!/bin/sh\nprintf called > {marker}\n",
                encoding="utf-8",
            )
            script.chmod(0o755)
            with self.assertRaisesRegex(NativeToolError, "not an ELF"):
                resolve_native(str(script))
            self.assertFalse(marker.exists(), "identity checking must not execute a script")

            non_executable = root / "no execute permission"
            non_executable.write_text("data", encoding="utf-8")
            non_executable.chmod(0o600)
            with self.assertRaisesRegex(NativeToolError, "not executable"):
                resolve_native(str(non_executable))

            with self.assertRaisesRegex(NativeToolError, "not a regular file"):
                resolve_native(str(root))

            dangling = root / "dangling compiler"
            dangling.symlink_to(root / "missing target")
            with self.assertRaises(NativeToolError):
                resolve_native(str(dangling))

    def test_test_entrypoints_never_refresh_native(self) -> None:
        system_elf = Path(sys.executable).resolve(strict=True)
        with tempfile.TemporaryDirectory(prefix="native workflow prefix ") as temporary:
            root = Path(temporary)
            prefix = root / "installed tools with spaces"
            native_bin = prefix / "bin"
            native_bin.mkdir(parents=True)
            for name in ("yianc", "anx", "yian-lsp"):
                (native_bin / name).symlink_to(system_elf)

            with patch.object(run_tests, "run_suite", return_value=0) as run_suite, patch.object(
                run_tests.subprocess,
                "run",
                side_effect=AssertionError("the test entrypoint must not build or refresh tools"),
            ) as run_process, redirect_stdout(StringIO()):
                result = run_tests.main([
                    "--suite", "package", "--prefix", str(prefix), "-q",
                ])
            self.assertEqual(result, 0)
            self.assertEqual(run_suite.call_count, 1)
            toolchain = run_suite.call_args.args[2]
            self.assertEqual(toolchain.name, "native")
            self.assertEqual(toolchain.compiler, (str(system_elf),))
            self.assertEqual(toolchain.anx, (str(system_elf),))
            self.assertEqual(toolchain.environment["YIANC"], str(system_elf))
            self.assertEqual(
                toolchain.environment.get("PYTHONPATH"), os.environ.get("PYTHONPATH")
            )
            run_process.assert_not_called()

            with patch.object(run_tests, "run_suite", return_value=0) as native_alias, patch.object(
                run_tests.subprocess,
                "run",
                side_effect=AssertionError("the native compatibility alias must not build tools"),
            ) as run_process, redirect_stdout(StringIO()):
                result = run_tests.main([
                    "--suite", "package", "--implementation", "native", "--prefix", str(prefix), "-q",
                ])
            self.assertEqual(result, 0)
            self.assertEqual(native_alias.call_count, 1)
            run_process.assert_not_called()

            with patch.object(run_tests, "run_suite", return_value=0) as selected_raw, patch.object(
                run_tests.subprocess,
                "run",
                side_effect=AssertionError("pointer-mode selection must not build tools"),
            ) as run_process, redirect_stdout(StringIO()):
                result = run_tests.main([
                    "--suite", "basic", "--pointer-mode", "raw", "--prefix", str(prefix), "-q",
                ])
            self.assertEqual(result, 0)
            self.assertEqual(selected_raw.call_count, 1)
            self.assertEqual(selected_raw.call_args.args[1].pointer_mode, "raw")
            run_process.assert_not_called()

            for legacy in ("python", "both"):
                with patch.object(run_tests, "run_suite") as no_suite, redirect_stderr(StringIO()):
                    with self.assertRaises(SystemExit) as error:
                        run_tests.main(["--implementation", legacy, "--prefix", str(root / "empty")])
                self.assertEqual(error.exception.code, 2)
                no_suite.assert_not_called()

            with patch.object(run_tests, "run_suite") as no_suite, patch.object(
                run_tests.subprocess,
                "run",
                side_effect=AssertionError("missing tools must not launch a build"),
            ) as run_process, redirect_stdout(StringIO()), redirect_stderr(StringIO()) as errors:
                result = run_tests.main([
                    "--suite", "basic", "--implementation", "native", "--prefix", str(root / "missing"),
                ])
            self.assertEqual(result, 1)
            self.assertIn("scripts/bootstrap_toolchain.sh", errors.getvalue())
            self.assertIn("cannot resolve native tool", errors.getvalue())
            no_suite.assert_not_called()
            run_process.assert_not_called()

            missing_lsp = root / "missing LSP install"
            with patch.object(check_lsp.subprocess, "run", side_effect=AssertionError("must not refresh")) as run_process, redirect_stderr(StringIO()) as errors:
                result = check_lsp.main(["--mode", "infrastructure", "--prefix", str(missing_lsp)])
            self.assertEqual(result, 1)
            self.assertIn("scripts/bootstrap_toolchain.sh", errors.getvalue())
            self.assertIn("scripts/refresh_toolchain.sh", errors.getvalue())
            run_process.assert_not_called()

            seeded = root / "seeded tools"
            seeded_bin = seeded / "bin"
            seeded_bin.mkdir(parents=True)
            for name in ("yianc", "anx"):
                (seeded_bin / name).symlink_to(system_elf)
            with patch.object(check_lsp.subprocess, "run", side_effect=AssertionError("must not refresh")) as run_process, redirect_stderr(StringIO()) as errors:
                result = check_lsp.main(["--mode", "infrastructure", "--prefix", str(seeded)])
            self.assertEqual(result, 1)
            self.assertIn("scripts/refresh_toolchain.sh", errors.getvalue())
            self.assertNotIn("scripts/bootstrap_toolchain.sh", errors.getvalue())
            run_process.assert_not_called()

    def test_bench_compiler_selection_and_identity(self) -> None:
        system_elf = Path(sys.executable).resolve(strict=True)
        with tempfile.TemporaryDirectory(prefix="native benchmark selection ") as temporary:
            root = Path(temporary)
            explicit = root / "explicit compiler"
            configured = root / "environment compiler"
            explicit.symlink_to(system_elf)
            configured.symlink_to(system_elf)
            path_dir = root / "path"
            path_dir.mkdir()
            (path_dir / "yianc").symlink_to(system_elf)

            selected, source = resolve_native_selection(
                explicit=str(explicit), configured=str(configured), default="yianc", search_path=str(path_dir)
            )
            self.assertEqual((selected, source), (system_elf, "explicit"))
            selected, source = resolve_native_selection(
                explicit=None, configured=str(configured), default="yianc", search_path=str(path_dir)
            )
            self.assertEqual((selected, source), (system_elf, "YIANC"))
            selected, source = resolve_native_selection(
                explicit=None, configured="", default="yianc", search_path=str(path_dir)
            )
            self.assertEqual((selected, source), (system_elf, "PATH"))
            self.assertEqual(file_sha256(system_elf), hashlib.sha256(system_elf.read_bytes()).hexdigest())

            marker = root / "invalid compiler executed"
            invalid = root / "invalid configured compiler"
            invalid.write_text(f"#!/bin/sh\nprintf called > {marker}\n", encoding="utf-8")
            invalid.chmod(0o755)
            with self.assertRaisesRegex(NativeToolError, "not an ELF"):
                resolve_native_selection(
                    explicit=None,
                    configured=str(invalid),
                    default="yianc",
                    search_path=str(path_dir),
                )
            self.assertFalse(marker.exists(), "an invalid configured compiler must not fall back or execute")

            with patch.object(bench_three_way, "_tool_version", return_value="clang"), patch.object(
                bench_three_way, "_git_head", return_value="abc123"
            ):
                fingerprint = bench_three_way.machine_fingerprint(
                    None, 1, "fast", "fast", system_elf, "explicit", True
                )
            self.assertEqual(fingerprint["yianc"], str(system_elf))
            self.assertEqual(fingerprint["yianc_sha256"], file_sha256(system_elf))
            self.assertEqual(fingerprint["yianc_source"], "explicit")
            self.assertEqual(fingerprint["yianc_used"], "yes")

    def test_bench_compilers_use_native_fat_raw_argv(self) -> None:
        compiler = Path(sys.executable).resolve(strict=True)
        with tempfile.TemporaryDirectory(prefix="native benchmark argv ") as temporary:
            root = Path(temporary)
            lib_root = root / "source tree with spaces" / "lib" / "src"
            lib_root.mkdir(parents=True)
            source = root / "case.an"
            source.write_text("", encoding="utf-8")
            raw_source = root / "case.raw.an"
            raw_source.write_text("", encoding="utf-8")

            three_output = root / "three way output"
            three_spec = SimpleNamespace(key="BG/case")
            fake_success = SimpleNamespace(returncode=0, stdout="", stderr="")
            with patch.object(bench_three_way, "spec_bin", return_value=three_output), patch.object(
                bench_three_way,
                "materialize",
                return_value=(source, raw_source),
            ), patch.object(bench_three_way.subprocess, "run", return_value=fake_success) as process:
                bench_three_way.compile_an(three_spec, "fast", raw=False, compiler=compiler)
                fat_command = process.call_args.args[0]
                self.assertEqual(fat_command[:2], [str(compiler), bench_three_way.OPT_LEVEL])
                self.assertNotIn("--raw-pointers", fat_command)
                bench_three_way.compile_an(three_spec, "fast", raw=True, compiler=compiler)
                raw_command = process.call_args.args[0]
                self.assertEqual(raw_command[:2], [str(compiler), bench_three_way.OPT_LEVEL])
                self.assertIn("--raw-pointers", raw_command)
                self.assertNotIn("yianc.main", raw_command)
                self.assertEqual(
                    process.call_args.kwargs["env"].get("PYTHONPATH"),
                    os.environ.get("PYTHONPATH"),
                )

            allocator_output = root / "allocator output"
            with patch.object(bench_allocator, "BUILD_DIR", allocator_output), patch.object(
                bench_allocator, "source_path", return_value=source
            ), patch.object(bench_allocator.subprocess, "run", return_value=fake_success) as process:
                bench_allocator.compile_one(
                    "case", False, lib_root.parents[1], {}, "full", compiler
                )
                fat_command = process.call_args.args[0]
                self.assertEqual(fat_command[:2], [str(compiler), bench_allocator.OPT_LEVEL])
                self.assertIn(str(lib_root), fat_command)
                self.assertNotIn("--raw-pointers", fat_command)
                bench_allocator.compile_one(
                    "case", True, lib_root.parents[1], {}, "full", compiler
                )
                raw_command = process.call_args.args[0]
                self.assertEqual(raw_command[:2], [str(compiler), bench_allocator.OPT_LEVEL])
                self.assertIn("--raw-pointers", raw_command)
                self.assertNotIn("yianc.main", raw_command)
                self.assertEqual(
                    process.call_args.kwargs["env"].get("PYTHONPATH"),
                    os.environ.get("PYTHONPATH"),
                )

    def test_runner_pointer_modes_and_safety_na_reporting(self) -> None:
        toolchain = SimpleNamespace(name="native")
        case = run_tests.TestCase(
            name="smoke.an",
            source_files=[],
            expect_error=False,
            expected_substring="",
        )

        def args(pointer_mode: str) -> SimpleNamespace:
            return SimpleNamespace(
                pointer_mode=pointer_mode,
                include_experimental=False,
                filter_str=None,
                no_run=False,
                dump=False,
                quiet=True,
                verbose=False,
            )

        def success(test: run_tests.TestCase, *_: object, **__: object) -> run_tests.TestResult:
            return run_tests.TestResult(
                test,
                0 if test.expected_exit_code is None else test.expected_exit_code,
                1.0,
                test.expected_output,
                check_stdout=bool(test.expected_output),
            )

        with patch.object(run_tests, "discover_tests", return_value=[case]), patch.object(
            run_tests, "run_test", side_effect=success
        ) as run_test, redirect_stdout(StringIO()):
            result = run_tests.run_suite("basic", args("both"), toolchain)
        self.assertEqual(result, 0)
        self.assertEqual(
            [call.args[0].name for call in run_test.call_args_list],
            ["smoke.an@fat", "smoke.an@raw"],
        )
        self.assertNotIn("--raw-pointers", run_test.call_args_list[0].args[0].compiler_args)
        self.assertIn("--raw-pointers", run_test.call_args_list[1].args[0].compiler_args)

        def fat_failure(test: run_tests.TestCase, *_: object, **__: object) -> run_tests.TestResult:
            return run_tests.TestResult(
                test, 1 if test.name.endswith("@fat") else 0, 1.0, "expected output"
            )

        with tempfile.TemporaryDirectory(prefix="native pointer failure ") as temporary:
            with patch.object(run_tests, "ROOT_DIR", Path(temporary)), patch.object(
                run_tests, "discover_tests", return_value=[case]
            ), patch.object(run_tests, "run_test", side_effect=fat_failure) as run_test, redirect_stdout(StringIO()):
                result = run_tests.run_suite("basic", args("both"), toolchain)
        self.assertEqual(result, 1)
        self.assertEqual(run_test.call_count, 2)

        with tempfile.TemporaryDirectory(prefix="native package pointer modes ") as temporary:
            package_case = run_tests.TestCase(
                name="package_tests",
                source_files=[],
                expect_error=False,
                expected_substring="",
                package_root=Path(temporary),
                anx_command="test",
            )
            graph_case = run_tests.TestCase(
                name="package_graph",
                source_files=[],
                expect_error=False,
                expected_substring="",
                package_root=Path(temporary),
                anx_command="graph",
            )
            check_case = run_tests.TestCase(
                name="package_check",
                source_files=[],
                expect_error=False,
                expected_substring="",
                package_root=Path(temporary),
                anx_command="check",
            )

            def package_success(test: run_tests.TestCase, *_: object, **__: object) -> run_tests.TestResult:
                return run_tests.TestResult(test, 0, 1.0, "", compile_only=True)

            with patch.object(
                run_tests,
                "discover_tests",
                return_value=[package_case, graph_case, check_case],
            ), patch.object(
                run_tests, "run_package_test", side_effect=package_success
            ) as package_test, redirect_stdout(StringIO()):
                result = run_tests.run_suite("package", args("both"), toolchain)
            self.assertEqual(result, 0)
            self.assertEqual(package_test.call_count, 5)
            self.assertEqual(
                [call.args[0].anx_args for call in package_test.call_args_list],
                [
                    ["--pointer-mode", "fat"],
                    ["--pointer-mode", "raw"],
                    [],
                    [],
                    ["--raw-pointers"],
                ],
            )
            self.assertEqual(
                [call.args[0].name for call in package_test.call_args_list],
                [
                    "package_tests@fat",
                    "package_tests@raw",
                    "package_graph",
                    "package_check@fat",
                    "package_check@raw",
                ],
            )

        fat_uaf_source = ROOT / "tests/safety/fat_uaf.an"
        fat_uaf_expectation = ROOT / "tests/output/safety/fat_uaf.an.ans"
        parse_ans = getattr(run_tests, "__parse_ans")
        expect_error, exit_code, expected_output, expected_substring = parse_ans(
            fat_uaf_expectation
        )
        fat_uaf_case = run_tests.TestCase(
            name="fat_uaf.an",
            source_files=[fat_uaf_source],
            expect_error=expect_error,
            expected_substring=expected_substring,
            expected_exit_code=exit_code,
            expected_output=expected_output,
        )
        with patch.object(run_tests, "discover_tests", return_value=[fat_uaf_case]), patch.object(
            run_tests, "run_test", side_effect=success
        ) as run_test, redirect_stdout(StringIO()) as output:
            result = run_tests.run_suite("safety", args("both"), toolchain)
        self.assertEqual(result, 0)
        self.assertEqual(run_test.call_count, 1)
        scheduled_fat = run_test.call_args.args[0]
        self.assertEqual(scheduled_fat.name, "fat_uaf.an@fat")
        self.assertEqual(scheduled_fat.expected_exit_code, 1)
        self.assertEqual(scheduled_fat.expected_output, expected_output)
        self.assertIn("p[0] = 99", fat_uaf_source.read_text(encoding="utf-8"))
        self.assertIn("N/A: 1", output.getvalue())
        self.assertIn("raw pointers do not provide those checks", output.getvalue())

        with patch.object(run_tests, "discover_tests", return_value=[fat_uaf_case]), patch.object(
            run_tests, "run_test", side_effect=AssertionError("raw safety tests must not execute")
        ) as run_test, redirect_stdout(StringIO()) as output:
            result = run_tests.run_suite("safety", args("raw"), toolchain)
        self.assertEqual(result, 1)
        run_test.assert_not_called()
        self.assertIn("No tests are applicable in raw pointer mode", output.getvalue())

    def test_pointer_mode_config_validation_and_legacy_variants(self) -> None:
        with tempfile.TemporaryDirectory(prefix="native pointer config ") as temporary:
            config = Path(temporary) / "case.compile.json"
            config.write_text(
                '{"variants": [], "pointer_modes": ["fat"], '
                '"raw_inapplicable_reason": "checks fat-pointer instrumentation"}',
                encoding="utf-8",
            )
            parse_config = getattr(run_tests, "__parse_compile_config")
            variants, modes, reason = parse_config(config)
            self.assertEqual(variants, [])
            self.assertEqual(modes, ("fat",))
            self.assertEqual(reason, "checks fat-pointer instrumentation")

            config.write_text(
                '{"variants": [], "pointer_modes": ["fat"]}',
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "excluding raw requires"):
                parse_config(config)

            config.write_text(
                '{"variants": [], "pointer_modes": ["fat", "unsafe"], '
                '"raw_inapplicable_reason": "not applicable"}',
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "entries must be fat or raw"):
                parse_config(config)

        legacy = ROOT / "tests/input/basic/std/raw_coerce_neg.an.compile.json"
        variants, modes, reason = parse_config(legacy)
        self.assertEqual([variant.name for variant in variants], ["raw"])
        self.assertIsNone(modes)
        self.assertIsNone(reason)

        migrated = ROOT / "tests/input/basic/type/alias/ffi_alias_permission.err.an.compile.json"
        variants, modes, reason = parse_config(migrated)
        self.assertEqual([variant.name for variant in variants], ["ffi-permission-does-not-transfer-through-alias"])
        self.assertEqual(variants[0].compiler_args, ["--allow-ffi"])
        self.assertEqual(modes, ("fat", "raw"))
        self.assertIsNone(reason)

if __name__ == "__main__":
    unittest.main()
