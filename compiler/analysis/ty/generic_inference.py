from __future__ import annotations

from collections import defaultdict
from typing import TYPE_CHECKING

from compiler.analysis.error import AnalysisError
from compiler.analysis.passes.expr_evaluator import ExprEvaluator
from compiler.analysis.ty import ty as Type
from compiler.analysis.unit import hir as HIR
from compiler.utils.IR.position import SrcSpan

if TYPE_CHECKING:
    from compiler.analysis.ty.context import TypeCtx


class GenericInference:
    def __init__(self, type_ctx: TypeCtx, span: SrcSpan):
        self.__type_ctx = type_ctx
        self.__span = span
        self.__bindings: dict[int, list[int]] = defaultdict(list)  # generic type id -> list of candidate type ids
        self.__solved_substs: dict[int, int] | None = None

    def constrain(self, expected_type_id: int, actual_type_id: int) -> None:
        expected_ty = self.__type_ctx[expected_type_id]

        if isinstance(expected_ty, Type.GenericType):
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
            if expected_ty.length != actual_ty.length:
                raise AnalysisError("array lengths do not match during generic inference", self.__span)
            self.constrain(expected_ty.element_type, actual_ty.element_type)
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
            if type(expected_ty) is not type(actual_ty) or id(expected_ty.custom_def) != id(actual_ty.custom_def):
                raise AnalysisError(
                    f"cannot infer generic arguments from '{self.__type_ctx.get_name(actual_type_id)}' to '{self.__type_ctx.get_name(expected_type_id)}'",
                    self.__span,
                )

            if len(expected_ty.generic_args) != len(actual_ty.generic_args):
                raise AnalysisError(
                    f"generic argument count mismatch between '{self.__type_ctx.get_name(expected_type_id)}' and '{self.__type_ctx.get_name(actual_type_id)}'",
                    self.__span,
                )

            for expected_arg, actual_arg in zip(expected_ty.generic_args, actual_ty.generic_args):
                self.constrain(expected_arg, actual_arg)
            return

        if self.__is_literal_type(actual_type_id):
            return

        if expected_type_id != actual_type_id:
            raise AnalysisError(
                f"cannot infer generic arguments from '{self.__type_ctx.get_name(actual_type_id)}' for '{self.__type_ctx.get_name(expected_type_id)}'",
                self.__span,
            )

    def infer_arguments(self, expected_type_ids: list[int], args: list[HIR.Expr], expr_evaluator: ExprEvaluator) -> list[HIR.Expr]:
        if len(expected_type_ids) != len(args):
            raise AnalysisError(
                f"generic inference expects {len(expected_type_ids)} arguments, got {len(args)}",
                self.__span,
            )

        for expected_type_id, arg in zip(expected_type_ids, args):
            self.constrain(expected_type_id, arg.type_id)

        return [expr_evaluator.coerce(arg, self.instantiate(expected_type_id)) for expected_type_id, arg in zip(expected_type_ids, args)]

    def instantiate(self, type_id: int) -> int:
        self.__solve()
        assert self.__solved_substs is not None

        instantiated_type_id = self.__type_ctx.instantiate(type_id, self.__solved_substs)
        instantiated_type_id = self.__default_literals(instantiated_type_id)

        if self.__contains_generic(instantiated_type_id, set()):
            raise AnalysisError(
                f"cannot infer generic type '{self.__type_ctx.get_name(type_id)}' from the call arguments",
                self.__span,
            )

        return instantiated_type_id

    def substitutions(self) -> dict[int, int]:
        self.__solve()
        assert self.__solved_substs is not None
        return dict(self.__solved_substs)

    def __solve(self) -> None:
        if self.__solved_substs is not None:
            return

        self.__solved_substs = {}
        for generic_id in sorted(self.__bindings):
            self.__solved_substs[generic_id] = self.__resolve_binding(generic_id, set())

        for generic_id, type_id in list(self.__solved_substs.items()):
            self.__solved_substs[generic_id] = self.__default_literals(self.__type_ctx.instantiate(type_id, self.__solved_substs))

    def __resolve_binding(self, generic_id: int, resolving: set[int]) -> int:
        if self.__solved_substs is not None and generic_id in self.__solved_substs:
            return self.__solved_substs[generic_id]

        if generic_id in resolving:
            raise AnalysisError(
                f"cyclic generic inference detected for '{self.__type_ctx.get_name(generic_id)}'",
                self.__span,
            )

        resolving.add(generic_id)
        candidates = self.__bindings.get(generic_id, [])
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
                merged_type_id = self.__merge_types(merged_type_id, resolved_candidate)

        assert merged_type_id is not None
        merged_type_id = self.__default_literals(merged_type_id)

        if self.__contains_generic(merged_type_id, set()):
            raise AnalysisError(
                f"cannot infer generic parameter '{self.__type_ctx.get_name(generic_id)}'",
                self.__span,
            )

        resolving.remove(generic_id)
        if self.__solved_substs is not None:
            self.__solved_substs[generic_id] = merged_type_id
        return merged_type_id

    def __resolve_type(self, type_id: int, resolving: set[int]) -> int:
        ty = self.__type_ctx[type_id]

        match ty:
            case Type.GenericType(type_id=generic_type_id):
                if generic_type_id in self.__bindings:
                    return self.__resolve_binding(generic_type_id, resolving)
                return type_id
            case Type.PointerType(pointee_type=pointee_type):
                return self.__type_ctx.alloc_pointer(self.__resolve_type(pointee_type, resolving))
            case Type.SliceType(element_type=element_type):
                return self.__type_ctx.alloc_slice(self.__resolve_type(element_type, resolving))
            case Type.ArrayType(element_type=element_type, length=length):
                return self.__type_ctx.alloc_array(self.__resolve_type(element_type, resolving), length)
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

    def __merge_types(self, left_type_id: int, right_type_id: int) -> int:
        if left_type_id == right_type_id:
            return left_type_id

        left_ty = self.__type_ctx[left_type_id]
        right_ty = self.__type_ctx[right_type_id]

        if isinstance(left_ty, Type.GenericType):
            return right_type_id
        if isinstance(right_ty, Type.GenericType):
            return left_type_id

        if self.__is_int_literal_type(left_ty):
            return self.__merge_int_literal(left_type_id, right_type_id)
        if self.__is_float_literal_type(left_ty):
            return self.__merge_float_literal(left_type_id, right_type_id)
        if self.__is_int_literal_type(right_ty):
            return self.__merge_int_literal(right_type_id, left_type_id)
        if self.__is_float_literal_type(right_ty):
            return self.__merge_float_literal(right_type_id, left_type_id)

        if isinstance(left_ty, Type.PointerType) and isinstance(right_ty, Type.PointerType):
            return self.__type_ctx.alloc_pointer(self.__merge_types(left_ty.pointee_type, right_ty.pointee_type))

        if isinstance(left_ty, Type.SliceType) and isinstance(right_ty, Type.SliceType):
            return self.__type_ctx.alloc_slice(self.__merge_types(left_ty.element_type, right_ty.element_type))

        if isinstance(left_ty, Type.ArrayType) and isinstance(right_ty, Type.ArrayType):
            if left_ty.length != right_ty.length:
                raise AnalysisError(
                    f"array lengths do not match: {self.__type_ctx.get_name(left_type_id)} vs {self.__type_ctx.get_name(right_type_id)}",
                    self.__span,
                )
            return self.__type_ctx.alloc_array(self.__merge_types(left_ty.element_type, right_ty.element_type), left_ty.length)

        if isinstance(left_ty, Type.TupleType) and isinstance(right_ty, Type.TupleType):
            if len(left_ty.element_types) != len(right_ty.element_types):
                raise AnalysisError(
                    f"tuple element counts do not match: {self.__type_ctx.get_name(left_type_id)} vs {self.__type_ctx.get_name(right_type_id)}",
                    self.__span,
                )
            return self.__type_ctx.alloc_tuple([
                self.__merge_types(left_element_type, right_element_type)
                for left_element_type, right_element_type in zip(left_ty.element_types, right_ty.element_types)
            ])

        if isinstance(left_ty, Type.FunctionPointerType) and isinstance(right_ty, Type.FunctionPointerType):
            if len(left_ty.parameter_types) != len(right_ty.parameter_types):
                raise AnalysisError(
                    f"function pointer parameter counts do not match: {self.__type_ctx.get_name(left_type_id)} vs {self.__type_ctx.get_name(right_type_id)}",
                    self.__span,
                )
            return self.__type_ctx.alloc_function_pointer(
                [self.__merge_types(left_param_type, right_param_type) for left_param_type, right_param_type in zip(left_ty.parameter_types, right_ty.parameter_types)],
                self.__merge_types(left_ty.return_type, right_ty.return_type),
            )

        if isinstance(left_ty, Type.CustomType) and isinstance(right_ty, Type.CustomType):
            if type(left_ty) is not type(right_ty) or id(left_ty.custom_def) != id(right_ty.custom_def):
                raise AnalysisError(
                    f"incompatible types: {self.__type_ctx.get_name(left_type_id)} vs {self.__type_ctx.get_name(right_type_id)}",
                    self.__span,
                )
            if len(left_ty.generic_args) != len(right_ty.generic_args):
                raise AnalysisError(
                    f"generic argument count mismatch: {self.__type_ctx.get_name(left_type_id)} vs {self.__type_ctx.get_name(right_type_id)}",
                    self.__span,
                )
            return self.__type_ctx.alloc_instance(left_type_id, [
                self.__merge_types(left_arg, right_arg)
                for left_arg, right_arg in zip(left_ty.generic_args, right_ty.generic_args)
            ])

        raise AnalysisError(
            f"incompatible types: {self.__type_ctx.get_name(left_type_id)} vs {self.__type_ctx.get_name(right_type_id)}",
            self.__span,
        )

    def __merge_int_literal(self, literal_type_id: int, other_type_id: int) -> int:
        other_ty = self.__type_ctx[other_type_id]
        if isinstance(other_ty, (Type.IntType, Type.FloatType, Type.IntLiteralType, Type.FloatLiteralType)):
            return other_type_id
        raise AnalysisError(
            f"cannot merge integer literal with '{self.__type_ctx.get_name(other_type_id)}'",
            self.__span,
        )

    def __merge_float_literal(self, literal_type_id: int, other_type_id: int) -> int:
        other_ty = self.__type_ctx[other_type_id]
        if isinstance(other_ty, (Type.FloatType, Type.FloatLiteralType)):
            return other_type_id
        if isinstance(other_ty, Type.IntLiteralType):
            return literal_type_id
        raise AnalysisError(
            f"cannot merge float literal with '{self.__type_ctx.get_name(other_type_id)}'",
            self.__span,
        )

    def __default_literals(self, type_id: int) -> int:
        ty = self.__type_ctx[type_id]

        match ty:
            case Type.IntLiteralType():
                return self.__type_ctx.i32_id
            case Type.FloatLiteralType():
                return self.__type_ctx.f64_id
            case Type.PointerType(pointee_type=pointee_type):
                return self.__type_ctx.alloc_pointer(self.__default_literals(pointee_type))
            case Type.SliceType(element_type=element_type):
                return self.__type_ctx.alloc_slice(self.__default_literals(element_type))
            case Type.ArrayType(element_type=element_type, length=length):
                return self.__type_ctx.alloc_array(self.__default_literals(element_type), length)
            case Type.TupleType(element_types=element_types):
                return self.__type_ctx.alloc_tuple([self.__default_literals(element_type) for element_type in element_types])
            case Type.FunctionPointerType(parameter_types=parameter_types, return_type=return_type):
                return self.__type_ctx.alloc_function_pointer(
                    [self.__default_literals(parameter_type) for parameter_type in parameter_types],
                    self.__default_literals(return_type),
                )
            case Type.StructType(generic_args=generic_args):
                if len(generic_args) == 0:
                    return type_id
                return self.__type_ctx.alloc_instance(type_id, [self.__default_literals(arg_type) for arg_type in generic_args])
            case Type.EnumType(generic_args=generic_args):
                if len(generic_args) == 0:
                    return type_id
                return self.__type_ctx.alloc_instance(type_id, [self.__default_literals(arg_type) for arg_type in generic_args])
            case Type.TraitType(generic_args=generic_args):
                if len(generic_args) == 0:
                    return type_id
                return self.__type_ctx.alloc_instance(type_id, [self.__default_literals(arg_type) for arg_type in generic_args])
            case Type.MethodType(generic_args=generic_args):
                if len(generic_args) == 0:
                    return type_id
                return self.__type_ctx.alloc_instance(type_id, [self.__default_literals(arg_type) for arg_type in generic_args])
            case Type.FunctionType(generic_args=generic_args):
                if len(generic_args) == 0:
                    return type_id
                return self.__type_ctx.alloc_instance(type_id, [self.__default_literals(arg_type) for arg_type in generic_args])
            case Type.AliasType(generic_args=generic_args):
                if len(generic_args) == 0:
                    return type_id
                return self.__type_ctx.alloc_instance(type_id, [self.__default_literals(arg_type) for arg_type in generic_args])
            case _:
                return type_id

    def __contains_generic(self, type_id: int, visiting: set[int]) -> bool:
        if type_id in visiting:
            return False
        visiting.add(type_id)

        ty = self.__type_ctx[type_id]
        match ty:
            case Type.GenericType():
                return True
            case Type.PointerType(pointee_type=pointee_type):
                return self.__contains_generic(pointee_type, visiting)
            case Type.SliceType(element_type=element_type):
                return self.__contains_generic(element_type, visiting)
            case Type.ArrayType(element_type=element_type, length=_):
                return self.__contains_generic(element_type, visiting)
            case Type.TupleType(element_types=element_types):
                return any(self.__contains_generic(element_type, visiting) for element_type in element_types)
            case Type.FunctionPointerType(parameter_types=parameter_types, return_type=return_type):
                return any(self.__contains_generic(parameter_type, visiting) for parameter_type in parameter_types) or self.__contains_generic(return_type, visiting)
            case Type.StructType(generic_args=generic_args) | Type.EnumType(generic_args=generic_args) | Type.TraitType(generic_args=generic_args) | Type.MethodType(generic_args=generic_args) | Type.FunctionType(generic_args=generic_args) | Type.AliasType(generic_args=generic_args):
                if len(ty.custom_def.generics) > 0 and len(generic_args) == 0:
                    return True
                return any(self.__contains_generic(arg_type, visiting) for arg_type in generic_args)
            case _:
                return False

    def __is_literal_type(self, type_id: int) -> bool:
        ty = self.__type_ctx[type_id]
        return self.__is_int_literal_type(ty) or self.__is_float_literal_type(ty)

    def __is_int_literal_type(self, ty: Type.Ty) -> bool:
        return isinstance(ty, Type.IntLiteralType)

    def __is_float_literal_type(self, ty: Type.Ty) -> bool:
        return isinstance(ty, Type.FloatLiteralType)
