from collections import defaultdict
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Callable

from compiler.config.constants import IntrinsicTrait
from compiler.config.defs import StmtId, TypeId
from compiler.utils.errors import CompilerError, SemanticError, YianTypeError
from compiler.utils.IR.typed_value import LiteralValue, TypedValue

from .impl import Impl
from .space import TypeSpace
from .utils import is_same_template, type_unification
from .yian_types import GenericType, PointerType

if TYPE_CHECKING:
    from compiler.utils import IR


@dataclass
class LookupResult:
    method_id: TypeId           # The found method type ID (instantiated if generics were involved)
    deref_levels: int           # Number of dereference levels needed to reach the target type
    impl: Impl                  # The impl block where the method was found


class MethodRegistry:
    """
    Registry for managing type-to-method and type-to-trait mappings.
    """

    def __init__(self, type_space: TypeSpace):
        self.__space = type_space

        self.__impls: dict[StmtId, Impl] = {}

        # impl blocks and impl for blocks are stored directly by mapping target type ID to the block
        self.__impl_blocks: dict[TypeId, list[StmtId]] = defaultdict(list)
        self.__impl_for_blocks: dict[TypeId, list[StmtId]] = defaultdict(list)
        # impl with generics can not be stored simply by mapping target type ID, so we store them in lists
        self.__generic_impl_blocks: list[StmtId] = []
        self.__generic_impl_for_blocks: list[StmtId] = []

    def register_impl(self, stmt_id: StmtId, generics: list[str]) -> Impl:
        generic_types = [self.__space.alloc_generic(name, index, stmt_id) for index, name in enumerate(generics)]

        impl = Impl(stmt_id)
        impl.generics = generic_types

        if stmt_id in self.__impls:
            raise CompilerError(f"Impl block with stmt ID {stmt_id} is already registered.")
        self.__impls[stmt_id] = impl
        return impl

    def cache_impls(self):
        """
        Cache the impl blocks for efficient lookup.
        """
        for stmt_id, impl in self.__impls.items():
            if len(impl.generics) == 0:
                if impl.trait is None:
                    # inherent impl block
                    self.__impl_blocks[impl.target].append(stmt_id)
                else:
                    # impl for block
                    self.__impl_for_blocks[impl.target].append(stmt_id)
            else:
                if impl.trait is None:
                    # generic inherent impl block
                    self.__generic_impl_blocks.append(stmt_id)
                else:
                    # generic impl for block
                    self.__generic_impl_for_blocks.append(stmt_id)

    def __getitem__(self, stmt_id: StmtId) -> Impl:
        return self.__impls[stmt_id]

    def __try_deref(self, ty: TypeId) -> TypeId | None:
        """
        A type can be dereferenced if:

        - It is a pointer type.
        - It implements the `Deref` trait.

        Args:
            ty (TypeId): The type ID to dereference.
        """
        type_def = self.__space[ty]

        # Dereference pointer types
        if isinstance(type_def, PointerType):
            return type_def.pointee_type

        deref_trait = self.__space.intrinsic_trait(IntrinsicTrait.Deref)

        # Non-generic impl for Deref trait
        for stmt_id in self.__impl_for_blocks[ty]:
            impl = self.__impls[stmt_id]
            assert impl.trait is not None
            if is_same_template(deref_trait, impl.trait, self.__space):
                deref_id = impl.for_name("deref")
                deref_method = self.__space[deref_id].expect_method()
                deref_return_type = deref_method.return_type(self.__space.instantiate)
                return self.__space[deref_return_type].expect_pointer().pointee_type

        # Generic impl for Deref trait
        for stmt_id in self.__generic_impl_for_blocks:
            impl = self.__impls[stmt_id]
            assert impl.trait is not None
            if not is_same_template(deref_trait, impl.trait, self.__space):
                continue
            if impl.target == ty or is_same_template(impl.target, ty, self.__space):
                try:
                    mapping = type_unification(self.__space, [ty], [impl.target])
                except CompilerError:
                    continue
                deref_id = impl.for_name("deref")
                deref_id = self.__space.instantiate(deref_id, mapping)
                deref_method = self.__space[deref_id].expect_method()
                deref_return_type = deref_method.return_type(self.__space.instantiate)
                return self.__space[deref_return_type].expect_pointer().pointee_type

        # Cannot deref
        return None

    def __deref_chain(self, ty: TypeId) -> list[TypeId]:
        """
        Given a type `ty`, return a list of types obtained by dereferencing `ty` repeatedly
        until it can no longer be dereferenced.
        """
        deref_types = [ty]
        current_type = ty

        while True:
            next_type = self.__try_deref(current_type)
            if next_type is None:
                break
            deref_types.append(next_type)
            current_type = next_type

        return deref_types

    def method_lookup(
        self,
        caller: TypedValue,
        method_name: str,
        generic_args: list[TypeId] | None,
        arg_values: list["IR.TypedValue"],
        assignable_checker: Callable[[TypeId, TypedValue], None]
    ) -> LookupResult:
        """
        Lookup a method for a given caller type. See details in `manual/impl.md`.

        Args:
            caller (TypedValue): The caller value for which to lookup the method. Must be a concrete type.
            method_name (str): The name of the method to lookup.
        """
        # arg_types: list[TypeId] = [v.type_id for v in arg_values]

        def try_build_candidate(
            impl: Impl,
            deref_levels: int,
            ty_at_level: TypeId,
            caller: TypedValue | TypeId,
        ) -> TypeId | None:
            """
            Try to turn (impl + method_name) into a concrete MethodType ID for this call site.

            Returns:
                concrete method type id if this impl's method matches the call; otherwise None.
            """
            if not impl.exists_method(method_name):
                return None
            try:
                type_unification(self.__space, [caller], [impl.target])
            except Exception:
                return None
            # if not (isinstance(impl.target, GenericType) or impl.target == caller_type_id or is_same_template(caller_type_id, impl.target, self.__space)):
            #     return None

            raw_method_id = impl.for_name(method_name)
            raw_method_ty = self.__space[raw_method_id].expect_method()
            raw_method_def = raw_method_ty.method_def

            all_generics = raw_method_def.generics
            impl_generic_cnt = len(impl.generics)
            method_generics = all_generics[:len(all_generics) - impl_generic_cnt]

            if len(arg_values) != len(raw_method_def.parameters):
                return None

            fixed_args = []
            fixed_params = []
            for i, arg in enumerate(arg_values):
                param_ty = raw_method_def.parameters[i].type_id
                if isinstance(arg, LiteralValue) and not isinstance(self.__space[param_ty], GenericType):
                    try:
                        assignable_checker(param_ty, arg)

                    except SemanticError:
                        return None
                    continue
                fixed_args.append(arg)
                fixed_params.append(param_ty)

            args_for_unify: list[Any] = [ty_at_level] + fixed_args
            params_for_unify: list[Any] = [impl.target] + fixed_params
            if len(method_generics) > 0 and generic_args is not None:
                if len(method_generics) != len(generic_args):
                    return None
                args_for_unify.extend(generic_args)
                params_for_unify.extend(method_generics)

            try:
                full_mapping = type_unification(self.__space, args_for_unify, params_for_unify)
            except CompilerError:
                return None

            return self.__space.instantiate(raw_method_id, full_mapping)

        deref_chain = self.__deref_chain(caller.type_id)

        for deref_levels, ty_at_level in enumerate(deref_chain):
            inherent_hits: list[tuple[Impl, TypeId]] = []
            for stmt_id in self.__impl_blocks[ty_at_level]:
                impl = self.__impls[stmt_id]
                if impl.exists_method(method_name):
                    inherent_hits.append((impl, impl.for_name(method_name)))
            if len(inherent_hits) > 1:
                raise SemanticError(
                    f"Ambiguous inherent method '{method_name}' for type ID {ty_at_level}: "
                    f"found in multiple impl blocks."
                )
            if len(inherent_hits) == 1:
                impl, _ = inherent_hits[0]
                concrete_id = try_build_candidate(impl, deref_levels, ty_at_level, ty_at_level)
                if concrete_id is None:
                    raise YianTypeError(
                        f"Method '{method_name}' exists for type ID {ty_at_level}, but cannot be called with the given arguments."
                    )
                return LookupResult(method_id=concrete_id, deref_levels=deref_levels, impl=impl)

            inherent_generic_concrete: list[tuple[Impl, TypeId]] = []
            for stmt_id in self.__generic_impl_blocks:
                impl = self.__impls[stmt_id]
                cid = try_build_candidate(impl, deref_levels, ty_at_level, ty_at_level)
                if cid is not None:
                    inherent_generic_concrete.append((impl, cid))

            if len(inherent_generic_concrete) == 1:
                impl, cid = inherent_generic_concrete[0]
                return LookupResult(method_id=cid, deref_levels=deref_levels, impl=impl)
            if len(inherent_generic_concrete) > 1:
                raise SemanticError(
                    f"Ambiguous inherent method '{method_name}' for type ID {ty_at_level}: "
                    f"multiple generic impls match this call."
                )

            trait_impls: list[Impl] = []
            for stmt_id in self.__impl_for_blocks[ty_at_level]:
                impl = self.__impls[stmt_id]
                if impl.exists_method(method_name):
                    trait_impls.append(impl)

            if len(trait_impls) == 1:
                impl = trait_impls[0]
                cid = try_build_candidate(impl, deref_levels, ty_at_level, ty_at_level)
                if cid is not None:
                    return LookupResult(method_id=cid, deref_levels=deref_levels, impl=impl)
            if len(trait_impls) > 1:
                concrete: list[tuple[Impl, TypeId]] = []
                for impl in trait_impls:
                    cid = try_build_candidate(impl, deref_levels, ty_at_level, ty_at_level)
                    if cid is not None:
                        concrete.append((impl, cid))
                if len(concrete) == 1:
                    impl, cid = concrete[0]
                    return LookupResult(method_id=cid, deref_levels=deref_levels, impl=impl)
                if len(concrete) > 1:
                    trait_names = []
                    for impl, _ in concrete:
                        if impl.trait is not None:
                            trait_names.append(str(impl.trait))
                    raise SemanticError(
                        f"Ambiguous trait method '{method_name}' for type ID {ty_at_level}: "
                        f"multiple impl-for blocks match this call (traits={trait_names})."
                    )
            trait_generic_concrete: list[tuple[Impl, TypeId]] = []
            for stmt_id in self.__generic_impl_for_blocks:
                impl = self.__impls[stmt_id]
                cid = try_build_candidate(impl, deref_levels, ty_at_level, ty_at_level)
                if cid is not None:
                    trait_generic_concrete.append((impl, cid))

            if len(trait_generic_concrete) == 1:
                impl, cid = trait_generic_concrete[0]
                return LookupResult(method_id=cid, deref_levels=deref_levels, impl=impl)
            if len(trait_generic_concrete) > 1:
                raise SemanticError(
                    f"Ambiguous trait method '{method_name}' for type ID {ty_at_level}: "
                    f"multiple generic impl-for blocks match this call."
                )

        raise SemanticError(
            f"Method '{method_name}' not found for {caller} or any of its dereferenced types."
        )

    def static_method_lookup(
        self,
        caller_type: TypeId,
        method_name: str,
        generic_args: list[TypeId] | None,
        arg_values: list["IR.TypedValue"],
        assignable_checker: Callable[[TypeId, TypedValue], None]
    ) -> LookupResult:
        """
        Lookup a static method for a given caller type. See details in `manual/impl.md`.

        Args:
            caller_type (TypeId): The caller type ID for which to lookup the static method.
            method_name (str): The name of the static method to lookup.
        """
        def try_build_candidate(
            impl: Impl,
            ty_at_level: TypeId,
            caller_type_id: TypeId,
        ) -> TypeId | None:
            """
            Try to turn (impl + method_name) into a concrete MethodType ID for this call site.

            Returns:
                concrete method type id if this impl's method matches the call; otherwise None.
            """
            if not impl.exists_method(method_name):
                return None
            try:
                type_unification(self.__space, [caller_type_id], [impl.target])
            except Exception:
                return None
            # if not (impl.target == caller_type_id or is_same_template(caller_type_id, impl.target, self.__space)):
            #     return None

            raw_method_id = impl.for_name(method_name)
            raw_method_ty = self.__space[raw_method_id].expect_method()

            if not raw_method_ty.method_def.is_static:
                return None

            raw_method_def = raw_method_ty.method_def

            all_generics = raw_method_def.generics
            impl_generic_cnt = len(impl.generics)
            method_generics = all_generics[:len(all_generics) - impl_generic_cnt]

            if len(arg_values) != len(raw_method_def.parameters):
                return None

            try:
                full_mapping1 = type_unification(self.__space, [ty_at_level], [impl.target])
            except CompilerError:
                return None

            raw_params = [self.__space.instantiate(p.type_id, full_mapping1) for p in raw_method_def.parameters]
            fixed_args = []
            fixed_params = []
            for i, arg in enumerate(arg_values):
                param_ty = raw_params[i]
                if isinstance(arg, LiteralValue) and not isinstance(self.__space[param_ty], GenericType):
                    try:
                        assignable_checker(param_ty, arg)

                    except SemanticError:
                        return None
                else:
                    fixed_args.append(arg)
                    fixed_params.append(param_ty)

            if len(method_generics) > 0 and generic_args is not None:
                if len(method_generics) != len(generic_args):
                    return None
                fixed_args.extend(generic_args)
                fixed_params.extend(method_generics)

            try:
                full_mapping2 = type_unification(self.__space, fixed_args, fixed_params)
            except CompilerError:
                return None

            return self.__space.instantiate(raw_method_id, full_mapping1 | full_mapping2)

        deref_chain = self.__deref_chain(caller_type)

        for deref_levels, ty_at_level in enumerate(deref_chain):
            inherent_hits: list[tuple[Impl, TypeId]] = []
            for stmt_id in self.__impl_blocks[ty_at_level]:
                impl = self.__impls[stmt_id]
                if not impl.exists_method(method_name):
                    continue
                mid = impl.for_name(method_name)
                mty = self.__space[mid].expect_method()
                if mty.method_def.is_static:
                    inherent_hits.append((impl, mid))

            if len(inherent_hits) > 1:
                raise SemanticError(
                    f"Ambiguous inherent static method '{method_name}' for type ID {ty_at_level}: "
                    f"found in multiple impl blocks."
                )
            if len(inherent_hits) == 1:
                impl, _ = inherent_hits[0]
                concrete_id = try_build_candidate(impl, ty_at_level, ty_at_level)
                if concrete_id is None:
                    raise YianTypeError(
                        f"Static method '{method_name}' exists for type ID {ty_at_level}, but cannot be called with the given arguments."
                    )
                return LookupResult(method_id=concrete_id, deref_levels=deref_levels, impl=impl)

            inherent_generic_concrete: list[tuple[Impl, TypeId]] = []
            for stmt_id in self.__generic_impl_blocks:
                impl = self.__impls[stmt_id]
                cid = try_build_candidate(impl, ty_at_level, ty_at_level)
                if cid is not None:
                    inherent_generic_concrete.append((impl, cid))

            if len(inherent_generic_concrete) == 1:
                impl, cid = inherent_generic_concrete[0]
                return LookupResult(method_id=cid, deref_levels=deref_levels, impl=impl)
            if len(inherent_generic_concrete) > 1:
                raise SemanticError(
                    f"Ambiguous inherent static method '{method_name}' for type ID {ty_at_level}: "
                    f"multiple generic impls match this call."
                )

            trait_impls: list[Impl] = []
            for stmt_id in self.__impl_for_blocks[ty_at_level]:
                impl = self.__impls[stmt_id]
                if not impl.exists_method(method_name):
                    continue
                mid = impl.for_name(method_name)
                mty = self.__space[mid].expect_method()
                if mty.method_def.is_static:
                    trait_impls.append(impl)

            if len(trait_impls) == 1:
                impl = trait_impls[0]
                cid = try_build_candidate(impl, ty_at_level, ty_at_level)
                if cid is not None:
                    return LookupResult(method_id=cid, deref_levels=deref_levels, impl=impl)
            if len(trait_impls) > 1:
                concrete: list[tuple[Impl, TypeId]] = []
                for impl in trait_impls:
                    cid = try_build_candidate(impl, ty_at_level, ty_at_level)
                    if cid is not None:
                        concrete.append((impl, cid))
                if len(concrete) == 1:
                    impl, cid = concrete[0]
                    return LookupResult(method_id=cid, deref_levels=deref_levels, impl=impl)
                if len(concrete) > 1:
                    trait_names = []
                    for impl, _ in concrete:
                        if impl.trait is not None:
                            trait_names.append(str(impl.trait))
                    raise SemanticError(
                        f"Ambiguous trait static method '{method_name}' for type ID {ty_at_level}: "
                        f"multiple impl-for blocks match this call (traits={trait_names})."
                    )

            trait_generic_concrete: list[tuple[Impl, TypeId]] = []
            for stmt_id in self.__generic_impl_for_blocks:
                impl = self.__impls[stmt_id]
                cid = try_build_candidate(impl, ty_at_level, ty_at_level)
                if cid is not None:
                    trait_generic_concrete.append((impl, cid))

            if len(trait_generic_concrete) == 1:
                impl, cid = trait_generic_concrete[0]
                return LookupResult(method_id=cid, deref_levels=deref_levels, impl=impl)
            if len(trait_generic_concrete) > 1:
                raise SemanticError(
                    f"Ambiguous trait static method '{method_name}' for type ID {ty_at_level}: "
                    f"multiple generic impl-for blocks match this call."
                )

        raise SemanticError(
            f"Static method '{method_name}' not found for type {self.__space.get_name(caller_type)} or any of its dereferenced types."
        )

    def trait_method_lookup(self, trait: IntrinsicTrait, caller: TypeId, method_name: str, arg_values: list["IR.TypedValue"], assignable_checker: Callable[[TypeId, TypedValue], None]) -> TypeId | None:
        """
        Do method lookup, but only within the specified intrinsic trait.

        1. No dereferencing is performed.
        2. If no matching method is found within the trait, return None.
        3. Simply return the found method type ID.
        """
        trait_id = self.__space.intrinsic_trait(trait)
        # arg_types: list[TypeId] = [v.type_id for v in arg_values]

        def try_build_candidate(impl: Impl, caller_type_id: TypeId) -> TypeId | None:
            if impl.trait is None:
                return None
            if not impl.exists_method(method_name):
                return None
            try:
                type_unification(self.__space, [caller_type_id], [impl.target])
            except Exception:
                return None
            # if not (impl.target == caller_type_id or is_same_template(caller_type_id, impl.target, self.__space)):
            #     return None
            if not (impl.trait == trait_id or is_same_template(trait_id, impl.trait, self.__space)):
                return None

            raw_method_id = impl.for_name(method_name)
            raw_method_ty = self.__space[raw_method_id].expect_method()
            raw_method_def = raw_method_ty.method_def

            if len(arg_values) != len(raw_method_def.parameters):
                return None

            fixed_args = []
            fixed_params = []
            for i, arg in enumerate(arg_values):
                param = raw_method_def.parameters[i]
                param_ty = param.type_id
                if isinstance(arg, LiteralValue) and not isinstance(self.__space[param_ty], GenericType):
                    try:
                        assignable_checker(param_ty, arg)

                    except SemanticError:
                        return None
                    continue
                fixed_args.append(arg)
                fixed_params.append(param_ty)

            args_for_unify: list[Any] = [caller] + fixed_args
            params_for_unify: list[Any] = [impl.target] + fixed_params
            if len(raw_method_ty.generic_args) > 0:
                args_for_unify.extend(raw_method_ty.generic_args)
                params_for_unify.extend(raw_method_def.generics)

            try:
                full_mapping = type_unification(self.__space, args_for_unify, params_for_unify)
            except CompilerError:
                return None

            return self.__space.instantiate(raw_method_id, full_mapping)

        candidates: list[TypeId] = []

        # Non-generic impl-for blocks are indexed by concrete caller type.
        for stmt_id in self.__impl_for_blocks[caller]:
            impl = self.__impls[stmt_id]
            cid = try_build_candidate(impl, caller)
            if cid is not None:
                candidates.append(cid)

        # Generic impl-for blocks: need to try unification to see if they match this call.
        for stmt_id in self.__generic_impl_for_blocks:
            impl = self.__impls[stmt_id]
            cid = try_build_candidate(impl, caller)
            if cid is not None:
                candidates.append(cid)

        if len(candidates) == 0:
            return None
        if len(candidates) == 1:
            return candidates[0]

        raise SemanticError(
            f"Ambiguous trait method '{method_name}' for type {caller} within intrinsic trait '{trait.name}': "
            f"multiple impl-for blocks match this call."
        )

    def query_trait(self, callable_type_id: TypeId) -> TypeId | None:
        matched_trait: TypeId | None = None

        for impl in self.__impls.values():
            if impl.trait is None:
                continue

            for mid in impl.methods.values():
                if mid == callable_type_id or is_same_template(mid, callable_type_id, self.__space):
                    matched_trait = impl.trait
                    break

        return matched_trait

    def query_impl(self, callable_type_id: TypeId) -> StmtId:
        for impl in self.__impls.values():
            for mid in impl.methods.values():
                if mid == callable_type_id or is_same_template(mid, callable_type_id, self.__space):
                    return impl.stmt_id
        raise CompilerError(f"Impl block for callable type ID {callable_type_id} not found.")
