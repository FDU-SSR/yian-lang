"""Trusted source roots used by compiler security boundaries.

Source paths are input data and must not acquire standard-library privileges
merely because a user chose a directory name such as ``lib``.  This module
keeps the root selection and path classification shared by the prelude,
global resolver, and restricted-operation checks.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

#: Environment variables that point at the standard library, for installs that
#: do not sit next to the checkout.
ENV_ROOT = "YIAN_ROOT"
ENV_LIB = "YIAN_LIB"


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
    """Return the standard library source root shipped with this checkout."""
    return Path(__file__).resolve().parents[2] / "lib" / "src"


def stdlib_root_of(compiler_root: Path) -> Path:
    """The standard library source root inside *compiler_root*."""
    return (compiler_root / "lib" / "src").resolve()


def resolve_stdlib_root(compiler_root: Path | None = None) -> Path:
    """Locate the standard library source root.

    Precedence: an explicit ``--compiler-root``, then ``YIAN_LIB`` (the source
    root itself), then ``YIAN_ROOT`` (a checkout root), then the checkout this
    module was imported from.  The last case is what an editable install uses;
    a non-editable install has no ``lib/`` next to it and must be told where to
    look.

    The root is *configured*, never inferred from input paths: a directory named
    ``lib``, or a ``package.anx`` claiming ``name = "std"``, must not grant a
    source file the standard library's privileges.
    """
    if compiler_root is not None:
        return stdlib_root_of(compiler_root)

    lib = os.environ.get(ENV_LIB)
    if lib:
        return Path(lib).resolve()

    root = os.environ.get(ENV_ROOT)
    if root:
        return stdlib_root_of(Path(root))

    return default_stdlib_root()


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
