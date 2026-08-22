from __future__ import annotations

from dataclasses import dataclass

from compiler.analysis.symbol.symbol import Symbol, SymbolAttribute, SymbolKind
from compiler.analysis.ty.context import TypeCtx
from compiler.error import CompilerError


@dataclass
class Scope:
    symbols: dict[str, int]  # name -> symbol_id
    parent: Scope | None
    shared: bool = False  # symbols dict may be shared with another context

    def clone(self) -> Scope:
        """Clones the scope, creating a new instance with the same symbols and parent."""
        return Scope(symbols=self.symbols.copy(), parent=self.parent.clone() if self.parent else None)


class SymbolCtx:
    def __init__(self):
        self.__all_symbols: dict[int, Symbol] = {}  # symbol_id -> Symbol
        self.__next_id_holder: list[int] = [0]
        self.__current_scope = Scope(symbols={}, parent=None)

        self.__exportable_symbols: dict[str, int] = {}  # name -> symbol_id

        # Copy-on-write sharing: a clone shares the symbol dictionaries and
        # scope chain with its source; the first mutation detaches it.
        self.__all_shared = False
        self.__exportable_shared = False

        # add built-in types
        self.add_symbol(name="void", type_id=TypeCtx.void_id, kind=SymbolKind.Type)
        self.add_symbol(name="bool", type_id=TypeCtx.bool_id, kind=SymbolKind.Type)
        self.add_symbol(name="char", type_id=TypeCtx.char_id, kind=SymbolKind.Type)
        self.add_symbol(name="str", type_id=TypeCtx.str_id, kind=SymbolKind.Type)

        self.add_symbol(name="i8", type_id=TypeCtx.i8_id, kind=SymbolKind.Type)
        self.add_symbol(name="i16", type_id=TypeCtx.i16_id, kind=SymbolKind.Type)
        self.add_symbol(name="i32", type_id=TypeCtx.i32_id, kind=SymbolKind.Type)
        self.add_symbol(name="i64", type_id=TypeCtx.i64_id, kind=SymbolKind.Type)

        self.add_symbol(name="u8", type_id=TypeCtx.u8_id, kind=SymbolKind.Type)
        self.add_symbol(name="u16", type_id=TypeCtx.u16_id, kind=SymbolKind.Type)
        self.add_symbol(name="u32", type_id=TypeCtx.u32_id, kind=SymbolKind.Type)
        self.add_symbol(name="u64", type_id=TypeCtx.u64_id, kind=SymbolKind.Type)

        self.add_symbol(name="f16", type_id=TypeCtx.f16_id, kind=SymbolKind.Type)
        self.add_symbol(name="f32", type_id=TypeCtx.f32_id, kind=SymbolKind.Type)
        self.add_symbol(name="f64", type_id=TypeCtx.f64_id, kind=SymbolKind.Type)

        self.add_symbol(name="int", type_id=TypeCtx.i32_id, kind=SymbolKind.Type)
        self.add_symbol(name="uint", type_id=TypeCtx.u32_id, kind=SymbolKind.Type)
        self.add_symbol(name="float", type_id=TypeCtx.f64_id, kind=SymbolKind.Type)

    def __next_symbol_id(self) -> int:
        symbol_id = self.__next_id_holder[0]
        self.__next_id_holder[0] += 1
        return symbol_id

    def __ensure_all_owned(self) -> None:
        """Detach from a shared symbol table before mutating it."""
        if self.__all_shared:
            self.__all_symbols = self.__all_symbols.copy()
            self.__all_shared = False

    def __ensure_exportable_owned(self) -> None:
        """Detach from a shared exportable-symbol table before mutating it."""
        if self.__exportable_shared:
            self.__exportable_symbols = self.__exportable_symbols.copy()
            self.__exportable_shared = False

    def __ensure_scope_owned(self) -> None:
        """Detach the current scope's symbol dict before mutating it."""
        if self.__current_scope.shared:
            self.__current_scope.symbols = self.__current_scope.symbols.copy()
            self.__current_scope.shared = False

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

        self.__ensure_all_owned()
        self.__ensure_scope_owned()

        symbol_id = self.__next_symbol_id()
        self.__current_scope.symbols[name] = symbol_id
        symbol = Symbol(symbol_id=symbol_id, name=name, kind=kind, type_id=type_id, attributes=attributes)
        self.__all_symbols[symbol_id] = symbol

        # add a pub symbol to global scope will be exported
        if SymbolAttribute.Public in attributes and self.__current_scope.parent is None:
            self.__ensure_exportable_owned()
            self.__exportable_symbols[name] = symbol_id

        return symbol_id

    def add_symbol_with_id(self, symbol_id: int, name: str, kind: SymbolKind,
                            type_id: int) -> bool:
        """Add a symbol with a specific symbol_id. Returns False if ID exists."""
        if symbol_id in self.__all_symbols:
            return False
        if name in self.__current_scope.symbols:
            return False
        self.__ensure_all_owned()
        self.__ensure_scope_owned()
        self.__current_scope.symbols[name] = symbol_id
        symbol = Symbol(symbol_id=symbol_id, name=name, kind=kind, type_id=type_id, attributes=set())
        self.__all_symbols[symbol_id] = symbol
        self.__next_id_holder[0] = max(self.__next_id_holder[0], symbol_id + 1)
        return True

    def get(self, symbol_id: int) -> Symbol:
        """Gets a symbol by its ID."""
        return self.__all_symbols[symbol_id]

    def items(self):
        return self.__all_symbols.items()

    def lookup(self, name: str) -> Symbol | None:
        """Looks up a symbol by name in the current scope and its parents."""
        scope = self.__current_scope
        while scope is not None:
            if name in scope.symbols:
                return self.__all_symbols[scope.symbols[name]]
            scope = scope.parent
        return None  # Symbol not found

    def lookup_exportable(self, name: str) -> Symbol | None:
        """Looks up an exportable symbol by name."""
        symbol_id = self.__exportable_symbols.get(name)
        if symbol_id is not None:
            return self.__all_symbols[symbol_id]
        return None

    def clone(self) -> SymbolCtx:
        """Clones the symbol context, creating a new instance with the same symbols and scope structure.

        Copy-on-write: the clone shares the symbol tables and the current
        scope's symbol dict with the source; both sides detach on first
        mutation, preserving full isolation between the two contexts.
        """
        new_ctx = SymbolCtx()
        new_ctx.__all_symbols = self.__all_symbols
        new_ctx.__next_id_holder = self.__next_id_holder
        new_ctx.__current_scope = Scope(symbols=self.__current_scope.symbols, parent=self.__current_scope.parent, shared=True)
        new_ctx.__exportable_symbols = self.__exportable_symbols
        new_ctx.__all_shared = True
        new_ctx.__exportable_shared = True
        self.__all_shared = True
        self.__exportable_shared = True
        self.__current_scope.shared = True
        return new_ctx
