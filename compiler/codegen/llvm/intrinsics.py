"""
External C function declarations.
"""

from __future__ import annotations

from enum import Enum, auto

from llvmlite import ir  # type: ignore[import-untyped]


class IntrinsicKind(Enum):
    Malloc = auto()
    Free = auto()
    Write = auto()
    Read = auto()
    Exit = auto()
    MemCopy = auto()
    SysRandom = auto()


_i8p = ir.PointerType(ir.IntType(8))
_i32 = ir.IntType(32)
_i64 = ir.IntType(64)
_void = ir.VoidType()

_DECLS: dict[IntrinsicKind, tuple[ir.Type, list[ir.Type], str]] = {
    IntrinsicKind.Malloc:    (_i8p, [_i64], "malloc"),
    IntrinsicKind.Free:      (_void, [_i8p], "free"),
    IntrinsicKind.Write:     (_i64, [_i32, _i8p, _i64], "write"),
    IntrinsicKind.Read:      (_i64, [_i32, _i8p, _i64], "read"),
    IntrinsicKind.Exit:      (_void, [_i32], "exit"),
    IntrinsicKind.MemCopy:   (_void, [_i8p, _i8p, _i64], "memcpy"),
    IntrinsicKind.SysRandom: (_i32, [], "rand"),
}


class IntrinsicManager:
    def __init__(self, module: ir.Module) -> None:
        self.__mod = module
        self.__cache: dict[IntrinsicKind, ir.Function] = {}

    def get(self, kind: IntrinsicKind) -> ir.Function:
        if kind in self.__cache:
            return self.__cache[kind]
        ret, params, name = _DECLS[kind]
        func = ir.Function(self.__mod, ir.FunctionType(ret, params), name=name)
        self.__cache[kind] = func
        return func
