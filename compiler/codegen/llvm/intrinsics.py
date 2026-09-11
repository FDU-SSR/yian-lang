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
    Open = auto()
    Close = auto()
    Exit = auto()
    MemCopy = auto()
    SysRandom = auto()


class IntrinsicManager:
    _DECLARATIONS: dict[IntrinsicKind, tuple[ir.Type, list[ir.Type], str]] = {
        IntrinsicKind.Malloc:    (ir.PointerType(ir.IntType(8)), [ir.IntType(64)], "malloc"),
        IntrinsicKind.Free:      (ir.VoidType(), [ir.PointerType(ir.IntType(8))], "free"),
        IntrinsicKind.Write:     (ir.IntType(64), [ir.IntType(32), ir.PointerType(ir.IntType(8)), ir.IntType(64)], "write"),
        IntrinsicKind.Read:      (ir.IntType(64), [ir.IntType(32), ir.PointerType(ir.IntType(8)), ir.IntType(64)], "read"),
        IntrinsicKind.Open:      (ir.IntType(32), [ir.PointerType(ir.IntType(8)), ir.IntType(32), ir.IntType(32)], "open"),
        IntrinsicKind.Close:     (ir.IntType(32), [ir.IntType(32)], "close"),
        IntrinsicKind.Exit:      (ir.VoidType(), [ir.IntType(32)], "exit"),
        IntrinsicKind.MemCopy:   (ir.VoidType(), [ir.PointerType(ir.IntType(8)), ir.PointerType(ir.IntType(8)), ir.IntType(64)], "memcpy"),
        IntrinsicKind.SysRandom: (ir.IntType(32), [], "rand"),
    }

    def __init__(self, module: ir.Module) -> None:
        self.__module = module
        self.__cache: dict[IntrinsicKind, ir.Function] = {}

    def get(self, kind: IntrinsicKind) -> ir.Function:
        if kind in self.__cache:
            return self.__cache[kind]
        return_type, param_types, name = self._DECLARATIONS[kind]
        func = ir.Function(self.__module, ir.FunctionType(return_type, param_types), name=name)
        self.__cache[kind] = func
        return func
