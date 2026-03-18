"""
Import Resolve pass: Resolves import statements in the GIR by linking imported symbols to their definitions in other units. Then, it updates the symbol table accordingly.
"""

from copy import deepcopy
from compiler.analysis.semantic_analysis.utils.analysis_pass import UnitPass
from compiler.config.defs import IRHandlerMap
from compiler.utils.IR import gir as ir


class ImportResolver(UnitPass):
    @property
    def _code_block_handlers(self) -> IRHandlerMap[ir.GIRStmt]:
        return {}  # No need to dive into code blocks for import resolution

    @property
    def _top_level_handlers(self) -> IRHandlerMap[ir.GIRStmt]:
        return {
            ir.FunctionDeclStmt: lambda stmt: None,
            ir.VariableDeclStmt: lambda stmt: None,
            ir.ImportStmt: self.__import,
            ir.ImplementDeclStmt: lambda stmt: None,
            ir.TraitDeclStmt: lambda stmt: None,
            ir.StructDeclStmt: lambda stmt: None,
            ir.EnumDeclStmt: lambda stmt: None,
            ir.TypeAliasDeclStmt: lambda stmt: None,
        }

    def _run_prelude(self) -> None:
        pass

    def _run_postlude(self) -> None:
        pass

    def _unit_prelude(self) -> None:
        pass

    def _unit_postlude(self) -> None:
        pass

    # ========== Handlers ==========

    def __import(self, stmt: ir.GIRStmt):
        assert isinstance(stmt, ir.ImportStmt)

        external_symbol = self._ctx.symbol_get_imported(stmt.paths, stmt.target)

        local_symbol = deepcopy(external_symbol)
        if stmt.alias is not None:
            local_symbol.name = stmt.alias
            local_symbol.symbol_id = self._ctx.symbol_lookup_def(stmt.stmt_id, stmt.alias)
        else:
            local_symbol.symbol_id = self._ctx.symbol_lookup_def(stmt.stmt_id, stmt.target)

        self._ctx.symbol_register(local_symbol)
