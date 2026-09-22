"""
External C function declarations.
"""

from __future__ import annotations

from enum import Enum, auto

from llvmlite import ir  # type: ignore[import-untyped]

# 胖指针块布局:每个堆块布局为「块头 + 负载」——块首 8 B 元数据
# ({extent:u32, pad:u32},块头区 [b, b+8) 在负载之前;分配锚定 data = b + 8)。
# 分配时把这一轮的身份与该负载锚写进全局锁表项(key / anchor_lo32),
# 释放把它换代并把下标压回自由链;本模块只声明这些 C 函数的签名,
# 机制常量与谓词(SENTINEL / BlockHeader / LockEntry / FrameLockArena / is_heap /
# live / is_raw)定义于 compiler/codegen/cfg/lockmech.py。


class IntrinsicKind(Enum):
    Malloc = auto()
    Free = auto()
    Write = auto()
    Read = auto()
    Open = auto()
    Close = auto()
    ImmediateExit = auto()
    StrLen = auto()
    MemCopy = auto()
    Sqrt = auto()
    SysRandom = auto()


class IntrinsicManager:
    __DECLARATIONS: dict[IntrinsicKind, tuple[ir.Type, list[ir.Type], str]] = {
        IntrinsicKind.Malloc:    (ir.PointerType(), [ir.IntType(64)], "malloc"),
        IntrinsicKind.Free:      (ir.VoidType(), [ir.PointerType()], "free"),
        IntrinsicKind.Write:     (ir.IntType(64), [ir.IntType(32), ir.PointerType(), ir.IntType(64)], "write"),
        IntrinsicKind.Read:      (ir.IntType(64), [ir.IntType(32), ir.PointerType(), ir.IntType(64)], "read"),
        IntrinsicKind.Open:      (ir.IntType(32), [ir.PointerType(), ir.IntType(32), ir.IntType(32)], "open"),
        IntrinsicKind.Close:     (ir.IntType(32), [ir.IntType(32)], "close"),
        IntrinsicKind.ImmediateExit: (ir.VoidType(), [ir.IntType(32)], "_exit"),
        IntrinsicKind.StrLen: (ir.IntType(64), [ir.PointerType()], "strlen"),
        IntrinsicKind.MemCopy:   (ir.VoidType(), [ir.PointerType(), ir.PointerType(), ir.IntType(64)], "memcpy"),
        # LLVM 内建(llvm.sqrt.f64): 后端直接落 sqrtsd, 与 C 参考的 `sqrt()` 同一原语。
        IntrinsicKind.Sqrt:      (ir.DoubleType(), [ir.DoubleType()], "llvm.sqrt.f64"),
        IntrinsicKind.SysRandom: (ir.IntType(32), [], "rand"),
    }

    def __init__(self, module: ir.Module) -> None:
        self.__module = module
        self.__cache: dict[IntrinsicKind, ir.Function] = {}

    def get(self, kind: IntrinsicKind) -> ir.Function:
        if kind in self.__cache:
            return self.__cache[kind]
        return_type, param_types, name = self.__DECLARATIONS[kind]
        func = ir.Function(self.__module, ir.FunctionType(return_type, param_types), name=name)
        # 事实性标注(不是优化手段): 这些 C 函数不会 unwind —— libc 的 malloc/free/read/write
        # 失败时返回错误值而不是抛异常, 本模块也用不到任何异常机制; 逐条按各自语义标:
        # _exit 不返回(noreturn), 其余只标 nounwind。据此 LLVM 的 FunctionAttrs 才能为
        # 调用它们的 YIAN 函数推出 nounwind(AArch64 等目标上落到无 EH 表的代码)。
        func.attributes.add("nounwind")  # type: ignore
        if kind is IntrinsicKind.ImmediateExit:
            func.attributes.add("noreturn")  # type: ignore
        self.__cache[kind] = func
        return func
