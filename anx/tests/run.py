#! /usr/bin/env python3
"""anx integration test runner.

Usage:
    python3 -m anx.main test
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

_YIAN_ROOT = Path(__file__).resolve().parent.parent.parent
ROOT = Path(__file__).resolve().parent


def _discover() -> list[Path]:
    cases: list[Path] = []
    for pkg in sorted(ROOT.rglob("package.anx")):
        base = pkg.parent
        if any(p in base.parents for p in cases):
            continue
        if (base / "expected.stderr").exists() or (base / "src" / "main.an").exists():
            cases.append(base)
    return cases


def run_case(case: Path) -> tuple[bool, str]:
    expected_stdout = b""
    expected_stderr = ""
    expected_exit = 0

    if (case / "expected.stdout").exists():
        expected_stdout = (case / "expected.stdout").read_bytes()
    if (case / "expected.stderr").exists():
        expected_stderr = (case / "expected.stderr").read_text().strip()
        expected_exit = 1
    if (case / "expected.exit").exists():
        expected_exit = int((case / "expected.exit").read_text().strip())

    result = subprocess.run(
        [sys.executable, "-m", "anx.main", "build", str(case)],
        cwd=str(_YIAN_ROOT), capture_output=True, text=True, check=False
    )
    output = result.stdout + result.stderr

    if expected_stderr:
        if expected_stderr not in output:
            return False, (
                f"expected stderr to contain '{expected_stderr}'\n"
                f"got: {output[:500]}"
            )
        return True, ""

    if result.returncode != 0:
        return False, f"build failed (rc={result.returncode}):\n{output}"

    app = case / "build" / "app"
    if not app.exists():
        return False, "build succeeded but no 'build/app' produced"

    run_result = subprocess.run(
        [str(app)],
        cwd=case, capture_output=True, check=False
    )

    if run_result.returncode != expected_exit:
        return False, f"expected exit {expected_exit}, got {run_result.returncode}"

    if expected_stdout and run_result.stdout != expected_stdout:
        return False, (
            f"stdout mismatch:\n"
            f"expected: {expected_stdout!r}\n"
            f"got: {run_result.stdout!r}"
        )

    return True, ""


def main() -> int:
    cases = _discover()
    if not cases:
        print("No test cases found.")
        return 1

    passed = 0
    for case in cases:
        name = str(case.relative_to(ROOT))
        ok, msg = run_case(case)
        if ok:
            print(f"  [OK] {name}")
            passed += 1
        else:
            print(f"  [FAIL] {name}")
            for line in msg.split("\n"):
                if line.strip():
                    print(f"        {line}")

    print(f"\n{passed}/{len(cases)} passed")
    return 0 if passed == len(cases) else 1


if __name__ == "__main__":
    raise SystemExit(main())
