from __future__ import annotations

from typing import TYPE_CHECKING

from compiler.analysis.error import AnalysisError
from compiler.analysis.ty import ty as Type
from compiler.frontend.lex.position import SrcSpan

if TYPE_CHECKING:
    from compiler.analysis.ty.context import TypeCtx


def instantiate(ctx: TypeCtx, type_id: int, substs: dict[int, int]) -> int:
    """Given a type that may contain generic parameters, apply substitutions."""
    if len(substs) == 0:
        return type_id

    ty = ctx[type_id]
    match ty:
        case Type.GenericType() | Type.ConstGenericType():
            return substs.get(type_id, type_id)
        case Type.LiteralValueType():
            return type_id
        case Type.StructType(generic_args=generic_args) | Type.EnumType(generic_args=generic_args) \
                | Type.TraitType(generic_args=generic_args) | Type.MethodType(generic_args=generic_args) \
                | Type.FunctionType(generic_args=generic_args) | Type.AliasType(generic_args=generic_args):
            instantiated_args = [instantiate(ctx, arg_id, substs) for arg_id in generic_args]
            return ctx.alloc_instance(type_id, instantiated_args)
        case Type.PointerType(pointee_type=pointee_type):
            instantiated_pointee = instantiate(ctx, pointee_type, substs)
            return ctx.alloc_pointer(instantiated_pointee)
        case Type.RefType(pointee_type=pointee_type):
            instantiated_pointee = instantiate(ctx, pointee_type, substs)
            return ctx.alloc_ref(instantiated_pointee)
        case Type.SliceType(element_type=element_type):
            instantiated_element = instantiate(ctx, element_type, substs)
            return ctx.alloc_slice(instantiated_element)
        case Type.ArrayType(element_type=element_type, length=length):
            instantiated_element = instantiate(ctx, element_type, substs)
            instantiated_length = instantiate(ctx, length, substs)
            return ctx.alloc_array(instantiated_element, instantiated_length)
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
        # Compare shapes, not ids: `Code` and `u64` are one type written two ways.
        if any(not same(ctx, type_id, first_type_id) for type_id in definite_type_ids[1:]):
            element_names = [ctx.get_name(type_id) for type_id in type_ids]
            raise AnalysisError(f"{context_name} must have a compatible type, got [{element_names}]", span)
        return first_type_id

    return __common_literal_type(ctx, type_ids, span)


def __common_literal_type(ctx: TypeCtx, type_ids: list[int], span: SrcSpan) -> int:
    result_type_id = type_ids[0]
    for next_type_id in type_ids[1:]:
        result_type_id = __gcd_literal_type(ctx, result_type_id, next_type_id, span)
    return result_type_id


def __gcd_literal_type(ctx: TypeCtx, left_type_id: int, right_type_id: int, span: SrcSpan) -> int:
    if left_type_id == right_type_id:
        return left_type_id

    left_ty = ctx[ctx.resolve_aliases(left_type_id)]
    right_ty = ctx[ctx.resolve_aliases(right_type_id)]

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
        element_type_id = __gcd_literal_type(ctx, left_ty.element_type, right_ty.element_type, span)
        return ctx.alloc_array(element_type_id, left_ty.length)

    if isinstance(left_ty, Type.TupleType) and isinstance(right_ty, Type.TupleType):
        if len(left_ty.element_types) != len(right_ty.element_types):
            left_name = ctx.get_name(left_type_id)
            right_name = ctx.get_name(right_type_id)
            raise AnalysisError(f"array elements must have a compatible type, got [{left_name}, {right_name}]", span)
        element_types = [
            __gcd_literal_type(ctx, left_elem, right_elem, span)
            for left_elem, right_elem in zip(left_ty.element_types, right_ty.element_types)
        ]
        return ctx.alloc_tuple(element_types)

    left_name = ctx.get_name(left_type_id)
    right_name = ctx.get_name(right_type_id)
    raise AnalysisError(f"array elements must have a compatible type, got [{left_name}, {right_name}]", span)


def canonical(ctx: TypeCtx, type_id: int) -> int:
    """Return the id of the type *type_id* names, with aliases resolved everywhere.

    ``typedef`` is transparent, so ``Option<i32, ErrorCode>`` and
    ``Option<i32, u64>`` are one type written two ways.  Arguments are resolved
    and the instance is re-interned, so both spellings end up with one id and
    codegen emits one LLVM type.  Only call this once GlobalResolve has filled
    every alias body: resolving a nested alias needs it.
    """
    type_id = ctx.resolve_aliases(type_id)
    ty = ctx[type_id]
    if isinstance(ty, Type.CustomType) and ty.generic_args:
        args = [canonical(ctx, arg) for arg in ty.generic_args]
        if args != list(ty.generic_args):
            return ctx.alloc_instance(type_id, args)
    return type_id


def same(ctx: TypeCtx, left: int, right: int) -> bool:
    """Structural type equality that sees through aliases at every level.

    Two ids can name one type: an alias keeps its own id, and a generic argument
    may be written as an alias (``Result<i32, ErrorCode>``) while the other side
    spells the target (``Result<i32, u64>``).  Comparing ids alone would call
    those different, so compare the shapes instead, resolving aliases as we
    descend.
    """
    # The error type is a poison value, not a type: anything compared against it
    # matches, so the one error that produced it does not turn into a mismatch at
    # every use site.
    if left == ctx.error_id or right == ctx.error_id:
        return True

    left = ctx.resolve_aliases(left)
    right = ctx.resolve_aliases(right)
    if left == right:
        return True

    left_ty = ctx[left]
    right_ty = ctx[right]
    match (left_ty, right_ty):
        case (Type.PointerType(), Type.PointerType()):
            return same(ctx, left_ty.pointee_type, right_ty.pointee_type)
        case (Type.RefType(), Type.RefType()):
            return same(ctx, left_ty.pointee_type, right_ty.pointee_type)
        case (Type.SliceType(), Type.SliceType()):
            return same(ctx, left_ty.element_type, right_ty.element_type)
        case (Type.ArrayType(), Type.ArrayType()):
            return same(ctx, left_ty.element_type, right_ty.element_type) and same(
                ctx, left_ty.length, right_ty.length
            )
        case (Type.TupleType(), Type.TupleType()):
            return len(left_ty.element_types) == len(right_ty.element_types) and all(
                same(ctx, left_element, right_element)
                for left_element, right_element in zip(
                    left_ty.element_types, right_ty.element_types
                )
            )
        case (Type.FunctionPointerType(), Type.FunctionPointerType()):
            return len(left_ty.parameter_types) == len(
                right_ty.parameter_types
            ) and all(
                same(ctx, left_param, right_param)
                for left_param, right_param in zip(
                    left_ty.parameter_types, right_ty.parameter_types
                )
            ) and same(ctx, left_ty.return_type, right_ty.return_type)
        case _:
            pass

    # `CustomType` is a union alias, not a class, so it cannot be a match arm.
    # Named types are identical only when they come from one declaration;
    # instances of it are identical when their arguments are.
    if isinstance(left_ty, Type.CustomType) and isinstance(right_ty, Type.CustomType):
        if id(left_ty.custom_def) != id(right_ty.custom_def):
            return False
        return len(left_ty.generic_args) == len(right_ty.generic_args) and all(
            same(ctx, left_arg, right_arg)
            for left_arg, right_arg in zip(left_ty.generic_args, right_ty.generic_args)
        )
    return False


def is_literal_type(ctx: TypeCtx, type_id: int) -> bool:
    # `typedef Index = 3`-style aliases aside, an alias may still stand for a
    # literal-typed array/tuple, so classify what the id names.
    ty = ctx[ctx.resolve_aliases(type_id)]
    match ty:
        case Type.IntLiteralType() | Type.FloatLiteralType():
            return True
        case Type.ArrayType(element_type=element_type):
            return is_literal_type(ctx, element_type)
        case Type.TupleType(element_types=element_types):
            return all(is_literal_type(ctx, element_type) for element_type in element_types)
        case _:
            return False


def is_numeric_type(ctx: TypeCtx, type_id: int, include_literals: bool = True) -> bool:
    ty = ctx[ctx.resolve_aliases(type_id)]
    if include_literals:
        return isinstance(ty, (Type.IntType, Type.FloatType, Type.IntLiteralType, Type.FloatLiteralType))
    return isinstance(ty, (Type.IntType, Type.FloatType))


def is_integer_type(ctx: TypeCtx, type_id: int, include_literals: bool = True) -> bool:
    ty = ctx[ctx.resolve_aliases(type_id)]
    if include_literals:
        return isinstance(ty, (Type.IntType, Type.IntLiteralType))
    return isinstance(ty, Type.IntType)


def default_literals(ctx: TypeCtx, type_id: int) -> int:
    """Replace unresolved literal types with their default concrete types.

    The lookup sees through aliases (an alias of a literal type defaults like the
    literal), but a type that is *not* a literal is returned as it came in, so an
    alias keeps the spelling the program wrote.
    """
    resolved = ctx.resolve_aliases(type_id)
    if resolved == type_id:
        ty = ctx[type_id]
    else:
        default = default_literals(ctx, resolved)
        # Only replace when the resolved type actually defaulted to something
        # else; otherwise keep the alias.
        return type_id if default == resolved else default

    match ty:
        case Type.IntLiteralType():
            return ctx.i32_id
        case Type.FloatLiteralType():
            return ctx.f64_id
        case Type.PointerType(pointee_type=pointee_type):
            return ctx.alloc_pointer(default_literals(ctx, pointee_type))
        case Type.RefType(pointee_type=pointee_type):
            return ctx.alloc_ref(default_literals(ctx, pointee_type))
        case Type.SliceType(element_type=element_type):
            return ctx.alloc_slice(default_literals(ctx, element_type))
        case Type.ArrayType(element_type=element_type, length=length):
            return ctx.alloc_array(default_literals(ctx, element_type), default_literals(ctx, length))
        case Type.ConstGenericType() | Type.LiteralValueType():
            return type_id
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

    def __contains(tid: int) -> bool:
        tid = ctx.resolve_aliases(tid)
        if tid in visiting:
            return False
        visiting.add(tid)
        ty = ctx[tid]
        match ty:
            case Type.GenericType() | Type.ConstGenericType():
                return True
            case Type.LiteralValueType():
                return False
            case Type.PointerType(pointee_type=pointee_type):
                return __contains(pointee_type)
            case Type.RefType(pointee_type=pointee_type):
                return __contains(pointee_type)
            case Type.SliceType(element_type=element_type):
                return __contains(element_type)
            case Type.ArrayType(element_type=element_type, length=length):
                return __contains(element_type) or __contains(length)
            case Type.TupleType(element_types=element_types):
                return any(__contains(element_type) for element_type in element_types)
            case Type.FunctionPointerType(parameter_types=parameter_types, return_type=return_type):
                return any(__contains(parameter_type) for parameter_type in parameter_types) or __contains(return_type)
            case Type.StructType(generic_args=generic_args) | Type.EnumType(generic_args=generic_args) | Type.TraitType(generic_args=generic_args) | Type.MethodType(generic_args=generic_args) | Type.FunctionType(generic_args=generic_args) | Type.AliasType(generic_args=generic_args):
                if len(ty.custom_def.generics) > 0 and len(generic_args) == 0:
                    return True
                return any(__contains(arg_type) for arg_type in generic_args)
            case _:
                return False

    return __contains(type_id)


def is_int_literal_type(ctx: TypeCtx, type_id: int) -> bool:
    ty = ctx[ctx.resolve_aliases(type_id)]
    return isinstance(ty, Type.IntLiteralType)


def is_float_literal_type(ctx: TypeCtx, type_id: int) -> bool:
    ty = ctx[ctx.resolve_aliases(type_id)]
    return isinstance(ty, Type.FloatLiteralType)


def is_zst(ctx: TypeCtx, type_id: int) -> bool:
    """Return whether the given type is a Zero-Sized Type (ZST).

    A type is a ZST iff its values carry no runtime information (at most one
    observable inhabitant). Recursively:

    - ``void`` / ``never`` are ZST;
    - an array is ZST when its length is 0, or its element type is ZST;
    - a tuple / struct is ZST when *every* element / field is ZST;
    - an enum is ZST when it has zero variants, or a single variant whose
      payload is ZST or absent;
    - a pointer is ZST when its pointee is ZST (per the language spec: a
      pointer to a ZST carries no observable address);
    - a reference is ZST when its pointee is ZST (same rule as pointers);
    - a function *item* (the unique per-definition type of a named function)
      is always ZST: its identity lives in the type, not in any runtime value;
    - everything else (numbers, ``bool``, ``char``, ``str``, slices, function
      pointers, unresolved generics / literals) is not a ZST.

    Cyclic occurrences (self-referential types reached through a pointer)
    resolve to ``False``: such a type is only reachable through a non-ZST
    pointer, so it is correctly treated as non-ZST.
    """
    visiting: set[int] = set()

    def work(tid: int) -> bool:
        tid = ctx.resolve_aliases(tid)
        if tid in visiting:
            return False
        visiting.add(tid)
        try:
            ty = ctx[tid]
            match ty:
                case Type.VoidType() | Type.NeverType():
                    return True
                case Type.ArrayType(element_type=element_type):
                    if ctx.try_extract_array_length(tid) == 0:
                        return True
                    return work(element_type)
                case Type.TupleType(element_types=element_types):
                    return all(work(element_type) for element_type in element_types)
                case Type.PointerType(pointee_type=pointee_type):
                    return work(pointee_type)
                case Type.RefType(pointee_type=pointee_type):
                    return work(pointee_type)
                case Type.StructType():
                    return all(work(f.type_id) for f in ctx.get_struct_fields(tid))
                case Type.EnumType():
                    variants = ctx.get_enum_variants(tid)
                    if len(variants) == 0:
                        return True
                    if len(variants) == 1:
                        payload = variants[0].payload_type
                        return payload is None or work(payload)
                    return False
                case Type.FunctionType():
                    return True
                case _:
                    return False
        finally:
            visiting.discard(tid)

    return work(type_id)


def merge_types(ctx: TypeCtx, type_ids: list[int], span: SrcSpan) -> int:
    """Merge a list of candidate types into one compatible result.

    Filters out ``never`` types (compatible with everything), then
    pairwise-merges the remainder.
    """
    non_never = [t for t in type_ids if t != ctx.never_id]
    if not non_never:
        return ctx.never_id
    result = non_never[0]
    for t in non_never[1:]:
        result = __merge_two(ctx, result, t, span)
    return result


def __merge_two(ctx: TypeCtx, left_type_id: int, right_type_id: int, span: SrcSpan) -> int:
    """Merge exactly two types (never already filtered out by caller)."""
    if left_type_id == right_type_id:
        return left_type_id

    # An alias and the type it stands for are the same type, and a merged result
    # should keep the spelling the program used: merge in resolved space, then
    # re-attach the alias if the result is one of the two sides.
    left_resolved = ctx.resolve_aliases(left_type_id)
    right_resolved = ctx.resolve_aliases(right_type_id)
    if left_resolved != left_type_id or right_resolved != right_type_id:
        merged = __merge_two(ctx, left_resolved, right_resolved, span)
        if merged == left_resolved:
            return left_type_id
        if merged == right_resolved:
            return right_type_id
        return merged

    left_ty = ctx[left_type_id]
    right_ty = ctx[right_type_id]

    if isinstance(left_ty, (Type.GenericType, Type.ConstGenericType)):
        return right_type_id
    if isinstance(right_ty, (Type.GenericType, Type.ConstGenericType)):
        return left_type_id
    if isinstance(left_ty, Type.LiteralValueType) and isinstance(right_ty, Type.LiteralValueType):
        if left_ty.value == right_ty.value:
            return left_type_id
        raise AnalysisError(f"const value mismatch: {left_ty.value} vs {right_ty.value}", span)

    if is_int_literal_type(ctx, left_type_id):
        return __merge_int_literal(ctx, left_type_id, right_type_id, span)
    if is_float_literal_type(ctx, left_type_id):
        return __merge_float_literal(ctx, left_type_id, right_type_id, span)
    if is_int_literal_type(ctx, right_type_id):
        return __merge_int_literal(ctx, right_type_id, left_type_id, span)
    if is_float_literal_type(ctx, right_type_id):
        return __merge_float_literal(ctx, right_type_id, left_type_id, span)

    if isinstance(left_ty, Type.PointerType) and isinstance(right_ty, Type.PointerType):
        return ctx.alloc_pointer(__merge_two(ctx, left_ty.pointee_type, right_ty.pointee_type, span))

    if isinstance(left_ty, Type.RefType) and isinstance(right_ty, Type.RefType):
        return ctx.alloc_ref(__merge_two(ctx, left_ty.pointee_type, right_ty.pointee_type, span))

    if isinstance(left_ty, Type.SliceType) and isinstance(right_ty, Type.SliceType):
        return ctx.alloc_slice(__merge_two(ctx, left_ty.element_type, right_ty.element_type, span))

    if isinstance(left_ty, Type.ArrayType) and isinstance(right_ty, Type.ArrayType):
        merged_length = __merge_two(ctx, left_ty.length, right_ty.length, span)
        return ctx.alloc_array(__merge_two(ctx, left_ty.element_type, right_ty.element_type, span), merged_length)

    if isinstance(left_ty, Type.TupleType) and isinstance(right_ty, Type.TupleType):
        if len(left_ty.element_types) != len(right_ty.element_types):
            raise AnalysisError(
                f"tuple element counts do not match: {ctx.get_name(left_type_id)} vs {ctx.get_name(right_type_id)}",
                span,
            )
        return ctx.alloc_tuple([
            __merge_two(ctx, left_element_type, right_element_type, span)
            for left_element_type, right_element_type in zip(left_ty.element_types, right_ty.element_types)
        ])

    if isinstance(left_ty, Type.FunctionPointerType) and isinstance(right_ty, Type.FunctionPointerType):
        if len(left_ty.parameter_types) != len(right_ty.parameter_types):
            raise AnalysisError(
                f"function pointer parameter counts do not match: {ctx.get_name(left_type_id)} vs {ctx.get_name(right_type_id)}",
                span,
            )
        return ctx.alloc_function_pointer(
            [__merge_two(ctx, left_param_type, right_param_type, span) for left_param_type, right_param_type in zip(left_ty.parameter_types, right_ty.parameter_types)],
            __merge_two(ctx, left_ty.return_type, right_ty.return_type, span),
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
            __merge_two(ctx, left_arg, right_arg, span)
            for left_arg, right_arg in zip(left_ty.generic_args, right_ty.generic_args)
        ])

    raise AnalysisError(
        f"incompatible types: {ctx.get_name(left_type_id)} vs {ctx.get_name(right_type_id)}",
        span,
    )


def __merge_int_literal(ctx: TypeCtx, literal_type_id: int, other_type_id: int, span: SrcSpan) -> int:
    other_ty = ctx[ctx.resolve_aliases(other_type_id)]
    if isinstance(other_ty, (Type.IntType, Type.FloatType, Type.IntLiteralType, Type.FloatLiteralType)):
        return other_type_id
    raise AnalysisError(
        f"cannot merge integer literal with '{ctx.get_name(other_type_id)}'",
        span,
    )


def __merge_float_literal(ctx: TypeCtx, literal_type_id: int, other_type_id: int, span: SrcSpan) -> int:
    other_ty = ctx[ctx.resolve_aliases(other_type_id)]
    if isinstance(other_ty, (Type.FloatType, Type.FloatLiteralType)):
        return other_type_id
    if isinstance(other_ty, Type.IntLiteralType):
        return literal_type_id
    raise AnalysisError(
        f"cannot merge float literal with '{ctx.get_name(other_type_id)}'",
        span,
    )
