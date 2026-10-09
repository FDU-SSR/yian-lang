"""Single-writer JSONL tracing for reproducible LSP measurements."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from threading import Lock

from lsp.ipc.types import JsonObject

__all__ = ["TraceWriter"]


class TraceWriter:
    """Write bounded, structured events from the protocol process."""

    def __init__(self, path: Path) -> None:
        self.__stream = path.open("a", encoding="utf-8")
        self.__lock = Lock()

    def emit(self, event: str, fields: JsonObject) -> None:
        record: JsonObject = {
            "event": event,
            "monotonic_ns": time.monotonic_ns(),
            "process_id": os.getpid(),
            **fields,
        }
        line = json.dumps(record, ensure_ascii=False, separators=(",", ":"))
        with self.__lock:
            self.__stream.write(line + "\n")
            self.__stream.flush()

    def close(self) -> None:
        with self.__lock:
            self.__stream.close()
