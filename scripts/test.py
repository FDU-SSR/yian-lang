#!/usr/bin/env python3
"""Dump the tokens and AST of one source file.

A thin wrapper over ``yianc --dump``: the compiler writes ``build/tokens.txt``
and ``build/ast.txt`` (plus ``hir.txt``/``cfg.txt``) for the files it was given.
The old ``--token``/``--ast`` options with custom output paths no longer exist.

Usage: python3 scripts/test.py [source.an]
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
LIB_DIR = ROOT_DIR / "lib" / "src"
DEFAULT_SOURCE = ROOT_DIR / "tests" / "basic" / "array" / "access.an"


def main(argv: list[str]) -> int:
    source = Path(argv[0]) if argv else DEFAULT_SOURCE
    command = ["yianc", "--dump", str(LIB_DIR), str(source)]
    completed = subprocess.run(command, cwd=ROOT_DIR, check=False)
    if completed.returncode == 0:
        print(f"wrote {ROOT_DIR / 'build' / 'tokens.txt'} and ast.txt")
    return completed.returncode


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
