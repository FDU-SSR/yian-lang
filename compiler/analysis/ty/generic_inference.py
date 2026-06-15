from __future__ import annotations

from collections import defaultdict
from typing import TYPE_CHECKING

from compiler.analysis.error import AnalysisError
from compiler.analysis.ty import ty as Type
from compiler.frontend.lex.position import SrcSpan

if TYPE_CHECKING:
    from compiler.analysis.ty.context import TypeCtx


class GenericInference:
    def __init__(self, type_ctx: TypeCtx, span: SrcSpan):
        """Create a new generic-inference session.

        Args:
            type_ctx: The type context used to resolve, allocate, and name types.
            span: The source span used for diagnostics emitted by this session.
        """
        self.__type_ctx = type_ctx
        self.__span = span
        self.__bindings: dict[int, list[int]] = defaultdict(list)  # generic type id -> list of candidate type ids
        self.__solved_substs: dict[int, int] | None = None

    def constrain(self, expected_type_id: int, actual_type_id: int) -> None:
        """Add a new inference constraint.

        The expected type is treated as a pattern. If it contains generic
        parameters, the corresponding parts of the actual type are recorded as
        candidate bindings. If it is already concrete, the method validates
        structural compatibility and raises `AnalysisError` when the types do
        not match.

        Args:
            expected_type_id: The type shape we want the actual type to satisfy.
            actual_type_id: The concrete type observed at the call site.
        """
        expected_ty = self.__type_ctx[expected_type_id]

        if isinstance(expected_ty, (Type.GenericType, Type.ConstGenericType)):
            self.__bindings[expected_type_id].append(actual_type_id)
            return

        actual_ty = self.__type_ctx[actual_type_id]

        if isinstance(expected_ty, Type.PointerType) and isinstance(actual_ty, Type.PointerType):
            self.constrain(expected_ty.pointee_type, actual_ty.pointee_type)
            return

        if isinstance(expected_ty, Type.SliceType) and isinstance(actual_ty, Type.SliceType):
            self.constrain(expected_ty.element_type, actual_ty.element_type)
            return

        if isinstance(expected_ty, Type.ArrayType) and isinstance(actual_ty, Type.ArrayType):
            self.constrain(expected_ty.element_type, actual_ty.element_type)
            self.constrain(expected_ty.length, actual_ty.length)
            return

        if isinstance(expected_ty, Type.TupleType) and isinstance(actual_ty, Type.TupleType):
            if len(expected_ty.element_types) != len(actual_ty.element_types):
                raise AnalysisError("tuple element counts do not match during generic inference", self.__span)
            for expected_element, actual_element in zip(expected_ty.element_types, actual_ty.element_types):
                self.constrain(expected_element, actual_element)
            return

        if isinstance(expected_ty, Type.FunctionPointerType) and isinstance(actual_ty, Type.FunctionPointerType):
            if len(expected_ty.parameter_types) != len(actual_ty.parameter_types):
                raise AnalysisError("function pointer parameter counts do not match during generic inference", self.__span)
            for expected_param, actual_param in zip(expected_ty.parameter_types, actual_ty.parameter_types):
                self.constrain(expected_param, actual_param)
            self.constrain(expected_ty.return_type, actual_ty.return_type)
            return

        if isinstance(expected_ty, Type.CustomType) and isinstance(actual_ty, Type.CustomType):
            if id(expected_ty.custom_def) != id(actual_ty.custom_def):
                raise AnalysisError(
                    f"cannot infer generic arguments from '{self.__type_ctx.get_name(actual_type_id)}' to '{self.__type_ctx.get_name(expected_type_id)}'",
                    self.__span,
                )

            for expected_arg, actual_arg in zip(expected_ty.generic_args, actual_ty.generic_args):
                self.constrain(expected_arg, actual_arg)
            return

        if self.__type_ctx.is_literal_type(actual_type_id):
            if isinstance(actual_ty, Type.IntLiteralType) and not self.__type_ctx.is_integer_type(expected_type_id, include_literals=True):
                raise AnalysisError(
                    f"cannot infer generic arguments from '{self.__type_ctx.get_name(actual_type_id)}' for '{self.__type_ctx.get_name(expected_type_id)}'",
                    self.__span,
                )
            if isinstance(actual_ty, Type.FloatLiteralType) and not isinstance(expected_ty, (Type.FloatType, Type.FloatLiteralType)):
                raise AnalysisError(
                    f"cannot infer generic arguments from '{self.__type_ctx.get_name(actual_type_id)}' for '{self.__type_ctx.get_name(expected_type_id)}'",
                    self.__span,
                )
            return

        if expected_type_id != actual_type_id:
            raise AnalysisError(
                f"cannot infer generic arguments from '{self.__type_ctx.get_name(actual_type_id)}' for '{self.__type_ctx.get_name(expected_type_id)}'",
                self.__span,
            )

    def instantiate(self, type_id: int) -> int:
        """Instantiate a type with the solved substitutions.

        This resolves all collected bindings first, substitutes every generic
        parameter in `type_id`, and then applies default concrete types for any
        remaining literal types. The result must not contain unresolved generic
        parameters; otherwise inference is considered incomplete.

        Args:
            type_id: The type to instantiate after inference completes.

        Returns:
            A fully instantiated type ID.

        Raises:
            AnalysisError: If inference remains incomplete after substitution.
        """
        self.__solve()
        assert self.__solved_substs is not None

        instantiated_type_id = self.__type_ctx.instantiate(type_id, self.__solved_substs)
        instantiated_type_id = self.__type_ctx.default_literals(instantiated_type_id)

        if self.__type_ctx.contains_generic(instantiated_type_id):
            raise AnalysisError(
                f"cannot infer generic type '{self.__type_ctx.get_name(type_id)}' from the call arguments",
                self.__span,
            )

        return instantiated_type_id

    def substitutions(self) -> dict[int, int]:
        """Return the solved generic substitutions.

        The returned dictionary maps generic type IDs to the concrete type IDs
        inferred for them. A copy is returned so callers cannot mutate the
        cached solution stored inside the inference session.
        """
        self.__solve()
        assert self.__solved_substs is not None
        return dict(self.__solved_substs)

    def __solve(self) -> None:
        """Solve all generic bindings once and cache the result.

        Binding resolution is performed in deterministic order by generic ID.
        Each binding is resolved recursively, merged across candidates, and then
        normalized with default literal handling before being cached.
        """
        if self.__solved_substs is not None:
            return

        self.__solved_substs = {}
        for generic_id in sorted(self.__bindings):
            self.__solved_substs[generic_id] = self.__resolve_binding(generic_id, set())

        for generic_id, type_id in list(self.__solved_substs.items()):
            self.__solved_substs[generic_id] = self.__type_ctx.default_literals(self.__type_ctx.instantiate(type_id, self.__solved_substs))

    def __resolve_binding(self, generic_id: int, resolving: set[int]) -> int:
        """Resolve one generic parameter into a concrete candidate type.

        A binding may have several candidate types. They are resolved one by one
        and merged into a single compatible type. The `resolving` set is used to
        detect cycles when a generic references itself through other bindings.

        Args:
            generic_id: The generic type ID being resolved.
            resolving: The current recursion stack of generic IDs.

        Returns:
            The resolved type ID for the generic parameter.

        Raises:
            AnalysisError: If no candidate exists, merging fails, or a cycle is
            detected.
        """
        if self.__solved_substs is not None and generic_id in self.__solved_substs:
            return self.__solved_substs[generic_id]

        if generic_id in resolving:
            raise AnalysisError(
                f"cyclic generic inference detected for '{self.__type_ctx.get_name(generic_id)}'",
                self.__span,
            )

        resolving.add(generic_id)
        candidates = self.__bindings[generic_id]
        if not candidates:
            raise AnalysisError(
                f"cannot infer generic parameter '{self.__type_ctx.get_name(generic_id)}'",
                self.__span,
            )

        merged_type_id: int | None = None
        for candidate_type_id in candidates:
            resolved_candidate = self.__resolve_type(candidate_type_id, resolving)
            if merged_type_id is None:
                merged_type_id = resolved_candidate
            else:
                merged_type_id = self.__type_ctx.merge_types([merged_type_id, resolved_candidate], self.__span)

        assert merged_type_id is not None
        merged_type_id = self.__type_ctx.default_literals(merged_type_id)

        if self.__type_ctx.contains_generic(merged_type_id):
            raise AnalysisError(
                f"cannot infer generic parameter '{self.__type_ctx.get_name(generic_id)}'",
                self.__span,
            )

        resolving.remove(generic_id)
        if self.__solved_substs is not None:
            self.__solved_substs[generic_id] = merged_type_id
        return merged_type_id

    def __resolve_type(self, type_id: int, resolving: set[int]) -> int:
        """Recursively replace any nested generic bindings inside a type.

        Composite types are rebuilt from their resolved children so the final
        merged candidate uses the most specific fully-resolved shape available.
        Unbound generics are preserved as-is.
        """
        ty = self.__type_ctx[type_id]

        match ty:
            case Type.GenericType() | Type.ConstGenericType():
                if type_id in self.__bindings:
                    return self.__resolve_binding(type_id, resolving)
                return type_id
            case Type.LiteralValueType():
                return type_id
            case Type.PointerType(pointee_type=pointee_type):
                return self.__type_ctx.alloc_pointer(self.__resolve_type(pointee_type, resolving))
            case Type.SliceType(element_type=element_type):
                return self.__type_ctx.alloc_slice(self.__resolve_type(element_type, resolving))
            case Type.ArrayType(element_type=element_type, length=length):
                return self.__type_ctx.alloc_array(
                    self.__resolve_type(element_type, resolving),
                    self.__resolve_type(length, resolving),
                )
            case Type.TupleType(element_types=element_types):
                return self.__type_ctx.alloc_tuple([self.__resolve_type(element_type, resolving) for element_type in element_types])
            case Type.FunctionPointerType(parameter_types=parameter_types, return_type=return_type):
                return self.__type_ctx.alloc_function_pointer(
                    [self.__resolve_type(parameter_type, resolving) for parameter_type in parameter_types],
                    self.__resolve_type(return_type, resolving),
                )
            case Type.StructType(generic_args=generic_args):
                if len(generic_args) == 0:
                    return type_id
                return self.__type_ctx.alloc_instance(type_id, [self.__resolve_type(arg_type, resolving) for arg_type in generic_args])
            case Type.EnumType(generic_args=generic_args):
                if len(generic_args) == 0:
                    return type_id
                return self.__type_ctx.alloc_instance(type_id, [self.__resolve_type(arg_type, resolving) for arg_type in generic_args])
            case Type.TraitType(generic_args=generic_args):
                if len(generic_args) == 0:
                    return type_id
                return self.__type_ctx.alloc_instance(type_id, [self.__resolve_type(arg_type, resolving) for arg_type in generic_args])
            case Type.MethodType(generic_args=generic_args):
                if len(generic_args) == 0:
                    return type_id
                return self.__type_ctx.alloc_instance(type_id, [self.__resolve_type(arg_type, resolving) for arg_type in generic_args])
            case Type.FunctionType(generic_args=generic_args):
                if len(generic_args) == 0:
                    return type_id
                return self.__type_ctx.alloc_instance(type_id, [self.__resolve_type(arg_type, resolving) for arg_type in generic_args])
            case Type.AliasType(generic_args=generic_args):
                if len(generic_args) == 0:
                    return type_id
                return self.__type_ctx.alloc_instance(type_id, [self.__resolve_type(arg_type, resolving) for arg_type in generic_args])
            case _:
                return type_id
