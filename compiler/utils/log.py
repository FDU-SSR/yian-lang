"""Compiler log infrastructure.

Phase 1: LogLevel, LogOutput, LogFormatter — zero compiler dependencies.
Phase 2: LogChannel, NoopChannel.
Phase 3: CompilerLog (singleton), LogFilter, configuration parsing.
"""
# pylint: disable=too-few-public-methods

from __future__ import annotations

import sys
from abc import ABC, abstractmethod
from enum import Enum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from compiler.frontend.lex.position import SrcSpan


# =============================================================================
# Phase 1 — LogLevel, LogOutput, LogFormatter
# =============================================================================


class LogLevel(Enum):
    """Severity levels for log messages, in increasing verbosity."""

    OFF = 0
    ERROR = 1
    WARN = 2
    INFO = 3
    DEBUG = 4
    TRACE = 5

    @classmethod
    def from_str(cls, s: str) -> "LogLevel":
        """Parse a case-insensitive level name, e.g. ``\"debug\"`` → ``DEBUG``."""
        try:
            return cls[s.upper()]
        except KeyError:
            raise ValueError(f"unknown log level: {s!r}") from None


# ── LogOutput ────────────────────────────────────────────────────────────────


class LogOutput(ABC):
    """Abstract sink for formatted log messages."""

    @abstractmethod
    def write(self, text: str) -> None: ...


class StderrOutput(LogOutput):
    """Write log messages to standard error."""

    def write(self, text: str) -> None:
        print(text, file=sys.stderr, flush=True)


class FileOutput(LogOutput):
    """Write log messages to a file.  Line-buffered."""

    def __init__(self, path: str) -> None:
        self.__file = open(path, "w", encoding="utf-8", buffering=1)  # pylint: disable=consider-using-with

    def write(self, text: str) -> None:
        self.__file.write(text + "\n")

    def close(self) -> None:
        self.__file.close()


class MultiOutput(LogOutput):
    """Fan-out log messages to multiple sinks."""

    def __init__(self, outputs: list[LogOutput]) -> None:
        self.__outputs = outputs

    def write(self, text: str) -> None:
        for o in self.__outputs:
            o.write(text)


# ── LogFormatter ─────────────────────────────────────────────────────────────


class LogFormatter:
    LEVEL_WIDTH = 5
    CHANNEL_WIDTH = 12

    @staticmethod
    def format(
        level: LogLevel, channel: str, depth: int,
        msg: str, span: "SrcSpan | None",
    ) -> str:
        indent = "  " * depth
        lev_str = level.name.rjust(LogFormatter.LEVEL_WIDTH)
        ch_str = channel.ljust(LogFormatter.CHANNEL_WIDTH)[:LogFormatter.CHANNEL_WIDTH]
        prefix = f"[{lev_str}][{ch_str}]{indent}"
        lines = msg.split("\n")
        out = [f"{prefix}{lines[0]}"]
        cont_indent = " " * len(prefix)
        for line in lines[1:]:
            out.append(f"{cont_indent}{line}")
        if span is not None:
            out.append(f"{cont_indent}--> {span}")
        return "\n".join(out)
