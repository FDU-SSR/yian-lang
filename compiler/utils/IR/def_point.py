from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from compiler.config.defs import StmtId, SymbolId, TypeId, UnitId
from compiler.utils.errors.yian_error import CompilerError

if TYPE_CHECKING:
    from compiler.utils.IR import cgir
    from compiler.utils.IR.symbol import Symbol


@dataclass
class DefPoint:
    unit_id: UnitId
    stmt_id: StmtId
    procedure_name: str
    type_id: TypeId
    root_block_id: StmtId

    # Store CGIR statements here
    cgirs: dict[StmtId, "cgir.CheckedGIR"] = field(default_factory=dict, hash=False, repr=False, compare=False)
    # Store local symbols here
    symbol_table: dict[SymbolId, "Symbol"] = field(default_factory=dict, hash=False, repr=False, compare=False)

    def __hash__(self):
        return hash((self.unit_id, self.stmt_id, self.procedure_name, self.type_id, self.root_block_id))

    def __iter__(self):
        return iter(self.cgirs)

    def __repr__(self):
        return f"DefPoint(unit_id={self.unit_id}, procedure_name='{self.procedure_name}', type_id={self.type_id})"

    def get(self, stmt_id: StmtId) -> "cgir.CheckedGIR":
        """
        Given a statement ID, get the corresponding CGIR statement.
        """
        return self.cgirs[stmt_id]

    def contains(self, stmt_id: StmtId) -> bool:
        """
        Check if the CGIR statement collection contains a statement with the given ID.
        """
        return stmt_id in self.cgirs

    def emit(self, cgir_stmt: "cgir.CheckedGIR") -> None:
        """
        Add a CGIR statement to the collection.
        Add this CGIR statement to parent block.
        """
        from compiler.utils.IR import cgir as cir

        self.cgirs[cgir_stmt.stmt_id] = cgir_stmt
        if not isinstance(cgir_stmt, cir.Block):
            parent_block = self.cgirs[cgir_stmt.metadata.parent_stmt_id].expect_block()
            parent_block.statements.append(cgir_stmt.stmt_id)

    def register_symbol(self, symbol: "Symbol"):
        """
        Register a symbol in the local symbol table.
        """
        self.symbol_table[symbol.symbol_id] = symbol

    def get_symbol(self, symbol_id: SymbolId) -> "Symbol":
        """
        Get a symbol from the local symbol table.
        """
        return self.symbol_table[symbol_id]

    def export(self, path: str) -> None:
        """
        Export the CGIR statements of this DefPoint to a file for debugging purposes.
        """
        try:
            with open(path, "w", encoding="utf-8") as f:
                for cgir_stmt in self.cgirs.values():
                    f.write(f"{cgir_stmt}\n")
        except Exception as e:
            raise CompilerError(f"Failed to export CGIR '{path}': {e}")
