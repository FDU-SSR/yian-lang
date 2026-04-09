from typing import TypeVar, cast

from compiler.analysis.semantic_analysis.utils.cgir_builder import CGIRBuilder
from compiler.analysis.semantic_analysis.utils.operation_checker import OperationChecker, OperationResult
from compiler.analysis.semantic_analysis.utils.scope_manager import (CustomTypeDeclScope, FunctionDeclScope,
                                                                     ImplDeclScope, MethodDeclScope, ScopeManager,
                                                                     TraitDeclScope)
from compiler.analysis.semantic_analysis.utils.type_parser import TypeParser
from compiler.analysis.semantic_analysis.utils.value_parser import ValueParser
from compiler.backend.utils.context import LLVMCtx
from compiler.config.constants import NO_PATH, NO_STMT_ID, IntrinsicTrait
from compiler.config.defs import IRHandlerMap, StmtId, SymbolId, TypeId, UnitId
from compiler.unit_data import UnitData
from compiler.utils import IR, ty
from compiler.utils.errors import CompilerError, ErrorReporter, NameResolutionError, YianSyntaxError, YianTypeError
from compiler.utils.IR import DefPoint, TypedValue
from compiler.utils.IR import cgir as cir
from compiler.utils.IR import gir as ir
from compiler.utils.ty import Impl, MethodRegistry, TypeSpace


class SemanticCtx:
    def __init__(
        self,
        type_space: TypeSpace,
        method_registry: MethodRegistry,
        unit_datas: dict[UnitId, UnitData],
        def_points: set[DefPoint],
        max_gir_id: StmtId,
    ):
        self.__space = type_space
        self.__method_registry = method_registry
        self.__unit_datas = unit_datas
        self.__def_points = def_points

        self.__def_point_registry: dict[tuple[UnitId, StmtId, str, TypeId], DefPoint] = {}

        self.__scope_manager = ScopeManager(type_space, method_registry)
        self.__value_manager = ValueParser(self.__scope_manager, type_space)
        self.__operation_checker = OperationChecker(type_space, method_registry)
        self.__type_parser = TypeParser(type_space, self.__scope_manager, self.__unit_datas)
        self.__cgir_builder = CGIRBuilder(type_space, max_gir_id, self.symbol_register_def, self.symbol_register, self.cgir_emit)
        self.__unit_data: UnitData | None = None
        self.__def_point: DefPoint | None = None
        self.__error_reporter = ErrorReporter(NO_PATH)

        self.__symbol_id_counter = 0

    def into_llvm_ctx(self) -> LLVMCtx:
        return LLVMCtx(
            type_space=self.__space,
            method_registry=self.__method_registry,
            unit_datas=self.__unit_datas,
        )

    def record_processed_def_points(self, def_points: set[DefPoint]):
        self.__def_points.update(def_points)

    def process_gir_block(self, block_id: StmtId, handlers: IRHandlerMap[ir.GIRStmt]):
        if self.__unit_data is None:
            raise CompilerError("Unit data is not set")
        block_stmt = self.__unit_data.get(block_id).expect_block()
        for stmt_id in block_stmt.body:
            stmt = self.__unit_data.get(stmt_id)
            try:
                if type(stmt) in handlers:
                    handlers[type(stmt)](stmt)
                else:
                    raise CompilerError(f"No handler for statement type: {type(stmt)}")
            except Exception as e:
                self.__error_reporter.report(stmt, e)

    def process_cgir_block(self, block_id: StmtId, handlers: IRHandlerMap[cir.CheckedGIR]):
        if self.__def_point is None:
            raise CompilerError("Def point is not set")
        if self.__unit_data is None:
            raise CompilerError("Unit data is not set")
        block_stmt = self.__def_point.get(block_id).expect_block()
        for stmt_id in block_stmt.statements:
            stmt = self.__def_point.get(stmt_id)
            try:
                if type(stmt) in handlers:
                    handlers[type(stmt)](stmt)
                else:
                    raise CompilerError(f"No handler for statement type: {type(stmt)}")
            except Exception as e:
                self.__error_reporter.report(stmt, e)

    @property
    def is_global_scope(self):
        return self.__scope_manager.is_global_scope

    @property
    def unit_id(self) -> UnitId:
        if self.__unit_data is None:
            raise CompilerError("Unit data is not set")
        return self.__unit_data.unit_id

    @property
    def unit_name(self) -> str:
        if self.__unit_data is None:
            raise CompilerError("Unit data is not set")
        return self.__unit_data.unit_name

    def set_unit_data(self, unit_data: UnitData):
        if self.__unit_data is not None:
            raise CompilerError("Unit data is already set")
        self.__unit_data = unit_data
        self.__error_reporter.set_path(unit_data.original_path)

    @property
    def is_lib_module(self) -> bool:
        if self.__unit_data is None:
            raise CompilerError("Unit data is not set")
        return self.__unit_data.is_lib_module

    def set_unit_data_by_id(self, unit_id: UnitId):
        if unit_id not in self.__unit_datas:
            raise CompilerError(f"Unit data for unit ID {unit_id} not found")
        self.set_unit_data(self.__unit_datas[unit_id])

    def unset_unit_data(self):
        if self.__unit_data is None:
            raise CompilerError("Unit data is not set")
        self.__unit_data = None
        self.__error_reporter.set_path(NO_PATH)

    def set_def_point(self, def_point: DefPoint):
        if self.__def_point is not None:
            raise CompilerError("Def point is already set")
        self.__def_point = def_point

    def unset_def_point(self):
        if self.__def_point is None:
            raise CompilerError("Def point is not set")
        self.__def_point = None

    def enter_function_scope(self, func_ty: TypeId):
        self.__scope_manager.push(FunctionDeclScope(func_ty))

    def enter_method_scope(self, method_ty: TypeId):
        self.__scope_manager.push(MethodDeclScope(method_ty))

    def enter_trait_scope(self, trait_ty: TypeId):
        self.__scope_manager.push(TraitDeclScope(trait_ty))

    def enter_custom_type_scope(self, type_id: TypeId):
        self.__scope_manager.push(CustomTypeDeclScope(type_id))

    def enter_impl_scope(self, stmt_id: StmtId):
        self.__scope_manager.push(ImplDeclScope(stmt_id))

    def exit_scope(self):
        self.__scope_manager.pop()

    T = TypeVar("T")

    def symbol_get_imported(self, paths: list[str], symbol_name: str) -> IR.Symbol:
        """
        Find and return the imported symbol by its name and import paths.
        """
        if self.__unit_data is None:
            raise CompilerError("Unit data is not set")

        is_std_import = len(paths) > 0 and paths[0] == "std"

        for unit_id, unit_data in self.__unit_datas.items():
            if unit_id == self.__unit_data.unit_id:
                continue  # skip self

            if is_std_import:
                if unit_data.unit_name != paths[-1]:
                    continue

                expected_suffix = ["lib"] + paths[1:-1]
                if len(unit_data.unit_path) < len(expected_suffix):
                    continue

                if unit_data.unit_path[-len(expected_suffix):] != expected_suffix:
                    continue

                return unit_data.symbol_get_exported(symbol_name)
            else:
                relative_path = self.__unit_data.relative_path_to(unit_data.unit_path)
                if relative_path + [unit_data.unit_name] != paths:
                    continue  # skip unmatched paths

                return unit_data.symbol_get_exported(symbol_name)

        raise NameResolutionError(f"Cannot resolve imported symbol '{symbol_name}' from paths: {'::'.join(paths)}")

    def symbol_register_def(self, stmt_id: StmtId, name: str) -> SymbolId:
        """
        Register a symbol ID with a given statement ID and name.
        """
        symbol_id = self.__symbol_id_counter
        self.__symbol_id_counter += 1

        if self.__def_point is None:
            assert self.__unit_data is not None
            self.__unit_data.symbol_register_def(stmt_id, name, symbol_id)

        return symbol_id

    def symbol_register_use(self, stmt_id: StmtId, name: str, symbol_id: SymbolId) -> None:
        """
        Register a symbol usage with a given statement ID and name.
        """
        if self.__unit_data is None:
            raise CompilerError("Unit data is not set")
        self.__unit_data.symbol_register_usage(stmt_id, name, symbol_id)

    def symbol_export(self, symbol_id: SymbolId) -> None:
        if self.__unit_data is None:
            raise CompilerError("Unit data is not set")
        self.__unit_data.symbol_export(symbol_id)

    def unit_is_lib_module(self, unit_id: UnitId) -> bool:
        if unit_id not in self.__unit_datas:
            raise CompilerError(f"Unit data for unit ID {unit_id} not found")
        return self.__unit_datas[unit_id].is_lib_module

    def symbol_get(self, symbol_id: SymbolId) -> IR.Symbol:
        if self.__unit_data is None:
            raise CompilerError("Unit data is not set")
        return self.__unit_data.symbol_get(symbol_id)

    def symbol_lookup(self, stmt_id: StmtId, name: str) -> IR.Symbol:
        """
        Given a symbol name and the statement ID where it's used, look up the symbol definition and return the corresponding symbol.
        """
        if self.__unit_data is None:
            raise CompilerError("Unit data is not set")
        symbol = self.__unit_data.symbol_lookup(stmt_id, name)

        if self.__def_point is not None and symbol.symbol_id in self.__def_point.symbol_table:
            return self.__def_point.get_symbol(symbol.symbol_id)
        return symbol

    def symbol_lookup_def(self, stmt_id: StmtId, name: str) -> SymbolId:
        """
        Given a symbol name and the statement ID where it's defined, return the symbol ID of the definition.
        """
        if self.__unit_data is None:
            raise CompilerError("Unit data is not set")
        return self.__unit_data.symbol_lookup_def(stmt_id, name)

    def symbol_is_inplace_defined(self, stmt_id: StmtId, name: str) -> bool:
        """
        Check if a symbol is defined in-place in the current stmt.
        """
        if self.__unit_data is None:
            raise CompilerError("Unit data is not set")
        return self.__unit_data.symbol_is_inplace_defined(stmt_id, name)

    def symbol_register(self, symbol: IR.Symbol):
        if self.__unit_data is None:
            raise CompilerError("Unit data is not set")
        self.__unit_data.symbol_register(symbol)

        if self.__def_point is not None:
            self.__def_point.register_symbol(symbol)

    @property
    def ty_current_procedure(self) -> TypeId:
        """
        Get the type ID of the current procedure (function or method) in scope.
        """
        return self.__scope_manager.procedure_type

    def ty_instantiate(self, ty_id: TypeId, substs: dict[TypeId, TypeId]) -> TypeId:
        return self.__space.instantiate(ty_id, substs)

    def ty_formatter(self, type_id: TypeId) -> str:
        return self.__space.get_name(type_id)

    def ty_resolve_def_point(self, type_id: TypeId) -> DefPoint:
        ty_def = self.__space[type_id]

        def_point = None
        match ty_def:
            case ty.FunctionType():
                func_unit = self.__unit_datas[ty_def.function_def.unit_id]
                func_stmt = func_unit.get(ty_def.function_def.stmt_id).expect_function_decl()
                def_point = DefPoint(ty_def.function_def.unit_id, ty_def.function_def.stmt_id, ty_def.function_def.name, type_id, func_stmt.body)

                key = (def_point.unit_id, def_point.stmt_id, def_point.procedure_name, def_point.type_id)
                if key in self.__def_point_registry:
                    return self.__def_point_registry[key]

                for param in ty_def.function_def.parameters:
                    def_point.register_symbol(IR.VariableSymbol(param.stmt_id, param.name, param.type_id))

            case ty.MethodType():
                method_unit = self.__unit_datas[ty_def.method_def.unit_id]
                method_stmt = method_unit.get(ty_def.method_def.stmt_id).expect_method_decl()
                def_point = DefPoint(ty_def.method_def.unit_id, ty_def.method_def.stmt_id, ty_def.method_def.name, type_id, method_stmt.body)

                key = (def_point.unit_id, def_point.stmt_id, def_point.procedure_name, def_point.type_id)
                if key in self.__def_point_registry:
                    return self.__def_point_registry[key]

                for param in ty_def.method_def.parameters:
                    def_point.register_symbol(IR.VariableSymbol(param.stmt_id, param.name, param.type_id))

            case _:
                raise CompilerError(f"Type ID {type_id} is not a function or method type")

        return self.register_def_point(def_point)

    def register_def_point(self, def_point: DefPoint) -> DefPoint:
        key = (def_point.unit_id, def_point.stmt_id, def_point.procedure_name, def_point.type_id)
        if key in self.__def_point_registry:
            return self.__def_point_registry[key]
        self.__def_point_registry[key] = def_point
        return def_point

    @property
    def def_points(self) -> list[DefPoint]:
        return list(self.__def_point_registry.values())

    def ty_get(self, type_id: TypeId) -> ty.YianType:
        return self.__space[type_id]

    def ty_impl_get(self, impl_id: StmtId) -> Impl:
        return self.__method_registry[impl_id]

    def ty_assignable_check(self, target_type: TypeId, source_value: IR.TypedValue) -> None:
        """
        Check if the source value can be assigned to the target type.
        """
        self.__operation_checker.assignable_check(target_type, source_value)

    def ty_binary_op_check(self, op: IR.Operator, left: IR.TypedValue, right: IR.TypedValue) -> OperationResult:
        """
        Given a binary operator and operand values, perform type checking.

        1. Check if the operator is valid for the given operand types.
        2. Return the resulting type after applying the operator.
        3. If the operator is overloaded, return the method type ID used for the operation.
        """
        return self.__operation_checker.binary_op(op, left, right)

    def ty_unary_op_check(self, op: IR.Operator, operand: IR.TypedValue) -> OperationResult:
        """
        Given a unary operator and operand value, perform type checking.

        1. Check if the operator is valid for the given operand type.
        2. Return the resulting type after applying the operator.
        3. If the operator is overloaded, return the method type ID used for the operation.
        """
        return self.__operation_checker.unary_op(op, operand)

    def ty_delete_check(self, target: IR.TypedValue) -> TypeId | None:
        """
        Given a target value, perform type checking for deletion.

        1. Check if the target can be deleted.
        2. Return the type ID of the `delete` method if `del` is overloaded for the target type.
        """
        target_type_id = target.type_id
        type_def = self.ty_get(target_type_id)

        if isinstance(type_def, ty.PointerType):
            return None

        try:
            lookup_result = self.ty_method_lookup(
                target,
                "delete",
                None,
                []
            )
        except Exception as e:
            raise YianTypeError(f"Type '{self.ty_formatter(target_type_id)}' does not support delete trait: {e}")

        return lookup_result.method_id

    def ty_method_lookup(
        self,
        caller: TypedValue,
        method_name: str,
        generic_args: list[TypeId] | None,
        arg_values: list[IR.TypedValue],
    ) -> ty.LookupResult:
        """
        Lookup a method for a given caller type. See details in `manual/impl.md`.
        """
        return self.__method_registry.method_lookup(
            caller,
            method_name,
            generic_args,
            arg_values,
            self.__operation_checker.assignable_check,
        )

    def ty_static_method_lookup(
        self,
        type_id: TypeId,
        method_name: str,
        generic_args: list[TypeId] | None,
        arg_values: list[IR.TypedValue],
    ) -> ty.LookupResult:
        """
        Lookup a static method for a given type. See details in `manual/impl.md`.
        """
        return self.__method_registry.static_method_lookup(
            type_id,
            method_name,
            generic_args,
            arg_values,
            self.__operation_checker.assignable_check,
        )

    def ty_func_call_check(
        self,
        func_type: TypeId,
        generic_args: list[TypeId] | None,
        positional_arg_values: list[IR.TypedValue],
        named_arg_values: dict[str, IR.TypedValue],
    ) -> tuple[TypeId, list[IR.TypedValue]]:
        """
        Given a function type and argument values, perform type checking and return function type.

        1. Check if the function can be called with the given arguments.
        2. If generic arguments are provided, instantiate the function type accordingly.
        3. Return the resulting function type and argument values after type checking.
        """
        # TODO：named_args将被移除，只允许positional_args，因此不做相关检查
        ty_def = self.ty_get(func_type).expect_function()
        generic_params = ty_def.function_def.generics

        if generic_args is not None:
            if len(generic_params) != len(generic_args):
                raise CompilerError(f"Function '{ty_def.name}' expects {len(generic_params)} generic arguments, but got {len(generic_args)}")
            func_type = self.ty_instantiate(func_type, dict(zip(generic_params, generic_args)))
            ty_def = self.ty_get(func_type).expect_function()
        else:
            if len(generic_params) > 0:
                if len(positional_arg_values) != len(ty_def.function_def.parameters):
                    raise YianTypeError(f"Function '{ty_def.name}' expects {len(ty_def.function_def.parameters)} arguments, but got {len(positional_arg_values)}")

                generic_mapping = ty.type_unification(
                    self.__space,
                    [arg for arg in positional_arg_values],
                    [param.type_id for param in ty_def.function_def.parameters]
                )
                generic_args = [generic_mapping.get(gen_id, gen_id) for gen_id in generic_params]
                func_type = self.ty_instantiate(func_type, dict(zip(generic_params, generic_args)))
                ty_def = self.ty_get(func_type).expect_function()

        if len(positional_arg_values) != len(ty_def.function_def.parameters):
            raise YianTypeError(f"Function '{ty_def.name}' expects {len(ty_def.function_def.parameters)} arguments, but got {len(positional_arg_values)}")

        param_types = ty_def.parameter_types(self.ty_instantiate)
        for param_ty, arg in zip(param_types, positional_arg_values):
            self.ty_assignable_check(param_ty, arg)

        return func_type, positional_arg_values

    def ty_invoke_check(
        self,
        invokable_type: TypeId,
        positional_arg_values: list[IR.TypedValue],
    ) -> None:
        """
        Given an invokable type(function pointer) and argument values, perform type checking for invocation.

        1. Check if the invokable can be invoked with the given arguments.
        """
        ty_def = self.ty_get(invokable_type).expect_function_pointer()

        if len(positional_arg_values) != len(ty_def.parameter_types):
            raise YianTypeError(
                f"Invokable expects {len(ty_def.parameter_types)} arguments, but got {len(positional_arg_values)}"
            )

        for param_ty, arg in zip(ty_def.parameter_types, positional_arg_values):
            self.ty_assignable_check(param_ty, arg)

    def ty_struct_construct(
        self,
        struct_type: TypeId,
        positional_arg_values: list[IR.TypedValue],
        named_arg_values: dict[str, IR.TypedValue],
    ) -> tuple[TypeId, dict[str, IR.TypedValue]]:
        """
        Given a struct type and argument values, perform type checking for struct construction.

        1. Check if the struct can be constructed with the given arguments.
        2. If the struct is generic and not instantiated, infer generic arguments from provided field values.
        3. Return the instantiated struct type id (possibly unchanged) and a mapping from field names to their corresponding typed values.
        """
        ty_def = self.ty_get(struct_type).expect_struct()
        struct_def = ty_def.struct_def

        if len(positional_arg_values) + len(named_arg_values) != len(struct_def.fields):
            raise YianTypeError(f"Struct '{ty_def.name}' has {len(struct_def.fields)} fields, but got {len(positional_arg_values) + len(named_arg_values)} arguments")

        fields = sorted(struct_def.fields.values(), key=lambda f: f.index)
        field_names = [f.name for f in fields]
        field_values: dict[str, IR.TypedValue | None] = {name: None for name in field_names}

        for name, arg in named_arg_values.items():
            if name not in struct_def.fields:
                raise YianTypeError(f"Unknown field '{name}' in struct '{ty_def.name}'")
            field_values[name] = arg

        for i, arg in enumerate(positional_arg_values):
            field_name = field_names[i]
            if field_values[field_name] is not None:
                raise YianTypeError(f"Field '{field_name}' is specified multiple times in struct '{ty_def.name}'")
            field_values[field_name] = arg

        completed_field_values = cast(dict[str, IR.TypedValue], field_values)

        # Generic inference
        generic_params = struct_def.generics
        is_uninstantiated_template = len(generic_params) > 0 and ty_def.generic_args == generic_params
        if is_uninstantiated_template:
            try:
                generic_mapping = ty.type_unification(
                    self.__space,
                    [completed_field_values[name] for name in field_names],
                    [struct_def.fields[name].type_id for name in field_names]
                )
                inferred_args = [generic_mapping.get(gen_id, gen_id) for gen_id in generic_params]
                struct_type = self.ty_instantiate(struct_type, dict(zip(generic_params, inferred_args)))
                ty_def = self.ty_get(struct_type).expect_struct()
            except CompilerError as e:
                raise YianTypeError(f"Cannot infer type arguments for generic struct '{ty_def.name}': {e}")

        # Type check
        for name in field_names:
            inst_field = ty_def.get_field_by_name(name, self.ty_instantiate)
            assert inst_field is not None
            self.ty_assignable_check(inst_field.type_id, completed_field_values[name])

        return struct_type, completed_field_values

    def ty_struct_field_access(
        self,
        struct_type: TypeId,
        field_name: str,
    ) -> tuple[int, TypeId]:
        """
        Access the field of a struct variable via field_name

        1. If receiver is a struct, access its field directly
        2. If receiver is a pointer, deref it and access its field

        Return the dereference level needed and the actual struct type.
        """
        type_def = self.ty_get(struct_type)
        if isinstance(type_def, ty.PointerType):
            deref_level, actual_struct_type = self.ty_struct_field_access(
                type_def.pointee_type, field_name
            )
            return deref_level + 1, actual_struct_type

        if isinstance(type_def, ty.StructType):
            if not type_def.has_field(field_name):
                raise YianTypeError.member_not_found(struct_type, field_name, self.ty_formatter)
            return 0, struct_type

        raise YianTypeError.member_not_found(struct_type, field_name, self.ty_formatter)

    def ty_basic_type_conversion_check(self, from_value: IR.TypedValue, to_type: TypeId) -> None:
        """
        Check if a basic type can be converted to another basic type. Consider two cases:

        1. `from_value` is literal value: check if the literal can be represented in `to_type`.
        2. `from_value` is non-literal value: check if `from_value`'s type can be converted to `to_type`.
        """
        to_basic_type = self.__space[to_type].expect_basic()

        # int/float literal conversion check
        match from_value:
            case IR.IntegerLiteral():
                if isinstance(to_basic_type, ty.IntType):
                    min_val, max_val = to_basic_type.value_range
                    if not (min_val <= from_value.value <= max_val):
                        raise YianTypeError(f"Integer literal value {from_value.value} out of range for type '{self.ty_formatter(to_type)}'")
                elif isinstance(to_basic_type, ty.FloatType):
                    pass  # all integer literals are convertible to float types
                elif isinstance(to_basic_type, ty.CharType):
                    # only u8/u16/u32 can be converted to char
                    if not (0 <= from_value.value <= 0x10FFFF):
                        raise YianTypeError(f"Integer literal value {from_value.value} out of range for type 'char'")
                else:
                    raise YianTypeError.mismatch(to_type, from_value.type_id, self.ty_formatter)
                return
            case IR.FloatLiteral():
                if isinstance(to_basic_type, (ty.IntType, ty.FloatType)):
                    pass  # all float literals are convertible to int/float types
                else:
                    raise YianTypeError.mismatch(to_type, from_value.type_id, self.ty_formatter)
                return
            case IR.ArrayLiteral():
                raise YianSyntaxError("Array literal cannot be converted to basic types")
            case IR.TupleLiteral():
                raise YianSyntaxError("Tuple literal cannot be converted to basic types")
            case _:
                pass  # other literals use type_id for checking

        if not self.__space.basic_type_convertible(from_value.type_id, to_type):
            raise YianTypeError.mismatch(to_type, from_value.type_id, self.ty_formatter)

    def ty_deref(self, pointer_type: TypeId) -> TypeId:
        """
        Dereference a type. The type can be:
        1. A pointer type - returns the pointee type
        2. A struct that implements the Deref trait - returns the return type of the deref method
        """
        type_def = self.ty_get(pointer_type)

        # Check if it's a pointer type
        if isinstance(type_def, ty.PointerType):
            return type_def.pointee_type

        # Check if it implements the Deref trait
        method_id = self.__method_registry.trait_method_lookup(
            IntrinsicTrait.Deref,
            pointer_type,
            "deref",
            [],
            self.__operation_checker.assignable_check
        )
        if method_id is not None:
            method_ty = self.ty_get(method_id).expect_method()
            return_type = method_ty.return_type(self.ty_instantiate)
            return return_type

        # Cannot dereference
        raise YianTypeError(f"Cannot dereference type '{self.ty_formatter(pointer_type)}'")

    def ty_alloc_generic(self, name: str, index: int, decl_stmt_id: StmtId) -> TypeId:
        return self.__space.alloc_generic(name, index, decl_stmt_id)

    def ty_alloc_pointer(self, base_type: TypeId) -> TypeId:
        return self.__space.alloc_pointer(base_type)

    def ty_alloc_function(self, unit_id: UnitId, stmt_id: StmtId, name: str, generics: list[str]) -> TypeId:
        return self.__space.alloc_function(unit_id, stmt_id, name, generics)

    def ty_alloc_method(self, unit_id: UnitId, stmt_id: StmtId, name: str) -> TypeId:
        return self.__space.alloc_method(unit_id, stmt_id, name)

    def ty_alloc_trait(self, unit_id: UnitId, stmt_id: StmtId, name: str, generics: list[str]) -> TypeId:
        return self.__space.alloc_trait(unit_id, stmt_id, name, generics)

    def ty_alloc_struct(self, unit_id: UnitId, stmt_id: StmtId, name: str, generics: list[str] = []) -> TypeId:
        return self.__space.alloc_struct(unit_id, stmt_id, name, generics)

    def ty_alloc_enum(self, unit_id: UnitId, stmt_id: StmtId, name: str, generics: list[str]) -> TypeId:
        return self.__space.alloc_enum(unit_id, stmt_id, name, generics)

    def ty_alloc_impl(self, stmt_id: StmtId, generics: list[str]) -> Impl:
        return self.__method_registry.register_impl(stmt_id, generics)

    def ty_constructable_check(self, struct_type: TypeId) -> None:
        """
        Check if the struct type can be constructed directly in current scope.
        """
        ty_def = self.ty_get(struct_type).expect_struct()
        struct_def = ty_def.struct_def

        if self.__unit_data is None:
            raise CompilerError("Unit data is not set")

        owner_unit = struct_def.unit_id

        if self.__scope_manager.is_in_impl_of(struct_type):
            return

        if struct_def.is_private or struct_def.contains_private_field:
            if owner_unit != self.__unit_data.unit_id:
                raise YianTypeError(f"Struct '{struct_def.name}' is not constructable outside its defining unit")

    def ty_cache_impls(self):
        self.__method_registry.cache_impls()

    def ty_trait_method_to_impl(self, trait_method_type: TypeId, new_target_type: TypeId, impl: Impl) -> TypeId:
        """
        Given a trait method type and a new target type

        1. Copy the trait method and change its target type to the new target type.
        2. Return the new method type ID.
        3. If the trait method has no body (is abstract), raise error.
        """
        trait_method_ty = self.ty_get(trait_method_type).expect_method()
        old_method_def = trait_method_ty.method_def

        if old_method_def.is_header:
            raise YianTypeError(f"Trait method '{old_method_def.name}' has no body")

        new_method_type = self.__space.alloc_method(old_method_def.unit_id, old_method_def.stmt_id, trait_method_ty.name)
        new_method_ty = self.ty_get(new_method_type).expect_method()
        new_method_def = new_method_ty.method_def

        new_method_def.generics = old_method_def.generics.copy() + impl.generics.copy()
        # new_method_def.generics = impl.generics.copy()
        new_method_def.attributes = old_method_def.attributes.copy()
        new_method_def.receiver_type = new_target_type
        new_method_def.parameters = old_method_def.parameters.copy()
        new_method_def.return_type = old_method_def.return_type
        new_method_def.is_header = False

        new_method_ty.def_id = new_method_type
        new_method_ty.generic_args = trait_method_ty.generic_args.copy() + impl.generics.copy()
        # new_method_ty.generic_args = impl.generics.copy()
        new_method_ty.method_def = new_method_def

        return new_method_type

    def ty_query_impl(self, callable_type_id: TypeId) -> StmtId:
        return self.__method_registry.query_impl(callable_type_id)

    def ty_query_trait(self, callable_type_id: TypeId) -> TypeId | None:
        return self.__method_registry.query_trait(callable_type_id)

    def ty_finalize(self):
        self.__space.finalize()

    def gir_get(self, stmt_id: StmtId) -> ir.GIRStmt:
        if self.__unit_data is None:
            raise CompilerError("Unit data is not set")
        return self.__unit_data.get(stmt_id)

    def cgir_emit(self, stmt: cir.CheckedGIR) -> None:
        """
        Emit a CGIR statement to the unit data.

        If the `stmt_id` of the statement is not set, it will be assigned a new ID.
        """
        if self.__unit_data is None:
            raise CompilerError("Unit data is not set")
        if self.__def_point is None:
            raise CompilerError("Def point is not set")
        if stmt.stmt_id == NO_STMT_ID:
            stmt.metadata.stmt_id = self.__cgir_builder.new_stmt_id()
        self.__def_point.emit(stmt)

    def cgir_contains(self, stmt_id: StmtId) -> bool:
        if self.__def_point is None:
            raise CompilerError("Def point is not set")
        return self.__def_point.contains(stmt_id)

    def cgir_build_binary_op(self, stmt: ir.GIRStmt, op: IR.Operator, lhs: IR.TypedValue, rhs: IR.TypedValue, target_type: TypeId, emit: bool = True) -> cir.BinaryOpAssign:
        return self.__cgir_builder.build_binary_op(stmt, op, lhs, rhs, target_type, emit)

    def cgir_build_unary_op(self, stmt: ir.GIRStmt, op: IR.Operator, operand: IR.TypedValue, target_type: TypeId, emit: bool = True) -> cir.UnaryOpAssign:
        return self.__cgir_builder.build_unary_op(stmt, op, operand, target_type, emit)

    def cgir_build_field_access(self, stmt: ir.GIRStmt, receiver: IR.TypedValue, field: ty.StructField, emit: bool = True) -> cir.FieldAccess:
        return self.__cgir_builder.build_field_access(stmt, receiver, field, emit)

    def cgir_build_assign(self, stmt: ir.GIRStmt, value: IR.TypedValue, target: IR.Variable, emit: bool = True) -> cir.Assign:
        return self.__cgir_builder.build_assign(stmt, value, target, emit)

    def cgir_build_method_call(self, stmt: ir.GIRStmt, receiver: IR.TypedValue, method: TypeId, arg_values: list[IR.TypedValue], target_type: TypeId | None, emit: bool = True) -> cir.MethodCall:
        return self.__cgir_builder.build_method_call(stmt, receiver, method, arg_values, target_type, emit)

    def cgir_build_struct_construct(self, stmt: ir.GIRStmt, struct_type: TypeId, field_values: dict[str, IR.TypedValue], emit: bool = True) -> cir.StructConstruct:
        return self.__cgir_builder.build_struct_construct(stmt, field_values, struct_type, emit)

    def cgir_build_if(self, stmt: ir.GIRStmt, condition: IR.TypedValue, then_stmts: list[cir.CheckedGIR], else_stmts: list[cir.CheckedGIR] | None, emit: bool = True) -> cir.If:
        return self.__cgir_builder.build_if(stmt, condition, then_stmts, else_stmts, emit)

    def cgir_build_panic(self, stmt: ir.GIRStmt, message: IR.TypedValue, emit: bool = True) -> cir.Panic:
        return self.__cgir_builder.build_panic(stmt, message, emit)

    def cgir_build_loop(self, stmt: ir.GIRStmt, body_stmts: list[cir.CheckedGIR], emit: bool = True) -> cir.Loop:
        return self.__cgir_builder.build_loop(stmt, body_stmts, emit)

    def cgir_build_match(self, stmt: ir.GIRStmt, match_value: IR.TypedValue, cases: list[cir.CheckedGIR], emit: bool = True) -> cir.Match:
        return self.__cgir_builder.build_match(stmt, match_value, cases, emit)

    def cgir_build_enum_case(self, stmt: ir.GIRStmt, case_values: list[str], body_stmts: list[cir.CheckedGIR], emit: bool = True) -> cir.EnumCase:
        return self.__cgir_builder.build_enum_case(stmt, case_values, body_stmts, emit)

    def cgir_build_enum_payload_case(self, stmt: ir.GIRStmt, case_value: str, payload: IR.Variable, body_stmts: list[cir.CheckedGIR], emit: bool = True) -> cir.EnumPayloadCase:
        return self.__cgir_builder.build_enum_payload_case(stmt, case_value, payload, body_stmts, emit)

    def cgir_build_break(self, stmt: ir.GIRStmt, emit: bool = True) -> cir.Break:
        return self.__cgir_builder.build_break(stmt, emit)

    def parse_type(self, stmt_id: StmtId, type_str: str) -> TypeId:
        """
        Parse type string to type id.

        If the type string is invalid, raise error.

        Args:
            stmt: The statement where the type string is located.
            type_str (str): The type string to parse.
        """
        if self.__unit_data is None:
            raise CompilerError("Unit data is not set for type parsing")

        type_str = type_str.strip()
        generic_dict = self.__scope_manager.generic_dict
        result, suffix = self.__type_parser.parse_type(stmt_id, type_str, generic_dict, self.__unit_data)
        if suffix != "":
            raise NameResolutionError(f"Invalid type string: {type_str}")
        return result

    def parse_value(self, stmt_id: StmtId, name: str) -> IR.TypedValue:
        """
        Parse a typed value by name in the context of the given statement.
        """
        if self.__unit_data is None:
            raise CompilerError("Unit data is not set for value parsing")
        return self.__value_manager.parse_value(stmt_id, name, self.__unit_data, self.__def_point)
