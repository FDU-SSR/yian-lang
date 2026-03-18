"""
Decl Scan pass: scan all declarations' type information in unit data, including:

- Custom types (structs, enums, traits, aliases) and their inner declarations
- Function signatures
- Method signatures (in traits or impl blocks)
- Global variable declarations
"""

from compiler.analysis.semantic_analysis.utils.analysis_pass import UnitPass
from compiler.config.constants import AccessMode, YianAttribute
from compiler.config.defs import IRHandlerMap
from compiler.utils import IR, ty
from compiler.utils.IR import gir as ir


class DeclScanner(UnitPass):
    @property
    def _code_block_handlers(self) -> IRHandlerMap[ir.GIRStmt]:
        return {}  # No need to dive into code blocks for declaration scanning

    @property
    def _top_level_handlers(self) -> IRHandlerMap[ir.GIRStmt]:
        return {
            ir.FunctionDeclStmt: self.__function_decl,
            ir.VariableDeclStmt: self.__global_variable_decl,  # Only global variables are handled here
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

        # fetch raw info
        symbol_id = self._ctx.symbol_lookup_def(stmt.stmt_id, stmt.name)
        func_symbol = self._ctx.symbol_get(symbol_id)
        assert isinstance(func_symbol, IR.Function)
        func_ty = self._ctx.ty_get(func_symbol.type_id).expect_function()
        func_def = func_ty.function_def
        self._ctx.enter_function_scope(func_ty.type_id)

        # handle attributes
        func_def.attributes = set(stmt.attributes)

        # handle return type
        func_def.return_type = self._ctx.parse_type(stmt.stmt_id, stmt.return_type)

        # handle parameters
        params: list[ty.Parameter] = []

        def param_handler(param_stmt: ir.GIRStmt):
            assert isinstance(param_stmt, ir.ParameterDeclStmt)
            symbol_id = self._ctx.symbol_lookup_def(param_stmt.stmt_id, param_stmt.name)
            param_ty = self._ctx.parse_type(param_stmt.stmt_id, param_stmt.data_type)
            params.append(ty.Parameter(symbol_id, param_stmt.name, param_ty))

        if stmt.parameters is not None:
            self._process_block(stmt.parameters, {
                ir.ParameterDeclStmt: param_handler
            })
        func_def.parameters = params

        # exit scope
        self._ctx.exit_scope()

    def __global_variable_decl(self, stmt: ir.GIRStmt):
        assert isinstance(stmt, ir.VariableDeclStmt)

        # handle type
        ty = self._ctx.parse_type(stmt.stmt_id, stmt.data_type)

        # set type to symbol
        symbol_id = self._ctx.symbol_lookup_def(stmt.stmt_id, stmt.name)
        var_symbol = self._ctx.symbol_get(symbol_id)
        assert isinstance(var_symbol, IR.VariableSymbol)
        var_symbol.set_type(ty)

    def __implement_decl(self, stmt: ir.GIRStmt):
        assert isinstance(stmt, ir.ImplementDeclStmt)

        # register impl
        impl = self._ctx.ty_alloc_impl(stmt.stmt_id, stmt.type_parameters)
        self._ctx.enter_impl_scope(stmt.stmt_id)

        # parse trait type
        if stmt.trait_type is not None:
            impl.trait = self._ctx.parse_type(stmt.stmt_id, stmt.trait_type)

        # parse target type
        impl.target = self._ctx.parse_type(stmt.stmt_id, stmt.target_type)

        def method_decl(method_stmt: ir.GIRStmt):
            assert isinstance(method_stmt, ir.MethodDeclStmt)

            # fetch raw info
            symbol_id = self._ctx.symbol_lookup_def(method_stmt.stmt_id, method_stmt.name)
            method_symbol = self._ctx.symbol_get(symbol_id)
            assert isinstance(method_symbol, IR.Method)
            method_ty = self._ctx.ty_get(method_symbol.type_id).expect_method()
            method_def = method_ty.method_def
            self._ctx.enter_method_scope(method_ty.type_id)

            method_def.is_header = False

            # parse generic
            impl_generics = impl.generics
            offset = len(impl_generics)
            method_generics = [
                self._ctx.ty_alloc_generic(name, offset + index, method_stmt.stmt_id)
                for index, name in enumerate(method_stmt.type_parameters)
            ]
            all_generics = method_generics + impl_generics
            method_ty.generic_args = all_generics.copy()
            method_def.generics = all_generics

            # handle attributes
            method_def.attributes = set(method_stmt.attributes)

            # handle receiver
            method_def.receiver_type = impl.target

            # handle return type
            method_def.return_type = self._ctx.parse_type(method_stmt.stmt_id, method_stmt.return_type)

            # handle parameters
            params: list[ty.Parameter] = []

            def param_handler(param_stmt: ir.GIRStmt):
                assert isinstance(param_stmt, ir.ParameterDeclStmt)

                if param_stmt.name == "self":
                    return

                symbol_id = self._ctx.symbol_lookup_def(param_stmt.stmt_id, param_stmt.name)
                param_ty = self._ctx.parse_type(param_stmt.stmt_id, param_stmt.data_type)
                params.append(ty.Parameter(symbol_id, param_stmt.name, param_ty))

            if method_stmt.parameters is not None:
                self._process_block(method_stmt.parameters, {
                    ir.ParameterDeclStmt: param_handler
                })
            method_def.parameters = params

            # exit scopes
            self._ctx.exit_scope()

            # register method in impl
            impl.add_method(method_stmt.name, method_ty.type_id)

        if stmt.methods is not None:
            self._process_block(stmt.methods, {
                ir.MethodDeclStmt: method_decl
            })

        # exit scopes
        self._ctx.exit_scope()

    def __trait_decl(self, stmt: ir.GIRStmt):
        assert isinstance(stmt, ir.TraitDeclStmt)

        # fetch raw info
        symbol_id = self._ctx.symbol_lookup_def(stmt.stmt_id, stmt.name)
        trait_symbol = self._ctx.symbol_get(symbol_id)
        assert isinstance(trait_symbol, IR.CustomType)
        trait_ty = self._ctx.ty_get(trait_symbol.type_id).expect_trait()
        trait_def = trait_ty.trait_def
        self._ctx.enter_trait_scope(trait_ty.type_id)

        # handle attributes
        trait_def.attributes = set(stmt.attributes)

        def method_handler(method_stmt: ir.GIRStmt):
            assert isinstance(method_stmt, (ir.MethodDeclStmt, ir.MethodHeaderStmt))

            # fetch raw info
            symbol_id = self._ctx.symbol_lookup_def(method_stmt.stmt_id, method_stmt.name)
            method_symbol = self._ctx.symbol_get(symbol_id)
            assert isinstance(method_symbol, IR.Method)
            method_ty = self._ctx.ty_get(method_symbol.type_id).expect_method()
            method_def = method_ty.method_def
            self._ctx.enter_method_scope(method_ty.type_id)

            method_def.is_header = isinstance(method_stmt, ir.MethodHeaderStmt)

            # parse generic
            trait_generics = trait_def.generics
            offset = len(trait_generics)
            method_generics = [
                self._ctx.ty_alloc_generic(name, offset + index, method_stmt.stmt_id)
                for index, name in enumerate(method_stmt.type_parameters)
            ]
            all_generics = method_generics + trait_generics
            method_ty.generic_args = all_generics.copy()
            method_def.generics = all_generics

            # handle attributes
            method_def.attributes = set(method_stmt.attributes)

            # handle receiver
            method_def.receiver_type = trait_ty.type_id

            # handle return type
            method_def.return_type = self._ctx.parse_type(method_stmt.stmt_id, method_stmt.return_type)

            # handle parameters
            params: list[ty.Parameter] = []

            def param_handler(stmt: ir.GIRStmt):
                assert isinstance(stmt, ir.ParameterDeclStmt)
                symbol_id = self._ctx.symbol_lookup_def(stmt.stmt_id, stmt.name)
                param_ty = self._ctx.parse_type(stmt.stmt_id, stmt.data_type)
                params.append(ty.Parameter(symbol_id, stmt.name, param_ty))

            if method_stmt.parameters is not None:
                self._process_block(method_stmt.parameters, {
                    ir.ParameterDeclStmt: param_handler
                })
            method_def.parameters = params

            # exit scopes
            self._ctx.exit_scope()

            # register method in trait
            trait_def.add_method(method_stmt.name, method_ty.type_id)

        if stmt.methods is not None:
            self._process_block(stmt.methods, {
                ir.MethodDeclStmt: method_handler,
                ir.MethodHeaderStmt: method_handler,
            })

        # exit scopes
        self._ctx.exit_scope()

    def __struct_decl(self, stmt: ir.GIRStmt):
        assert isinstance(stmt, ir.StructDeclStmt)

        # fetch raw info
        symbol_id = self._ctx.symbol_lookup_def(stmt.stmt_id, stmt.name)
        struct_symbol = self._ctx.symbol_get(symbol_id)
        assert isinstance(struct_symbol, IR.CustomType)
        struct_ty = self._ctx.ty_get(struct_symbol.type_id).expect_struct()
        struct_def = struct_ty.struct_def
        self._ctx.enter_custom_type_scope(struct_ty.type_id)

        # handle attributes
        struct_def.attributes = set(stmt.attributes)

        # handle fields
        def field_handler(field_stmt: ir.GIRStmt):
            assert isinstance(field_stmt, ir.VariableDeclStmt)
            field_ty = self._ctx.parse_type(field_stmt.stmt_id, field_stmt.data_type)
            if YianAttribute.Public in field_stmt.attributes:
                access_mode = AccessMode.Public
            else:
                access_mode = AccessMode.Private
            struct_def.add_field(field_stmt.name, field_ty, access_mode)

        if stmt.fields is not None:
            self._process_block(stmt.fields, {
                ir.VariableDeclStmt: field_handler
            })

        # exit scopes
        self._ctx.exit_scope()

    def __enum_decl(self, stmt: ir.GIRStmt):
        assert isinstance(stmt, ir.EnumDeclStmt)

        # fetch raw info
        symbol_id = self._ctx.symbol_lookup_def(stmt.stmt_id, stmt.name)
        enum_symbol = self._ctx.symbol_get(symbol_id)
        assert isinstance(enum_symbol, IR.CustomType)
        enum_ty = self._ctx.ty_get(enum_symbol.type_id).expect_enum()
        enum_def = enum_ty.enum_def
        self._ctx.enter_custom_type_scope(enum_ty.type_id)

        # handle attributes
        enum_def.attributes = set(stmt.attributes)

        # handle variants
        def variant_handler(variant_stmt: ir.GIRStmt):
            assert isinstance(variant_stmt, ir.VariantDeclStmt)

            if variant_stmt.payload is None:
                enum_def.add_variant(variant_stmt.name, None)
            else:
                payload_ty = self._ctx.ty_alloc_struct(self._ctx.unit_id, variant_stmt.stmt_id, f"{stmt.name}::{variant_stmt.name}Payload")
                payload_ty_def = self._ctx.ty_get(payload_ty).expect_struct()
                payload_def = payload_ty_def.struct_def

                payload_ty_def.generic_args = enum_def.generics.copy()
                payload_def.generics = enum_def.generics.copy()

                def field_handler(field_stmt: ir.GIRStmt):
                    assert isinstance(field_stmt, ir.VariableDeclStmt)
                    field_ty = self._ctx.parse_type(field_stmt.stmt_id, field_stmt.data_type)
                    payload_def.add_field(field_stmt.name, field_ty, AccessMode.Public)

                self._process_block(variant_stmt.payload, {
                    ir.VariableDeclStmt: field_handler
                })

                enum_def.add_variant(variant_stmt.name, payload_ty)

        self._process_block(stmt.variants, {
            ir.VariantDeclStmt: variant_handler
        })

        # exit scopes
        self._ctx.exit_scope()

    def __type_alias_decl(self, stmt: ir.GIRStmt):
        assert isinstance(stmt, ir.TypeAliasDeclStmt)

        # fetch raw info
        symbol_id = self._ctx.symbol_lookup_def(stmt.stmt_id, stmt.name)
        alias_symbol = self._ctx.symbol_get(symbol_id)
        assert isinstance(alias_symbol, IR.TypeAlias)

        # handle aliased type
        alias_ty = self._ctx.parse_type(stmt.stmt_id, stmt.aliased_type)
        alias_symbol.set_type(alias_ty)
