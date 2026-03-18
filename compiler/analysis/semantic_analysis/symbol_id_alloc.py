from dataclasses import dataclass

from compiler import utils
from compiler.analysis.semantic_analysis.utils.analysis_pass import UnitPass
from compiler.analysis.semantic_analysis.utils.context import SemanticCtx
from compiler.analysis.semantic_analysis.utils.type_parser import TypeParser
from compiler.config.constants import ROOT_BLOCK_ID
from compiler.config.defs import IRHandlerMap, StmtId, SymbolId
from compiler.utils.errors import SemanticError
from compiler.utils.IR import Operator
from compiler.utils.IR import gir as ir


@dataclass
class SymbolScope:
    symbols: dict[str, SymbolId]

    def contains(self, name: str) -> bool:
        return name in self.symbols


class SymbolIDAllocator(UnitPass):
    def __init__(self, ctx: SemanticCtx):
        super().__init__(ctx)

        self.__id_counter: SymbolId = 0
        self.__scopes: list[SymbolScope] = [SymbolScope({})]

    @property
    def _code_block_handlers(self) -> IRHandlerMap[ir.GIRStmt]:
        return {
            ir.VariableDeclStmt: self.__variable_decl,

            ir.IfStmt: self.__if_stmt,
            ir.ForStmt: self.__for_stmt,
            ir.ForInStmt: self.__forin_stmt,
            ir.LoopStmt: self.__loop_stmt,
            ir.SwitchStmt: self.__switch_stmt,
            ir.BlockStmt: self.__block,

            ir.ReturnStmt: self.__return_stmt,
            ir.CallStmt: self.__call_stmt,
            ir.AssignStmt: self.__assign_stmt,
            ir.BreakStmt: lambda stmt: None,
            ir.ContinueStmt: lambda stmt: None,
            ir.AssertStmt: self.__assert_stmt,
            ir.DeleteStmt: self.__del_stmt,

            ir.NewObjectStmt: self.__new_object,
            ir.NewArrayStmt: self.__new_array,
        }

    @property
    def _top_level_handlers(self) -> IRHandlerMap[ir.GIRStmt]:
        return {
            ir.FunctionDeclStmt: self.__alloc_func,
            ir.VariableDeclStmt: self.__alloc_global_var,
            ir.ImportStmt: self.__alloc_imported,
            ir.ImplementDeclStmt: lambda stmt: None,
            ir.TraitDeclStmt: self.__alloc_trait,
            ir.StructDeclStmt: self.__alloc_struct,
            ir.EnumDeclStmt: self.__alloc_enum,
            ir.TypeAliasDeclStmt: self.__alloc_type_alias,
        }

    def _run_prelude(self) -> None:
        pass

    def _run_postlude(self) -> None:
        pass

    def _unit_prelude(self) -> None:
        pass

    def _unit_postlude(self) -> None:
        """
        Main method to execute the symbol ID allocation pass for each unit.
        It is done in postlude to ensure that all symbols registered in global scope are available when processing symbol usages in the unit.
        """
        handlers = {
            ir.FunctionDeclStmt: self.__function_decl,
            ir.VariableDeclStmt: self.__global_variable_decl,
            ir.ImportStmt: lambda stmt: None,
            ir.ImplementDeclStmt: self.__implement_decl,
            ir.TraitDeclStmt: self.__trait_decl,
            ir.StructDeclStmt: self.__struct_decl,
            ir.EnumDeclStmt: self.__enum_decl,
            ir.TypeAliasDeclStmt: self.__type_alias_decl,
        }
        self._process_block(ROOT_BLOCK_ID, handlers)

        # clear global scope
        self.__scopes = [SymbolScope({})]

    # ========== Helper Methods ==========

    @property
    def current_scope(self) -> SymbolScope:
        return self.__scopes[-1]

    def __enter_scope(self) -> None:
        self.__scopes.append(SymbolScope({}))

    def __exit_scope(self) -> None:
        self.__scopes.pop()

    def __lookup_name(self, name: str) -> SymbolId | None:
        for scope in reversed(self.__scopes):
            if scope.contains(name):
                return scope.symbols[name]
        return None

    def __register_def(self, stmt_id: StmtId, name: str) -> SymbolId:
        """
        Register a symbol definition in current scope.
        """
        symbol_id = self._ctx.symbol_register_def(stmt_id, name)
        self.current_scope.symbols[name] = symbol_id

        return symbol_id

    def __register_use(self, stmt_id: StmtId, name: str) -> None:
        symbol_id = self.__lookup_name(name)
        if symbol_id is None:
            raise SemanticError(f"Undefined symbol: {name}")
        self._ctx.symbol_register_use(stmt_id, name, symbol_id)

    def __def_or_use(self, stmt_id: StmtId, name: str) -> None:
        """
        If the name is already defined in current scope, register it as usage.
        Otherwise, if it's compiler-generated symbol (e.g. %vv0), register it as definition and usage at the same time.
        """
        symbol_id = self.__lookup_name(name)
        if symbol_id is None:
            if name.startswith("%vv"):
                symbol_id = self.__register_def(stmt_id, name)
            else:
                raise SemanticError(f"Undefined symbol: {name}")
        self._ctx.symbol_register_use(stmt_id, name, symbol_id)

    def __use_type(self, stmt_id: StmtId, type_str: str) -> None:
        """
        Extract symbol names from the type string and register them as usages.
        """
        symbol_names = TypeParser.extract_symbol_names(type_str)
        for symbol_name in symbol_names:
            # Only register user-defined types as symbol usages. Intrinsic types (e.g. `i32`, `str`) are not registered in the symbol table and should be ignored here.
            if utils.is_identifier(symbol_name) or utils.is_compiler_generated_name(symbol_name):
                self.__register_use(stmt_id, symbol_name)

    def __use_expr(self, stmt_id: StmtId, expr_str: str) -> None:
        """
        Extract symbol names from the expression string and register them as usages. `expr_str` is expected to be:

        - a simple symbol name (e.g. `foo`)
        - literal value (e.g. `42`, `"hello"`, `true`)
        """
        if utils.is_identifier(expr_str) or utils.is_compiler_generated_name(expr_str):
            self.__register_use(stmt_id, expr_str)

    # ========== Global Symbol Allocators ==========

    def __alloc_func(self, stmt: ir.GIRStmt):
        assert isinstance(stmt, ir.FunctionDeclStmt)

        symbol_name = stmt.name
        self.__register_def(stmt.stmt_id, symbol_name)

    def __alloc_global_var(self, stmt: ir.GIRStmt):
        assert isinstance(stmt, ir.VariableDeclStmt)

        symbol_name = stmt.name
        self.__register_def(stmt.stmt_id, symbol_name)

    def __alloc_imported(self, stmt: ir.GIRStmt):
        assert isinstance(stmt, ir.ImportStmt)

        symbol_name = stmt.alias if stmt.alias is not None else stmt.target
        self.__register_def(stmt.stmt_id, symbol_name)

    def __alloc_trait(self, stmt: ir.GIRStmt):
        assert isinstance(stmt, ir.TraitDeclStmt)

        symbol_name = stmt.name
        self.__register_def(stmt.stmt_id, symbol_name)

    def __alloc_struct(self, stmt: ir.GIRStmt):
        assert isinstance(stmt, ir.StructDeclStmt)

        symbol_name = stmt.name
        self.__register_def(stmt.stmt_id, symbol_name)

    def __alloc_enum(self, stmt: ir.GIRStmt):
        assert isinstance(stmt, ir.EnumDeclStmt)

        symbol_name = stmt.name
        self.__register_def(stmt.stmt_id, symbol_name)

    def __alloc_type_alias(self, stmt: ir.GIRStmt):
        assert isinstance(stmt, ir.TypeAliasDeclStmt)

        symbol_name = stmt.name
        self.__register_def(stmt.stmt_id, symbol_name)

    # ========== Handlers ==========

    def __parameter_decl(self, stmt: ir.GIRStmt):
        assert isinstance(stmt, ir.ParameterDeclStmt)

        symbol_name = stmt.name
        self.__register_def(stmt.stmt_id, symbol_name)
        self.__use_type(stmt.stmt_id, stmt.data_type)

    def __function_decl(self, stmt: ir.GIRStmt):
        assert isinstance(stmt, ir.FunctionDeclStmt)

        self.__enter_scope()

        # type parameters
        for type_param in stmt.type_parameters:
            self.__register_def(stmt.stmt_id, type_param)

        # parameters
        if stmt.parameters is not None:
            self._process_block(stmt.parameters, {
                ir.ParameterDeclStmt: self.__parameter_decl,
            })

        # return type
        self.__use_type(stmt.stmt_id, stmt.return_type)

        # body
        self._process_block(stmt.body, self._code_block_handlers)

        self.__exit_scope()

    def __global_variable_decl(self, stmt: ir.GIRStmt):
        assert isinstance(stmt, ir.VariableDeclStmt)

        self.__use_type(stmt.stmt_id, stmt.data_type)

    def __implement_decl(self, stmt: ir.GIRStmt):
        assert isinstance(stmt, ir.ImplementDeclStmt)

        self.__enter_scope()

        # type parameters
        for type_param in stmt.type_parameters:
            self.__register_def(stmt.stmt_id, type_param)

        # trait type
        if stmt.trait_type is not None:
            self.__use_type(stmt.stmt_id, stmt.trait_type)

        # target type
        self.__use_type(stmt.stmt_id, stmt.target_type)

        # methods
        if stmt.methods is not None:
            self._process_block(stmt.methods, {
                ir.MethodDeclStmt: self.__method_decl,
            })

        self.__exit_scope()

    def __trait_decl(self, stmt: ir.GIRStmt):
        assert isinstance(stmt, ir.TraitDeclStmt)

        self.__enter_scope()

        # type parameters
        for type_param in stmt.type_parameters:
            self.__register_def(stmt.stmt_id, type_param)

        # methods
        if stmt.methods is not None:
            self._process_block(stmt.methods, {
                ir.MethodDeclStmt: self.__method_decl,
                ir.MethodHeaderStmt: self.__method_header,
            })

        self.__exit_scope()

    def __struct_decl(self, stmt: ir.GIRStmt):
        assert isinstance(stmt, ir.StructDeclStmt)

        self.__enter_scope()

        # type parameters
        for type_param in stmt.type_parameters:
            self.__register_def(stmt.stmt_id, type_param)

        # fields
        if stmt.fields is not None:
            self._process_block(stmt.fields, {
                ir.VariableDeclStmt: self.__variable_decl,
            })

        self.__exit_scope()

    def __enum_decl(self, stmt: ir.GIRStmt):
        assert isinstance(stmt, ir.EnumDeclStmt)

        self.__enter_scope()

        # type parameters
        for type_param in stmt.type_parameters:
            self.__register_def(stmt.stmt_id, type_param)

        # variants
        self._process_block(stmt.variants, {
            ir.VariantDeclStmt: self.__variant_decl,
        })

        self.__exit_scope()

    def __type_alias_decl(self, stmt: ir.GIRStmt):
        assert isinstance(stmt, ir.TypeAliasDeclStmt)

        self.__use_type(stmt.stmt_id, stmt.aliased_type)

    def __method_decl(self, stmt: ir.GIRStmt):
        assert isinstance(stmt, ir.MethodDeclStmt)

        symbol_name = stmt.name
        self.__register_def(stmt.stmt_id, symbol_name)

        self.__enter_scope()

        # type parameters
        for type_param in stmt.type_parameters:
            self.__register_def(stmt.stmt_id, type_param)

        # parameters
        if stmt.parameters is not None:
            self._process_block(stmt.parameters, {
                ir.ParameterDeclStmt: self.__parameter_decl,
            })

        # return type
        self.__use_type(stmt.stmt_id, stmt.return_type)

        # body
        self._process_block(stmt.body, self._code_block_handlers)

        self.__exit_scope()

    def __method_header(self, stmt: ir.GIRStmt):
        assert isinstance(stmt, ir.MethodHeaderStmt)

        symbol_name = stmt.name
        self.__register_def(stmt.stmt_id, symbol_name)

        self.__enter_scope()

        # type parameters
        for type_param in stmt.type_parameters:
            self.__register_def(stmt.stmt_id, type_param)

        # parameters
        if stmt.parameters is not None:
            self._process_block(stmt.parameters, {
                ir.ParameterDeclStmt: self.__parameter_decl,
            })

        # return type
        self.__use_type(stmt.stmt_id, stmt.return_type)

        self.__exit_scope()

    def __variable_decl(self, stmt: ir.GIRStmt):
        assert isinstance(stmt, ir.VariableDeclStmt)

        symbol_name = stmt.name
        self.__register_def(stmt.stmt_id, symbol_name)

        self.__use_type(stmt.stmt_id, stmt.data_type)

    def __variant_decl(self, stmt: ir.GIRStmt):
        assert isinstance(stmt, ir.VariantDeclStmt)

        symbol_name = stmt.name
        self.__register_def(stmt.stmt_id, symbol_name)

        # payload
        self.__enter_scope()
        if stmt.payload is not None:
            self._process_block(stmt.payload, {
                ir.VariableDeclStmt: self.__variable_decl,
            })
        self.__exit_scope()

    def __if_stmt(self, stmt: ir.GIRStmt):
        assert isinstance(stmt, ir.IfStmt)

        # condition
        self.__use_expr(stmt.stmt_id, stmt.condition)

        # then branch
        self.__enter_scope()
        self._process_block(stmt.then_body, self._code_block_handlers)
        self.__exit_scope()

        # else branch
        if stmt.else_body is not None:
            self.__enter_scope()
            self._process_block(stmt.else_body, self._code_block_handlers)
            self.__exit_scope()

    def __for_stmt(self, stmt: ir.GIRStmt):
        assert isinstance(stmt, ir.ForStmt)

        self.__enter_scope()

        # init body
        if stmt.init_body is not None:
            self._process_block(stmt.init_body, self._code_block_handlers)

        # condition prebody
        if stmt.condition_prebody is not None:
            self._process_block(stmt.condition_prebody, self._code_block_handlers)

        # condition
        self.__use_expr(stmt.stmt_id, stmt.condition)

        # body
        self.__enter_scope()
        self._process_block(stmt.body, self._code_block_handlers)
        self.__exit_scope()

        # update body
        if stmt.update_body is not None:
            self._process_block(stmt.update_body, self._code_block_handlers)

        self.__exit_scope()

    def __forin_stmt(self, stmt: ir.GIRStmt):
        assert isinstance(stmt, ir.ForInStmt)

        self.__enter_scope()

        # iterable expression
        self.__use_expr(stmt.stmt_id, stmt.iterable)

        # loop variable
        symbol_name = stmt.iterator
        self.__register_def(stmt.stmt_id, symbol_name)

        # body
        self.__enter_scope()
        self._process_block(stmt.body, self._code_block_handlers)
        self.__exit_scope()

        self.__exit_scope()

    def __loop_stmt(self, stmt: ir.GIRStmt):
        assert isinstance(stmt, ir.LoopStmt)

        # body
        self.__enter_scope()
        self._process_block(stmt.body, self._code_block_handlers)
        self.__exit_scope()

    def __switch_stmt(self, stmt: ir.GIRStmt):
        assert isinstance(stmt, ir.SwitchStmt)

        # condition
        self.__use_expr(stmt.stmt_id, stmt.condition)

        # body
        self._process_block(stmt.body, {
            ir.CaseStmt: self.__case_stmt,
            ir.DefaultStmt: self.__default_stmt,
        })

    def __case_stmt(self, stmt: ir.GIRStmt):
        assert isinstance(stmt, ir.CaseStmt)

        # case value will be looked up in enum namespace

        # body
        self.__enter_scope()

        if stmt.payload is not None:
            symbol_name = stmt.payload
            self.__register_def(stmt.stmt_id, symbol_name)

        self._process_block(stmt.body, self._code_block_handlers)

        self.__exit_scope()

    def __default_stmt(self, stmt: ir.GIRStmt):
        assert isinstance(stmt, ir.DefaultStmt)

        # body
        self.__enter_scope()
        if stmt.body is not None:
            self._process_block(stmt.body, self._code_block_handlers)
        self.__exit_scope()

    def __block(self, stmt: ir.GIRStmt):
        assert isinstance(stmt, ir.BlockStmt)

        self.__enter_scope()
        self._process_block(stmt.stmt_id, self._code_block_handlers)
        self.__exit_scope()

    def __return_stmt(self, stmt: ir.GIRStmt):
        assert isinstance(stmt, ir.ReturnStmt)

        if stmt.value is not None:
            self.__use_expr(stmt.stmt_id, stmt.value)

    def __call_stmt(self, stmt: ir.GIRStmt):
        assert isinstance(stmt, ir.CallStmt)

        # callee can be expr or type
        # for method call with explicit receiver, the callee is the method name and should be looked up in the method namespace
        if stmt.receiver is None:
            self.__use_expr(stmt.stmt_id, stmt.name)
            self.__use_type(stmt.stmt_id, stmt.name)

        # type arguments
        for type_arg in stmt.type_arguments:
            self.__use_type(stmt.stmt_id, type_arg)

        # target
        if stmt.target is not None:
            self.__def_or_use(stmt.stmt_id, stmt.target)

        # arguments
        for arg in stmt.positional_arguments:
            self.__use_expr(stmt.stmt_id, arg)
        for arg in stmt.named_arguments.values():
            self.__use_expr(stmt.stmt_id, arg)

        # receiver can be expr or type
        if stmt.receiver is not None:
            self.__use_expr(stmt.stmt_id, stmt.receiver)
            self.__use_type(stmt.stmt_id, stmt.receiver)

    def __assign_stmt(self, stmt: ir.GIRStmt):
        assert isinstance(stmt, ir.AssignStmt)

        # target
        self.__def_or_use(stmt.stmt_id, stmt.target)

        # lhs
        self.__use_expr(stmt.stmt_id, stmt.lhs)
        if stmt.operator == Operator.Dot:  # for dot operator, lhs is expected to be a type or expr
            self.__use_type(stmt.stmt_id, stmt.lhs)

        # rhs
        # for dot operator, rhs is not a symbol usage but a field name, so we should not register it as symbol usage
        if stmt.rhs is not None and stmt.operator != Operator.Dot:
            self.__use_expr(stmt.stmt_id, stmt.rhs)

    def __assert_stmt(self, stmt: ir.GIRStmt):
        assert isinstance(stmt, ir.AssertStmt)

        # condition
        self.__use_expr(stmt.stmt_id, stmt.condition)

        # message
        self.__use_expr(stmt.stmt_id, stmt.message)

    def __del_stmt(self, stmt: ir.GIRStmt):
        assert isinstance(stmt, ir.DeleteStmt)

        # target
        self.__use_expr(stmt.stmt_id, stmt.target)

    def __new_object(self, stmt: ir.GIRStmt):
        assert isinstance(stmt, ir.NewObjectStmt)

        # type
        if stmt.data_type is not None:
            self.__use_type(stmt.stmt_id, stmt.data_type)

        # init value
        if stmt.init_value is not None:
            self.__use_expr(stmt.stmt_id, stmt.init_value)

        # target
        self.__def_or_use(stmt.stmt_id, stmt.target)

    def __new_array(self, stmt: ir.GIRStmt):
        assert isinstance(stmt, ir.NewArrayStmt)

        # element type
        self.__use_type(stmt.stmt_id, stmt.data_type)

        # length
        self.__use_expr(stmt.stmt_id, stmt.length)

        # target
        self.__def_or_use(stmt.stmt_id, stmt.target)
