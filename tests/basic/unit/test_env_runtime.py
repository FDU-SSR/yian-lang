"""Black-box checks for invalid process arguments across pointer modes."""

from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[3]


def main() -> int:
    source_text = """
from std.core.env import args;

fn main() {
    let values = args();
    assert values.len() == 2;
}
""".lstrip()
    expected_stderr = b"yian: panic: process argument is not valid UTF-8\n"

    variants = (
        ("fat", []),
        ("raw", ["--raw-pointers"]),
        ("no-fat-checks", ["--no-fat-checks"]),
    )
    with tempfile.TemporaryDirectory(prefix="yian-env-") as temp_dir:
        temp = Path(temp_dir)
        source = temp / "invalid_arg.an"
        source.write_text(source_text, encoding="utf-8")
        for name, flags in variants:
            executable = temp / name
            compile_proc = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "compiler.main",
                    "-O3",
                    *flags,
                    "-o",
                    str(executable),
                    "lib",
                    str(source),
                ],
                cwd=ROOT_DIR,
                capture_output=True,
                check=False,
            )
            if compile_proc.returncode != 0:
                sys.stderr.buffer.write(compile_proc.stdout + compile_proc.stderr)
                raise AssertionError(f"{name}: compilation failed")

            run_proc = subprocess.run(
                [str(executable), b"\xff"],
                cwd=ROOT_DIR,
                capture_output=True,
                check=False,
            )
            if run_proc.returncode != 1:
                raise AssertionError(f"{name}: expected exit 1, got {run_proc.returncode}")
            if run_proc.stdout != b"":
                raise AssertionError(f"{name}: unexpected stdout: {run_proc.stdout!r}")
            if run_proc.stderr != expected_stderr:
                raise AssertionError(f"{name}: unexpected stderr: {run_proc.stderr!r}")

        for code in (0, 42):
            exit_source = temp / f"exit_{code}.an"
            exit_source.write_text(
                f"from std.core.env import exit;\n\nfn main() {{\n    exit({code});\n}}\n",
                encoding="utf-8",
            )
            for name, flags in variants:
                executable = temp / f"exit-{name}-{code}"
                compile_proc = subprocess.run(
                    [
                        sys.executable,
                        "-m",
                        "compiler.main",
                        "-O3",
                        *flags,
                        "-o",
                        str(executable),
                        "lib",
                        str(exit_source),
                    ],
                    cwd=ROOT_DIR,
                    capture_output=True,
                    check=False,
                )
                if compile_proc.returncode != 0:
                    sys.stderr.buffer.write(compile_proc.stdout + compile_proc.stderr)
                    raise AssertionError(f"{name}, exit({code}): compilation failed")

                run_proc = subprocess.run(
                    [str(executable)],
                    cwd=ROOT_DIR,
                    capture_output=True,
                    check=False,
                )
                if run_proc.returncode != code:
                    raise AssertionError(
                        f"{name}, exit({code}): expected {code}, got {run_proc.returncode}"
                    )
                if run_proc.stdout != b"" or run_proc.stderr != b"":
                    raise AssertionError(f"{name}, exit({code}): exit was not silent")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
