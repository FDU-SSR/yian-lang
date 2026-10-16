"""Shared formatter fixtures used by the recovery formatter and native checks.

A fixture is a pair: ``fixtures/<topic>/<name>.an`` and the ``.expected.an`` file
the formatter must produce for it.  They cover what the repository corpus rarely
shows (block comments, generic brackets, f-strings, one-line blocks, blank-line
and comment placement), and they belong to the formatter. The native parity oracle
and native formatter integration test consume these fixed inputs; the standard
language-suite runner does not discover this fixture directory.

The native formatter is authoritative for daily checks. ``--bless`` remains a
recovery-only convenience and must not be used to establish new expectations.
"""

from __future__ import annotations

from pathlib import Path

from yianc.format.formatter import format_text
from yianc.format.selfcheck import Problem

__all__ = ["FIXTURES", "bless_cases", "case_files", "run_cases"]

#: Neutral fixture location shared with native formatter tests.
FIXTURES = Path(__file__).resolve().parents[3] / "tests" / "format" / "fixtures"


def case_files() -> list[Path]:
    """Every fixture input, in a stable order."""
    if not FIXTURES.is_dir():
        return []
    return sorted(path for path in FIXTURES.rglob("*.an") if not path.name.endswith(".expected.an"))


def expected_path(case: Path) -> Path:
    """The expectation that belongs to *case*."""
    return case.with_name(case.stem + ".expected.an")


def run_cases() -> tuple[Problem, ...]:
    """Format every fixture and compare with its expectation."""
    problems: list[Problem] = []
    for case in case_files():
        expected = expected_path(case)
        text = case.read_text()
        formatted = format_text(text, path=case)
        if formatted is None:
            problems.append(Problem(case, "format", "refused to format a fixture"))
            continue
        if not expected.exists():
            problems.append(Problem(case, "missing", f"no {expected.name} (run --bless)"))
            continue
        if formatted != expected.read_text():
            problems.append(Problem(case, "expected", f"differs from {expected.name}"))
    return tuple(problems)


def bless_cases() -> int:
    """Rewrite every expectation from the current formatter; returns the count."""
    written = 0
    for case in case_files():
        formatted = format_text(case.read_text(), path=case)
        if formatted is None:
            continue
        expected_path(case).write_text(formatted)
        written += 1
    return written
