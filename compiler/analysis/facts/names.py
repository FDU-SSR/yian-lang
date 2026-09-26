"""Resolved source names, independent of the type-space lifecycle."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TypeAlias

from compiler.analysis.symbol.symbol import Symbol
from compiler.analysis.ty import ty as Type
from compiler.frontend.lex.position import SrcSpan

NameTarget: TypeAlias = Symbol | Type.StructField | Type.EnumVariant | int


@dataclass(frozen=True)
class NameRef:
    span: SrcSpan
    target: NameTarget
    expression_type: int | None = None


class NameReferences:
    """Keep the first resolution for each real source position."""

    def __init__(self) -> None:
        self.__items: list[NameRef] = []
        self.__keys: set[tuple[str, int, int]] = set()

    def record(self, span: SrcSpan, target: NameTarget, expression_type: int | None = None) -> None:
        if span.is_synthetic():
            return
        key = (str(span.path), span.start.row, span.start.col)
        if key in self.__keys:
            return
        self.__keys.add(key)
        self.__items.append(NameRef(span, target, expression_type))

    def entries(self) -> tuple[NameRef, ...]:
        return tuple(self.__items)
