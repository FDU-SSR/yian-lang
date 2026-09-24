"""
External C function declarations.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, auto
from typing import cast

from compiler.analysis.ty.context import TypeCtx
from llvmlite import ir  # type: ignore[import-untyped]

# 胖指针块布局:每个堆块布局为「块头 + 负载」——块首 8 B 元数据
# ({extent:u32, pad:u32},块头区 [b, b+8) 在负载之前;分配锚定 data = b + 8)。
# 分配时把这一轮的身份与该负载锚写进全局锁表项(key / anchor_lo32),
# 释放把它换代并把下标压回自由链;本模块只声明这些 C 函数的签名,
# 机制常量与谓词(SENTINEL / BlockHeader / LockEntry / FrameLockArena / is_heap /
# live / is_raw)定义于 compiler/codegen/cfg/lockmech.py。


class IntrinsicKind(Enum):
    Malloc = auto()
    Realloc = auto()
    Free = auto()
    Write = auto()
    Read = auto()
    Open = auto()
    Close = auto()
    ImmediateExit = auto()
    StrLen = auto()
    MemCopy = auto()
    MemSet = auto()
    Sqrt = auto()
    Sin = auto()
    Cos = auto()
    SysRandom = auto()


def _int_type(bits: int) -> ir.Type:
    return cast(ir.Type, ir.IntType(bits))


def _pointer_type() -> ir.Type:
    return cast(ir.Type, ir.PointerType())


def _void_type() -> ir.Type:
    return cast(ir.Type, ir.VoidType())


def _double_type() -> ir.Type:
    return cast(ir.Type, ir.DoubleType())


@dataclass(frozen=True)
class IntrinsicSpec:
    return_type: ir.Type
    parameter_types: tuple[ir.Type, ...]
    name: str
    return_type_id: int | None
    parameter_attributes: tuple[tuple[int, str], ...] = ()
    function_attributes: tuple[str, ...] = ("nounwind",)
    append_volatile_flag: bool = False


class IntrinsicManager:
    __DECLARATIONS: dict[IntrinsicKind, IntrinsicSpec] = {
        IntrinsicKind.Malloc: IntrinsicSpec(_pointer_type(), (_int_type(64),), "malloc", None),
        IntrinsicKind.Realloc: IntrinsicSpec(_pointer_type(), (_pointer_type(), _int_type(64)), "realloc", None),
        IntrinsicKind.Free: IntrinsicSpec(_void_type(), (_pointer_type(),), "free", TypeCtx.void_id),
        IntrinsicKind.Write: IntrinsicSpec(_int_type(64), (_int_type(32), _pointer_type(), _int_type(64)), "write", TypeCtx.u64_id),
        IntrinsicKind.Read: IntrinsicSpec(_int_type(64), (_int_type(32), _pointer_type(), _int_type(64)), "read", TypeCtx.u64_id),
        IntrinsicKind.Open: IntrinsicSpec(_int_type(32), (_pointer_type(), _int_type(32), _int_type(32)), "open", TypeCtx.i32_id),
        IntrinsicKind.Close: IntrinsicSpec(_int_type(32), (_int_type(32),), "close", TypeCtx.i32_id),
        IntrinsicKind.ImmediateExit: IntrinsicSpec(_void_type(), (_int_type(32),), "_exit", TypeCtx.void_id, function_attributes=("nounwind", "noreturn")),
        IntrinsicKind.StrLen: IntrinsicSpec(_int_type(64), (_pointer_type(),), "strlen", TypeCtx.u64_id),
        # LLVM memory intrinsics take an immarg isvolatile flag, which is always false here.
        IntrinsicKind.MemCopy: IntrinsicSpec(
            _void_type(),
            (_pointer_type(), _pointer_type(), _int_type(64), _int_type(1)),
            "llvm.memcpy.p0.p0.i64",
            TypeCtx.void_id,
            parameter_attributes=((0, "noalias"), (1, "noalias"), (3, "immarg")),
            append_volatile_flag=True,
        ),
        IntrinsicKind.MemSet: IntrinsicSpec(
            _void_type(),
            (_pointer_type(), _int_type(8), _int_type(64), _int_type(1)),
            "llvm.memset.p0.i64",
            TypeCtx.void_id,
            parameter_attributes=((3, "immarg"),),
            append_volatile_flag=True,
        ),
        IntrinsicKind.Sqrt: IntrinsicSpec(_double_type(), (_double_type(),), "llvm.sqrt.f64", TypeCtx.f64_id),
        IntrinsicKind.Sin: IntrinsicSpec(_double_type(), (_double_type(),), "llvm.sin.f64", TypeCtx.f64_id),
        IntrinsicKind.Cos: IntrinsicSpec(_double_type(), (_double_type(),), "llvm.cos.f64", TypeCtx.f64_id),
        IntrinsicKind.SysRandom: IntrinsicSpec(_int_type(32), (), "rand", TypeCtx.u32_id),
    }

    def __init__(self, module: ir.Module) -> None:
        self.__module = module
        self.__cache: dict[IntrinsicKind, ir.Function] = {}
        self.__memset_pattern_cache: dict[tuple[ir.Type, ir.Type, ir.Type], ir.Function] = {}
        self.__memset_pattern_counter = 0

    def get(self, kind: IntrinsicKind) -> ir.Function:
        if kind in self.__cache:
            return self.__cache[kind]
        spec = self.__DECLARATIONS[kind]
        func = ir.Function(self.__module, ir.FunctionType(spec.return_type, list(spec.parameter_types)), name=spec.name)
        for index, attribute in spec.parameter_attributes:
            func.args[index].add_attribute(attribute)  # type: ignore
        for attribute in spec.function_attributes:
            func.attributes.add(attribute)  # type: ignore
        self.__cache[kind] = func
        return func

    def prepare_call_args(self, kind: IntrinsicKind, args: list[ir.Value]) -> list[ir.Value]:
        """Adapt source operands to the declared LLVM intrinsic signature."""
        spec = self.__DECLARATIONS[kind]
        if spec.append_volatile_flag:
            return [*args, ir.Constant(ir.IntType(1), 0)]  # type: ignore
        return args

    def return_type_id(self, kind: IntrinsicKind) -> int | None:
        return self.__DECLARATIONS[kind].return_type_id

    def get_memset_pattern(
        self,
        pointer_type: ir.Type,
        pattern_type: ir.Type,
        count_type: ir.Type,
    ) -> ir.Function:
        """Declare ``llvm.experimental.memset.pattern`` for a sized element type."""
        key = (pointer_type, pattern_type, count_type)
        cached = self.__memset_pattern_cache.get(key)
        if cached is not None:
            return cached

        function_type = ir.FunctionType(
            ir.VoidType(),
            [pointer_type, pattern_type, count_type, ir.IntType(1)],  # type: ignore
        )
        # llvmlite's intrinsic-name helper only supports types with an
        # ``intrinsic_name`` property; identified structs do not have one.
        # LLVM canonicalizes this unique placeholder from the declared function
        # signature when it parses the module, including aggregate overloads.
        name = f"llvm.experimental.memset.pattern.yian.{self.__memset_pattern_counter}"
        self.__memset_pattern_counter += 1
        func = ir.Function(self.__module, function_type, name=name)
        func.args[3].add_attribute("immarg")  # type: ignore
        self.__memset_pattern_cache[key] = func
        return func
