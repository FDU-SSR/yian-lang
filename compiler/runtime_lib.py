"""运行时库的定位与按需构建.

编译器在链接阶段需要 `runtime/` 产出的静态库（`-t exe`）与可重定位对象
（`-t obj` 合并）。库缓存到 `build/runtime/`，只有运行时源码比产物新时才重新构建。
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
    """运行时库构建失败（源码缺失或 clang 报错）。"""


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
    """返回运行时静态库路径（供 `-t exe` 链接）。"""
    __ensure_built()
    return ARCHIVE


def ensure_object() -> Path:
    """返回运行时可重定位对象路径（供 `-t obj` 合并）。"""
    __ensure_built()
    return OBJECT
