#!/usr/bin/env python3
"""Load-level properties of the project model.

The package fixtures drive ``anx build`` end to end and can only observe a
diagnostic substring.  What they cannot observe — that a broken root manifest
yields ``project is None``, that diagnostics come back in a stable order, and
that the returned mappings are read-only — is asserted here against temporary
projects.

Run directly (``python3 scripts/test_project_model.py``) or through
``scripts/run_tests.py --suite package``.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from anx.diagnostics import (
    AX_BAD_MANIFEST,
    AX_DEPENDENCY_PATH_MISSING,
    AX_NESTED_SOURCE_ROOTS,
    AX_NO_PROJECT_ROOT,
    AX_RESERVED_PACKAGE_NAME,
    Diagnostic,
    sort_diagnostics,
)
from anx.project import LoadResult, PackageKind, Project, load


class ProjectLoaderTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.base = Path(self._tmp.name)
        self.std_root = self.base / "std"
        (self.std_root / "src").mkdir(parents=True)

    # -- helpers ---------------------------------------------------------
    def project(self, files: dict[str, str]) -> Path:
        root = self.base / "project"
        for rel, text in files.items():
            path = root / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text)
        return root

    def load(self, files: dict[str, str]) -> LoadResult:
        return load(self.project(files), std_root=self.std_root)

    @staticmethod
    def project_of(result: LoadResult) -> Project:
        assert result.project is not None
        return result.project

    # -- root package usability -----------------------------------------
    def test_missing_root_manifest_yields_no_project(self) -> None:
        result = self.load({"src/main.an": "fn main() {}\n"})
        self.assertIsNone(result.project)
        self.assertEqual([d.code for d in result.diagnostics], [AX_NO_PROJECT_ROOT])

    def test_broken_root_manifest_yields_no_project(self) -> None:
        result = self.load({"package.anx": '[package\nname = "app"\n'})
        self.assertIsNone(result.project)
        self.assertEqual([d.code for d in result.diagnostics], [AX_BAD_MANIFEST])

    def test_reserved_root_name_yields_no_project(self) -> None:
        result = self.load(
            {
                "package.anx": '[package]\nname = "std"\n',
                "src/main.an": "fn main() {}\n",
            }
        )
        self.assertIsNone(result.project)
        self.assertEqual([d.code for d in result.diagnostics], [AX_RESERVED_PACKAGE_NAME])

    def test_broken_root_layout_yields_no_project(self) -> None:
        result = self.load(
            {
                "package.anx": '[package]\nname = "app"\nentry = "src/nope.an"\n',
                "src/lib.an": "pub fn helper() -> i32 { return 1; }\n",
            }
        )
        self.assertIsNone(result.project)
        self.assertTrue(result.diagnostics)

    # -- a broken dependency keeps the rest of the project ---------------
    def test_missing_dependency_path_keeps_project(self) -> None:
        result = self.load(
            {
                "package.anx": '[package]\nname = "app"\n\n[dependencies]\nghost = { path = "ghost" }\n',
                "src/main.an": "fn main() {}\n",
            }
        )
        project = self.project_of(result)
        self.assertEqual([d.code for d in result.diagnostics], [AX_DEPENDENCY_PATH_MISSING])
        self.assertEqual(set(project.packages), {"app", "std"})

    def test_nested_source_roots_do_not_block_loading(self) -> None:
        result = self.load(
            {
                "package.anx": (
                    '[package]\nname = "app"\n\n[dependencies]\nvendor = { path = "src/vendor" }\n'
                ),
                "src/main.an": "fn main() {}\n",
                "src/vendor/package.anx": '[package]\nname = "vendor"\nkind = "lib"\n',
                "src/vendor/src/ops.an": "pub fn helper() -> i32 { return 1; }\n",
            }
        )
        project = self.project_of(result)
        self.assertEqual([d.code for d in result.diagnostics], [AX_NESTED_SOURCE_ROOTS])
        # The nested package owns its file; the outer package must not claim it.
        nested = project.files[project.packages["vendor"].source_root / "ops.an"]
        self.assertEqual(nested.package, "vendor")

    # -- model guarantees -------------------------------------------------
    def test_kind_and_entry_are_resolved(self) -> None:
        result = self.load(
            {
                "package.anx": '[package]\nname = "app"\nkind = "hybrid"\nentry = "src/cli.an"\n',
                "src/cli.an": "fn main() {}\n",
            }
        )
        root = self.project_of(result).packages["app"]
        self.assertIs(root.kind, PackageKind.HYBRID)
        self.assertEqual(root.entry, root.source_root / "cli.an")

    def test_mappings_are_read_only(self) -> None:
        result = self.load(
            {
                "package.anx": '[package]\nname = "app"\n',
                "src/main.an": "fn main() {}\n",
            }
        )
        project = self.project_of(result)
        with self.assertRaises(TypeError):
            project.packages["app"] = None  # type: ignore[index]
        with self.assertRaises(TypeError):
            project.files[Path("/nope.an")] = None  # type: ignore[index]
        with self.assertRaises(TypeError):
            project.dependencies["app"] = ()  # type: ignore[index]

    def test_diagnostics_are_deterministic(self) -> None:
        files = {
            "package.anx": (
                '[package]\nname = "app"\n\n[dependencies]\n'
                'ghost = { path = "ghost" }\nmissing = { path = "missing" }\n'
            ),
            "src/main.an": "fn main() {}\n",
        }
        first = self.load(files)
        second = self.load(files)
        self.assertEqual(first.diagnostics, second.diagnostics)
        self.assertEqual([d.code for d in first.diagnostics], [AX_DEPENDENCY_PATH_MISSING] * 2)
        self.assertEqual([d.message.split("'")[1] for d in first.diagnostics], ["ghost", "missing"])

    def test_sort_puts_pathless_diagnostics_first(self) -> None:
        with_path = Diagnostic(AX_BAD_MANIFEST, "b", Path("/b/package.anx"))
        without_path = Diagnostic(AX_NO_PROJECT_ROOT, "a", None)
        also_with_path = Diagnostic(AX_BAD_MANIFEST, "c", Path("/a/package.anx"))
        ordered = sort_diagnostics([with_path, also_with_path, without_path])
        self.assertEqual(
            [d.path for d in ordered],
            [None, Path("/a/package.anx"), Path("/b/package.anx")],
        )

    def test_same_code_and_path_sorts_by_span(self) -> None:
        later = Diagnostic(AX_BAD_MANIFEST, "later", Path("/a/package.anx"), (10, 12))
        earlier = Diagnostic(AX_BAD_MANIFEST, "earlier", Path("/a/package.anx"), (1, 3))
        ordered = sort_diagnostics([later, earlier])
        self.assertEqual([d.message for d in ordered], ["earlier", "later"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
