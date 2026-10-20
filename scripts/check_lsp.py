#!/usr/bin/env python3
"""Replay and measure the native YIAN language server over stdio JSON-RPC."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import select
import shlex
import shutil
import signal
import statistics
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TypeAlias, cast

from _toolchain.native import NativeToolError, resolve_native

ROOT = Path(__file__).resolve().parents[1]
Json: TypeAlias = None | bool | int | float | str | list["Json"] | dict[str, "Json"]


@dataclass(frozen=True)
class Scenario:
    name: str
    root: Path
    source: Path
    text: str
    project_root: bool


@dataclass(frozen=True)
class QueryCase:
    method: str
    params: Json


@dataclass(frozen=True)
class QuerySpec:
    primary: QueryCase
    distinct: tuple[QueryCase, ...] = ()
    empty_probe: QueryCase | None = None


class ProtocolError(RuntimeError):
    """An LSP process or JSON-RPC protocol failure."""


class StdioSession:
    """One lossless JSON-RPC transcript over the server's stdio transport."""

    def __init__(
        self,
        command: list[str],
        *,
        cwd: Path,
        environment: dict[str, str],
        timeout: float,
    ) -> None:
        self.command = command
        self.cwd = cwd
        self.environment = environment
        self.timeout = timeout
        self.process = subprocess.Popen(
            command,
            cwd=cwd,
            env=environment,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=0,
            start_new_session=True,
        )
        self.buffer = bytearray()
        self.messages: list[dict[str, Json]] = []
        self.sent: list[dict[str, Json]] = []
        self.consumed: set[int] = set()
        self.__timed_request_ids: set[int | str] = set()
        self.__arrival_times_ns: dict[int | str, int] = {}
        self.next_id = 0
        self.stderr = bytearray()
        self.shutdown_ms: float | None = None
        self.stderr_thread = threading.Thread(target=self.__drain_stderr, daemon=True)
        self.stderr_thread.start()

    def notify(self, method: str, params: Json) -> None:
        self.__send({"jsonrpc": "2.0", "method": method, "params": params})

    def request(self, method: str, params: Json) -> Json:
        message = self.request_message(method, params)
        if "error" in message:
            raise ProtocolError(f"{method} failed: {message['error']!r}")
        return message.get("result")

    def request_message(self, method: str, params: Json) -> dict[str, Json]:
        self.next_id += 1
        request_id = self.next_id
        self.__send({
            "jsonrpc": "2.0",
            "id": request_id,
            "method": method,
            "params": params,
        })
        return self.until(lambda value: value.get("id") == request_id)

    def request_batch(
        self,
        method: str,
        params: Json,
        count: int,
    ) -> list[tuple[int, int, dict[str, Json]]]:
        request_times: dict[int, int] = {}
        for _ in range(count):
            self.next_id += 1
            request_id = self.next_id
            self.__timed_request_ids.add(request_id)
            request_times[request_id] = time.monotonic_ns()
            self.__send({
                "jsonrpc": "2.0",
                "id": request_id,
                "method": method,
                "params": params,
            })

        responses: list[tuple[int, int, dict[str, Json]]] = []
        try:
            for request_id, sent_ns in request_times.items():
                response = self.until(lambda value: value.get("id") == request_id)
                arrived_ns = self.__arrival_times_ns.pop(request_id, time.monotonic_ns())
                responses.append((sent_ns, arrived_ns, response))
        finally:
            self.__timed_request_ids.difference_update(request_times)
            for request_id in request_times:
                self.__arrival_times_ns.pop(request_id, None)
        return responses

    def until(self, predicate: object, *, timeout: float | None = None) -> dict[str, Json]:
        if not callable(predicate):
            raise TypeError("predicate must be callable")
        deadline = time.monotonic() + (self.timeout if timeout is None else timeout)
        while time.monotonic() < deadline:
            for index, message in enumerate(self.messages):
                if index in self.consumed:
                    continue
                if self.__answer_server_request(message):
                    self.consumed.add(index)
                    continue
                if predicate(message):
                    self.consumed.add(index)
                    return message
            self.__read(max(0.0, deadline - time.monotonic()))
        raise ProtocolError(
            "timed out waiting for an LSP message\n"
            f"command: {self.command!r}\n"
            f"exit status: {self.process.poll()}\n"
            f"stderr: {bytes(self.stderr).decode('utf-8', errors='replace')[-12000:]}"
        )

    def initialize(self, root: Path | None) -> Json:
        result = self.request("initialize", {
            "processId": os.getpid(),
            "rootUri": None if root is None else root.as_uri(),
            "capabilities": {
                "workspace": {
                    "didChangeWatchedFiles": {
                        "dynamicRegistration": True,
                        "relativePatternSupport": True,
                    },
                    "semanticTokens": {"refreshSupport": True},
                    "inlayHint": {"refreshSupport": True},
                }
            },
        })
        self.notify("initialized", {})
        return result

    def open_document(self, scenario: Scenario, *, version: int = 1, text: str | None = None) -> float:
        started = time.perf_counter()
        self.notify("textDocument/didOpen", {
            "textDocument": {
                "uri": scenario.source.as_uri(),
                "languageId": "yian",
                "version": version,
                "text": scenario.text if text is None else text,
            }
        })
        self.wait_diagnostics(scenario.source.as_uri(), version)
        return (time.perf_counter() - started) * 1000

    def wait_diagnostics(self, uri: str, version: int) -> dict[str, Json]:
        return self.until(lambda message: (
            message.get("method") == "textDocument/publishDiagnostics"
            and isinstance(message.get("params"), dict)
            and message["params"].get("uri") == uri
            and message["params"].get("version") == version
        ))

    def shutdown(self) -> int:
        started = time.perf_counter()
        if self.process.poll() is None:
            try:
                self.request("shutdown", None)
                self.notify("exit", None)
                if self.process.stdin is not None:
                    self.process.stdin.close()
                status = self.process.wait(timeout=self.timeout)
                self.__close_pipes()
                self.shutdown_ms = (time.perf_counter() - started) * 1000
                return status
            except (BrokenPipeError, OSError, ProtocolError, subprocess.TimeoutExpired):
                self.kill()
        status = self.process.wait(timeout=self.timeout)
        self.__close_pipes()
        self.shutdown_ms = (time.perf_counter() - started) * 1000
        return status

    def kill(self) -> None:
        if self.process.poll() is None:
            try:
                os.killpg(self.process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                self.process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(self.process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                self.process.wait(timeout=2)
        for stream in (self.process.stdin, self.process.stdout, self.process.stderr):
            if stream is not None and not stream.closed:
                stream.close()
        self.stderr_thread.join(timeout=1)

    def __send(self, message: dict[str, Json]) -> None:
        self.sent.append(message)
        if self.process.stdin is None:
            raise ProtocolError("server stdin is unavailable")
        body = json.dumps(message, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self.process.stdin.write(f"Content-Length: {len(body)}\r\n\r\n".encode() + body)
        self.process.stdin.flush()

    def __read(self, timeout: float) -> None:
        if self.process.stdout is None:
            return
        while True:
            header_end = self.buffer.find(b"\r\n\r\n")
            if header_end >= 0:
                header = bytes(self.buffer[:header_end])
                length = self.__content_length(header)
                body_start = header_end + 4
                if len(self.buffer) >= body_start + length:
                    body = bytes(self.buffer[body_start:body_start + length])
                    del self.buffer[:body_start + length]
                    value = cast(Json, json.loads(body.decode("utf-8")))
                    if isinstance(value, dict):
                        message = cast(dict[str, Json], value)
                        request_id = message.get("id")
                        if (
                            isinstance(request_id, (int, str))
                            and not isinstance(request_id, bool)
                            and request_id in self.__timed_request_ids
                        ):
                            self.__arrival_times_ns[request_id] = time.monotonic_ns()
                        self.messages.append(message)
                    continue
            ready, _, _ = select.select(
                [self.process.stdout], [], [], min(timeout, 0.01)
            )
            if not ready:
                return
            chunk = os.read(self.process.stdout.fileno(), 65536)
            if not chunk:
                status = self.process.poll()
                detail = bytes(self.stderr).decode("utf-8", errors="replace")[-12000:]
                raise ProtocolError(
                    "server closed stdout before the requested response\n"
                    f"command: {self.command!r}\n"
                    f"exit status: {status}\n"
                    f"stderr: {detail}"
                )
            self.buffer.extend(chunk)

    def __answer_server_request(self, message: dict[str, Json]) -> bool:
        method = message.get("method")
        request_id = message.get("id")
        if not isinstance(method, str) or not isinstance(request_id, (int, str)):
            return False
        if isinstance(request_id, int) and not isinstance(request_id, bool):
            if abs(request_id) > 2**53 - 1:
                raise ProtocolError(
                    "server emitted a JSON-RPC numeric request ID outside the "
                    "exact integer range of JavaScript clients"
                )
        self.__send({"jsonrpc": "2.0", "id": request_id, "result": None})
        return True

    def __drain_stderr(self) -> None:
        if self.process.stderr is None:
            return
        while True:
            data = os.read(self.process.stderr.fileno(), 65536)
            if not data:
                return
            self.stderr.extend(data)
            if len(self.stderr) > 1024 * 1024:
                del self.stderr[:-1024 * 1024]

    def __close_pipes(self) -> None:
        for stream in (self.process.stdin, self.process.stdout, self.process.stderr):
            if stream is not None and not stream.closed:
                stream.close()
        self.stderr_thread.join(timeout=1)

    @staticmethod
    def __content_length(header: bytes) -> int:
        for line in header.split(b"\r\n"):
            key, separator, value = line.partition(b":")
            if separator and key.strip().lower() == b"content-length":
                return int(value.strip())
        raise ProtocolError(f"missing Content-Length in {header!r}")


def __make_scenario(name: str, parent: Path) -> Scenario:
    root = parent / "workspace"
    root.mkdir(parents=True)
    if name == "standalone":
        source = root / "main.an"
        text = __sample_source()
        source.write_text(text, encoding="utf-8", newline="")
        return Scenario(name, root, source, text, True)
    if name == "json-project":
        source = root / "src" / "main.an"
        source.parent.mkdir()
        text = __sample_source()
        source.write_text(text, encoding="utf-8", newline="")
        (root / "package.anx").write_text(
            '[package]\nname = "lsp_probe"\nversion = "0.1.0"\n'
            'kind = "bin"\nentry = "src/main.an"\n',
            encoding="utf-8",
        )
        return Scenario(name, root, source, text, True)
    if name == "compiler-project":
        shutil.copytree(
            ROOT / "yianc",
            root / "yianc",
            ignore=shutil.ignore_patterns("build", "__pycache__", ".pytest_cache"),
        )
        package_parent = root / "bootstrap"
        package_parent.mkdir()
        for package in ("json", "llvm", "ffi"):
            shutil.copytree(
                ROOT / "bootstrap" / package,
                package_parent / package,
                ignore=shutil.ignore_patterns("build", "__pycache__", ".pytest_cache"),
            )
        project_root = root / "yianc"
        source = project_root / "src" / "lsp_probe.an"
        text = __sample_source()
        source.write_text(text, encoding="utf-8", newline="")
        return Scenario(name, project_root, source, text, True)
    raise ValueError(f"unknown scenario: {name}")


def __sample_source() -> str:
    return (
        "fn identity(value: i32) -> i32 {\n"
        "    value\n"
        "}\n\n"
        "fn main() {\n"
        "    let local: i32 = identity(41);\n"
        "    local;\n"
        "}\n"
    )


def __command(template: list[str], root: Path, raw: bool) -> list[str]:
    command: list[str] = []
    for part in template:
        if part == "{raw_flag}":
            if raw:
                command.append("--raw-pointers")
            continue
        command.append(part.replace("{compiler_root}", str(root)))
    if raw and "--raw-pointers" not in command:
        command.append("--raw-pointers")
    return command


def __default_command() -> list[str]:
    executable = os.environ.get("YIAN_LSP_BIN") or "yian-lsp"
    try:
        resolved = resolve_native(executable)
    except NativeToolError as error:
        raise ValueError(
            f"native yian-lsp is unavailable ({error}); run scripts/refresh_toolchain.sh "
            "with a compatible native anx/yianc pair"
        ) from error
    return [str(resolved), "--stdio", "--compiler-root", "{compiler_root}"]


def __observe(client: StdioSession, scenario: Scenario) -> dict[str, Json]:
    uri = scenario.source.as_uri()
    source = scenario.text
    line_identity = source.splitlines()[1]
    identity_use = line_identity.index("value")
    local_declaration = source.splitlines()[5].index("local")
    local_use = source.splitlines()[6].index("local")
    call_line = source.splitlines()[5]
    signature_position = call_line.index("41")
    document = {"uri": uri}

    result: dict[str, Json] = {}
    result["document_symbols"] = client.request(
        "textDocument/documentSymbol", {"textDocument": document}
    )
    result["definition"] = client.request("textDocument/definition", {
        "textDocument": document,
        "position": {"line": 6, "character": local_use},
    })
    result["hover"] = client.request("textDocument/hover", {
        "textDocument": document,
        "position": {"line": 6, "character": local_use},
    })
    result["semantic_tokens"] = client.request(
        "textDocument/semanticTokens/full", {"textDocument": document}
    )
    result["references"] = client.request("textDocument/references", {
        "textDocument": document,
        "position": {"line": 6, "character": local_use},
        "context": {"includeDeclaration": True},
    })
    result["highlights"] = client.request("textDocument/documentHighlight", {
        "textDocument": document,
        "position": {"line": 6, "character": local_use},
    })
    closed_methods = {
        "completion": ("textDocument/completion", {
            "textDocument": document,
            "position": {"line": 6, "character": len(source.splitlines()[6])},
        }),
        "signature_help": ("textDocument/signatureHelp", {
            "textDocument": document,
            "position": {"line": 5, "character": signature_position},
        }),
        "prepare_rename": ("textDocument/prepareRename", {
            "textDocument": document,
            "position": {"line": 6, "character": local_use},
        }),
        "rename": ("textDocument/rename", {
            "textDocument": document,
            "position": {"line": 6, "character": local_use},
            "newName": "renamed_local",
        }),
        "code_action": ("textDocument/codeAction", {
            "textDocument": document,
            "range": {
                "start": {"line": 6, "character": local_use},
                "end": {"line": 6, "character": local_use + len("local")},
            },
            "context": {"diagnostics": []},
        }),
        "formatting": ("textDocument/formatting", {
            "textDocument": document,
            "options": {"tabSize": 4, "insertSpaces": True},
        }),
    }
    result["closed_methods"] = {
        name: client.request_message(method, params).get("error")
        for name, (method, params) in closed_methods.items()
    }

    del identity_use, local_declaration
    unsaved = "fn main(){ let formatted:i32=1; formatted; }\n"
    client.notify("textDocument/didChange", {
        "textDocument": {"uri": uri, "version": 2},
        "contentChanges": [{"text": unsaved}],
    })
    client.wait_diagnostics(uri, 2)
    client.request("textDocument/documentSymbol", {"textDocument": document})
    invalid = "fn main() { let value = ; }\n"
    client.notify("textDocument/didChange", {
        "textDocument": {"uri": uri, "version": 3},
        "contentChanges": [{"text": invalid}],
    })
    invalid_diagnostics = client.wait_diagnostics(uri, 3)
    client.request("textDocument/documentSymbol", {"textDocument": document})
    result["invalid_diagnostics"] = invalid_diagnostics.get("params")
    client.notify("textDocument/didChange", {
        "textDocument": {"uri": uri, "version": 4},
        "contentChanges": [{"text": scenario.text}],
    })
    valid_diagnostics = client.wait_diagnostics(uri, 4)
    client.request("textDocument/documentSymbol", {"textDocument": document})
    result["cleared_diagnostics"] = valid_diagnostics.get("params")
    client.notify("textDocument/didClose", {"textDocument": document})
    try:
        close_clear = client.until(lambda message: (
            message.get("method") == "textDocument/publishDiagnostics"
            and isinstance(message.get("params"), dict)
            and message["params"].get("uri") == uri
            and "version" not in message["params"]
            and message["params"].get("diagnostics") == []
        ), timeout=0.5)
        result["close_notification"] = close_clear.get("params")
    except ProtocolError:
        result["close_notification"] = None
    return result


def __positions_for_word(source: str, line_number: int, word: str) -> tuple[dict[str, Json], ...]:
    """Return valid cursors within one source identifier, including its end."""
    line = source.splitlines()[line_number]
    start = line.index(word)
    return tuple(
        {"line": line_number, "character": start + offset}
        for offset in range(len(word) + 1)
    )


def __query_specs(scenario: Scenario) -> dict[str, QuerySpec]:
    """Build repeat and distinct requests from real declarations and references."""
    uri = scenario.source.as_uri()
    document: Json = {"uri": uri}
    local_positions = __positions_for_word(scenario.text, 6, "local")
    identity_positions = __positions_for_word(scenario.text, 5, "identity")
    argument_positions = __positions_for_word(scenario.text, 5, "41")
    local_use = local_positions[0]
    empty_position: Json = {"line": 3, "character": 0}

    def position_case(method: str, position: Json, extra: dict[str, Json] | None = None) -> QueryCase:
        params: dict[str, Json] = {"textDocument": document, "position": position}
        if extra is not None:
            params.update(extra)
        return QueryCase(method, params)

    def reference_case(method: str, position: Json) -> QueryCase:
        return position_case(method, position, {"context": {"includeDeclaration": True}})

    local_ref_positions = tuple(position_case("textDocument/definition", p) for p in local_positions)
    identity_ref_positions = tuple(position_case("textDocument/definition", p) for p in identity_positions)
    refs_positions = tuple(reference_case("textDocument/references", p) for p in local_positions)
    refs_identity = tuple(reference_case("textDocument/references", p) for p in identity_positions)
    highlight_positions = tuple(position_case("textDocument/documentHighlight", p) for p in local_positions)
    highlight_identity = tuple(position_case("textDocument/documentHighlight", p) for p in identity_positions)
    symbols = QueryCase("textDocument/documentSymbol", {"textDocument": document})
    semantic_tokens = QueryCase("textDocument/semanticTokens/full", {"textDocument": document})

    return {
        "definition": QuerySpec(
            local_ref_positions[0],
            local_ref_positions[1:] + identity_ref_positions,
            position_case("textDocument/definition", empty_position),
        ),
        "hover": QuerySpec(
            position_case("textDocument/hover", local_use),
            tuple(position_case("textDocument/hover", p) for p in local_positions[1:] + identity_positions),
            position_case("textDocument/hover", empty_position),
        ),
        "document_symbols": QuerySpec(symbols),
        "semantic_tokens": QuerySpec(semantic_tokens),
        "references": QuerySpec(
            refs_positions[0], refs_positions[1:] + refs_identity,
            reference_case("textDocument/references", empty_position),
        ),
        "highlights": QuerySpec(
            highlight_positions[0], highlight_positions[1:] + highlight_identity,
            position_case("textDocument/documentHighlight", empty_position),
        ),
    }


def __normalize(value: Json, paths: list[Path]) -> Json:
    if isinstance(value, str):
        for path in paths:
            value = value.replace(str(path), "<workspace>")
        return value
    if isinstance(value, list):
        return [__normalize(item, paths) for item in value]
    if isinstance(value, dict):
        normalized: dict[str, Json] = {}
        for key, item in value.items():
            normalized_key = __normalize(key, paths)
            if not isinstance(normalized_key, str):
                raise TypeError("normalized JSON object key must remain a string")
            normalized[normalized_key] = __normalize(item, paths)
        return normalized
    return value


def __percentile(values: list[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, math.ceil(len(ordered) * percentile) - 1))
    return ordered[index]


def __wait_for_worker_operation(
    trace_path: Path,
    operation: str,
    offset: int,
    timeout: float,
) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if trace_path.exists():
            with trace_path.open("rb") as stream:
                stream.seek(offset)
                for line in stream:
                    try:
                        event = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if (
                        isinstance(event, dict)
                        and event.get("event") == "worker_operation_started"
                        and event.get("operation") == operation
                    ):
                        return True
        time.sleep(0.005)
    return False


def __replace_artifact_paths(value: Json, replacements: dict[str, str]) -> Json:
    if isinstance(value, str):
        return replacements.get(value, value)
    if isinstance(value, list):
        return [__replace_artifact_paths(item, replacements) for item in value]
    if isinstance(value, dict):
        return {
            key: __replace_artifact_paths(item, replacements)
            for key, item in value.items()
        }
    return value


def __source_fingerprint() -> dict[str, Json]:
    files = [
        *sorted((ROOT / "lsp").rglob("*.an")),
        *sorted((ROOT / "yianc" / "src").rglob("*.an")),
        *sorted((ROOT / "anx" / "src").rglob("*.an")),
        *sorted((ROOT / "bootstrap" / "json" / "src").rglob("*.an")),
        *sorted((ROOT / "bootstrap" / "jsonrpc" / "src").rglob("*.an")),
        *sorted((ROOT / "bootstrap" / "lsp_protocol" / "src").rglob("*.an")),
        *sorted((ROOT / "lib" / "src").rglob("*.an")),
        *sorted((ROOT / "runtime" / "src").rglob("*.c")),
        ROOT / "lsp" / "package.anx",
        ROOT / "yianc" / "package.anx",
        ROOT / "anx" / "package.anx",
        ROOT / "bootstrap" / "json" / "package.anx",
        ROOT / "bootstrap" / "jsonrpc" / "package.anx",
        ROOT / "bootstrap" / "lsp_protocol" / "package.anx",
        ROOT / "scripts" / "check_lsp.py",
        ROOT / "scripts" / "probe_lsp_navigation.py",
        *sorted((ROOT / "tests" / "unit").glob("test_lsp_*.py")),
    ]
    digest = hashlib.sha256()
    relative_paths: list[Json] = []
    for path in files:
        relative = path.relative_to(ROOT).as_posix()
        relative_paths.append(relative)
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return {
        "sha256": digest.hexdigest(),
        "files": relative_paths,
    }


def __environment(trace_path: Path | None, profile_path: Path | None) -> dict[str, str]:
    environment = os.environ.copy()
    environment.pop("YIAN_LSP_TRACE_FILE", None)
    environment.pop("YIAN_LSP_PROFILE_FILE", None)
    if trace_path is not None:
        environment["YIAN_LSP_TRACE_FILE"] = str(trace_path)
    if profile_path is not None:
        environment["YIAN_LSP_PROFILE_FILE"] = str(profile_path)
    return environment


def __run_session(
    template: list[str],
    server_root: Path,
    scenario_name: str,
    raw: bool,
    parent: Path,
    timeout: float,
    *,
    profile: bool = False,
    tracing: bool = True,
    functional: bool = True,
) -> dict[str, Json]:
    scenario_dir = parent / "session"
    scenario_dir.mkdir(parents=True)
    scenario = __make_scenario(scenario_name, scenario_dir)
    trace_path = scenario_dir / "trace.jsonl" if tracing else None
    profile_path = scenario_dir / "native-profile.jsonl" if profile else None
    command = __command(template, server_root, raw)
    environment = __environment(
        trace_path,
        profile_path,
    )
    started = time.perf_counter()
    client = StdioSession(
        command,
        cwd=server_root,
        environment=environment,
        timeout=timeout,
    )
    status = -1
    initialize_result: Json = None
    observation: dict[str, Json] = {}
    error: str | None = None
    initialize_ms: float | None = None
    open_ms: float | None = None
    try:
        initialize_result = client.initialize(scenario.root if scenario.project_root else None)
        initialize_ms = (time.perf_counter() - started) * 1000
        open_ms = client.open_document(scenario)
        if functional:
            observation = __observe(client, scenario)
            uri = scenario.source.as_uri()
            close_clear = any(
                message.get("method") == "textDocument/publishDiagnostics"
                and isinstance(message.get("params"), dict)
                and message["params"].get("uri") == uri
                and "version" not in message["params"]
                and message["params"].get("diagnostics") == []
                for message in client.messages
            )
            observation["did_close_clears_diagnostics"] = close_clear
        status = client.shutdown()
    except Exception as exception:
        error = f"{type(exception).__name__}: {exception}"
        client.kill()
        status = client.process.poll() if client.process.poll() is not None else -1
    elapsed_ms = (time.perf_counter() - started) * 1000
    close_params = observation.get("close_notification")
    diagnostic_messages = [
        message for message in client.messages
        if message.get("method") == "textDocument/publishDiagnostics"
        and message.get("params") != close_params
    ]
    record: dict[str, Json] = {
        "scenario": scenario_name,
        "pointer_mode": "raw" if raw else "fat",
        "command": command,
        "cwd": str(server_root),
        "environment": {
            key: environment[key]
            for key in (
                "YIAN_LSP_TRACE_FILE",
                "YIAN_LSP_PROFILE_FILE",
                "YIAN_LSP_DISABLE_QUERY_CACHE",
            )
            if key in environment
        },
        "exit_status": status,
        "elapsed_ms": elapsed_ms,
        "startup_ms": initialize_ms,
        "open_to_diagnostics_ms": open_ms,
        "shutdown_ms": client.shutdown_ms,
        "source_sha256": hashlib.sha256(scenario.text.encode("utf-8")).hexdigest(),
        "initialize_result": initialize_result,
        "observations": observation,
        "comparison_observations": __normalize({
            "initialize": initialize_result,
            "feature_results": {
                key: value for key, value in observation.items()
                if key not in ("did_close_clears_diagnostics", "close_notification")
            },
            "diagnostic_notifications": diagnostic_messages,
            "server_requests": [
                {
                    "method": message["method"],
                    "params": message.get("params"),
                }
                for message in client.messages
                if isinstance(message.get("method"), str)
                and message.get("id") is not None
            ],
        }, [scenario_dir]),
        "sent": client.sent,
        "received": client.messages,
        "stderr": bytes(client.stderr).decode("utf-8", errors="replace"),
        "error": error,
        "trace_path": (
            str(trace_path)
            if trace_path is not None and trace_path.exists()
            else None
        ),
        "profile_path": str(profile_path) if profile_path is not None and profile_path.exists() else None,
        "profile_format": __profile_format(profile_path),
    }
    return record


def __profile_format(path: Path | None) -> str | None:
    if path is None or not path.exists():
        return None
    return "native-operation-trace"


def __read_trace(path: Path) -> list[Json]:
    if not path.exists():
        return []
    return [
        cast(Json, json.loads(line))
        for line in path.read_text(encoding="utf-8").splitlines()
    ]


def __run_control_probe(
    template: list[str],
    server_root: Path,
    scenario_name: str,
    raw: bool,
    parent: Path,
    timeout: float,
) -> dict[str, Json]:
    """Measure protocol responsiveness separately from uninstrumented timings."""
    scenario_dir = parent / "control-probe"
    scenario_dir.mkdir(parents=True)
    scenario = __make_scenario(scenario_name, scenario_dir)
    trace_path = scenario_dir / "trace.jsonl"
    command = __command(template, server_root, raw)
    environment = __environment(trace_path, None)
    client = StdioSession(command, cwd=server_root, environment=environment, timeout=timeout)
    response_ms: list[float] = []
    error_codes: list[int] = []
    large_text = scenario.text + "//" + ("x" * (8 * 1024 * 1024)) + "\n"
    busy_offset = 0
    busy_started_ns: int | None = None
    probe_started_ns = 0
    control_finished_ns = 0
    status = -1
    try:
        client.initialize(scenario.root if scenario.project_root else None)
        client.open_document(scenario)
        busy_offset = trace_path.stat().st_size if trace_path.exists() else 0
        probe_started_ns = time.monotonic_ns()
        client.notify("textDocument/didChange", {
            "textDocument": {"uri": scenario.source.as_uri(), "version": 2},
            "contentChanges": [{"text": large_text}],
        })
        busy_confirmed = __wait_for_worker_operation(
            trace_path, "syntax", busy_offset, timeout=timeout
        )
        events = __read_trace(trace_path)
        for event in events:
            if (
                isinstance(event, dict)
                and event.get("event") == "worker_operation_started"
                and event.get("operation") == "syntax"
            ):
                timestamp = event.get("monotonic_ns")
                if isinstance(timestamp, int) and timestamp >= probe_started_ns:
                    busy_started_ns = timestamp
        responses = client.request_batch("yian/benchmarkControl", {}, 20)
        for sent_ns, arrived_ns, response in responses:
            response_ms.append((arrived_ns - sent_ns) / 1_000_000)
            error_value = response.get("error")
            if isinstance(error_value, dict):
                code = error_value.get("code")
                if isinstance(code, int) and not isinstance(code, bool):
                    error_codes.append(code)
        control_finished_ns = max((arrived for _sent, arrived, _response in responses), default=0)
        status = client.shutdown()
    except Exception:
        client.kill()
        raise

    trace = __read_trace(trace_path)
    syntax_finished_ns: int | None = None
    for event in trace:
        if (
            isinstance(event, dict)
            and event.get("event") == "worker_operation_finished"
            and event.get("operation") == "syntax"
        ):
            timestamp = event.get("monotonic_ns")
            if isinstance(timestamp, int) and timestamp >= probe_started_ns:
                syntax_finished_ns = timestamp
    busy_through_probe = (
        busy_confirmed
        and busy_started_ns is not None
        and (
            syntax_finished_ns is None
            or syntax_finished_ns >= control_finished_ns
        )
    )
    return {
        "method": "yian/benchmarkControl",
        "worker_busy_confirmed": busy_confirmed,
        "worker_busy_through_probe": busy_through_probe,
        "worker_operation_started_ns": busy_started_ns,
        "worker_operation_finished_ns": syntax_finished_ns,
        "samples_ms": response_ms,
        "p95_ms": __percentile(response_ms, 0.95),
        "response_error_codes": error_codes,
        "sample_count": len(response_ms),
        "submitted_text_bytes": len(large_text.encode("utf-8")),
        "shutdown_ms": client.shutdown_ms,
        "exit_status": status,
        "trace_path": str(trace_path),
    }


def __run_cache_probe(
    template: list[str],
    server_root: Path,
    scenario_name: str,
    raw: bool,
    parent: Path,
    timeout: float,
    query_cache_policy: str,
) -> dict[str, Json]:
    """Observe query-cache hit/miss behavior separately from formal timings."""
    scenario_dir = parent / "cache-probe"
    scenario_dir.mkdir(parents=True)
    scenario = __make_scenario(scenario_name, scenario_dir)
    trace_path = scenario_dir / "trace.jsonl"
    command = __command(template, server_root, raw)
    environment = __environment(trace_path, None)
    query_cache_disabled = environment.get("YIAN_LSP_DISABLE_QUERY_CACHE") == "1"
    client = StdioSession(command, cwd=server_root, environment=environment, timeout=timeout)
    specs = __query_specs(scenario)
    try:
        client.initialize(scenario.root if scenario.project_root else None)
        client.open_document(scenario)
        for spec in specs.values():
            for case in (spec.primary, spec.primary, *spec.distinct[:1]):
                response = client.request_message(case.method, case.params)
                if "error" in response:
                    raise ProtocolError(f"cache probe {case.method} failed: {response['error']!r}")
            if spec.empty_probe is not None:
                response = client.request_message(spec.empty_probe.method, spec.empty_probe.params)
                if "error" in response:
                    raise ProtocolError(
                        f"empty-result cache probe {spec.empty_probe.method} failed: {response['error']!r}"
                    )
        status = client.shutdown()
    except Exception:
        client.kill()
        raise

    trace_events = __read_trace(trace_path)
    query_operations = [
        event for event in trace_events
        if isinstance(event, dict)
        and event.get("event") == "worker_operation_finished"
        and event.get("operation") in ("query", "format")
    ]
    cache_metrics_available = any(
        isinstance(event.get("cache_hit"), bool)
        for event in query_operations
    )
    by_method: dict[str, list[dict[str, Json]]] = {}
    for event in query_operations:
        method = event.get("method")
        if isinstance(method, str):
            by_method.setdefault(method, []).append(event)
    method_counts: dict[str, Json] = {}
    for method, events in by_method.items():
        method_counts[method] = {
            "hit_count": (
                sum(1 for event in events if event.get("cache_hit") is True)
                if cache_metrics_available else None
            ),
            "miss_count": (
                sum(1 for event in events if event.get("cache_hit") is False)
                if cache_metrics_available else None
            ),
            "operation_count": len(events),
        }
    analysis_timing_sample = next(
        (
            event.get("analysis_timings")
            for event in trace_events
            if isinstance(event, dict)
            and event.get("event") == "analysis_completed"
            and event.get("full") is True
            and isinstance(event.get("analysis_timings"), dict)
        ),
        None,
    )
    return {
        "exit_status": status,
        "shutdown_ms": client.shutdown_ms,
        "query_cache_disabled": query_cache_disabled,
        "query_cache_policy": query_cache_policy,
        "cache_metrics_available": cache_metrics_available,
        "hit_count": (
            sum(1 for event in query_operations if event.get("cache_hit") is True)
            if cache_metrics_available else None
        ),
        "miss_count": (
            sum(1 for event in query_operations if event.get("cache_hit") is False)
            if cache_metrics_available else None
        ),
        "query_operation_count": len(query_operations),
        "methods": method_counts,
        # This traced sample is separate from formal latency measurements.
        "analysis_timing_sample": analysis_timing_sample,
        "trace_path": str(trace_path),
    }


def __child_processes(pid: int) -> list[int]:
    children_path = Path("/proc") / str(pid) / "task" / str(pid) / "children"
    try:
        values = children_path.read_text(encoding="ascii").split()
    except OSError:
        return []
    return [int(value) for value in values]


def __process_rss_kib(pid: int) -> int | None:
    status_path = Path("/proc") / str(pid) / "status"
    try:
        for line in status_path.read_text(encoding="ascii").splitlines():
            if line.startswith("VmRSS:"):
                fields = line.split()
                if len(fields) >= 2:
                    return int(fields[1])
    except (OSError, ValueError):
        return None
    return None


def __run_memory_probe(
    template: list[str],
    server_root: Path,
    raw: bool,
    parent: Path,
    timeout: float,
    sample_count: int,
) -> dict[str, Json]:
    """Record process RSS during repeated edits and semantic queries."""
    scenario_dir = parent / "memory-probe"
    scenario_dir.mkdir(parents=True)
    scenario = __make_scenario("standalone", scenario_dir)
    command = __command(template, server_root, raw)
    environment = __environment(None, None)
    client = StdioSession(command, cwd=server_root, environment=environment, timeout=timeout)
    samples: list[Json] = []
    maximum_workers = 0

    def capture(label: str, cycle: int) -> None:
        nonlocal maximum_workers
        children = __child_processes(client.process.pid)
        maximum_workers = max(maximum_workers, len(children))
        sample: dict[str, Json] = {
            "label": label,
            "cycle": cycle,
            "main_rss_kib": __process_rss_kib(client.process.pid),
            "worker_count": len(children),
            "worker_rss_kib": (
                __process_rss_kib(children[0])
                if len(children) == 1
                else None
            ),
        }
        samples.append(sample)

    try:
        client.initialize(None)
        client.open_document(scenario)
        capture("initial", 0)
        for cycle in range(1, sample_count + 1):
            changed_text = scenario.text + f"// memory cycle {cycle}\n"
            client.notify("textDocument/didChange", {
                "textDocument": {"uri": scenario.source.as_uri(), "version": cycle + 1},
                "contentChanges": [{"text": changed_text}],
            })
            client.wait_diagnostics(scenario.source.as_uri(), cycle + 1)
            client.request("textDocument/hover", {
                "textDocument": {"uri": scenario.source.as_uri()},
                "position": {"line": 6, "character": 4},
            })
            capture("after_edit_query", cycle)
        status = client.shutdown()
    except Exception:
        client.kill()
        raise

    def window_median(field: str, *, tail: bool) -> float | None:
        values = [
            float(sample[field])
            for sample in samples
            if isinstance(sample, dict)
            and isinstance(sample.get(field), (int, float))
        ]
        if not values:
            return None
        window = values[-10:] if tail else values[:10]
        return statistics.median(window)

    def slope_per_cycle(field: str) -> float | None:
        points = [
            (float(sample["cycle"]), float(sample[field]))
            for sample in samples
            if isinstance(sample, dict)
            and isinstance(sample.get("cycle"), int)
            and isinstance(sample.get(field), (int, float))
        ]
        if len(points) < 2:
            return None
        mean_x = statistics.mean(point[0] for point in points)
        mean_y = statistics.mean(point[1] for point in points)
        variance = sum((x - mean_x) ** 2 for x, _y in points)
        if variance == 0:
            return None
        covariance = sum((x - mean_x) * (y - mean_y) for x, y in points)
        return covariance / variance

    return {
        "scenario": "standalone",
        "pointer_mode": "raw" if raw else "fat",
        "sample_count": sample_count,
        "exit_status": status,
        "shutdown_ms": client.shutdown_ms,
        "maximum_live_workers": maximum_workers,
        "samples": samples,
        "main_rss_first_window_median_kib": window_median("main_rss_kib", tail=False),
        "main_rss_last_window_median_kib": window_median("main_rss_kib", tail=True),
        "worker_rss_first_window_median_kib": window_median("worker_rss_kib", tail=False),
        "worker_rss_last_window_median_kib": window_median("worker_rss_kib", tail=True),
        "main_rss_slope_kib_per_cycle": slope_per_cycle("main_rss_kib"),
        "worker_rss_slope_kib_per_cycle": slope_per_cycle("worker_rss_kib"),
    }


def __timed_query(
    client: StdioSession, case: QueryCase, paths: list[Path]
) -> tuple[float, dict[str, Json], str, bool]:
    started = time.perf_counter()
    response = client.request_message(case.method, case.params)
    elapsed_ms = (time.perf_counter() - started) * 1000
    normalized = cast(dict[str, Json], __normalize(response, paths))
    result = normalized.get("result")
    result_text = json.dumps(result, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    is_error = "error" in normalized
    is_empty = not is_error and ("result" not in normalized or result is None or result == [] or result == {})
    return elapsed_ms, normalized, hashlib.sha256(result_text.encode("utf-8")).hexdigest(), is_empty


def __measure_queries(
    client: StdioSession,
    scenario: Scenario,
    sample_count: int,
) -> tuple[dict[str, Json], list[float], list[float], list[str]]:
    """Measure each LSP method at valid sites and retain result evidence."""
    reports: dict[str, Json] = {}
    repeated_all: list[float] = []
    distinct_all: list[float] = []
    failures: list[str] = []
    specs = __query_specs(scenario)
    paths = [scenario.root, scenario.source.parent]

    for name, spec in specs.items():
        first_ms, first_response, first_digest, first_empty = __timed_query(
            client, spec.primary, paths
        )
        repeated_ms: list[float] = []
        repeated_digests: list[str] = []
        repeated_errors = 0
        for _ in range(sample_count):
            elapsed, response, digest, _empty = __timed_query(client, spec.primary, paths)
            repeated_ms.append(elapsed)
            repeated_all.append(elapsed)
            repeated_digests.append(digest)
            if "error" in response:
                repeated_errors += 1

        distinct_ms: list[float] = []
        distinct_digests: list[str] = []
        distinct_errors = 0
        if spec.distinct:
            for index in range(sample_count):
                case = spec.distinct[index % len(spec.distinct)]
                elapsed, response, digest, _empty = __timed_query(client, case, paths)
                distinct_ms.append(elapsed)
                distinct_all.append(elapsed)
                distinct_digests.append(digest)
                if "error" in response:
                    distinct_errors += 1

        empty_probe: Json = None
        empty_probe_is_empty: bool | None = None
        empty_probe_ms: float | None = None
        if spec.empty_probe is not None:
            empty_probe_ms, empty_response, empty_digest, empty_probe_is_empty = __timed_query(
                client, spec.empty_probe, paths
            )
            empty_probe = {
                "params": __normalize(spec.empty_probe.params, paths),
                "response": empty_response,
                "result_sha256": empty_digest,
                "result_is_empty": empty_probe_is_empty,
                "elapsed_ms": empty_probe_ms,
            }
            if "error" in empty_response:
                failures.append(f"{name}: empty probe returned an error")
            elif not empty_probe_is_empty:
                failures.append(f"{name}: expected the designated empty-result probe to be empty")

        if "error" in first_response:
            failures.append(f"{name}: first request returned an error")
        if repeated_errors:
            failures.append(f"{name}: {repeated_errors} repeated requests returned errors")
        if distinct_errors:
            failures.append(f"{name}: {distinct_errors} distinct-position requests returned errors")

        reports[name] = {
            "method": spec.primary.method,
            "primary_params": __normalize(spec.primary.params, paths),
            "first_request": {
                "elapsed_ms": first_ms,
                "response": first_response,
                "result_sha256": first_digest,
                "result_is_empty": first_empty,
            },
            "repeated": {
                "sample_count": len(repeated_ms),
                "samples_ms": repeated_ms,
                "median_ms": statistics.median(repeated_ms) if repeated_ms else None,
                "p95_ms": __percentile(repeated_ms, 0.95),
                "result_sha256": repeated_digests,
                "error_count": repeated_errors,
            },
            "distinct_positions": {
                "status": "measured" if spec.distinct else "not_applicable",
                "variant_count": len(spec.distinct),
                "sample_count": len(distinct_ms),
                "samples_ms": distinct_ms,
                "median_ms": statistics.median(distinct_ms) if distinct_ms else None,
                "p95_ms": __percentile(distinct_ms, 0.95),
                "result_sha256": distinct_digests,
                "error_count": distinct_errors,
            },
            "empty_result_probe": empty_probe,
            "empty_result_probe_is_empty": empty_probe_is_empty,
            "empty_result_probe_ms": empty_probe_ms,
        }

    return reports, repeated_all, distinct_all, failures


def __run_benchmark(
    template: list[str],
    server_root: Path,
    scenario_name: str,
    raw: bool,
    parent: Path,
    timeout: float,
    cold_samples: int,
    analysis_samples: int,
    query_samples: int,
    *,
    memory_samples: int,
    query_cache_policy: str,
    profile: bool = False,
) -> dict[str, Json]:
    cold: list[float] = []
    cold_diagnostics: list[float] = []
    cold_records: list[dict[str, Json]] = []
    for index in range(cold_samples):
        record = __run_session(
            template,
            server_root,
            scenario_name,
            raw,
            parent / f"cold-{index}",
            timeout,
            tracing=False,
            functional=False,
        )
        if record.get("error") is not None:
            raise ProtocolError(str(record["error"]))
        cold_records.append(record)
        elapsed = record.get("startup_ms")
        diagnostic = record.get("open_to_diagnostics_ms")
        if isinstance(elapsed, (int, float)):
            cold.append(float(elapsed))
        if isinstance(diagnostic, (int, float)):
            cold_diagnostics.append(float(diagnostic))

    scenario_dir = parent / "samples"
    scenario_dir.mkdir(parents=True)
    scenario = __make_scenario(scenario_name, scenario_dir)
    command = __command(template, server_root, raw)
    environment = __environment(None, None)
    client = StdioSession(command, cwd=server_root, environment=environment, timeout=timeout)
    syntax_samples: list[float] = []
    analysis_samples_ms: list[float] = []
    repeated_query_ms: list[float] = []
    distinct_query_ms: list[float] = []
    query_methods: dict[str, Json] = {}
    query_failures: list[str] = []
    uri = scenario.source.as_uri()
    try:
        client.initialize(scenario.root if scenario.project_root else None)
        client.open_document(scenario)
        for index in range(analysis_samples):
            version = index + 2
            changed_text = scenario.text + f"// analysis sample {index}\n"
            started = time.perf_counter()
            client.notify("textDocument/didChange", {
                "textDocument": {"uri": uri, "version": version},
                "contentChanges": [{"text": changed_text}],
            })
            client.wait_diagnostics(uri, version)
            syntax_samples.append((time.perf_counter() - started) * 1000)
            full_started = time.perf_counter()
            client.request("textDocument/documentSymbol", {
                "textDocument": {"uri": uri}
            })
            analysis_samples_ms.append((time.perf_counter() - full_started) * 1000)
        query_methods, repeated_query_ms, distinct_query_ms, query_failures = __measure_queries(
            client, scenario, query_samples
        )
        exit_status = client.shutdown()
    except Exception:
        client.kill()
        raise
    control_probe = None
    cache_probe = None
    profile_run = None
    memory_probe = None
    control_probe = __run_control_probe(
        template,
        server_root,
        scenario_name,
        raw,
        parent,
        timeout,
    )
    cache_probe = __run_cache_probe(
        template,
        server_root,
        scenario_name,
        raw,
        parent,
        timeout,
        query_cache_policy,
    )
    if profile:
        profile_run = __run_session(
            template,
            server_root,
            scenario_name,
            raw,
            parent / "profile-run",
            timeout,
            profile=True,
            tracing=False,
            functional=True,
        )
    if scenario_name == "standalone" and not raw:
        memory_probe = __run_memory_probe(
            template,
            server_root,
            raw,
            parent,
            timeout,
            memory_samples,
        )

    return {
        "pointer_mode": "raw" if raw else "fat",
        "command": command,
        "cwd": str(server_root),
        "exit_status": exit_status,
        "source_sha256": hashlib.sha256(scenario.text.encode("utf-8")).hexdigest(),
        "environment": {
            key: environment[key]
            for key in (
                "YIAN_LSP_TRACE_FILE",
                "YIAN_LSP_PROFILE_FILE",
                "YIAN_LSP_DISABLE_QUERY_CACHE",
            )
            if key in environment
        },
        "cold_sessions": cold_records,
        "formal_measurement_instrumentation": {
            "trace": False,
            "native_profile": False,
        },
        "shutdown_ms": client.shutdown_ms,
        "control_probe": control_probe,
        "cache_probe": cache_probe,
        "query_methods": query_methods,
        "query_failures": query_failures,
        "profile_run": profile_run,
        "memory_probe": memory_probe,
        "sample_counts": {
            "cold": len(cold),
            "analysis": len(analysis_samples_ms),
            "syntax_feedback": len(syntax_samples),
            "queries": len(repeated_query_ms) + len(distinct_query_ms),
            "query_methods": len(query_methods),
        },
        "cold_start_ms": {
            "samples": cold,
            "median": statistics.median(cold) if cold else None,
            "p95": __percentile(cold, 0.95),
        },
        "first_diagnostics_ms": {
            "samples": cold_diagnostics,
            "median": statistics.median(cold_diagnostics) if cold_diagnostics else None,
            "p95": __percentile(cold_diagnostics, 0.95),
        },
        "syntax_feedback_ms": {
            "samples": syntax_samples,
            "median": statistics.median(syntax_samples) if syntax_samples else None,
            "p95": __percentile(syntax_samples, 0.95),
        },
        "full_analysis_request_ms": {
            "samples": analysis_samples_ms,
            "median": statistics.median(analysis_samples_ms) if analysis_samples_ms else None,
            "p95": __percentile(analysis_samples_ms, 0.95),
        },
        "repeated_query_ms": {
            "samples": repeated_query_ms,
            "median": statistics.median(repeated_query_ms) if repeated_query_ms else None,
            "p95": __percentile(repeated_query_ms, 0.95),
        },
        "distinct_query_ms": {
            "samples": distinct_query_ms,
            "median": statistics.median(distinct_query_ms) if distinct_query_ms else None,
            "p95": __percentile(distinct_query_ms, 0.95),
        },
        "query_cache_observations": {
            "policy": query_cache_policy,
            "metrics_available": (
                False if cache_probe is None
                else cache_probe["cache_metrics_available"]
            ),
            "hit_count": None if cache_probe is None else cache_probe["hit_count"],
            "miss_count": None if cache_probe is None else cache_probe["miss_count"],
            "query_operation_count": (
                None if cache_probe is None else cache_probe["query_operation_count"]
            ),
        },
    }


def __parse_command(value: str | None, *, default: list[str]) -> list[str]:
    if value is None:
        return default
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as error:
        raise ValueError(f"command must be a JSON string array: {error}") from error
    if not isinstance(parsed, list) or not all(isinstance(item, str) for item in parsed):
        raise ValueError("command must be a JSON string array")
    if not parsed:
        raise ValueError("command must not be empty")
    return parsed


def __run_infrastructure(args: argparse.Namespace) -> int:
    """Run the library-level checks that underpin the native LSP integration."""
    prefix = args.prefix.expanduser().resolve()
    native_bin = prefix / "bin"
    required = (native_bin / "yianc", native_bin / "anx", native_bin / "yian-lsp")
    native_tools: dict[str, Path] = {}
    missing: list[Path] = []
    tool_errors: list[str] = []
    for path in required:
        try:
            native_tools[path.name] = resolve_native(str(path))
        except NativeToolError as error:
            missing.append(path)
            tool_errors.append(str(error))
    resources = (
        prefix / "share" / "yian" / "lib" / "src",
        prefix / "share" / "yian" / "runtime" / "src",
        prefix / "share" / "yian" / "runtime" / "include",
    )
    missing.extend(path for path in resources if not path.is_dir())
    if missing:
        native_seeds = all(path.name in native_tools for path in required[:2])
        if native_seeds:
            recovery = (
                f"scripts/refresh_toolchain.sh --prefix {shlex.quote(str(prefix))} "
                f"--anx {shlex.quote(str(required[1]))} --yianc {shlex.quote(str(required[0]))}"
            )
        else:
            recovery = (
                f"scripts/bootstrap_toolchain.sh --prefix {shlex.quote(str(prefix))} "
                f"--python <Python 3.12+ path>, then scripts/refresh_toolchain.sh "
                f"--prefix {shlex.quote(str(prefix))} --anx {shlex.quote(str(required[1]))} "
                f"--yianc {shlex.quote(str(required[0]))}"
            )
        print(
            "error: LSP infrastructure requires an existing native yianc/anx/yian-lsp installation "
            f"and shared resources; missing or invalid: {', '.join(str(path) for path in missing)}. "
            f"{' '.join(tool_errors)} "
            f"Prepare it explicitly with {recovery}",
            file=sys.stderr,
        )
        return 1
    environment = os.environ.copy()
    environment["PATH"] = str(native_bin) + os.pathsep + environment.get("PATH", "")
    environment["YIAN_INSTALL_PREFIX"] = str(prefix)
    environment["YIANC"] = str(native_tools["yianc"])
    environment["YIAN_LSP_BIN"] = str(native_tools["yian-lsp"])
    commands = [
        [
            sys.executable,
            "scripts/run_tests.py",
            "--suite",
            "bootstrap",
            "--implementation",
            "native",
            "--prefix",
            str(prefix),
            "-q",
        ],
        [sys.executable, "-m", "unittest", "discover", "-s", "tests/unit", "-v"],
        [str(native_tools["anx"]), "fmt", "--check", str(ROOT / "yianc/src")],
        [str(native_bin / "anx"), "fmt", "--check", str(ROOT / "lsp")],
        [sys.executable, "runtime/build.py", "--check"],
        [sys.executable, "runtime/build.py", "--asan"],
    ]
    results: list[dict[str, Json]] = []
    failures = 0

    for command in commands:
        started = time.perf_counter()
        try:
            completed = subprocess.run(
                command,
                cwd=ROOT,
                env=environment,
                capture_output=True,
                text=True,
                errors="replace",
                timeout=args.infrastructure_timeout,
                check=False,
            )
            elapsed_ms = (time.perf_counter() - started) * 1000
            results.append({
                "command": command,
                "exit_status": completed.returncode,
                "elapsed_ms": elapsed_ms,
                "stdout": completed.stdout,
                "stderr": completed.stderr,
            })
            if completed.returncode != 0:
                failures += 1
        except subprocess.TimeoutExpired as error:
            elapsed_ms = (time.perf_counter() - started) * 1000
            results.append({
                "command": command,
                "exit_status": None,
                "elapsed_ms": elapsed_ms,
                "timeout_seconds": args.infrastructure_timeout,
                "stdout": error.stdout.decode("utf-8", "replace")
                if isinstance(error.stdout, bytes) else error.stdout or "",
                "stderr": error.stderr.decode("utf-8", "replace")
                if isinstance(error.stderr, bytes) else error.stderr or "",
                "error": "timeout",
            })
            failures += 1
        except OSError as error:
            elapsed_ms = (time.perf_counter() - started) * 1000
            results.append({
                "command": command,
                "exit_status": None,
                "elapsed_ms": elapsed_ms,
                "error": f"{type(error).__name__}: {error}",
            })
            failures += 1

    report: dict[str, Json] = {
        "format": 1,
        "mode": "infrastructure",
        "source_root": str(ROOT),
        "source_revision": subprocess.run(
            ["git", "-C", str(ROOT), "rev-parse", "HEAD"],
            check=False,
            capture_output=True,
            text=True,
        ).stdout.strip(),
        "source_worktree": __source_fingerprint(),
        "environment": {
            "harness_runtime": sys.executable,
            "harness_runtime_version": sys.version,
            "platform": sys.platform,
            "cwd": str(Path.cwd()),
            "install_prefix": str(prefix),
        },
        "results": results,
        "ok": failures == 0,
        "failure_count": failures,
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"LSP infrastructure: {'PASS' if failures == 0 else 'FAIL'} ({failures} failures)")
    print(f"report: {args.report}")
    return 0 if failures == 0 else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("check", "benchmark", "profile", "infrastructure"), default="check")
    parser.add_argument("--server-command", help="server command as a JSON string array")
    parser.add_argument("--server-root", type=Path, default=ROOT)
    parser.add_argument("--prefix", type=Path, default=Path(os.environ.get("YIAN_INSTALL_PREFIX", Path.home() / ".local")))
    parser.add_argument(
        "--scenario",
        choices=("standalone", "json-project", "compiler-project"),
        action="append",
        default=None,
    )
    parser.add_argument("--pointer-mode", choices=("fat", "raw", "both"), default="both")
    parser.add_argument("--cold-samples", type=int, default=5)
    parser.add_argument("--analysis-samples", type=int, default=20)
    parser.add_argument("--query-samples", type=int, default=100)
    parser.add_argument("--memory-samples", type=int, default=100)
    parser.add_argument(
        "--query-cache-policy",
        choices=("enabled", "disabled", "absent", "unavailable"),
        default="absent",
        help="declare whether query caching is enabled, disabled, absent, or unmeasured (default: absent)",
    )
    parser.add_argument("--timeout", type=float, default=120.0)
    parser.add_argument("--infrastructure-timeout", type=float, default=1800.0)
    parser.add_argument("--report", type=Path, default=ROOT / "build/lsp/report.json")
    args = parser.parse_args(argv)
    if args.infrastructure_timeout <= 0:
        parser.error("infrastructure timeout must be positive")
    if args.mode == "infrastructure":
        return __run_infrastructure(args)

    try:
        default_command = __default_command() if args.server_command is None else []
        command = __parse_command(args.server_command, default=default_command)
        command[0] = str(resolve_native(command[0]))
    except ValueError as error:
        parser.error(str(error))
    if min(args.cold_samples, args.analysis_samples, args.query_samples, args.memory_samples) < 1:
        parser.error("sample counts must be positive")
    scenarios = args.scenario or ["standalone", "json-project", "compiler-project"]
    server_root = args.server_root.resolve()
    modes = (False, True) if args.pointer_mode == "both" else (args.pointer_mode == "raw",)
    report: dict[str, Json] = {
        "format": 1,
        "mode": args.mode,
        "source_root": str(ROOT),
        "source_revision": subprocess.run(
            ["git", "-C", str(ROOT), "rev-parse", "HEAD"],
            check=False,
            capture_output=True,
            text=True,
        ).stdout.strip(),
        "source_worktree": __source_fingerprint(),
        "environment": {
            "harness_runtime": sys.executable,
            "harness_runtime_version": sys.version,
            "platform": sys.platform,
            "cwd": str(Path.cwd()),
        },
        "server_command": command,
        "server_root": str(server_root),
        "scenarios": scenarios,
        "pointer_modes": ["fat", "raw"] if args.pointer_mode == "both" else [args.pointer_mode],
        "servers": [],
    }
    server_reports: list[Json] = []
    failures = 0
    with tempfile.TemporaryDirectory(prefix="yian-lsp-replay-") as temporary:
        temporary_root = Path(temporary)
        for scenario_name in scenarios:
            for raw in modes:
                run_dir = temporary_root / scenario_name / ("raw" if raw else "fat")
                run_dir.mkdir(parents=True)
                try:
                    if args.mode == "check":
                        record = __run_session(
                            command,
                            server_root,
                            scenario_name,
                            raw,
                            run_dir / "functional",
                            args.timeout,
                        )
                    else:
                        record = __run_benchmark(
                            command,
                            server_root,
                            scenario_name,
                            raw,
                            run_dir,
                            args.timeout,
                            args.cold_samples,
                            args.analysis_samples,
                            args.query_samples,
                            memory_samples=args.memory_samples,
                            query_cache_policy=args.query_cache_policy,
                            profile=args.mode == "profile",
                        )
                    record = cast(dict[str, Json], record)
                    if record.get("error") is not None or record.get("exit_status") != 0:
                        failures += 1
                    query_errors = record.get("query_failures")
                    if isinstance(query_errors, list) and query_errors:
                        failures += 1
                    if args.mode in ("benchmark", "profile"):
                        probe = record.get("control_probe")
                        if not isinstance(probe, dict):
                            failures += 1
                        else:
                            p95 = probe.get("p95_ms")
                            codes = probe.get("response_error_codes")
                            if (
                                probe.get("worker_busy_through_probe") is not True
                                or not isinstance(p95, (int, float))
                                or p95 > 100
                                or not isinstance(codes, list)
                                or len(codes) != 20
                                or any(code != -32601 for code in codes)
                            ):
                                failures += 1
                            if probe.get("exit_status") != 0:
                                failures += 1
                            cache_probe = record.get("cache_probe")
                            if not isinstance(cache_probe, dict):
                                failures += 1
                            else:
                                hits = cache_probe.get("hit_count")
                                misses = cache_probe.get("miss_count")
                                cache_disabled = cache_probe.get("query_cache_disabled") is True
                                cache_policy = cache_probe.get("query_cache_policy")
                                cache_metrics_available = (
                                    cache_probe.get("cache_metrics_available") is True
                                )
                                operation_count = cache_probe.get("query_operation_count")
                                if cache_policy == "unavailable":
                                    if (
                                        cache_metrics_available
                                        or hits is not None
                                        or misses is not None
                                        or not isinstance(operation_count, int)
                                        or operation_count < 1
                                    ):
                                        failures += 1
                                elif cache_policy == "absent":
                                    if (
                                        not cache_metrics_available
                                        or hits != 0
                                        or not isinstance(misses, int)
                                        or misses < 1
                                        or not isinstance(operation_count, int)
                                        or operation_count < 1
                                    ):
                                        failures += 1
                                else:
                                    hits_valid = (
                                        hits == 0 if cache_disabled or cache_policy == "disabled"
                                        else isinstance(hits, int) and hits >= 1
                                        if cache_policy == "enabled"
                                        else False
                                    )
                                    if (
                                        not cache_metrics_available
                                        or not isinstance(hits, int)
                                        or not hits_valid
                                        or not isinstance(misses, int)
                                        or misses < 1
                                    ):
                                        failures += 1
                                if cache_probe.get("exit_status") != 0:
                                    failures += 1
                                method_counts = cache_probe.get("methods")
                                measured_methods = record.get("query_methods")
                                if not isinstance(method_counts, dict) or not isinstance(measured_methods, dict):
                                    failures += 1
                                else:
                                    for method_report in measured_methods.values():
                                        method = method_report.get("method") if isinstance(method_report, dict) else None
                                        method_cache = method_counts.get(method) if isinstance(method, str) else None
                                        method_hits = (
                                            method_cache.get("hit_count")
                                            if isinstance(method_cache, dict)
                                            else None
                                        )
                                        method_operations = (
                                            method_cache.get("operation_count")
                                            if isinstance(method_cache, dict)
                                            else None
                                        )
                                        if cache_policy == "unavailable":
                                            if (
                                                not isinstance(method_cache, dict)
                                                or method_hits is not None
                                                or method_cache.get("miss_count") is not None
                                                or not isinstance(method_operations, int)
                                                or method_operations < 1
                                            ):
                                                failures += 1
                                        elif cache_policy == "absent":
                                            if (
                                                not isinstance(method_cache, dict)
                                                or method_hits != 0
                                                or not isinstance(method_cache.get("miss_count"), int)
                                                or method_cache["miss_count"] < 1
                                                or not isinstance(method_operations, int)
                                                or method_operations < 1
                                            ):
                                                failures += 1
                                        else:
                                            method_hits_valid = (
                                                method_hits == 0
                                                if cache_disabled or cache_policy == "disabled"
                                                else isinstance(method_hits, int) and method_hits >= 1
                                                if cache_policy == "enabled"
                                                else False
                                            )
                                            if (
                                                not isinstance(method_cache, dict)
                                                or not isinstance(method_hits, int)
                                                or not method_hits_valid
                                                or not isinstance(method_cache.get("miss_count"), int)
                                                or method_cache["miss_count"] < 1
                                            ):
                                                failures += 1
                            if args.mode == "profile":
                                profile_run = record.get("profile_run")
                                if (
                                    not isinstance(profile_run, dict)
                                    or profile_run.get("error") is not None
                                    or profile_run.get("profile_path") is None
                                ):
                                    failures += 1
                            if scenario_name == "standalone" and not raw:
                                memory_probe = record.get("memory_probe")
                                worker_count = (
                                    memory_probe.get("maximum_live_workers")
                                    if isinstance(memory_probe, dict)
                                    else None
                                )
                                if (
                                    not isinstance(memory_probe, dict)
                                    or memory_probe.get("exit_status") != 0
                                    or memory_probe.get("sample_count") != args.memory_samples
                                    or not isinstance(worker_count, int)
                                    or worker_count > 1
                                ):
                                    failures += 1
                    if (
                        args.mode == "check"
                        and record.get("observations", {}).get(
                            "did_close_clears_diagnostics"
                        ) is not True
                    ):
                        failures += 1
                    result: dict[str, Json] = {
                        "scenario": scenario_name,
                        "pointer_mode": "raw" if raw else "fat",
                        "server": record,
                    }
                except Exception as error:
                    failures += 1
                    result = {
                        "scenario": scenario_name,
                        "pointer_mode": "raw" if raw else "fat",
                        "error": f"{type(error).__name__}: {error}",
                    }
                server_reports.append(result)
        artifact_dir = args.report.parent / f"{args.report.stem}.artifacts"
        replacements: dict[str, str] = {}
        artifacts: list[Path] = []
        for path in temporary_root.rglob("*"):
            if path.is_file() and path.suffix == ".jsonl":
                artifacts.append(path)
        if artifacts:
            artifact_dir.mkdir(parents=True, exist_ok=True)
            for path in artifacts:
                relative = path.relative_to(temporary_root)
                destination = artifact_dir / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(path, destination)
                replacements[str(path)] = str(destination)
            report["artifact_directory"] = str(artifact_dir)
            report["artifacts"] = [replacements[str(path)] for path in artifacts]
        report["results"] = server_reports
        report["ok"] = failures == 0
        report["failure_count"] = failures
        report = cast(dict[str, Json], __replace_artifact_paths(report, replacements))
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"LSP {args.mode}: {'PASS' if failures == 0 else 'FAIL'} ({failures} failures)")
    print(f"report: {args.report}")
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
