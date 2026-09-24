"""CFG 共享上下文：session 资源 + 本模块函数表 + 每函数的事实。

形态对齐中端 `analysis/lowering/sem_ctx.py`：**一个** ctx 对象由管线持有并传给各段，
而不是每个函数一份。构造时给 session 级资源（`type_ctx` / `raw_pointers`）；每个函数
开始下降时由 `passes/translator.py` 的 `_CfgBuilder` 调 `begin_def()`，它把该函数的事实
装成不可变的 `FuncFacts` 存进表里并返回；下降完成后 `declare_function()` 把函数登记进
函数表。`passes/` 的三段——`CfgTranslator`（下降）、`InsertChecks`、`Cleanup`——与下降
机器（`lower/` 各簇、`PtrPredicates`）读的都是这一个对象。

因此任一 pass 都能：遍历 `ctx.functions` 看到本模块全部函数、用 `ctx.facts(type_id)`
取某函数的事实；不需要"每函数一份 ctx"，也不需要把上下文列表传来传去。
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from compiler.analysis.symbol.context import SymbolCtx
from compiler.analysis.ty.context import TypeCtx
from compiler.codegen.cfg import ir as IR
from compiler.codegen.cfg.lower.type_mapper import CfgTypeMapper
from compiler.frontend.lex.position import SrcSpan


@dataclass(frozen=True)
class FuncFacts:
    """一个函数在下降期确定、后两段 pass 仍要用的事实（构造即完整，无未初始化态）。"""

    type_id: int
    symbol_ctx: SymbolCtx
    func_name: str
    span: SrcSpan
    return_type: int
    new_void_value: Callable[[], IR.Value]
    """构造 void 型占位寄存器（命名计数器由下降期的发射句柄持有）。"""


class CfgCtx:
    """本模块的共享上下文：三段 pass + 下降机器读同一份。"""

    def __init__(self, type_ctx: TypeCtx, raw_pointers: bool) -> None:
        self.__type_ctx = type_ctx
        self.__raw_pointers = raw_pointers
        self.__type_mapper = CfgTypeMapper(type_ctx)
        # 本模块已降好的函数（type_id → Function，插入序即下降序）
        self.__functions: dict[int, IR.Function] = {}
        # 每函数的事实（type_id → FuncFacts）
        self.__facts: dict[int, FuncFacts] = {}

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

    # ------------------------------------------------------------------
    # 函数表
    # ------------------------------------------------------------------

    @property
    def functions(self) -> dict[int, IR.Function]:
        """本模块的全部 CFG 函数（`type_id` → `Function`，按下降顺序）。"""
        return self.__functions

    def declare_function(self, func: IR.Function) -> None:
        """登记一个下降完成的函数（由 `CfgTranslator` 在 `_CfgBuilder.build()` 之后调用）。"""
        self.__functions[func.type_id] = func

    # ------------------------------------------------------------------
    # 每函数事实
    # ------------------------------------------------------------------

    def begin_def(
        self,
        *,
        type_id: int,
        symbol_ctx: SymbolCtx,
        func_name: str,
        span: SrcSpan,
        return_type: int,
        new_void_value: Callable[[], IR.Value],
    ) -> FuncFacts:
        """开始一个函数：装好它的事实存表并返回（session 级字段不受影响）。"""
        facts = FuncFacts(
            type_id=type_id,
            symbol_ctx=symbol_ctx,
            func_name=func_name,
            span=span,
            return_type=return_type,
            new_void_value=new_void_value,
        )
        self.__facts[type_id] = facts
        return facts

    def facts(self, type_id: int) -> FuncFacts:
        """取某函数的事实。"""
        return self.__facts[type_id]
