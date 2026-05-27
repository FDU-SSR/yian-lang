from __future__ import annotations

from collections import defaultdict
from copy import deepcopy
from dataclasses import dataclass, field

from compiler.analysis.error import AnalysisError
from compiler.analysis.ty import ty as Type
from compiler.analysis.ty.context import TypeCtx
from compiler.utils.IR.position import SrcSpan


@dataclass
class Impl:
    span: SrcSpan
    generics: list[int]
    target: int
    trait: int | None
    methods: dict[str, int] = field(default_factory=dict[str, int])


class ImplRegistry:
    def __init__(self, type_ctx: TypeCtx):
        self.__ctx = type_ctx

        self.__impls: list[Impl] = []

        self.__impl_cache: dict[int, list[Impl]] = defaultdict(list)  # target type -> list of impls for the target type
        self.__trait_impl_cache: dict[int, list[Impl]] = defaultdict(list)  # target type -> list of trait impls for the target type
        self.__generic_impl_cache: list[Impl] = []  # list of generic impls
        self.__trait_generic_impl_cache: list[Impl] = []  # list of generic trait impls

    def register_impl(self, span: SrcSpan, generics: list[int], target: int, trait: int | None) -> Impl:
        impl = Impl(span=span, generics=generics, target=target, trait=trait)
        self.__impls.append(impl)
        return impl

    def check_impls(self) -> None:
        for impl in self.__impls:
            self.__check_impl(impl)

        self.__cache_impls()

    def iter_impls(self) -> list[Impl]:
        return list(self.__impls)

    def __check_impl(self, impl: Impl) -> None:
        """
        Check if the given impl has implemented all required methods and that the signatures match.
        """
        if impl.trait is None:
            return
        trait_ty = self.__ctx[impl.trait]
        assert isinstance(trait_ty, Type.TraitType)

        impl_methods = impl.methods
        trait_methods = trait_ty.get_methods(self.__ctx)

        # Check that all methods in the trait are implemented
        for method_name, trait_method_ty_id in trait_methods.items():
            # method name in impl -> check signature matches the trait method
            if method_name in impl_methods:
                impl_method_ty_id = impl_methods[method_name]
                if not self.__impl_signature_match(trait_ty.type_id, impl.target, trait_method_ty_id, impl_method_ty_id):
                    raise AnalysisError(
                        f"Method '{method_name}' in impl of trait '{self.__ctx.get_name(impl.trait)}' for type '{self.__ctx.get_name(impl.target)}' does not match the signature of the trait method",
                        impl.span,
                    )
                continue

            # method name not in impl -> check if the trait method has a default implementation
            trait_method_ty = self.__ctx[trait_method_ty_id]
            assert isinstance(trait_method_ty, Type.MethodType)
            if not trait_method_ty.custom_def.is_header:
                # add the default implementation to the impl
                impl.methods[method_name] = self.__clone_trait_method_as_impl(trait_method_ty_id, impl.target, impl)
                continue

            # method name not in impl and no default implementation -> error
            raise AnalysisError(
                f"Method '{method_name}' is not implemented for type '{self.__ctx.get_name(impl.target)}' in impl of trait '{self.__ctx.get_name(impl.trait)}'",
                impl.span,
            )

    def __impl_signature_match(self, trait_type: int, target_type: int, trait_method: int, impl_method: int) -> bool:
        """
        Check that the method defined in the impl matches the method defined in the trait.
        """
        trait_method_ty = self.__ctx[trait_method]
        assert isinstance(trait_method_ty, Type.MethodType)
        impl_method_ty = self.__ctx[impl_method]
        assert isinstance(impl_method_ty, Type.MethodType)
        receiver_type = impl_method_ty.receiver_type(self.__ctx)

        if receiver_type != target_type:
            return False
        if trait_method_ty.receiver_type(self.__ctx) != trait_type:
            return False
        if trait_method_ty.return_type(self.__ctx) != impl_method_ty.return_type(self.__ctx):
            return False
        trait_params = trait_method_ty.parameters(self.__ctx)
        impl_params = impl_method_ty.parameters(self.__ctx)
        if len(trait_params) != len(impl_params):
            return False
        for trait_param, impl_param in zip(trait_params, impl_params):
            if trait_param.type_id != impl_param.type_id:
                return False
        return True

    def __clone_trait_method_as_impl(self, trait_method_id: int, target_type: int, impl: Impl) -> int:
        """
        Clone the given trait method as an impl method for the given target type.

        This is used to add default implementations of trait methods to impls that do not provide their own implementation.
        """
        trait_method_ty = self.__ctx[trait_method_id]
        assert isinstance(trait_method_ty, Type.MethodType)

        # alloc a new method type
        method_type_id = self.__ctx.alloc_method(trait_method_ty.custom_def.name)
        method_ty = self.__ctx[method_type_id]
        assert isinstance(method_ty, Type.MethodType)

        method_ty.custom_def = deepcopy(trait_method_ty.custom_def)

        method_ty.custom_def.generics = trait_method_ty.custom_def.generics + impl.generics
        method_ty.custom_def.receiver_type = target_type

        method_ty.generic_args = trait_method_ty.generic_args + impl.generics

        # add cloned procedure to the type context
        self.__ctx.add_procedure(method_type_id, *self.__ctx.get_procedure(trait_method_id))

        return method_type_id

    def __cache_impls(self) -> None:
        """
        Cache the implementations for faster lookup.
        """
        for impl in self.__impls:
            if impl.trait is None and len(impl.generics) == 0:
                self.__impl_cache[impl.target].append(impl)
            elif impl.trait is not None and len(impl.generics) == 0:
                self.__trait_impl_cache[impl.target].append(impl)
            elif impl.trait is None and len(impl.generics) > 0:
                self.__generic_impl_cache.append(impl)
            elif impl.trait is not None and len(impl.generics) > 0:
                self.__trait_generic_impl_cache.append(impl)
