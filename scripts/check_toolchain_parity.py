#!/usr/bin/env python3
"""Check selected Python/native compiler behavior against explicit oracles."""

from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import cast

SCRIPT_ROOT = Path(__file__).resolve().parent
ROOT = SCRIPT_ROOT.parent
sys.path.insert(0, str(SCRIPT_ROOT))
sys.path.insert(0, str(ROOT / "python"))

from _toolchain import compiler_parity
from _toolchain.identity import (
    IdentityError,
    component_source_sha256,
    file_sha256,
    paths_sha256,
    resources_source_sha256,
    tree_sha256,
)

Case = compiler_parity.Case
Json = compiler_parity.Json

DEFAULT_CASES: dict[str, Case] = {
    "compiler/analysis/unicode-range": Case(
        "compiler/analysis/unicode-range",
        "analyze",
        ("tests/compiler_parity/unicode_error.an",),
        ("--analyze", "--json"),
        json_output=True,
        expected_error="missing",
    ),
    "compiler/native/array-access": Case(
        "compiler/native/array-access",
        "native",
        ("tests/basic/array/access.an",),
        ("-O2", "-o", "result"),
        run=True,
    ),
    "package/std_usage": Case(
        "package/std_usage",
        "package",
        ("tests/package/std_usage/src/main.an",),
        (),
        fixture="tests/package/std_usage",
        package_build=True,
    ),
}


class SelectionError(ValueError):
    """A user-selected parity set is empty, duplicate, or unknown."""


def select_cases(case_ids: list[str] | None) -> list[tuple[str, Case]]:
    if not case_ids:
        return list(DEFAULT_CASES.items())
    if len(set(case_ids)) != len(case_ids):
        raise SelectionError("--case values must be unique")
    unknown = [case_id for case_id in case_ids if case_id not in DEFAULT_CASES]
    if unknown:
        raise SelectionError("unknown --case id: " + ", ".join(unknown))
    return [(case_id, DEFAULT_CASES[case_id]) for case_id in case_ids]


def _expected_reference_source_sha256() -> str:
    compiler_root = ROOT / "python" / "yianc"
    if not compiler_root.is_dir():
        raise IdentityError(f"Python reference compiler sources are missing: {compiler_root}")
    sources = [path.resolve() for path in compiler_root.rglob("*.py") if path.is_file()]
    return paths_sha256(ROOT, sources)


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
    for required in ("yianc", "anx", "resources", "build_info_sha256"):
        if required not in values:
            raise IdentityError(f"native install marker is missing {required}: {path}")
    return values


def validate_install(prefix: Path) -> dict[str, Json]:
    """Reject unknown or stale installed tools without refreshing them."""
    requested_prefix = prefix.expanduser()
    if requested_prefix.is_symlink():
        raise IdentityError(f"refusing a symlinked toolchain prefix: {requested_prefix}")
    resolved_prefix = requested_prefix.resolve()
    bin_root = resolved_prefix / "bin"
    share_root = resolved_prefix / "share"
    data_root = share_root / "yian"
    yianc = bin_root / "yianc"
    anx = bin_root / "anx"
    info_path = data_root / ".build-info"
    marker_path = data_root / ".native-install"

    for path in (bin_root, share_root, data_root):
        if path.is_symlink():
            raise IdentityError(f"installed toolchain path contains a symlink: {path}")
    for path in (yianc, anx, info_path, marker_path):
        if path.is_symlink() or not path.is_file():
            raise IdentityError(f"required installed tool or identity file is missing or unsafe: {path}")
    for path in (yianc, anx):
        if not os.access(path, os.X_OK):
            raise IdentityError(f"installed tool is not executable: {path}")
        with path.open("rb") as stream:
            if stream.read(4) != b"\x7fELF":
                raise IdentityError(f"installed native tool is not an ELF executable: {path}")
    if not data_root.is_dir() or data_root.is_symlink():
        raise IdentityError(f"installed resource directory is missing or unsafe: {data_root}")
    for path in data_root.rglob("*"):
        if path.is_symlink():
            raise IdentityError(f"installed resources contain a symlink: {path}")

    marker = _read_marker(marker_path)
    marker_hashes = {
        "yianc": file_sha256(yianc),
        "anx": file_sha256(anx),
    }
    for name, actual in marker_hashes.items():
        if marker[name] != actual:
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
    for component, artifact in marker_hashes.items():
        item = components.get(component)
        if not isinstance(item, dict):
            raise IdentityError(f"installed build identity is missing component {component}")
        expected_source = component_source_sha256(ROOT, component)
        if item.get("source_sha256") != expected_source:
            raise IdentityError(f"installed {component} was built from stale or unknown source inputs")
        if item.get("artifact_sha256") != artifact:
            raise IdentityError(f"installed {component} artifact does not match build-info")

    expected_reference = _expected_reference_source_sha256()
    recorded_reference = build_info.get("reference_compiler_source_sha256")
    if recorded_reference is not None and recorded_reference != expected_reference:
        raise IdentityError("installed toolchain records stale or unknown Python compiler sources")

    resources = cast(dict[str, Json], build_info.get("resources", {}))
    current_resource_sources = resources_source_sha256(ROOT)
    if resources.get("source_sha256") != current_resource_sources:
        raise IdentityError("installed toolchain was built from stale or unknown resource inputs")
    installed_resource_hash = tree_sha256(
        data_root,
        excluded_names=frozenset((".native-install", ".build-info")),
    )
    if resources.get("artifact_sha256") != installed_resource_hash:
        raise IdentityError("installed resources do not match their build identity")
    if marker["resources"] != installed_resource_hash:
        raise IdentityError("installed resources do not match their install marker")

    return {
        "prefix": str(resolved_prefix),
        "yianc_sha256": marker_hashes["yianc"],
        "anx_sha256": marker_hashes["anx"],
        "build_info_sha256": marker["build_info_sha256"],
        "resources_sha256": installed_resource_hash,
        "yianc_source_sha256": cast(dict[str, Json], components["yianc"])["source_sha256"],
        "anx_source_sha256": cast(dict[str, Json], components["anx"])["source_sha256"],
        "reference_compiler_source_sha256": expected_reference,
    }


def _diagnostic_oracle(observation: dict[str, Json]) -> bool:
    result = observation.get("stdout")
    if not isinstance(result, dict) or result.get("ok") is not False:
        return False
    if observation.get("status") != 1 or observation.get("expected_error") is not True:
        return False
    diagnostics = result.get("diagnostics")
    if not isinstance(diagnostics, list):
        return False
    errors = [item for item in diagnostics if isinstance(item, dict) and item.get("severity") == "error"]
    return any(
        "missing" in str(item.get("message", ""))
        and isinstance(item.get("range"), dict)
        and isinstance(item["range"].get("start"), dict)
        and isinstance(item["range"].get("end"), dict)
        for item in errors
    )


def oracle_passes(case_id: str, record: dict[str, Json]) -> tuple[bool, bool]:
    reference = cast(dict[str, Json], record["reference"]["observation"])
    native = cast(dict[str, Json], record["native"]["observation"])
    if case_id == "compiler/analysis/unicode-range":
        return _diagnostic_oracle(reference), _diagnostic_oracle(native)
    if case_id == "compiler/native/array-access":
        def array_ok(observation: dict[str, Json]) -> bool:
            run = observation.get("run")
            return (
                observation.get("status") == 0
                and "result" in cast(list[Json], observation.get("artifacts", []))
                and isinstance(run, dict)
                and run.get("status") == 0
                and run.get("stdout") == ""
                and run.get("stderr") == ""
            )

        return array_ok(reference), array_ok(native)
    if case_id == "package/std_usage":
        def package_ok(observation: dict[str, Json]) -> bool:
            package_map = observation.get("package_map")
            run = observation.get("run")
            if not isinstance(package_map, dict) or not isinstance(run, dict):
                return False
            packages = package_map.get("packages")
            std = packages.get("std") if isinstance(packages, dict) else None
            return (
                observation.get("status") == 0
                and package_map.get("format") == 2
                and package_map.get("root") == "stdtest"
                and isinstance(std, dict)
                and std.get("sourceRoot") == "<stdlib>"
                and run.get("status") == 0
                and run.get("stdout") == ""
                and run.get("stderr") == ""
            )

        return package_ok(reference), package_ok(native)
    raise SelectionError(f"no expected-result oracle for {case_id}")


def _reference_launcher(path: Path) -> None:
    path.write_text(
        "#!/bin/sh\n"
        f"exec {shlex.quote(sys.executable)} -m yianc.main \"$@\"\n",
        encoding="utf-8",
    )
    path.chmod(0o755)


def _record_pair(
    case_id: str,
    case: Case,
    raw: bool,
    reference: list[str],
    native: list[str],
    anx: list[str],
    prefix: Path,
    reference_launcher: Path,
) -> dict[str, Json]:
    with tempfile.TemporaryDirectory(prefix="yian-toolchain-parity-") as temporary:
        root = Path(temporary)
        reference_dir = root / "reference"
        native_dir = root / "native"
        reference_dir.mkdir()
        native_dir.mkdir()
        reference_result = compiler_parity.run_case(
            reference,
            case,
            reference_dir,
            raw,
            prefix / "share" / "yian" / "lib" / "src",
            anx,
            reference_launcher,
        )
        native_result = compiler_parity.run_case(
            native,
            case,
            native_dir,
            raw,
            prefix / "share" / "yian" / "lib" / "src",
            anx,
            reference_launcher,
        )
    record: dict[str, Json] = {
        "case": case_id,
        "pointer_mode": "raw" if raw else "fat",
        "reference": reference_result,
        "native": native_result,
    }
    reference_observation = cast(dict[str, Json], reference_result["observation"])
    native_observation = cast(dict[str, Json], native_result["observation"])
    observations_equal = reference_observation == native_observation
    reference_oracle, native_oracle = oracle_passes(case_id, record)
    record["observations_equal"] = observations_equal
    record["reference_oracle"] = reference_oracle
    record["native_oracle"] = native_oracle
    record["passed"] = observations_equal and reference_oracle and native_oracle
    return record


def _preview(
    selections: list[tuple[str, Case]],
    modes: list[bool],
    prefix: Path,
) -> dict[str, Json]:
    native = str(prefix / "bin" / "yianc")
    anx = str(prefix / "bin" / "anx")
    entries: list[Json] = []
    for case_id, case in selections:
        for raw in modes:
            mode = "raw" if raw else "fat"
            if case.package_build:
                raw_argument = ["--raw-pointers"] if raw else []
                entry: dict[str, Json] = {
                    "case": case_id,
                    "pointer_mode": mode,
                    "python": [anx, "build", "<temp>/reference/project", *raw_argument],
                    "python_YIANC": "<temporary Python reference compiler launcher>",
                    "python_run": ["<temp>/reference/project/build/app"],
                    "native": [anx, "build", "<temp>/native/project", *raw_argument],
                    "native_YIANC": native,
                    "native_run": ["<temp>/native/project/build/app"],
                }
            else:
                reference_command = [sys.executable, "-m", "yianc.main", *case.arguments]
                native_command = [native, *case.arguments]
                if case.root_option:
                    reference_command.extend(("--compiler-root", str(ROOT)))
                    native_command.extend(("--compiler-root", str(ROOT)))
                if raw:
                    reference_command.append("--raw-pointers")
                    native_command.append("--raw-pointers")
                sources = [
                    *([str(ROOT / "lib" / "src")] if case.standard_library else []),
                    *(str(ROOT / source) for source in case.sources),
                ]
                reference_command.extend(sources)
                native_command.extend(sources)
                entry = {
                    "case": case_id,
                    "pointer_mode": mode,
                    "python": reference_command,
                    "native": native_command,
                }
                if case.run:
                    entry["python_run"] = ["<temp>/" + case.executable]
                    entry["native_run"] = ["<temp>/" + case.executable]
            entries.append(entry)
    package_builds = sum(1 for _, case in selections if case.package_build) * len(modes) * 2
    direct_compiler_calls = sum(1 for _, case in selections if not case.package_build) * len(modes) * 2
    program_runs = sum(1 for _, case in selections if case.run or case.package_build) * len(modes) * 2
    return {
        "selection": [case_id for case_id, _ in selections],
        "pointer_modes": ["raw" if raw else "fat" for raw in modes],
        "paired_observations": len(entries),
        "direct_compiler_calls": direct_compiler_calls,
        "package_builds": package_builds,
        "total_compiler_calls_including_package_builds": direct_compiler_calls + package_builds,
        "program_runs": program_runs,
        "commands": entries,
    }


def _write_report(path: Path, report: dict[str, Json]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _refresh_guidance(prefix: Path) -> str:
    return (
        "After reviewing matching seed tools, explicitly run scripts/refresh_toolchain.sh "
        f"--prefix {shlex.quote(str(prefix))} --anx <native-anx-path> --yianc <native-yianc-path>; "
        "if no compatible native seed exists, use scripts/bootstrap_toolchain.sh with a configured Python 3.12+ environment."
    )


def run_selected(
    selections: list[tuple[str, Case]],
    modes: list[bool],
    *,
    prefix: Path,
    report_path: Path,
    reference: list[str] | None = None,
    native: list[str] | None = None,
    anx: list[str] | None = None,
) -> int:
    resolved_prefix = prefix.expanduser().resolve()
    identity = validate_install(resolved_prefix)
    reference_command = reference or [sys.executable, "-m", "yianc.main"]
    native_command = native or [str(resolved_prefix / "bin" / "yianc")]
    anx_command = anx or [str(resolved_prefix / "bin" / "anx")]
    records: list[Json] = []
    failure_path = report_path.with_suffix(".failures.jsonl")
    failure_path.parent.mkdir(parents=True, exist_ok=True)
    failure_path.write_text("", encoding="utf-8")
    with tempfile.TemporaryDirectory(prefix="yian-reference-launcher-") as launcher_directory:
        launcher = Path(launcher_directory) / "yianc"
        _reference_launcher(launcher)
        for case_id, case in selections:
            for raw in modes:
                record = _record_pair(
                    case_id,
                    case,
                    raw,
                    reference_command,
                    native_command,
                    anx_command,
                    resolved_prefix,
                    launcher,
                )
                records.append(record)
                if record["passed"] is not True:
                    with failure_path.open("a", encoding="utf-8") as stream:
                        stream.write(json.dumps(record, ensure_ascii=False) + "\n")
                print(
                    f"{'PASS' if record['passed'] else 'FAIL'} {case_id}@{record['pointer_mode']}",
                    flush=True,
                )
    failures = sum(record["passed"] is not True for record in records)
    planned_cost = _preview(selections, modes, resolved_prefix)
    report: dict[str, Json] = {
        "format": 1,
        "mode": "smoke",
        "identity": identity,
        "selection": [case_id for case_id, _ in selections],
        "pointer_modes": ["raw" if raw else "fat" for raw in modes],
        "cost": {
            "paired_observations": len(records),
            "direct_compiler_calls": planned_cost["direct_compiler_calls"],
            "package_builds": planned_cost["package_builds"],
            "compiler_calls_including_package_builds": planned_cost["total_compiler_calls_including_package_builds"],
            "program_runs": sum(
                isinstance(cast(dict[str, Json], cast(dict[str, Json], record[side])["observation"]).get("run"), dict)
                for record in records
                for side in ("reference", "native")
            ),
        },
        "total": len(records),
        "failures": failures,
        "cases": records,
    }
    _write_report(report_path, report)
    print(f"Total: {len(records)}; failures: {failures}; report: {report_path}")
    return 1 if failures else 0


def _full(prefix: Path, report_path: Path) -> int:
    """Run the existing parity corpus and the established nonexperimental suites."""
    resolved_prefix = prefix.expanduser().resolve()
    native = resolved_prefix / "bin" / "yianc"
    anx = resolved_prefix / "bin" / "anx"
    parity_report_path = report_path.with_name(report_path.stem + ".full-corpus.json")
    suites_log_path = report_path.with_name(report_path.stem + ".full-suites.log")
    parity_command = [
        sys.executable,
        str(SCRIPT_ROOT / "_toolchain" / "compiler_parity.py"),
        "--reference",
        "python",
        "--bootstrap",
        str(native),
        "--anx",
        str(anx),
        "--build-mode",
        "unspecified",
        "--report",
        str(parity_report_path),
        "--full-analysis",
        "--full-corpus",
    ]
    parity_process = subprocess.run(
        parity_command,
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    suites_command = [
        sys.executable,
        str(SCRIPT_ROOT / "run_tests.py"),
        "--all",
        "--implementation",
        "both",
        "--prefix",
        str(resolved_prefix),
    ]
    suites_process = subprocess.run(
        suites_command,
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    suites_log_path.parent.mkdir(parents=True, exist_ok=True)
    suites_log_path.write_text(
        "COMMAND: " + json.dumps(suites_command) + "\n"
        + "EXIT: " + str(suites_process.returncode) + "\n"
        + "STDOUT:\n" + suites_process.stdout
        + "\nSTDERR:\n" + suites_process.stderr,
        encoding="utf-8",
    )
    report: dict[str, Json] = {
        "format": 1,
        "mode": "full",
        "prefix": str(resolved_prefix),
        "parity": {
            "command": parity_command,
            "exit_code": parity_process.returncode,
            "report": str(parity_report_path),
            "stdout": parity_process.stdout[-4000:],
            "stderr": parity_process.stderr[-4000:],
        },
        "suites": {
            "command": suites_command,
            "exit_code": suites_process.returncode,
            "log": str(suites_log_path),
        },
    }
    _write_report(report_path, report)
    return 0 if parity_process.returncode == 0 and suites_process.returncode == 0 else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", action="append", dest="case_ids", help="Exact stable case ID; repeat to select multiple cases.")
    parser.add_argument("--pointer-mode", choices=("fat", "raw", "both"), default="both")
    parser.add_argument("--list", action="store_true", help="Print the selected commands and cost without invoking tools.")
    parser.add_argument("--full", action="store_true", help="Run the complete compiler parity corpus and nonexperimental test suites.")
    parser.add_argument("--report", type=Path, default=ROOT / "build/toolchain-workflows/toolchain-parity-report.json")
    parser.add_argument("--prefix", type=Path, default=Path(os.environ.get("YIAN_INSTALL_PREFIX", Path.home() / ".local")))
    args = parser.parse_args(argv)

    if args.full and args.case_ids:
        parser.error("--full cannot be combined with --case")
    if args.full and args.pointer_mode != "both":
        parser.error("--full uses the modes defined by the complete existing suites; do not select --pointer-mode")
    try:
        selections = select_cases(args.case_ids)
    except SelectionError as error:
        parser.error(str(error))
    if args.pointer_mode == "both":
        modes = [False, True]
    else:
        modes = [args.pointer_mode == "raw"]

    if args.list:
        if args.full:
            commands = {
                "compiler_parity": [
                    sys.executable,
                    str(SCRIPT_ROOT / "_toolchain" / "compiler_parity.py"),
                    "--reference",
                    "python",
                    "--bootstrap",
                    str(args.prefix.expanduser().resolve() / "bin" / "yianc"),
                    "--anx",
                    str(args.prefix.expanduser().resolve() / "bin" / "anx"),
                    "--build-mode",
                    "unspecified",
                    "--report",
                    str(args.report.with_name(args.report.stem + ".full-corpus.json")),
                    "--full-analysis",
                    "--full-corpus",
                ],
                "test_suites": [sys.executable, str(SCRIPT_ROOT / "run_tests.py"), "--all", "--implementation", "both", "--prefix", str(args.prefix.expanduser().resolve())],
                "note": "Delegates the legacy complete parity corpus plus basic, safety, package, and bootstrap suites; no refresh is performed by this checker.",
            }
            print(json.dumps(commands, ensure_ascii=False, indent=2))
        else:
            print(json.dumps(_preview(selections, modes, args.prefix.expanduser()), ensure_ascii=False, indent=2))
        return 0

    if args.full:
        try:
            validate_install(args.prefix)
        except IdentityError as error:
            report = {
                "format": 1,
                "mode": "full",
                "status": "rejected_stale_or_unknown_install",
                "prefix": str(args.prefix.expanduser()),
                "error": str(error),
                "refresh_hint": _refresh_guidance(args.prefix.expanduser()),
            }
            _write_report(args.report, report)
            print(f"error: {error}; see {args.report}", file=sys.stderr)
            return 1
        return _full(args.prefix, args.report)
    try:
        return run_selected(selections, modes, prefix=args.prefix, report_path=args.report)
    except IdentityError as error:
        report = {
            "format": 1,
            "mode": "smoke",
            "status": "rejected_stale_or_unknown_install",
            "prefix": str(args.prefix.expanduser()),
            "error": str(error),
            "refresh_hint": _refresh_guidance(args.prefix.expanduser()),
        }
        _write_report(args.report, report)
        print(f"error: {error}; see {args.report}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
