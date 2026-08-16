"""
External C function declarations.
"""

from __future__ import annotations

from enum import Enum, auto

from llvmlite import ir  # type: ignore[import-untyped]

# 胖指针块头布局(定义 7):每个堆块布局为「锁头 + 负载」——块首 H 字节锁槽
# (锁头仅锁槽,H = w = 8B),锁头区 [b, b+H) 在负载之前;分配锚定 data = b + H。
# 本表 Malloc 内在函数在 t8 与块头锁槽写键交互(规则 3.6.1:锁槽首字写
# k ← Gen(),堆键最高位 1),Free/Delete 与写 SENTINEL 交互(规则 3.6.2)。
# 机制常量(SENTINEL / KeyGen / BlockHeader / FrameLock / 谓词)定义于
# compiler/codegen/cfg/lockmech.py;本模块只声明 C 函数签名。


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
