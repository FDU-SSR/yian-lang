from compiler.analysis.semantic_analysis.utils.analysis_pass import UnitPass
from compiler.config.defs import IRHandlerMap, TypeId
from compiler.utils import ty
from compiler.utils.errors import SemanticError, YianTypeError
from compiler.utils.IR import gir as ir
from compiler.utils.ty import InstantiatedType


class ImplValidator(UnitPass):
    @property
    def _code_block_handlers(self) -> IRHandlerMap[ir.GIRStmt]:
        return {}  # No need to dive into code blocks for impl validation

    @property
    def _top_level_handlers(self) -> IRHandlerMap[ir.GIRStmt]:
        return {
            ir.FunctionDeclStmt: lambda stmt: None,
            ir.VariableDeclStmt: lambda stmt: None,
            ir.ImportStmt: lambda stmt: None,
            ir.ImplementDeclStmt: self.__implement_decl,
            ir.TraitDeclStmt: lambda stmt: None,
            ir.StructDeclStmt: lambda stmt: None,
            ir.EnumDeclStmt: lambda stmt: None,
            ir.TypeAliasDeclStmt: lambda stmt: None,
        }

    def _run_prelude(self) -> None:
        pass

    def _run_postlude(self) -> None:
        self._ctx.ty_cache_impls()

    def _unit_prelude(self) -> None:
        pass

    def _unit_postlude(self) -> None:
        pass

    # ========= Helpers ==========

    def __type_unit_id(self, type_id: TypeId) -> int | None:
        type_def = self._ctx.ty_get(type_id)
        match type_def:
            case ty.StructType(struct_def=struct_def):
                return struct_def.unit_id
            case ty.EnumType(enum_def=enum_def):
                return enum_def.unit_id
            case ty.TraitType(trait_def=trait_def):
                return trait_def.unit_id
            case ty.MethodType(method_def=method_def):
                return method_def.unit_id
            case ty.FunctionType(function_def=function_def):
                return function_def.unit_id
            case _:
                return None

    def __is_user_defined_type_effective(self, type_id: TypeId, _seen: set[TypeId] | None = None) -> bool:
        if _seen is None:
            _seen = set()
        if type_id in _seen:
            return False
        _seen.add(type_id)

        unit_id = self.__type_unit_id(type_id)
        if unit_id is None:
            return False

        if not self._ctx.unit_is_lib_module(unit_id):
            return True

        type_def = self._ctx.ty_get(type_id)
        if isinstance(type_def, InstantiatedType):
            for arg in type_def.generic_args:
                flag = self.__is_user_defined_type_effective(arg, _seen)
                if flag:
                    return True
            return False

        return False

    def __is_user_defined_trait(self, trait_type_id: TypeId) -> bool:
        unit_id = self.__type_unit_id(trait_type_id)
        if unit_id is None:
            return False
        return not self._ctx.unit_is_lib_module(unit_id)

    # ========== Handlers ==========

    def __implement_decl(self, stmt: ir.GIRStmt):
        assert isinstance(stmt, ir.ImplementDeclStmt)

        impl = self._ctx.ty_impl_get(stmt.stmt_id)

        trait_type = impl.trait
        target_type = impl.target

        if not self._ctx.is_lib_module:
            target_is_user_effective = self.__is_user_defined_type_effective(target_type)

            if trait_type is None:
                if not target_is_user_effective:
                    raise SemanticError(
                        f"Cannot add inherent methods to standard library type '{self._ctx.ty_formatter(target_type)}'"
                    )
                return

            trait_is_user_effective = self.__is_user_defined_trait(trait_type)
            if not target_is_user_effective and not trait_is_user_effective:
                raise SemanticError(
                    f"Cannot implement standard library trait '{self._ctx.ty_formatter(trait_type)}' "
                    f"for standard library type '{self._ctx.ty_formatter(target_type)}' "
                )

        if trait_type is None:
            return

        trait_ty = self._ctx.ty_get(trait_type).expect_trait()

        trait_method_names = trait_ty.method_names
        implemented_names = set(impl.methods.keys())

        if implemented_names - trait_method_names:
            raise SemanticError(
                f"Impl block for '{self._ctx.ty_formatter(target_type)}' implements methods that are not defined in the trait '{self._ctx.ty_formatter(trait_type)}': {implemented_names - trait_method_names}"
            )

        unimplemented_names = trait_method_names - implemented_names

        for name in implemented_names:
            trait_method = trait_ty.get_method_by_name(name, self._ctx.ty_instantiate)
            impl_method = impl.for_name(name)
            self.__check_impl_method_signature(trait_type, target_type, trait_method, impl_method)

        for name in unimplemented_names:
            trait_method = trait_ty.get_method_by_name(name, self._ctx.ty_instantiate)
            trait_method_ty = self._ctx.ty_get(trait_method).expect_method()

            if trait_method_ty.method_def.is_header:
                raise SemanticError(
                    f"Trait method '{name}' has no default implementation and must be implemented for "
                    f"'{self._ctx.ty_formatter(target_type)}' (trait '{self._ctx.ty_formatter(trait_type)}')"
                )

            impl_method = self._ctx.ty_trait_method_to_impl(trait_method, target_type, impl)
            impl.add_method(name, impl_method)

    def __check_impl_method_signature(
        self,
        trait: TypeId,
        target: TypeId,
        trait_method: TypeId,
        impl_method: TypeId,
    ) -> None:
        """
        Check that the method defined in the impl matches the method defined in the trait.
        """
        trait_method_ty = self._ctx.ty_get(trait_method).expect_method()
        impl_method_ty = self._ctx.ty_get(impl_method).expect_method()
        impl_receiver_id = impl_method_ty.receiver_type(self._ctx.ty_instantiate)

        if impl_receiver_id != target:
            raise YianTypeError.mismatch(
                target,
                impl_receiver_id,
                self._ctx.ty_formatter,
            )
        if trait_method_ty.receiver_type(self._ctx.ty_instantiate) != trait:
            raise YianTypeError.mismatch(
                trait,
                trait_method_ty.receiver_type(self._ctx.ty_instantiate),
                self._ctx.ty_formatter,
            )
        if self.__resolve_trait_self_type(trait_method_ty.return_type(self._ctx.ty_instantiate), impl_receiver_id, trait) != impl_method_ty.return_type(self._ctx.ty_instantiate):
            raise YianTypeError.mismatch(
                self.__resolve_trait_self_type(trait_method_ty.return_type(self._ctx.ty_instantiate), impl_receiver_id, trait),
                impl_method_ty.return_type(self._ctx.ty_instantiate),
                self._ctx.ty_formatter,
            )

        if len(impl_method_ty.method_def.parameters) != len(trait_method_ty.method_def.parameters):
            raise YianTypeError(
                f"Parameter count mismatch: expected {len(trait_method_ty.method_def.parameters)}, got {len(impl_method_ty.method_def.parameters)}"
            )
        impl_params = impl_method_ty.parameter_types(self._ctx.ty_instantiate)
        trait_params = trait_method_ty.parameter_types(self._ctx.ty_instantiate)
        for impl_param, trait_param in zip(impl_params, trait_params):
            if isinstance(self._ctx.ty_get(impl_param), ty.GenericType) or isinstance(self._ctx.ty_get(trait_param), ty.GenericType):
                continue
            if impl_param != self.__resolve_trait_self_type(trait_param, impl_receiver_id, trait):
                raise YianTypeError.mismatch(
                    trait_param,
                    impl_param,
                    self._ctx.ty_formatter,
                )

        # if len(impl_method_ty.method_def.generics) != len(trait_method_ty.method_def.generics):
        #     raise YianTypeError(
        #         f"Method generics count mismatch: expected {len(trait_method_ty.method_def.generics)}, got {len(impl_method_ty.method_def.generics)}"
        #     )

    def __resolve_trait_self_type(self, ty_id: TypeId, impl_receiver_id: TypeId, trait_id: TypeId) -> TypeId:
        if ty_id != trait_id:
            return ty_id
        return impl_receiver_id
