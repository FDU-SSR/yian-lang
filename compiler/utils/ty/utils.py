from compiler.config.defs import TypeId
from compiler.utils.IR.typed_value import LiteralValue, TypedValue, Variable
from compiler.utils.errors import CompilerError

from .space import TypeSpace
from .yian_types import ArrayType, FunctionPointerType, GenericType, InstantiatedType, PointerType, SliceType, TupleType


def type_unification(
    type_space: TypeSpace,
    args: list[TypedValue | TypeId],
    params: list[TypeId],
) -> dict[TypeId, TypeId]:
    """
    Perform most general unification between argument types and parameter types.

    See details in `manual/type.md`.

    Args:
        args (list[TypedValue | TypeId]): A list of argument type values provided.
        params (list[TypeId]): A list of parameter type IDs expected.

    Returns:
        dict[TypeId, TypeId]: A substitution mapping from generic TypeId to concrete TypeId.
    """
    if len(args) != len(params):
        raise CompilerError("Arguments and parameters length mismatch in type unification.")

    generic_mapping: dict[TypeId, TypeId] = {}
    seen_generics: set[TypeId] = set()
    literal_pairs: list[tuple[LiteralValue, TypeId]] = []

    for arg_obj, param_type_id in zip(args, params):
        if isinstance(arg_obj, TypeId):
            arg_type_id = arg_obj
        elif isinstance(arg_obj, Variable):
            arg_type_id = arg_obj.type_id
        else:
            arg_type_id = arg_obj.get_determined_type_id()
            if arg_type_id is None:
                literal_pairs.append((arg_obj, param_type_id))
                continue

        __unify(arg_type_id, param_type_id, generic_mapping, seen_generics, type_space)

    for literal_obj, param_type_id in literal_pairs:
        if not isinstance(type_space[param_type_id], GenericType):
            literal_obj.type_id = param_type_id  # type: ignore
            continue
        resolved_type_id = generic_mapping.get(param_type_id)

        if resolved_type_id is not None:
            literal_obj.type_id = resolved_type_id  # type: ignore
            arg_type_id = resolved_type_id
        else:
            arg_type_id = literal_obj.type_id

        __unify(arg_type_id, param_type_id, generic_mapping, seen_generics, type_space)

    for key, val in list(generic_mapping.items()):
        generic_mapping[key] = __apply_subst(val, generic_mapping, type_space)

    unmapped_generics = seen_generics - generic_mapping.keys()
    for generic in unmapped_generics:
        generic_mapping[generic] = generic

    return generic_mapping


def is_same_template(a: TypeId, b: TypeId, type_space: TypeSpace) -> bool:
    """
    Returns true if `a` and `b` share the same template type (i.e., they are instantiated from the same generic type).
    """
    a_ty = type_space[a]
    b_ty = type_space[b]
    if not isinstance(a_ty, InstantiatedType) or not isinstance(b_ty, InstantiatedType):
        return False
    return a_ty.def_id == b_ty.def_id


def __apply_subst(ty: TypeId, substs: dict[TypeId, TypeId], type_space: TypeSpace) -> TypeId:
    """
    Apply substitutions to a type id (only needs to chase GenericType bindings).
    """
    cur = ty
    while cur in type_space and isinstance(type_space[cur], GenericType) and cur in substs and substs[cur] != cur:
        nxt = substs[cur]
        if nxt in substs and substs[nxt] != nxt:
            substs[cur] = substs[nxt]
            cur = substs[cur]
        else:
            cur = nxt
    return cur


def __occurs_in(var: TypeId, ty: TypeId, substs: dict[TypeId, TypeId], type_space: TypeSpace) -> bool:
    """
    Occurs check: whether var occurs in ty (after applying current substitutions).
    Prevents infinite/self-referential substitutions like T := Array<T>.
    """
    ty = __apply_subst(ty, substs, type_space)
    if var == ty:
        return True
    if ty not in type_space:
        return False
    node = type_space[ty]
    match node:
        case GenericType():
            return False
        case InstantiatedType(generic_args=generic_args):
            return any(__occurs_in(var, a, substs, type_space) for a in generic_args)
        case ArrayType(element_type=element_type):
            return __occurs_in(var, element_type, substs, type_space)
        case TupleType(element_types=element_types):
            return any(__occurs_in(var, e, substs, type_space) for e in element_types)
        case PointerType(pointee_type=pointee_type):
            return __occurs_in(var, pointee_type, substs, type_space)
        case FunctionPointerType(parameter_types=parameter_types, return_type=return_type):
            return any(__occurs_in(var, p, substs, type_space) for p in parameter_types) or __occurs_in(var, return_type, substs, type_space)
        case _:
            return False


def __bind_generic(
    var: TypeId,
    ty: TypeId,
    substs: dict[TypeId, TypeId],
    seen_generics: set[TypeId],
    type_space: TypeSpace,
):
    """
    Bind a generic variable to a type.
    """
    seen_generics.add(var)
    ty = __apply_subst(ty, substs, type_space)
    if var == ty:
        return
    if __occurs_in(var, ty, substs, type_space):
        raise CompilerError("Occurs check failed during type unification.")
    substs[var] = ty


def __unify(
    arg_type: TypeId,
    param_type: TypeId,
    generic_mapping: dict[TypeId, TypeId],
    seen_generics: set[TypeId],
    type_space: TypeSpace,
):
    a = __apply_subst(arg_type, generic_mapping, type_space)
    b = __apply_subst(param_type, generic_mapping, type_space)
    if a == b:
        if a in type_space and isinstance(type_space[a], GenericType):
            seen_generics.add(a)
        return

    if a not in type_space or b not in type_space:
        raise CompilerError("Type mismatch during type unification.")

    arg = type_space[a]
    param = type_space[b]

    # variables on either side (symmetric)
    if isinstance(arg, GenericType) and a not in generic_mapping:
        __bind_generic(a, b, generic_mapping, seen_generics, type_space)
        return
    if isinstance(param, GenericType) and b not in generic_mapping:
        __bind_generic(b, a, generic_mapping, seen_generics, type_space)
        return

    # compound types
    if isinstance(arg, InstantiatedType) and isinstance(param, InstantiatedType):
        if type(arg) is not type(param) or arg.def_id != param.def_id or len(arg.generic_args) != len(param.generic_args):
            raise CompilerError("Type mismatch during type unification.")
        for ag, pg in zip(arg.generic_args, param.generic_args):
            __unify(ag, pg, generic_mapping, seen_generics, type_space)
        return

    if isinstance(arg, ArrayType) and isinstance(param, ArrayType):
        if arg.length != param.length:
            raise CompilerError("Type mismatch during type unification.")
        __unify(arg.element_type, param.element_type, generic_mapping, seen_generics, type_space)
        return

    if isinstance(arg, TupleType) and isinstance(param, TupleType):
        if len(arg.element_types) != len(param.element_types):
            raise CompilerError("Type mismatch during type unification.")
        for ae, pe in zip(arg.element_types, param.element_types):
            __unify(ae, pe, generic_mapping, seen_generics, type_space)
        return

    if isinstance(arg, PointerType) and isinstance(param, PointerType):
        __unify(arg.pointee_type, param.pointee_type, generic_mapping, seen_generics, type_space)
        return

    if isinstance(arg, SliceType) and isinstance(param, SliceType):
        __unify(arg.element_type, param.element_type, generic_mapping, seen_generics, type_space)
        return

    if isinstance(arg, FunctionPointerType) and isinstance(param, FunctionPointerType):
        if len(arg.parameter_types) != len(param.parameter_types):
            raise CompilerError("Function parameter length mismatch during type unification.")
        for ap, pp in zip(arg.parameter_types, param.parameter_types):
            __unify(ap, pp, generic_mapping, seen_generics, type_space)
        __unify(arg.return_type, param.return_type, generic_mapping, seen_generics, type_space)
        return

    # basic / non-decomposable
    if a != b:
        raise CompilerError("Type mismatch during type unification.")
