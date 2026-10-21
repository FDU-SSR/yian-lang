"""Focused native probes for non-fatal warnings and shared diagnostic layout.

The driver requires an explicit native prefix through
``YIAN_WARNING_TEST_PREFIX``. It never refreshes tools, discovers other
fixtures, or runs package suites.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LIB = ROOT / "lib" / "src"

WARNING_SOURCE = """\
from std.core.option import Option;

fn main() {
    let value: Option<i32> = Option<i32>.None;
    match value { Some(_) => {}, None => {}, }
}
"""

ERROR_SOURCE = """\
fn main() {
    let value: i32 = "bad";
}
"""


class WarningDiagnosticTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        prefix_value = os.environ.get("YIAN_WARNING_TEST_PREFIX")
        if not prefix_value:
            raise AssertionError("YIAN_WARNING_TEST_PREFIX must name the authorized native prefix")
        cls.prefix = Path(prefix_value).resolve()
        cls.yianc = cls.prefix / "bin" / "yianc"
        cls.anx = cls.prefix / "bin" / "anx"
        for tool in (cls.yianc, cls.anx):
            if not tool.is_file() or not os.access(tool, os.X_OK):
                raise AssertionError(f"native tool is missing from the authorized prefix: {tool}")
        cls.environment = os.environ.copy()
        cls.environment["YIANC"] = str(cls.yianc)
        cls.environment["YIAN_ROOT"] = str(ROOT)
        cls.environment["YIAN_LIB"] = str(LIB)
        cls.environment["YIAN_CC"] = cls.environment.get("YIAN_CC", "/usr/lib/llvm-22/bin/clang")
        cls.environment["PATH"] = str(cls.prefix / "bin") + os.pathsep + cls.environment.get("PATH", "")

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="warning diagnostic probe ")
        self.work = Path(self.temporary.name)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def source(self, name: str, text: str) -> Path:
        path = self.work / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8", newline="\n")
        return path

    def compiler(self, source: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [str(self.yianc), "--compiler-root", str(ROOT), str(LIB), str(source), *arguments],
            cwd=self.work,
            env=self.environment,
            text=True,
            capture_output=True,
            check=False,
            timeout=600,
        )

    def source_line(self, source: Path, location_line: str) -> str:
        match = re.search(r":(\d+):(\d+)$", location_line)
        self.assertIsNotNone(match, location_line)
        row = int(match.group(1))
        return source.read_text(encoding="utf-8").splitlines()[row - 1]

    def assert_warning_block(self, stderr: str, source: Path) -> None:
        lines = stderr.splitlines()
        self.assertEqual(len(lines), 4, stderr)
        self.assertEqual(lines[0], "warning[W502]: unnecessary import of 'Option': the prelude already provides it")
        self.assertRegex(lines[1], rf"^--> .*{re.escape(source.name)}:1:29$")
        self.assertEqual(lines[2], "    " + self.source_line(source, lines[1]))
        self.assertEqual(lines[3], "    " + " " * 28 + "^^^^^^")

    def assert_error_block(self, stderr: str, source: Path) -> None:
        lines = stderr.splitlines()
        self.assertGreaterEqual(len(lines), 4, stderr)
        self.assertRegex(lines[0], r"^error\[E\d+\]: ")

        match = re.match(rf"^--> .*{re.escape(source.name)}:(\d+):(\d+)$", lines[1])
        self.assertIsNotNone(match, stderr)
        self.assertEqual(lines[2], "    " + self.source_line(source, lines[1]))
        self.assertRegex(lines[3], r"^ {4,}\^+$")
        self.assertNotRegex(lines[0], r" at .+:\d+:\d+$")

    def test_warning_block_and_non_fatal_executable(self) -> None:
        source = self.source("main.an", WARNING_SOURCE)
        output = self.work / "warning-app"
        completed = self.compiler(source, "-t", "exe", "-o", str(output))
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assert_warning_block(completed.stderr, source)
        ran = subprocess.run(
            [str(output)],
            cwd=self.work,
            env=self.environment,
            text=True,
            capture_output=True,
            check=False,
            timeout=120,
        )
        self.assertEqual(ran.returncode, 0, ran.stderr)
        self.assertEqual(ran.stdout, "")
        self.assertEqual(ran.stderr, "")

    def test_warning_json(self) -> None:
        source = self.source("main.an", WARNING_SOURCE)
        completed = self.compiler(source, "--analyze", "--json")
        self.assertEqual(completed.returncode, 0, completed.stderr)
        payload = json.loads(completed.stdout)
        self.assertEqual(payload["format"], 1)
        self.assertIs(payload["ok"], True)
        self.assertEqual(payload["summary"], {"errors": 0, "warnings": 1})
        diagnostics = payload["diagnostics"]
        self.assertEqual(len(diagnostics), 1)
        diagnostic = diagnostics[0]
        self.assertEqual(diagnostic["code"], "W502")
        self.assertEqual(diagnostic["severity"], "warning")
        self.assertEqual(
            diagnostic["message"],
            "unnecessary import of 'Option': the prelude already provides it",
        )
        self.assertEqual(diagnostic["range"]["start"], {"line": 0, "character": 28})
        self.assertIs(diagnostic["recovered"], False)

    def test_error_block_uses_shared_layout(self) -> None:
        source = self.source("error.an", ERROR_SOURCE)
        completed = self.compiler(source, "-t", "none")
        self.assertNotEqual(completed.returncode, 0)
        self.assert_error_block(completed.stderr, source)

    def test_no_warning_for_aliases_non_prelude_and_user_symbols(self) -> None:
        cases = {
            "alias.an": """\
from std.core.vec import Vec as MyVec;

fn main() {
    let values: MyVec<i32> = MyVec<i32>.new();
    values.push(1);
    assert values.len() == 1;
}
""",
            "non_prelude.an": """\
from std.core.io import eprint;

fn main() {
    eprint("ok\\n");
}
""",
        }
        for name, text in cases.items():
            with self.subTest(name=name):
                source = self.source(name, text)
                completed = self.compiler(source, "-t", "none")
                self.assertEqual(completed.returncode, 0, completed.stderr)
                self.assertEqual(completed.stderr, "")

        other = self.source("other.an", """\
pub struct Option {
    pub value: i32,
}
""")
        main = self.source("user_symbol.an", """\
from other import Option;

fn main() {
    let option = Option(7);
    assert option.value == 7;
}
""")
        completed = self.compiler(main, other, "-t", "none")
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stderr, "")

    def test_stdlib_units_do_not_warn(self) -> None:
        source = self.source("empty.an", "fn main() {}\n")
        completed = self.compiler(source, "-t", "none")
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stderr, "")

    def test_anx_forwards_warning_without_failing_build(self) -> None:
        project = self.work / "project"
        (project / "src").mkdir(parents=True)
        (project / "package.anx").write_text(
            '[package]\nname = "warning_probe"\nkind = "bin"\nversion = "0.1.0"\n',
            encoding="utf-8",
        )
        source = project / "src" / "main.an"
        source.write_text(WARNING_SOURCE, encoding="utf-8", newline="\n")
        completed = subprocess.run(
            [str(self.anx), "build", str(project)],
            cwd=project,
            env=self.environment,
            text=True,
            capture_output=True,
            check=False,
            timeout=600,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assert_warning_block(completed.stderr, source)
        executable = project / "build" / "app"
        self.assertTrue(executable.is_file(), "package build produced no executable")
        ran = subprocess.run(
            [str(executable)],
            cwd=project,
            env=self.environment,
            text=True,
            capture_output=True,
            check=False,
            timeout=120,
        )
        self.assertEqual(ran.returncode, 0, ran.stderr)


if __name__ == "__main__":
    unittest.main()
