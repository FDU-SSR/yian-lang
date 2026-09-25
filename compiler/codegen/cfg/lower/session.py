"""Type mapping and configuration used only while lowering HIR to CFG."""
from __future__ import annotations

from compiler.analysis.ty.context import TypeCtx
from compiler.codegen.cfg import ir as IR
from compiler.codegen.cfg.lower.type_mapper import CfgTypeMapper


class LoweringSession:
    """Type mapping and compiler mode shared by the functions being lowered."""

    def __init__(self, type_ctx: TypeCtx, raw_pointers: bool) -> None:
        self.__type_ctx = type_ctx
        self.__raw_pointers = raw_pointers
        self.__type_mapper = CfgTypeMapper(type_ctx)

    # ------------------------------------------------------------------
    # session 级
    # ------------------------------------------------------------------

    @property
    def type_ctx(self) -> TypeCtx:
        return self.__type_ctx

    @property
    def raw_pointers(self) -> bool:
        """诊断模式开关：开启时指针一律按裸 8B 处理，不生成检查、锁槽或帧锁。"""
        return self.__raw_pointers

    def cfg_type_id(self, type_id: int) -> int:
        """Return the CFG-visible representation of a semantic type."""
        return self.__type_mapper.lower(type_id)

    def cfg_function_type_id(self, type_id: int) -> int:
        """Map a DefPoint type, using a closure's generated call method."""
        return self.__type_mapper.function_type(type_id)

    def normalize_function(self, function: IR.Function) -> None:
        """Map all type references in a completed function before CFG passes."""
        self.__type_mapper.normalize_function(function)
