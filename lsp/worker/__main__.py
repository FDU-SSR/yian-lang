"""Run one serial analysis worker over framed JSON on stdin/stdout."""

from __future__ import annotations

import cProfile
import logging
import os
import sys
import time
from argparse import ArgumentParser

from lsp.ipc.protocol import FrameDecoder, FrameError, encode_message
from lsp.ipc.types import JsonObject, require_integer, require_object, require_string
from lsp.worker.service import OperationFailure, WorkerService

_PROTOCOL_VERSION = 1
_LOGGER = logging.getLogger("lsp.worker")


def main() -> int:
    parser = ArgumentParser(add_help=False)
    parser.add_argument("--epoch", type=int, required=True)
    args = parser.parse_args()
    logging.basicConfig(
        level="INFO",
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )
    decoder = FrameDecoder()
    service: WorkerService | None = None
    epoch = args.epoch
    profile_path = os.environ.get("YIAN_LSP_PROFILE_FILE")
    profile = cProfile.Profile() if profile_path else None
    if profile is not None:
        profile.enable()
    _write({
        "protocol_version": _PROTOCOL_VERSION,
        "epoch": epoch,
        "internal_request_id": 0,
        "operation": "ready",
    })

    while True:
        data = os.read(sys.stdin.fileno(), 65536)
        if not data:
            __dump_profile(profile, profile_path)
            return 0
        try:
            messages = decoder.feed(data)
        except FrameError as error:
            _LOGGER.error("invalid IPC input: %s", error)
            __dump_profile(profile, profile_path)
            return 2
        for message in messages:
            request_id = 0
            operation = "invalid"
            revision = 0
            topology_revision = 0
            try:
                request_epoch = require_integer(message.get("epoch"), field="epoch")
                request_id = require_integer(
                    message.get("internal_request_id"), field="internal_request_id"
                )
                operation = require_string(message.get("operation"), field="operation")
                revision = require_integer(message.get("revision"), field="revision")
                topology_revision = require_integer(
                    message.get("topology_revision"), field="topology_revision"
                )
                payload = require_object(message.get("payload"), field="payload")
                if message.get("protocol_version") != _PROTOCOL_VERSION:
                    raise OperationFailure(-32600, "unsupported worker protocol version")
                if request_id <= 0:
                    raise OperationFailure(-32600, "worker request id must be positive")
                if service is None:
                    if operation != "initialize":
                        raise OperationFailure(-32600, "worker must be initialized first")
                    epoch = request_epoch
                    service = WorkerService(epoch=epoch)
                if request_epoch != epoch:
                    raise OperationFailure(-32600, "request belongs to a stale worker epoch")
                started = time.perf_counter()
                result = service.handle(
                    operation,
                    payload,
                    revision=revision,
                    topology_revision=topology_revision,
                )
                elapsed_ms = (time.perf_counter() - started) * 1000
                response: JsonObject = {
                    "protocol_version": _PROTOCOL_VERSION,
                    "epoch": epoch,
                    "internal_request_id": request_id,
                    "operation": operation,
                    "revision": revision,
                    "topology_revision": topology_revision,
                    "ok": True,
                    "result": result,
                    "elapsed_ms": elapsed_ms,
                }
            except OperationFailure as error:
                response = {
                    "protocol_version": _PROTOCOL_VERSION,
                    "epoch": epoch,
                    "internal_request_id": request_id,
                    "operation": operation,
                    "revision": revision,
                    "topology_revision": topology_revision,
                    "ok": False,
                    "error": {"code": error.code, "message": str(error)},
                }
            except Exception as error:
                _LOGGER.exception("worker operation failed")
                response = {
                    "protocol_version": _PROTOCOL_VERSION,
                    "epoch": epoch,
                    "internal_request_id": request_id,
                    "operation": operation,
                    "revision": revision,
                    "topology_revision": topology_revision,
                    "ok": False,
                    "error": {"code": -32603, "message": str(error)},
                }
            if operation == "shutdown":
                __dump_profile(profile, profile_path)
            try:
                _write(response)
            except (BrokenPipeError, FrameError) as error:
                _LOGGER.error("cannot write worker response: %s", error)
                __dump_profile(profile, profile_path)
                return 2
            if operation == "shutdown":
                return 0


def _write(message: JsonObject) -> None:
    sys.stdout.buffer.write(encode_message(message))
    sys.stdout.buffer.flush()


def __dump_profile(profile: cProfile.Profile | None, path: str | None) -> None:
    if profile is None or path is None:
        return
    profile.disable()
    profile.dump_stats(path)


if __name__ == "__main__":
    raise SystemExit(main())
