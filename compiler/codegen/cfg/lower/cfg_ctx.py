"""单个 CFG 函数的共享上下文：三段 pass 与下降机器读同一份事实。

形态对齐中端 `analysis/lowering/sem_ctx.py`：构造时只给 session 级资源
（`type_ctx` / `raw_pointers` / 本模块的**函数表**）；每个函数开始下降时由
`passes/translator.py` 的 `_CfgBuilder` 调 `begin_def()` 写入该函数的事实（type_id、
符号表、函数名、源跨度、返回类型、void 占位通道）。此后 `passes/` 的三段——
`CfgTranslator`（下降）、`InsertChecks`、`Cleanup`——与下降机器（`lower/` 各簇、
`PtrPredicates`）读的都是同一个对象，不再各自持有副本。

函数表由 `spawn()` 出的各 ctx **共享同一个 dict**：因此任一 pass 手里的 ctx 都能看到
本模块已经降好的全部函数（`ctx.functions`），也可以拿到自己那一条（`ctx.function`）。
"""
from __future__ import annotations

from collections.abc import Callable

from compiler.analysis.symbol.context import SymbolCtx
from compiler.analysis.ty.context import TypeCtx
from compiler.codegen.cfg import ir as IR
from compiler.frontend.lex.position import SrcSpan


class CfgCtx:
    """一个 CFG 函数的共享事实（三段 pass + 下降机器）。"""

    def __init__(self, type_ctx: TypeCtx, raw_pointers: bool) -> None:
        # ── session 级（整个编译共享，构造即定）──
        self.__type_ctx = type_ctx
        self.__raw_pointers = raw_pointers
        # 本模块已降好的函数（type_id → Function）；spawn 出的 ctx 共享同一个 dict
        self.__functions: dict[int, IR.Function] = {}
        # ── 函数级（begin_def 写入）──
        self.__type_id: int | None = None
        self.__symbol_ctx: SymbolCtx | None = None
        self.__func_name: str | None = None
        self.__span: SrcSpan | None = None
        self.__return_type: int | None = None
        self.__new_void_value: Callable[[], IR.Value] | None = None

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

    @property
    def functions(self) -> dict[int, IR.Function]:
        """本模块已降好的全部函数（`type_id` → `Function`）——各 ctx 共享同一份。"""
        return self.__functions

    def declare_function(self, func: IR.Function) -> None:
        """登记一个下降完成的函数（由 `CfgTranslator` 在 `_CfgBuilder.build()` 之后调用）。"""
        self.__functions[func.type_id] = func

    def spawn(self) -> "CfgCtx":
        """派生一个共享同一 session 资源（含函数表）的新 ctx——每个函数一份。"""
        child = CfgCtx(self.__type_ctx, self.__raw_pointers)
        child.__functions = self.__functions
        return child

    # ------------------------------------------------------------------
    # 函数级
    # ------------------------------------------------------------------

    @property
    def type_id(self) -> int:
        assert self.__type_id is not None, "CfgCtx used before begin_def()"
        return self.__type_id

    @property
    def function(self) -> IR.Function:
        """本 ctx 对应的函数（下降完成后才可用）。"""
        assert self.__type_id is not None, "CfgCtx used before begin_def()"
        assert self.__type_id in self.__functions, "CfgCtx.function accessed before declare_function()"
        return self.__functions[self.__type_id]

    @property
    def symbol_ctx(self) -> SymbolCtx:
        assert self.__symbol_ctx is not None, "CfgCtx used before begin_def()"
        return self.__symbol_ctx

    @property
    def func_name(self) -> str:
        assert self.__func_name is not None, "CfgCtx used before begin_def()"
        return self.__func_name

    @property
    def span(self) -> SrcSpan:
        assert self.__span is not None, "CfgCtx used before begin_def()"
        return self.__span

    @property
    def return_type(self) -> int:
        assert self.__return_type is not None, "CfgCtx used before begin_def()"
        return self.__return_type

    @property
    def new_void_value(self) -> Callable[[], IR.Value]:
        """构造 void 型占位寄存器（命名计数器由下降期的发射句柄持有）。"""
        assert self.__new_void_value is not None, "CfgCtx used before begin_def()"
        return self.__new_void_value

    def begin_def(
        self,
        *,
        type_id: int,
        symbol_ctx: SymbolCtx,
        func_name: str,
        span: SrcSpan,
        return_type: int,
        new_void_value: Callable[[], IR.Value],
    ) -> None:
        """开始一个函数：写入函数级事实（session 级字段与函数表保持不变）。"""
        self.__type_id = type_id
        self.__symbol_ctx = symbol_ctx
        self.__func_name = func_name
        self.__span = span
        self.__return_type = return_type
        self.__new_void_value = new_void_value
