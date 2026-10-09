"""JSON value types shared by the protocol process and worker."""

from __future__ import annotations

from typing import TypeAlias

JsonValue: TypeAlias = None | bool | int | float | str | list["JsonValue"] | dict[str, "JsonValue"]
JsonObject: TypeAlias = dict[str, JsonValue]


def require_object(value: JsonValue, *, field: str) -> JsonObject:
    if not isinstance(value, dict):
        raise ValueError(f"{field} must be a JSON object")
    return value


def require_string(value: JsonValue, *, field: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field} must be a string")
    return value


def require_integer(value: JsonValue, *, field: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f"{field} must be an integer")
    return value
