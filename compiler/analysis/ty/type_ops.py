from __future__ import annotations

from typing import TYPE_CHECKING

from compiler.analysis.error import AnalysisError
from compiler.analysis.ty import ty as Type
from compiler.utils.IR.position import SrcSpan

if TYPE_CHECKING:
    from compiler.analysis.ty.context import TypeCtx


def instantiate(ctx: TypeCtx, type_id: int, substs: dict[int, int]) -> int:
    """Given a type that may contain generic parameters, apply substitutions."""
    if len(substs) == 0:
        return type_id

    ty = ctx[type_id]
    match ty:
        case Type.GenericType(type_id=generic_type_id):
            if generic_type_id in substs:
                return substs[generic_type_id]
            return type_id
        case Type.StructType(generic_args=generic_args) | Type.EnumType(generic_args=generic_args) \
                | Type.TraitType(generic_args=generic_args) | Type.MethodType(generic_args=generic_args) | Type.FunctionType(generic_args=generic_args):
            instantiated_args = [instantiate(ctx, arg_id, substs) for arg_id in generic_args]
            return ctx.alloc_instance(type_id, instantiated_args)
        case Type.PointerType(pointee_type=pointee_type):
            instantiated_pointee = instantiate(ctx, pointee_type, substs)
            return ctx.alloc_pointer(instantiated_pointee)
        case Type.SliceType(element_type=element_type):
            instantiated_element = instantiate(ctx, element_type, substs)
            return ctx.alloc_slice(instantiated_element)
        case Type.ArrayType(element_type=element_type, length=length):
            instantiated_element = instantiate(ctx, element_type, substs)
            return ctx.alloc_array(instantiated_element, length)
        case Type.TupleType(element_types=element_types):
            instantiated_elements = [instantiate(ctx, elem_id, substs) for elem_id in element_types]
            return ctx.alloc_tuple(instantiated_elements)
        case Type.FunctionPointerType(parameter_types=param_types, return_type=return_type):
            instantiated_params = [instantiate(ctx, param_id, substs) for param_id in param_types]
            instantiated_return = instantiate(ctx, return_type, substs)
            return ctx.alloc_function_pointer(instantiated_params, instantiated_return)
        case _:
            return type_id


def infer_common_type(ctx: TypeCtx, type_ids: list[int], span: SrcSpan, context_name: str) -> int:
    """Infer a common type from a list of types."""
    definite_type_ids = [type_id for type_id in type_ids if not is_literal_type(ctx, type_id)]

    if definite_type_ids:
        first_type_id = definite_type_ids[0]
        if any(type_id != first_type_id for type_id in definite_type_ids[1:]):
            element_names = [ctx.get_name(type_id) for type_id in type_ids]
            raise AnalysisError(f"{context_name} must have a compatible type, got [{element_names}]", span)
        return first_type_id

    return _common_literal_type(ctx, type_ids, span)


def _common_literal_type(ctx: TypeCtx, type_ids: list[int], span: SrcSpan) -> int:
    result_type_id = type_ids[0]
    for next_type_id in type_ids[1:]:
        result_type_id = _gcd_literal_type(ctx, result_type_id, next_type_id, span)
    return result_type_id


def _gcd_literal_type(ctx: TypeCtx, left_type_id: int, right_type_id: int, span: SrcSpan) -> int:
    if left_type_id == right_type_id:
        return left_type_id

    left_ty = ctx[left_type_id]
    right_ty = ctx[right_type_id]

    if isinstance(left_ty, Type.IntLiteralType):
        if isinstance(right_ty, Type.IntLiteralType):
            return ctx.int_literal_id
        if isinstance(right_ty, Type.FloatLiteralType):
            return ctx.float_literal_id
    if isinstance(left_ty, Type.FloatLiteralType):
        if isinstance(right_ty, Type.IntLiteralType | Type.FloatLiteralType):
            return ctx.float_literal_id

    if isinstance(left_ty, Type.ArrayType) and isinstance(right_ty, Type.ArrayType):
        if left_ty.length != right_ty.length:
            left_name = ctx.get_name(left_type_id)
            right_name = ctx.get_name(right_type_id)
            raise AnalysisError(f"array elements must have a compatible type, got [{left_name}, {right_name}]", span)
        element_type_id = _gcd_literal_type(ctx, left_ty.element_type, right_ty.element_type, span)
        return ctx.alloc_array(element_type_id, left_ty.length)

    if isinstance(left_ty, Type.TupleType) and isinstance(right_ty, Type.TupleType):
        if len(left_ty.element_types) != len(right_ty.element_types):
            left_name = ctx.get_name(left_type_id)
            right_name = ctx.get_name(right_type_id)
            raise AnalysisError(f"array elements must have a compatible type, got [{left_name}, {right_name}]", span)
        element_types = [
            _gcd_literal_type(ctx, left_elem, right_elem, span)
            for left_elem, right_elem in zip(left_ty.element_types, right_ty.element_types)
        ]
        return ctx.alloc_tuple(element_types)

    left_name = ctx.get_name(left_type_id)
    right_name = ctx.get_name(right_type_id)
    raise AnalysisError(f"array elements must have a compatible type, got [{left_name}, {right_name}]", span)


def is_literal_type(ctx: TypeCtx, type_id: int) -> bool:
    ty = ctx[type_id]
    match ty:
        case Type.IntLiteralType() | Type.FloatLiteralType():
            return True
        case Type.ArrayType(element_type=element_type):
            return is_literal_type(ctx, element_type)
        case Type.TupleType(element_types=element_types):
            return all(is_literal_type(ctx, element_type) for element_type in element_types)
        case _:
            return False


def default_literals(ctx: TypeCtx, type_id: int) -> int:
    """Replace unresolved literal types with their default concrete types."""
    ty = ctx[type_id]

    match ty:
        case Type.IntLiteralType():
            return ctx.i32_id
        case Type.FloatLiteralType():
            return ctx.f64_id
        case Type.PointerType(pointee_type=pointee_type):
            return ctx.alloc_pointer(default_literals(ctx, pointee_type))
        case Type.SliceType(element_type=element_type):
            return ctx.alloc_slice(default_literals(ctx, element_type))
        case Type.ArrayType(element_type=element_type, length=length):
            return ctx.alloc_array(default_literals(ctx, element_type), length)
        case Type.TupleType(element_types=element_types):
            return ctx.alloc_tuple([default_literals(ctx, element_type) for element_type in element_types])
        case Type.FunctionPointerType(parameter_types=parameter_types, return_type=return_type):
            return ctx.alloc_function_pointer(
                [default_literals(ctx, parameter_type) for parameter_type in parameter_types],
                default_literals(ctx, return_type),
            )
        case Type.StructType(generic_args=generic_args):
            if len(generic_args) == 0:
                return type_id
            return ctx.alloc_instance(type_id, [default_literals(ctx, arg_type) for arg_type in generic_args])
        case Type.EnumType(generic_args=generic_args):
            if len(generic_args) == 0:
                return type_id
            return ctx.alloc_instance(type_id, [default_literals(ctx, arg_type) for arg_type in generic_args])
        case Type.TraitType(generic_args=generic_args):
            if len(generic_args) == 0:
                return type_id
            return ctx.alloc_instance(type_id, [default_literals(ctx, arg_type) for arg_type in generic_args])
        case Type.MethodType(generic_args=generic_args):
            if len(generic_args) == 0:
                return type_id
            return ctx.alloc_instance(type_id, [default_literals(ctx, arg_type) for arg_type in generic_args])
        case Type.FunctionType(generic_args=generic_args):
            if len(generic_args) == 0:
                return type_id
            return ctx.alloc_instance(type_id, [default_literals(ctx, arg_type) for arg_type in generic_args])
        case Type.AliasType(generic_args=generic_args):
            if len(generic_args) == 0:
                return type_id
            return ctx.alloc_instance(type_id, [default_literals(ctx, arg_type) for arg_type in generic_args])
        case _:
            return type_id


def contains_generic(ctx: TypeCtx, type_id: int) -> bool:
    """Return whether the given `type_id` contains any unresolved generic parameter."""
    visiting: set[int] = set()

    def _contains(tid: int) -> bool:
        if tid in visiting:
            return False
        visiting.add(tid)
        ty = ctx[tid]
        match ty:
            case Type.GenericType():
                return True
            case Type.PointerType(pointee_type=pointee_type):
                return _contains(pointee_type)
            case Type.SliceType(element_type=element_type):
                return _contains(element_type)
            case Type.ArrayType(element_type=element_type, length=_):
                return _contains(element_type)
            case Type.TupleType(element_types=element_types):
                return any(_contains(element_type) for element_type in element_types)
            case Type.FunctionPointerType(parameter_types=parameter_types, return_type=return_type):
                return any(_contains(parameter_type) for parameter_type in parameter_types) or _contains(return_type)
            case Type.StructType(generic_args=generic_args) | Type.EnumType(generic_args=generic_args) | Type.TraitType(generic_args=generic_args) | Type.MethodType(generic_args=generic_args) | Type.FunctionType(generic_args=generic_args) | Type.AliasType(generic_args=generic_args):
                if len(ty.custom_def.generics) > 0 and len(generic_args) == 0:
                    return True
                return any(_contains(arg_type) for arg_type in generic_args)
            case _:
                return False

    return _contains(type_id)


def is_int_literal_type(ctx: TypeCtx, type_id: int) -> bool:
    ty = ctx[type_id]
    return isinstance(ty, Type.IntLiteralType)


def is_float_literal_type(ctx: TypeCtx, type_id: int) -> bool:
    ty = ctx[type_id]
    return isinstance(ty, Type.FloatLiteralType)


def merge_types(ctx: TypeCtx, left_type_id: int, right_type_id: int, span: SrcSpan) -> int:
    """Merge two candidate types into one compatible result."""
    if left_type_id == right_type_id:
        return left_type_id

    left_ty = ctx[left_type_id]
    right_ty = ctx[right_type_id]

    if isinstance(left_ty, Type.GenericType):
        return right_type_id
    if isinstance(right_ty, Type.GenericType):
        return left_type_id

    if is_int_literal_type(ctx, left_type_id):
        return _merge_int_literal(ctx, left_type_id, right_type_id, span)
    if is_float_literal_type(ctx, left_type_id):
        return _merge_float_literal(ctx, left_type_id, right_type_id, span)
    if is_int_literal_type(ctx, right_type_id):
        return _merge_int_literal(ctx, right_type_id, left_type_id, span)
    if is_float_literal_type(ctx, right_type_id):
        return _merge_float_literal(ctx, right_type_id, left_type_id, span)

    if isinstance(left_ty, Type.PointerType) and isinstance(right_ty, Type.PointerType):
        return ctx.alloc_pointer(merge_types(ctx, left_ty.pointee_type, right_ty.pointee_type, span))

    if isinstance(left_ty, Type.SliceType) and isinstance(right_ty, Type.SliceType):
        return ctx.alloc_slice(merge_types(ctx, left_ty.element_type, right_ty.element_type, span))

    if isinstance(left_ty, Type.ArrayType) and isinstance(right_ty, Type.ArrayType):
        if left_ty.length != right_ty.length:
            raise AnalysisError(
                f"array lengths do not match: {ctx.get_name(left_type_id)} vs {ctx.get_name(right_type_id)}",
                span,
            )
        return ctx.alloc_array(merge_types(ctx, left_ty.element_type, right_ty.element_type, span), left_ty.length)

    if isinstance(left_ty, Type.TupleType) and isinstance(right_ty, Type.TupleType):
        if len(left_ty.element_types) != len(right_ty.element_types):
            raise AnalysisError(
                f"tuple element counts do not match: {ctx.get_name(left_type_id)} vs {ctx.get_name(right_type_id)}",
                span,
            )
        return ctx.alloc_tuple([
            merge_types(ctx, left_element_type, right_element_type, span)
            for left_element_type, right_element_type in zip(left_ty.element_types, right_ty.element_types)
        ])

    if isinstance(left_ty, Type.FunctionPointerType) and isinstance(right_ty, Type.FunctionPointerType):
        if len(left_ty.parameter_types) != len(right_ty.parameter_types):
            raise AnalysisError(
                f"function pointer parameter counts do not match: {ctx.get_name(left_type_id)} vs {ctx.get_name(right_type_id)}",
                span,
            )
        return ctx.alloc_function_pointer(
            [merge_types(ctx, left_param_type, right_param_type, span) for left_param_type, right_param_type in zip(left_ty.parameter_types, right_ty.parameter_types)],
            merge_types(ctx, left_ty.return_type, right_ty.return_type, span),
        )

    if isinstance(left_ty, Type.CustomType) and isinstance(right_ty, Type.CustomType):
        if type(left_ty) is not type(right_ty) or id(left_ty.custom_def) != id(right_ty.custom_def):
            raise AnalysisError(
                f"incompatible types: {ctx.get_name(left_type_id)} vs {ctx.get_name(right_type_id)}",
                span,
            )
        if len(left_ty.generic_args) != len(right_ty.generic_args):
            raise AnalysisError(
                f"generic argument count mismatch: {ctx.get_name(left_type_id)} vs {ctx.get_name(right_type_id)}",
                span,
            )
        return ctx.alloc_instance(left_type_id, [
            merge_types(ctx, left_arg, right_arg, span)
            for left_arg, right_arg in zip(left_ty.generic_args, right_ty.generic_args)
        ])

    raise AnalysisError(
        f"incompatible types: {ctx.get_name(left_type_id)} vs {ctx.get_name(right_type_id)}",
        span,
    )


def _merge_int_literal(ctx: TypeCtx, literal_type_id: int, other_type_id: int, span: SrcSpan) -> int:
    other_ty = ctx[other_type_id]
    if isinstance(other_ty, (Type.IntType, Type.FloatType, Type.IntLiteralType, Type.FloatLiteralType)):
        return other_type_id
    raise AnalysisError(
        f"cannot merge integer literal with '{ctx.get_name(other_type_id)}'",
        span,
    )


def _merge_float_literal(ctx: TypeCtx, literal_type_id: int, other_type_id: int, span: SrcSpan) -> int:
    other_ty = ctx[other_type_id]
    if isinstance(other_ty, (Type.FloatType, Type.FloatLiteralType)):
        return other_type_id
    if isinstance(other_ty, Type.IntLiteralType):
        return literal_type_id
    raise AnalysisError(
        f"cannot merge float literal with '{ctx.get_name(other_type_id)}'",
        span,
    )
