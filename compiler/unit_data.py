from collections import defaultdict
from dataclasses import dataclass, field

from compiler.config.defs import StmtId, SymbolId, UnitId
from compiler.utils import IR
from compiler.utils.errors import CompilerError, NameResolutionError
from compiler.utils.IR import Symbol
from compiler.utils.IR import gir as ir
from compiler.utils.IR import map_stmts

from lian.main import Lian


@dataclass
class SymbolTable:
    symbols: dict[SymbolId, Symbol] = field(default_factory=dict)
    usage_table: dict[StmtId, dict[str, SymbolId]] = field(default_factory=lambda: defaultdict(dict))
    def_table: dict[StmtId, dict[str, SymbolId]] = field(default_factory=lambda: defaultdict(dict))

    def add_usage(self, stmt_id: StmtId, name: str, symbol_id: SymbolId):
        if name in self.usage_table[stmt_id] and self.usage_table[stmt_id][name] != symbol_id:
            raise CompilerError(f"Duplicate symbol name '{name}' in statement ID {stmt_id}")
        self.usage_table[stmt_id][name] = symbol_id

    def add_def(self, stmt_id: StmtId, name: str, symbol_id: SymbolId):
        if name in self.def_table[stmt_id]:
            raise CompilerError(f"Duplicate definition for statement ID {stmt_id}")
        self.def_table[stmt_id][name] = symbol_id

    def lookup(self, stmt_id: StmtId, name: str) -> Symbol:
        """
        Look up a symbol by its name in the context of a given statement ID.
        """
        if name in self.usage_table[stmt_id]:
            symbol_id = self.usage_table[stmt_id][name]
        else:
            raise NameResolutionError(f"Symbol '{name}' not found in statement ID {stmt_id}")
        if symbol_id not in self.symbols:
            raise NameResolutionError(f"Symbol '{name}'(id: {symbol_id}) not found in symbol table")
        return self.symbols[symbol_id]

    def lookup_def(self, stmt_id: StmtId, name: str) -> SymbolId:
        """
        Look up the symbol ID of a definition by its name in the context of a given statement ID.
         """
        if name in self.def_table[stmt_id]:
            return self.def_table[stmt_id][name]
        else:
            raise NameResolutionError(f"Definition for symbol '{name}' not found in statement ID {stmt_id}")

    def register(self, symbol: Symbol):
        if symbol.symbol_id in self.symbols:
            # raise CompilerError(f"Duplicate symbol ID '{symbol.symbol_id}' for symbol '{symbol.name}'")
            return  # ignore duplicate symbol registration
        self.symbols[symbol.symbol_id] = symbol

    def __getitem__(self, symbol_id: SymbolId) -> Symbol:
        return self.symbols[symbol_id]


class UnitData:
    def __init__(self, lian: Lian, unit_info):
        self.unit_id: UnitId = int(unit_info.unit_id)
        self.original_path: str = unit_info.original_path
        self.unit_name: str = unit_info.symbol_name

        # unit path(file directory components)
        self.unit_path: list[str] = self.__split_unit_path(unit_info.original_path)

        # IR storage
        self.__girs = map_stmts(list(lian.loader.get_unit_gir(self.unit_id)))  # type: ignore

        # Symbol table
        self.__symbol_table = SymbolTable()

        # Exportable symbols
        self.__exportable_symbols: dict[str, IR.Symbol] = {}

    def __repr__(self) -> str:
        return f"UnitData(unit_name={self.unit_name}, unit_id={self.unit_id})"

    @property
    def root_block(self) -> ir.BlockStmt:
        """
        Get the root block of the unit.
        """
        return self.__girs[0].expect_block()

    def __split_unit_path(self, path: str) -> list[str]:
        """
        Split the unit path into its components.
        """
        components = []
        current_component = ""
        for char in path:
            if char == "/":
                if current_component:
                    components.append(current_component)
                    current_component = ""
            else:
                current_component += char
        if current_component:
            components.append(current_component)
        return components[:-1]  # exclude the file name

    @property
    def is_lib_module(self) -> bool:
        """
        Check if the unit is a standard library module.
        """
        # TODO: re-enable this check when std lib handling is complete
        return True
        # return self.unit_name in STD_LIBS

    def get(self, stmt_id: StmtId) -> ir.GIRStmt:
        """
        Given a statement ID, get the corresponding GIR statement.
        """
        if stmt_id not in self.__girs:
            raise CompilerError(f"GIR statement id '{stmt_id}' not found in unit: {self.unit_name}")
        return self.__girs[stmt_id]

    def set(self, stmt_id: StmtId, stmt: ir.GIRStmt):
        """
        Set or update a GIR statement in the collection.
        """
        self.__girs[stmt_id] = stmt

    def contains(self, stmt_id: StmtId) -> bool:
        """
        Check if the GIR statement collection contains a statement with the given ID.
        """
        return stmt_id in self.__girs

    def export(self, path: str):
        try:
            with open(path, "w", encoding="utf-8") as f:
                for gir_stmt in self.__girs.values():
                    f.write(f"{gir_stmt}\n")
        except Exception as e:
            raise CompilerError(f"failed to export GIR to '{path}': {e}")

    def symbol_register(self, symbol: Symbol):
        """
        Register a symbol in the symbol table.
        """
        self.__symbol_table.register(symbol)

    def symbol_register_def(self, stmt_id: StmtId, name: str, symbol_id: SymbolId):
        """
        Register a symbol definition in the symbol table.
        """
        self.__symbol_table.add_def(stmt_id, name, symbol_id)

    def symbol_register_usage(self, stmt_id: StmtId, name: str, symbol_id: SymbolId):
        """
        Register a symbol usage in the symbol table.
        """
        self.__symbol_table.add_usage(stmt_id, name, symbol_id)

    def symbol_lookup(self, stmt_id: StmtId, symbol_name: str) -> Symbol:
        """
        Given a statement ID and a symbol name, look up the corresponding symbol.
        """
        return self.__symbol_table.lookup(stmt_id, symbol_name)

    def symbol_lookup_def(self, stmt_id: StmtId, name: str) -> SymbolId:
        """
        Given a statement ID and a symbol name, look up the symbol ID of the definition.
        """
        return self.__symbol_table.lookup_def(stmt_id, name)

    def symbol_get(self, symbol_id: SymbolId) -> Symbol:
        """
        Given a symbol ID, get the corresponding symbol.
        """
        return self.__symbol_table[symbol_id]

    def symbol_export(self, symbol_id: SymbolId) -> None:
        """
        Mark a symbol as exportable from the unit.
        """
        symbol = self.symbol_get(symbol_id)
        self.__exportable_symbols[symbol.name] = symbol

    def symbol_get_exported(self, symbol_name: str) -> IR.Symbol:
        """
        Get an exported symbol by its name.
        """
        if symbol_name not in self.__exportable_symbols:
            raise NameResolutionError(f"No exported symbol '{symbol_name}' found in unit: {self.unit_name}.an")
        return self.__exportable_symbols[symbol_name]

    def symbol_is_inplace_defined(self, stmt_id: StmtId, name: str) -> bool:
        """
        Check if a symbol is defined in-place in the current statement.

        Args:
            stmt_id (StmtId): The ID of the statement.
            name (str): The name of the symbol to check.
        """
        return name in self.__symbol_table.def_table[stmt_id]

    def get_enclosing_method_id(self, stmt_id: StmtId) -> StmtId:
        """
        Get the enclosing method ID of a given statement.

        Args:
            stmt_id (StmtId): The ID of the statement.
        """
        stmt = self.__girs[stmt_id]
        while not isinstance(stmt, (ir.MethodDeclStmt, ir.FunctionDeclStmt)):
            stmt = self.__girs[stmt.metadata.parent_stmt_id]
        return stmt.stmt_id

    def relative_path_to(self, paths: list[str]) -> list[str]:
        """
        Get the relative path from the current unit to the target unit.

        Args:
            paths (list[str]): The target unit path components.
        """
        # find common prefix length
        common_length = 0
        for comp_a, comp_b in zip(self.unit_path, paths):
            if comp_a == comp_b:
                common_length += 1
            else:
                break

        # build relative path
        relative_path = []
        up_levels = len(self.unit_path) - common_length
        for _ in range(up_levels):
            relative_path.append("..")
        relative_path.extend(paths[common_length:])
        return relative_path
