#!/usr/bin/env python3
"""build.py — 构建 YIAN 运行时库并做一致性检查与自测.

用法 (在仓库根或任意位置执行):

  python3 runtime/build.py            # 构建 build/runtime/libyian_rt.{o,a} (已最新则跳过)
  python3 runtime/build.py --force    # 强制重建
  python3 runtime/build.py --check    # 构建 + ABI 常量一致性断言 + 自测
  python3 runtime/build.py --asan     # 构建并运行 ASan/UBSan 版自测
  python3 runtime/build.py --quiet    # 只输出错误 (供编译器内部调用)

一致性断言:
  - runtime/include/yian_rt.h 的 YIAN_FRAME_LOCK_SLOTS / YIAN_FRAME_LOCK_SLOT_BYTES
    与 compiler/codegen/cfg/lockmech.py::FrameLockArena 相等;
  - YIAN_HDR_BYTES 与 lockmech.py::BlockHeader.BYTES 相等;
  - YIAN_CLASS_COUNT / YIAN_CLASS_BYTES 与 compiler/runtime_lib.py::CLASS_BYTES 逐项相等;
  - YIAN_ABI_FAIL_MESSAGE / YIAN_OOM_MESSAGE 与 compiler/runtime_error.py 的 S002/R002
    消息逐字节相等.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RUNTIME_DIR = ROOT / "runtime"
HEADER = RUNTIME_DIR / "include" / "yian_rt.h"
SOURCES = sorted((RUNTIME_DIR / "src").glob("*.c"))
SELFTEST = RUNTIME_DIR / "selftest" / "selftest.c"
BUILD_DIR = ROOT / "build" / "runtime"
LIB_OBJECT = BUILD_DIR / "libyian_rt.o"
LIB_ARCHIVE = BUILD_DIR / "libyian_rt.a"
SELFTEST_BIN = BUILD_DIR / "selftest"
SELFTEST_ASAN_BIN = BUILD_DIR / "selftest-asan"

CC = "clang"
CFLAGS = ["-O2", "-fno-strict-aliasing", f"-I{RUNTIME_DIR / 'include'}"]
ASAN_FLAGS = ["-O1", "-g", "-fsanitize=address,undefined", "-fno-omit-frame-pointer"]


def __sources() -> list[Path]:
    return SOURCES + [HEADER, SELFTEST, Path(__file__).resolve()]


def __is_fresh() -> bool:
    if not (LIB_OBJECT.exists() and LIB_ARCHIVE.exists()):
        return False
    built = min(LIB_OBJECT.stat().st_mtime, LIB_ARCHIVE.stat().st_mtime)
    newest = max(path.stat().st_mtime for path in __sources())
    return built >= newest


def __run(cmd: list[str]) -> None:
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False, cwd=ROOT)
    if proc.returncode != 0:
        raise SystemExit(f"[runtime] 命令失败: {' '.join(cmd)}\n{proc.stdout}\n{proc.stderr}")


def build(force: bool = False, quiet: bool = False) -> None:
    if not force and __is_fresh():
        if not quiet:
            print(f"[runtime] 已是最新: {LIB_ARCHIVE.relative_to(ROOT)}")
        return
    BUILD_DIR.mkdir(parents=True, exist_ok=True)
    if not quiet:
        print(f"[runtime] 构建 {LIB_ARCHIVE.relative_to(ROOT)}")
    objects: list[Path] = []
    for source in SOURCES:
        obj = BUILD_DIR / (source.stem + ".o")
        __run([CC, *CFLAGS, "-c", str(source), "-o", str(obj)])
        objects.append(obj)
    # 合并为单个可重定位对象: 供 `-t obj` 与用户对象再合并, 也作为静态库的唯一成员.
    __run([CC, "-r", "-nostdlib", *[str(obj) for obj in objects], "-o", str(LIB_OBJECT)])
    __run(["llvm-ar", "rcs", str(LIB_ARCHIVE), str(LIB_OBJECT)])


def run_selftest(sanitizers: bool) -> None:
    BUILD_DIR.mkdir(parents=True, exist_ok=True)
    flags = [*ASAN_FLAGS, f"-I{RUNTIME_DIR / 'include'}"] if sanitizers else list(CFLAGS)
    binary = SELFTEST_ASAN_BIN if sanitizers else SELFTEST_BIN
    __run([CC, *flags, str(SELFTEST), *[str(src) for src in SOURCES], "-o", str(binary)])
    proc = subprocess.run([str(binary)], capture_output=True, text=True, check=False, cwd=ROOT)
    label = "ASan/UBSan" if sanitizers else "普通"
    if proc.stdout.strip():
        print(proc.stdout.strip())
    if proc.returncode != 0:
        raise SystemExit(f"[runtime] {label} 自测失败 (退出码 {proc.returncode})\n{proc.stderr}")
    print(f"[runtime] {label} 自测通过")


def __macro_int(name: str) -> int:
    text = HEADER.read_text(encoding="utf-8")
    match = re.search(rf"^#define\s+{name}\s+(.+)$", text, re.MULTILINE)
    if match is None:
        raise SystemExit(f"[check] {HEADER.name} 缺少宏 {name}")
    expr = match.group(1).strip()
    expr = re.sub(r"(?<=[0-9])[uUlL]+", "", expr)
    if re.fullmatch(r"[0-9()<>&| +\-*]+", expr) is None:
        raise SystemExit(f"[check] 宏 {name} 不是可求值的整数字面量表达式: {expr!r}")
    return int(eval(expr, {"__builtins__": {}}, {}))  # noqa: S307 - 已限制字符集


def __macro_bytes(name: str) -> bytes:
    text = HEADER.read_text(encoding="utf-8")
    match = re.search(rf'^#define\s+{name}\s+"((?:[^"\\]|\\.)*)"', text, re.MULTILINE)
    if match is None:
        raise SystemExit(f"[check] {HEADER.name} 缺少字符串宏 {name}")
    return match.group(1).encode("utf-8").decode("unicode_escape").encode("latin-1")


def __macro_int_list(name: str) -> list[int]:
    """读取由整数字面量逗号分隔 (可带续行反斜杠) 的宏, 例如尺寸类表."""
    text = HEADER.read_text(encoding="utf-8")
    match = re.search(rf"^#define\s+{name}\s+((?:[^\n]*\\\n)*[^\n]*)$", text, re.MULTILINE)
    if match is None:
        raise SystemExit(f"[check] {HEADER.name} 缺少宏 {name}")
    body = match.group(1).replace("\\\n", " ")
    items = [item.strip() for item in body.split(",")]
    if any(re.fullmatch(r"[0-9]+[uUlL]*", item) is None for item in items):
        raise SystemExit(f"[check] 宏 {name} 含非整数字面量: {body!r}")
    return [int(re.sub(r"[uUlL]+$", "", item)) for item in items]


def check_consistency() -> None:
    sys.path.insert(0, str(ROOT))
    from compiler.codegen.cfg import lockmech  # noqa: PLC0415
    from compiler.runtime_error import RuntimeErrorCode, runtime_error_message  # noqa: PLC0415
    from compiler.runtime_lib import CLASS_BYTES  # noqa: PLC0415

    slots = __macro_int("YIAN_FRAME_LOCK_SLOTS")
    slot_bytes = __macro_int("YIAN_FRAME_LOCK_SLOT_BYTES")
    if slots != lockmech.FrameLockArena.SLOTS:
        raise SystemExit(f"[check] 帧锁槽位数不一致: 头文件 {slots} / lockmech {lockmech.FrameLockArena.SLOTS}")
    if slot_bytes != lockmech.FrameLockArena.SLOT_BYTES:
        raise SystemExit(
            f"[check] 帧锁槽宽不一致: 头文件 {slot_bytes} / lockmech {lockmech.FrameLockArena.SLOT_BYTES}"
        )
    header_bytes = __macro_int("YIAN_HDR_BYTES")
    if header_bytes != lockmech.BlockHeader.BYTES:
        raise SystemExit(
            f"[check] 堆块头字节数不一致: 头文件 {header_bytes} / lockmech {lockmech.BlockHeader.BYTES}"
        )
    for macro, code in (
        ("YIAN_ABI_FAIL_MESSAGE", RuntimeErrorCode.S002),
        ("YIAN_OOM_MESSAGE", RuntimeErrorCode.R002),
        ("YIAN_METADATA_MESSAGE", RuntimeErrorCode.R003),
    ):
        declared = __macro_bytes(macro)
        expected = runtime_error_message(code)
        if declared != expected:
            raise SystemExit(
                f"[check] {macro} 与 {code.name} 消息不一致:\n  头文件 {declared!r}\n  Python {expected!r}"
            )
    class_count = __macro_int("YIAN_CLASS_COUNT")
    class_bytes = __macro_int_list("YIAN_CLASS_BYTES")
    if class_count != len(class_bytes):
        raise SystemExit(
            f"[check] YIAN_CLASS_COUNT={class_count} 与尺寸类表项数 {len(class_bytes)} 不一致"
        )
    if tuple(class_bytes) != CLASS_BYTES:
        raise SystemExit(
            f"[check] 尺寸类表与 runtime_lib.CLASS_BYTES 不一致:\n"
            f"  头文件 {class_bytes}\n  Python {list(CLASS_BYTES)}"
        )
    print("[runtime] ABI 常量与 lockmech.py / runtime_error.py / runtime_lib.py 一致")


def main() -> int:
    parser = argparse.ArgumentParser(description="构建 YIAN 运行时库并做检查与自测")
    parser.add_argument("--force", action="store_true", help="强制重建")
    parser.add_argument("--check", action="store_true", help="一致性断言 + 普通自测")
    parser.add_argument("--asan", action="store_true", help="构建并运行 ASan/UBSan 自测")
    parser.add_argument("--quiet", action="store_true", help="只输出错误")
    args = parser.parse_args()

    build(force=args.force, quiet=args.quiet)
    if args.check or args.asan:
        check_consistency()
    if args.check:
        run_selftest(sanitizers=False)
    if args.asan:
        run_selftest(sanitizers=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
