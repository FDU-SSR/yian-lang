"""Versioned background analysis for a single editor workspace."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import TypeVar

from compiler.analysis.documents import Document, DocumentStore
from compiler.analysis.session import AnalysisResult, AnalysisSession
from lsp.workspace import Snapshot, Workspace


_LOGGER = logging.getLogger(__name__)
_Result = TypeVar("_Result")


def _analyze(
    files: tuple[Path, ...], documents: tuple[Document, ...], session: AnalysisSession, full: bool
) -> AnalysisResult:
    overlay = DocumentStore(documents)
    frozen = DocumentStore(
        Document(path=path, text=overlay.text(path), version=overlay.version(path))
        for path in files
    )
    result = session.analyze(files, documents=frozen, syntax_only=not full)
    if full and result.index is not None:
        # The first navigation request must not materialize the index on the protocol loop.
        _ = result.index.declarations
    return result


class AnalysisCoordinator:
    """Serialize compiler work while protocol events continue on the event loop."""

    def __init__(
        self, model: Workspace, on_result: Callable[[Snapshot, str, float], None]
    ) -> None:
        self.__model = model
        self.__on_result = on_result
        self.__executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="yian-analysis")
        self.__lock = asyncio.Lock()
        self.__tasks: dict[tuple[int, bool], asyncio.Task[Snapshot | None]] = {}
        self.__active: asyncio.Task[Snapshot | None] | None = None
        self.__syntax_timer: asyncio.TimerHandle | None = None
        self.__full_timer: asyncio.TimerHandle | None = None
        self.__closed = False

    def schedule_edit(self, reason: str) -> None:
        """Publish syntax diagnostics first, then full diagnostics after idle time."""
        self.__cancel_timers()
        self.__cancel_obsolete()
        loop = asyncio.get_running_loop()
        self.__syntax_timer = loop.call_later(0.2, self.__start, False, reason)
        self.__full_timer = loop.call_later(0.6, self.__start, True, reason)

    def schedule_full(self, reason: str) -> None:
        self.__cancel_timers()
        self.__cancel_obsolete()
        self.__start(True, reason)

    def close(self) -> None:
        self.__cancel_timers()
        self.__closed = True
        self.__executor.shutdown(wait=False, cancel_futures=True)

    async def request_full(self) -> Snapshot | None:
        """Wait for this revision, never for an analysis of superseding text."""
        revision = self.__model.revision
        snapshot = self.__model.fresh_snapshot
        if snapshot is not None:
            return snapshot
        self.__cancel_obsolete()
        try:
            snapshot = await asyncio.shield(self.__task(revision, True, "request"))
        except asyncio.CancelledError:
            if revision != self.__model.revision:
                return None
            raise
        return snapshot if revision == self.__model.revision else None

    async def run_query(self, query: Callable[[], _Result]) -> _Result:
        """Keep semantic query work off the protocol loop and serialize cache use."""
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(self.__executor, query)

    def __start(self, full: bool, reason: str) -> None:
        self.__syntax_timer = None if not full else self.__syntax_timer
        self.__full_timer = None if full else self.__full_timer
        task = self.__task(self.__model.revision, full, reason)
        # Timer-triggered tasks have no request waiting for their result.
        task.add_done_callback(self.__log_failure)

    def __task(self, revision: int, full: bool, reason: str) -> asyncio.Task[Snapshot | None]:
        key = (revision, full)
        existing = self.__tasks.get(key)
        if existing is not None:
            return existing
        task = asyncio.create_task(self.__compute(revision, full, reason))
        self.__tasks[key] = task
        task.add_done_callback(lambda finished: self.__tasks.pop(key, None))
        return task

    async def __compute(self, revision: int, full: bool, reason: str) -> Snapshot | None:
        async with self.__lock:
            if self.__closed or revision != self.__model.revision:
                return None
            if not full:
                complete = self.__model.fresh_snapshot
                if complete is not None:
                    return complete
            cached = self.__model.fresh_snapshot if full else self.__model.fresh_syntax_snapshot
            if cached is not None:
                return cached
            files, documents, session = self.__model.capture()
            started = time.perf_counter()
            loop = asyncio.get_running_loop()
            active = asyncio.current_task()
            self.__active = active
            try:
                result = await loop.run_in_executor(
                    self.__executor, _analyze, files, documents, session, full
                )
            except Exception:
                _LOGGER.exception("analysis failed (%s)", reason)
                return None
            finally:
                if self.__active is active:
                    self.__active = None
            if self.__closed:
                return None
            snapshot = self.__model.accept(revision, files, result)
            if snapshot is not None:
                self.__on_result(snapshot, reason, (time.perf_counter() - started) * 1000)
            return snapshot

    def __cancel_timers(self) -> None:
        if self.__syntax_timer is not None:
            self.__syntax_timer.cancel()
            self.__syntax_timer = None
        if self.__full_timer is not None:
            self.__full_timer.cancel()
            self.__full_timer = None

    def __cancel_obsolete(self) -> None:
        current = self.__model.revision
        for (revision, _full), task in tuple(self.__tasks.items()):
            if revision != current and task is not self.__active:
                task.cancel()

    @staticmethod
    def __log_failure(task: asyncio.Task[Snapshot | None]) -> None:
        if not task.cancelled():
            error = task.exception()
            if error is not None:
                _LOGGER.error("background analysis failed: %s", error, exc_info=error)
