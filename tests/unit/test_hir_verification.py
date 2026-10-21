"""Focused native probe for duplicated HIR verification queries."""

from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = ROOT / "yianc" / "tests" / "unit" / "analysis" / "hir_verification.an"


class HirVerificationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        prefix_value = os.environ.get("YIAN_HIR_TEST_PREFIX")
        if not prefix_value:
            raise AssertionError("YIAN_HIR_TEST_PREFIX must name the authorized native prefix")
        cls.prefix = Path(prefix_value).resolve()
        cls.anx = cls.prefix / "bin" / "anx"
        cls.yianc = cls.prefix / "bin" / "yianc"
        if not cls.anx.is_file() or not os.access(cls.anx, os.X_OK):
            raise AssertionError(f"anx is missing from the authorized prefix: {cls.anx}")
        if not cls.yianc.is_file() or not os.access(cls.yianc, os.X_OK):
            raise AssertionError(f"yianc is missing from the authorized prefix: {cls.yianc}")
        if not FIXTURE.is_file():
            raise AssertionError(f"the HIR verification fixture is missing: {FIXTURE}")

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="hir verification probe ")
        self.project = Path(self.temporary.name)
        (self.project / "src").mkdir()
        (self.project / "package.anx").write_text(
            '[package]\nname = "hir_verification_probe"\nkind = "bin"\nversion = "0.1.0"\n\n'
            f'[dependencies]\nyianc = {{ path = "{ROOT / "yianc"}" }}\n',
            encoding="utf-8",
        )
        (self.project / "src" / "main.an").write_text(FIXTURE.read_text(encoding="utf-8"), encoding="utf-8")
        self.environment = os.environ.copy()
        self.environment["YIANC"] = str(self.yianc)
        self.environment["YIAN_ROOT"] = str(ROOT)
        self.environment["YIAN_LIB"] = str(ROOT / "lib" / "src")
        self.environment["YIAN_CC"] = os.environ.get("YIAN_CC", "/usr/lib/llvm-22/bin/clang")
        self.environment["PATH"] = str(self.prefix / "bin") + os.pathsep + self.environment.get("PATH", "")

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def build_and_run(self, *arguments: str) -> None:
        built = subprocess.run(
            [str(self.anx), "build", str(self.project), "-O0", *arguments],
            cwd=self.project,
            env=self.environment,
            text=True,
            capture_output=True,
            check=False,
            timeout=600,
        )
        self.assertEqual(built.returncode, 0, f"anx build failed for {arguments}:\n{built.stdout}\n{built.stderr}")
        executable = self.project / "build" / "app"
        self.assertTrue(executable.is_file(), f"anx build produced no executable for {arguments}")
        ran = subprocess.run(
            [str(executable)],
            cwd=FIXTURE.parent,
            env=self.environment,
            text=True,
            capture_output=True,
            check=False,
            timeout=120,
        )
        self.assertEqual(ran.returncode, 0, f"probe failed for {arguments}:\n{ran.stdout}\n{ran.stderr}")
        self.assertEqual(ran.stdout, "", f"probe wrote unexpected stdout for {arguments}")
        self.assertEqual(ran.stderr, "", f"probe wrote unexpected stderr for {arguments}")

    def test_cache_and_context(self) -> None:
        self.build_and_run()
        self.build_and_run("--raw-pointers")
