"""Script-only selection and fixed-oracle checks for native compiler runners."""

from __future__ import annotations

import json
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts"
import sys

sys.path.insert(0, str(SCRIPTS))

import check_toolchain_parity
from _toolchain import compiler_parity


class NativeParityTests(unittest.TestCase):
    def test_check_toolchain_case_selection_is_exact_and_unique(self) -> None:
        self.assertEqual(
            [case_id for case_id, _ in check_toolchain_parity.select_cases([
                "compiler/analysis/unicode-range",
                "package/std_usage",
            ])],
            ["compiler/analysis/unicode-range", "package/std_usage"],
        )
        for case_ids in ([], [""], ["compiler/analysis/unicode-range"] * 2, ["unknown"]):
            with self.subTest(case_ids=case_ids), self.assertRaises(check_toolchain_parity.SelectionError):
                check_toolchain_parity.select_cases(case_ids)

    def test_check_toolchain_list_prints_native_commands_without_starting_tools(self) -> None:
        with tempfile.TemporaryDirectory(prefix="native parity list ") as temporary:
            prefix = Path(temporary)
            with patch.object(check_toolchain_parity, "validate_install", side_effect=AssertionError("list must not validate tools")), patch.object(
                check_toolchain_parity.subprocess,
                "run",
                side_effect=AssertionError("list must not start tools"),
            ), redirect_stdout(StringIO()) as output:
                result = check_toolchain_parity.main([
                    "--case", "compiler/analysis/unicode-range", "--list", "--prefix", str(prefix),
                ])
            self.assertEqual(result, 0)
            preview = json.loads(output.getvalue())
            self.assertEqual(preview["selection"], ["compiler/analysis/unicode-range"])
            self.assertEqual(preview["pointer_modes"], ["fat", "raw"])
            self.assertEqual(preview["native_compiler_calls"], 2)
            self.assertEqual(preview["total_native_compilation_calls"], 2)
            self.assertNotIn("reference", preview)
            self.assertTrue(all(
                command["command"][0] == str(prefix / "bin/yianc")
                for command in preview["commands"]
            ))

    def test_check_toolchain_oracle_rejects_shared_wrong_range_and_reports_failure(self) -> None:
        case_id = "compiler/analysis/unicode-range"
        correct = {
            "status": 1,
            "stdout": "",
            "stderr": "error[E301]: Unknown identifier 'missing'\n--> input.an:1:31\n",
        }
        self.assertTrue(check_toolchain_parity.oracle_passes(case_id, correct))
        wrong = dict(correct)
        wrong["stderr"] = "error[E301]: Unknown identifier 'missing'\n--> input.an:1:30\n"
        self.assertFalse(check_toolchain_parity.oracle_passes(case_id, wrong))

        with tempfile.TemporaryDirectory(prefix="native parity report ") as temporary:
            root = Path(temporary)
            identity = {
                "prefix": str(root),
                "yianc": str(root / "bin/yianc"),
                "anx": str(root / "bin/anx"),
                "yianc_sha256": "native-binary",
                "anx_sha256": "native-anx",
                "yianc_source_sha256": "yianc-source",
                "anx_source_sha256": "anx-source",
                "resources_sha256": "resources",
            }
            report_path = root / "report.json"
            with patch.object(check_toolchain_parity, "validate_install", return_value=identity), patch.object(
                check_toolchain_parity, "_observe", return_value=wrong
            ), redirect_stdout(StringIO()):
                result = check_toolchain_parity.run_selected(
                    [(case_id, check_toolchain_parity.DEFAULT_CASES[case_id])],
                    ["fat"],
                    prefix=root,
                    report_path=report_path,
                )
            self.assertEqual(result, 1)
            report = json.loads(report_path.read_text(encoding="utf-8"))
            self.assertEqual(report["mode"], "native-oracle")
            self.assertEqual(report["failures"], 1)
            self.assertFalse(report["cases"][0]["passed"])
            self.assertEqual(len(report_path.with_suffix(".failures.jsonl").read_text(encoding="utf-8").splitlines()), 1)

    def test_compiler_parity_has_unique_native_cases_and_fixed_format_expectations(self) -> None:
        cases = compiler_parity.cases()
        names = [case.name for case in cases]
        self.assertEqual(len(names), len(set(names)))
        for case in cases:
            if case.group != "format" or case.name.startswith("crlf-format-"):
                continue
            for source in case.sources:
                expected = ROOT / source
                expected = expected.with_name(expected.stem + ".expected.an")
                self.assertTrue(expected.is_file(), f"{case.name}: {expected}")

        case = next(case for case in cases if case.name == "format-stdout")
        source = ROOT / case.sources[0]
        expected = source.with_name(source.stem + ".expected.an").read_text(encoding="utf-8")
        original = source.read_text(encoding="utf-8")
        observation = {
            "observation": {
                "status": 0,
                "stdout": expected,
                "source_inputs": {case.sources[0]: original},
                "sources": {case.sources[0]: original},
            },
        }
        self.assertTrue(compiler_parity.oracle_passes(case, observation))
        observation["observation"]["stdout"] = expected + "unexpected\n"
        self.assertFalse(compiler_parity.oracle_passes(case, observation))

    def test_compiler_parity_pins_unchecked_diagnostics_to_code_and_message(self) -> None:
        for name, code in (
            ("division-by-zero", "E503"),
            ("defer-uninitialized", "E502"),
            ("uncalled-concrete-definition", "E502"),
            ("uncalled-comptime-condition", "E503"),
            ("strict-partial-input", "E301"),
            ("restricted-bitcast", "E501"),
        ):
            case = next(case for case in compiler_parity.cases() if case.name == name)
            message = getattr(compiler_parity, "__expected_error_text")(case)
            self.assertTrue(message, name)
            observation = {
                "observation": {
                    "status": 1,
                    "stdout": "",
                    "stderr": f"error[{code}]: {message}\n",
                },
            }
            self.assertTrue(compiler_parity.oracle_passes(case, observation), name)
            observation["observation"]["stderr"] = f"error[E000]: {message}\n"
            self.assertFalse(compiler_parity.oracle_passes(case, observation), name)

    def test_compiler_parity_format_write_checks_fixed_output_and_summary(self) -> None:
        case = next(case for case in compiler_parity.cases() if case.name == "format-write")
        source = case.sources[0]
        original = (ROOT / source).read_text(encoding="utf-8")
        expected_path = (ROOT / source).with_name(Path(source).stem + ".expected.an")
        formatted = expected_path.read_text(encoding="utf-8")
        rewritten = int(original != formatted)
        observation = {
            "observation": {
                "status": 0,
                "stdout": f"1 file(s) checked, {rewritten} rewritten\n",
                "source_inputs": {source: original},
                "sources": {source: formatted},
            },
        }
        self.assertTrue(compiler_parity.oracle_passes(case, observation))
        observation["observation"]["stdout"] = "1 file(s) checked, 0 rewritten\n"
        self.assertFalse(compiler_parity.oracle_passes(case, observation))


if __name__ == "__main__":
    unittest.main()
