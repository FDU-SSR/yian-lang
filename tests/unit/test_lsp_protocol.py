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
from typing import cast

from lsp.ipc.types import JsonObject, JsonValue


ROOT = Path(__file__).resolve().parents[2]
MessagePredicate = Callable[[JsonObject], bool]


class StdioClient:
    """A stdio client that retains unrelated messages and responds to server requests."""

    def __init__(
        self,
        command: Sequence[str] | None = None,
        *,
        cwd: Path = ROOT,
        environment: Mapping[str, str] | None = None,
    ) -> None:
        self.command = list(command or [
            sys.executable,
            "-m",
            "lsp.main",
            "--stdio",
            "--compiler-root",
            str(ROOT),
        ])
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
    def __initialize(self, client: StdioClient, root: Path) -> None:
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
        client.until(lambda message: message.get("id") == 1)
        client.send({"jsonrpc": "2.0", "method": "initialized", "params": {}})

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

    def test_formatting_uses_unsaved_text_and_returns_protocol_edits(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "format.an"
            source.write_text("fn main() {}\n", encoding="utf-8")
            client = StdioClient()
            uri = source.as_uri()
            text = "fn main(){let x:i32=1;x;}\n"
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
                    "id": 3,
                    "method": "textDocument/formatting",
                    "params": {"textDocument": {"uri": uri}, "options": {"tabSize": 4, "insertSpaces": True}},
                })
                response = client.until(lambda message: message.get("id") == 3)
                edits = response.get("result")
                self.assertIsInstance(edits, list)
                self.assertTrue(edits)
                self.assertIn("fn main()", edits[0]["newText"])
            finally:
                client.shutdown()


if __name__ == "__main__":
    unittest.main()
