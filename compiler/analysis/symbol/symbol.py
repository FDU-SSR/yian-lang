from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class SymbolKind(Enum):
    Variable = "variable"
    Function = "function"
    Type = "type"
    ConstGeneric = "const_generic"


class SymbolAttribute(Enum):
    Public = "public"


@dataclass
class Symbol:
    symbol_id: int
    name: str
    kind: SymbolKind
    type_id: int
    attributes: set[SymbolAttribute]
