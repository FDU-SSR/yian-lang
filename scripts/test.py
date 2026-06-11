#!/usr/bin/env python3

import subprocess
import sys
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parent.parent
TEST_FILE = ROOT_DIR / "tests" / "call" / "func_ptr.an"
TOKEN_OUTPUT = ROOT_DIR / "build" / "token.txt"
AST_OUTPUT = ROOT_DIR / "build" / "ast.txt"


def main() -> int:
    TOKEN_OUTPUT.parent.mkdir(parents=True, exist_ok=True)

    command = [
        sys.executable,
        "-m",
        "compiler.main",
        "--token",
        str(TOKEN_OUTPUT),
        "--ast",
        str(AST_OUTPUT),
        str(TEST_FILE),
    ]

    completed = subprocess.run(command, cwd=ROOT_DIR, check=False)
    return completed.returncode


if __name__ == "__main__":
    raise SystemExit(main())
