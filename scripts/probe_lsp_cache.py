#!/usr/bin/env python3
"""Small protocol probes for LSP input isolation, reuse, and cache behavior.

Each --case runs one deliberately small fixture. The first cases cover the
single-main-project routing contract; later plan tasks extend this entrypoint
with their own exact selectors.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import signal
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
ROOT = SCRIPT_DIR.parent
sys.path.insert(0, str(SCRIPT_DIR))

from check_lsp import ProtocolError, StdioSession, resolve_native  # noqa: E402


class ProbeFailure(Exception):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ProbeFailure(message)


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="")


def manifest(name: str, *, kind: str = "lib", dependencies: str = "") -> str:
    return (
        "[package]\n"
        f'name = "{name}"\n'
        f'kind = "{kind}"\n'
        'version = "0.1.0"\n'
        f"entry = \"src/main.an\"\n" if kind == "bin" else
        "[package]\n"
        f'name = "{name}"\n'
        f'kind = "{kind}"\n'
        'version = "0.1.0"\n'
    ) + ("\n[dependencies]\n" + dependencies if dependencies else "\n[dependencies]\n")


def source_position(text: str, token: str, occurrence: int = 0) -> dict[str, int]:
    offset = -1
    start = 0
    for _ in range(occurrence + 1):
        offset = text.index(token, start)
        start = offset + len(token)
    prefix = text[:offset]
    line = prefix.count("\n")
    column = offset - (prefix.rfind("\n") + 1)
    return {"line": line, "character": column}


def location_matches(value: Any, uri: str, text: str, token: str, occurrence: int = 0) -> bool:
    if isinstance(value, list):
        return len(value) == 1 and location_matches(value[0], uri, text, token, occurrence)
    if not isinstance(value, dict) or value.get("uri") != uri:
        return False
    position = source_position(text, token, occurrence)
    span = value.get("range")
    return (
        isinstance(span, dict)
        and span.get("start") == position
        and span.get("end") == {
            "line": position["line"],
            "character": position["character"] + len(token),
        }
    )


def open_document(client: StdioSession, path: Path, text: str, version: int = 1) -> dict[str, Any]:
    client.notify("textDocument/didOpen", {
        "textDocument": {
            "uri": path.as_uri(),
            "languageId": "yian",
            "version": version,
            "text": text,
        }
    })
    return client.wait_diagnostics(path.as_uri(), version)


def definition(client: StdioSession, path: Path, text: str, token: str,
               occurrence: int = 0) -> Any:
    return client.request("textDocument/definition", {
        "textDocument": {"uri": path.as_uri()},
        "position": source_position(text, token, occurrence),
    })


def start_client(native: Path, root: Path, trace: Path, timeout: float) -> StdioSession:
    environment = dict(os.environ)
    environment.setdefault("NO_COLOR", "1")
    environment["YIAN_LSP_TRACE_FILE"] = str(trace)
    client = StdioSession(
        [str(native), "--stdio", "--compiler-root", str(ROOT)],
        cwd=root,
        environment=environment,
        timeout=timeout,
    )
    try:
        client.initialize(root)
    except Exception:
        client.kill()
        raise
    return client


def wait_worker_pid(server_pid: int, timeout: float) -> int:
    children = Path(f"/proc/{server_pid}/task/{server_pid}/children")
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            pids = [int(value) for value in children.read_text().split()]
        except (OSError, ValueError):
            pids = []
        for pid in pids:
            try:
                command = Path(f"/proc/{pid}/cmdline").read_bytes().split(b"\0")
            except OSError:
                continue
            if b"--worker" in command:
                return pid
        time.sleep(0.01)
    raise ProbeFailure("could not identify this LSP session's direct worker child")


def wait_worker_stopped(worker_pid: int, timeout: float) -> None:
    status = Path(f"/proc/{worker_pid}/status")
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            state = next(
                line.split()[1]
                for line in status.read_text().splitlines()
                if line.startswith("State:")
            )
        except (OSError, StopIteration, IndexError):
            break
        if state == "T":
            return
        time.sleep(0.01)
    raise ProbeFailure("the selected LSP worker did not enter the stopped state")


def trace_events(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    events: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            events.append(value)
    return events


def wait_unit_sync(trace: Path, unit_key: str, count: int, timeout: float) -> list[dict[str, Any]]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        events = [
            event for event in trace_events(trace)
            if event.get("event") == "unit_sync_completed"
            and event.get("unit_key") == unit_key
        ]
        if len(events) >= count:
            return events
        time.sleep(0.01)
    observed = [
        {
            "unit_key": event.get("unit_key"),
            "revision": event.get("revision"),
            "strict_analysis_count": event.get("strict_analysis_count"),
        }
        for event in trace_events(trace)
        if event.get("event") == "unit_sync_completed"
    ]
    raise ProbeFailure(
        f"timed out waiting for {unit_key!r} sync trace #{count}; observed={observed!r}"
    )


def primary_sync_events(trace: Path) -> list[dict[str, Any]]:
    return [
        event for event in trace_events(trace)
        if event.get("event") == "unit_sync_completed"
        and event.get("unit_key") == "primary"
    ]


def wait_worker_analysis_finish(trace: Path, count: int,
                                timeout: float) -> list[dict[str, Any]]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        events = [
            event for event in trace_events(trace)
            if event.get("event") == "worker_operation_finished"
            and event.get("operation") == "analysis"
        ]
        if len(events) >= count:
            return events
        time.sleep(0.01)
    raise ProbeFailure(f"timed out waiting for worker analysis completion #{count}")


def wait_trace_event_count(trace: Path, event_name: str, count: int,
                           timeout: float) -> list[dict[str, Any]]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        events = [event for event in trace_events(trace)
                  if event.get("event") == event_name]
        if len(events) >= count:
            return events
        time.sleep(0.01)
    raise ProbeFailure(f"timed out waiting for trace event {event_name!r} #{count}")


def assert_zero_rebuild(event: dict[str, Any], label: str) -> None:
    for field in (
        "strict_analysis_count",
        "source_index_build_count",
        "semantic_index_build_count",
    ):
        require(event.get(field) == 0, f"{label} rebuilt {field}: {event!r}")
    require(event.get("reuse_kind") in ("memory", "failed_memory"),
            f"{label} did not report an in-memory result: {event!r}")


def run_fingerprint(yianc: Path, timeout: float) -> dict[str, str]:
    expected = {
        "empty": hashlib.sha256(b"").hexdigest(),
        "abc": hashlib.sha256(b"abc").hexdigest(),
    }
    program = (
        "from lsp.analysis.fingerprint import sha256;\n"
        "from std.core.drop import Drop;\n"
        "from std.core.string import String;\n"
        "fn main() {\n"
        '    let empty_bytes = String.from(&"");\n'
        '    let abc_bytes = String.from(&"abc");\n'
        '    let empty = sha256(empty_bytes.as_slice());\n'
        '    let abc = sha256(abc_bytes.as_slice());\n'
        f'    assert empty == "{expected["empty"]}": "empty SHA-256";\n'
        f'    assert abc == "{expected["abc"]}": "abc SHA-256";\n'
        "    empty.drop();\n"
        "    abc.drop();\n"
        "    empty_bytes.drop();\n"
        "    abc_bytes.drop();\n"
        "}\n"
    )
    with tempfile.TemporaryDirectory(prefix="yian-lsp-fingerprint-") as directory:
        temp_root = Path(directory)
        executable = temp_root / "fingerprint"
        probe_source = temp_root / "main.an"
        package_map = temp_root / "pkg.json"
        package_map.write_text(json.dumps({
            "format": 2,
            "root": "__probe",
            "packages": {
                "__probe": {
                    "sourceRoot": str(temp_root),
                    "kind": "bin",
                    "entry": str(probe_source),
                    "dependencies": ["lsp", "std"],
                    "ffi": False,
                },
                "lsp": {
                    "sourceRoot": str(ROOT / "lsp" / "src"),
                    "kind": "lib",
                    "dependencies": ["std"],
                    "ffi": False,
                },
                "std": {
                    "sourceRoot": str(ROOT / "lib" / "src"),
                    "kind": "lib",
                    "dependencies": [],
                    "ffi": True,
                },
            },
        }, separators=(",", ":")), encoding="utf-8")
        probe_source.write_text(program, encoding="utf-8", newline="")
        # The native frontend needs the std package's primitive impls and
        # prelude modules together; this keeps the probe to that 49-file
        # dependency plus the one LSP module under test and this tiny caller.
        sources = [
            *sorted((ROOT / "lib" / "src").rglob("*.an")),
            ROOT / "lsp" / "src" / "analysis" / "fingerprint.an",
            probe_source,
        ]
        compiled = subprocess.run(
            [
                str(yianc),
                "--compiler-root", str(ROOT),
                "--packages", str(package_map),
                "-o", str(executable),
                    *(str(source) for source in sources),
            ],
            cwd=ROOT,
            text=True,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
        require(compiled.returncode == 0,
                "native fingerprint probe compilation failed: "
                f"{compiled.stderr[-4000:]}{compiled.stdout[-1000:]}")
        executed = subprocess.run(
            [str(executable)],
            cwd=ROOT,
            text=True,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
        require(executed.returncode == 0,
                f"native fingerprint probe failed: {executed.stderr[-4000:]}")
        return expected


def run_unchanged(native: Path, timeout: float) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="yian-lsp-unchanged-") as directory:
        parent = Path(directory)
        project = parent / "project"
        main = project / "src" / "main.an"
        provider = project / "src" / "provider.an"
        main_text = "from app.provider import answer;\nfn main() { let result: i32 = answer; }\n"
        provider_text = "pub const answer: i32 = 42;\n"
        write(project / "package.anx", manifest("app", kind="bin"))
        write(main, main_text)
        write(provider, provider_text)
        trace = parent / "trace.jsonl"
        client = start_client(native, project, trace, timeout)
        try:
            main_diagnostics = open_document(client, main, main_text)
            require(main_diagnostics["params"].get("diagnostics") == [],
                    "the initial project analysis produced diagnostics")
            events = wait_unit_sync(trace, "primary", 1, timeout)
            initial_event_count = len(events)
            initial_counts = {
                field: sum(event.get(field, 0) for event in events)
                for field in (
                    "strict_analysis_count",
                    "source_index_build_count",
                    "semantic_index_build_count",
                )
            }
            require(initial_counts == {
                "strict_analysis_count": 1,
                "source_index_build_count": 1,
                "semantic_index_build_count": 1,
            }, f"initial project work did not analyze/build each stage once: {initial_counts!r}")

            def unchanged_after(label: str, diagnostics: dict[str, Any]) -> None:
                nonlocal events
                require(diagnostics["params"].get("diagnostics") == [],
                        f"{label} changed successful diagnostics")
                updated = wait_unit_sync(trace, "primary", len(events) + 1, timeout)
                for event in updated[len(events):]:
                    assert_zero_rebuild(event, label)
                events = updated

            provider_diagnostics = open_document(client, provider, provider_text)
            unchanged_after("opening an existing provider", provider_diagnostics)

            client.notify("textDocument/didChange", {
                "textDocument": {"uri": provider.as_uri(), "version": 2},
                "contentChanges": [{"text": provider_text}],
            })
            unchanged_after("same-text change", client.wait_diagnostics(provider.as_uri(), 2))

            client.notify("textDocument/didSave", {
                "textDocument": {"uri": provider.as_uri()},
            })
            unchanged_after("same-text save", client.wait_diagnostics(provider.as_uri(), 2))

            client.notify("workspace/didChangeWatchedFiles", {
                "changes": [{"uri": provider.as_uri(), "type": 2}],
            })
            unchanged_after("duplicate file watcher event",
                            client.wait_diagnostics(provider.as_uri(), 2))

            require(location_matches(
                definition(client, main, main_text, "answer", occurrence=1),
                provider.as_uri(), provider_text, "answer"),
                "navigation changed after same-content notifications")
            analysis_events = [
                event for event in trace_events(trace)
                if event.get("event") == "analysis_completed"
                and event.get("unit_key") == "primary"
            ]
            require(len(analysis_events) == 1,
                    f"unchanged events emitted extra strict analysis events: {analysis_events!r}")
            return_counts = {
                field: sum(event.get(field, 0) for event in events)
                for field in (
                    "strict_analysis_count",
                    "source_index_build_count",
                    "semantic_index_build_count",
                )
            }
            require(return_counts == initial_counts,
                    f"same-content events changed stage counts: {return_counts!r}")
            report = {
                "initial_counts": initial_counts,
                "same_content_syncs": len(events) - initial_event_count,
                "unchanged_counts": return_counts,
                "definition_preserved": True,
            }
        finally:
            client.shutdown()
    return report


def run_invalidation(native: Path, timeout: float) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="yian-lsp-invalidation-") as directory:
        parent = Path(directory)
        project = parent / "project"
        main = project / "src" / "main.an"
        provider = project / "src" / "provider.an"
        loose = parent / "loose.an"
        main_text = "from app.provider import answer;\nfn main() { let result: i32 = answer; }\n"
        provider_text = "pub const answer: i32 = 42;\n"
        broken_text = "pub const answer: i32 = Missing;\n"
        repaired_text = "// moved\npub const answer: i32 = 41;\n"
        write(project / "package.anx", manifest("app", kind="bin"))
        write(main, main_text)
        write(provider, provider_text)
        trace = parent / "trace.jsonl"
        client = start_client(native, project, trace, timeout)
        try:
            main_diagnostics = open_document(client, main, main_text)
            require(main_diagnostics["params"].get("diagnostics") == [],
                    "the initial project analysis produced diagnostics")
            events = wait_unit_sync(trace, "primary", 1, timeout)
            initial_count = len(events)
            initial_analysis = sum(event.get("strict_analysis_count", 0) for event in events)
            require(initial_analysis == 1,
                    f"initial project analysis count was {initial_analysis}, expected one")

            provider_diagnostics = open_document(client, provider, provider_text)
            require(provider_diagnostics["params"].get("diagnostics") == [],
                    "opening the unchanged provider produced diagnostics")
            events = wait_unit_sync(trace, "primary", len(events) + 1, timeout)
            require(all(event.get("strict_analysis_count") == 0 for event in events[initial_count:]),
                    "opening the existing provider rebuilt the project snapshot")

            client.notify("textDocument/didChange", {
                "textDocument": {"uri": provider.as_uri(), "version": 2},
                "contentChanges": [{"text": broken_text}],
            })
            broken_diagnostics = client.until(lambda message: (
                message.get("method") == "textDocument/publishDiagnostics"
                and isinstance(message.get("params"), dict)
                and message["params"].get("uri") == provider.as_uri()
                and message["params"].get("version") == 2
                and bool(message["params"].get("diagnostics"))
            ))
            require(broken_diagnostics["params"].get("diagnostics"),
                    "the invalid provider input was not reported")
            events = wait_unit_sync(trace, "primary", len(events) + 1, timeout)
            failed_event = events[-1]
            require(failed_event.get("reuse_kind") == "analyzed" and
                    failed_event.get("strict_analysis_count") == 1 and
                    failed_event.get("source_index_build_count") == 0 and
                    failed_event.get("semantic_index_build_count") == 0,
                    f"the failed input did not record one strict analysis and no indexes: {failed_event!r}")
            require(definition(client, main, main_text, "answer", occurrence=1) is None,
                    "the failed current input exposed stale navigation")

            client.notify("textDocument/didSave", {
                "textDocument": {"uri": provider.as_uri()},
            })
            saved_diagnostics = client.until(lambda message: (
                message.get("method") == "textDocument/publishDiagnostics"
                and isinstance(message.get("params"), dict)
                and message["params"].get("uri") == provider.as_uri()
                and message["params"].get("version") == 2
                and bool(message["params"].get("diagnostics"))
            ))
            require(saved_diagnostics["params"].get("diagnostics"),
                    "saving the unchanged failed input cleared its diagnostics")
            events = wait_unit_sync(trace, "primary", len(events) + 1, timeout)
            saved_failure = events[-1]
            assert_zero_rebuild(saved_failure, "same failed input save")
            require(saved_failure.get("reuse_kind") == "failed_memory",
                    f"the failed input was not reused from memory: {saved_failure!r}")

            client.notify("textDocument/didChange", {
                "textDocument": {"uri": provider.as_uri(), "version": 3},
                "contentChanges": [{"text": repaired_text}],
            })
            repaired_diagnostics = client.wait_diagnostics(provider.as_uri(), 3)
            require(repaired_diagnostics["params"].get("diagnostics") == [],
                    "the repaired input kept stale diagnostics")
            events = wait_unit_sync(trace, "primary", len(events) + 1, timeout)
            repaired = events[-1]
            require(repaired.get("reuse_kind") == "analyzed" and
                    repaired.get("strict_analysis_count") == 1 and
                    repaired.get("source_index_build_count") == 1 and
                    repaired.get("semantic_index_build_count") == 1,
                    f"the repaired input did not rebuild current indexes: {repaired!r}")
            require(location_matches(
                definition(client, main, main_text, "answer", occurrence=1),
                provider.as_uri(), repaired_text, "answer"),
                "the repaired input did not return the new declaration range")

            loose_text = "pub const local: i32 = 3;\n"
            primary_before_standalone = len(primary_sync_events(trace))
            loose_diagnostics = open_document(client, loose, loose_text)
            require(loose_diagnostics["params"].get("diagnostics") == [],
                    "the standalone file was not analyzed independently")
            loose_key = f"file:{loose.resolve()}"
            standalone_events = wait_unit_sync(trace, loose_key, 1, timeout)
            require(standalone_events[-1].get("strict_analysis_count") == 1,
                    f"the standalone unit did not start from one strict analysis: "
                    f"{standalone_events[-1]!r}")
            require(len(primary_sync_events(trace)) == primary_before_standalone,
                    "opening an independent file refreshed the primary unit")

            analyses_before_close = len(wait_worker_analysis_finish(trace, 1, timeout))
            client.notify("textDocument/didClose", {
                "textDocument": {"uri": loose.as_uri()},
            })
            client.until(lambda message: (
                message.get("method") == "textDocument/publishDiagnostics"
                and isinstance(message.get("params"), dict)
                and message["params"].get("uri") == loose.as_uri()
                and message["params"].get("diagnostics") == []
            ))
            close_barrier = client.request_message("yian-probe/processed", {})
            require(close_barrier.get("error", {}).get("code") == -32601,
                    "the standalone close processing barrier did not reject the probe method")
            wait_worker_analysis_finish(trace, analyses_before_close + 1, timeout)
            require(len(primary_sync_events(trace)) == primary_before_standalone,
                    "closing an independent file refreshed the primary unit")
            require(location_matches(
                definition(client, main, main_text, "answer", occurrence=1),
                provider.as_uri(), repaired_text, "answer"),
                "the primary project lost navigation after an independent close")

            analysis_events = [
                event for event in trace_events(trace)
                if event.get("event") == "analysis_completed"
                and event.get("unit_key") == "primary"
            ]
            strict_total = sum(event.get("strict_analysis_count", 0) for event in analysis_events)
            source_total = sum(event.get("source_index_build_count", 0) for event in analysis_events)
            semantic_total = sum(event.get("semantic_index_build_count", 0) for event in analysis_events)
            require((strict_total, source_total, semantic_total) == (3, 2, 2),
                    "valid-invalid-repair stage counts were "
                    f"{(strict_total, source_total, semantic_total)!r}, expected (3, 2, 2)")
            return {
                "valid_invalid_repair_counts": {
                    "strict_analysis": strict_total,
                    "source_index": source_total,
                    "semantic_index": semantic_total,
                },
                "same_failure_reused": True,
                "old_navigation_hidden": True,
                "repair_navigation_moved": True,
                "standalone_open_close_preserved_primary": True,
            }
        finally:
            client.shutdown()


def run_unit_isolation(native: Path, timeout: float) -> dict[str, Any]:
    report: dict[str, Any] = {}
    with tempfile.TemporaryDirectory(prefix="yian-lsp-unit-isolation-") as directory:
        parent = Path(directory)
        primary = parent / "primary"
        unrelated = parent / "other-project"
        main = primary / "src" / "main.an"
        provider = primary / "src" / "provider.an"
        other = unrelated / "src" / "entry.an"
        sibling = unrelated / "src" / "sibling.an"
        loose = parent / "loose.an"
        main_text = "from app.provider import answer;\nfn main() { let result: i32 = answer; }\n"
        provider_text = "pub const answer: i32 = 42;\n"
        other_text = "from bapp.sibling import hidden;\nfn read() -> i32 { return hidden; }\n"
        loose_text = "from bapp.sibling import hidden;\nfn read() -> i32 { return hidden; }\n"
        write(primary / "package.anx", manifest("app", kind="bin"))
        write(main, main_text)
        write(provider, provider_text)
        write(unrelated / "package.anx", manifest("bapp"))
        write(other, other_text)
        write(sibling, "pub const hidden: i32 = 17;\n")
        write(loose, loose_text)

        trace = parent / "trace.jsonl"
        client = start_client(native, primary, trace, timeout)
        worker_pid = 0
        stopped = False
        try:
            main_diagnostics = open_document(client, main, main_text)
            other_diagnostics = open_document(client, other, other_text)
            loose_diagnostics = open_document(client, loose, loose_text)
            require(main_diagnostics["params"].get("diagnostics") == [],
                    "the primary project produced diagnostics")
            require(other_diagnostics["params"].get("diagnostics"),
                    "the unrelated project's manifest or sibling source was used")
            require(loose_diagnostics["params"].get("diagnostics"),
                    "the loose file unexpectedly resolved another project's import")
            require(location_matches(
                definition(client, main, main_text, "answer", occurrence=1),
                provider.as_uri(), provider_text, "answer"),
                "the primary project lost navigation after unrelated files failed")
            require(definition(client, other, other_text, "hidden", occurrence=1) is None,
                    "the unrelated file navigated through its unopened sibling")

            worker_pid = wait_worker_pid(client.process.pid, timeout)
            os.kill(worker_pid, signal.SIGSTOP)
            stopped = True
            wait_worker_stopped(worker_pid, timeout)
            client.next_id += 1
            query_id = client.next_id
            client._StdioSession__send({
                "jsonrpc": "2.0",
                "id": query_id,
                "method": "textDocument/definition",
                "params": {
                    "textDocument": {"uri": main.as_uri()},
                    "position": source_position(main_text, "answer", 1),
                },
            })
            changed_other = other_text.replace("hidden", "missing")
            client.notify("textDocument/didChange", {
                "textDocument": {"uri": other.as_uri(), "version": 2},
                "contentChanges": [{"text": changed_other}],
            })
            barrier = client.request_message("yian-probe/processed", {})
            require(barrier.get("error", {}).get("code") == -32601,
                    "the protocol processing barrier did not reject the probe method")
            os.kill(worker_pid, signal.SIGCONT)
            stopped = False
            response = client.until(lambda value: value.get("id") == query_id)
            require("error" not in response,
                    f"an unrelated edit invalidated a pending primary query: {response!r}")
            require(location_matches(response.get("result"), provider.as_uri(),
                                     provider_text, "answer"),
                    "the pending primary query returned the wrong definition")

            # Revision invalidation publishes an immediate empty diagnostic at the
            # new version. Wait for the final analysis publication containing the
            # expected unresolved-import diagnostic instead of accepting that clear.
            changed_diagnostics = client.until(lambda message: (
                message.get("method") == "textDocument/publishDiagnostics"
                and isinstance(message.get("params"), dict)
                and message["params"].get("uri") == other.as_uri()
                and message["params"].get("version") == 2
                and bool(message["params"].get("diagnostics"))
            ))
            require(changed_diagnostics["params"].get("diagnostics"),
                    "the unrelated file's changed error was not reported")
            fixed_loose = "pub const lone: i32 = 3;\n"
            client.notify("textDocument/didChange", {
                "textDocument": {"uri": loose.as_uri(), "version": 2},
                "contentChanges": [{"text": fixed_loose}],
            })
            fixed_diagnostics = client.wait_diagnostics(loose.as_uri(), 2)
            require(fixed_diagnostics["params"].get("diagnostics") == [],
                    "the standalone unit did not recover independently")
            require(location_matches(
                definition(client, main, main_text, "answer", occurrence=1),
                provider.as_uri(), provider_text, "answer"),
                "the primary project lost navigation after a standalone edit")
            report.update({
                "primary_diagnostics": len(main_diagnostics["params"].get("diagnostics", [])),
                "other_project_diagnostics": len(other_diagnostics["params"].get("diagnostics", [])),
                "loose_diagnostics": len(loose_diagnostics["params"].get("diagnostics", [])),
                "pending_primary_query_survived_other_edit": True,
                "standalone_recovered": True,
            })
        finally:
            if stopped:
                try:
                    os.kill(worker_pid, signal.SIGCONT)
                except ProcessLookupError:
                    pass
            client.shutdown()
    return report


def run_primary_routing(native: Path, timeout: float) -> dict[str, Any]:
    report: dict[str, Any] = {}
    with tempfile.TemporaryDirectory(prefix="yian-lsp-primary-routing-") as directory:
        parent = Path(directory)
        primary = parent / "primary"
        dependency = parent / "dep"
        extra = parent / "extra"
        main = primary / "src" / "main.an"
        dep_provider = dependency / "src" / "provider.an"
        dep_helper = dependency / "src" / "helper.an"
        extra_provider = extra / "src" / "provider.an"
        extra_helper = extra / "src" / "helper.an"
        main_text = "from dep.provider import read;\nfn main() { let result: i32 = read(); }\n"
        dep_provider_text = "from dep.helper import answer;\npub fn read() -> i32 { return answer; }\n"
        dep_helper_text = "pub const answer: i32 = 41;\n"
        extra_provider_text = "from extra.helper import code;\npub fn fetch() -> i32 { return code; }\n"
        extra_helper_text = "pub const code: i32 = 9;\n"
        initial_dependencies = 'dep = { path = "../dep" }\n'
        updated_dependencies = initial_dependencies + 'extra = { path = "../extra" }\n'
        primary_manifest = primary / "package.anx"
        write(primary_manifest, manifest("app", kind="bin", dependencies=initial_dependencies))
        write(main, main_text)
        write(dependency / "package.anx", manifest("dep"))
        write(dep_provider, dep_provider_text)
        write(dep_helper, dep_helper_text)
        write(extra / "package.anx", manifest("extra"))
        write(extra_provider, extra_provider_text)
        write(extra_helper, extra_helper_text)

        trace = parent / "trace.jsonl"
        client = start_client(native, primary, trace, timeout)
        worker_pid = 0
        stopped = False
        try:
            open_document(client, main, main_text)
            dep_diagnostics = open_document(client, dep_provider, dep_provider_text)
            extra_diagnostics = open_document(client, extra_provider, extra_provider_text)
            require(dep_diagnostics["params"].get("diagnostics") == [],
                    "a real primary dependency was not analyzed in the project context")
            require(extra_diagnostics["params"].get("diagnostics"),
                    "an unrelated package was loaded before it became a dependency")
            require(location_matches(
                definition(client, dep_provider, dep_provider_text, "answer", occurrence=1),
                dep_helper.as_uri(), dep_helper_text, "answer"),
                "the dependency source did not use the primary package map")
            require(definition(client, extra_provider, extra_provider_text,
                               "code", occurrence=1) is None,
                    "the unrelated package resolved through its own package manifest")

            worker_pid = wait_worker_pid(client.process.pid, timeout)
            os.kill(worker_pid, signal.SIGSTOP)
            stopped = True
            wait_worker_stopped(worker_pid, timeout)
            client.next_id += 1
            stale_query_id = client.next_id
            client._StdioSession__send({
                "jsonrpc": "2.0",
                "id": stale_query_id,
                "method": "textDocument/definition",
                "params": {
                    "textDocument": {"uri": extra_provider.as_uri()},
                    "position": source_position(extra_provider_text, "code", 1),
                },
            })
            write(primary_manifest, manifest("app", kind="bin", dependencies=updated_dependencies))
            client.notify("textDocument/didSave", {
                "textDocument": {"uri": primary_manifest.as_uri()},
            })
            barrier = client.request_message("yian-probe/processed", {})
            require(barrier.get("error", {}).get("code") == -32601,
                    "the topology processing barrier did not reject the probe method")
            os.kill(worker_pid, signal.SIGCONT)
            stopped = False
            stale_query = client.until(lambda value: value.get("id") == stale_query_id)
            require(stale_query.get("error", {}).get("code") == -32801,
                    f"a request from the old standalone route was not rejected: {stale_query!r}")
            client.until(lambda message: (
                message.get("method") == "textDocument/publishDiagnostics"
                and isinstance(message.get("params"), dict)
                and message["params"].get("uri") == extra_provider.as_uri()
                and message["params"].get("diagnostics") == []
            ))
            migrated_definition = definition(client, extra_provider, extra_provider_text,
                                             "code", occurrence=1)
            require(location_matches(migrated_definition, extra_helper.as_uri(),
                                     extra_helper_text, "code"),
                    "the opened file did not migrate to the newly updated primary dependency graph")
            messages = [
                message["params"]
                for message in client.messages
                if message.get("method") == "textDocument/publishDiagnostics"
                and isinstance(message.get("params"), dict)
                and message["params"].get("uri") == extra_provider.as_uri()
                and message["params"].get("version") == 1
            ]
            require(messages and messages[-1].get("diagnostics") == [],
                    "the migrated file kept diagnostics from its old standalone route")
            report.update({
                "primary_dependency_navigation": True,
                "unrelated_package_initially_isolated": True,
                "dependency_addition_migrated_open_file": True,
                "migrated_diagnostics": len(messages[-1].get("diagnostics", [])),
            })
        finally:
            if stopped:
                try:
                    os.kill(worker_pid, signal.SIGCONT)
                except ProcessLookupError:
                    pass
            client.shutdown()
    return report


def run_coalesce(native: Path, timeout: float) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="yian-lsp-coalesce-") as directory:
        parent = Path(directory)
        project = parent / "project"
        main = project / "src" / "main.an"
        provider = project / "src" / "provider.an"
        main_text = "from app.provider import answer;\nfn main() { let result: i32 = answer; }\n"
        provider_text = "pub const answer: i32 = 42;\n"
        invalid_text = "pub const answer: Missing = 10;\n"
        latest_text = "\n\npub const answer: i32 = 55;\n"
        write(project / "package.anx", manifest("app", kind="bin"))
        write(main, main_text)
        write(provider, provider_text)
        trace = parent / "trace.jsonl"
        client = start_client(native, project, trace, timeout)
        worker_pid = 0
        stopped = False
        try:
            diagnostics = open_document(client, main, main_text)
            require(diagnostics["params"].get("diagnostics") == [],
                    "the coalescing fixture did not start from a valid analysis")
            baseline_syncs = wait_unit_sync(trace, "primary", 1, timeout)
            baseline_queued = wait_trace_event_count(
                trace, "server_sync_queued", 1, timeout)
            baseline_syncs = wait_unit_sync(
                trace, "primary", len(baseline_queued), timeout)
            baseline_analyses = [event for event in trace_events(trace)
                                 if event.get("event") == "analysis_completed"
                                 and event.get("unit_key") == "primary"]
            require(baseline_syncs, "the initial unit sync was not traced")

            worker_pid = wait_worker_pid(client.process.pid, timeout)
            status_text = Path(f"/proc/{worker_pid}/status").read_text()
            parent_line = next(line for line in status_text.splitlines()
                               if line.startswith("PPid:"))
            require(int(parent_line.split()[1]) == client.process.pid,
                    "the selected worker is not a direct child of this probe server")
            os.kill(worker_pid, signal.SIGSTOP)
            stopped = True
            wait_worker_stopped(worker_pid, timeout)

            stale_query_id = client.next_id + 1
            client.next_id = stale_query_id
            client._StdioSession__send({
                "jsonrpc": "2.0",
                "id": stale_query_id,
                "method": "textDocument/definition",
                "params": {
                    "textDocument": {"uri": main.as_uri()},
                    "position": source_position(main_text, "answer", 1),
                },
            })
            client.notify("textDocument/didChange", {
                "textDocument": {"uri": provider.as_uri(), "version": 2},
                "contentChanges": [{"text": invalid_text}],
            })
            stale_response = client.until(lambda value: value.get("id") == stale_query_id)
            require(stale_response.get("error", {}).get("code") == -32801,
                    f"an old in-flight query was not rejected by unit revision: {stale_response!r}")

            queued = wait_trace_event_count(
                trace, "server_sync_queued", len(baseline_queued) + 1, timeout)
            require(len(queued) == len(baseline_queued) + 1 and
                    queued[-1].get("unit_key") == "primary" and
                    queued[-1].get("refresh_all") is False,
                    f"version 2 did not produce one selected-unit in-flight sync: {queued!r}")

            for version, text in (
                (3, "// middle version 3\n" + invalid_text),
                (4, "// middle version 4\n// another line\n" + invalid_text),
                (5, latest_text),
            ):
                client.notify("textDocument/didChange", {
                    "textDocument": {"uri": provider.as_uri(), "version": version},
                    "contentChanges": [{"text": text}],
                })

            cancelled_query_id = client.next_id + 1
            client.next_id = cancelled_query_id
            client._StdioSession__send({
                "jsonrpc": "2.0",
                "id": cancelled_query_id,
                "method": "textDocument/definition",
                "params": {
                    "textDocument": {"uri": main.as_uri()},
                    "position": source_position(main_text, "answer", 1),
                },
            })
            client.notify("$/cancelRequest", {"id": cancelled_query_id})
            time.sleep(0.7)
            barrier = client.request_message("yian-probe/processed", {})
            require(barrier.get("error", {}).get("code") == -32601,
                    "the protocol processing barrier did not reject the probe method")
            cancelled = client.until(lambda value: value.get("id") == cancelled_query_id)
            require(cancelled.get("error", {}).get("code") == -32800,
                    f"a deferred query did not return RequestCancelled: {cancelled!r}")
            queued_while_stopped = wait_trace_event_count(
                trace, "server_sync_queued", len(queued), timeout)
            require(len(queued_while_stopped) == len(queued),
                    "intermediate versions were dispatched while the old sync was in flight")

            os.kill(worker_pid, signal.SIGCONT)
            stopped = False
            latest_diagnostics = client.wait_diagnostics(provider.as_uri(), 5)
            require(latest_diagnostics["params"].get("diagnostics") == [],
                    "the latest valid provider input did not replace the stale failure")
            final_queued = wait_trace_event_count(
                trace, "server_sync_queued", len(queued) + 1, timeout)
            require(len(final_queued) == len(queued) + 1 and
                    final_queued[-1].get("unit_key") == "primary" and
                    final_queued[-1].get("refresh_all") is False,
                    f"coalesced versions did not produce exactly one latest sync: {final_queued!r}")
            require(final_queued[-1].get("unit_revision") == 5,
                    f"the queued sync did not carry the latest unit revision: {final_queued[-1]!r}")
            result = definition(client, main, main_text, "answer", occurrence=1)
            require(location_matches(result, provider.as_uri(), latest_text, "answer"),
                    "the latest sync did not expose the final provider location")
            final_analyses = [event for event in trace_events(trace)
                              if event.get("event") == "analysis_completed"
                              and event.get("unit_key") == "primary"]
            require(len(final_analyses) == len(baseline_analyses) + 2,
                    "middle versions were strictly analyzed instead of coalesced")
            return {
                "initial_syncs": len(baseline_queued),
                "in_flight_old_sync": True,
                "intermediate_versions_coalesced": [3, 4],
                "latest_version_dispatched": 5,
                "stale_query_code": stale_response.get("error", {}).get("code"),
                "cancelled_query_code": cancelled.get("error", {}).get("code"),
                "strict_analysis_events": len(final_analyses),
                "latest_definition_line": result["range"]["start"]["line"],
            }
        finally:
            if stopped:
                try:
                    os.kill(worker_pid, signal.SIGCONT)
                except ProcessLookupError:
                    pass
            client.shutdown()


def run_watch_roots(native: Path, timeout: float) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="yian-lsp-watch-roots-") as directory:
        parent = Path(directory)
        project = parent / "project"
        dependency = parent / "outside-dependency"
        main = project / "src" / "main.an"
        provider = dependency / "src" / "provider.an"
        main_text = "from dep.provider import answer;\nfn main() { let result: i32 = answer; }\n"
        provider_text = "pub const answer: i32 = 42;\n"
        changed_provider_text = "// moved by a watched dependency edit\n\npub const answer: i32 = 43;\n"
        write(project / "package.anx", manifest(
            "app", kind="bin", dependencies='dep = { path = "../outside-dependency" }\n'))
        write(main, main_text)
        write(dependency / "package.anx", manifest("dep"))
        write(provider, provider_text)
        trace = parent / "trace.jsonl"
        client = start_client(native, project, trace, timeout)
        try:
            diagnostics = open_document(client, main, main_text)
            require(diagnostics["params"].get("diagnostics") == [],
                    "the watched-root project did not analyze successfully")
            initial_queued = wait_trace_event_count(
                trace, "server_sync_queued", 1, timeout)
            initial_events = wait_unit_sync(
                trace, "primary", len(initial_queued), timeout)
            require(location_matches(
                definition(client, main, main_text, "answer", occurrence=1),
                provider.as_uri(), provider_text, "answer"),
                "the external dependency was not part of the primary input graph")

            registrations = [message for message in client.messages
                             if message.get("method") == "client/registerCapability"]
            require(registrations, "the client did not receive watcher registration")
            watcher_lists: list[list[dict[str, Any]]] = []
            for message in registrations:
                params = message.get("params")
                if not isinstance(params, dict):
                    continue
                entries = params.get("registrations")
                if not isinstance(entries, list) or not entries:
                    continue
                options = entries[0].get("registerOptions", {})
                watchers = options.get("watchers", []) if isinstance(options, dict) else []
                if isinstance(watchers, list):
                    watcher_lists.append(watchers)
            watchers = watcher_lists[-1] if watcher_lists else []
            relative_roots = {
                watcher.get("globPattern", {}).get("baseUri")
                for watcher in watchers
                if isinstance(watcher.get("globPattern"), dict)
            }
            expected_roots = {
                (project / "src").as_uri(),
                (dependency / "src").as_uri(),
                (project).as_uri(),
                (dependency).as_uri(),
                (ROOT / "lib" / "src").as_uri(),
            }
            require(expected_roots <= relative_roots,
                    f"watcher roots omitted actual project/dependency/manifest/std roots: "
                    f"expected={sorted(expected_roots)!r} actual={sorted(relative_roots)!r}")
            watcher_keys = [
                (watcher.get("globPattern", {}).get("baseUri"),
                 watcher.get("globPattern", {}).get("pattern"))
                for watcher in watchers
                if isinstance(watcher.get("globPattern"), dict)
            ]
            require(len(watcher_keys) == len(set(watcher_keys)),
                    "watcher registration contains duplicate root/pattern pairs")

            write(provider, changed_provider_text)
            before_dependency_event = len(initial_events)
            client.notify("workspace/didChangeWatchedFiles", {
                "changes": [{"uri": provider.as_uri(), "type": 2}],
            })
            barrier = client.request_message("yian-probe/processed", {})
            require(barrier.get("error", {}).get("code") == -32601,
                    "the dependency watcher processing barrier did not reject the probe method")
            changed_events = wait_unit_sync(
                trace, "primary", before_dependency_event + 1, timeout)
            require(location_matches(
                definition(client, main, main_text, "answer", occurrence=1),
                provider.as_uri(), changed_provider_text, "answer"),
                "the external dependency edit did not refresh primary navigation")

            before_cache_event = len(changed_events)
            cache_path = project / "build" / "lsp" / "v1-fat.json"
            write(cache_path, "{}\n")
            client.notify("workspace/didChangeWatchedFiles", {
                "changes": [{"uri": cache_path.as_uri(), "type": 2}],
            })
            cache_barrier = client.request_message("yian-probe/cache-barrier", {})
            require(cache_barrier.get("error", {}).get("code") == -32601,
                    "the cache notification processing barrier did not reject the probe method")
            time.sleep(0.05)
            final_events = [event for event in trace_events(trace)
                            if event.get("event") == "unit_sync_completed"
                            and event.get("unit_key") == "primary"]
            require(len(final_events) == before_cache_event,
                    "a build/lsp cache path scheduled analysis")
            return {
                "registered_roots": sorted(relative_roots),
                "external_dependency_refreshed": True,
                "cache_notification_ignored": True,
                "unit_sync_count": len(final_events),
            }
        finally:
            client.shutdown()


def source_position_utf16(text: str, token: str, occurrence: int = 0) -> dict[str, int]:
    offset = -1
    start = 0
    for _ in range(occurrence + 1):
        offset = text.index(token, start)
        start = offset + len(token)
    prefix = text[:offset]
    line = prefix.count("\n")
    column = len(prefix[prefix.rfind("\n") + 1:].encode("utf-16-le")) // 2
    return {"line": line, "character": column}


def request_position(client: StdioSession, method: str, path: Path, text: str,
                     token: str, occurrence: int = 0, extra: dict[str, Any] | None = None) -> Any:
    params: dict[str, Any] = {
        "textDocument": {"uri": path.as_uri()},
        "position": source_position_utf16(text, token, occurrence),
    }
    if extra:
        params.update(extra)
    return client.request(method, params)


def query_bundle(client: StdioSession, path: Path, text: str,
                 symbols: list[str]) -> dict[str, Any]:
    result: dict[str, Any] = {
        "documentSymbol": client.request("textDocument/documentSymbol", {
            "textDocument": {"uri": path.as_uri()},
        }),
        "semanticTokens": client.request("textDocument/semanticTokens/full", {
            "textDocument": {"uri": path.as_uri()},
        }),
    }
    for symbol in symbols:
        result[f"definition:{symbol}"] = request_position(
            client, "textDocument/definition", path, text, symbol, 0)
        result[f"hover:{symbol}"] = request_position(
            client, "textDocument/hover", path, text, symbol, 0)
        result[f"highlight:{symbol}"] = request_position(
            client, "textDocument/documentHighlight", path, text, symbol, 0)
        result[f"references:{symbol}"] = request_position(
            client, "textDocument/references", path, text, symbol, 0,
            {"context": {"includeDeclaration": True}})
    return result


def wait_for_cache(native: Path, project: Path, trace: Path, timeout: float,
                   main: Path, text: str, version: int = 1) -> tuple[StdioSession, dict[str, Any], list[dict[str, Any]]]:
    client = start_client(native, project, trace, timeout)
    diagnostics = open_document(client, main, text, version=version)
    events = wait_unit_sync(trace, "primary", 1, timeout)
    return client, diagnostics, events


RESTART_MAIN_TEXT = """pub const answer: i32 = 42;

fn classify(value: i32) -> i32 {
    match value {
        _ => 1,
        0 => 2,
    }
}

fn main() { let result: i32 = classify(answer); }
"""


def run_detached_queries(native: Path, timeout: float) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="yian-lsp-detached-") as directory:
        parent = Path(directory)
        project = parent / "project"
        main = project / "src" / "main.an"
        write(project / "package.anx", manifest("app", kind="bin"))
        write(main, RESTART_MAIN_TEXT)
        trace = parent / "trace.jsonl"
        client, diagnostics, events = wait_for_cache(
            native, project, trace, timeout, main, RESTART_MAIN_TEXT)
        try:
            require(events[-1].get("query_index_build_count") == 1,
                    "the analyzed snapshot did not build the query index once")
            require(events[-1].get("session_released") is True,
                    "the analyzed snapshot did not report released compiler state")
            baseline = query_bundle(client, main, RESTART_MAIN_TEXT, ["classify", "answer"])
            require(any(isinstance(value, dict) and value.get("range")
                        for value in baseline.values() if not isinstance(value, list)),
                    "queries returned no positioned results")
            warnings = diagnostics["params"].get("diagnostics") or []
            require(any(item.get("code") == "W501" for item in warnings),
                    f"detached warning diagnostics were not published: {warnings!r}")
            before_syncs = len([event for event in trace_events(trace)
                                if event.get("event") == "unit_sync_completed"
                                and event.get("unit_key") == "primary"])
            before_analyses = len([event for event in trace_events(trace)
                                   if event.get("event") == "analysis_completed"])
            for _ in range(2):
                require(query_bundle(client, main, RESTART_MAIN_TEXT, ["classify", "answer"]) == baseline,
                        "released-session queries changed between identical requests")
            require(len([event for event in trace_events(trace)
                         if event.get("event") == "unit_sync_completed"
                         and event.get("unit_key") == "primary"]) == before_syncs,
                    "queries triggered new unit synchronization")
            require(len([event for event in trace_events(trace)
                         if event.get("event") == "analysis_completed"]) == before_analyses,
                    "queries triggered strict analysis after extraction")
            return {"queries_stable": True, "warning_preserved": True,
                    "strict_analysis_after_query": 0}
        finally:
            client.shutdown()


def run_positions(native: Path, timeout: float) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="yian-lsp-positions-") as directory:
        parent = Path(directory)
        project = parent / "project"
        main = project / "src" / "main.an"
        text = (
            "pub const answer: i32 = 42;\n"
            "fn main() {\n"
            "    let note: str = \"中文😀\"; let value: i32 = answer;\n"
            "}\n"
        )
        write(project / "package.anx", manifest("app", kind="bin"))
        write(main, text)
        trace = parent / "trace.jsonl"
        client, diagnostics, events = wait_for_cache(native, project, trace, timeout, main, text)
        try:
            require(diagnostics["params"].get("diagnostics") == [],
                    "the position fixture did not analyze successfully")
            position = source_position_utf16(text, "answer", 1)
            require(position["character"] != source_position(text, "answer", 1)["character"],
                    "the position fixture does not distinguish UTF-16 from scalar columns")
            definition_value = request_position(
                client, "textDocument/definition", main, text, "answer", 1)
            require(location_matches(definition_value, main.as_uri(), text, "answer", 0),
                    f"UTF-16 definition missed the declaration: {definition_value!r}")
            hover = request_position(client, "textDocument/hover", main, text, "answer", 1)
            require(isinstance(hover, dict) and hover.get("contents"),
                    f"UTF-16 hover returned no content: {hover!r}")
            highlight = request_position(
                client, "textDocument/documentHighlight", main, text, "answer", 1)
            require(isinstance(highlight, list) and highlight,
                    f"UTF-16 highlight returned no occurrence: {highlight!r}")
            tokens = client.request("textDocument/semanticTokens/full", {
                "textDocument": {"uri": main.as_uri()},
            })
            data = tokens.get("data") if isinstance(tokens, dict) else None
            require(isinstance(data, list) and data,
                    "semantic tokens were empty for the position fixture")
            before = len([event for event in trace_events(trace)
                          if event.get("event") == "unit_sync_completed"
                          and event.get("unit_key") == "primary"])
            require(query_bundle(client, main, text, ["answer"])["definition:answer"] == definition_value,
                    "position queries changed the snapshot result")
            after = len([event for event in trace_events(trace)
                         if event.get("event") == "unit_sync_completed"
                         and event.get("unit_key") == "primary"])
            require(before == after, "position queries triggered resynchronization")
            return {"utf16_character": position["character"],
                    "scalar_character": source_position(text, "answer", 1)["character"],
                    "definition_hover_highlight_tokens": True}
        finally:
            client.shutdown()


def run_restart(native: Path, timeout: float) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="yian-lsp-restart-") as directory:
        parent = Path(directory)
        project = parent / "project"
        main = project / "src" / "main.an"
        write(project / "package.anx", manifest("app", kind="bin"))
        write(main, RESTART_MAIN_TEXT)
        first_trace = parent / "first.jsonl"
        client, diagnostics, events = wait_for_cache(
            native, project, first_trace, timeout, main, RESTART_MAIN_TEXT)
        try:
            require(events[-1].get("cache_state") == "published",
                    f"the first analysis did not publish a cache: {events[-1]!r}")
            cache_path = project / "build" / "lsp" / "analysis-v1.json"
            require(cache_path.is_file(), "the analysis cache file was not created")
            baseline = query_bundle(client, main, RESTART_MAIN_TEXT, ["classify", "answer"])
            baseline_diagnostics = diagnostics["params"].get("diagnostics")
            require(any(item.get("code") == "W501" for item in baseline_diagnostics or []),
                    "the restart fixture did not produce a warning diagnostic")
        finally:
            require(client.shutdown() == 0, "the first server did not shut down cleanly")

        second_trace = parent / "second.jsonl"
        client, diagnostics, events = wait_for_cache(
            native, project, second_trace, timeout, main, RESTART_MAIN_TEXT, version=2)
        try:
            last = events[-1]
            require(last.get("cache_state") == "disk", f"restart did not restore the cache: {last!r}")
            require(last.get("reuse_kind") == "disk", f"restart reuse kind is not disk: {last!r}")
            require(last.get("cache_hit") is True, f"restart did not report a cache hit: {last!r}")
            require(last.get("strict_analysis_count") == 0, f"restart repeated strict analysis: {last!r}")
            require(last.get("source_index_build_count") == 0 and
                    last.get("semantic_index_build_count") == 0 and
                    last.get("query_index_build_count") == 0,
                    f"restart rebuilt analysis indexes: {last!r}")
            require(diagnostics["params"].get("diagnostics") == baseline_diagnostics,
                    "restored diagnostics differ from the analyzed diagnostics")
            require(query_bundle(client, main, RESTART_MAIN_TEXT, ["classify", "answer"]) == baseline,
                    "restored navigation differs from the analyzed navigation")
            return {"cache_restored": True, "strict_analysis_on_restart": 0,
                    "queries_equal": True, "diagnostics_equal": True}
        finally:
            client.shutdown()


def run_cache_reject(native: Path, timeout: float) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="yian-lsp-cache-reject-") as directory:
        parent = Path(directory)
        project = parent / "project"
        main = project / "src" / "main.an"
        write(project / "package.anx", manifest("app", kind="bin"))
        write(main, RESTART_MAIN_TEXT)
        trace = parent / "first.jsonl"
        client, diagnostics, events = wait_for_cache(
            native, project, trace, timeout, main, RESTART_MAIN_TEXT)
        client.shutdown()
        cache_path = project / "build" / "lsp" / "analysis-v1.json"
        require(cache_path.is_file(), "the cache reject fixture did not publish a cache")
        baseline = diagnostics["params"].get("diagnostics")

        write(cache_path, "{not valid json\n")
        trace = parent / "corrupt.jsonl"
        client, diagnostics, events = wait_for_cache(
            native, project, trace, timeout, main, RESTART_MAIN_TEXT, version=2)
        try:
            require(events[-1].get("cache_hit") is False,
                    f"a malformed cache was accepted: {events[-1]!r}")
            require(events[-1].get("strict_analysis_count") == 1,
                    "a malformed cache did not fall back to strict analysis")
            require(diagnostics["params"].get("diagnostics") == baseline,
                    "malformed-cache fallback published different diagnostics")
        finally:
            client.shutdown()

        require(cache_path.is_file(), "the fallback analysis did not republish the cache")
        cache = json.loads(cache_path.read_text())
        cache["tool"]["executable"] = "0" * 64
        cache_path.write_text(json.dumps(cache))
        trace = parent / "identity.jsonl"
        client, diagnostics, events = wait_for_cache(
            native, project, trace, timeout, main, RESTART_MAIN_TEXT, version=3)
        try:
            require(events[-1].get("cache_hit") is False,
                    f"a mismatched tool identity was accepted: {events[-1]!r}")
            require(events[-1].get("strict_analysis_count") == 1,
                    "a mismatched tool identity did not reanalyze")
            require(location_matches(definition(client, main, RESTART_MAIN_TEXT, "answer", 1),
                                     main.as_uri(), RESTART_MAIN_TEXT, "answer", 0),
                    "mismatched-identity fallback returned wrong navigation")
        finally:
            client.shutdown()

        require(cache_path.is_file(), "the identity fallback did not republish the cache")
        cache = json.loads(cache_path.read_text())
        require(cache.get("occurrences"), "the cache has no occurrence to corrupt")
        cache["occurrences"][0]["file"] = 999
        cache_path.write_text(json.dumps(cache))
        trace = parent / "bad-reference.jsonl"
        client, diagnostics, events = wait_for_cache(
            native, project, trace, timeout, main, RESTART_MAIN_TEXT, version=4)
        try:
            require(events[-1].get("cache_hit") is False,
                    f"an out-of-range cache reference was accepted: {events[-1]!r}")
            require(events[-1].get("strict_analysis_count") == 1,
                    "an out-of-range cache reference did not reanalyze")
            require(location_matches(definition(client, main, RESTART_MAIN_TEXT, "answer", 1),
                                     main.as_uri(), RESTART_MAIN_TEXT, "answer", 0),
                    "out-of-range-reference fallback returned wrong navigation")
        finally:
            client.shutdown()

        old_cache_bytes = cache_path.read_bytes()
        changed_text = "// shifted by a disk edit\n" + RESTART_MAIN_TEXT
        write(main, changed_text)
        os.chmod(cache_path.parent, 0o500)
        trace = parent / "write-failure.jsonl"
        client, diagnostics, events = wait_for_cache(
            native, project, trace, timeout, main, changed_text, version=5)
        try:
            require(events[-1].get("cache_state") == "write_failed",
                    f"a failed cache write was not reported: {events[-1]!r}")
            require(events[-1].get("analysis_ready") is True,
                    "a failed cache write damaged the in-memory snapshot")
            require(cache_path.read_bytes() == old_cache_bytes,
                    "a failed cache write truncated or replaced the previous cache file")
            require(any(item.get("code") == "W501"
                        for item in diagnostics["params"].get("diagnostics") or []),
                    "a failed cache write changed published diagnostics")
            require(location_matches(definition(client, main, changed_text, "answer", 1),
                                     main.as_uri(), changed_text, "answer", 0),
                    "a failed cache write damaged navigation")
        finally:
            os.chmod(cache_path.parent, 0o700)
            client.shutdown()
        return {"corrupt_rejected": True, "identity_rejected": True,
                "tool_identity_rejected": True, "bad_reference_rejected": True,
                "write_failure_reported": True, "previous_cache_preserved": True}


def run_publish(native: Path, timeout: float) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="yian-lsp-publish-") as directory:
        parent = Path(directory)
        project = parent / "project"
        main = project / "src" / "main.an"
        disk_text = RESTART_MAIN_TEXT
        unsaved_text = disk_text + "// unsaved analysis overlay\n"
        changed_text = "// changed on disk\n" + disk_text
        write(project / "package.anx", manifest("app", kind="bin"))
        write(main, disk_text)
        trace = parent / "trace.jsonl"
        client, diagnostics, events = wait_for_cache(
            native, project, trace, timeout, main, unsaved_text)
        try:
            cache_path = project / "build" / "lsp" / "analysis-v1.json"
            require(not cache_path.exists(),
                    "an unsaved overlay was published to the disk cache")
            require(events[-1].get("cache_state") == "ineligible",
                    f"unsaved inputs were not marked ineligible: {events[-1]!r}")
            before = len(events)
            write(main, unsaved_text)
            client.notify("textDocument/didSave", {
                "textDocument": {"uri": main.as_uri()},
            })
            saved_events = wait_unit_sync(trace, "primary", before + 1, timeout)
            require(cache_path.is_file(), "saving an unchanged overlay did not publish the cache")
            require(saved_events[-1].get("cache_state") == "published",
                    f"save did not publish the existing result: {saved_events[-1]!r}")
            require(saved_events[-1].get("strict_analysis_count") == 0,
                    "saving an unchanged overlay repeated strict analysis")
            require(location_matches(definition(client, main, unsaved_text, "answer", 1),
                                     main.as_uri(), unsaved_text, "answer", 0),
                    "navigation was lost while publishing a saved snapshot")
        finally:
            client.shutdown()

        write(main, changed_text)
        trace = parent / "mismatch.jsonl"
        client, diagnostics, events = wait_for_cache(
            native, project, trace, timeout, main, changed_text, version=2)
        try:
            require(events[-1].get("cache_hit") is False,
                    f"disk-mismatched cache was accepted: {events[-1]!r}")
            require(events[-1].get("strict_analysis_count") == 1,
                    "disk-mismatched inputs did not reanalyze")
            require(location_matches(definition(client, main, changed_text, "answer", 1),
                                     main.as_uri(), changed_text, "answer", 0),
                    "disk-mismatched fallback returned stale navigation")
            return {"unsaved_not_published": True, "saved_published_without_analysis": True,
                    "disk_mismatch_rejected": True}
        finally:
            client.shutdown()


def run_cache_primitives(native: Path, timeout: float) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="yian-lsp-cache-primitives-") as directory:
        parent = Path(directory)
        project = parent / "project"
        main = project / "src" / "main.an"
        write(project / "package.anx", manifest("app", kind="bin"))
        write(main, RESTART_MAIN_TEXT)
        trace = parent / "trace.jsonl"
        client, diagnostics, events = wait_for_cache(
            native, project, trace, timeout, main, RESTART_MAIN_TEXT)
        try:
            require(events[-1].get("cache_state") == "published",
                    "the primitive fixture did not publish its cache")
            cache_path = project / "build" / "lsp" / "analysis-v1.json"
            record = json.loads(cache_path.read_text())
            require(record.get("version") == 1, "the cache record version is not 1")
            tool = record.get("tool")
            require(isinstance(tool, dict), "the cache record has no tool identity")
            executable = tool.get("executable")
            require(isinstance(executable, str) and len(executable) == 64
                    and all(character in "0123456789abcdef" for character in executable),
                    f"the cache tool executable digest is invalid: {executable!r}")
            target = tool.get("target_triple")
            layout = tool.get("data_layout")
            version = tool.get("llvm_version")
            require(isinstance(target, str) and "x86_64" in target and "linux" in target,
                    f"the cache target triple is not Linux x86-64: {target!r}")
            require(isinstance(layout, str) and layout, "the cache data layout is empty")
            require(isinstance(version, str) and version, "the cache LLVM version is empty")
            require(isinstance(tool.get("raw_pointers"), bool),
                    "the cache pointer mode is missing")
            leftovers = sorted(path.name for path in cache_path.parent.iterdir()
                               if ".tmp-" in path.name)
            require(not leftovers, f"atomic cache publication left temporary files: {leftovers!r}")
            manifest_text = (ROOT / "lsp" / "package.anx").read_text()
            require("ffi = true" not in manifest_text,
                    "the language-server manifest enables FFI")
            server_sources = "\n".join(path.read_text(errors="replace")
                                        for path in (ROOT / "lsp" / "src").rglob("*.an"))
            require("textDocument/rename" not in server_sources
                    and "prepareRename" not in server_sources,
                    "the language server implements rename")
            require(diagnostics["params"].get("diagnostics") == [] or
                    any(item.get("code") == "W501"
                        for item in diagnostics["params"].get("diagnostics", [])),
                    "the primitive fixture published unexpected diagnostics")
            for method in ("textDocument/rename", "textDocument/prepareRename"):
                rejected = client.request_message(method, {
                    "textDocument": {"uri": main.as_uri()},
                    "position": source_position_utf16(RESTART_MAIN_TEXT, "answer", 1),
                    "newName": "renamed",
                })
                require(rejected.get("error", {}).get("code") == -32601,
                        f"{method} was not rejected with method-not-found: {rejected!r}")
            return {"tool_identity": True, "target_identity": True,
                    "atomic_temporaries_cleaned": True,
                    "no_ffi": True, "rename_disabled": True,
                    "target_triple": target, "llvm_version": version,
                    "data_layout": layout}
        finally:
            client.shutdown()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", choices=(
        "fingerprint", "unchanged", "invalidation", "unit-isolation", "primary-routing",
        "coalesce", "watch-roots", "detached-queries", "positions",
        "restart", "cache-reject", "publish", "cache-primitives"
    ), required=True)
    parser.add_argument("--native", "--lsp", dest="native",
                        default=os.environ.get("YIAN_LSP_BIN", "yian-lsp"))
    parser.add_argument("--yianc", default=os.environ.get("YIAN_COMPILER_BIN"))
    parser.add_argument("--timeout", type=float, default=30.0)
    args = parser.parse_args()
    try:
        native = resolve_native(args.native)
        if args.case in ("fingerprint", "unchanged"):
            yianc_name = args.yianc or str(native.with_name("yianc"))
            yianc = resolve_native(yianc_name)
            fingerprint = run_fingerprint(yianc, args.timeout)
        else:
            fingerprint = None
        if args.case == "fingerprint":
            report = {"sha256": fingerprint}
        elif args.case == "unchanged":
            report = {"sha256": fingerprint, **run_unchanged(native, args.timeout)}
        elif args.case == "invalidation":
            report = run_invalidation(native, args.timeout)
        elif args.case == "unit-isolation":
            report = run_unit_isolation(native, args.timeout)
        elif args.case == "primary-routing":
            report = run_primary_routing(native, args.timeout)
        elif args.case == "coalesce":
            report = run_coalesce(native, args.timeout)
        elif args.case == "watch-roots":
            report = run_watch_roots(native, args.timeout)
        elif args.case == "detached-queries":
            report = run_detached_queries(native, args.timeout)
        elif args.case == "positions":
            report = run_positions(native, args.timeout)
        elif args.case == "restart":
            report = run_restart(native, args.timeout)
        elif args.case == "cache-reject":
            report = run_cache_reject(native, args.timeout)
        elif args.case == "publish":
            report = run_publish(native, args.timeout)
        else:
            report = run_cache_primitives(native, args.timeout)
        print(json.dumps({"case": args.case, "result": "PASS", **report}, sort_keys=True))
        return 0
    except (OSError, ProtocolError, ProbeFailure, ValueError) as error:
        print(f"probe {args.case} failed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
