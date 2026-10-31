#!/usr/bin/env python3
"""Regenerate the small, platform-specific static archives for this fixture."""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
ARCHIVES = (
    (ROOT / "vendor/mid", ("fixture-src/mid.c", "fixture-src/bridge.c"), "lib/libmid.a"),
    (ROOT / "vendor/leaf", ("fixture-src/leaf.c",), "lib/libleaf.a"),
    (ROOT / "vendor/testkit", ("fixture-src/testkit.c",), "lib/libtestkit.a"),
)


def main() -> None:
    clang = shutil.which("clang")
    llvm_ar = shutil.which("llvm-ar")
    if clang is None or llvm_ar is None:
        raise SystemExit("fixture archive generation requires clang and llvm-ar")
    for package, sources, archive in ARCHIVES:
        target = package / archive
        target.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="native c fixture ") as temporary:
            objects: list[str] = []
            for index, source in enumerate(sources):
                output = Path(temporary) / f"{index}.o"
                subprocess.run(
                    [clang, "-O2", "-fPIC", "-std=c11", "-c", str(package / source), "-o", str(output)],
                    check=True,
                )
                objects.append(str(output))
            subprocess.run([llvm_ar, "rcs", str(target), *objects], check=True)


if __name__ == "__main__":
    main()
