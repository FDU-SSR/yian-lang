"""Shared resources for one semantic-analysis run."""

from __future__ import annotations

from pathlib import Path
from collections.abc import Callable

from compiler.analysis.const_eval import ConstantExpressionEvaluator, ConstantValue
from compiler.analysis.error import AnalysisError, ComptimeConditionError
from compiler.analysis.facts.names import NameReferences
from compiler.analysis.package_map import PackageMap
from compiler.analysis.resolution.types import TypeResolver
from compiler.analysis.resolution.aliases import AliasRegistry
from compiler.analysis.symbol.context import SymbolCtx
from compiler.analysis.ty.context import TypeCtx
from compiler.analysis.unit.procedures import ProcedureRegistry
from compiler.analysis.unit.unit_data import UnitData
from compiler.analysis.symbol.symbol import AliasSymbol, Symbol
from compiler.analysis.ty import ty as Type
from compiler.analysis.unit import hir as HIR
from compiler.frontend.parse.ast_type import ASTType, ConstExpr
from compiler.frontend.parse import ast as AST
from compiler.frontend.lex.position import SrcSpan


class SemanticState:
    def __init__(
        self,
        type_ctx: TypeCtx,
        raw_pointers: bool,
        unit_datas: dict[int, UnitData],
        packages: PackageMap | None = None,
        stdlib_root: Path | None = None,
        type_size: Callable[[int], int] | None = None,
    ) -> None:
        self.type_ctx = type_ctx
        self.raw_pointers = raw_pointers
        self.unit_datas = unit_datas
        self.packages = packages
        self.stdlib_root = stdlib_root
        self.__type_size = type_size
        self.__constant_defs: dict[tuple[int, int], AST.ConstDef] = {}
        self.__constant_values: dict[tuple[int, int], tuple[ConstantValue, int]] = {}
        self.__constant_stack: list[tuple[int, int]] = []
        self.procedures = ProcedureRegistry(type_ctx)
        self.names = NameReferences()
        self.aliases = AliasRegistry(type_ctx, self.resolve_type_in)
        self.__type_resolver = TypeResolver(type_ctx, self.names, self.constant_value, self.aliases)

    def resolve_type_in(self, ast_type: ASTType, symbol_ctx: SymbolCtx) -> int:
        return self.__type_resolver.resolve(ast_type, symbol_ctx)

    def resolve_const_expr(self, const_expr: ConstExpr, symbol_ctx: SymbolCtx) -> int:
        return self.__type_resolver.resolve_const_expr(const_expr, symbol_ctx)

    def resolve_type_symbol(self, symbol: Symbol | AliasSymbol, arguments: list[int] | None = None,
                            span: SrcSpan | None = None) -> int:
        return self.__type_resolver.resolve_symbol(symbol, arguments, span)

    def register_constant(self, unit_id: int, symbol_id: int, definition: AST.ConstDef) -> None:
        self.__constant_defs[(unit_id, symbol_id)] = definition

    def constant_value(self, symbol: Symbol) -> tuple[ConstantValue, int]:
        """Resolve an imported or local constant to its defining value."""
        origin = symbol.const_origin
        if origin is None:
            raise AnalysisError(
                f"Constant '{symbol.name}' has no compile-time definition",
                symbol.span or SrcSpan.empty(),
            )
        return self.__evaluate_constant(origin)

    def evaluate_constants(self) -> None:
        """Evaluate each declared constant, including declarations not referenced elsewhere."""
        for origin in self.__constant_defs:
            self.__evaluate_constant(origin)

    def evaluate_comptime_condition(self, expr: HIR.Expr) -> bool:
        """Evaluate one typed condition before checking either branch body."""
        if self.__type_size is None:
            raise ComptimeConditionError("comptime if condition evaluation failed: target layout unavailable", expr.span)
        evaluator = ConstantExpressionEvaluator(
            self.type_ctx, self.__type_size, "comptime if condition"
        )
        try:
            value, _ = evaluator.evaluate(expr)
        except AnalysisError as error:
            raise ComptimeConditionError(str(error), error.span) from error
        if type(value) is not bool:
            raise ComptimeConditionError(
                "comptime if condition is not compile-time evaluable: condition is not bool", expr.span
            )
        return value

    def __evaluate_constant(self, origin: tuple[int, int]) -> tuple[ConstantValue, int]:
        cached = self.__constant_values.get(origin)
        if cached is not None:
            return cached

        definition = self.__constant_defs.get(origin)
        if definition is None:
            raise AnalysisError("Unknown compile-time constant", SrcSpan.empty())
        if origin in self.__constant_stack:
            cycle = self.__constant_stack[self.__constant_stack.index(origin):] + [origin]
            names = [self.__constant_defs[item].name.name for item in cycle]
            raise AnalysisError(
                f"Circular constant dependency: {' -> '.join(names)}",
                definition.name.span,
            )

        self.__constant_stack.append(origin)
        try:
            unit_id, symbol_id = origin
            unit = self.unit_datas[unit_id]
            symbol = unit.symbol_ctx.get(symbol_id)
            if isinstance(symbol, AliasSymbol):
                raise AnalysisError("constant declaration resolved to an alias", definition.name.span)
            type_id = self.resolve_type_in(definition.const_type, unit.symbol_ctx)
            symbol.type_id = type_id
            for candidate_unit in self.unit_datas.values():
                for _candidate_id, candidate in candidate_unit.symbol_ctx.items():
                    if isinstance(candidate, Symbol) and candidate.const_origin == origin:
                        candidate.type_id = type_id
            resolved_type = self.type_ctx[type_id]
            if not isinstance(
                resolved_type,
                (Type.IntType, Type.FloatType, Type.BoolType, Type.CharType, Type.StrType),
            ):
                raise AnalysisError(
                    "constant declarations require a scalar type",
                    definition.const_type.span,
                )

            # A temporary definition context lets constant initializers use the
            # ordinary expression checker without creating a runtime function.
            from compiler.analysis.lowering.expr_checker import ExprChecker
            from compiler.analysis.lowering.state import DefKind, DefinitionState

            state = DefinitionState(self)
            state.begin_def(
                unit_id=unit_id,
                def_type_id=type_id,
                def_kind=DefKind.Function,
                ast_body=AST.Block(span=definition.value.span, stmts=[]),
                return_type_id=type_id,
                receiver_type_id=None,
                is_static=False,
                symbol_ctx=unit.symbol_ctx.clone(),
            )
            state.set_def_reporter(lambda _type_id: None)
            checker = ExprChecker(state)
            initializer = checker.coerce(checker.value(definition.value), type_id)
            if self.__type_size is None:
                raise AnalysisError("constant evaluation has no target layout", definition.value.span)
            evaluator = ConstantExpressionEvaluator(
                self.type_ctx,
                self.__type_size,
                "constant initializer",
            )
            value, _ = evaluator.evaluate(initializer)
            result = (value, type_id)
            self.__constant_values[origin] = result
            return result
        finally:
            self.__constant_stack.pop()
