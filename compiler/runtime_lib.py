"""Locate and build the runtime library on demand.

Linking uses the runtime's static library for -t exe and relocatable object for -t obj.
Cache artifacts in build/runtime/ and rebuild only when runtime sources are newer.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RUNTIME_DIR = ROOT / "runtime"
BUILD_DIR = ROOT / "build" / "runtime"
ARCHIVE = BUILD_DIR / "libyian_rt.a"
OBJECT = BUILD_DIR / "libyian_rt.o"
BUILD_SCRIPT = RUNTIME_DIR / "build.py"


class RuntimeBuildError(Exception):
    """Runtime build failure due to missing sources or clang errors."""


# Size-class table: Python mirror of yian_rt.h::YIAN_CLASS_BYTES; the index is the class number.
# runtime/build.py --check asserts element-by-element equality.
CLASS_BYTES: tuple[int, ...] = (
    16, 20, 25, 32, 40, 50, 64, 80, 100,
    128, 160, 200, 256, 320, 400, 512, 640, 800,
    1024, 1280, 1600, 2048, 2560, 3200, 4096, 5120, 6400,
    8192, 10240, 12800, 16384, 20480, 25600, 32768, 40960, 49152,
)


def class_index_for_payload(payload: int) -> int | None:
    """Return the smallest class fitting payload bytes, or None above the largest class."""
    for index, capacity in enumerate(CLASS_BYTES):
        if payload <= capacity:
            return index
    return None


def __sources() -> list[Path]:
    return (
        sorted(RUNTIME_DIR.rglob("*.c"))
        + sorted(RUNTIME_DIR.rglob("*.h"))
        + [BUILD_SCRIPT]
    )


def __is_stale() -> bool:
    if not (ARCHIVE.exists() and OBJECT.exists()):
        return True
    built = min(ARCHIVE.stat().st_mtime, OBJECT.stat().st_mtime)
    return max(path.stat().st_mtime for path in __sources()) > built


def __ensure_built() -> None:
    if not __is_stale():
        return
    proc = subprocess.run(
        [sys.executable, str(BUILD_SCRIPT), "--quiet"],
        capture_output=True,
        text=True,
        cwd=ROOT,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeBuildError(
            f"runtime build failed:\n{proc.stdout}\n{proc.stderr}"
        )


def ensure_archive() -> Path:
    """Return the runtime static library path for -t exe linking."""
    __ensure_built()
    return ARCHIVE


def ensure_object() -> Path:
    """Return the runtime relocatable object path for -t obj merging."""
    __ensure_built()
    return OBJECT
