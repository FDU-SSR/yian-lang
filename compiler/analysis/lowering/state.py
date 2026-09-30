"""Mutable state for checking one definition at a time."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Callable

from compiler.analysis.error import AnalysisError
from compiler.analysis.facts.names import NameReferences
from compiler.analysis.package_map import PackageMap
from compiler.analysis.state import SemanticState
from compiler.analysis.symbol.context import SymbolCtx
from compiler.analysis.symbol.symbol import AliasSymbol, Symbol, SymbolKind
from compiler.analysis.ty import ty as Type
from compiler.analysis.unit.procedures import ProcedureRegistry
from compiler.analysis.unit.unit_data import UnitData
from compiler.analysis.ty.context import TypeCtx
from compiler.analysis.unit import hir as HIR
from compiler.error import CompilerError
from compiler.frontend.lex.position import SrcSpan
from compiler.frontend.parse import ast as AST
from compiler.frontend.parse.ast_type import ASTType, ConstExpr
from compiler.frontend.parse import ast_type as ASTTy


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


class DefinitionState:
    """Current definition facts and traversal state owned by type checking."""

    def __init__(self, semantic: SemanticState) -> None:
        self.__semantic = semantic

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
        return self.__semantic.type_ctx

    @property
    def raw_pointers(self) -> bool:
        return self.__semantic.raw_pointers

    @property
    def ffi_allowed(self) -> bool:
        if self.__current is None:
            return False
        ty = self.type_ctx[self.__current.def_type_id]
        if isinstance(ty, Type.FunctionType):
            return ty.custom_def.is_ffi
        if isinstance(ty, Type.MethodType):
            return ty.custom_def.is_ffi
        return False

    @property
    def unit_datas(self) -> dict[int, UnitData]:
        return self.__semantic.unit_datas

    @property
    def packages(self) -> PackageMap | None:
        return self.__semantic.packages

    @property
    def procedures(self) -> ProcedureRegistry:
        return self.__semantic.procedures

    @property
    def names(self) -> NameReferences:
        return self.__semantic.names

    def resolve_type_in(self, ast_type: ASTType, symbol_ctx: SymbolCtx) -> int:
        return self.__semantic.resolve_type_in(ast_type, symbol_ctx)

    def resolve_const_expr(self, const_expr: ConstExpr, symbol_ctx: SymbolCtx) -> int:
        return self.__semantic.resolve_const_expr(const_expr, symbol_ctx)

    def constant_value(self, symbol: Symbol) -> tuple[int | bool | float | str, int]:
        return self.__semantic.constant_value(symbol)

    def evaluate_comptime_condition(self, expr: HIR.Expr) -> bool:
        return self.__semantic.evaluate_comptime_condition(expr)

    @property
    def unit_id(self) -> int:
        if self.__current is None:
            raise CompilerError("DefinitionState.unit_id accessed before begin_def()")
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

    def declare_local(self, name: AST.Identifier, type_id: int) -> int:
        symbol_ctx = self.symbol_ctx
        if symbol_ctx is None:
            raise CompilerError("Cannot declare a local outside a definition")
        symbol_id = symbol_ctx.add_symbol(name.name, SymbolKind.Variable, type_id, span=name.span)
        if symbol_id is None:
            raise AnalysisError(f"Variable '{name.name}' is already defined in the current scope", name.span)
        self.push_local(symbol_id)
        return symbol_id

    # loop stack
    def push_loop(self, frame: LoopFrame) -> None:
        self.__loop_stack.append(frame)

    def pop_loop(self) -> LoopFrame:
        return self.__loop_stack.pop()

    # helpers
    def resolve_type(self, ast_type: ASTType) -> int:
        """Resolve a type in the current definition and validate object types."""
        assert self.__current is not None
        type_id = self.resolve_type_in(ast_type, self.__current.symbol_ctx)
        resolved_ty = self.type_ctx[type_id]
        if not self.ffi_allowed and self.__uses_c_type_syntax(ast_type):
            raise AnalysisError("C ABI types require an ffi fn", ast_type.span)
        if isinstance(resolved_ty, Type.TraitObjectType):
            self.type_ctx.check_trait_object_safe(resolved_ty.trait_type_id, ast_type.span)
        return type_id

    def resolve_type_symbol(self, symbol: Symbol | AliasSymbol, arguments: list[int] | None = None,
                            span: SrcSpan | None = None) -> int:
        type_id = self.__semantic.resolve_type_symbol(symbol, arguments, span)
        if not self.ffi_allowed and isinstance(symbol, AliasSymbol) \
                and self.type_ctx.contains_ffi_type(self.__semantic.resolve_type_symbol(symbol)):
            raise AnalysisError("C ABI types require an ffi fn", span or symbol.span or SrcSpan.empty())
        return type_id

    def __uses_c_type_syntax(self, ast_type: ASTType) -> bool:
        """C types explicitly named in a definition require FFI privileges.

        Concrete generic bindings retain their definition's permissions; a
        parameter instantiated with a C scalar is not itself C type syntax.
        """
        match ast_type:
            case ASTTy.CScalarType() | ASTTy.CPtrType():
                return True
            case ASTTy.PointerType(pointee_type=inner) | ASTTy.RefType(pointee_type=inner) \
                    | ASTTy.SliceType(element_type=inner) | ASTTy.ArrayType(element_type=inner):
                return self.__uses_c_type_syntax(inner)
            case ASTTy.TupleType(element_types=elements):
                return any(self.__uses_c_type_syntax(item) for item in elements)
            case ASTTy.FunctionType(param_types=parameters, return_type=result):
                return any(self.__uses_c_type_syntax(item) for item in parameters) \
                    or self.__uses_c_type_syntax(result)
            case ASTTy.NamedType(name=name):
                assert self.__current is not None
                symbol = self.__current.symbol_ctx.lookup(name.name)
                return isinstance(symbol, AliasSymbol) and self.type_ctx.contains_ffi_type(
                    self.__semantic.resolve_type_symbol(symbol)
                )
            case ASTTy.InstanceType(base=base, generic_args=arguments):
                return self.__uses_c_type_syntax(base) or any(
                    self.__uses_c_type_syntax(item) for item in arguments
                    if not isinstance(item, (
                        ASTTy.LiteralConstExpr, ASTTy.GenericConstExpr,
                        ASTTy.UnaryConstExpr, ASTTy.BinaryConstExpr,
                    ))
                )
            case _:
                return False

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
            raise CompilerError("No def reporter registered in DefinitionState")
        self.__def_reporter(type_id)

    def current_return_type(self) -> int | None:
        return self.__current.return_type_id if self.__current is not None else None
