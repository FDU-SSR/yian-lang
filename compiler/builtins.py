"""Names for compiler-provided instructions in the reserved ``@`` namespace."""

from __future__ import annotations

from enum import Enum


class BuiltinKind(Enum):
    SizeOf = "sizeof"
    Undef = "undef"
    Dangling = "dangling"
    BitCast = "bitcast"
    Alloc = "alloc"
    Realloc = "realloc"
    Panic = "panic"
    RuntimeFail = "runtime_fail"
    AssumeInit = "assume_init"
    MemCopy = "memcpy"
    SliceFromParts = "slice_from_parts"
    SliceGetPtr = "slice_get_ptr"
    SliceGetLen = "slice_get_len"
    StrFromParts = "str_from_parts"
    StrGetPtr = "str_get_ptr"
    StrGetLen = "str_get_len"
    SysRead = "sys_read"
    SysWrite = "sys_write"
    Open = "open"
    Close = "close"
    Sqrt = "sqrt"
    Sin = "sin"
    Cos = "cos"
    Argc = "argc"
    ArgBytes = "arg_bytes"
    Exit = "exit"

    @property
    def spelling(self) -> str:
        return f"@{self.value}"

    @classmethod
    def try_from_name(cls, name: str) -> BuiltinKind | None:
        return _BUILTIN_KIND_BY_NAME.get(name)


_BUILTIN_KIND_BY_NAME: dict[str, BuiltinKind] = {kind.value: kind for kind in BuiltinKind}
