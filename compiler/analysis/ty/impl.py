from __future__ import annotations

from collections import defaultdict
from copy import deepcopy
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from compiler.analysis.error import AnalysisError
from compiler.analysis.ty import ty as Type
from compiler.analysis.ty.generic_inference import GenericInference
from compiler.frontend.lex.position import SrcSpan

if TYPE_CHECKING:
    from compiler.analysis.ty.context import TypeCtx


@dataclass
class Impl:
    span: SrcSpan
    generics: list[int]
    target: int
    trait: int | None
    methods: dict[str, int] = field(default_factory=dict[str, int])
    conditions: dict[int, list[int]] = field(default_factory=dict[int, list[int]])  # generic_type_id -> [required_trait_type_id, ...]


class ImplRegistry:
    def __init__(self, type_ctx: TypeCtx):
        self.__ctx = type_ctx

        self.__impls: list[Impl] = []

        self.__impl_cache: dict[int, list[Impl]] = defaultdict(list)  # target type -> list of impls for the target type
        self.__trait_impl_cache: dict[int, list[Impl]] = defaultdict(list)  # target type -> list of trait impls for the target type
        self.__generic_impl_cache: list[Impl] = []  # list of generic impls
        self.__trait_generic_impl_cache: list[Impl] = []  # list of generic trait impls

        # Top-level has_impl memoization. The impl registry is frozen after
        # GlobalResolve, so results are stable; enabled by TypeCtx.finalize().
        self.__memoize_enabled = False
        self.__has_impl_cache: dict[tuple[int, int], bool] = {}

    def enable_memoization(self) -> None:
        """Enable memoization of has_impl results (after impls are frozen)."""
        self.__memoize_enabled = True

    def register_impl(self, span: SrcSpan, generics: list[int], target: int, trait: int | None, conditions: dict[int, list[int]] | None = None) -> Impl:
        impl = Impl(span=span, generics=generics, target=target, trait=trait, conditions=conditions or {})
        self.__impls.append(impl)
        return impl

    def check_impls(self) -> None:
        for impl in self.__impls:
            self.__check_impl(impl)

        self.__cache_impls()

    def iter_impls(self) -> list[Impl]:
        return list(self.__impls)

    def find_deref_target(self, type_id: int) -> int | None:
        """
        If `type_id` implements the `Deref` trait, return the return type
        of the deref() method. Otherwise return None.

        Used by TypeCtx.try_deref to support auto-deref via the Deref trait.
        """
        deref_trait_ty = self.__ctx[self.__ctx.deref_id]
        assert isinstance(deref_trait_ty, Type.TraitType)

        def is_deref_impl(impl: Impl) -> bool:
            """Check whether *impl* is a Deref trait implementation."""
            if impl.trait is None or "deref" not in impl.methods:
                return False
            impl_trait_ty = self.__ctx[impl.trait]
            return (
                isinstance(impl_trait_ty, Type.TraitType)
                and impl_trait_ty.custom_def is deref_trait_ty.custom_def
            )

        # 1) Non-generic trait impls that match the exact target type
        for impl in self.__trait_impl_cache[type_id]:
            if is_deref_impl(impl):
                return self.__resolve_deref_target(impl, {})

        # 2) Generic trait impls — try to unify impl.target against type_id
        for impl in self.__trait_generic_impl_cache:
            if not is_deref_impl(impl):
                continue
            inference = GenericInference(self.__ctx, SrcSpan.empty())
            try:
                inference.constrain(impl.target, type_id)
                substs = inference.substitutions()
            except AnalysisError:
                continue
            if not self.check_conditions(impl, substs):
                continue
            return self.__resolve_deref_target(impl, substs)

        return None

    def check_conditions(self, impl: Impl, substs: dict[int, int], visited: set[tuple[int, int]] | None = None) -> bool:
        """Return True if all trait conditions on *impl* are satisfied under *substs*."""
        if not impl.conditions:
            return True
        fresh = visited is None
        if fresh:
            visited = set()
        for generic_id, required_traits in impl.conditions.items():
            concrete_type_id = substs.get(generic_id, generic_id)
            for trait_id in required_traits:
                substed_trait = self.__ctx.instantiate(trait_id, substs)
                if not self.has_impl(concrete_type_id, substed_trait, visited, fresh):
                    return False
        return True

    def has_impl(self, type_id: int, trait_id: int, visited: set[tuple[int, int]] | None = None, fresh: bool = False) -> bool:
        """Check whether *type_id* implements *trait_id*.

        *fresh* marks a top-level query backed by a freshly created (empty)
        visited set, whose result is context-independent and cacheable.
        Recursive calls pass a non-fresh shared set, so their results are
        never cached — cycle detection makes them context-dependent.
        """
        if visited is None:
            visited = set()
            fresh = True
        key = (type_id, trait_id)
        if fresh and self.__memoize_enabled:
            cached = self.__has_impl_cache.get(key)
            if cached is not None:
                return cached
        result = self.__has_impl_inner(type_id, trait_id, visited)
        if fresh and self.__memoize_enabled:
            self.__has_impl_cache[key] = result
        return result

    def __has_impl_inner(self, type_id: int, trait_id: int, visited: set[tuple[int, int]] | None = None) -> bool:
        if visited is None:
            visited = set()
        key = (type_id, trait_id)
        if key in visited:
            return False
        visited.add(key)

        # Check exact match
        for impl in self.__trait_impl_cache.get(type_id, []):
            if impl.trait == trait_id:
                return self.check_conditions(impl, {}, visited)

        # Check generic impls
        for impl in self.__trait_generic_impl_cache:
            if impl.trait is None:
                continue
            impl_trait = self.__ctx[impl.trait]
            target_trait = self.__ctx[trait_id]
            if not (isinstance(impl_trait, Type.TraitType) and isinstance(target_trait, Type.TraitType) and impl_trait.custom_def is target_trait.custom_def):
                continue
            inference = GenericInference(self.__ctx, SrcSpan.empty())
            try:
                inference.constrain(impl.target, type_id)
                substs = inference.substitutions()
            except AnalysisError:
                continue
            if self.check_conditions(impl, substs, visited):
                return True

        # Simple types implicitly implement Move and Clone.
        if self.__ctx.is_simple_type(type_id):
            trait_ty = self.__ctx[trait_id]
            if isinstance(trait_ty, Type.TraitType):
                if trait_ty.custom_def.name in ("Move", "Clone"):
                    return True

        return False

    def __resolve_deref_target(self, impl: Impl, substs: dict[int, int]) -> int | None:
        """Given a Deref impl and substitutions, return the return type of deref()."""
        deref_method_id = impl.methods["deref"]
        instantiated_id = deref_method_id
        if substs:
            instantiated_id = self.__ctx.instantiate(deref_method_id, substs)
        method_ty = self.__ctx[instantiated_id]
        assert isinstance(method_ty, Type.MethodType)
        return self.__ctx.get_return_type(instantiated_id)

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

    def __impl_compatible(self, trait_type: int, target_type: int, type_from_trait: int, type_from_impl: int) -> bool:
        """
        Check that the type from the impl is compatible with the type from the trait.
        """
        if type_from_trait == type_from_impl:
            return True
        if type_from_trait == trait_type and type_from_impl == target_type:
            return True
        if isinstance(self.__ctx[type_from_trait], (Type.GenericType, Type.ConstGenericType)) and \
           isinstance(self.__ctx[type_from_impl], (Type.GenericType, Type.ConstGenericType)):
            return True
        return False

    def __impl_signature_match(self, trait_type: int, target_type: int, trait_method: int, impl_method: int) -> bool:
        """
        Check that the method defined in the impl matches the method defined in the trait.
        """
        trait_method_ty = self.__ctx[trait_method]
        assert isinstance(trait_method_ty, Type.MethodType)
        impl_method_ty = self.__ctx[impl_method]
        assert isinstance(impl_method_ty, Type.MethodType)
        receiver_type = self.__ctx.get_receiver_type(impl_method)

        if receiver_type != target_type:
            return False
        if self.__ctx.get_receiver_type(trait_method) != trait_type:
            return False
        if not self.__impl_compatible(trait_type, target_type, self.__ctx.get_return_type(trait_method), self.__ctx.get_return_type(impl_method)):
            return False
        trait_params = self.__ctx.get_params(trait_method)
        impl_params = self.__ctx.get_params(impl_method)
        if len(trait_params) != len(impl_params):
            return False
        for trait_param, impl_param in zip(trait_params, impl_params):
            if not self.__impl_compatible(trait_type, target_type, trait_param.type_id, impl_param.type_id):
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
        method_type_id = self.__ctx.alloc_method(trait_method_ty.custom_def.name, span=trait_method_ty.custom_def.span)
        method_ty = self.__ctx[method_type_id]
        assert isinstance(method_ty, Type.MethodType)

        method_ty.custom_def = deepcopy(trait_method_ty.custom_def)
        old_self = trait_method_ty.custom_def.receiver_type

        method_ty.custom_def.generics = trait_method_ty.custom_def.generics + impl.generics
        method_ty.custom_def.receiver_type = target_type
        method_ty.custom_def.return_type = self.__subst_trait_self(method_ty.custom_def.return_type, old_self, target_type)
        for param in method_ty.custom_def.parameters:
            param.type_id = self.__subst_trait_self(param.type_id, old_self, target_type)

        method_ty.generic_args = trait_method_ty.generic_args + impl.generics

        # add cloned procedure to the type context
        self.__ctx.add_procedure(method_type_id, *self.__ctx.get_procedure(trait_method_id))

        return method_type_id

    def __subst_trait_self(self, type_id: int, old_self: int, new_target: int) -> int:
        """Replace *old_self* with *new_target* inside *type_id*, recursively."""
        if type_id == old_self:
            return new_target
        ty = self.__ctx[type_id]
        if isinstance(ty, Type.PointerType):
            return self.__ctx.alloc_pointer(self.__subst_trait_self(ty.pointee_type, old_self, new_target))
        if isinstance(ty, Type.RefType):
            return self.__ctx.alloc_ref(self.__subst_trait_self(ty.pointee_type, old_self, new_target))
        if isinstance(ty, Type.SliceType):
            return self.__ctx.alloc_slice(self.__subst_trait_self(ty.element_type, old_self, new_target))
        if isinstance(ty, Type.ArrayType):
            return self.__ctx.alloc_array(self.__subst_trait_self(ty.element_type, old_self, new_target), ty.length)
        if isinstance(ty, Type.TupleType):
            return self.__ctx.alloc_tuple([self.__subst_trait_self(et, old_self, new_target) for et in ty.element_types])
        if isinstance(ty, Type.FunctionPointerType):
            return self.__ctx.alloc_function_pointer([self.__subst_trait_self(pt, old_self, new_target) for pt in ty.parameter_types], self.__subst_trait_self(ty.return_type, old_self, new_target))
        if isinstance(ty, (Type.StructType, Type.EnumType, Type.TraitType, Type.MethodType, Type.FunctionType, Type.AliasType)):
            if ty.generic_args:
                return self.__ctx.alloc_instance(type_id, [self.__subst_trait_self(ga, old_self, new_target) for ga in ty.generic_args])
            return type_id
        return type_id

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

    def iter_candidate_impls(self, type_id: int) -> list[Impl]:
        """Return the impls that could potentially match the given type_id.

        Returns exact matches plus all generic impls. Downstream
        GenericInference.constrain in method_lookup performs the actual
        matching/filtering.
        """
        exact = self.__impl_cache.get(type_id, []) + self.__trait_impl_cache.get(type_id, [])
        generic = self.__generic_impl_cache + self.__trait_generic_impl_cache
        return exact + generic
