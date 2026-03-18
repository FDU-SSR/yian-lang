from dataclasses import dataclass

from compiler.config.defs import SymbolId, TypeId
from compiler.utils.errors import CompilerError


@dataclass
class CustomType:
    symbol_id: SymbolId
    name: str
    type_id: TypeId


@dataclass
class TypeAlias:
    symbol_id: SymbolId
    name: str
    type_id: TypeId | None

    def set_type(self, type_id: TypeId):
        if self.type_id is not None:
            raise CompilerError(f"Type for variable symbol {self.symbol_id} is already set.")
        self.type_id = type_id


@dataclass
class Function:
    symbol_id: SymbolId
    name: str
    type_id: TypeId


@dataclass
class Method:
    symbol_id: SymbolId
    name: str
    type_id: TypeId


@dataclass
class VariableSymbol:
    """
    A VariableSymbol is an lvalue iff:

    - It is a compiler-generated temporary variable.
    - Logically, it represents a value
    - Actually, it is maintained by storing the address of the value
    """

    symbol_id: SymbolId
    name: str
    type_id: TypeId | None
    lvalue: bool = False

    def set_type(self, type_id: TypeId):
        if self.type_id is not None:
            raise CompilerError(f"Type for variable symbol {self.symbol_id} is already set.")
        self.type_id = type_id


@dataclass
class Literal:
    symbol_id: SymbolId
    name: str


Symbol = CustomType | Function | VariableSymbol | Method | Literal | TypeAlias
