"""Exercise the LSP process through configurable, lossless stdio framing."""

from __future__ import annotations

import json
import os
import select
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import TypeAlias, cast
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from _toolchain.native import NativeToolError, resolve_native


JsonObject: TypeAlias = dict[str, "JsonValue"]
JsonValue: TypeAlias = None | bool | int | float | str | list["JsonValue"] | JsonObject
MessagePredicate = Callable[[JsonObject], bool]


def native_lsp_path() -> Path:
    program = os.environ.get("YIAN_LSP_BIN") or "yian-lsp"
    try:
        return resolve_native(program)
    except NativeToolError as error:
        raise FileNotFoundError(
            f"native yian-lsp is unavailable: {error}; refresh with scripts/refresh_toolchain.sh"
        ) from error


def native_formatter_path() -> Path:
    configured = os.environ.get("YIAN_FMT")
    if configured:
        try:
            return resolve_native(configured)
        except NativeToolError as error:
            raise FileNotFoundError(
                f"native yian-fmt is unavailable: {error}; refresh with scripts/refresh_toolchain.sh"
            ) from error
    sibling = native_lsp_path().parent / "yian-fmt"
    if sibling.is_file():
        return resolve_native(str(sibling))
    try:
        return resolve_native("yian-fmt")
    except NativeToolError as error:
        raise FileNotFoundError(
            f"native yian-fmt is unavailable: {error}; refresh with scripts/refresh_toolchain.sh"
        ) from error


class StdioClient:
    """A stdio client that retains unrelated messages and responds to server requests."""

    def __init__(
        self,
        command: Sequence[str] | None = None,
        *,
        cwd: Path = ROOT,
        environment: Mapping[str, str] | None = None,
    ) -> None:
        if command is None:
            command = [str(native_lsp_path()), "--stdio", "--compiler-root", str(ROOT)]
        else:
            command = [str(resolve_native(str(command[0]))), *command[1:]]
        self.command = list(command)
        child_environment = os.environ.copy()
        if environment is not None:
            child_environment.update(environment)
        self.process = subprocess.Popen(
            self.command,
            cwd=cwd,
            env=child_environment,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=0,
            start_new_session=True,
        )
        self.buffer = bytearray()
        self.messages: list[JsonObject] = []
        self.__consumed: set[int] = set()
        self.__stderr = bytearray()
        self.__stderr_lock = threading.Lock()
        self.__stderr_thread = threading.Thread(target=self.__drain_stderr, daemon=True)
        self.__stderr_thread.start()

    @property
    def stderr(self) -> str:
        with self.__stderr_lock:
            return bytes(self.__stderr).decode("utf-8", errors="replace")

    def send(self, message: JsonObject) -> None:
        data = json.dumps(message, ensure_ascii=False).encode("utf-8")
        if self.process.stdin is None:
            raise AssertionError("language server stdin is closed")
        frame = f"Content-Length: {len(data)}\r\n\r\n".encode("ascii") + data
        self.process.stdin.write(frame)
        self.process.stdin.flush()

    def until(self, predicate: MessagePredicate, timeout: float = 15) -> JsonObject:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            for index, message in enumerate(self.messages):
                if index in self.__consumed:
                    continue
                if self.__answer_server_request(index, message):
                    continue
                if predicate(message):
                    self.__consumed.add(index)
                    return message
            self.__read_messages(max(0.0, deadline - time.monotonic()))
        raise AssertionError(
            "timed out waiting for an LSP message\n"
            f"command: {self.command!r}\n"
            f"status: {self.process.poll()}\n"
            f"stderr:\n{self.stderr[-12000:]}"
        )

    def shutdown(self, timeout: float = 5) -> int:
        if self.process.poll() is None:
            try:
                self.send({
                    "jsonrpc": "2.0",
                    "id": 900,
                    "method": "shutdown",
                    "params": None,
                })
                self.until(lambda message: message.get("id") == 900, timeout=timeout)
                self.send({"jsonrpc": "2.0", "method": "exit", "params": None})
                if self.process.stdin is not None:
                    self.process.stdin.close()
                status = self.process.wait(timeout=timeout)
                self.__close_pipes()
                return status
            except (AssertionError, BrokenPipeError, OSError, subprocess.TimeoutExpired):
                self.kill_group()
        status = self.process.wait(timeout=timeout)
        self.__close_pipes()
        return status

    def kill_group(self) -> None:
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
        self.__close_pipes()

    def __read_messages(self, timeout: float) -> None:
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
                    value = cast(JsonValue, json.loads(body.decode("utf-8")))
                    if isinstance(value, dict):
                        self.messages.append(cast(JsonObject, value))
                    continue
            ready, _, _ = select.select(
                [self.process.stdout],
                [],
                [],
                min(timeout, 0.25),
            )
            if not ready:
                return
            chunk = os.read(self.process.stdout.fileno(), 65536)
            if not chunk:
                return
            self.buffer.extend(chunk)

    @staticmethod
    def __content_length(header: bytes) -> int:
        for line in header.split(b"\r\n"):
            key, separator, value = line.partition(b":")
            if separator and key.strip().lower() == b"content-length":
                return int(value.strip())
        raise AssertionError(f"missing Content-Length in {header!r}")

    def __answer_server_request(self, index: int, message: JsonObject) -> bool:
        if "method" not in message or "id" not in message:
            return False
        request_id = message.get("id")
        if not isinstance(request_id, (int, str)):
            return False
        self.__consumed.add(index)
        self.send({"jsonrpc": "2.0", "id": request_id, "result": None})
        return True

    def __drain_stderr(self) -> None:
        if self.process.stderr is None:
            return
        while True:
            data = os.read(self.process.stderr.fileno(), 65536)
            if not data:
                return
            with self.__stderr_lock:
                self.__stderr.extend(data)
                if len(self.__stderr) > 1024 * 1024:
                    del self.__stderr[:-1024 * 1024]

    def __close_pipes(self) -> None:
        for stream in (self.process.stdin, self.process.stdout, self.process.stderr):
            if stream is not None and not stream.closed:
                stream.close()
        self.__stderr_thread.join(timeout=1)


class LspProtocolTests(unittest.TestCase):
    def __initialize(self, client: StdioClient, root: Path) -> JsonObject:
        client.send({
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "processId": os.getpid(),
                "rootUri": root.as_uri(),
                "capabilities": {
                    "workspace": {
                        "didChangeWatchedFiles": {"dynamicRegistration": True},
                        "semanticTokens": {"refreshSupport": True},
                    }
                },
            },
        })
        response = client.until(lambda message: message.get("id") == 1)
        client.send({"jsonrpc": "2.0", "method": "initialized", "params": {}})
        result = response.get("result")
        return result if isinstance(result, dict) else {}

    def test_configured_lsp_wrapper_is_rejected_without_execution(self) -> None:
        with tempfile.TemporaryDirectory(prefix="native lsp identity ") as temporary:
            root = Path(temporary)
            marker = root / "executed"
            wrapper = root / "lsp-wrapper"
            wrapper.write_text(
                f"#!/bin/sh\nprintf called > {marker}\n",
                encoding="utf-8",
            )
            wrapper.chmod(0o755)
            with patch.dict(os.environ, {"YIAN_LSP_BIN": str(wrapper)}):
                with self.assertRaisesRegex(FileNotFoundError, "not an ELF"):
                    native_lsp_path()
            self.assertFalse(marker.exists(), "configured LSP wrappers are rejected before execution")

    def test_manifest_diagnostic_clears_after_reload(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = root / "package.anx"
            manifest.write_text('[package]\nname = 1\nversion = "0.1.0"\n', encoding="utf-8")
            src = root / "src"
            src.mkdir()
            (src / "main.an").write_text("fn main() {}\n", encoding="utf-8")
            client = StdioClient()
            try:
                self.__initialize(client, root)
                initial = client.until(lambda message: (
                    message.get("method") == "textDocument/publishDiagnostics"
                    and message.get("params", {}).get("uri") == manifest.as_uri()
                    and bool(message.get("params", {}).get("diagnostics"))
                ))
                self.assertEqual(initial["params"]["diagnostics"][0]["source"], "anx")
                manifest.write_text(
                    '[package]\nname = "example"\nversion = "0.1.0"\n',
                    encoding="utf-8",
                )
                client.send({
                    "jsonrpc": "2.0",
                    "method": "workspace/didChangeWatchedFiles",
                    "params": {"changes": [{"uri": manifest.as_uri(), "type": 2}]},
                })
                cleared = client.until(lambda message: (
                    message.get("method") == "textDocument/publishDiagnostics"
                    and message.get("params", {}).get("uri") == manifest.as_uri()
                    and message.get("params", {}).get("diagnostics") == []
                ))
                self.assertEqual(cleared["params"]["diagnostics"], [])
            finally:
                client.shutdown()

    def test_shutdown_waits_for_worker_reaping(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "main.an"
            source.write_text("fn main() {}\n", encoding="utf-8")
            client = StdioClient()
            try:
                self.__initialize(client, root)
                uri = source.as_uri()
                client.send({
                    "jsonrpc": "2.0",
                    "method": "textDocument/didOpen",
                    "params": {
                        "textDocument": {
                            "uri": uri,
                            "languageId": "yian",
                            "version": 1,
                            "text": "fn main() {}\n",
                        }
                    },
                })
                client.until(lambda message: (
                    message.get("method") == "textDocument/publishDiagnostics"
                    and message.get("params", {}).get("uri") == uri
                    and message.get("params", {}).get("version") == 1
                ))
                child_path = (
                    Path("/proc")
                    / str(client.process.pid)
                    / "task"
                    / str(client.process.pid)
                    / "children"
                )
                child_ids = child_path.read_text(encoding="ascii").split()
                self.assertEqual(len(child_ids), 1)
                worker_pid = int(child_ids[0])

                self.assertEqual(client.shutdown(), 0)

                worker_path = Path("/proc") / str(worker_pid)
                deadline = time.monotonic() + 1
                while worker_path.exists() and time.monotonic() < deadline:
                    time.sleep(0.01)
                self.assertFalse(worker_path.exists(), "analysis worker survived server exit")
            finally:
                client.shutdown()

    def test_definition_and_idle_semantic_diagnostics(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "main.an"
            valid = "fn main() { let value: i32 = 1; value; }\n"
            invalid = "fn main() { let value: i32; value; }\n"
            source.write_text(valid, encoding="utf-8")
            client = StdioClient()
            uri = source.as_uri()
            try:
                self.__initialize(client, root)
                client.send({
                    "jsonrpc": "2.0",
                    "method": "textDocument/didOpen",
                    "params": {
                        "textDocument": {
                            "uri": uri,
                            "languageId": "yian",
                            "version": 1,
                            "text": valid,
                        }
                    },
                })
                client.send({
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "textDocument/definition",
                    "params": {
                        "textDocument": {"uri": uri},
                        "position": {"line": 0, "character": valid.rfind("value;")},
                    },
                })
                definition = client.until(lambda message: message.get("id") == 2)
                self.assertIsNotNone(definition.get("result"))
                client.send({
                    "jsonrpc": "2.0",
                    "method": "textDocument/didChange",
                    "params": {
                        "textDocument": {"uri": uri, "version": 2},
                        "contentChanges": [{"text": invalid}],
                    },
                })
                diagnostic = client.until(lambda message: (
                    message.get("method") == "textDocument/publishDiagnostics"
                    and message.get("params", {}).get("version") == 2
                    and any(
                        item.get("code") == "E502"
                        for item in message.get("params", {}).get("diagnostics", [])
                    )
                ))
                self.assertEqual(diagnostic["params"]["version"], 2)
            finally:
                client.shutdown()

    def test_completion_on_partial_documents_keeps_semantic_kinds_and_receivers(self) -> None:
        for raw in (False, True):
            with self.subTest(raw_pointers=raw):
                self.__check_partial_completion(raw)

    def __check_partial_completion(self, raw: bool) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "main.an"
            invalid = "fn main() { let values = Vec<i32>::new(); }\n"
            source.write_text(invalid, encoding="utf-8")
            command = [str(native_lsp_path()), "--stdio", "--compiler-root", str(ROOT)]
            if raw:
                command.append("--raw-pointers")
            client = StdioClient(command)
            uri = source.as_uri()
            try:
                self.__initialize(client, root)
                client.send({
                    "jsonrpc": "2.0",
                    "method": "textDocument/didOpen",
                    "params": {
                        "textDocument": {
                            "uri": uri,
                            "languageId": "yian",
                            "version": 1,
                            "text": invalid,
                        }
                    },
                })
                diagnostic = client.until(lambda message: (
                    message.get("method") == "textDocument/publishDiagnostics"
                    and message.get("params", {}).get("uri") == uri
                    and message.get("params", {}).get("version") == 1
                ))
                self.assertTrue(diagnostic["params"]["diagnostics"])

                client.send({
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "textDocument/completion",
                    "params": {
                        "textDocument": {"uri": uri},
                        "position": {"line": 0, "character": invalid.index("Vec") + len("Vec")},
                    },
                })
                response = client.until(lambda message: message.get("id") == 2)
                self.assertNotIn("error", response)

                items = response.get("result", {}).get("items", [])
                self.assertTrue(any(
                    item.get("label") == "Vec" and item.get("kind") == 22
                    for item in items
                ))

                declarations = (
                    "fn helper(value: i32, other: i32) -> i32 { value + other }\n"
                    "fn make_vec() -> Vec<i32> { Vec<i32>.new() }\n"
                )
                request_id = 3
                cases = [(expression, "") for expression in ("v.p;", "v.", "make_vec().p;", "(v).p;", "v.p; helper(",
                         "missing; v.p;", "{ let v = missing; v.p; }", "{ let v = ; v.p; }", "{ let v: Vec<i32> = missing; v.p; }", "{ let (v, other) = missing; v.p; }", "[v][0].p;", "v.push(true);", "v.push(",
                         "v.p; helper([1, 2][0], ", "v.p; helper(helper(1, 2), ", "v.p; helper(1 < 2, ")]
                cases.extend(("v.p;", suffix) for suffix in (
                    "struct Broken { field: Missing }\n", "fn broken(value: Missing) {}\n",
                    "fn unrelated() { missing; }\n", "fn forbidden() { @bitcast<i32>(0); }\n", 'fn damaged() { let value = "unterminated',
                    "fn damaged(\npub fn recovered() {}\n",
                    "struct Range {}\nenum Option { Local }\ntrait Add { fn local_add(); }\n",
                ))
                cases.append(("v.p; damaged(", "fn damaged(value: i32, broken) {}\n"))
                cases.append(("v.p; v.damaged(", "impl<T> Vec<T> { pub fn damaged(value: i32, broken) {} }\n"))
                for version, (expression, suffix) in enumerate(cases, start=2):
                    with self.subTest(expression=expression, suffix=suffix):
                        text = declarations + "fn main() { let v = Vec<i32>.new(); " + expression + " }\n" + suffix
                        client.send({
                            "jsonrpc": "2.0", "method": "textDocument/didChange",
                            "params": {"textDocument": {"uri": uri, "version": version},
                                       "contentChanges": [{"text": text}]},
                        })
                        client.until(lambda message: (
                            message.get("method") == "textDocument/publishDiagnostics"
                            and message.get("params", {}).get("uri") == uri
                            and message.get("params", {}).get("version") == version
                        ))
                        row = text.splitlines()[3]
                        column = row.index(expression) + expression.index(".") + 1
                        if expression.split(".", 1)[1].startswith("p"):
                            column += 1
                        client.send({
                            "jsonrpc": "2.0", "id": request_id, "method": "textDocument/completion",
                            "params": {"textDocument": {"uri": uri},
                                       "position": {"line": 3, "character": column}},
                        })
                        response = client.until(lambda message: message.get("id") == request_id)
                        request_id += 1
                        self.assertNotIn("error", response)
                        candidates = response.get("result", {}).get("items", [])
                        self.assertNotIn("print", {item["label"] for item in candidates})
                        self.assertNotIn("p", {item["label"] for item in candidates})
                        if "damaged(" in expression:
                            client.send({
                                "jsonrpc": "2.0", "id": request_id, "method": "textDocument/signatureHelp",
                                "params": {"textDocument": {"uri": uri},
                                           "position": {"line": 3, "character": row.index("damaged(") + 8}},
                            })
                            signature = client.until(lambda message: message.get("id") == request_id)
                            request_id += 1
                            self.assertNotIn("error", signature)
                            self.assertIsNone(signature.get("result"))
                            client.send({
                                "jsonrpc": "2.0", "id": request_id, "method": "textDocument/completion",
                                "params": {"textDocument": {"uri": uri},
                                           "position": {"line": 3, "character": row.index("damaged(") + 7}},
                            })
                            completion = client.until(lambda message: message.get("id") == request_id)
                            request_id += 1
                            self.assertNotIn("error", completion)
                            if "v.damaged(" not in expression:
                                damaged = next(item for item in completion["result"]["items"] if item["label"] == "damaged")
                                self.assertEqual(damaged.get("kind"), 3)
                                self.assertNotIn("detail", damaged)
                                self.assertEqual(damaged.get("insertTextFormat"), 1)
                                self.assertEqual(damaged.get("insertText"), "damaged")
                        if "let v = missing" in expression or "let v = ;" in expression or "let (v, other)" in expression:
                            self.assertEqual(candidates, [])
                        else:
                            self.assertTrue(any(item["label"] == "push" and item.get("kind") == 2
                                                for item in candidates))
                        for method in ("textDocument/documentSymbol", "textDocument/semanticTokens/full"):
                            client.send({"jsonrpc": "2.0", "id": request_id, "method": method,
                                         "params": {"textDocument": {"uri": uri}}})
                            readonly = client.until(lambda message: message.get("id") == request_id)
                            request_id += 1
                            self.assertNotIn("error", readonly)
                            self.assertTrue(readonly.get("result"))
                            if method == "textDocument/documentSymbol" and "fn recovered" in suffix:
                                self.assertIn("recovered", {item["name"] for item in readonly["result"]})
                        if "push(" in expression:
                            client.send({
                                "jsonrpc": "2.0", "id": request_id, "method": "textDocument/signatureHelp",
                                "params": {"textDocument": {"uri": uri},
                                           "position": {"line": 3, "character": row.index("push(") + 5}},
                            })
                            signature = client.until(lambda message: message.get("id") == request_id)
                            request_id += 1
                            self.assertNotIn("error", signature)
                            payload = signature.get("result")
                            self.assertIsInstance(payload, dict)
                            self.assertTrue(payload.get("signatures") if isinstance(payload, dict) else False)
                            if isinstance(payload, dict) and payload.get("signatures"):
                                self.assertIn("value: i32", payload["signatures"][0]["label"])
                        if expression.endswith(", "):
                            client.send({"jsonrpc": "2.0", "id": request_id, "method": "textDocument/signatureHelp",
                                         "params": {"textDocument": {"uri": uri},
                                                    "position": {"line": 3, "character": row.rindex(",") + 1}}})
                            signature = client.until(lambda message: message.get("id") == request_id)
                            request_id += 1
                            payload = signature.get("result")
                            self.assertIsInstance(payload, dict)
                            if isinstance(payload, dict):
                                self.assertEqual(payload.get("activeParameter"), 1)
                text = "trait EditorTrait { fn push(value: i32); }\nfn query(v: EditorTrait&) { v.p; }\n"
                client.send({
                    "jsonrpc": "2.0", "method": "textDocument/didChange",
                    "params": {"textDocument": {"uri": uri, "version": version + 1},
                               "contentChanges": [{"text": text}]},
                })
                client.send({
                    "jsonrpc": "2.0", "id": request_id, "method": "textDocument/completion",
                    "params": {"textDocument": {"uri": uri},
                               "position": {"line": 1, "character": text.splitlines()[1].index("v.p") + 3}},
                })
                response = client.until(lambda message: message.get("id") == request_id)
                self.assertNotIn("error", response)
                self.assertTrue(any(item["label"] == "push" and item.get("kind") == 2
                                    for item in response.get("result", {}).get("items", [])))
            finally:
                client.shutdown()

    def test_formatting_uses_unsaved_text_and_returns_protocol_edits(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "format.an"
            source.write_text("fn main() {}\n", encoding="utf-8")
            client = StdioClient(environment={"YIAN_FMT": str(native_formatter_path())})
            uri = source.as_uri()
            text = "fn main(){\nlet x:i32=1;\nx;\n}\n"
            try:
                initialized = self.__initialize(client, root)
                capabilities = initialized.get("capabilities")
                self.assertIsInstance(capabilities, dict)
                self.assertIs(capabilities.get("documentFormattingProvider"), True,
                              "the server must declare document formatting")
                client.send({
                    "jsonrpc": "2.0",
                    "method": "textDocument/didOpen",
                    "params": {
                        "textDocument": {
                            "uri": uri,
                            "languageId": "yian",
                            "version": 1,
                            "text": text,
                        }
                    },
                })
                client.send({
                    "jsonrpc": "2.0",
                    "id": 3,
                    "method": "textDocument/formatting",
                    "params": {"textDocument": {"uri": uri}, "options": {"tabSize": 4, "insertSpaces": True}},
                })
                response = client.until(lambda message: message.get("id") == 3)
                edits = response.get("result")
                self.assertIsInstance(edits, list)
                self.assertEqual(
                    edits,
                    [{
                        "range": {
                            "start": {"line": 0, "character": 0},
                            "end": {"line": 4, "character": 0},
                        },
                        "newText": "fn main() {\n    let x: i32 = 1;\n    x;\n}\n",
                    }],
                    "formatting must use the unsaved document and return the exact native output",
                )

                formatted = "fn main() {\n    let x: i32 = 1;\n    x;\n}\n"
                client.send({
                    "jsonrpc": "2.0",
                    "method": "textDocument/didChange",
                    "params": {
                        "textDocument": {"uri": uri, "version": 2},
                        "contentChanges": [{"text": formatted}],
                    },
                })
                client.send({
                    "jsonrpc": "2.0",
                    "id": 4,
                    "method": "textDocument/formatting",
                    "params": {"textDocument": {"uri": uri}, "options": {"tabSize": 4, "insertSpaces": True}},
                })
                unchanged = client.until(lambda message: message.get("id") == 4)
                self.assertEqual(unchanged.get("result"), [],
                                 "an already formatted buffer must produce no edit")

                client.send({
                    "jsonrpc": "2.0",
                    "method": "textDocument/didChange",
                    "params": {
                        "textDocument": {"uri": uri, "version": 3},
                        "contentChanges": [{"text": "fn main() { let value = ; }\n"}],
                    },
                })
                client.send({
                    "jsonrpc": "2.0",
                    "id": 5,
                    "method": "textDocument/formatting",
                    "params": {"textDocument": {"uri": uri}, "options": {"tabSize": 4, "insertSpaces": True}},
                })
                unparseable = client.until(lambda message: message.get("id") == 5)
                self.assertEqual(unparseable.get("result"), [],
                                 "text that cannot be parsed must produce no edit")

                client.send({
                    "jsonrpc": "2.0",
                    "id": 6,
                    "method": "textDocument/formatting",
                    "params": {"textDocument": {}},
                })
                malformed = client.until(lambda message: message.get("id") == 6)
                self.assertEqual(malformed.get("error", {}).get("code"), -32602)

                unknown_uri = (root / "missing.an").as_uri()
                client.send({
                    "jsonrpc": "2.0",
                    "id": 7,
                    "method": "textDocument/formatting",
                    "params": {"textDocument": {"uri": unknown_uri},
                               "options": {"tabSize": 4, "insertSpaces": True}},
                })
                unknown = client.until(lambda message: message.get("id") == 7)
                self.assertEqual(unknown.get("result"), [],
                                 "an unopened document must produce no edit")

                typed_error = "fn main(){ let value: Missing = 1; }\n"
                client.send({
                    "jsonrpc": "2.0",
                    "method": "textDocument/didChange",
                    "params": {
                        "textDocument": {"uri": uri, "version": 4},
                        "contentChanges": [{"text": typed_error}],
                    },
                })
                client.send({
                    "jsonrpc": "2.0",
                    "id": 8,
                    "method": "textDocument/formatting",
                    "params": {"textDocument": {"uri": uri}, "options": {"tabSize": 4, "insertSpaces": True}},
                })
                type_failure = client.until(lambda message: message.get("id") == 8)
                self.assertEqual(
                    type_failure.get("result"),
                    [{
                        "range": {
                            "start": {"line": 0, "character": 0},
                            "end": {"line": 1, "character": 0},
                        },
                        "newText": "fn main() { let value: Missing = 1; }\n",
                    }],
                    "formatting must not depend on a successful analysis",
                )
            finally:
                client.shutdown()

    def test_cancelled_semantic_request_returns_cancelled_error(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "main.an"
            text = "fn main() { let value: i32 = 1; value; }\n"
            source.write_text(text, encoding="utf-8")
            uri = source.as_uri()
            client = StdioClient()
            try:
                self.__initialize(client, root)
                client.send({
                    "jsonrpc": "2.0",
                    "method": "textDocument/didOpen",
                    "params": {
                        "textDocument": {
                            "uri": uri,
                            "languageId": "yian",
                            "version": 1,
                            "text": text,
                        }
                    },
                })
                client.send({
                    "jsonrpc": "2.0",
                    "id": 7,
                    "method": "textDocument/definition",
                    "params": {
                        "textDocument": {"uri": uri},
                        "position": {"line": 0, "character": text.index("value;")},
                    },
                })
                client.send({
                    "jsonrpc": "2.0",
                    "method": "$/cancelRequest",
                    "params": {"id": 7},
                })

                response = client.until(lambda message: message.get("id") == 7)
                self.assertEqual(response.get("error", {}).get("code"), -32800)
            finally:
                client.shutdown()


if __name__ == "__main__":
    unittest.main()
