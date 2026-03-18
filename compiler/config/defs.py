"""
This module defines type aliases used throughout the compiler configuration. Including

- Various identifier types (UnitId, StmtId, SymbolId, TypeId)
- Generic ID generator type (IdGen)
- IR handler and handler map types (IRHandler, IRHandlerMap)
- Type formatter and instantiator types (TypeFormatter, TypeInstantiator)
"""

from typing import Callable, TypeAlias, TypeVar


UnitId: TypeAlias = int
StmtId: TypeAlias = int
SymbolId: TypeAlias = int
TypeId: TypeAlias = int

IdType = TypeVar("IdType", UnitId, StmtId, SymbolId, TypeId)
IdGen: TypeAlias = Callable[[], IdType]

IRType = TypeVar("IRType")
IRHandler: TypeAlias = Callable[[IRType], None]
IRHandlerMap: TypeAlias = dict[type[IRType], IRHandler[IRType]]

TypeFormatter: TypeAlias = Callable[[TypeId], str]
TypeInstantiator: TypeAlias = Callable[[TypeId, dict[TypeId, TypeId]], TypeId]
