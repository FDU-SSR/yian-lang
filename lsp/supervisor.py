"""Protocol-process state and lifecycle control for the analysis worker."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import AsyncGenerator, Callable
from contextlib import asynccontextmanager
from pathlib import Path

from lsp.ipc.types import JsonObject, JsonValue, require_object
from lsp.process import WorkerFailure, WorkerProcess

__all__ = ["WorkerOperationError", "WorkerSupervisor"]

_LOGGER = logging.getLogger(__name__)
_MAX_PENDING_REQUESTS = 128


class WorkerOperationError(RuntimeError):
    """An operation-level JSON-RPC failure returned by the worker."""

    def __init__(self, code: int, message: str) -> None:
        super().__init__(message)
        self.code = code


class WorkerSupervisor:
    """Own editor text and revision counters; keep compiler objects in the child."""

    def __init__(
        self,
        *,
        compiler_root: Path | None,
        raw_pointers: bool,
        event_sink: Callable[[str, JsonObject], None] | None = None,
    ) -> None:
        self.__compiler_root = compiler_root
        self.__raw_pointers = raw_pointers
        self.__workspace_root: Path | None = None
        self.__documents: dict[str, tuple[str, int | None]] = {}
        self.__revision = 0
        self.__topology_revision = 0
        self.__epoch = 0
        self.__worker: WorkerProcess | None = None
        self.__request_lock = asyncio.Lock()
        self.__pending_slots = asyncio.Semaphore(_MAX_PENDING_REQUESTS)
        self.__retirement: asyncio.Task[None] | None = None
        self.__closed = False
        self.__pending_client_requests = 0
        self.__synced_revision = -1
        self.__synced_topology_revision = -1
        self.__last_project_result: JsonObject = {}
        self.restart_count = 0
        self.__retry_revision = -1
        self.__retry_used = False
        self.__event_sink = event_sink
        self.__waiting_requests = 0

    @property
    def revision(self) -> int:
        return self.__revision

    @property
    def topology_revision(self) -> int:
        return self.__topology_revision

    @property
    def workspace_root(self) -> Path | None:
        return self.__workspace_root

    @property
    def project_loaded(self) -> bool:
        return self.__last_project_result.get("project_loaded") is True

    @property
    def project_root(self) -> Path | None:
        value = self.__last_project_result.get("project_root")
        return Path(value) if isinstance(value, str) else None

    @property
    def open_documents(self) -> tuple[tuple[str, str, int | None], ...]:
        return tuple(
            (uri, text, version) for uri, (text, version) in self.__documents.items()
        )

    @property
    def full_revision(self) -> int:
        value = self.__last_project_result.get("full_revision")
        return value if isinstance(value, int) and not isinstance(value, bool) else -1

    @property
    def worker_pid(self) -> int | None:
        worker = self.__worker
        return None if worker is None else worker.process_id

    @property
    def busy(self) -> bool:
        worker = self.__worker
        return worker is not None and worker.busy

    @asynccontextmanager
    async def client_request_slot(self) -> AsyncGenerator[None, None]:
        """Bound client work before it can accumulate behind a shared analysis."""
        if self.__pending_client_requests >= _MAX_PENDING_REQUESTS:
            self.__emit("client_request_rejected", {
                "revision": self.__revision,
                "pending_requests": self.__pending_client_requests,
            })
            raise WorkerOperationError(-32803, "too many pending language-server requests")
        self.__pending_client_requests += 1
        self.__emit("client_request_queued", {
            "revision": self.__revision,
            "pending_requests": self.__pending_client_requests,
        })
        try:
            yield
        finally:
            self.__pending_client_requests -= 1
            self.__emit("client_request_completed", {
                "revision": self.__revision,
                "pending_requests": self.__pending_client_requests,
            })

    def set_event_sink(self, sink: Callable[[str, JsonObject], None] | None) -> None:
        self.__event_sink = sink

    def trace_event(self, event: str, fields: JsonObject) -> None:
        self.__emit(event, fields)

    def set_workspace_root(self, root: Path | None) -> None:
        self.__workspace_root = None if root is None else root.resolve()
        self.invalidate(topology=True)

    def open_document(self, uri: str, text: str, version: int | None) -> None:
        self.__documents[uri] = (text, version)
        self.invalidate()

    def change_document(self, uri: str, text: str, version: int | None) -> None:
        self.open_document(uri, text, version)

    def close_document(self, uri: str) -> None:
        self.__documents.pop(uri, None)
        self.invalidate()

    def invalidate(self, *, topology: bool = False) -> None:
        self.__revision += 1
        if topology:
            self.__topology_revision += 1
        self.__emit("revision_changed", {
            "revision": self.__revision,
            "topology_revision": self.__topology_revision,
            "topology": topology,
            "open_documents": len(self.__documents),
        })
        worker = self.__worker
        if worker is not None and worker.busy:
            if worker.active_operation == "initialize":
                return
            if (
                worker.active_operation == "sync"
                and worker.active_topology_revision == self.__topology_revision
            ):
                return
            if self.__retirement is None or self.__retirement.done():
                self.__retirement = asyncio.create_task(self.__retire(worker))

    async def start(self) -> JsonObject:
        async with self.__request_lock:
            await self.__ensure_worker()
            return self.__last_project_result

    async def analyze(self, *, full: bool, reason: str) -> JsonObject:
        result = await self.__request(
            "analyze",
            {"full": full, "reason": reason},
            sync=True,
        )
        if full:
            self.__last_project_result["full_revision"] = self.__revision
        self.__emit("analysis_completed", {
            "revision": self.__revision,
            "full": full,
            "reason": reason,
            "diagnostic_count": result.get("diagnostic_count", 0),
            "analysis_timings": result.get("analysis_timings", {}),
        })
        return result

    async def syntax(self, uri: str) -> JsonObject:
        text = next((text for path, text, _version in self.open_documents if path == uri), None)
        if text is None:
            raise WorkerOperationError(-32801, "document is no longer open")
        return await self.__request(
            "syntax",
            {"uri": uri, "text": text},
            sync=True,
        )

    async def query(self, method: str, params: JsonObject) -> JsonValue:
        if self.full_revision != self.__revision:
            await self.analyze(full=True, reason="semantic request")
        response = await self.__request(
            "query",
            {"method": method, "params": params},
            sync=True,
        )
        return response.get("value")

    async def format(self, uri: str, text: str | None) -> JsonValue:
        response = await self.__request(
            "format",
            {"uri": uri, "text": text},
            sync=True,
        )
        return response.get("value")

    async def load_project(self) -> JsonObject:
        await self.invalidate_async(topology=True)
        result = await self.__request("sync", self.__sync_payload(), sync=False)
        self.__last_project_result = result
        return result

    def begin_shutdown(self) -> None:
        self.__closed = True
        worker = self.__worker
        if worker is not None and (self.__retirement is None or self.__retirement.done()):
            self.__retirement = asyncio.create_task(self.__retire(worker, graceful=True))

    async def close(self) -> None:
        self.begin_shutdown()
        task = self.__retirement
        if task is not None:
            await task

    async def invalidate_async(self, *, topology: bool = False) -> None:
        self.invalidate(topology=topology)
        if self.__retirement is not None:
            await self.__retirement

    async def __request(
        self, operation: str, payload: JsonObject, *, sync: bool
    ) -> JsonObject:
        if self.__closed:
            raise WorkerFailure("language server is shutting down")
        if self.__waiting_requests >= _MAX_PENDING_REQUESTS:
            raise WorkerOperationError(-32803, "too many pending language-server requests")
        self.__waiting_requests += 1
        queued_at = time.monotonic_ns()
        self.__emit("request_queued", {
            "operation": operation,
            "revision": self.__revision,
            "queue_length": self.__waiting_requests,
        })
        try:
            async with self.__pending_slots:
                async with self.__request_lock:
                    queue_wait_ns = time.monotonic_ns() - queued_at
                    self.__emit("request_started", {
                        "operation": operation,
                        "revision": self.__revision,
                        "queue_wait_ns": queue_wait_ns,
                    })
                    return await self.__request_locked(operation, payload, sync=sync)
        except asyncio.CancelledError:
            self.__emit("request_cancelled", {
                "operation": operation,
                "revision": self.__revision,
            })
            raise
        finally:
            self.__waiting_requests -= 1

    async def __request_locked(
        self, operation: str, payload: JsonObject, *, sync: bool
    ) -> JsonObject:
        if self.__retry_revision != self.__revision:
            self.__retry_revision = self.__revision
            self.__retry_used = False
        for attempt in range(2):
            try:
                worker = await self.__ensure_worker()
                if sync and (
                    self.__synced_revision != self.__revision
                    or self.__synced_topology_revision != self.__topology_revision
                ):
                    sync_started = time.monotonic_ns()
                    self.__emit("worker_operation_started", {
                        "operation": "sync",
                        "revision": self.__revision,
                        "topology_revision": self.__topology_revision,
                    })
                    sync_response = await worker.request(
                        "sync",
                        revision=self.__revision,
                        topology_revision=self.__topology_revision,
                        payload=self.__sync_payload(),
                    )
                    self.__check_response(sync_response, "sync")
                    self.__last_project_result = require_object(
                        sync_response.get("result"), field="sync result"
                    )
                    self.__synced_revision = self.__revision
                    self.__synced_topology_revision = self.__topology_revision
                    self.__emit("worker_operation_finished", {
                        "operation": "sync",
                        "revision": self.__revision,
                        "elapsed_ns": time.monotonic_ns() - sync_started,
                    })
                operation_started = time.monotonic_ns()
                self.__emit("worker_operation_started", {
                    "operation": operation,
                    "revision": self.__revision,
                    "topology_revision": self.__topology_revision,
                })
                response = await worker.request(
                    operation,
                    revision=self.__revision,
                    topology_revision=self.__topology_revision,
                    payload=payload,
                )
                self.__check_response(response, operation)
                if response.get("revision") != self.__revision:
                    raise WorkerOperationError(-32801, "worker response has a stale revision")
                if response.get("topology_revision") != self.__topology_revision:
                    raise WorkerOperationError(
                        -32801, "worker response has a stale topology revision"
                    )
                result = response.get("result")
                if not isinstance(result, dict):
                    raise WorkerFailure("worker response result must be an object")
                self.__emit("worker_operation_finished", {
                    "operation": operation,
                    "revision": self.__revision,
                    "elapsed_ns": time.monotonic_ns() - operation_started,
                    "worker_elapsed_ms": response.get("elapsed_ms", 0),
                    "cache_hit": result.get("cached") is True,
                })
                if operation == "sync":
                    self.__synced_revision = self.__revision
                    self.__synced_topology_revision = self.__topology_revision
                return result
            except WorkerOperationError:
                raise
            except WorkerFailure:
                worker = self.__worker
                if worker is not None:
                    await self.__retire(worker)
                if attempt == 1 or self.__retry_used:
                    raise
                self.__retry_used = True
                self.restart_count += 1
                self.__emit("worker_restarted", {
                    "revision": self.__revision,
                    "restart_count": self.restart_count,
                })
        raise WorkerFailure(f"worker operation {operation} failed twice")

    async def __ensure_worker(self) -> WorkerProcess:
        if self.__retirement is not None:
            await self.__retirement
            self.__retirement = None
        worker = self.__worker
        if worker is not None and worker.returncode is None:
            return worker
        self.__epoch += 1
        cwd = Path(__file__).resolve().parents[1]
        worker = await WorkerProcess.start(epoch=self.__epoch, cwd=cwd)
        self.__emit("worker_started", {"epoch": self.__epoch, "pid": worker.process_id})
        self.__worker = worker
        self.__synced_revision = -1
        self.__synced_topology_revision = -1
        try:
            initialize = await worker.request(
                "initialize",
                revision=self.__revision,
                topology_revision=self.__topology_revision,
                payload=self.__initialization_payload(),
            )
            self.__check_response(initialize, "initialize")
            self.__last_project_result = require_object(
                initialize.get("result"), field="worker initialization result"
            )
        except Exception:
            await self.__retire(worker)
            raise
        return worker

    def __emit(self, event: str, fields: JsonObject) -> None:
        sink = self.__event_sink
        if sink is not None:
            sink(event, fields)

    async def __retire(self, worker: WorkerProcess, *, graceful: bool = False) -> None:
        if self.__worker is worker:
            self.__worker = None
        try:
            if graceful:
                await worker.shutdown(
                    revision=self.__revision,
                    topology_revision=self.__topology_revision,
                )
            else:
                await worker.terminate()
        except WorkerFailure:
            _LOGGER.exception("worker termination failed")

    def __initialization_payload(self) -> JsonObject:
        return {
            "compiler_root": None if self.__compiler_root is None else str(self.__compiler_root),
            "raw_pointers": self.__raw_pointers,
            "workspace_root": None if self.__workspace_root is None else str(self.__workspace_root),
        }

    def __sync_payload(self) -> JsonObject:
        documents: list[JsonValue] = []
        for uri, (text, version) in self.__documents.items():
            documents.append({"uri": uri, "text": text, "version": version})
        return {
            "workspace_root": None if self.__workspace_root is None else str(self.__workspace_root),
            "documents": documents,
        }

    @staticmethod
    def __check_response(response: JsonObject, operation: str) -> None:
        if response.get("ok") is True:
            return
        error = require_object(response.get("error"), field="worker error")
        code_value = error.get("code")
        message_value = error.get("message")
        if not isinstance(code_value, int) or isinstance(code_value, bool):
            raise WorkerFailure("worker error has an invalid code")
        if not isinstance(message_value, str):
            raise WorkerFailure("worker error has an invalid message")
        if code_value == -32801:
            raise WorkerOperationError(code_value, message_value)
        if code_value in (-32600, -32601, -32602, -32803):
            raise WorkerOperationError(code_value, message_value)
        if code_value == -32603:
            raise WorkerOperationError(code_value, message_value)
        raise WorkerFailure(f"worker returned an invalid {operation} error: {message_value}")
