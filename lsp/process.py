"""Asynchronous ownership of the single Python analysis worker process."""

from __future__ import annotations

import asyncio
import logging
import os
import signal
import subprocess
import sys
from pathlib import Path

from lsp.ipc.protocol import FrameDecoder, FrameError, encode_message
from lsp.ipc.types import JsonObject

__all__ = ["WorkerFailure", "WorkerProcess"]

_LOGGER = logging.getLogger(__name__)
_PROTOCOL_VERSION = 1
_READY_TIMEOUT_SECONDS = 10.0
_TERMINATE_GRACE_SECONDS = 0.25
_MAX_QUEUED_BYTES = 128 * 1024 * 1024


class WorkerFailure(RuntimeError):
    """A worker startup, protocol, or operation failure."""


class WorkerProcess:
    """One framed IPC connection and its child process.

    File descriptors are nonblocking and serviced by the asyncio loop. The
    process is the sole owner of compiler state; this object only owns JSON
    messages and process resources.
    """

    def __init__(self, process: subprocess.Popen[bytes], epoch: int) -> None:
        self.__process = process
        self.__epoch = epoch
        self.__loop = asyncio.get_running_loop()
        self.__decoder = FrameDecoder()
        self.__ready: asyncio.Future[None] = self.__loop.create_future()
        self.__pending: dict[int, asyncio.Future[JsonObject]] = {}
        self.__next_request_id = 0
        self.__write_lock = asyncio.Lock()
        self.__exchange_lock = asyncio.Lock()
        self.__queued_bytes = 0
        self.__closed = False
        self.__failure: WorkerFailure | None = None
        self.__stderr_buffer = bytearray()
        self.__active_operation: str | None = None
        self.__active_topology_revision = -1

        if process.stdin is None or process.stdout is None or process.stderr is None:
            raise WorkerFailure("worker pipes were not created")
        self.__stdin_fd = process.stdin.fileno()
        self.__stdout_fd = process.stdout.fileno()
        self.__stderr_fd = process.stderr.fileno()
        os.set_blocking(self.__stdin_fd, False)
        os.set_blocking(self.__stdout_fd, False)
        os.set_blocking(self.__stderr_fd, False)
        self.__loop.add_reader(self.__stdout_fd, self.__read_stdout)
        self.__loop.add_reader(self.__stderr_fd, self.__read_stderr)

    @classmethod
    async def start(cls, *, epoch: int, cwd: Path) -> WorkerProcess:
        process = subprocess.Popen(
            [sys.executable, "-m", "lsp.worker", "--epoch", str(epoch)],
            cwd=cwd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=0,
            close_fds=True,
        )
        worker = cls(process, epoch)
        try:
            await asyncio.wait_for(asyncio.shield(worker.__ready), _READY_TIMEOUT_SECONDS)
        except (TimeoutError, WorkerFailure) as error:
            await worker.terminate()
            raise WorkerFailure(f"worker did not become ready: {error}") from error
        return worker

    @property
    def busy(self) -> bool:
        return bool(self.__pending)

    @property
    def returncode(self) -> int | None:
        return self.__process.poll()

    @property
    def process_id(self) -> int:
        return self.__process.pid

    @property
    def active_operation(self) -> str | None:
        return self.__active_operation

    @property
    def active_topology_revision(self) -> int:
        return self.__active_topology_revision

    async def request(
        self,
        operation: str,
        *,
        revision: int,
        topology_revision: int,
        payload: JsonObject,
    ) -> JsonObject:
        exchange = asyncio.create_task(self.__exchange(
            operation,
            revision=revision,
            topology_revision=topology_revision,
            payload=payload,
        ))
        try:
            return await asyncio.shield(exchange)
        except asyncio.CancelledError:
            exchange.add_done_callback(self.__consume_abandoned)
            raise

    async def __exchange(
        self,
        operation: str,
        *,
        revision: int,
        topology_revision: int,
        payload: JsonObject,
    ) -> JsonObject:
        async with self.__exchange_lock:
            return await self.__exchange_one(
                operation,
                revision=revision,
                topology_revision=topology_revision,
                payload=payload,
            )

    async def __exchange_one(
        self,
        operation: str,
        *,
        revision: int,
        topology_revision: int,
        payload: JsonObject,
    ) -> JsonObject:
        if self.__closed:
            raise WorkerFailure("worker is closed")
        if self.__failure is not None:
            raise self.__failure

        self.__next_request_id += 1
        request_id = self.__next_request_id
        future: asyncio.Future[JsonObject] = self.__loop.create_future()
        self.__pending[request_id] = future
        message: JsonObject = {
            "protocol_version": _PROTOCOL_VERSION,
            "epoch": self.__epoch,
            "internal_request_id": request_id,
            "operation": operation,
            "revision": revision,
            "topology_revision": topology_revision,
            "payload": payload,
        }
        try:
            frame = encode_message(message)
        except FrameError as error:
            raise WorkerFailure(str(error)) from error
        self.__queued_bytes += len(frame)
        if self.__queued_bytes > _MAX_QUEUED_BYTES:
            self.__pending.pop(request_id, None)
            self.__queued_bytes -= len(frame)
            raise WorkerFailure("worker IPC queue exceeds the 128 MiB limit")
        self.__active_operation = operation
        self.__active_topology_revision = topology_revision
        try:
            async with self.__write_lock:
                await self.__write(frame)
            response = await asyncio.shield(future)
        except asyncio.CancelledError:
            # Keep the response slot until the worker replies. Cancelling one
            # client waiter must not corrupt a response shared by other work.
            raise
        except (OSError, WorkerFailure) as error:
            self.__pending.pop(request_id, None)
            if not future.done():
                future.cancel()
            raise WorkerFailure(f"worker request {operation} failed: {error}") from error
        finally:
            self.__queued_bytes = max(0, self.__queued_bytes - len(frame))
            self.__active_operation = None
            self.__active_topology_revision = -1
        return response

    @staticmethod
    def __consume_abandoned(task: asyncio.Task[JsonObject]) -> None:
        if not task.cancelled():
            task.exception()

    async def terminate(self) -> None:
        if self.__closed:
            return
        self.__closed = True
        self.__loop.remove_reader(self.__stdout_fd)
        self.__loop.remove_reader(self.__stderr_fd)
        self.__loop.remove_writer(self.__stdin_fd)

        process = self.__process
        if process.poll() is None:
            try:
                process.send_signal(signal.SIGTERM)
            except ProcessLookupError:
                pass
            if not await self.__wait_for_exit(_TERMINATE_GRACE_SECONDS):
                try:
                    process.kill()
                except ProcessLookupError:
                    pass
                await self.__wait_for_exit(2.0)
        if process.poll() is None:
            raise WorkerFailure("worker could not be reaped after SIGKILL")
        for stream in (process.stdin, process.stdout, process.stderr):
            if stream is not None:
                stream.close()
        self.__fail_all(WorkerFailure("worker stopped"))

    async def shutdown(self, *, revision: int, topology_revision: int) -> None:
        """Ask the worker to release its snapshot before stopping the process."""
        if self.__closed:
            return
        try:
            await asyncio.wait_for(
                self.request(
                    "shutdown",
                    revision=revision,
                    topology_revision=topology_revision,
                    payload={},
                ),
                timeout=10.0,
            )
        except (TimeoutError, WorkerFailure):
            pass
        await self.terminate()

    async def __write(self, frame: bytes) -> None:
        view = memoryview(frame)
        while view:
            if self.__failure is not None:
                raise self.__failure
            try:
                written = os.write(self.__stdin_fd, view)
            except BlockingIOError:
                await self.__wait_writable()
                continue
            except BrokenPipeError as error:
                raise WorkerFailure("worker closed its input pipe") from error
            if written <= 0:
                raise WorkerFailure("worker input pipe made no progress")
            view = view[written:]

    async def __wait_writable(self) -> None:
        future: asyncio.Future[None] = self.__loop.create_future()

        def ready() -> None:
            if not future.done():
                future.set_result(None)
            self.__loop.remove_writer(self.__stdin_fd)

        self.__loop.add_writer(self.__stdin_fd, ready)
        try:
            await future
        finally:
            self.__loop.remove_writer(self.__stdin_fd)

    def __read_stdout(self) -> None:
        try:
            while True:
                data = os.read(self.__stdout_fd, 65536)
                if not data:
                    self.__break(WorkerFailure("worker closed its output pipe"))
                    return
                for message in self.__decoder.feed(data):
                    self.__accept_message(message)
        except BlockingIOError:
            return
        except (OSError, FrameError, WorkerFailure) as error:
            self.__break(WorkerFailure(f"invalid worker output: {error}"))

    def __read_stderr(self) -> None:
        try:
            while True:
                data = os.read(self.__stderr_fd, 65536)
                if not data:
                    self.__loop.remove_reader(self.__stderr_fd)
                    self.__log_stderr()
                    return
                self.__stderr_buffer.extend(data)
                while b"\n" in self.__stderr_buffer:
                    line, _, remaining = self.__stderr_buffer.partition(b"\n")
                    self.__stderr_buffer = bytearray(remaining)
                    _LOGGER.info("worker: %s", line.decode("utf-8", errors="replace")[:4096])
                if len(self.__stderr_buffer) > 8192:
                    self.__log_stderr()
        except BlockingIOError:
            return
        except OSError as error:
            self.__break(WorkerFailure(f"cannot read worker stderr: {error}"))

    def __log_stderr(self) -> None:
        if self.__stderr_buffer:
            line = bytes(self.__stderr_buffer[:4096]).decode("utf-8", errors="replace")
            self.__stderr_buffer.clear()
            _LOGGER.info("worker: %s", line)

    def __accept_message(self, message: JsonObject) -> None:
        if message.get("protocol_version") != _PROTOCOL_VERSION:
            raise WorkerFailure("worker IPC protocol version mismatch")
        if message.get("epoch") != self.__epoch:
            raise WorkerFailure("worker response belongs to a stale epoch")
        operation = message.get("operation")
        request_id = message.get("internal_request_id")
        if operation == "ready" and request_id == 0:
            if not self.__ready.done():
                self.__ready.set_result(None)
            return
        if not isinstance(request_id, int) or isinstance(request_id, bool):
            raise WorkerFailure("worker response has no valid request id")
        future = self.__pending.pop(request_id, None)
        if future is None:
            raise WorkerFailure(f"worker returned an unknown request id: {request_id}")
        if not future.done():
            future.set_result(message)

    def __break(self, error: WorkerFailure) -> None:
        if self.__failure is not None:
            return
        self.__failure = error
        if not self.__ready.done():
            self.__ready.set_exception(error)
        self.__fail_all(error)

    def __fail_all(self, error: WorkerFailure) -> None:
        for future in self.__pending.values():
            if not future.done():
                future.set_exception(error)
        self.__pending.clear()

    async def __wait_for_exit(self, timeout: float) -> bool:
        deadline = self.__loop.time() + timeout
        while self.__process.poll() is None and self.__loop.time() < deadline:
            await asyncio.sleep(0.01)
        return self.__process.poll() is not None
