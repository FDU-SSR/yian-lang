"""The compiler and editor consume the same semantic validation stages."""

from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from compiler.analysis.session import AnalysisSession
from compiler.target_layout import type_size_provider


ROOT = Path(__file__).resolve().parents[2]
STDLIB = ROOT / "lib" / "src"


class SharedAnalysisTests(unittest.TestCase):
    def test_existing_comptime_and_assignment_errors(self) -> None:
        for raw in (False, True):
            for name, code in (
                ("comptime_if_div_zero.err.an", "E503"),
                ("defer_uninitialized.err.an", "E502"),
            ):
                with self.subTest(name=name, raw=raw):
                    source = ROOT / "tests" / "basic" / "error" / name
                    result = AnalysisSession(
                        compiler_root=ROOT, raw_pointers=raw,
                        type_size_factory=type_size_provider,
                    ).analyze((STDLIB, source))
                    self.assertIn(code, {diagnostic.code for diagnostic in result.diagnostics})
                    command = [sys.executable, "-m", "compiler.main", "-t", "none"]
                    if raw:
                        command.append("--raw-pointers")
                    process = subprocess.run(
                        [*command, str(STDLIB), str(source)],
                        cwd=ROOT, capture_output=True, text=True, check=False,
                    )
                    self.assertNotEqual(process.returncode, 0)
                    self.assertIn(f"error[{code}]", process.stderr)

    def test_uncalled_concrete_definition_is_validated(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "main.an"
            source.write_text(
                "fn unused() { let value: i32; value; }\nfn main() {}\n",
                encoding="utf-8",
            )
            result = AnalysisSession(
                compiler_root=ROOT, type_size_factory=type_size_provider,
            ).analyze((STDLIB, source))
            self.assertIn("E502", {diagnostic.code for diagnostic in result.diagnostics})
            process = subprocess.run(
                [sys.executable, "-m", "compiler.main", "-t", "none", str(STDLIB), str(source)],
                cwd=ROOT, capture_output=True, text=True, check=False,
            )
            self.assertIn("error[E502]", process.stderr)

    def test_editor_accepts_entryless_text_but_build_requires_entry(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "library.an"
            source.write_text("fn helper() {}\n", encoding="utf-8")
            result = AnalysisSession(
                compiler_root=ROOT, type_size_factory=type_size_provider,
            ).analyze((STDLIB, source))
            self.assertTrue(result.ok())
            process = subprocess.run(
                [sys.executable, "-m", "compiler.main", "-t", "none", str(STDLIB), str(source)],
                cwd=ROOT, capture_output=True, text=True, check=False,
            )
            self.assertNotEqual(process.returncode, 0)
            self.assertIn("No 'main' function found", process.stderr)

    def test_uncalled_comptime_condition_is_validated(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "main.an"
            source.write_text(
                "fn unused() { let x: i32 = comptime if (1i32 / 0i32) == 0i32 { 1 } else { 2 }; }\n"
                "fn main() {}\n",
                encoding="utf-8",
            )
            result = AnalysisSession(
                compiler_root=ROOT, type_size_factory=type_size_provider,
            ).analyze((STDLIB, source))
            self.assertIn("E503", {diagnostic.code for diagnostic in result.diagnostics})
            process = subprocess.run(
                [sys.executable, "-m", "compiler.main", "-t", "none", str(STDLIB), str(source)],
                cwd=ROOT, capture_output=True, text=True, check=False,
            )
            self.assertIn("error[E503]", process.stderr)


if __name__ == "__main__":
    unittest.main()
