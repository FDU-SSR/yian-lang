"""Debounce edits and share same-revision worker analysis requests."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable

from lsp.ipc.types import JsonObject, JsonValue
from lsp.supervisor import WorkerOperationError, WorkerSupervisor

__all__ = ["AnalysisCoordinator"]

_LOGGER = logging.getLogger(__name__)


class AnalysisCoordinator:
    """Schedule syntax and semantic work without blocking the protocol loop."""

    def __init__(
        self,
        worker: WorkerSupervisor,
        on_result: Callable[[JsonObject, str, float], None],
    ) -> None:
        self.__worker = worker
        self.__on_result = on_result
        self.__tasks: dict[tuple[int, bool], asyncio.Task[JsonObject | None]] = {}
        self.__syntax_timer: asyncio.TimerHandle | None = None
        self.__full_timer: asyncio.TimerHandle | None = None
        self.__closed = False
        self.__full_revision = -1
        self.__full_result: JsonObject | None = None

    @property
    def current_full_revision(self) -> int:
        return self.__full_revision

    def schedule_edit(self, reason: str, uri: str | None = None) -> None:
        """Schedule a quick syntax pass and a later full pass."""
        self.__cancel_timers()
        loop = asyncio.get_running_loop()
        if uri is not None:
            self.__syntax_timer = loop.call_later(0.2, self.__start, False, reason, uri)
        self.__full_timer = loop.call_later(0.6, self.__start, True, reason, uri)

    def schedule_full(self, reason: str) -> None:
        self.__cancel_timers()
        self.__start(True, reason, None)

    def invalidate(self) -> None:
        self.__full_revision = -1
        self.__full_result = None

    def close(self) -> None:
        self.__cancel_timers()
        self.__closed = True
        self.__worker.begin_shutdown()

    async def aclose(self) -> None:
        await self.__worker.close()

    async def request_full(self) -> JsonObject | None:
        """Wait for one full result associated with the current input revision."""
        revision = self.__worker.revision
        if revision == self.__full_revision and self.__full_result is not None:
            return self.__full_result
        if self.__closed:
            return None
        self.__cancel_waiting_syntax(revision)
        task = self.__task(revision, True, "semantic request", None)
        try:
            result = await asyncio.shield(task)
        except asyncio.CancelledError:
            raise
        return result if revision == self.__worker.revision else None

    async def query(self, method: str, params: JsonObject) -> JsonValue:
        async with self.__worker.client_request_slot():
            revision = self.__worker.revision
            if await self.request_full() is None:
                raise WorkerOperationError(-32801, "current analysis is unavailable")
            if revision != self.__worker.revision:
                raise WorkerOperationError(-32801, "document changed before query")
            result = await self.__worker.query(method, params)
            if revision != self.__worker.revision:
                raise WorkerOperationError(-32801, "document changed during query")
            return result

    async def format(self, uri: str, text: str | None) -> JsonValue:
        async with self.__worker.client_request_slot():
            revision = self.__worker.revision
            self.__cancel_waiting_syntax(revision)
            result = await self.__worker.format(uri, text)
            if revision != self.__worker.revision:
                raise WorkerOperationError(-32801, "document changed during formatting")
            return result

    async def reload_project(self) -> JsonObject:
        self.invalidate()
        return await self.__worker.load_project()

    def __cancel_waiting_syntax(self, revision: int) -> None:
        """Let interactive work overtake syntax queued behind another operation."""
        task = self.__tasks.get((revision, False))
        if task is not None and not task.done() and not self.__worker.busy:
            task.cancel()

    def __start(self, full: bool, reason: str, uri: str | None) -> None:
        if self.__closed:
            return
        if full:
            self.__full_timer = None
        else:
            self.__syntax_timer = None
            if self.__full_revision == self.__worker.revision or (
                (self.__worker.revision, True) in self.__tasks
            ):
                return
        task = self.__task(self.__worker.revision, full, reason, uri)
        task.add_done_callback(self.__log_failure)

    def __task(
        self, revision: int, full: bool, reason: str, uri: str | None
    ) -> asyncio.Task[JsonObject | None]:
        key = (revision, full)
        existing = self.__tasks.get(key)
        if existing is not None:
            return existing
        task = asyncio.create_task(self.__compute(revision, full, reason, uri))
        self.__tasks[key] = task
        task.add_done_callback(lambda finished: self.__tasks.pop(key, None))
        return task

    async def __compute(
        self, revision: int, full: bool, reason: str, uri: str | None
    ) -> JsonObject | None:
        if self.__closed or revision != self.__worker.revision:
            return None
        started = time.perf_counter()
        try:
            result = (
                await self.__worker.analyze(full=True, reason=reason)
                if full
                else await self.__worker.syntax(uri or "")
            )
        except WorkerOperationError as error:
            if error.code == -32801:
                return None
            _LOGGER.error("worker analysis failed (%s): %s", reason, error)
            return None
        except Exception:
            _LOGGER.exception("worker analysis failed (%s)", reason)
            return None
        if self.__closed or revision != self.__worker.revision:
            return None
        elapsed_ms = (time.perf_counter() - started) * 1000
        if full:
            self.__full_revision = revision
            self.__full_result = result
        self.__on_result(result, reason, elapsed_ms)
        return result

    def __cancel_timers(self) -> None:
        if self.__syntax_timer is not None:
            self.__syntax_timer.cancel()
            self.__syntax_timer = None
        if self.__full_timer is not None:
            self.__full_timer.cancel()
            self.__full_timer = None

    @staticmethod
    def __log_failure(task: asyncio.Task[JsonObject | None]) -> None:
        if not task.cancelled():
            error = task.exception()
            if error is not None:
                _LOGGER.error("background analysis task failed: %s", error, exc_info=error)
