"""Versioned LSP analysis and project-topology regression tests."""

from __future__ import annotations

import asyncio
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from pygls.exceptions import JsonRpcException

from compiler.analysis.documents import Document
from compiler.analysis.session import AnalysisResult
from lsp.coordinator import AnalysisCoordinator
from lsp.server import __query as query_snapshot, create_server
from lsp.workspace import Workspace


ROOT = Path(__file__).resolve().parents[2]


class BlockingSession:
    def __init__(self) -> None:
        self.started = threading.Event()
        self.release = threading.Event()
        self.calls = 0

    def analyze(self, files: tuple[Path, ...], *, documents: object, syntax_only: bool) -> AnalysisResult:
        self.calls += 1
        self.started.set()
        self.release.wait(timeout=5)
        return AnalysisResult(sources={files[0]: "fn main() {}"}, syntax_only=syntax_only)


class CoordinatorTests(unittest.IsolatedAsyncioTestCase):
    async def test_query_rejects_a_snapshot_superseded_while_running(self) -> None:
        server = create_server(compiler_root=ROOT)
        source = ROOT / "tests" / "basic" / "array" / "access.an"
        result = AnalysisResult(sources={source: "fn main() {}"})
        snapshot = server.model.accept(server.model.revision, (source,), result)
        self.assertIsNotNone(snapshot)
        started = threading.Event()
        release = threading.Event()

        def slow_query() -> int:
            started.set()
            release.wait(timeout=5)
            return 42

        try:
            request = asyncio.create_task(query_snapshot(server, result, slow_query))
            self.assertTrue(await asyncio.wait_for(asyncio.to_thread(started.wait, 2), 3))
            server.model.invalidate()
            release.set()
            with self.assertRaises(JsonRpcException):
                await asyncio.wait_for(request, 2)
        finally:
            release.set()
            server.cancel_analysis()

    async def test_stale_result_is_discarded_without_blocking_loop(self) -> None:
        model = Workspace(compiler_root=ROOT)
        source = ROOT / "tests" / "basic" / "array" / "access.an"
        session = BlockingSession()
        published: list[int] = []
        coordinator = AnalysisCoordinator(model, lambda snapshot, _reason, _ms: published.append(snapshot.revision))
        try:
            with patch.object(model, "capture", return_value=((source,), (Document(source, "", 1),), session)):
                request = asyncio.create_task(coordinator.request_full())
                self.assertTrue(await asyncio.wait_for(asyncio.to_thread(session.started.wait, 2), 3))
                await asyncio.wait_for(asyncio.sleep(0), 1)
                model.invalidate()
                session.release.set()
                self.assertIsNone(await asyncio.wait_for(request, 2))
                self.assertEqual(published, [])
                self.assertIsNotNone(await asyncio.wait_for(coordinator.request_full(), 2))
                self.assertEqual(published, [model.revision])
                self.assertEqual(session.calls, 2)
                self.assertIsNotNone(await coordinator.request_full())
                self.assertEqual(session.calls, 2)
        finally:
            session.release.set()
            coordinator.close()

    async def test_cancelled_request_does_not_cancel_shared_analysis(self) -> None:
        model = Workspace(compiler_root=ROOT)
        source = ROOT / "tests" / "basic" / "array" / "access.an"
        session = BlockingSession()
        published: list[int] = []
        coordinator = AnalysisCoordinator(model, lambda snapshot, _reason, _ms: published.append(snapshot.revision))
        try:
            with patch.object(model, "capture", return_value=((source,), (), session)):
                first = asyncio.create_task(coordinator.request_full())
                self.assertTrue(await asyncio.wait_for(asyncio.to_thread(session.started.wait, 2), 3))
                second = asyncio.create_task(coordinator.request_full())
                first.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await first
                session.release.set()
                self.assertIsNotNone(await asyncio.wait_for(second, 2))
                self.assertEqual(published, [model.revision])
                coordinator.schedule_edit("test")
                await asyncio.sleep(0.75)
                self.assertEqual(published, [model.revision])
        finally:
            session.release.set()
            coordinator.close()


class ProjectTopologyTests(unittest.TestCase):
    def test_directory_reload_tracks_source_and_manifest_changes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            src = root / "src"
            src.mkdir()
            main = src / "main.an"
            main.write_text("fn main() {}\n", encoding="utf-8")
            model = Workspace(compiler_root=ROOT)
            self.assertIsNone(model.use_directory(root))
            manifest = root / "package.anx"
            manifest.write_text('[package]\nname = "example"\nversion = "0.1.0"\n', encoding="utf-8")
            self.assertIsNotNone(model.use_directory(root))
            self.assertIn(main.resolve(), {path.resolve() for path in model.files()})

            added = src / "added.an"
            added.write_text("fn helper() {}\n", encoding="utf-8")
            self.assertIsNotNone(model.use_directory(root))
            self.assertIn(added.resolve(), {path.resolve() for path in model.files()})
            added.unlink()
            self.assertIsNotNone(model.use_directory(root))
            self.assertNotIn(added.resolve(), {path.resolve() for path in model.files()})

            manifest.unlink()
            self.assertIsNone(model.use_directory(root))
            self.assertIsNone(model.project)


if __name__ == "__main__":
    unittest.main()
