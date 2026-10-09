"""Worker IPC, coalesced analysis, and serial process lifecycle tests."""

from __future__ import annotations

import asyncio
import os
import signal
import tempfile
import unittest
from collections.abc import AsyncGenerator
from contextlib import AsyncExitStack, asynccontextmanager
from pathlib import Path

from lsp.coordinator import AnalysisCoordinator
from lsp.ipc.protocol import FrameDecoder, FrameError, encode_message
from lsp.ipc.types import JsonObject
from lsp.supervisor import WorkerOperationError, WorkerSupervisor


ROOT = Path(__file__).resolve().parents[2]


class ControlledWorker:
    def __init__(self) -> None:
        self.revision = 1
        self.busy = False
        self.syntax_started = asyncio.Event()
        self.syntax_cancelled = asyncio.Event()
        self.analysis_started = asyncio.Event()
        self.release_first_analysis = asyncio.Event()
        self.analysis_revisions: list[int] = []

    async def analyze(self, *, full: bool, reason: str) -> JsonObject:
        del reason
        revision = self.revision
        self.analysis_revisions.append(revision)
        self.analysis_started.set()
        if revision == 1:
            await self.release_first_analysis.wait()
        return {"revision": revision, "full": full}

    async def syntax(self, uri: str) -> JsonObject:
        del uri
        self.syntax_started.set()
        try:
            await asyncio.Future()
        except asyncio.CancelledError:
            self.syntax_cancelled.set()
            raise

    async def format(self, uri: str, text: str | None) -> JsonValue:
        del uri, text
        return None

    async def query(self, method: str, params: JsonObject) -> JsonValue:
        del method, params
        return None

    @asynccontextmanager
    async def client_request_slot(self) -> AsyncGenerator[None, None]:
        yield

    def begin_shutdown(self) -> None:
        pass

    async def close(self) -> None:
        pass


class FrameProtocolTests(unittest.TestCase):
    def test_split_and_coalesced_utf8_frames_preserve_message_order(self) -> None:
        first: JsonObject = {
            "protocol_version": 1,
            "payload": {"text": "emoji 😀"},
            "internal_request_id": 1,
        }
        second: JsonObject = {
            "protocol_version": 1,
            "payload": {"revision": 42},
            "internal_request_id": 2,
        }
        frames = encode_message(first) + encode_message(second)
        decoder = FrameDecoder()
        messages = []
        messages.extend(decoder.feed(frames[:7]))
        messages.extend(decoder.feed(frames[7:29]))
        messages.extend(decoder.feed(frames[29:]))
        self.assertEqual(messages, [first, second])

    def test_oversized_header_is_rejected(self) -> None:
        decoder = FrameDecoder()
        with self.assertRaises(FrameError):
            decoder.feed(b"X" * (8 * 1024 + 1))

    def test_oversized_body_declaration_is_rejected_without_allocating_it(self) -> None:
        decoder = FrameDecoder()
        with self.assertRaises(FrameError):
            decoder.feed(b"Content-Length: 67108865\r\n\r\n")


class WorkerAnalysisTests(unittest.IsolatedAsyncioTestCase):
    async def test_client_request_limit_is_enforced_before_worker_queueing(self) -> None:
        worker = WorkerSupervisor(compiler_root=ROOT, raw_pointers=False)
        stack = AsyncExitStack()
        try:
            for _ in range(128):
                await stack.enter_async_context(worker.client_request_slot())
            with self.assertRaises(WorkerOperationError) as raised:
                async with worker.client_request_slot():
                    self.fail("over-limit request unexpectedly acquired a slot")
            self.assertEqual(raised.exception.code, -32803)
        finally:
            await stack.aclose()
            await worker.close()

    async def test_failed_worker_restarts_once_and_rebuilds_its_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "main.an"
            text = "fn main() { let value: i32 = 1; value; }\n"
            source.write_text(text, encoding="utf-8")
            worker = WorkerSupervisor(compiler_root=ROOT, raw_pointers=False)
            worker.set_workspace_root(root)
            worker.open_document(source.as_uri(), text, 1)
            try:
                await worker.start()
                old_pid = worker.worker_pid
                assert old_pid is not None
                os.kill(old_pid, signal.SIGKILL)
                result = await asyncio.wait_for(
                    worker.analyze(full=True, reason="worker restart test"),
                    timeout=30,
                )
                self.assertEqual(worker.restart_count, 1)
                self.assertNotEqual(worker.worker_pid, old_pid)
                self.assertEqual(result.get("diagnostic_count"), 0)
            finally:
                await worker.close()

    async def test_semantic_request_overtakes_queued_syntax(self) -> None:
        worker = ControlledWorker()
        worker.release_first_analysis.set()
        coordinator = AnalysisCoordinator(
            worker, lambda _result, _reason, _elapsed: None
        )
        try:
            coordinator.schedule_edit("edit", "file:///main.an")
            await asyncio.wait_for(worker.syntax_started.wait(), timeout=2)
            result = await coordinator.request_full()
            self.assertIsNotNone(result)
            self.assertTrue(worker.syntax_cancelled.is_set())
            self.assertEqual(worker.analysis_revisions, [1])
        finally:
            coordinator.close()
            await coordinator.aclose()

    async def test_stale_analysis_result_is_discarded_after_revision_change(self) -> None:
        worker = ControlledWorker()
        published: list[int] = []
        coordinator = AnalysisCoordinator(
            worker,
            lambda result, _reason, _elapsed: published.append(
                int(result["revision"])
            ),
        )
        try:
            old_request = asyncio.create_task(coordinator.request_full())
            await asyncio.wait_for(worker.analysis_started.wait(), timeout=2)
            worker.revision = 2
            worker.release_first_analysis.set()
            self.assertIsNone(await old_request)
            current = await coordinator.request_full()
            self.assertIsNotNone(current)
            self.assertEqual(published, [2])
        finally:
            coordinator.close()
            await coordinator.aclose()

    async def test_cancelled_waiter_keeps_shared_analysis_and_worker_is_reaped(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "main.an"
            source.write_text("fn main() { let value: i32 = 1; value; }\n", encoding="utf-8")
            worker = WorkerSupervisor(compiler_root=ROOT, raw_pointers=False)
            worker.set_workspace_root(root)
            worker.open_document(
                source.as_uri(),
                "fn main() { let value: i32 = 1; value; }\n",
                1,
            )
            completed: list[JsonObject] = []
            coordinator = AnalysisCoordinator(
                worker,
                lambda result, _reason, _elapsed: completed.append(result),
            )
            try:
                await worker.start()
                first = asyncio.create_task(coordinator.request_full())
                second = asyncio.create_task(coordinator.request_full())
                await asyncio.sleep(0)
                first.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await first
                result = await asyncio.wait_for(second, timeout=10)
                self.assertIsNotNone(result)
                assert result is not None
                self.assertIs(result["full"], True)
                self.assertEqual(result["diagnostic_count"], 0)
                self.assertEqual(len(completed), 1)
                self.assertIsNotNone(worker.worker_pid)
            finally:
                coordinator.close()
                await coordinator.aclose()
            self.assertIsNone(worker.worker_pid)

    async def test_edit_schedules_single_document_syntax_before_full_analysis(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "main.an"
            source.write_text("fn main() {}\n", encoding="utf-8")
            uri = source.as_uri()
            worker = WorkerSupervisor(compiler_root=ROOT, raw_pointers=False)
            worker.set_workspace_root(root)
            worker.open_document(uri, "fn main() {}\n", 1)
            syntax_done = asyncio.Event()
            seen: list[JsonObject] = []

            def completed(result: JsonObject, _reason: str, _elapsed: float) -> None:
                if result.get("syntax_document") == uri:
                    seen.append(result)
                    syntax_done.set()

            coordinator = AnalysisCoordinator(worker, completed)
            try:
                await worker.start()
                worker.change_document(uri, "fn main() { let value = ; }\n", 2)
                coordinator.invalidate()
                coordinator.schedule_edit("didChange", uri)
                await asyncio.wait_for(syntax_done.wait(), timeout=10)
                self.assertEqual(len(seen), 1)
                diagnostics_value = seen[0].get("diagnostics")
                self.assertIsInstance(diagnostics_value, dict)
                assert isinstance(diagnostics_value, dict)
                items = diagnostics_value.get(uri)
                self.assertIsInstance(items, list)
                assert isinstance(items, list)
                self.assertTrue(any(
                    isinstance(item, dict) and item.get("code") == "E203"
                    for item in items
                ))
            finally:
                coordinator.close()
                await coordinator.aclose()


if __name__ == "__main__":
    unittest.main()
