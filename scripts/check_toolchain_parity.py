#!/usr/bin/env python3
"""Check the installed native toolchain against fixed behavioral oracles."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import TypeAlias, cast

SCRIPT_ROOT = Path(__file__).resolve().parent
ROOT = SCRIPT_ROOT.parent
sys.path.insert(0, str(SCRIPT_ROOT))

from _toolchain.identity import (
    IdentityError,
    component_source_sha256,
    file_sha256,
    resources_source_sha256,
    tree_sha256,
)
from _toolchain.native import NativeToolError, resolve_native

Json: TypeAlias = None | bool | int | float | str | list["Json"] | dict[str, "Json"]


@dataclass(frozen=True)
class Case:
    case_id: str
    kind: str
    source: str
    arguments: tuple[str, ...]
    run_program: bool = False
    fixture: str = "tests/package/std_usage"
    project_directory_name: str = "project"


DEFAULT_CASES: dict[str, Case] = {
    "compiler/analysis/unicode-range": Case(
        "compiler/analysis/unicode-range",
        "analyze",
        "tests/compiler_parity/unicode_error.an",
        ("--check",),
    ),
    "compiler/analysis/restricted-bitcast": Case(
        "compiler/analysis/restricted-bitcast",
        "analyze",
        "tests/basic/error/restricted_bitcast.err.an",
        ("--check",),
    ),
    "compiler/native/array-access": Case(
        "compiler/native/array-access",
        "compile",
        "tests/basic/array/access.an",
        ("-O2", "-o", "result"),
        run_program=True,
    ),
    "package/std_usage": Case(
        "package/std_usage",
        "package",
        "tests/package/std_usage/src/main.an",
        (),
        run_program=True,
    ),
    "package/external-cwd": Case(
        "package/external-cwd",
        "package",
        "tests/package/std_usage/src/main.an",
        (),
        run_program=True,
        project_directory_name="package path 中文",
    ),
}


class SelectionError(ValueError):
    """A selected case set is empty, duplicated, or unknown."""


def select_cases(case_ids: list[str] | None) -> list[tuple[str, Case]]:
    if case_ids is None:
        return list(DEFAULT_CASES.items())
    if not case_ids or any(not case_id for case_id in case_ids):
        raise SelectionError("at least one nonempty --case value is required")
    if len(set(case_ids)) != len(case_ids):
        raise SelectionError("--case values must be unique")
    unknown = [case_id for case_id in case_ids if case_id not in DEFAULT_CASES]
    if unknown:
        raise SelectionError("unknown --case id: " + ", ".join(unknown))
    return [(case_id, DEFAULT_CASES[case_id]) for case_id in case_ids]


def _read_marker(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as error:
        raise IdentityError(f"cannot read install marker {path}: {error}") from error
    if not lines or lines[0] != "yian-native-install-v1":
        raise IdentityError(f"unrecognized native install marker: {path}")
    for line in lines[1:]:
        name, separator, value = line.partition("=")
        if not separator or not name or not value or name in values:
            raise IdentityError(f"malformed or duplicate install marker field in {path}: {line!r}")
        values[name] = value
    for name in ("yianc", "anx", "yian-fmt", "resources", "build_info_sha256"):
        if name not in values:
            raise IdentityError(f"native install marker is missing {name}: {path}")
    for name in ("yianc_build_mode", "anx_build_mode", "fmt_build_mode"):
        if values.get(name) != "raw":
            raise IdentityError(f"native install marker is missing {name}=raw: {path}")
    return values


def validate_install(prefix: Path) -> dict[str, Json]:
    """Require native ELF tools whose sources and resources match this checkout."""
    requested = prefix.expanduser()
    if requested.is_symlink():
        raise IdentityError(f"refusing a symlinked toolchain prefix: {requested}")
    resolved = requested.resolve()
    bin_root = resolved / "bin"
    data_root = resolved / "share" / "yian"
    yianc = bin_root / "yianc"
    anx = bin_root / "anx"
    formatter = bin_root / "yian-fmt"
    info_path = data_root / ".build-info"
    marker_path = data_root / ".native-install"

    for path in (bin_root, resolved / "share", data_root):
        if path.is_symlink():
            raise IdentityError(f"installed toolchain path contains a symlink: {path}")
    for path in (yianc, anx, formatter, info_path, marker_path):
        if path.is_symlink() or not path.is_file():
            raise IdentityError(f"required installed tool or identity file is missing or unsafe: {path}")
    try:
        yianc = resolve_native(str(yianc))
        anx = resolve_native(str(anx))
        formatter = resolve_native(str(formatter))
    except NativeToolError as error:
        raise IdentityError(str(error)) from error

    resources = (
        data_root / "lib" / "src",
        data_root / "runtime" / "src",
        data_root / "runtime" / "include",
    )
    if any(not path.is_dir() or path.is_symlink() for path in resources):
        raise IdentityError(f"installed compiler resources are missing or unsafe under {data_root}")
    for path in data_root.rglob("*"):
        if path.is_symlink():
            raise IdentityError(f"installed resources contain a symlink: {path}")

    marker = _read_marker(marker_path)
    artifacts = {
        "yianc": file_sha256(yianc),
        "anx": file_sha256(anx),
        "yian-fmt": file_sha256(formatter),
    }
    for name, digest in artifacts.items():
        if marker[name] != digest:
            raise IdentityError(f"installed {name} digest does not match its marker")
    if marker["build_info_sha256"] != file_sha256(info_path):
        raise IdentityError("installed build-info digest does not match its marker")

    try:
        build_info = cast(dict[str, Json], json.loads(info_path.read_text(encoding="utf-8")))
    except (OSError, json.JSONDecodeError) as error:
        raise IdentityError(f"cannot read installed build identity {info_path}: {error}") from error
    if build_info.get("format") != 1:
        raise IdentityError(f"unsupported installed build identity format: {build_info.get('format')!r}")

    components = cast(dict[str, Json], build_info.get("components", {}))
    for name, artifact in artifacts.items():
        record = components.get(name)
        if not isinstance(record, dict):
            raise IdentityError(f"installed build identity is missing component {name}")
        if record.get("source_sha256") != component_source_sha256(ROOT, name):
            raise IdentityError(f"installed {name} was built from stale or unknown source inputs")
        if record.get("artifact_sha256") != artifact:
            raise IdentityError(f"installed {name} artifact does not match build-info")
        if record.get("build_mode") != "raw":
            raise IdentityError(f"installed {name} was not recorded as a raw tool")

    resource_record = cast(dict[str, Json], build_info.get("resources", {}))
    resource_source = resources_source_sha256(ROOT)
    if resource_record.get("source_sha256") != resource_source:
        raise IdentityError("installed toolchain was built from stale or unknown resource inputs")
    installed_resource_hash = tree_sha256(
        data_root, excluded_names=frozenset((".native-install", ".build-info"))
    )
    if resource_record.get("artifact_sha256") != installed_resource_hash:
        raise IdentityError("installed resources do not match their build identity")
    if marker["resources"] != installed_resource_hash:
        raise IdentityError("installed resources do not match their install marker")

    return {
        "prefix": str(resolved),
        "yianc": str(yianc),
        "anx": str(anx),
        "yianc_sha256": artifacts["yianc"],
        "anx_sha256": artifacts["anx"],
        "formatter": str(formatter),
        "formatter_sha256": artifacts["yian-fmt"],
        "yianc_source_sha256": cast(dict[str, Json], components["yianc"])["source_sha256"],
        "anx_source_sha256": cast(dict[str, Json], components["anx"])["source_sha256"],
        "formatter_source_sha256": cast(dict[str, Json], components["yian-fmt"])["source_sha256"],
        "resources_sha256": installed_resource_hash,
    }


def _modes(selection: str) -> list[str]:
    return ["fat", "raw"] if selection == "both" else [selection]


def _preview(
    selections: list[tuple[str, Case]], modes: list[str], prefix: Path
) -> dict[str, Json]:
    native = str(prefix.expanduser().resolve() / "bin" / "yianc")
    anx = str(prefix.expanduser().resolve() / "bin" / "anx")
    commands: list[Json] = []
    for case_id, case in selections:
        for mode in modes:
            target = f"<temp>/{case.project_directory_name}" if case.kind == "package" else str(ROOT / case.source)
            raw = ["--raw-pointers"] if mode == "raw" else []
            if case.kind == "package":
                commands.append({
                    "case": case_id,
                    "pointer_mode": mode,
                    "command": [anx, "build", target, *raw],
                    "YIANC": native,
                    "run": [f"<temp>/{case.project_directory_name}/build/app"],
                })
            else:
                commands.append({
                    "case": case_id,
                    "pointer_mode": mode,
                    "command": [native, *case.arguments, *raw, "--compiler-root",
                                str(prefix.expanduser().resolve() / "share/yian"),
                                str(prefix.expanduser().resolve() / "share/yian/lib/src"), target],
                    "run": ["<temp>/result"] if case.run_program else None,
                })
    direct_calls = sum(case.kind != "package" for _, case in selections) * len(modes)
    package_builds = sum(case.kind == "package" for _, case in selections) * len(modes)
    runs = sum(case.run_program for _, case in selections) * len(modes)
    return {
        "selection": [case_id for case_id, _ in selections],
        "pointer_modes": modes,
        "native_compiler_calls": direct_calls,
        "package_builds": package_builds,
        "total_native_compilation_calls": direct_calls + package_builds,
        "program_runs": runs,
        "commands": commands,
    }


def _environment(prefix: Path, yianc: Path) -> dict[str, str]:
    environment = os.environ.copy()
    for name in ("YIAN_LIB", "YIAN_ROOT", "YIAN_LOG", "YIAN_LOG_FILE", "YIANC"):
        environment.pop(name, None)
    environment["YIAN_ROOT"] = str(ROOT)
    environment["YIAN_LIB"] = str(prefix / "share" / "yian" / "lib" / "src")
    environment["YIANC"] = str(yianc)
    return environment


def _observe(
    case: Case,
    mode: str,
    directory: Path,
    yianc: Path,
    anx: Path,
    prefix: Path,
) -> dict[str, Json]:
    raw = ["--raw-pointers"] if mode == "raw" else []
    environment = _environment(prefix, yianc)
    if case.kind == "package":
        project = directory / case.project_directory_name
        project.parent.mkdir(parents=True, exist_ok=True)
        import shutil

        shutil.copytree(
            ROOT / case.fixture,
            project,
            ignore=shutil.ignore_patterns("build", "__pycache__", ".pytest_cache"),
        )
        command = [str(anx), "build", str(project), *raw]
        process = subprocess.run(command, cwd=directory, env=environment, capture_output=True,
                                 text=True, check=False)
        package_map: Json = None
        map_path = project / "build/pkg.json"
        if map_path.is_file():
            try:
                package_map = cast(Json, json.loads(map_path.read_text(encoding="utf-8")))
            except json.JSONDecodeError:
                package_map = None
        run_result: Json = None
        executable = project / "build/app"
        if process.returncode == 0 and executable.is_file():
            run = subprocess.run([str(executable)], cwd=directory, env=environment,
                                 capture_output=True, text=True, check=False)
            run_result = {"status": run.returncode, "stdout": run.stdout, "stderr": run.stderr}
        return {
            "command": command,
            "environment": {"YIANC": str(yianc), "YIAN_LIB": environment["YIAN_LIB"]},
            "status": process.returncode,
            "stdout": process.stdout,
            "stderr": process.stderr,
            "package_map": package_map,
            "run": run_result,
        }

    source = directory / "input.an"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_bytes((ROOT / case.source).read_bytes())
    command = [str(yianc), *case.arguments, *raw, "--compiler-root", str(prefix / "share/yian"),
               str(prefix / "share/yian/lib/src"), str(source)]
    if case.kind == "compile":
        command.extend(())
    process = subprocess.run(command, cwd=directory, env=environment, capture_output=True,
                             text=True, check=False)
    stdout: Json = process.stdout
    if case.kind == "analyze":
        try:
            stdout = cast(Json, json.loads(process.stdout))
        except json.JSONDecodeError:
            pass
    run_result = None
    if case.run_program and process.returncode == 0:
        executable = directory / "result"
        if executable.is_file():
            run = subprocess.run([str(executable)], cwd=directory, env=environment,
                                 capture_output=True, text=True, check=False)
            run_result = {"status": run.returncode, "stdout": run.stdout, "stderr": run.stderr}
    return {
        "command": command,
        "environment": {"YIANC": str(yianc), "YIAN_LIB": environment["YIAN_LIB"]},
        "status": process.returncode,
        "stdout": stdout,
        "stderr": process.stderr,
        "artifact": (directory / "result").is_file(),
        "run": run_result,
    }


def oracle_passes(case_id: str, observation: dict[str, Json]) -> bool:
    if case_id == "compiler/analysis/unicode-range":
        stderr = observation.get("stderr")
        return (
            observation.get("status") == 1
            and isinstance(stderr, str)
            and "error[E301]: Unknown identifier 'missing'" in stderr
            and "input.an:1:31" in stderr
            and observation.get("stdout") == ""
        )
    if case_id == "compiler/analysis/restricted-bitcast":
        stderr = observation.get("stderr")
        return (
            observation.get("status") == 1
            and isinstance(stderr, str)
            and "error[E501]: restricted operation '@bitcast' is only allowed in the standard library" in stderr
            and observation.get("stdout") == ""
        )
    if case_id == "compiler/native/array-access":
        run = observation.get("run")
        return (
            observation.get("status") == 0
            and observation.get("artifact") is True
            and isinstance(run, dict)
            and run.get("status") == 0
            and run.get("stdout") == ""
            and run.get("stderr") == ""
        )
    if case_id in ("package/std_usage", "package/external-cwd"):
        package_map = observation.get("package_map")
        run = observation.get("run")
        command = observation.get("command")
        project = (
            Path(command[2])
            if isinstance(command, list)
            and len(command) > 2
            and isinstance(command[2], str)
            else None
        )
        if not isinstance(package_map, dict) or not isinstance(run, dict) or project is None:
            return False
        packages = package_map.get("packages")
        root_name = package_map.get("root")
        root_package = packages.get(root_name) if isinstance(packages, dict) and isinstance(root_name, str) else None
        std = packages.get("std") if isinstance(packages, dict) else None
        return (
            observation.get("status") == 0
            and package_map.get("format") == 2
            and root_name == "stdtest"
            and isinstance(root_package, dict)
            and root_package.get("kind") == "bin"
            and root_package.get("sourceRoot") == str((project / "src").resolve())
            and root_package.get("entry") == str((project / "src/main.an").resolve())
            and isinstance(std, dict)
            and std.get("sourceRoot") == observation.get("environment", {}).get("YIAN_LIB")
            and run.get("status") == 0
            and run.get("stdout") == ""
            and run.get("stderr") == ""
        )
    raise SelectionError(f"no expected-result oracle for {case_id}")


def _write_report(path: Path, report: dict[str, Json]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def run_selected(
    selections: list[tuple[str, Case]],
    modes: list[str],
    *,
    prefix: Path,
    report_path: Path,
) -> int:
    identity = validate_install(prefix)
    yianc = Path(cast(str, identity["yianc"]))
    anx = Path(cast(str, identity["anx"]))
    resolved_prefix = Path(cast(str, identity["prefix"]))
    records: list[Json] = []
    failures_path = report_path.with_suffix(".failures.jsonl")
    failures_path.parent.mkdir(parents=True, exist_ok=True)
    failures_path.write_text("", encoding="utf-8")
    for case_id, case in selections:
        for mode in modes:
            with tempfile.TemporaryDirectory(prefix="yian-native-parity-") as temporary:
                observation = _observe(case, mode, Path(temporary), yianc, anx, resolved_prefix)
                passed = oracle_passes(case_id, observation)
                record: dict[str, Json] = {
                    "case": case_id,
                    "pointer_mode": mode,
                    "observation": observation,
                    "oracle_passed": passed,
                    "passed": passed,
                }
            records.append(record)
            if not passed:
                with failures_path.open("a", encoding="utf-8") as stream:
                    stream.write(json.dumps(record, ensure_ascii=False) + "\n")
            print(f"{'PASS' if passed else 'FAIL'} {case_id}@{mode}", flush=True)
    failures = sum(record["passed"] is not True for record in records)
    planned = _preview(selections, modes, resolved_prefix)
    report: dict[str, Json] = {
        "format": 2,
        "mode": "native-oracle",
        "identity": identity,
        "selection": [case_id for case_id, _ in selections],
        "pointer_modes": modes,
        "cost": {
            "native_compiler_calls": planned["native_compiler_calls"],
            "package_builds": planned["package_builds"],
            "total_native_compilation_calls": planned["total_native_compilation_calls"],
            "program_runs": planned["program_runs"],
        },
        "total": len(records),
        "failures": failures,
        "cases": records,
    }
    _write_report(report_path, report)
    print(f"Total: {len(records)}; failures: {failures}; report: {report_path}")
    return 1 if failures else 0


def _full(prefix: Path, report_path: Path) -> int:
    resolved_prefix = prefix.expanduser().resolve()
    identity = validate_install(resolved_prefix)
    yianc = Path(cast(str, identity["yianc"]))
    anx = Path(cast(str, identity["anx"]))
    parity_report_path = report_path.with_name(report_path.stem + ".full-corpus.json")
    suites_log_path = report_path.with_name(report_path.stem + ".full-suites.log")
    parity_command = [
        sys.executable,
        str(SCRIPT_ROOT / "_toolchain" / "compiler_parity.py"),
        "--native",
        str(yianc),
        "--anx",
        str(anx),
        "--report",
        str(parity_report_path),
        "--full-analysis",
        "--full-corpus",
    ]
    parity_process = subprocess.run(parity_command, cwd=ROOT, capture_output=True, text=True, check=False)
    suites_command = [
        sys.executable,
        str(SCRIPT_ROOT / "run_tests.py"),
        "--all",
        "--implementation",
        "native",
        "--prefix",
        str(resolved_prefix),
    ]
    suites_process = subprocess.run(suites_command, cwd=ROOT, capture_output=True, text=True, check=False)
    suites_log_path.parent.mkdir(parents=True, exist_ok=True)
    suites_log_path.write_text(
        "COMMAND: " + json.dumps(suites_command) + "\n"
        + "EXIT: " + str(suites_process.returncode) + "\n"
        + "STDOUT:\n" + suites_process.stdout
        + "\nSTDERR:\n" + suites_process.stderr,
        encoding="utf-8",
    )
    report: dict[str, Json] = {
        "format": 2,
        "mode": "full-native",
        "identity": identity,
        "parity": {
            "command": parity_command,
            "exit_code": parity_process.returncode,
            "report": str(parity_report_path),
            "stdout": parity_process.stdout[-4000:],
            "stderr": parity_process.stderr[-4000:],
        },
        "suites": {"command": suites_command, "exit_code": suites_process.returncode, "log": str(suites_log_path)},
    }
    _write_report(report_path, report)
    return 0 if parity_process.returncode == 0 and suites_process.returncode == 0 else 1


def main(argv: list[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if not arguments:
        arguments.append("check")
    elif arguments[0] not in ("check", "full", "-h", "--help"):
        arguments.insert(0, "check")

    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "--report",
        type=Path,
        default=ROOT / "build/toolchain-workflows/toolchain-parity-report.json",
    )
    common.add_argument(
        "--prefix",
        type=Path,
        default=Path(os.environ.get("YIAN_INSTALL_PREFIX", Path.home() / ".local")),
    )
    common.add_argument(
        "--list",
        action="store_true",
        help="Print the planned native commands without running them.",
    )

    check_parser = commands.add_parser(
        "check", parents=[common], help="Run selected fixed native behavior oracles (default)."
    )
    check_parser.add_argument(
        "--case",
        action="append",
        dest="case_ids",
        help="Exact stable case ID; repeat to select cases.",
    )
    check_parser.add_argument(
        "--pointer-mode", choices=("fat", "raw", "both"), default="both"
    )
    commands.add_parser(
        "full", parents=[common], help="Run the native compiler corpus and native test suites."
    )
    args = parser.parse_args(arguments)

    if args.command == "full":
        if args.list:
            prefix = args.prefix.expanduser().resolve()
            print(json.dumps({
                "mode": "full-native",
                "compiler_parity": [sys.executable, str(SCRIPT_ROOT / "_toolchain/compiler_parity.py"),
                                    "--native", str(prefix / "bin/yianc"), "--anx", str(prefix / "bin/anx"),
                                    "--full-analysis", "--full-corpus"],
                "test_suites": [sys.executable, str(SCRIPT_ROOT / "run_tests.py"), "--all",
                                "--implementation", "native", "--prefix", str(prefix)],
                "note": "This delegates the full native corpus and suite selection; no tool refresh is performed.",
            }, ensure_ascii=False, indent=2))
            return 0
        try:
            return _full(args.prefix, args.report)
        except (IdentityError, NativeToolError) as error:
            _write_report(args.report, {"format": 2, "mode": "full-native", "status": "rejected", "error": str(error)})
            print(f"error: {error}", file=sys.stderr)
            return 1

    try:
        selections = select_cases(args.case_ids)
    except SelectionError as error:
        check_parser.error(str(error))
    modes = _modes(args.pointer_mode)
    if args.list:
        print(json.dumps(_preview(selections, modes, args.prefix.expanduser()), ensure_ascii=False, indent=2))
        return 0
    try:
        return run_selected(selections, modes, prefix=args.prefix, report_path=args.report)
    except (IdentityError, NativeToolError, OSError, subprocess.SubprocessError) as error:
        _write_report(args.report, {"format": 2, "mode": "native-oracle", "status": "failed", "error": str(error)})
        print(f"error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
