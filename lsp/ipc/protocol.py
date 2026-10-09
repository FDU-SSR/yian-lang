"""Length-framed JSON messages for the private LSP worker channel."""

from __future__ import annotations

import json
from typing import cast

from lsp.ipc.types import JsonObject, JsonValue

__all__ = ["MAX_BODY_BYTES", "MAX_HEADER_BYTES", "FrameDecoder", "FrameError", "encode_message"]

MAX_HEADER_BYTES = 8 * 1024
MAX_BODY_BYTES = 64 * 1024 * 1024
_HEADER_END = b"\r\n\r\n"


class FrameError(ValueError):
    """An invalid or oversized worker frame."""


def encode_message(message: JsonObject) -> bytes:
    body = json.dumps(message, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if len(body) > MAX_BODY_BYTES:
        raise FrameError("worker message exceeds the 64 MiB body limit")
    return f"Content-Length: {len(body)}\r\n\r\n".encode("ascii") + body


class FrameDecoder:
    """Incrementally decode Content-Length framed UTF-8 JSON objects."""

    def __init__(self) -> None:
        self.__buffer = bytearray()
        self.__expected: int | None = None

    def feed(self, data: bytes) -> list[JsonObject]:
        self.__buffer.extend(data)
        messages: list[JsonObject] = []
        while True:
            if self.__expected is None:
                header_end = self.__buffer.find(_HEADER_END)
                if header_end < 0:
                    if len(self.__buffer) > MAX_HEADER_BYTES:
                        raise FrameError("worker frame header exceeds the 8 KiB limit")
                    break
                if header_end > MAX_HEADER_BYTES:
                    raise FrameError("worker frame header exceeds the 8 KiB limit")
                self.__expected = self.__content_length(bytes(self.__buffer[:header_end]))
                del self.__buffer[:header_end + len(_HEADER_END)]

            expected = self.__expected
            if len(self.__buffer) < expected:
                break
            body = bytes(self.__buffer[:expected])
            del self.__buffer[:expected]
            self.__expected = None
            try:
                value = cast(JsonValue, json.loads(body.decode("utf-8")))
            except (UnicodeDecodeError, json.JSONDecodeError) as error:
                raise FrameError(f"worker sent invalid UTF-8 JSON: {error}") from error
            if not isinstance(value, dict):
                raise FrameError("worker message must be a JSON object")
            messages.append(cast(JsonObject, value))
        return messages

    @staticmethod
    def __content_length(header: bytes) -> int:
        content_length: int | None = None
        for line in header.split(b"\r\n"):
            key, separator, value = line.partition(b":")
            if not separator or key.strip().lower() != b"content-length":
                continue
            if content_length is not None:
                raise FrameError("worker frame has duplicate Content-Length headers")
            try:
                content_length = int(value.strip())
            except ValueError as error:
                raise FrameError("worker frame has an invalid Content-Length") from error
        if content_length is None or content_length < 0 or content_length > MAX_BODY_BYTES:
            raise FrameError("worker frame has a missing or oversized Content-Length")
        return content_length
