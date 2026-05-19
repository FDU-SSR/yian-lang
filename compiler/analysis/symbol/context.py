from __future__ import annotations

from dataclasses import dataclass

from compiler.analysis.symbol.symbol import Symbol, SymbolAttribute, SymbolKind
from compiler.utils.errors.yian_error import CompilerError


@dataclass
class Scope:
    symbols: dict[str, int]  # name -> symbol_id
    parent: Scope | None


class SymbolCtx:
    def __init__(self, unit_id: int):
        self.__unit_id = unit_id
        self.__all_symbols: dict[int, Symbol] = {}  # symbol_id -> Symbol
        self.__next_id = 0
        self.__current_scope = Scope(symbols={}, parent=None)

    @property
    def unit_id(self) -> int:
        return self.__unit_id

    def __next_symbol_id(self) -> int:
        symbol_id = self.__next_id
        self.__next_id += 1
        return symbol_id

    def enter_scope(self) -> None:
        """Enters a new scope."""
        self.__current_scope = Scope(symbols={}, parent=self.__current_scope)

    def exit_scope(self) -> None:
        """Exits the current scope."""
        if self.__current_scope.parent is None:
            raise CompilerError("Cannot exit global scope")
        self.__current_scope = self.__current_scope.parent

    def add_symbol(self, name: str, kind: SymbolKind, type_id: int, attributes: set[SymbolAttribute] = set()) -> int | None:
        """
        Adds a new symbol to the current scope and returns its symbol ID.

        returns None if the symbol already exists in the current scope.
        """
        if name in self.__current_scope.symbols:
            return None  # Symbol already exists in the current scope

        symbol_id = self.__next_symbol_id()
        self.__current_scope.symbols[name] = symbol_id
        symbol = Symbol(symbol_id=symbol_id, name=name, kind=kind, type_id=type_id, attributes=attributes)
        self.__all_symbols[symbol_id] = symbol
        return symbol_id

    def get(self, symbol_id: int) -> Symbol:
        """Gets a symbol by its ID."""
        return self.__all_symbols[symbol_id]

    def lookup(self, name: str) -> int | None:
        """Looks up a symbol by name in the current scope and its parents."""
        scope = self.__current_scope
        while scope is not None:
            if name in scope.symbols:
                return scope.symbols[name]
            scope = scope.parent
        return None  # Symbol not found
