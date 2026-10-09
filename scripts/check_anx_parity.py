#!/usr/bin/env python3
"""Compare the Python and native anx command contracts in isolated copies."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import TypeAlias

ROOT = Path(__file__).resolve().parents[1]
Json: TypeAlias = None | bool | int | float | str | list["Json"] | dict[str, "Json"]


@dataclass(frozen=True)
class Case:
    name: str
    command: tuple[str, ...]
    fixture: str = ""
    json_output: bool = False
    tree: str = ""
    executable: str = ""
    executable_args: tuple[str, ...] = ()
    flexible_help: bool = False
    toml_message_flexible: bool = False
    stdin_crlf: bool = False


def cases() -> list[Case]:
    result = [
        Case("no-command", (), flexible_help=True),
        Case("top-level-help", ("--help",), flexible_help=True),
        Case("unknown-command", ("unknown",), flexible_help=True),
        Case("new-bin", ("new", "probe"), tree="probe"),
        Case("new-lib", ("new", "probe", "--kind", "lib"), tree="probe"),
        Case("new-hybrid-attached", ("new", "probe", "--kind=hybrid"), tree="probe"),
        Case("new-lib-precedence", ("new", "probe", "--kind", "hybrid", "--lib"), tree="probe"),
        Case("graph-basic", ("graph", "--json", "."), "tests/package/basic", json_output=True),
        Case("graph-dependencies", ("graph", "--json", "app"), "tests/package/deps", json_output=True),
        Case("graph-cycle", ("graph", "--json", "."), "tests/package/errors/graph_cycle", json_output=True),
        Case("graph-diagnostics", ("graph", "--json", "."), "tests/package/errors/graph_diagnostics", json_output=True),
        Case("graph-broken-manifest", ("graph", "--json", "."), "tests/package/errors/broken_manifest", json_output=True, toml_message_flexible=True),
        Case("check", ("check", "."), "tests/package/basic"),
        Case("build-and-run", ("build", "."), "tests/package/basic", executable="build/app"),
        Case("run-arguments", ("run", ".", "--", "alpha beta", "--help"), "tests/package/basic"),
        Case("test", ("test", "."), "tests/package/project_tests"),
        Case("test-crlf-stdin", ("test", "."), "tests/package/project_tests", stdin_crlf=True),
        Case("fmt-check", ("fmt", ".", "--check"), "tests/package/project_tests"),
    ]
    return result


def normalize(value: Json, root: Path) -> Json:
    if isinstance(value, str):
        return value.replace(str(root), "<work>")
    if isinstance(value, list):
        return [normalize(item, root) for item in value]
    if isinstance(value, dict):
        return {key: normalize(item, root) for key, item in value.items()}
    return value


def diagnostic_shape(value: Json) -> Json:
    """Ignore TOML parser prose while preserving diagnostic codes and locations."""
    if isinstance(value, list):
        return [diagnostic_shape(item) for item in value]
    if isinstance(value, dict):
        shaped: dict[str, Json] = {}
        for key, item in value.items():
            if key == "message":
                continue
            shaped[key] = diagnostic_shape(item)
        return shaped
    return value


def tree_snapshot(directory: Path) -> dict[str, str]:
    return {
        path.relative_to(directory).as_posix(): path.read_text(encoding="utf-8")
        for path in sorted(directory.rglob("*"))
        if path.is_file() and "build" not in path.relative_to(directory).parts
    }


def run_case(binary: str, case: Case, temporary_root: Path, label: str, compiler: str) -> dict[str, Json]:
    directory = temporary_root / label
    directory.mkdir()
    if case.fixture:
        shutil.copytree(
            ROOT / case.fixture,
            directory,
            dirs_exist_ok=True,
            ignore=shutil.ignore_patterns("build", ".git"),
        )
    if case.stdin_crlf:
        (directory / "tests/input/stdin.stdin").write_bytes(b"piped input\r\n")

    command = [binary, *case.command]
    environment = os.environ.copy()
    if compiler:
        environment["YIANC"] = compiler
    else:
        environment.pop("YIANC", None)
    process = subprocess.run(
        command,
        cwd=directory,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    stdout: Json = process.stdout
    if case.json_output:
        try:
            stdout = json.loads(process.stdout)
        except json.JSONDecodeError:
            pass
    stdout = normalize(stdout, directory)
    stderr = normalize(process.stderr, directory)
    observation: dict[str, Json] = {
        "status": process.returncode,
        "stdout": stdout,
        "stderr": stderr,
    }

    if case.toml_message_flexible and case.json_output and isinstance(stdout, (dict, list)):
        observation["diagnostic_shape"] = diagnostic_shape(stdout)
    if case.tree:
        generated = directory / case.tree
        observation["tree"] = tree_snapshot(generated) if generated.is_dir() else "missing"
    if case.fixture:
        observation["source_tree"] = tree_snapshot(directory)
    for relative in ("build/pkg.json", "build/test/pkg.json"):
        package_map = directory / relative
        if package_map.is_file():
            observation[relative] = normalize(json.loads(package_map.read_text(encoding="utf-8")), directory)
    if case.executable and process.returncode == 0:
        executable = directory / case.executable
        if executable.is_file():
            executed = subprocess.run([str(executable), *case.executable_args], cwd=directory, capture_output=True, text=True, check=False)
            observation["execution"] = {
                "status": executed.returncode,
                "stdout": executed.stdout,
                "stderr": executed.stderr,
            }
        else:
            observation["execution"] = "missing executable"

    return {"command": command, "observation": observation, "raw_stdout": process.stdout, "raw_stderr": process.stderr}


def equivalent(case: Case, reference: dict[str, Json], native: dict[str, Json]) -> bool:
    reference_observation = reference["observation"]
    native_observation = native["observation"]
    if not isinstance(reference_observation, dict) or not isinstance(native_observation, dict):
        return reference_observation == native_observation
    if case.flexible_help:
        if reference_observation.get("status") != native_observation.get("status"):
            return False
        if case.name in ("no-command", "top-level-help"):
            text = f"{reference_observation.get('stdout', '')}\n{reference_observation.get('stderr', '')}"
            other = f"{native_observation.get('stdout', '')}\n{native_observation.get('stderr', '')}"
            expected = ("new", "build", "run", "check", "test", "fmt", "graph")
            return all(word in text and word in other for word in expected)
        return True
    if case.toml_message_flexible and case.json_output:
        return all(
            reference_observation.get(key) == native_observation.get(key)
            for key in ("status", "diagnostic_shape")
        )
    return reference_observation == native_observation


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reference", default="anx", help="Python anx executable (default: anx on PATH)")
    parser.add_argument("--native", required=True, help="Native anx executable")
    parser.add_argument("--report", type=Path, required=True, help="Write the complete JSON report here")
    parser.add_argument("--filter", default="", help="Run only case names containing this text")
    args = parser.parse_args()

    reference = shutil.which(args.reference)
    native_path = Path(args.native).expanduser().resolve()
    if reference is None or not native_path.is_file():
        parser.error("both anx executables must exist")
    reference_path = str(Path(reference).resolve())
    compiler = shutil.which("yianc") or ""
    selected = [case for case in cases() if args.filter in case.name]

    records: list[dict[str, Json]] = []
    failures = 0
    with tempfile.TemporaryDirectory(prefix="anx-parity-") as temporary:
        temporary_root = Path(temporary)
        for index, case in enumerate(selected):
            case_root = temporary_root / f"{index:03}-{case.name}"
            case_root.mkdir()
            reference_result = run_case(reference_path, case, case_root, "python", compiler)
            native_result = run_case(str(native_path), case, case_root, "native", compiler)
            equal = equivalent(case, reference_result, native_result)
            failures += int(not equal)
            records.append(
                {
                    "case": case.name,
                    "equal": equal,
                    "reference": reference_result,
                    "native": native_result,
                }
            )

    revision = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True, check=False
    ).stdout.strip()
    report = {
        "revision": revision,
        "reference": reference_path,
        "native": str(native_path),
        "compiler": compiler,
        "cases": records,
        "summary": {"total": len(records), "equal": len(records) - failures, "different": failures},
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"anx parity: {len(records) - failures}/{len(records)} cases match")
    print(f"report: {args.report}")
    for record in records:
        if record["equal"] is False:
            print(f"  different: {record['case']}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
