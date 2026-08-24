#!/usr/bin/env python3
"""Verify stable frame-lock shadow-stack emission in fat and raw modes.

The check establishes implementation correspondence that source-level runtime
tests cannot directly observe: frame locks come from the private fixed-address
arena, optimized exits retain volatile SENTINEL stores before depth pop, and
raw mode emits no arena state.
"""

from __future__ import annotations

import re
import subprocess
import sys
import tempfile
from pathlib import Path

from llvmlite import binding


ROOT_DIR = Path(__file__).resolve().parent.parent
SOURCE = ROOT_DIR / "tests" / "fat" / "negative" / "fat_dangle_after_rekey.an"
LIB = ROOT_DIR / "lib"
SLOTS = 1 << 20


def _compile(
    target: str, *, raw: bool = False, nocheck: bool = False, opt: int = 0
) -> bytes:
    if raw and nocheck:
        raise ValueError("raw and nocheck modes are mutually exclusive")
    suffix = ".bc" if target == "bc" else ".ll"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        output = Path(tmp.name)
    cmd = [
        sys.executable,
        "-m",
        "compiler.main",
        "-t",
        target,
        "-O",
        str(opt),
    ]
    if raw:
        cmd.append("--raw-pointers")
    if nocheck:
        cmd.append("--no-fat-checks")
    cmd.extend([str(LIB), str(SOURCE), "-o", str(output)])
    try:
        proc = subprocess.run(cmd, cwd=ROOT_DIR, capture_output=True, text=True)
        if proc.returncode != 0:
            raise RuntimeError(
                f"compiler failed (exit {proc.returncode}):\n{proc.stderr}"
            )
        return output.read_bytes()
    finally:
        output.unlink(missing_ok=True)


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def main() -> int:
    try:
        fat_ir = _compile("ll").decode("utf-8")
        nocheck_ir = _compile("ll", nocheck=True).decode("utf-8")
        raw_ir = _compile("ll", raw=True).decode("utf-8")
        optimized = str(binding.parse_bitcode(_compile("bc", opt=3)))

        arena_decl = f"[{SLOTS} x i64] zeroinitializer"
        _require(arena_decl in fat_ir, "fat IR lacks fixed frame-lock arena")
        _require("@\"__secl_frame_lock_depth\"" in fat_ir, "fat IR lacks depth cursor")
        _require(
            re.search(r"getelementptr inbounds \[1048576 x i64\].*__secl_frame_locks", fat_ir)
            is not None,
            "frame lock is not addressed through the stable arena",
        )
        _require("icmp ult i64" in fat_ir and ", 1048576" in fat_ir,
                 "frame-lock capacity is not guarded")
        _require("__secl_frame_locks" not in raw_ir,
                 "raw mode unexpectedly emits frame-lock arena")
        _require("__secl_frame_lock_depth" not in raw_ir,
                 "raw mode unexpectedly emits frame-lock depth")
        _require(arena_decl in nocheck_ir,
                 "nocheck mode does not share the stable frame-lock arena")
        _require("@\"__secl_frame_lock_depth\"" in nocheck_ir,
                 "nocheck mode lacks the frame-lock depth cursor")

        sentinel_positions = [
            match.start()
            for match in re.finditer(
                r"store volatile i64 -1, i64\* %frame\.lock", optimized
            )
        ]
        pop_positions = [
            match.start()
            for match in re.finditer(
                r"store i64 %frame\.depth\.prev, i64\* @__secl_frame_lock_depth",
                optimized,
            )
        ]
        _require(sentinel_positions, "optimized IR lacks volatile frame-exit SENTINEL")
        _require(pop_positions, "optimized nonterminal exits lack frame-depth pop")
        _require(
            all(any(s < p for s in sentinel_positions) for p in pop_positions),
            "an optimized frame-lock pop precedes every SENTINEL store",
        )
    except (AssertionError, RuntimeError) as exc:
        print(f"FRAME LOCK IR check FAILED: {exc}")
        return 1

    print(
        "PASS: stable frame-lock arena, guarded push, ordered volatile exit, "
        "nocheck sharing, and raw-mode omission"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
