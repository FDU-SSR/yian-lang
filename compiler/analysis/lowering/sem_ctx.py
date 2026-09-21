"""中端共享上下文：session 资源 + definition 产物表 + 每 def 事实 + 当前 def 的遍历状态。

一个实例由 `main`（或分析会话）创建后贯穿中端各段：`GlobalResolve` → `TypeCheck` →
`ComptimeIfSpecializer` → `ClosureLowering` → `DefiniteAssignment` → CFG 下降；各段读 session
资源（`type_ctx` / `raw_pointers` / `unit_datas` / `packages` / `stdlib_root`），并就地改写同一个
产物表（`def_points`）。type check 期间每次 `begin_def()` 装一份不可变 `DefFacts` 存表并设为
current；遍历期的可变状态（locals / 循环栈 / 作用域深度 / span）留在 ctx 上。

形态对齐 CFG 侧的 `cfg/lower/cfg_ctx.py::CfgCtx`；放在 `lowering/` 与机器的其他模块同处
（就像 `CfgCtx` 放在 `lower/`）。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Callable

from pathlib import Path

from compiler.analysis.package_map import PackageMap
from compiler.analysis.symbol.context import SymbolCtx
from compiler.analysis.ty.context import TypeCtx
from compiler.analysis.unit.def_point import DefPoint
from compiler.analysis.unit.unit_data import UnitData
from compiler.error import CompilerError
from compiler.frontend.lex.position import SrcSpan
from compiler.frontend.parse import ast as AST
from compiler.frontend.parse.ast_type import ASTType


@dataclass
class LoopFrame:
    span: SrcSpan
    break_value_type_ids: list[int] = field(default_factory=list[int])


class DefKind(Enum):
    Function = "function"
    Method = "method"
    Closure = "closure"


@dataclass(frozen=True)
class DefFacts:
    """一个 definition 在 type check 期间确定、helpers 要用的事实。

    构造即完整（`begin_def` 装好存表并返回），因此没有"未初始化"的中间态；
    遍历期的可变状态（locals / 循环栈 / 作用域深度 / 当前 span）不在里面。
    """

    def_type_id: int
    unit_id: int
    def_kind: DefKind
    ast_body: AST.Block
    return_type_id: int
    receiver_type_id: int | None
    is_static: bool
    symbol_ctx: SymbolCtx


class SemCtx:
    """中端各段共享的语义上下文（形态对齐 CFG 侧的 `CfgCtx`）。

    中端各段（`GlobalResolve` → `TypeCheck` → `ComptimeIfSpecializer` →
    `ClosureLowering` → `DefiniteAssignment` → CFG 下降）拿的都是这一个对象：

    - session 级资源：`type_ctx` / `raw_pointers` / `unit_datas` / `packages` / `stdlib_root`；
    - 产物表 `def_points`（codegen 集，type check 之后由 `main` 交进来，之后各段就地改写）；
    - 每 def 事实表 `facts` 与当前 def 的遍历期状态（locals / 循环栈 / 作用域深度 / span）。
    """

    def __init__(
        self,
        type_ctx: TypeCtx,
        raw_pointers: bool,
        unit_datas: dict[int, UnitData],
        packages: PackageMap | None = None,
        stdlib_root: Path | None = None,
    ) -> None:
        # session
        self.__type_ctx: TypeCtx = type_ctx
        self.__raw_pointers: bool = raw_pointers
        self.__unit_datas: dict[int, UnitData] = unit_datas
        self.__packages: PackageMap | None = packages
        self.__stdlib_root: Path | None = stdlib_root

        # 产物表（codegen 集）：type check 结束后由 main 交出，之后各段共享同一个 dict
        self.__def_points: dict[int, DefPoint] = {}

        # def facts（begin_def 写入；current = 正在遍历的那一个）
        self.__facts: dict[int, DefFacts] = {}
        self.__current: DefFacts | None = None

        # flow
        self.__locals: list[int] = []
        self.__loop_stack: list[LoopFrame] = []
        self.__scope_depth: int = 0
        self.__current_span: SrcSpan = SrcSpan.empty()
        # callback to report discovered reachable definition type ids
        self.__def_reporter: Callable[[int], None] | None = None

    @property
    def type_ctx(self) -> TypeCtx:
        return self.__type_ctx

    @property
    def raw_pointers(self) -> bool:
        """诊断模式开关：指针一律按裸 8B 处理。"""
        return self.__raw_pointers

    @property
    def unit_datas(self) -> dict[int, UnitData]:
        return self.__unit_datas

    @property
    def packages(self) -> PackageMap | None:
        return self.__packages

    @property
    def stdlib_root(self) -> Path | None:
        return self.__stdlib_root

    @property
    def def_points(self) -> dict[int, DefPoint]:
        """本次编译的 definition 产物表（`type_id` → `DefPoint`），各段就地改写。"""
        return self.__def_points

    def declare_def_points(self, def_points: dict[int, DefPoint]) -> None:
        """交出产物表（由 `main` 在 type check 之后调用；**按引用持有**，各段改的就是它）。"""
        self.__def_points = def_points

    @property
    def unit_id(self) -> int:
        if self.__current is None:
            raise CompilerError("SemCtx.unit_id accessed before begin_def()")
        return self.__current.unit_id

    @property
    def def_type_id(self) -> int | None:
        return self.__current.def_type_id if self.__current is not None else None

    @property
    def def_kind(self) -> DefKind | None:
        return self.__current.def_kind if self.__current is not None else None

    @property
    def ast_body(self) -> AST.Block | None:
        return self.__current.ast_body if self.__current is not None else None

    @property
    def return_type_id(self) -> int | None:
        return self.__current.return_type_id if self.__current is not None else None

    @property
    def receiver_type_id(self) -> int | None:
        return self.__current.receiver_type_id if self.__current is not None else None

    @property
    def is_static(self) -> bool:
        return self.__current.is_static if self.__current is not None else False

    @property
    def symbol_ctx(self) -> SymbolCtx | None:
        return self.__current.symbol_ctx if self.__current is not None else None

    @property
    def current(self) -> DefFacts | None:
        """正在遍历的 definition 的事实（`begin_def` 之前为 None）。"""
        return self.__current

    def facts(self, def_type_id: int) -> DefFacts:
        """取某个 definition 的事实。"""
        return self.__facts[def_type_id]

    @property
    def locals(self) -> list[int]:
        return self.__locals

    @property
    def loop_stack(self) -> list[LoopFrame]:
        return self.__loop_stack

    @property
    def scope_depth(self) -> int:
        return self.__scope_depth

    @property
    def current_span(self) -> SrcSpan:
        return self.__current_span

    # lifecycle
    def begin_def(self, *, unit_id: int, def_type_id: int, def_kind: DefKind, ast_body: AST.Block,
                  return_type_id: int, receiver_type_id: int | None, is_static: bool, symbol_ctx: SymbolCtx) -> DefFacts:
        """开始一个 definition：装好它的事实存表并设为 current，重置遍历期状态。"""
        facts = DefFacts(
            def_type_id=def_type_id,
            unit_id=unit_id,
            def_kind=def_kind,
            ast_body=ast_body,
            return_type_id=return_type_id,
            receiver_type_id=receiver_type_id,
            is_static=is_static,
            symbol_ctx=symbol_ctx,
        )
        self.__facts[def_type_id] = facts
        self.__current = facts

        # reset flow state
        self.__locals = []
        self.__loop_stack = []
        self.__scope_depth = 0
        self.__current_span = ast_body.span
        return facts

    # scope management (delegates to symbol_ctx)
    def enter_scope(self) -> None:
        assert self.__current is not None
        self.__current.symbol_ctx.enter_scope()
        self.__scope_depth += 1

    def exit_scope(self) -> None:
        assert self.__current is not None
        self.__current.symbol_ctx.exit_scope()
        self.__scope_depth -= 1

    # locals
    def push_local(self, symbol_id: int) -> None:
        self.__locals.append(symbol_id)

    # loop stack
    def push_loop(self, frame: LoopFrame) -> None:
        self.__loop_stack.append(frame)

    def pop_loop(self) -> LoopFrame:
        return self.__loop_stack.pop()

    # helpers
    def resolve_type(self, ast_type: ASTType) -> int:
        # delegate to TypeCtx; many call sites pass symbol_ctx for resolution
        assert self.__current is not None
        return self.__type_ctx.resolve_type(ast_type, self.__current.symbol_ctx)

    # ----------------- reachable def reporting API -----------------
    def set_def_reporter(self, reporter: Callable[[int], None]) -> None:
        """Set a callback that will be called with a concrete `type_id` whenever
        an expression resolver discovers a reachable function/method definition.

        The callback should be fast and idempotent; it is allowed to be called
        multiple times for the same `type_id`.
        """
        self.__def_reporter = reporter

    def report_def(self, type_id: int) -> None:
        """Report a reachable definition `type_id` to the registered reporter."""
        if self.__def_reporter is None:
            raise CompilerError("No def reporter registered in SemCtx")
        self.__def_reporter(type_id)

    def current_return_type(self) -> int | None:
        return self.__current.return_type_id if self.__current is not None else None
