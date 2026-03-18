from compiler.analysis.semantic_analysis.utils.analysis_pass import UnitPass
from compiler.config.constants import YianAttribute
from compiler.config.defs import IRHandlerMap

from compiler.utils.IR import gir as ir


class ExportCollector(UnitPass):
    @property
    def _code_block_handlers(self) -> IRHandlerMap[ir.GIRStmt]:
        return {}  # No need to handle code block statements for symbol collection

    @property
    def _top_level_handlers(self) -> IRHandlerMap[ir.GIRStmt]:
        return {
            ir.FunctionDeclStmt: self.__function_decl,
            ir.VariableDeclStmt: self.__global_variable_decl,
            ir.ImportStmt: lambda stmt: None,
            ir.ImplementDeclStmt: lambda stmt: None,
            ir.TraitDeclStmt: self.__trait_decl,
            ir.StructDeclStmt: self.__struct_decl,
            ir.EnumDeclStmt: self.__enum_decl,
            ir.TypeAliasDeclStmt: self.__type_alias_decl,
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

    def __function_decl(self, stmt: ir.GIRStmt):
        assert isinstance(stmt, ir.FunctionDeclStmt)

        if YianAttribute.Public in stmt.attributes:
            symbol_id = self._ctx.symbol_lookup_def(stmt.stmt_id, stmt.name)
            self._ctx.symbol_export(symbol_id)

    def __global_variable_decl(self, stmt: ir.GIRStmt):
        assert isinstance(stmt, ir.VariableDeclStmt)

        if YianAttribute.Public in stmt.attributes:
            symbol_id = self._ctx.symbol_lookup_def(stmt.stmt_id, stmt.name)
            self._ctx.symbol_export(symbol_id)

    def __trait_decl(self, stmt: ir.GIRStmt):
        assert isinstance(stmt, ir.TraitDeclStmt)

        if YianAttribute.Public in stmt.attributes:
            symbol_id = self._ctx.symbol_lookup_def(stmt.stmt_id, stmt.name)
            self._ctx.symbol_export(symbol_id)

    def __struct_decl(self, stmt: ir.GIRStmt):
        assert isinstance(stmt, ir.StructDeclStmt)

        if YianAttribute.Public in stmt.attributes:
            symbol_id = self._ctx.symbol_lookup_def(stmt.stmt_id, stmt.name)
            self._ctx.symbol_export(symbol_id)

    def __enum_decl(self, stmt: ir.GIRStmt):
        assert isinstance(stmt, ir.EnumDeclStmt)

        if YianAttribute.Public in stmt.attributes:
            symbol_id = self._ctx.symbol_lookup_def(stmt.stmt_id, stmt.name)
            self._ctx.symbol_export(symbol_id)

    def __type_alias_decl(self, stmt: ir.GIRStmt):
        assert isinstance(stmt, ir.TypeAliasDeclStmt)

        if YianAttribute.Public in stmt.attributes:
            symbol_id = self._ctx.symbol_lookup_def(stmt.stmt_id, stmt.name)
            self._ctx.symbol_export(symbol_id)
