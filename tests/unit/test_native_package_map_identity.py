"""Focused T013 checks for recovery format boundaries and declared native inputs."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "python"))

from _toolchain.identity import component_source_files, component_source_sha256
from yianc.analysis.package_map import PackageMap
from yianc.error import CompilerError


class NativePackageMapIdentityTests(unittest.TestCase):
    def test_python_recovery_rejects_v3_before_reading_native_fields(self) -> None:
        with self.assertRaisesRegex(CompilerError, "supports format 2 only"):
            PackageMap.from_document(
                {"format": 3, "nativeRoots": ["app"], "packages": {"app": {"nativeC": {}}}},
                source="focused T013 fixture",
            )

    def test_declared_native_c_inputs_change_component_identity(self) -> None:
        with tempfile.TemporaryDirectory(prefix="native identity fixture ") as temporary:
            root = Path(temporary)
            package = root / "anx"
            (package / "src").mkdir(parents=True)
            (package / "native").mkdir()
            (package / "include").mkdir()
            (package / "lib").mkdir()
            (root / "lib" / "src" / "std").mkdir(parents=True)
            (root / "lib" / "src" / "std" / "core.an").write_text("", encoding="utf-8")
            (package / "package.anx").write_text(
                "[package]\nname='anx'\nkind='hybrid'\nffi=true\n"
                "[native.c]\nsources=['native/shim.c']\n"
                "include-dirs=['include']\narchives=['lib/local.a']\n",
                encoding="utf-8",
            )
            (package / "src" / "main.an").write_text("fn main() {}\n", encoding="utf-8")
            (package / "native" / "shim.c").write_text("int value(void) { return 1; }\n", encoding="utf-8")
            header = package / "include" / "value.h"
            header.write_text("#define VALUE 1\n", encoding="utf-8")
            archive = package / "lib" / "local.a"
            archive.write_bytes(b"!<arch>\ninitial")

            files = component_source_files(root, "anx")
            self.assertIn(header.resolve(), files)
            self.assertIn(archive.resolve(), files)
            initial = component_source_sha256(root, "anx")

            header.write_text("#define VALUE 2\n", encoding="utf-8")
            after_header = component_source_sha256(root, "anx")
            self.assertNotEqual(initial, after_header)

            archive.write_bytes(b"!<arch>\nchanged")
            after_archive = component_source_sha256(root, "anx")
            self.assertNotEqual(after_header, after_archive)


if __name__ == "__main__":
    unittest.main()
