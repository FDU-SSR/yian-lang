"""Compiler log infrastructure.

Phase 1: LogLevel, LogOutput, LogFormatter — zero compiler dependencies.
Phase 2: LogChannel, NoopChannel.
Phase 3: CompilerLog (singleton), LogFilter, configuration parsing.
"""
# pylint: disable=too-few-public-methods

from __future__ import annotations

import sys
import time
from abc import ABC, abstractmethod
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator
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
    def from_str(cls, s: str) -> LogLevel:
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
        msg: str, span: SrcSpan | None,
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


# =============================================================================
# Phase 2 — LogFilter, LogChannel, NoopChannel
# =============================================================================


@dataclass
class LogFilter:
    """Optional source-location filter for a channel.

    When set, only messages whose *span* contains *pattern* (substring
    match) are emitted.  Messages without a span always pass through.
    """

    pattern: str
    mode: str = "file"  # "file" | "function" | "span"

    def matches(self, span: SrcSpan | None) -> bool:
        """Return True if *span* passes this filter."""
        if span is None:
            return True
        if self.mode == "file":
            return self.pattern in str(span.start.path)
        return True


# ── LogChannel ────────────────────────────────────────────────────────────────


class LogChannel:
    """Per-subsystem log channel with level, filter, indent, and scope support.

    Created by ``CompilerLog.channel(name)`` — users should not instantiate
    directly.
    """

    def __init__(
        self, name: str, level: LogLevel, output: LogOutput,
    ) -> None:
        self.__name = name
        self.__level = level
        self.__output = output
        self.__depth = 0
        self.__filter: LogFilter | None = None
        self.__formatter = LogFormatter()

    # ── properties ────────────────────────────────────────────────────────

    @property
    def name(self) -> str:
        return self.__name

    @property
    def level(self) -> LogLevel:
        return self.__level

    @level.setter
    def level(self, value: LogLevel) -> None:
        self.__level = value

    @property
    def filter(self) -> LogFilter | None:
        return self.__filter

    @filter.setter
    def filter(self, value: LogFilter | None) -> None:
        self.__filter = value

    # ── level check ───────────────────────────────────────────────────────

    def enabled(self, level: LogLevel) -> bool:
        """Return True if messages at *level* would be emitted."""
        return level.value <= self.__level.value and level != LogLevel.OFF

    # ── log methods ───────────────────────────────────────────────────────

    def error(self, msg: str | Callable[[], str], span: SrcSpan | None = None) -> None:
        self.__emit(LogLevel.ERROR, msg, span)

    def warn(self, msg: str | Callable[[], str], span: SrcSpan | None = None) -> None:
        self.__emit(LogLevel.WARN, msg, span)

    def info(self, msg: str | Callable[[], str], span: SrcSpan | None = None) -> None:
        self.__emit(LogLevel.INFO, msg, span)

    def debug(self, msg: str | Callable[[], str], span: SrcSpan | None = None) -> None:
        self.__emit(LogLevel.DEBUG, msg, span)

    def trace(self, msg: str | Callable[[], str], span: SrcSpan | None = None) -> None:
        self.__emit(LogLevel.TRACE, msg, span)

    # ── indent ────────────────────────────────────────────────────────────

    def push_indent(self) -> None:
        """Increase the indentation level by one."""
        self.__depth += 1

    def pop_indent(self) -> None:
        """Decrease the indentation level by one (clamped at zero)."""
        self.__depth = max(0, self.__depth - 1)

    @contextmanager
    def indent(self) -> Iterator[None]:
        """Context manager that increases indent for the duration of the block."""
        self.push_indent()
        try:
            yield
        finally:
            self.pop_indent()

    # ── scope ─────────────────────────────────────────────────────────────

    @contextmanager
    def scope(self, label: str, level: LogLevel = LogLevel.DEBUG) -> Iterator[None]:
        """Context manager that logs entry / exit and elapsed time.

        Usage::

            with ch.scope(\"checking main\"):
                ...
        """
        start = time.monotonic()
        self.__emit(level, f"→ {label}", None)
        try:
            with self.indent():
                yield
        finally:
            elapsed = (time.monotonic() - start) * 1000
            self.__emit(level, f"← {label} ({elapsed:.0f}ms)", None)

    # ── internal ──────────────────────────────────────────────────────────

    def __emit(
        self, level: LogLevel,
        msg: str | Callable[[], str],
        span: SrcSpan | None,
    ) -> None:
        """Format and write a log message if the level and filter allow it."""
        if not self.enabled(level):
            return
        if self.__filter is not None and not self.__filter.matches(span):
            return
        if callable(msg):
            msg = msg()
        text = self.__formatter.format(level, self.__name, self.__depth, msg, span)
        self.__output.write(text)


# ── NoopChannel ───────────────────────────────────────────────────────────────


class NoopChannel:
    """Silent channel with the same interface as ``LogChannel``.

    All method calls are no-ops.  Used in release builds or when
    logging is disabled (``YIAN_LOG=all=OFF``).
    """

    name: str = ""
    level: LogLevel = LogLevel.OFF
    filter: LogFilter | None = None

    @staticmethod
    def enabled(level: LogLevel) -> bool:
        return False

    def error(self, msg: str | Callable[[], str], span: SrcSpan | None = None) -> None:
        pass

    def warn(self, msg: str | Callable[[], str], span: SrcSpan | None = None) -> None:
        pass

    def info(self, msg: str | Callable[[], str], span: SrcSpan | None = None) -> None:
        pass

    def debug(self, msg: str | Callable[[], str], span: SrcSpan | None = None) -> None:
        pass

    def trace(self, msg: str | Callable[[], str], span: SrcSpan | None = None) -> None:
        pass

    @staticmethod
    def push_indent() -> None:
        pass

    @staticmethod
    def pop_indent() -> None:
        pass

    @staticmethod
    @contextmanager
    def __noop_ctx() -> Iterator[None]:
        """Reusable no-op context manager."""
        yield

    def indent(self) -> AbstractContextManager[None]:
        """Return a no-op context manager."""
        return self.__noop_ctx()

    def scope(self, label: str = "", level: LogLevel | None = None) -> AbstractContextManager[None]:
        # pylint: disable=unused-argument
        return self.__noop_ctx()
