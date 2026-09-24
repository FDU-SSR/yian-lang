"""Map semantic closure types to ordinary type IDs visible to CFG and LLVM."""
from __future__ import annotations

from dataclasses import fields, is_dataclass
from enum import Enum
from typing import cast

from compiler.analysis.ty import ty as Type
from compiler.analysis.ty.context import TypeCtx
from compiler.codegen.cfg import ir as IR


class CfgTypeMapper:
    """Create cached CFG types without rewriting existing semantic definitions."""

    __TYPE_ID_FIELDS = {
        "type_id",
        "to_type",
        "enum_type",
        "callee_type",
        "func_type_id",
        "payload_type",
    }

    def __init__(self, type_ctx: TypeCtx) -> None:
        self.__type_ctx = type_ctx
        self.__cache: dict[int, int] = {}
        self.__custom_base_cache: dict[tuple[type, int], int] = {}
        self.__closure_env_ids = {
            ty.struct_type_id
            for _, ty in type_ctx.items()
            if isinstance(ty, Type.ClosureType)
        }

    def lower(self, type_id: int) -> int:
        """Return the CFG-visible equivalent of *type_id*."""
        cached = self.__cache.get(type_id)
        if cached is not None:
            return cached

        ty = self.__type_ctx[type_id]
        match ty:
            case Type.ClosureType(struct_type_id=struct_type_id):
                result = self.lower(struct_type_id)
            case Type.PointerType(pointee_type=pointee_type):
                result = self.__type_ctx.alloc_pointer(self.lower(pointee_type))
            case Type.RefType(pointee_type=pointee_type):
                result = self.__type_ctx.alloc_ref(self.lower(pointee_type))
            case Type.SliceType(element_type=element_type):
                result = self.__type_ctx.alloc_slice(self.lower(element_type))
            case Type.ArrayType(element_type=element_type, length=length):
                result = self.__type_ctx.alloc_array(self.lower(element_type), self.lower(length))
            case Type.TupleType(element_types=element_types):
                result = self.__type_ctx.alloc_tuple([self.lower(element) for element in element_types])
            case Type.FunctionPointerType(parameter_types=parameters, return_type=return_type):
                result = self.__type_ctx.alloc_function_pointer(
                    [self.lower(parameter) for parameter in parameters], self.lower(return_type)
                )
            case Type.StructType():
                result = self.__lower_struct(type_id, ty)
            case Type.EnumType() | Type.TraitType():
                result = self.__lower_generic_instance(type_id, ty)
            case Type.FunctionType() | Type.MethodType():
                result = self.__lower_callable(type_id, ty)
            case Type.AliasType():
                result = self.__lower_alias(type_id, ty)
            case _:
                result = type_id

        self.__cache[type_id] = result
        return result

    def function_type(self, type_id: int) -> int:
        """Get the callable CFG type for a function DefPoint."""
        ty = self.__type_ctx[type_id]
        if isinstance(ty, Type.ClosureType):
            return self.lower(ty.call_method_type_id)
        return self.lower(type_id)

    def normalize_function(self, function: IR.Function) -> None:
        """Rewrite every type-id field in a completed CFG function."""
        seen: set[int] = set()

        def visit(value: object) -> object:
            if value is None or isinstance(value, (str, bytes, int, float, bool, Enum)):
                return value
            if isinstance(value, list):
                items = cast(list[object], value)
                for index, item in enumerate(items):
                    items[index] = visit(item)
                return items
            if isinstance(value, tuple):
                return tuple(visit(item) for item in cast(tuple[object, ...], value))
            if isinstance(value, dict):
                items = cast(dict[object, object], value)
                for key, item in list(items.items()):
                    items[key] = visit(item)
                return items
            if not is_dataclass(value):
                return value
            identity = id(value)
            if identity in seen:
                return value
            seen.add(identity)
            # dataclasses.fields inspects the runtime object; the cast supplies
            # the API's dataclass bound while the visitor walks every IR node.
            for field_ in fields(cast(IR.Function, value)):
                current: object = getattr(value, field_.name)
                if field_.name in self.__TYPE_ID_FIELDS and isinstance(current, int):
                    setattr(value, field_.name, self.lower(current))
                else:
                    setattr(value, field_.name, visit(current))
            return value

        visit(function)

    def __lower_struct(self, type_id: int, ty: Type.StructType) -> int:
        mapped_args = [self.lower(arg) for arg in ty.generic_args]
        if type_id not in self.__closure_env_ids:
            if mapped_args == ty.generic_args:
                return type_id
            return self.__type_ctx.alloc_instance(type_id, mapped_args)

        mapped_fields = [
            Type.StructField(
                name=field.name,
                type_id=self.lower(field.type_id),
                access_mode=field.access_mode,
                index=field.index,
                span=field.span,
            )
            for field in ty.custom_def.fields
        ]
        if mapped_args == ty.generic_args and all(
            new.type_id == old.type_id
            for new, old in zip(mapped_fields, ty.custom_def.fields)
        ):
            return type_id

        # The generated environment definition may contain another ClosureType.
        # Give CFG a separate struct definition so the semantic TypeCtx remains
        # untouched and LLVM sees only its mapped field types.
        lowered_id = self.__type_ctx.alloc_struct(f"{ty.custom_def.name}::cfg", ty.custom_def.span)
        lowered_ty = self.__type_ctx[lowered_id]
        assert isinstance(lowered_ty, Type.StructType)
        lowered_ty.custom_def.generics = ty.custom_def.generics.copy()
        lowered_ty.custom_def.unit_id = ty.custom_def.unit_id
        lowered_ty.custom_def.fields = mapped_fields
        lowered_ty.generic_args = mapped_args
        return lowered_id

    def __lower_generic_instance(self, type_id: int, ty: Type.EnumType | Type.TraitType) -> int:
        mapped_args = [self.lower(arg) for arg in ty.generic_args]
        if mapped_args == ty.generic_args:
            return type_id
        return self.__type_ctx.alloc_instance(type_id, mapped_args)

    def __lower_callable(self, type_id: int, ty: Type.FunctionType | Type.MethodType) -> int:
        mapped_args = [self.lower(arg) for arg in ty.generic_args]
        if isinstance(ty, Type.FunctionType):
            custom_def = ty.custom_def
            mapped_parameters = [
                Type.Parameter(
                    name=parameter.name,
                    type_id=self.lower(parameter.type_id),
                    span=parameter.span,
                )
                for parameter in custom_def.parameters
            ]
            mapped_return = self.lower(custom_def.return_type)
            signature_changed = (
                mapped_return != custom_def.return_type
                or any(
                    mapped.type_id != original.type_id
                    for mapped, original in zip(mapped_parameters, custom_def.parameters)
                )
            )
            mapped_receiver: int | None = None
        else:
            custom_def = ty.custom_def
            mapped_parameters = [
                Type.Parameter(
                    name=parameter.name,
                    type_id=self.lower(parameter.type_id),
                    span=parameter.span,
                )
                for parameter in custom_def.parameters
            ]
            mapped_return = self.lower(custom_def.return_type)
            mapped_receiver = self.lower(custom_def.receiver_type)
            signature_changed = (
                mapped_return != custom_def.return_type
                or mapped_receiver != custom_def.receiver_type
                or any(
                    mapped.type_id != original.type_id
                    for mapped, original in zip(mapped_parameters, custom_def.parameters)
                )
            )

        if not signature_changed:
            if mapped_args == ty.generic_args:
                return type_id
            return self.__type_ctx.alloc_instance(type_id, mapped_args)

        cache_key = (type(ty), id(custom_def))
        lowered_base = self.__custom_base_cache.get(cache_key)
        if lowered_base is None:
            if isinstance(ty, Type.FunctionType):
                lowered_base = self.__type_ctx.alloc_function(custom_def.name, custom_def.span)
                lowered_def_type = self.__type_ctx[lowered_base]
                assert isinstance(lowered_def_type, Type.FunctionType)
                lowered_def_type.custom_def.generics = custom_def.generics.copy()
                lowered_def_type.custom_def.parameters = mapped_parameters
                lowered_def_type.custom_def.return_type = mapped_return
                lowered_def_type.generic_args = ty.generic_args.copy()
            else:
                assert mapped_receiver is not None
                assert isinstance(custom_def, Type.MethodDef)
                lowered_base = self.__type_ctx.alloc_method(custom_def.name, custom_def.span)
                lowered_def_type = self.__type_ctx[lowered_base]
                assert isinstance(lowered_def_type, Type.MethodType)
                lowered_def_type.custom_def.generics = custom_def.generics.copy()
                lowered_def_type.custom_def.receiver_type = mapped_receiver
                lowered_def_type.custom_def.parameters = mapped_parameters
                lowered_def_type.custom_def.return_type = mapped_return
                lowered_def_type.custom_def.is_static = custom_def.is_static
                lowered_def_type.custom_def.is_header = custom_def.is_header
                lowered_def_type.generic_args = ty.generic_args.copy()
            self.__custom_base_cache[cache_key] = lowered_base

        lowered_base_ty = self.__type_ctx[lowered_base]
        assert isinstance(lowered_base_ty, Type.CustomType)
        if mapped_args == lowered_base_ty.generic_args:
            return lowered_base
        return self.__type_ctx.alloc_instance(lowered_base, mapped_args)

    def __lower_alias(self, type_id: int, ty: Type.AliasType) -> int:
        mapped_args = [self.lower(arg) for arg in ty.generic_args]
        if mapped_args == ty.generic_args:
            return type_id
        # The aliased definition is instantiated by TypeCtx from the mapped
        # generic arguments. Keeping its recursive structure intact also avoids
        # expanding recursive aliases while building the CFG view.
        return self.__type_ctx.alloc_instance(type_id, mapped_args)
