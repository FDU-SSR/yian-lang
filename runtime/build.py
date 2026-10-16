#!/usr/bin/env python3
"""build.py: build the YIAN runtime, check consistency, and run self-tests.

Usage from the repository root or any directory:

  python3 runtime/build.py            # Build build/runtime/libyian_rt.{o,a} unless current
  python3 runtime/build.py --force    # Force rebuilding
  python3 runtime/build.py --check-consistency-only  # Native/C ABI check only
  python3 runtime/build.py --check    # Build, check ABI constants, and self-test
  python3 runtime/build.py --asan     # Build/run ASan and UBSan self-tests
  python3 runtime/build.py --quiet    # Errors only, for internal compiler calls

Consistency checks compare fixed declarations in yianc/src/codegen/abi/runtime.an
with runtime/include/yian_rt.h. Clang evaluates integer assertions; the C header owns the
size-class table. Python compiler ABI modules are recovery mirrors, not daily check inputs.
"""

from __future__ import annotations

import argparse
import re
import shutil
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
ABI_SOURCE = ROOT / "yianc" / "src" / "codegen" / "abi" / "runtime.an"

CC = "clang"
CFLAGS = ["-O2", "-fno-strict-aliasing", f"-I{RUNTIME_DIR / 'include'}"]
ASAN_FLAGS = ["-O1", "-g", "-fsanitize=address,undefined", "-fno-omit-frame-pointer"]
ABI_CONSTANT_MACROS = {
    "FRAME_LOCK_SLOTS": "YIAN_FRAME_LOCK_SLOTS",
    "LOCK_ENTRY_BYTES": "YIAN_FRAME_LOCK_SLOT_BYTES",
    "HEADER_BYTES": "YIAN_HDR_BYTES",
}
ABI_MESSAGE_MACROS = {
    "S002": "YIAN_ABI_FAIL_MESSAGE",
    "R002": "YIAN_OOM_MESSAGE",
    "R003": "YIAN_METADATA_MESSAGE",
}


def __sources() -> list[Path]:
    return SOURCES + sorted((RUNTIME_DIR / "include").glob("*.h")) + [SELFTEST, Path(__file__).resolve()]


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
    # Merge into one relocatable object, used for -t obj merging and as the archive's sole member.
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


def __source_text(path: Path, label: str) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError as error:
        raise SystemExit(f"[check] 无法读取 {label} {path}: {error}") from error


def __native_integer_expression(path: Path, name: str) -> str:
    text = __source_text(path, "native ABI source")
    declaration = re.compile(
        rf"^\s*pub const {re.escape(name)}: u64 = (?P<expression>[^;]+);\s*$",
        re.MULTILINE,
    )
    matches = list(declaration.finditer(text))
    if len(matches) != 1:
        raise SystemExit(f"[check] native ABI 中 {name} 声明缺失或重复")

    expression = matches[0].group("expression")
    token_pattern = re.compile(
        r"\s*(?:(?P<number>0[xX][0-9A-Fa-f]+|[0-9]+)(?P<suffix>[uUiI](?:8|16|32|64)?)?"
        r"|(?P<identifier>[A-Za-z_][A-Za-z0-9_]*)"
        r"|(?P<operator><<|>>|[()+\-*/%&|^~]))"
    )
    identifiers = {
        "FRAME_LOCK_SLOTS": "YIAN_FRAME_LOCK_SLOTS",
        "LOCK_TABLE_SLOTS": "YIAN_LOCK_TABLE_SLOTS",
        "LITERAL_LOCK_INDEX": "YIAN_LITERAL_LOCK_INDEX",
        "ENV_LOCK_INDEX": "YIAN_ENV_LOCK_INDEX",
        "HEAP_LOCK_BASE": "YIAN_HEAP_LOCK_BASE",
        "LOCK_ENTRY_BYTES": "YIAN_FRAME_LOCK_SLOT_BYTES",
        "HEADER_BYTES": "YIAN_HDR_BYTES",
    }
    normalized: list[str] = []
    offset = 0
    while offset < len(expression):
        if not expression[offset:].strip():
            break
        match = token_pattern.match(expression, offset)
        if match is None:
            raise SystemExit(
                f"[check] native ABI {name} 使用不支持的整数表达式: {expression!r}"
            )
        offset = match.end()
        number = match.group("number")
        if number is not None:
            suffix = (match.group("suffix") or "").lower()
            if suffix in {"u64", "i64"}:
                number += "ULL" if suffix == "u64" else "LL"
            elif suffix.startswith("u"):
                number += "U"
            elif suffix.startswith("i"):
                number += ""
            normalized.append(number)
            continue
        identifier = match.group("identifier")
        if identifier is not None:
            mapped = identifiers.get(identifier)
            if mapped is None:
                raise SystemExit(
                    f"[check] native ABI {name} 引用了不支持的常量 {identifier!r}"
                )
            normalized.append(mapped)
            continue
        normalized.append(match.group("operator"))

    result = " ".join(normalized)
    if not result:
        raise SystemExit(f"[check] native ABI {name} 的整数表达式为空")
    return result


def __decode_protocol_string(value: str, label: str) -> bytes:
    output = bytearray()
    index = 0
    escapes = {"n": b"\n", "r": b"\r", "t": b"\t", "\\": b"\\", '"': b'"'}
    while index < len(value):
        character = value[index]
        if character != "\\":
            output.extend(character.encode("utf-8"))
            index += 1
            continue
        index += 1
        if index >= len(value) or value[index] not in escapes:
            raise SystemExit(f"[check] {label} 含不支持的字符串转义")
        output.extend(escapes[value[index]])
        index += 1
    return bytes(output)


def __native_failure_message(path: Path, code: str) -> bytes:
    text = __source_text(path, "native ABI source")
    if not re.search(r"^pub fn failure_message\(code: str\) -> str \{\s*$", text, re.MULTILINE):
        raise SystemExit("[check] native ABI 的 failure_message 函数形状不受支持")
    pattern = re.compile(
        rf'^\s*"{re.escape(code)}"\s*=>\s*"((?:[^"\\]|\\.)*)"\s*,?\s*$',
        re.MULTILINE,
    )
    matches = list(pattern.finditer(text))
    if len(matches) != 1:
        raise SystemExit(f"[check] native ABI failure_message({code}) 缺失或重复")
    return __decode_protocol_string(matches[0].group(1), f"native ABI {code} 消息")


def __macro_string(path: Path, name: str) -> bytes:
    text = __source_text(path, "C ABI header")
    pattern = re.compile(
        rf'^#define\s+{re.escape(name)}\s+"((?:[^"\\]|\\.)*)"\s*$',
        re.MULTILINE,
    )
    matches = list(pattern.finditer(text))
    if len(matches) != 1:
        raise SystemExit(f"[check] {path.name} 中 {name} 字符串宏缺失或重复")
    return __decode_protocol_string(matches[0].group(1), f"{path.name} {name}")


def __macro_integer_list(path: Path, name: str) -> list[str]:
    text = __source_text(path, "C ABI header")
    pattern = re.compile(
        rf"^#define\s+{re.escape(name)}\s+((?:[^\n]*\\\n)*[^\n]*)$",
        re.MULTILINE,
    )
    matches = list(pattern.finditer(text))
    if len(matches) != 1:
        raise SystemExit(f"[check] {path.name} 中 {name} 表缺失或重复")
    body = matches[0].group(1).replace("\\\n", " ")
    items = [item.strip() for item in body.split(",")]
    if not items or any(re.fullmatch(r"[0-9]+[uUlL]*", item) is None for item in items):
        raise SystemExit(f"[check] {name} 表形状不受支持: {body!r}")
    return [re.sub(r"[uUlL]+$", "", item) for item in items]


def __check_native_c_integers(
    abi_path: Path,
    header_path: Path,
    *,
    clang: str,
) -> None:
    assertions = [
        f'_Static_assert(({__native_integer_expression(abi_path, native_name)}) == '
        f'({macro}), "{native_name} disagrees with {macro}");'
        for native_name, macro in ABI_CONSTANT_MACROS.items()
    ]
    class_values = __macro_integer_list(header_path, "YIAN_CLASS_BYTES")
    assertions.extend(
        [
            "static const uint32_t yian_abi_class_bytes[] = { YIAN_CLASS_BYTES };",
            '_Static_assert(YIAN_CLASS_COUNT > 0, "YIAN_CLASS_COUNT must be positive");',
            '_Static_assert(YIAN_CLASS_COUNT == sizeof(yian_abi_class_bytes) / sizeof(yian_abi_class_bytes[0]), "YIAN_CLASS_COUNT disagrees with YIAN_CLASS_BYTES");',
        ]
    )
    for index, value in enumerate(class_values):
        assertions.append(
            f'_Static_assert(({value}) > 0, "YIAN_CLASS_BYTES[{index}] must be positive");'
        )
        if index:
            assertions.append(
                f'_Static_assert(({value}) > ({class_values[index - 1]}), '
                f'"YIAN_CLASS_BYTES[{index}] must increase");'
            )

    source = '#include "yian_rt.h"\n' + "\n".join(assertions) + "\n"
    executable = shutil.which(clang)
    if executable is None:
        raise SystemExit(f"[check] 找不到 C 编译器 {clang!r}")
    process = subprocess.run(
        [executable, "-std=c11", "-Werror", "-fsyntax-only", "-x", "c", "-", "-I", str(header_path.parent)],
        input=source,
        text=True,
        capture_output=True,
        check=False,
        cwd=ROOT,
    )
    if process.returncode != 0:
        detail = process.stderr.strip() or process.stdout.strip()
        raise SystemExit(f"[check] native/C ABI 整数断言失败:\n{detail}")


def check_consistency(
    *,
    abi_path: Path = ABI_SOURCE,
    header_path: Path = HEADER,
    clang: str = CC,
) -> None:
    for native_name in ABI_CONSTANT_MACROS:
        __native_integer_expression(abi_path, native_name)
    for code, macro in ABI_MESSAGE_MACROS.items():
        expected = __native_failure_message(abi_path, code)
        declared = __macro_string(header_path, macro)
        if declared != expected:
            raise SystemExit(
                f"[check] {macro} 与 native failure_message({code}) 不一致:\n"
                f"  C header {declared!r}\n  native {expected!r}"
            )
    __check_native_c_integers(abi_path, header_path, clang=clang)
    print("[runtime] native ABI declarations match the C header")


def main() -> int:
    parser = argparse.ArgumentParser(description="构建 YIAN 运行时库并做检查与自测")
    parser.add_argument("--force", action="store_true", help="强制重建")
    parser.add_argument("--check", action="store_true", help="一致性断言 + 普通自测")
    parser.add_argument(
        "--check-consistency-only",
        action="store_true",
        help="只核对 native ABI 与 C 头文件，不构建运行时库或运行自测",
    )
    parser.add_argument("--asan", action="store_true", help="构建并运行 ASan/UBSan 自测")
    parser.add_argument("--quiet", action="store_true", help="只输出错误")
    args = parser.parse_args()

    if args.check_consistency_only:
        if args.force or args.check or args.asan:
            parser.error("--check-consistency-only cannot be combined with build/self-test options")
        check_consistency()
        return 0

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
