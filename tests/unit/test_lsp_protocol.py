"""Exercise asynchronous analysis through the language server's stdio protocol."""

from __future__ import annotations

import json
import os
import select
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


class StdioClient:
    def __init__(self, process: subprocess.Popen[bytes]) -> None:
        self.process = process
        self.buffer = bytearray()

    def send(self, message: dict[str, object]) -> None:
        data = json.dumps(message).encode("utf-8")
        assert self.process.stdin is not None
        self.process.stdin.write(f"Content-Length: {len(data)}\r\n\r\n".encode() + data)
        self.process.stdin.flush()

    def until(self, predicate: object, timeout: float = 15) -> dict[str, object]:
        assert callable(predicate)
        assert self.process.stdout is not None
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            header_end = self.buffer.find(b"\r\n\r\n")
            if header_end >= 0:
                header = bytes(self.buffer[:header_end])
                length = next(
                    int(line.split(b":", 1)[1].strip())
                    for line in header.split(b"\r\n")
                    if line.lower().startswith(b"content-length:")
                )
                body_start = header_end + 4
                if len(self.buffer) >= body_start + length:
                    message = json.loads(bytes(self.buffer[body_start:body_start + length]))
                    del self.buffer[:body_start + length]
                    if predicate(message):
                        return message
                    continue
            ready, _, _ = select.select([self.process.stdout], [], [], max(0, deadline - time.monotonic()))
            if ready:
                chunk = os.read(self.process.stdout.fileno(), 65536)
                if not chunk:
                    break
                self.buffer.extend(chunk)
        raise AssertionError("timed out waiting for an LSP message")


class LspProtocolTests(unittest.TestCase):
    def test_manifest_diagnostic_clears_after_reload(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = root / "package.anx"
            manifest.write_text('[package]\nname = 1\nversion = "0.1.0"\n', encoding="utf-8")
            src = root / "src"
            src.mkdir()
            (src / "main.an").write_text("fn main() {}\n", encoding="utf-8")
            process = subprocess.Popen(
                [sys.executable, "-m", "lsp.main", "--stdio", "--compiler-root", str(ROOT)],
                cwd=ROOT, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                bufsize=0,
            )
            client = StdioClient(process)
            try:
                client.send({
                    "jsonrpc": "2.0", "id": 1, "method": "initialize",
                    "params": {"processId": os.getpid(), "rootUri": root.as_uri(), "capabilities": {}},
                })
                client.until(lambda message: message.get("id") == 1)
                client.send({"jsonrpc": "2.0", "method": "initialized", "params": {}})
                client.until(lambda message: (
                    message.get("method") == "textDocument/publishDiagnostics"
                    and message.get("params", {}).get("uri") == manifest.as_uri()
                    and bool(message.get("params", {}).get("diagnostics"))
                ))
                manifest.write_text('[package]\nname = "example"\nversion = "0.1.0"\n', encoding="utf-8")
                client.send({
                    "jsonrpc": "2.0", "method": "workspace/didChangeWatchedFiles",
                    "params": {"changes": [{"uri": manifest.as_uri(), "type": 2}]},
                })
                client.until(lambda message: (
                    message.get("method") == "textDocument/publishDiagnostics"
                    and message.get("params", {}).get("uri") == manifest.as_uri()
                    and message.get("params", {}).get("diagnostics") == []
                ))
            finally:
                process.terminate()
                process.communicate(timeout=5)

    def test_definition_and_idle_semantic_diagnostics(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "main.an"
            valid = "fn main() { let value: i32 = 1; value; }\n"
            invalid = "fn main() { let value: i32; value; }\n"
            source.write_text(valid, encoding="utf-8")
            process = subprocess.Popen(
                [sys.executable, "-m", "lsp.main", "--stdio", "--compiler-root", str(ROOT)],
                cwd=ROOT, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                bufsize=0,
            )
            client = StdioClient(process)
            uri = source.as_uri()
            try:
                client.send({
                    "jsonrpc": "2.0", "id": 1, "method": "initialize",
                    "params": {"processId": os.getpid(), "rootUri": Path(directory).as_uri(), "capabilities": {}},
                })
                client.until(lambda message: message.get("id") == 1)
                client.send({"jsonrpc": "2.0", "method": "initialized", "params": {}})
                client.send({
                    "jsonrpc": "2.0", "method": "textDocument/didOpen",
                    "params": {"textDocument": {"uri": uri, "languageId": "yian", "version": 1, "text": valid}},
                })
                client.send({
                    "jsonrpc": "2.0", "id": 2, "method": "textDocument/definition",
                    "params": {"textDocument": {"uri": uri}, "position": {"line": 0, "character": valid.rfind("value;")}},
                })
                definition = client.until(lambda message: message.get("id") == 2)
                self.assertIsNotNone(definition.get("result"))
                client.send({
                    "jsonrpc": "2.0", "method": "textDocument/didChange",
                    "params": {"textDocument": {"uri": uri, "version": 2}, "contentChanges": [{"text": invalid}]},
                })
                diagnostic = client.until(lambda message: (
                    message.get("method") == "textDocument/publishDiagnostics"
                    and any(item.get("code") == "E502" for item in message.get("params", {}).get("diagnostics", []))
                ))
                self.assertEqual(diagnostic["params"]["version"], 2)
            finally:
                process.terminate()
                process.communicate(timeout=5)


if __name__ == "__main__":
    unittest.main()
