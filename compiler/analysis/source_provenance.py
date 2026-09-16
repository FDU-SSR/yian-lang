"""Trusted source roots used by compiler security boundaries.

Source paths are input data and must not acquire standard-library privileges
merely because a user chose a directory name such as ``lib``.  This module
keeps the root selection and path classification shared by the prelude,
global resolver, and restricted-operation checks.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class SourceTrust:
    """Canonical roots used to classify compiler input files."""

    stdlib_root: Path
    test_roots: tuple[Path, ...]

    def is_stdlib(self, path: Path) -> bool:
        return _is_within(path, self.stdlib_root)

    def is_test_harness(self, path: Path) -> bool:
        return any(_is_within(path, root) for root in self.test_roots)

    def allows_restricted_ops(self, path: Path) -> bool:
        return self.is_stdlib(path) or self.is_test_harness(path)


def default_stdlib_root() -> Path:
    """Return the standard library shipped with this compiler checkout."""
    return Path(__file__).resolve().parents[2] / "lib"


def build_source_trust(stdlib_root: Path | None = None) -> SourceTrust:
    """Build source-root policy for a compiler invocation.

    The test roots are deliberately exact checkout paths.  They preserve the
    existing low-level test harness privilege without trusting arbitrary user
    directories whose names happen to contain ``tests`` or ``std``.
    """
    repo_root = Path(__file__).resolve().parents[2]
    root = (stdlib_root or default_stdlib_root()).resolve()
    test_roots = (
        (repo_root / "tests" / "basic" / "std").resolve(),
        (repo_root / "tests" / "safety" / "std").resolve(),
    )
    return SourceTrust(stdlib_root=root, test_roots=test_roots)


def _is_within(path: Path, root: Path) -> bool:
    resolved = path.resolve()
    try:
        resolved.relative_to(root)
    except ValueError:
        return False
    return True
