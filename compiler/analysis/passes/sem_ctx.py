from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional

from compiler.analysis.symbol.context import SymbolCtx
from compiler.analysis.ty.context import TypeCtx
from compiler.frontend.parse import ast as AST
from compiler.frontend.parse.ast_type import ASTType
from compiler.utils.IR.position import SrcSpan


@dataclass
class LoopFrame:
    span: SrcSpan
    kind: LoopKind
    break_allowed: bool = True
    continue_allowed: bool = True


class DefKind(Enum):
    Function = "function"
    Method = "method"


class LoopKind(Enum):
    For = "for"
    While = "while"
    Loop = "loop"


class SemCtx:
    """Shared semantic context for a single definition (function/method).

    Responsibilities:
    - hold references to session-level resources (type_ctx)
    - hold per-def metadata (unit_id, def_type_id, ast_body, symbol_ctx, return type)
    - maintain short-lived flow state (locals, loop stack, scope depth, current span)
    - provide a minimal, safe API for helpers
    """

    def __init__(self, type_ctx: TypeCtx, diagnostics: object | None = None):
        # session
        self.__type_ctx: TypeCtx = type_ctx
        self.__diagnostics = diagnostics

        # def (initialized by begin_def)
        self.__unit_id: Optional[int] = None
        self.__def_type_id: Optional[int] = None
        self.__def_kind: Optional[DefKind] = None
        self.__ast_body: Optional[AST.Block] = None
        self.__return_type_id: Optional[int] = None
        self.__receiver_type_id: Optional[int] = None
        self.__is_static: bool = False
        self.__symbol_ctx: Optional[SymbolCtx] = None

        # flow
        self.__locals: list[int] = []
        self.__loop_stack: list[LoopFrame] = []
        self.__scope_depth: int = 0
        self.__current_span: SrcSpan = SrcSpan.empty()

    @property
    def type_ctx(self) -> TypeCtx:
        return self.__type_ctx

    @property
    def diagnostics(self) -> object | None:
        return self.__diagnostics

    @property
    def unit_id(self) -> Optional[int]:
        return self.__unit_id

    @property
    def def_type_id(self) -> Optional[int]:
        return self.__def_type_id

    @property
    def def_kind(self) -> Optional[DefKind]:
        return self.__def_kind

    @property
    def ast_body(self) -> Optional[AST.Block]:
        return self.__ast_body

    @property
    def return_type_id(self) -> Optional[int]:
        return self.__return_type_id

    @property
    def receiver_type_id(self) -> Optional[int]:
        return self.__receiver_type_id

    @property
    def is_static(self) -> bool:
        return self.__is_static

    @property
    def symbol_ctx(self) -> Optional[SymbolCtx]:
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
                  return_type_id: int, receiver_type_id: Optional[int], is_static: bool, symbol_ctx: SymbolCtx) -> None:
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

    def current_return_type(self) -> Optional[int]:
        return self.__return_type_id
