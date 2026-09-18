"""``python3 -m compiler.format``: the formatter's own command line.

The formatter owns its fixtures and its corpus checks, so they live behind this
entry point rather than in the compiler's test runner:

- no flags: print the formatted source of one file to stdout;
- ``-w``: rewrite the given files in place;
- ``--check``: report the files whose formatting differs (exit code 1 if any);
- ``--verify``: check the formatter's invariants (tokens, idempotence, comments,
  layout) over the given files;
- ``--cases`` / ``--bless``: run / regenerate the fixtures under ``fixtures/``;
- ``--stats``: report the source statistics the style rules are based on.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from compiler.format.cases import bless_cases, case_files, run_cases
from compiler.format.formatter import format_text
from compiler.format.selfcheck import verify_paths
from compiler.format.stats import corpus_stats

__all__ = ["main"]


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python3 -m compiler.format", description="YIAN source formatter"
    )
    parser.add_argument("paths", nargs="*", type=Path, help="source files or directories")
    parser.add_argument("-w", "--write", action="store_true", help="rewrite files in place")
    parser.add_argument("--check", action="store_true", help="report files that are not formatted")
    parser.add_argument("--verify", action="store_true", help="check the formatter's invariants")
    parser.add_argument("--cases", action="store_true", help="run the formatter's fixtures")
    parser.add_argument("--bless", action="store_true", help="regenerate fixture expectations")
    parser.add_argument("--stats", action="store_true", help="report corpus statistics")
    args = parser.parse_args(argv)

    if args.cases or args.bless:
        return __cases(bless=args.bless)
    if not args.paths:
        parser.error("at least one path is required")
    if args.stats:
        print(corpus_stats(args.paths))
        return 0
    if args.verify:
        problems = verify_paths(args.paths)
        for problem in problems:
            print(problem, file=sys.stderr)
        print(f"{len(problems)} invariant violation(s)", file=sys.stderr)
        return 1 if problems else 0
    if args.check:
        return __check(args.paths)
    if args.write:
        return __write(args.paths)
    return __print(args.paths)


def __cases(*, bless: bool) -> int:
    if bless:
        written = bless_cases()
        print(f"blessed {written} fixture(s) under {len(case_files())} case(s)")
        return 0
    problems = run_cases()
    for problem in problems:
        print(problem, file=sys.stderr)
    print(f"{len(case_files())} case(s), {len(problems)} failure(s)")
    return 1 if problems else 0


def __files(paths: Sequence[Path]) -> list[Path]:
    found: list[Path] = []
    for path in paths:
        if path.is_dir():
            found.extend(sorted(path.rglob("*.an")))
        elif path.exists():
            found.append(path)
        else:
            print(f"skip {path}: no such file or directory", file=sys.stderr)
    return found


def __check(paths: Sequence[Path]) -> int:
    unformatted: list[Path] = []
    for path in __files(paths):
        text = path.read_text()
        formatted = format_text(text, path=path)
        if formatted is None:
            print(f"skip {path}: cannot be formatted", file=sys.stderr)
            continue
        if formatted != text:
            unformatted.append(path)
    for path in unformatted:
        print(path)
    print(f"{len(unformatted)} file(s) need formatting")
    return 1 if unformatted else 0


def __write(paths: Sequence[Path]) -> int:
    changed = 0
    for path in __files(paths):
        text = path.read_text()
        formatted = format_text(text, path=path)
        if formatted is None:
            print(f"skip {path}: cannot be formatted", file=sys.stderr)
            continue
        if formatted != text:
            path.write_text(formatted)
            changed += 1
    print(f"{changed} file(s) rewritten")
    return 0


def __print(paths: Sequence[Path]) -> int:
    files = __files(paths)
    if len(files) != 1:
        print("print mode takes exactly one file; use -w or --check otherwise", file=sys.stderr)
        return 2
    formatted = format_text(files[0].read_text(), path=files[0])
    if formatted is None:
        print(f"cannot format {files[0]}", file=sys.stderr)
        return 1
    sys.stdout.write(formatted)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
