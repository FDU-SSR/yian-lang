"""Shared runtime failure codes and their stable diagnostics."""

from __future__ import annotations

from enum import IntEnum


class RuntimeErrorCode(IntEnum):
    """Stable identifiers for unrecoverable runtime failures."""

    S001 = 1001
    S002 = 1002
    S003 = 1003
    S004 = 1004
    S005 = 1005
    S006 = 1006
    S007 = 1007
    R001 = 2001
    R002 = 2002
    R003 = 2003


__RUNTIME_ERROR_MESSAGES: dict[RuntimeErrorCode, bytes] = {
    RuntimeErrorCode.S001: b"yian: safety error [S001]: out-of-bounds memory access\n",
    RuntimeErrorCode.S002: b"yian: safety error [S002]: invalid memory access\n",
    RuntimeErrorCode.S003: b"yian: safety error [S003]: dangling reference access\n",
    RuntimeErrorCode.S004: b"yian: safety error [S004]: invalid pointer arithmetic\n",
    RuntimeErrorCode.S005: b"yian: safety error [S005]: invalid cross-object pointer operation\n",
    RuntimeErrorCode.S006: b"yian: safety error [S006]: invalid deallocation\n",
    RuntimeErrorCode.S007: b"yian: safety error [S007]: empty slice cannot form a reference\n",
    RuntimeErrorCode.R001: b"yian: runtime error [R001]: allocation size overflow\n",
    RuntimeErrorCode.R002: b"yian: runtime error [R002]: memory allocation failed\n",
    RuntimeErrorCode.R003: b"yian: runtime error [R003]: safety metadata exhausted\n",
}

__RUNTIME_ERROR_CODES: dict[str, RuntimeErrorCode] = {
    code.name: code for code in RuntimeErrorCode
}


def parse_runtime_error_code(value: str) -> RuntimeErrorCode | None:
    """Parse a source spelling such as ``S001`` or return ``None``."""
    return __RUNTIME_ERROR_CODES.get(value)


def runtime_error_message(code: RuntimeErrorCode) -> bytes:
    """Return the complete protocol diagnostic, including its newline."""
    return __RUNTIME_ERROR_MESSAGES[code]


def runtime_error_name(code: RuntimeErrorCode) -> str:
    """Return the stable textual identifier used in HIR/CFG dumps."""
    return code.name
