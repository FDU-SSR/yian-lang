#!/usr/bin/env python3
"""Check the deterministic runtime failure and panic protocol."""

from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[2]
LIB_DIR = ROOT_DIR / "lib"

SAFETY_CASES: tuple[tuple[str, str], ...] = (
    ("tests/array/safety_oob.an", "yian: safety error [S001]: out-of-bounds memory access\n"),
    ("tests/safety/fat_uaf.an", "yian: safety error [S002]: invalid memory access\n"),
    ("tests/safety/tiered_ref_dangle.an", "yian: safety error [S003]: dangling reference access\n"),
    ("tests/safety/fat_arith_overflow.an", "yian: safety error [S004]: invalid pointer arithmetic\n"),
    ("tests/safety/fat_ptr_diff_cross.an", "yian: safety error [S005]: invalid cross-object pointer operation\n"),
    ("tests/safety/fat_double_free.an", "yian: safety error [S006]: invalid deallocation\n"),
    ("tests/safety/tiered_ref_empty_slice.an", "yian: safety error [S007]: empty slice cannot form a reference\n"),
    ("tests/safety/malloc_overflow.an", "yian: runtime error [R001]: allocation size overflow\n"),
)


def _compile_and_run(source: Path, opt: int = 3) -> subprocess.CompletedProcess[str]:
    with tempfile.TemporaryDirectory(prefix="yian-runtime-protocol-") as directory:
        executable = Path(directory) / "program"
        compile_proc = subprocess.run(
            [
                sys.executable,
                "-m",
                "compiler.main",
                "-O",
                str(opt),
                str(LIB_DIR),
                str(source),
                "-o",
                str(executable),
            ],
            cwd=ROOT_DIR,
            capture_output=True,
            text=True,
            check=False,
        )
        if compile_proc.returncode != 0:
            raise AssertionError(
                f"compilation failed for {source} (exit {compile_proc.returncode}):\n"
                f"{compile_proc.stdout}{compile_proc.stderr}"
            )
        return subprocess.run(
            [str(executable)],
            cwd=ROOT_DIR,
            capture_output=True,
            text=True,
            check=False,
        )


def _compile_ir(source: Path) -> str:
    with tempfile.TemporaryDirectory(prefix="yian-runtime-ir-") as directory:
        output = Path(directory) / "program.ll"
        proc = subprocess.run(
            [
                sys.executable,
                "-m",
                "compiler.main",
                "-t",
                "ll",
                "-O",
                "0",
                str(LIB_DIR),
                str(source),
                "-o",
                str(output),
            ],
            cwd=ROOT_DIR,
            capture_output=True,
            text=True,
            check=False,
        )
        if proc.returncode != 0:
            raise AssertionError(
                f"IR compilation failed for {source} (exit {proc.returncode}):\n"
                f"{proc.stdout}{proc.stderr}"
            )
        return output.read_text(encoding="utf-8")


def _assert_runtime_failure(source_name: str, expected: str, opts: tuple[int, ...]) -> None:
    source = ROOT_DIR / source_name
    for opt in opts:
        result = _compile_and_run(source, opt)
        assert result.returncode == 1, (source_name, opt, result.returncode)
        assert result.stdout == "", (source_name, opt, result.stdout)
        assert result.stderr == expected, (source_name, opt, repr(result.stderr))


def _assert_panic() -> None:
    with tempfile.TemporaryDirectory(prefix="yian-panic-") as directory:
        source = Path(directory) / "panic.an"
        source.write_text('fn main() { panic("protocol panic"); }\n', encoding="utf-8")
        result = _compile_and_run(source)
    assert result.returncode == 1
    assert result.stdout == ""
    assert result.stderr == "yian: panic: protocol panic\n"


def _assert_restricted_and_invalid_forms() -> None:
    with tempfile.TemporaryDirectory(prefix="yian-runtime-forms-") as directory:
        root = Path(directory)
        user_source = root / "user.an"
        user_source.write_text(
            'fn main() { __yian_runtime_fail("S001"); }\n', encoding="utf-8"
        )
        user_proc = subprocess.run(
            [sys.executable, "-m", "compiler.main", str(LIB_DIR), str(user_source), "-t", "none"],
            cwd=ROOT_DIR,
            capture_output=True,
            text=True,
            check=False,
        )
        assert user_proc.returncode != 0
        user_output = user_proc.stdout + user_proc.stderr
        assert "restricted operation '__yian_runtime_fail'" in user_output

        trusted_root = root / "lib"
        trusted_root.mkdir()
        unknown_source = trusted_root / "unknown.an"
        unknown_source.write_text(
            'fn main() { __yian_runtime_fail("BAD"); }\n', encoding="utf-8"
        )
        variable_source = trusted_root / "variable.an"
        variable_source.write_text(
            'fn main() { let code = "S001"; __yian_runtime_fail(code); }\n',
            encoding="utf-8",
        )
        for source in (unknown_source, variable_source):
            proc = subprocess.run(
                [sys.executable, "-m", "compiler.main", str(LIB_DIR), str(source), "-t", "none"],
                cwd=ROOT_DIR,
                capture_output=True,
                text=True,
                check=False,
            )
            assert proc.returncode != 0
            assert "__yian_runtime_fail" in proc.stdout + proc.stderr


def main() -> int:
    for source_name, expected in SAFETY_CASES:
        opts = (0, 3) if source_name in {
            "tests/array/safety_oob.an",
            "tests/safety/fat_uaf.an",
            "tests/safety/fat_double_free.an",
            "tests/safety/malloc_overflow.an",
        } else (3,)
        _assert_runtime_failure(source_name, expected, opts)

    ir = _compile_ir(ROOT_DIR / "tests/safety/fat_uaf.an")
    assert "llvm.trap" not in ir
    assert "__yian_runtime_fail" in ir
    assert "cold noreturn nounwind" in ir
    assert "call i64 @\"write\"" in ir
    assert "call void @\"_exit\"(i32 1)" in ir
    assert "yian: runtime error [R002]: memory allocation failed" in ir
    assert "yian: runtime error [R003]: safety metadata exhausted" in ir

    _assert_panic()
    _assert_restricted_and_invalid_forms()
    print("PASS: deterministic runtime failure diagnostics and fail-stop protocol")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
