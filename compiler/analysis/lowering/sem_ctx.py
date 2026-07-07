from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Callable

from compiler.analysis.symbol.context import SymbolCtx
from compiler.analysis.ty.context import TypeCtx
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


class SemCtx:
    """Shared semantic context for a single definition (function/method).

    Responsibilities:
    - hold references to session-level resources (type_ctx)
    - hold per-def metadata (unit_id, def_type_id, ast_body, symbol_ctx, return type)
    - maintain short-lived flow state (locals, loop stack, scope depth, current span)
    - provide a minimal, safe API for helpers
    """

    def __init__(self, type_ctx: TypeCtx):
        # session
        self.__type_ctx: TypeCtx = type_ctx

        # def (initialized by begin_def)
        self.__unit_id: int | None = None
        self.__def_type_id: int | None = None
        self.__def_kind: DefKind | None = None
        self.__ast_body: AST.Block | None = None
        self.__return_type_id: int | None = None
        self.__receiver_type_id: int | None = None
        self.__is_static: bool = False
        self.__symbol_ctx: SymbolCtx | None = None

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
    def unit_id(self) -> int | None:
        return self.__unit_id

    @property
    def def_type_id(self) -> int | None:
        return self.__def_type_id

    @property
    def def_kind(self) -> DefKind | None:
        return self.__def_kind

    @property
    def ast_body(self) -> AST.Block | None:
        return self.__ast_body

    @property
    def return_type_id(self) -> int | None:
        return self.__return_type_id

    @property
    def receiver_type_id(self) -> int | None:
        return self.__receiver_type_id

    @property
    def is_static(self) -> bool:
        return self.__is_static

    @property
    def symbol_ctx(self) -> SymbolCtx | None:
        return self.__symbol_ctx

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
                  return_type_id: int, receiver_type_id: int | None, is_static: bool, symbol_ctx: SymbolCtx) -> None:
        self.__unit_id = unit_id
        self.__def_type_id = def_type_id
        self.__def_kind = def_kind
        self.__ast_body = ast_body
        self.__return_type_id = return_type_id
        self.__receiver_type_id = receiver_type_id
        self.__is_static = is_static
        self.__symbol_ctx = symbol_ctx

        # reset flow state
        self.__locals = []
        self.__loop_stack = []
        self.__scope_depth = 0
        self.__current_span = ast_body.span

    # scope management (delegates to symbol_ctx)
    def enter_scope(self) -> None:
        assert self.__symbol_ctx is not None
        self.__symbol_ctx.enter_scope()
        self.__scope_depth += 1

    def exit_scope(self) -> None:
        assert self.__symbol_ctx is not None
        self.__symbol_ctx.exit_scope()
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
        assert self.__symbol_ctx is not None
        return self.__type_ctx.resolve_type(ast_type, self.__symbol_ctx)

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
        return self.__return_type_id
