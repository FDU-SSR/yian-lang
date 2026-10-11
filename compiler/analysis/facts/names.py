"""Resolved source names, independent of the type-space lifecycle."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TypeAlias
from compiler.analysis.symbol.context import SymbolCtx

from compiler.analysis.symbol.symbol import AliasSymbol, Symbol
from compiler.analysis.ty import ty as Type
from compiler.frontend.lex.position import SrcSpan

NameTarget: TypeAlias = Symbol | AliasSymbol | Type.StructField | Type.EnumVariant | int


@dataclass(frozen=True)
class NameRef:
    span: SrcSpan
    target: NameTarget
    expression_type: int | None = None
    context: int | None = None


@dataclass(frozen=True)
class ExpressionFact:
    span: SrcSpan
    type_id: int
    is_place: bool
    is_static: bool
    context: int | None = None


@dataclass(frozen=True)
class ScopeFact:
    span: SrcSpan
    symbols: SymbolCtx
    context: int | None


class NameReferences:
    """Retain instance facts and expose only consistent current-source projections."""

    def __init__(self) -> None:
        self.__items: list[NameRef] = []
        self.__keys: dict[tuple[str, int, int], list[NameRef]] = {}
        self.definition_context: int | None = None
        self.expressions: dict[tuple[str, int, int, int, int, int | None], ExpressionFact | None] = {}
        self.scopes: list[ScopeFact] = []
        self.expression_conflicts: tuple[SrcSpan, ...] = ()
        self.__expression_spans: dict[tuple[str, int, int, int, int], SrcSpan] = {}

    def record_scope(self, span: SrcSpan, symbols: SymbolCtx) -> None:
        self.scopes.append(ScopeFact(span, symbols.clone(), self.definition_context))

    def record_expression(self, span: SrcSpan, type_id: int, is_place: bool, is_static: bool) -> None:
        if span.is_synthetic():
            return
        key = (str(span.path), span.start.row, span.start.col, span.end.row, span.end.col, self.definition_context)
        self.__expression_spans[key[:5]] = span
        value = ExpressionFact(span, type_id, is_place, is_static, self.definition_context)
        if key in self.expressions and self.expressions[key] != value:
            self.expressions[key] = None
        else:
            self.expressions[key] = value

    def record(self, span: SrcSpan, target: NameTarget, expression_type: int | None = None, *, synthetic: bool = False) -> None:
        if synthetic or span.is_synthetic():
            return
        key = (str(span.path), span.start.row, span.start.col)
        fact = NameRef(span, target, expression_type, self.definition_context)
        variants = self.__keys.setdefault(key, [])
        if fact not in variants:
            variants.append(fact)
            self.__items.append(fact)

    def expression_entries(self) -> tuple[ExpressionFact, ...]:
        groups: dict[tuple[str, int, int, int, int], list[ExpressionFact | None]] = {}
        for key, fact in self.expressions.items():
            groups.setdefault(key[:5], []).append(fact)
        result: list[ExpressionFact] = []
        conflicts: list[SrcSpan] = []
        for key, variants in groups.items():
            first = variants[0]
            if first is not None and all(value is not None and (value.type_id, value.is_place, value.is_static) ==
                                         (first.type_id, first.is_place, first.is_static) for value in variants):
                result.append(first)
            else:
                conflicts.append(self.__expression_spans[key])
        self.expression_conflicts = tuple(conflicts)
        return tuple(result)

    def entries(self) -> tuple[NameRef, ...]:
        result: list[NameRef] = []
        for variants in self.__keys.values():
            first = variants[0]
            if all(value.target == first.target and value.expression_type == first.expression_type for value in variants):
                result.append(first)
        return tuple(result)

    def contextual_entries(self) -> tuple[NameRef, ...]:
        return tuple(self.__items)

    def has_conflicts(self) -> bool:
        return len(self.entries()) != len(self.__keys)
