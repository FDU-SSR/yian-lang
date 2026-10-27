#!/usr/bin/env python3
"""Build and validate explicitly selected CVE witness projects.

YIAN uses native anx packages. C uses CMake with AddressSanitizer. Compilation
failures never satisfy a vulnerable witness's expected runtime diagnosis.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import NotRequired, TypedDict, cast

from _toolchain.native import NativeToolError, resolve_native

__ROOT = Path(__file__).resolve().parent.parent
__DATASET = __ROOT / "cve"


class __Index(TypedDict):
    format: int
    cases: list[str]


class __CRecord(TypedDict):
    archive_commit: str
    archive_path: str
    archive_sha256: str
    archive_normalized_sha256: str
    archive_entry: str
    entry_added_return: bool
    args: list[str]
    expected_asan: list[str]


class __YianRecord(TypedDict):
    exit: int
    diagnostic: str
    source_sha256: str


class __Case(TypedDict):
    id: str
    c: __CRecord
    yian: __YianRecord


class __Execution(TypedDict):
    id: str
    language: str
    source_sha256: NotRequired[str]
    result: str
    exit: NotRequired[int]
    log: NotRequired[str]
    error: NotRequired[str]


def __load_cases() -> dict[str, __Case]:
    index = cast(__Index, json.loads((__DATASET / "index.json").read_text()))
    if index.get("format") != 1:
        raise ValueError("unsupported CVE index format")
    cases: dict[str, __Case] = {}
    for identifier in index["cases"]:
        if not isinstance(identifier, str) or not __valid_id(identifier):
            raise ValueError(f"invalid CVE identifier: {identifier!r}")
        if identifier in cases:
            raise ValueError(f"duplicate CVE identifier: {identifier}")
        record = cast(__Case, json.loads((__DATASET / identifier / "case.json").read_text()))
        if record.get("id") != identifier:
            raise ValueError(f"case identity mismatch: {identifier}")
        cases[identifier] = record
    if not cases:
        raise ValueError("CVE index is empty")
    return cases


def __valid_id(value: str) -> bool:
    parts = value.split("-")
    return (len(parts) == 3 and parts[0] == "CVE" and len(parts[1]) == 4
            and all(part.isascii() and part.isdecimal() for part in parts[1:]))


def __check_layout(cases: dict[str, __Case]) -> None:
    for identifier, record in cases.items():
        root = __DATASET / identifier
        project = root / "yian"
        required = ["package.anx", "src/main.an", "src/scenario.an", "expected.txt"]
        for relative in required:
            if not (project / relative).is_file():
                raise ValueError(f"missing project file: {project / relative}")
        expected = record["yian"]
        text = (project / "expected.txt").read_text().strip()
        want = f"Exit code {expected['exit']}\n{expected['diagnostic']}"
        if expected["exit"] != 1 or "safety error" not in expected["diagnostic"] or text != want:
            raise ValueError(f"expected-result mismatch: {project}")
        for relative in ("CMakeLists.txt", "src/main.c", "src/reproducer.c", "include/reproducer.h"):
            if not (root / "c" / relative).is_file():
                raise ValueError(f"missing C project file: {root / 'c' / relative}")
        if not record["c"]["expected_asan"]:
            raise ValueError(f"missing C sanitizer expectation: {identifier}")
        if len(record["c"]["archive_sha256"]) != 64:
            raise ValueError(f"missing C provenance hash: {identifier}")
        source = (root / "c" / "src" / "reproducer.c").read_text()
        header = '#include "reproducer.h"\n\n'
        if not source.startswith(header):
            raise ValueError(f"missing C entry adapter header: {identifier}")
        source = source.removeprefix(header)
        if record["c"]["entry_added_return"]:
            marker = '    return 0; /* Preserve the implicit successful return of C main. */\n'
            if source.count(marker) != 1:
                raise ValueError(f"invalid C entry return adapter: {identifier}")
            source = source.replace(marker, "", 1)
        source, count = re.subn(r'int cve_reproduce\([^)]*\) \{',
                                lambda _: record["c"]["archive_entry"], source, count=1)
        if count != 1 or hashlib.sha256(source.encode()).hexdigest() != record["c"]["archive_normalized_sha256"]:
            raise ValueError(f"C archive adaptation hash mismatch: {identifier}")


def __command(command: list[str], log: Path, environment: dict[str, str], timeout: float) -> subprocess.CompletedProcess[str]:
    log.parent.mkdir(parents=True, exist_ok=True)
    try:
        result = subprocess.run(command, cwd=__ROOT, env=environment, text=True,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                timeout=timeout, check=False)
    except subprocess.TimeoutExpired as error:
        output = error.stdout or b""
        if isinstance(output, bytes):
            output = output.decode(errors="replace")
        log.write_text(output + "\nCommand timed out.\n")
        raise ValueError(f"command timed out; see {log.relative_to(__ROOT)}") from error
    log.write_text(result.stdout)
    return result


def __require_build(result: subprocess.CompletedProcess[str], log: Path) -> None:
    if result.returncode:
        raise ValueError(f"build failed ({result.returncode}); see {log.relative_to(__ROOT)}\n{result.stdout[-2000:]}")


def __source_hash(project: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(project.rglob("*")):
        relative = path.relative_to(project)
        if not path.is_file() or "build" in relative.parts:
            continue
        digest.update(relative.as_posix().encode() + b"\0")
        digest.update(path.read_bytes())
    return digest.hexdigest()


def __run_case(identifier: str, record: __Case, language: str,
               args: argparse.Namespace, environment: dict[str, str]) -> __Execution:
    project = __DATASET / identifier / language
    logs = __ROOT / "build" / "cve" / identifier
    stem = language
    build_log = logs / f"{stem}-build.log"
    if language == "yian":
        result = __command([args.anx, "build", str(project), "-O0"],
                           build_log, environment, args.timeout)
        __require_build(result, build_log)
        executable = project / "build" / "app"
        process_args = []
    else:
        result = __command(["cmake", "-S", str(project), "-B", str(project / "build"),
                            f"-DCMAKE_C_COMPILER={args.cc}", "-DCVE_ASAN=ON"],
                           build_log, environment, args.timeout)
        __require_build(result, build_log)
        compile_log = logs / f"{stem}-compile.log"
        result = __command(["cmake", "--build", str(project / "build"),
                            "--target", "reproducer", "--parallel", "1"],
                           compile_log, environment, args.timeout)
        __require_build(result, compile_log)
        executable = project / "build" / "reproducer"
        process_args = record["c"]["args"]
    entry: __Execution = {"id": identifier, "language": language,
             "source_sha256": __source_hash(project), "result": "BUILD_ONLY"}
    if args.build_only:
        return entry
    run_environment = environment.copy()
    if language == "c":
        # Leaks are outside this defect check; UAR detection is required.
        run_environment["ASAN_OPTIONS"] = "detect_leaks=0:detect_stack_use_after_return=1:halt_on_error=1:exitcode=86"
    run_log = logs / f"{stem}-run.log"
    result = __command([str(executable), *process_args], run_log,
                       run_environment, args.timeout)
    entry["exit"] = result.returncode
    entry["log"] = str(run_log.relative_to(__ROOT))
    if language == "yian":
        expected = record["yian"]
        passed = (result.returncode == expected["exit"]
                  and (not expected["diagnostic"] or expected["diagnostic"] in result.stdout))
    else:
        passed = (result.returncode != 0
                  and "AddressSanitizer" in result.stdout
                  and any(word in result.stdout for word in record["c"]["expected_asan"]))
    entry["result"] = "PASS" if passed else "FAIL"
    return entry


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    selection = parser.add_mutually_exclusive_group()
    selection.add_argument("--case", action="append", default=[], metavar="ID")
    selection.add_argument("--all", action="store_true", help="select every case explicitly")
    parser.add_argument("--list", action="store_true", help="list IDs without compiling")
    parser.add_argument("--check-layout", action="store_true", help="check project files without compiling")
    parser.add_argument("--language", choices=("yian", "c", "both"), default="yian")
    parser.add_argument("--build-only", action="store_true")
    parser.add_argument("--anx", default=os.environ.get("ANX", "anx"))
    parser.add_argument("--yianc", default=os.environ.get("YIANC", "yianc"))
    parser.add_argument("--cc", default=os.environ.get("CC", "clang"))
    parser.add_argument("--timeout", type=float, default=60.0)
    args = parser.parse_args()
    if args.timeout <= 0:
        parser.error("--timeout must be positive")
    try:
        cases = __load_cases()
        unknown = set(args.case) - cases.keys()
        if unknown:
            parser.error("unknown case(s): " + ", ".join(sorted(unknown)))
        if args.case:
            cases = {identifier: cases[identifier] for identifier in dict.fromkeys(args.case)}
        if args.list:
            print("\n".join(cases))
            return 0
        if args.check_layout:
            __check_layout(cases)
            print(f"Layout OK: {len(cases)} C projects, {len(cases)} YIAN projects")
            return 0
        if not args.case and not args.all:
            parser.error("select --case ID or --all; use --list for discovery")
        __check_layout(cases)
        environment = dict(os.environ)
        tools = {}
        if args.language in ("yian", "both"):
            args.anx = str(resolve_native(args.anx))
            args.yianc = str(resolve_native(args.yianc))
            environment["YIANC"] = args.yianc
            environment["YIAN_LIB"] = str(__ROOT / "lib" / "src")
            tools.update({"anx": args.anx, "yianc": args.yianc})
        if args.language in ("c", "both"):
            compiler = shutil.which(args.cc)
            if compiler is None or shutil.which("cmake") is None:
                raise ValueError("C witnesses require CMake and a C compiler (default clang)")
            args.cc = str(Path(compiler).resolve())
            tools["cc"] = args.cc
        reports: list[__Execution] = []
        languages = ("c", "yian") if args.language == "both" else (args.language,)
        for identifier, record in cases.items():
            for language in languages:
                entry: __Execution
                try:
                    entry = __run_case(identifier, record, language, args, environment)
                except (ValueError, OSError) as error:
                    entry = {"id": identifier, "language": language,
                             "result": "FAIL", "error": str(error)}
                reports.append(entry)
                print(f"{entry['result']} {identifier} {language}")
                if "error" in entry:
                    print(entry["error"], file=sys.stderr)
        report_path = __ROOT / "build" / "cve" / "report.json"
        report_path.parent.mkdir(parents=True, exist_ok=True)
        for name, executable in tuple(tools.items()):
            tools[name + "_sha256"] = hashlib.sha256(Path(executable).read_bytes()).hexdigest()
        report_path.write_text(json.dumps({"tools": tools, "cases": reports}, indent=2) + "\n")
        failed = sum(entry["result"] == "FAIL" for entry in reports)
        print(f"Selected projects: {len(reports)}; failed: {failed}; report: build/cve/report.json")
        return 1 if failed or not reports else 0
    except (ValueError, OSError, KeyError, NativeToolError) as error:
        print(f"CVE runner: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
