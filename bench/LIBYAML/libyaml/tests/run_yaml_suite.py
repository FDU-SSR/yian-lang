#!/usr/bin/env python3
"""Compare the pinned C libyaml oracle with the YIAN port over yaml-test-suite.

The runner is independent of the compiler, package and AWFY/BG suites.  It
selects concrete yaml-test-suite case directories, runs the C and YIAN
``--inspect`` protocol, and writes a self-contained correctness report under
``bench/LIBYAML/libyaml/reports``.  It never builds binaries, downloads data or
modifies the suite.

Usage:
  python3 run_yaml_suite.py SUITE [C_BINARY] [YIAN_BINARY] \
      (--case ID ... | --all | --list) [--mode tokens|events|documents] \
      [--pointer-mode raw|fat|both] [--timeout SECONDS] [--report-dir PATH] \
      [--chunk-size N] [--fail-after N]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import pathlib
import subprocess
import sys

__SCHEMA = "libyaml-report-v1"
__MODE_RECORD = {"tokens": "token", "events": "event", "documents": "document"}
__MODES = ("tokens", "events", "documents")


def __repo_root() -> pathlib.Path:
    return pathlib.Path(__file__).resolve().parents[4]


def __sha256_file(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def __sha256_files(root: pathlib.Path, paths: list[pathlib.Path]) -> str:
    digest = hashlib.sha256()
    for path in sorted(paths, key=lambda item: item.relative_to(root).as_posix()):
        relative = path.relative_to(root).as_posix().encode("utf-8")
        digest.update(len(relative).to_bytes(8, "big"))
        digest.update(relative)
        digest.update(bytes.fromhex(__sha256_file(path)))
    return digest.hexdigest()


def __source_identity() -> dict:
    root = __repo_root()
    yian_sources = sorted((root / "bench/LIBYAML/libyaml/yian/src").rglob("*.an"))
    c_root = root / "bench/LIBYAML/libyaml/c"
    c_sources = sorted(
        path
        for path in (c_root / "src").rglob("*")
        if path.is_file() and path.suffix in {".c", ".h"}
    )
    c_sources.extend(
        sorted(
            path
            for path in (c_root / "third_party").rglob("*")
            if path.is_file()
        )
    )
    revision = None
    try:
        revision = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "HEAD"],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        ).stdout.strip() or None
    except OSError:
        revision = None
    return {
        "revision": revision,
        "yian_sha256": __sha256_files(root, yian_sources),
        "c_sha256": __sha256_files(root, c_sources),
    }


def __suite_commit(suite: pathlib.Path) -> str | None:
    try:
        result = subprocess.run(
            ["git", "-C", str(suite), "rev-parse", "HEAD"],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except OSError:
        return None
    commit = result.stdout.strip()
    return commit or None


def __cases(suite: pathlib.Path) -> dict[str, pathlib.Path]:
    found: dict[str, pathlib.Path] = {}
    for path in sorted(suite.rglob("in.yaml")):
        if not path.is_file():
            continue
        relative = path.parent.relative_to(suite).as_posix()
        if relative == ".":
            raise ValueError("suite contains a root-level in.yaml; case selection requires directories")
        found[relative] = path
    return found


def __resolve_selection(
    suite: pathlib.Path, ids: list[str], all_cases: bool
) -> list[tuple[str, pathlib.Path]]:
    available = __cases(suite)
    if not available:
        raise ValueError(f"no in.yaml cases found under {suite}")
    if all_cases:
        return sorted(available.items())
    selected: list[tuple[str, pathlib.Path]] = []
    for identifier in ids:
        if identifier not in available:
            candidates = sorted(
                candidate for candidate in available
                if candidate.startswith(identifier + "/")
            )
            hint = f"; candidates: {', '.join(candidates[:8])}" if candidates else ""
            raise ValueError(f"invalid case ID {identifier!r}: no directory with in.yaml{hint}")
        selected.append((identifier, available[identifier]))
    return selected


def __parse(stdout: bytes, mode: str) -> tuple[str, list[dict], dict | None, str | None]:
    """Classify one inspection output as end/error/timeout-like malformed."""
    try:
        text = stdout.decode("utf-8")
    except UnicodeDecodeError:
        return "malformed", [], None, "output is not valid UTF-8"
    lines = [line for line in text.split("\n") if line]
    if not lines:
        return "malformed", [], None, "no output records"
    records: list[dict] = []
    for index, line in enumerate(lines):
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            return "malformed", records, None, f"line {index + 1} is not JSON"
        if not isinstance(value, dict):
            return "malformed", records, None, f"line {index + 1} is not a JSON object"
        records.append(value)
    header = records[0]
    if header.get("schema") != "yaml-compat-v1" or header.get("mode") != mode:
        return "malformed", records, None, "missing or mismatched protocol header"
    expected = __MODE_RECORD[mode]
    body = records[1:]
    if not body:
        return "malformed", [], None, "missing terminal record"
    last = body[-1]
    terminal_record = last.get("record")
    if terminal_record == "end":
        prior = body[:-1]
        for index, record in enumerate(prior):
            if record.get("record") != expected:
                return "malformed", [], None, f"unexpected record at line {index + 2}"
        return "end", prior, None, None
    if terminal_record == "error":
        prior = body[:-1]
        for index, record in enumerate(prior):
            if record.get("record") != expected:
                return "malformed", [], None, f"unexpected record at line {index + 2}"
        return "error", prior, last, None
    return "malformed", [], None, "missing end or error terminal record"


def __run(
    binary: pathlib.Path,
    case_path: pathlib.Path,
    mode: str,
    timeout: float,
    chunk_size: int,
    fail_after: int,
) -> tuple[dict, str, list[dict], dict | None]:
    command = [str(binary), "--inspect", mode, "--file", str(case_path)]
    if chunk_size > 0:
        command.extend(["--chunk-size", str(chunk_size)])
        if fail_after >= 0:
            command.extend(["--fail-after", str(fail_after)])
    try:
        completed = subprocess.run(command, capture_output=True, timeout=timeout)
    except subprocess.TimeoutExpired as expired:
        output = expired.stdout or b""
        leg = {
            "rc": None,
            "terminal": "timeout",
            "output_sha256": hashlib.sha256(output).hexdigest(),
        }
        return leg, "timeout", [], None
    except OSError as error:
        leg = {"rc": None, "terminal": "missing", "output_sha256": None}
        return leg, "missing", [], {"error": str(error)}
    terminal, prior, error_record, malformed = __parse(completed.stdout, mode)
    leg = {
        "rc": completed.returncode,
        "terminal": terminal if malformed is None else "malformed",
        "output_sha256": hashlib.sha256(completed.stdout).hexdigest(),
    }
    if malformed is not None:
        error_record = {"malformed": malformed}
    return leg, leg["terminal"], prior, error_record


def __case_result(
    legs: list[tuple[str, dict, str, list[dict], dict | None]],
) -> tuple[str, str]:
    """Return (result, reason) for the C leg first followed by YIAN legs."""
    c_name, c_leg, c_terminal, c_records, c_error = legs[0]
    if c_terminal not in ("end", "error"):
        return "failed", f"{c_name}: {c_terminal}"
    if c_terminal == "end":
        if c_leg["rc"] != 0:
            return "failed", f"{c_name}: end record with rc={c_leg['rc']}"
        for name, leg, terminal, records, _ in legs[1:]:
            if terminal != "end" or leg["rc"] != 0:
                return "failed", f"{name}: rc={leg['rc']} terminal={terminal}"
            if records != c_records:
                return "failed", f"{name}: structure differs from C"
        return "matched_success", ""
    if c_leg["rc"] != 1:
        return "failed", f"{c_name}: error record with rc={c_leg['rc']}"
    for name, leg, terminal, records, error in legs[1:]:
        if terminal != "error" or leg["rc"] != 1:
            return "failed", f"{name}: rc={leg['rc']} terminal={terminal}"
        if records != c_records or error != c_error:
            return "failed", f"{name}: rejection differs from C"
    return "matched_rejection", ""


def __leg_table(legs: list[tuple[str, dict, str, list[dict], dict | None]]) -> str:
    cells = []
    for name, leg, terminal, _, _ in legs:
        rc = leg["rc"] if leg["rc"] is not None else "null"
        cells.append(f"{name} rc={rc} {terminal}")
    return ", ".join(cells)


def __write_report(
    report_dir: pathlib.Path,
    mode: str,
    pointer_mode: str,
    document: dict,
) -> tuple[pathlib.Path, pathlib.Path]:
    report_dir.mkdir(parents=True, exist_ok=True)
    stem = f"correctness-{mode}-{pointer_mode}"
    json_path = report_dir / f"{stem}.json"
    md_path = report_dir / f"{stem}.md"
    __atomic_write(json_path, json.dumps(document, indent=2) + "\n")
    __atomic_write(md_path, __markdown(document))
    return json_path, md_path


def __atomic_write(path: pathlib.Path, text: str) -> None:
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        temporary.write_text(text, encoding="utf-8")
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def __markdown(document: dict) -> str:
    totals = document["totals"]
    lines = [
        f"# libyaml correctness report — {document['mode']}",
        "",
        f"- pointer modes: {', '.join(document['pointer_modes']) or 'none'}",
        f"- selection: {document['selection']['description']}",
        f"- suite: {document['selection']['suite']}",
        f"- suite commit: {document['suite_commit'] or 'unknown'}",
        f"- source revision: {document['source_identity']['revision'] or 'unknown'}",
        f"- yian sources sha256: {document['source_identity']['yian_sha256']}",
        f"- c sources sha256: {document['source_identity']['c_sha256']}",
    ]
    for name, identity in document["binary_identities"].items():
        if identity is None:
            lines.append(f"- binary {name}: not run")
        else:
            lines.append(f"- binary {name}: {identity['path']} ({identity['sha256']})")
    lines.extend([
        "",
        "| selected | matched_success | matched_rejection | failed | timeouts |",
        "| --- | --- | --- | --- | --- |",
        f"| {totals['selected']} | {totals['matched_success']} | "
        f"{totals['matched_rejection']} | {totals['failed']} | {totals['timeouts']} |",
        "",
        "| case | result | legs | reason |",
        "| --- | --- | --- | --- |",
    ])
    for case in document["cases"]:
        cells = []
        for name in ("c", "raw", "fat"):
            leg = case[name]
            if leg is None:
                continue
            rc = leg["rc"] if leg["rc"] is not None else "null"
            cells.append(f"{name} rc={rc} {leg['terminal']}")
        reason = case["reason"] or ""
        lines.append(
            f"| {case['id']} | {case['result']} | {'; '.join(cells)} | {reason} |"
        )
    lines.append("")
    return "\n".join(lines)


def __main() -> int:
    root = __repo_root()
    parser = argparse.ArgumentParser(
        description="Compare the pinned C libyaml oracle with the YIAN port."
    )
    parser.add_argument("suite", type=pathlib.Path)
    parser.add_argument(
        "c_binary",
        nargs="?",
        type=pathlib.Path,
        default=root / "build/bench/cbin/LIBYAML_libyaml",
    )
    parser.add_argument("yian_binary", nargs="?", type=pathlib.Path, default=None)
    parser.add_argument("--case", action="append", default=[], metavar="ID")
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--list", action="store_true")
    parser.add_argument("--mode", choices=__MODES, default="documents")
    parser.add_argument("--pointer-mode", choices=("raw", "fat", "both"), default="both")
    parser.add_argument("--timeout", type=float, default=5.0)
    parser.add_argument(
        "--report-dir",
        type=pathlib.Path,
        default=root / "bench/LIBYAML/libyaml/reports",
    )
    parser.add_argument("--chunk-size", type=int, default=0)
    parser.add_argument("--fail-after", type=int, default=-1)
    args = parser.parse_args()

    suite = args.suite.expanduser().resolve()
    if not suite.is_dir():
        parser.error(f"suite directory does not exist: {suite}")
    if args.timeout <= 0:
        parser.error("--timeout must be a positive number")
    if args.chunk_size < 0:
        parser.error("--chunk-size must be non-negative")
    if args.fail_after < -1:
        parser.error("--fail-after must be -1 or non-negative")
    if args.fail_after >= 0 and args.chunk_size == 0:
        parser.error("--fail-after requires a positive --chunk-size")
    if args.case and args.all:
        parser.error("--case and --all are mutually exclusive")
    if not args.case and not args.all and not args.list:
        parser.error("one of --case, --all or --list is required")
    try:
        if args.list and not args.case and not args.all:
            selection = __resolve_selection(suite, [], True)
            for identifier, _ in selection:
                print(identifier)
            return 0
        if args.list:
            selection = __resolve_selection(suite, args.case, args.all)
            for identifier, _ in selection:
                print(identifier)
            return 0
        if args.yian_binary is not None and args.pointer_mode == "both":
            parser.error("an explicit YIAN_BINARY requires --pointer-mode raw or fat")
        selection = __resolve_selection(suite, args.case, args.all)
    except ValueError as error:
        parser.error(str(error))

    yian_legs: list[tuple[str, pathlib.Path]] = []
    if args.yian_binary is not None:
        yian_legs.append((args.pointer_mode, args.yian_binary.expanduser().absolute()))
    else:
        for name in ("raw", "fat"):
            if args.pointer_mode in (name, "both"):
                yian_legs.append((name, root / f"build/bench/y{name}/LIBYAML_libyaml"))

    binaries: list[tuple[str, pathlib.Path]] = [
        ("c", args.c_binary.expanduser().absolute()),
        *yian_legs,
    ]
    for name, binary in binaries:
        if not binary.is_file() or not os.access(binary, os.X_OK):
            parser.error(f"{name} binary is missing or not executable: {binary}")

    identities = {
        "c": {"path": str(binaries[0][1]), "sha256": __sha256_file(binaries[0][1])},
    }
    for name, binary in yian_legs:
        identities[name] = {"path": str(binary), "sha256": __sha256_file(binary)}
    if args.yian_binary is not None:
        for name in ("raw", "fat"):
            if name not in identities:
                identities[name] = None

    pointer_modes = [name for name, _ in yian_legs]
    report_pointer_mode = (
        "both" if set(pointer_modes) == {"raw", "fat"} else pointer_modes[0]
    )
    selection_document = {
        "suite": str(suite),
        "description": "--all" if args.all else "cases " + ", ".join(args.case),
        "cases": [identifier for identifier, _ in selection],
    }
    suite_commit = __suite_commit(suite)
    source_identity = __source_identity()

    cases: list[dict] = []
    matched_success = 0
    matched_rejection = 0
    failed = 0
    timeouts = 0
    for identifier, case_path in selection:
        input_sha256 = __sha256_file(case_path)
        runs: list[tuple[str, dict, str, list[dict], dict | None]] = []
        for name, binary in binaries:
            leg, terminal, records, error = __run(
                binary,
                case_path,
                args.mode,
                args.timeout,
                args.chunk_size,
                args.fail_after,
            )
            runs.append((name, leg, terminal, records, error))
        if any(leg["terminal"] == "timeout" for _, leg, _, _, _ in runs):
            timeouts += 1
        result, reason = __case_result(runs)
        if result == "matched_success":
            matched_success += 1
        elif result == "matched_rejection":
            matched_rejection += 1
        else:
            failed += 1
        leg_by_name = {name: leg for name, leg, _, _, _ in runs}
        case_document = {
            "id": identifier,
            "input_sha256": input_sha256,
            "c": leg_by_name["c"],
            "raw": leg_by_name.get("raw"),
            "fat": leg_by_name.get("fat"),
            "result": result,
            "reason": reason,
        }
        cases.append(case_document)
        print(f"{identifier}: {result} ({__leg_table(runs)}){': ' + reason if reason else ''}")

    document = {
        "schema": __SCHEMA,
        "mode": args.mode,
        "pointer_modes": pointer_modes,
        "selection": selection_document,
        "suite_commit": suite_commit,
        "source_identity": source_identity,
        "binary_identities": identities,
        "cases": cases,
        "totals": {
            "selected": len(cases),
            "matched_success": matched_success,
            "matched_rejection": matched_rejection,
            "failed": failed,
            "timeouts": timeouts,
        },
    }
    json_path, md_path = __write_report(
        args.report_dir.expanduser().absolute(),
        args.mode,
        report_pointer_mode,
        document,
    )
    totals = document["totals"]
    print(
        f"selected={totals['selected']} matched_success={totals['matched_success']} "
        f"matched_rejection={totals['matched_rejection']} failed={totals['failed']} "
        f"timeouts={totals['timeouts']}"
    )
    print(f"report: {json_path}")
    print(f"summary: {md_path}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(__main())
