"""The formatter's own fixtures.

A fixture is a pair: ``fixtures/<topic>/<name>.an`` and the ``.expected.an`` file
the formatter must produce for it.  They cover what the repository corpus rarely
shows (block comments, generic brackets, f-strings, one-line blocks, blank-line
and comment placement), and they belong to the formatter — nothing here is wired
into the compiler's test runner.

``--bless`` rewrites every expectation from the current implementation, so a rule
change is reviewed as a diff over these files.
"""

from __future__ import annotations

from pathlib import Path

from compiler.format.formatter import format_text
from compiler.format.selfcheck import Problem

__all__ = ["FIXTURES", "bless_cases", "case_files", "run_cases"]

#: Where the fixture pairs live, next to this module.
FIXTURES = Path(__file__).with_name("fixtures")


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
