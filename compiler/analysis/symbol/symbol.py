from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from compiler.frontend.lex.position import SrcSpan


class SymbolKind(Enum):
    Variable = "variable"
    Constant = "constant"
    Function = "function"
    Type = "type"
    ConstGeneric = "const_generic"
    Alias = "alias"


class SymbolAttribute(Enum):
    Public = "public"
    FfiPublic = "ffi-public"


@dataclass(frozen=True)
class AliasDefId:
    value: int


@dataclass
class AliasSymbol:
    symbol_id: int
    name: str
    alias_id: AliasDefId
    attributes: set[SymbolAttribute]
    span: SrcSpan | None = None
    kind: SymbolKind = SymbolKind.Alias


@dataclass
class Symbol:
    symbol_id: int
    name: str
    kind: SymbolKind
    type_id: int
    attributes: set[SymbolAttribute]
    #: The defining (unit, symbol) pair for an imported compile-time constant.
    const_origin: tuple[int, int] | None = None
    #: Where the symbol is declared — the *name* span.  ``None`` for synthesized
    #: symbols (prelude injection, implicit `Self`), which no source position
    # points at. Editors need this to answer "go to definition".
    span: SrcSpan | None = None
