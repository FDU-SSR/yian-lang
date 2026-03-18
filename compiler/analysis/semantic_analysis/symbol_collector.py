"""
Symbol Collect pass: Collects symbols from GIR statements and registers them in the unit data's symbol table. Including:

- Custom Types (structs, enums, traits)
- Functions and Methods
- Variables (global and local) (loop iterators, switch case payloads, etc. are also treated as variables)

For Custom Types, Functions, and Methods, their types are allocated in the TypeSpace during this pass.
"""

from compiler.analysis.semantic_analysis.utils.analysis_pass import UnitPass
from compiler.config.defs import IRHandlerMap
from compiler.utils import IR
from compiler.utils.IR import gir as ir


class SymbolCollector(UnitPass):
    @property
    def _code_block_handlers(self) -> IRHandlerMap[ir.GIRStmt]:
        return {}  # No need to handle code block statements for symbol collection

    @property
    def _top_level_handlers(self) -> IRHandlerMap[ir.GIRStmt]:
        return {
            ir.FunctionDeclStmt: self.__function_decl,
            ir.VariableDeclStmt: self.__global_variable_decl,
            ir.ImportStmt: lambda stmt: None,
            ir.ImplementDeclStmt: self.__implement_decl,
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

        symbol_id = self._ctx.symbol_lookup_def(stmt.stmt_id, stmt.name)

        # alloc func type
        func_ty = self._ctx.ty_alloc_function(self._ctx.unit_id, stmt.stmt_id, stmt.name, stmt.type_parameters)

        # register function symbol
        func_symbol = IR.Function(symbol_id, stmt.name, func_ty)
        self._ctx.symbol_register(func_symbol)

    def __global_variable_decl(self, stmt: ir.GIRStmt):
        assert isinstance(stmt, ir.VariableDeclStmt)

        symbol_id = self._ctx.symbol_lookup_def(stmt.stmt_id, stmt.name)

        # register variable symbol
        var_symbol = IR.VariableSymbol(symbol_id, stmt.name, None)
        self._ctx.symbol_register(var_symbol)

    def __implement_decl(self, stmt: ir.GIRStmt):
        assert isinstance(stmt, ir.ImplementDeclStmt)

        def __method_decl(method_stmt: ir.GIRStmt):
            assert isinstance(method_stmt, ir.MethodDeclStmt)

            symbol_id = self._ctx.symbol_lookup_def(method_stmt.stmt_id, method_stmt.name)

            # alloc method type
            method_ty = self._ctx.ty_alloc_method(self._ctx.unit_id, method_stmt.stmt_id, method_stmt.name)

            # register method symbol
            method_symbol = IR.Method(symbol_id, method_stmt.name, method_ty)
            self._ctx.symbol_register(method_symbol)

        # handle methods
        if stmt.methods is not None:
            self._process_block(stmt.methods, {
                ir.MethodDeclStmt: __method_decl,
            })

    def __trait_decl(self, stmt: ir.GIRStmt):
        assert isinstance(stmt, ir.TraitDeclStmt)

        def __method_decl(method_stmt: ir.GIRStmt):
            assert isinstance(method_stmt, ir.MethodDeclStmt)

            symbol_id = self._ctx.symbol_lookup_def(method_stmt.stmt_id, method_stmt.name)

            # alloc method type
            method_ty = self._ctx.ty_alloc_method(self._ctx.unit_id, method_stmt.stmt_id, method_stmt.name)

            # register method symbol
            method_symbol = IR.Method(symbol_id, method_stmt.name, method_ty)
            self._ctx.symbol_register(method_symbol)

        def __method_header(method_stmt: ir.GIRStmt):
            assert isinstance(method_stmt, ir.MethodHeaderStmt)

            symbol_id = self._ctx.symbol_lookup_def(method_stmt.stmt_id, method_stmt.name)

            # alloc method type
            method_ty = self._ctx.ty_alloc_method(self._ctx.unit_id, method_stmt.stmt_id, method_stmt.name)

            # register method symbol
            method_symbol = IR.Method(symbol_id, method_stmt.name, method_ty)
            self._ctx.symbol_register(method_symbol)

        symbol_id = self._ctx.symbol_lookup_def(stmt.stmt_id, stmt.name)

        # alloc trait type
        trait_ty = self._ctx.ty_alloc_trait(self._ctx.unit_id, stmt.stmt_id, stmt.name, stmt.type_parameters)

        # register trait symbol
        trait_symbol = IR.CustomType(symbol_id, stmt.name, trait_ty)
        self._ctx.symbol_register(trait_symbol)

        # handle methods
        if stmt.methods is not None:
            self._process_block(stmt.methods, {
                ir.MethodDeclStmt: __method_decl,
                ir.MethodHeaderStmt: __method_header,
            })

    def __struct_decl(self, stmt: ir.GIRStmt):
        assert isinstance(stmt, ir.StructDeclStmt)

        symbol_id = self._ctx.symbol_lookup_def(stmt.stmt_id, stmt.name)

        # alloc struct type
        struct_ty = self._ctx.ty_alloc_struct(self._ctx.unit_id, stmt.stmt_id, stmt.name, stmt.type_parameters)

        # register struct symbol
        struct_symbol = IR.CustomType(symbol_id, stmt.name, struct_ty)
        self._ctx.symbol_register(struct_symbol)

    def __enum_decl(self, stmt: ir.GIRStmt):
        assert isinstance(stmt, ir.EnumDeclStmt)

        symbol_id = self._ctx.symbol_lookup_def(stmt.stmt_id, stmt.name)

        # alloc enum type
        enum_ty = self._ctx.ty_alloc_enum(self._ctx.unit_id, stmt.stmt_id, stmt.name, stmt.type_parameters)

        # register enum symbol
        enum_symbol = IR.CustomType(symbol_id, stmt.name, enum_ty)
        self._ctx.symbol_register(enum_symbol)

    def __type_alias_decl(self, stmt: ir.GIRStmt):
        assert isinstance(stmt, ir.TypeAliasDeclStmt)

        symbol_id = self._ctx.symbol_lookup_def(stmt.stmt_id, stmt.name)

        # register type alias symbol
        type_alias_symbol = IR.TypeAlias(symbol_id, stmt.name, None)
        self._ctx.symbol_register(type_alias_symbol)
