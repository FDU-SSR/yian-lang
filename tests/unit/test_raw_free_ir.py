#!/usr/bin/env python3
"""Regression check: raw-pointer mode must emit ``free`` calls on ``del``.

This check verifies *emission*, not O3 survival:

  1. Compile ``tests/pointer/raw_compat_del.an`` with
     ``-t ll --raw-pointers`` (zero-optimized LLVM IR).
  2. Count ``call.*free`` occurrences.
  3. Require >= 1: otherwise print ``RAW free emission check FAILED: 0 free calls``
     and exit non-zero.

Usage:
    python3 scripts/check_raw_free_ir.py
"""

from __future__ import annotations

import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[2]
SOURCE = ROOT_DIR / "tests" / "pointer" / "raw_compat_del.an"
LIB = ROOT_DIR / "lib"

MIN_FREE_CALLS = 1

_FREE_RE = re.compile(r"call.*\bfree\b")


def _emit_ir() -> str:
    """Compile SOURCE to LLVM IR text (-t ll --raw-pointers)."""
    if not SOURCE.exists():
        raise FileNotFoundError(f"test source not found: {SOURCE}")
    with tempfile.NamedTemporaryFile(suffix=".ll", delete=False) as tmp:
        out_path = tmp.name
    cmd = [
        sys.executable,
        "-m",
        "compiler.main",
        "-t",
        "ll",
        "--raw-pointers",
        str(LIB),
        str(SOURCE),
        "-o",
        out_path,
    ]
    proc = subprocess.run(cmd, cwd=ROOT_DIR, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(
            f"compiler failed (exit {proc.returncode}):\n{proc.stderr}"
        )
    ir = Path(out_path).read_text(encoding="utf-8")
    Path(out_path).unlink(missing_ok=True)
    return ir


def main() -> int:
    try:
        ir = _emit_ir()
    except (FileNotFoundError, RuntimeError) as exc:
        print(f"RAW free emission check ERROR: {exc}")
        return 2

    n = len(_FREE_RE.findall(ir))
    if n < MIN_FREE_CALLS:
        print(f"RAW free emission check FAILED: {n} free calls")
        return 1
    print(f"PASS: {n} free calls")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
