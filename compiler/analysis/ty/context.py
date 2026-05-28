from __future__ import annotations

from dataclasses import dataclass

from compiler.analysis.error import AnalysisError
from compiler.analysis.symbol.context import SymbolCtx
from compiler.analysis.ty import ty as Type
from compiler.analysis.ty.generic_inference import GenericInference
from compiler.analysis.ty.impl import Impl, ImplRegistry
from compiler.analysis.ty.resolver import TypeResolver
from compiler.analysis.unit import hir as HIR
from compiler.frontend.parse import ast as AST
from compiler.frontend.parse.ast_type import ASTType
from compiler.utils.errors.yian_error import CompilerError
from compiler.utils.IR.position import SrcSpan


class TypeCtx:
    # intrinsic basic type IDs
    void_id: int = 10
    bool_id: int = 11
    char_id: int = 12
    str_id: int = 13

    i8_id: int = 14
    i16_id: int = 15
    i32_id: int = 16
    i64_id: int = 17

    u8_id: int = 18
    u16_id: int = 19
    u32_id: int = 20
    u64_id: int = 21

    f16_id: int = 22
    f32_id: int = 23
    f64_id: int = 24

    int_literal_id: int = 25
    float_literal_id: int = 26

    # intrinsic trait IDs
    add_id: int = 50
    sub_id: int = 51
    mul_id: int = 52
    div_id: int = 53
    rem_id: int = 54
    neg_id: int = 55
    bitand_id: int = 56
    bitor_id: int = 57
    bitxor_id: int = 58
    bitnot_id: int = 59
    shl_id: int = 60
    shr_id: int = 61

    partial_eq_id: int = 70
    partial_ord_id: int = 71

    index_id: int = 80
    contains_id: int = 81
    deref_id: int = 82
    delete_id: int = 83
    drop_id: int = 84

    # intrinsic struct/enum IDs
    Range_id: int = 100
    Option_id: int = 101
    Result_id: int = 102

    def __init__(self):
        self.__space: dict[int, Type.Ty] = {}

        self.__next_id = 500

        self.__add_intrinsic_types()

        # === caches to avoid duplicate allocations ===
        self.__pointer_cache: dict[int, int] = {}  # pointee type id -> pointer type id
        self.__slice_cache: dict[int, int] = {}  # element type id -> slice type id
        self.__array_cache: dict[tuple[int, int], int] = {}  # (element type id, length) -> array type id
        self.__tuple_cache: dict[tuple[int, ...], int] = {}  # element type ids -> tuple type id
        self.__function_pointer_cache: dict[tuple[tuple[int, ...], int], int] = {}  # ((param type ids), return type id) -> function pointer type id
        # =============================================

        # === cache from template to instance ===
        self.__instance_cache: dict[tuple[int, tuple[int, ...]], int] = {}  # (def id, generic arg type ids) -> instance type id
        # =======================================

        self.__name_cache: dict[int, str] = {}  # type id -> type name (for debugging and error messages)

        self.__resolver = TypeResolver(self)
        self.__impl_registry = ImplRegistry(self)
        self.__procedures: dict[int, tuple[AST.Block, int]] = {}  # procedure_id -> procedure block

    def __force_add_type(self, ty: Type.Ty) -> None:
        """
        Assumes the type ID is already set in the type object and does not check for duplicates.
        """
        self.__space[ty.type_id] = ty

    def __add_intrinsic_types(self) -> None:
        self.__force_add_type(Type.VoidType(type_id=self.void_id))
        self.__force_add_type(Type.BoolType(type_id=self.bool_id))
        self.__force_add_type(Type.CharType(type_id=self.char_id))
        self.__force_add_type(Type.StrType(type_id=self.str_id))

        self.__force_add_type(Type.IntType(type_id=self.i8_id, size=1, signed=True))
        self.__force_add_type(Type.IntType(type_id=self.i16_id, size=2, signed=True))
        self.__force_add_type(Type.IntType(type_id=self.i32_id, size=4, signed=True))
        self.__force_add_type(Type.IntType(type_id=self.i64_id, size=8, signed=True))

        self.__force_add_type(Type.IntType(type_id=self.u8_id, size=1, signed=False))
        self.__force_add_type(Type.IntType(type_id=self.u16_id, size=2, signed=False))
        self.__force_add_type(Type.IntType(type_id=self.u32_id, size=4, signed=False))
        self.__force_add_type(Type.IntType(type_id=self.u64_id, size=8, signed=False))

        self.__force_add_type(Type.FloatType(type_id=self.f16_id, size=2))
        self.__force_add_type(Type.FloatType(type_id=self.f32_id, size=4))
        self.__force_add_type(Type.FloatType(type_id=self.f64_id, size=8))

        self.__force_add_type(Type.IntLiteralType(type_id=self.int_literal_id))
        self.__force_add_type(Type.FloatLiteralType(type_id=self.float_literal_id))

    def __add_type(self, ty: Type.Ty) -> int:
        if ty.type_id == -1:
            ty.type_id = self.__next_id
            self.__next_id += 1

        if ty.type_id in self.__space:
            raise CompilerError(f"Type ID {ty.type_id} already exists in the type context")

        self.__space[ty.type_id] = ty
        return ty.type_id

    def __getitem__(self, type_id: int) -> Type.Ty:
        if type_id not in self.__space:
            raise CompilerError(f"Type ID {type_id} does not exist in the type context")
        return self.__space[type_id]

    def __contains__(self, type_id: int) -> bool:
        return type_id in self.__space

    INTRINSIC_TYPE_DICT: dict[Type.IntrinsicType, int] = {
        Type.IntrinsicType.Void: void_id,
        Type.IntrinsicType.Bool: bool_id,
        Type.IntrinsicType.Char: char_id,
        Type.IntrinsicType.Str: str_id,

        Type.IntrinsicType.I8: i8_id,
        Type.IntrinsicType.I16: i16_id,
        Type.IntrinsicType.I32: i32_id,
        Type.IntrinsicType.I64: i64_id,

        Type.IntrinsicType.U8: u8_id,
        Type.IntrinsicType.U16: u16_id,
        Type.IntrinsicType.U32: u32_id,
        Type.IntrinsicType.U64: u64_id,

        Type.IntrinsicType.F16: f16_id,
        Type.IntrinsicType.F32: f32_id,
        Type.IntrinsicType.F64: f64_id,

        Type.IntrinsicType.Int: i64_id,
        Type.IntrinsicType.UInt: u64_id,
        Type.IntrinsicType.Float: f64_id,
    }

    @classmethod
    def intrinsic_type(cls, intrinsic: Type.IntrinsicType) -> int:
        return cls.INTRINSIC_TYPE_DICT[intrinsic]

    INTRINSIC_CUSTOM_TYPE_DICT: dict[Type.IntrinsicCustomType, int] = {
        Type.IntrinsicCustomType.Add: add_id,
        Type.IntrinsicCustomType.Sub: sub_id,
        Type.IntrinsicCustomType.Mul: mul_id,
        Type.IntrinsicCustomType.Div: div_id,
        Type.IntrinsicCustomType.Rem: rem_id,
        Type.IntrinsicCustomType.Neg: neg_id,

        Type.IntrinsicCustomType.BitAnd: bitand_id,
        Type.IntrinsicCustomType.BitOr: bitor_id,
        Type.IntrinsicCustomType.BitXor: bitxor_id,
        Type.IntrinsicCustomType.BitNot: bitnot_id,
        Type.IntrinsicCustomType.Shl: shl_id,
        Type.IntrinsicCustomType.Shr: shr_id,

        Type.IntrinsicCustomType.PartialEq: partial_eq_id,
        Type.IntrinsicCustomType.PartialOrd: partial_ord_id,

        Type.IntrinsicCustomType.Index: index_id,
        Type.IntrinsicCustomType.Contains: contains_id,
        Type.IntrinsicCustomType.Deref: deref_id,
        Type.IntrinsicCustomType.Delete: delete_id,
        Type.IntrinsicCustomType.Drop: drop_id,
    }

    @classmethod
    def intrinsic_custom_type(cls, intrinsic: Type.IntrinsicCustomType) -> int:
        return cls.INTRINSIC_CUSTOM_TYPE_DICT[intrinsic]

    def alloc_generic(self, name: str) -> int:
        return self.__add_type(Type.GenericType(type_id=-1, name=name))

    def alloc_pointer(self, pointee_type: int) -> int:
        if pointee_type in self.__pointer_cache:
            return self.__pointer_cache[pointee_type]

        pointer_ty = Type.PointerType(type_id=-1, pointee_type=pointee_type)
        pointer_ty_id = self.__add_type(pointer_ty)
        self.__pointer_cache[pointee_type] = pointer_ty_id
        return pointer_ty_id

    def alloc_slice(self, element_type: int) -> int:
        if element_type in self.__slice_cache:
            return self.__slice_cache[element_type]

        slice_ty = Type.SliceType(type_id=-1, element_type=element_type)
        slice_ty_id = self.__add_type(slice_ty)
        self.__slice_cache[element_type] = slice_ty_id
        return slice_ty_id

    def alloc_array(self, element_type: int, length: int) -> int:
        key = (element_type, length)
        if key in self.__array_cache:
            return self.__array_cache[key]

        array_ty = Type.ArrayType(type_id=-1, element_type=element_type, length=length)
        array_ty_id = self.__add_type(array_ty)
        self.__array_cache[key] = array_ty_id
        return array_ty_id

    def alloc_tuple(self, element_types: list[int]) -> int:
        key = tuple(element_types)
        if key in self.__tuple_cache:
            return self.__tuple_cache[key]

        tuple_ty = Type.TupleType(type_id=-1, element_types=element_types)
        tuple_ty_id = self.__add_type(tuple_ty)
        self.__tuple_cache[key] = tuple_ty_id
        return tuple_ty_id

    def alloc_function_pointer(self, param_types: list[int], return_type: int) -> int:
        key = (tuple(param_types), return_type)
        if key in self.__function_pointer_cache:
            return self.__function_pointer_cache[key]

        function_pointer_ty = Type.FunctionPointerType(type_id=-1, parameter_types=param_types, return_type=return_type)
        function_pointer_ty_id = self.__add_type(function_pointer_ty)
        self.__function_pointer_cache[key] = function_pointer_ty_id
        return function_pointer_ty_id

    def alloc_alias(self, name: str) -> int:
        alias_def = Type.AliasDef(name=name)
        alias_ty = Type.AliasType(type_id=-1, custom_def=alias_def)

        type_id = self.__add_type(alias_ty)
        return type_id

    def alloc_struct(self, name: str) -> int:
        struct_def = Type.StructDef(name=name)
        struct_ty = Type.StructType(type_id=-1, custom_def=struct_def)

        intrinsic_mapping = {
            "Range": self.Range_id,
        }
        if name in intrinsic_mapping:
            struct_ty.type_id = intrinsic_mapping[name]

        type_id = self.__add_type(struct_ty)
        return type_id

    def alloc_unnamed_struct(self, owner: str, field_names: list[str], field_types: list[int]) -> int:
        if len(field_names) != len(field_types):
            raise CompilerError(f"Field names and types count mismatch for unnamed struct in {owner}")

        struct_def = Type.StructDef(name=f"{owner}::{{unnamed}}")
        for index, (field_name, field_type) in enumerate(zip(field_names, field_types)):
            struct_def.fields.append(Type.StructField(name=field_name, type_id=field_type, access_mode=Type.AccessMode.Public, index=index))
        struct_ty = Type.StructType(type_id=-1, custom_def=struct_def)
        return self.__add_type(struct_ty)

    def alloc_enum(self, name: str) -> int:
        enum_def = Type.EnumDef(name=name)
        enum_ty = Type.EnumType(type_id=-1, custom_def=enum_def)

        intrinsic_mapping = {
            "Option": self.Option_id,
            "Result": self.Result_id,
        }
        if name in intrinsic_mapping:
            enum_ty.type_id = intrinsic_mapping[name]

        type_id = self.__add_type(enum_ty)
        return type_id

    def alloc_trait(self, name: str) -> int:
        trait_def = Type.TraitDef(name=name)
        trait_ty = Type.TraitType(type_id=-1, custom_def=trait_def)

        intrinsic_mapping = {
            "Add": self.add_id,
            "Sub": self.sub_id,
            "Mul": self.mul_id,
            "Div": self.div_id,
            "Rem": self.rem_id,
            "Neg": self.neg_id,

            "BitAnd": self.bitand_id,
            "BitOr": self.bitor_id,
            "BitXor": self.bitxor_id,
            "BitNot": self.bitnot_id,
            "Shl": self.shl_id,
            "Shr": self.shr_id,

            "PartialEq": self.partial_eq_id,
            "PartialOrd": self.partial_ord_id,

            "Index": self.index_id,
            "Contains": self.contains_id,
            "Deref": self.deref_id,
            "Delete": self.delete_id,
            "Drop": self.drop_id,
        }
        if name in intrinsic_mapping:
            trait_ty.type_id = intrinsic_mapping[name]

        type_id = self.__add_type(trait_ty)
        return type_id

    def alloc_method(self, name: str) -> int:
        method_def = Type.MethodDef(name=name)
        method_ty = Type.MethodType(type_id=-1, custom_def=method_def)

        type_id = self.__add_type(method_ty)
        return type_id

    def alloc_function(self, name: str) -> int:
        function_def = Type.FunctionDef(name=name)
        function_ty = Type.FunctionType(type_id=-1, custom_def=function_def)

        type_id = self.__add_type(function_ty)
        return type_id

    def alloc_range(self, type_id: int) -> int:
        return self.alloc_instance(self.Range_id, [type_id])

    def alloc_instance(self, type_id: int, generic_args: list[int]) -> int:
        """
        Given an uninstantiated type Ty<T1, T2, ..., Tn>, and a list of generic argument type IDs [A1, A2, ..., An],
        allocate an instantiated type Ty<A1, A2, ..., An>.
        """
        ty = self.__space[type_id]

        if not isinstance(ty, Type.CustomType):
            raise ValueError(f"Type ID {type_id} is not a custom type and cannot be instantiated")
        if len(ty.generic_args) != len(generic_args):
            raise ValueError(f"Generic argument count mismatch for type ID {type_id}")

        if len(ty.custom_def.generics) == 0:
            if len(generic_args) != 0:
                raise ValueError(f"Type ID {type_id} is not generic and cannot be instantiated with generic arguments")
            return type_id

        key = (id(ty.custom_def), tuple(generic_args))
        if key in self.__instance_cache:
            return self.__instance_cache[key]

        match ty:
            case Type.StructType(custom_def=struct_def):
                instance_ty = Type.StructType(type_id=-1, custom_def=struct_def, generic_args=generic_args)
            case Type.EnumType(custom_def=enum_def):
                instance_ty = Type.EnumType(type_id=-1, custom_def=enum_def, generic_args=generic_args)
            case Type.TraitType(custom_def=trait_def):
                instance_ty = Type.TraitType(type_id=-1, custom_def=trait_def, generic_args=generic_args)
            case Type.MethodType(custom_def=method_def):
                instance_ty = Type.MethodType(type_id=-1, custom_def=method_def, generic_args=generic_args)
            case Type.FunctionType(custom_def=function_def):
                instance_ty = Type.FunctionType(type_id=-1, custom_def=function_def, generic_args=generic_args)
            case Type.AliasType(custom_def=alias_def):
                instance_ty = Type.AliasType(type_id=-1, custom_def=alias_def, generic_args=generic_args)

        instance_ty_id = self.__add_type(instance_ty)
        self.__instance_cache[key] = instance_ty_id
        return instance_ty_id

    def instantiate(self, type_id: int, substs: dict[int, int]) -> int:
        """
        Given a type `ty` that may contain generic type parameters, and a substitution map `substs`,
        return a new type where the generic type parameters are replaced by the corresponding types in `substs`.
        """
        if len(substs) == 0:
            return type_id

        ty = self.__space[type_id]
        match ty:
            case Type.GenericType(type_id=generic_type_id):
                if generic_type_id in substs:
                    return substs[generic_type_id]
                return type_id
            case Type.StructType(generic_args=generic_args) | Type.EnumType(generic_args=generic_args) \
                    | Type.TraitType(generic_args=generic_args) | Type.MethodType(generic_args=generic_args) | Type.FunctionType(generic_args=generic_args):
                instantiated_args = [self.instantiate(arg_id, substs) for arg_id in generic_args]
                return self.alloc_instance(type_id, instantiated_args)
            case Type.PointerType(pointee_type=pointee_type):
                instantiated_pointee = self.instantiate(pointee_type, substs)
                return self.alloc_pointer(instantiated_pointee)
            case Type.SliceType(element_type=element_type):
                instantiated_element = self.instantiate(element_type, substs)
                return self.alloc_slice(instantiated_element)
            case Type.ArrayType(element_type=element_type, length=length):
                instantiated_element = self.instantiate(element_type, substs)
                return self.alloc_array(instantiated_element, length)
            case Type.TupleType(element_types=element_types):
                instantiated_elements = [self.instantiate(elem_id, substs) for elem_id in element_types]
                return self.alloc_tuple(instantiated_elements)
            case Type.FunctionPointerType(parameter_types=param_types, return_type=return_type):
                instantiated_params = [self.instantiate(param_id, substs) for param_id in param_types]
                instantiated_return = self.instantiate(return_type, substs)
                return self.alloc_function_pointer(instantiated_params, instantiated_return)
            case _:
                return type_id

    def get_name(self, type_id: int) -> str:
        if type_id in self.__name_cache:
            return self.__name_cache[type_id]

        ty = self.__space[type_id]
        match ty:
            case Type.VoidType():
                name = "void"
            case Type.BoolType():
                name = "bool"
            case Type.CharType():
                name = "char"
            case Type.StrType():
                name = "str"
            case Type.IntType(size=size, signed=signed):
                prefix = "i" if signed else "u"
                name = f"{prefix}{size * 8}"
            case Type.FloatType(size=size):
                name = f"f{size * 8}"
            case Type.IntLiteralType():
                name = "IntLiteralType"
            case Type.FloatLiteralType():
                name = "FloatLiteralType"
            case Type.PointerType(pointee_type=pointee_type):
                pointee_name = self.get_name(pointee_type)
                name = f"{pointee_name}*"
            case Type.SliceType(element_type=element_type):
                element_name = self.get_name(element_type)
                name = f"{element_name}[]"
            case Type.ArrayType(element_type=element_type, length=length):
                element_name = self.get_name(element_type)
                name = f"{element_name}[{length}]"
            case Type.TupleType(element_types=element_types):
                element_names = [self.get_name(elem_id) for elem_id in element_types]
                name = f"({', '.join(element_names)})"
            case Type.FunctionPointerType(parameter_types=param_types, return_type=return_type):
                param_names = [self.get_name(param_id) for param_id in param_types]
                return_name = self.get_name(return_type)
                name = f"fn({', '.join(param_names)}) -> {return_name}"
            case Type.GenericType(name=name):
                pass
            case Type.StructType(custom_def=custom_def, generic_args=generic_args):
                if len(generic_args) == 0:
                    name = custom_def.name
                else:
                    generic_arg_names = [self.get_name(arg_id) for arg_id in generic_args]
                    name = f"{custom_def.name}<{', '.join(generic_arg_names)}>"
            case Type.EnumType(custom_def=custom_def, generic_args=generic_args):
                if len(generic_args) == 0:
                    name = custom_def.name
                else:
                    generic_arg_names = [self.get_name(arg_id) for arg_id in generic_args]
                    name = f"{custom_def.name}<{', '.join(generic_arg_names)}>"
            case Type.TraitType(custom_def=custom_def, generic_args=generic_args):
                if len(generic_args) == 0:
                    name = custom_def.name
                else:
                    generic_arg_names = [self.get_name(arg_id) for arg_id in generic_args]
                    name = f"{custom_def.name}<{', '.join(generic_arg_names)}>"
            case Type.MethodType(custom_def=custom_def, generic_args=generic_args):
                receiver_name = self.get_name(custom_def.receiver_type)
                if len(generic_args) == 0:
                    name = f"{receiver_name}::{custom_def.name}"
                else:
                    generic_arg_names = [self.get_name(arg_id) for arg_id in generic_args]
                    name = f"{receiver_name}::{custom_def.name}<{', '.join(generic_arg_names)}>"
            case Type.FunctionType(custom_def=custom_def, generic_args=generic_args):
                if len(generic_args) == 0:
                    name = custom_def.name
                else:
                    generic_arg_names = [self.get_name(arg_id) for arg_id in generic_args]
                    name = f"{custom_def.name}<{', '.join(generic_arg_names)}>"
            case Type.AliasType(custom_def=custom_def, generic_args=generic_args):
                if len(generic_args) == 0:
                    name = custom_def.name
                else:
                    generic_arg_names = [self.get_name(arg_id) for arg_id in generic_args]
                    name = f"{custom_def.name}<{', '.join(generic_arg_names)}>"

        self.__name_cache[type_id] = name
        return name

    def infer_common_type(self, type_ids: list[int], span: SrcSpan, context_name: str) -> int:
        """Infer a common type from a list of types.

        Literal-only types are resolved by repeatedly merging literal types,
        while types with any concrete element type must agree on that type.
        """
        definite_type_ids = [type_id for type_id in type_ids if not self.is_literal_type(type_id)]

        if definite_type_ids:
            first_type_id = definite_type_ids[0]
            if any(type_id != first_type_id for type_id in definite_type_ids[1:]):
                element_names = ", ".join(self.get_name(type_id) for type_id in type_ids)
                raise AnalysisError(f"{context_name} must have a compatible type, got [{element_names}]", span)
            return first_type_id

        return self.common_literal_type(type_ids, span)

    def common_literal_type(self, type_ids: list[int], span: SrcSpan) -> int:
        """Fold a list of literal type IDs into the most specific common type."""
        result_type_id = type_ids[0]
        for next_type_id in type_ids[1:]:
            result_type_id = self.gcd_literal_type(result_type_id, next_type_id, span)
        return result_type_id

    def gcd_literal_type(self, left_type_id: int, right_type_id: int, span: SrcSpan) -> int:
        """Compute the greatest common literal type for two type IDs.

        This supports nested array and tuple literals so compound literals can
        still be inferred when their components are compatible.
        """
        if left_type_id == right_type_id:
            return left_type_id

        left_ty = self[left_type_id]
        right_ty = self[right_type_id]

        if isinstance(left_ty, Type.IntLiteralType):
            if isinstance(right_ty, Type.IntLiteralType):
                return self.int_literal_id
            if isinstance(right_ty, Type.FloatLiteralType):
                return self.float_literal_id
        if isinstance(left_ty, Type.FloatLiteralType):
            if isinstance(right_ty, Type.IntLiteralType | Type.FloatLiteralType):
                return self.float_literal_id

        if isinstance(left_ty, Type.ArrayType) and isinstance(right_ty, Type.ArrayType):
            if left_ty.length != right_ty.length:
                left_name = self.get_name(left_type_id)
                right_name = self.get_name(right_type_id)
                raise AnalysisError(f"array elements must have a compatible type, got [{left_name}, {right_name}]", span)
            element_type_id = self.gcd_literal_type(left_ty.element_type, right_ty.element_type, span)
            return self.alloc_array(element_type_id, left_ty.length)

        if isinstance(left_ty, Type.TupleType) and isinstance(right_ty, Type.TupleType):
            if len(left_ty.element_types) != len(right_ty.element_types):
                left_name = self.get_name(left_type_id)
                right_name = self.get_name(right_type_id)
                raise AnalysisError(f"array elements must have a compatible type, got [{left_name}, {right_name}]", span)
            element_types = [
                self.gcd_literal_type(left_elem, right_elem, span)
                for left_elem, right_elem in zip(left_ty.element_types, right_ty.element_types)
            ]
            return self.alloc_tuple(element_types)

        left_name = self.get_name(left_type_id)
        right_name = self.get_name(right_type_id)
        raise AnalysisError(f"array elements must have a compatible type, got [{left_name}, {right_name}]", span)

    def is_literal_type(self, type_id: int) -> bool:
        ty = self[type_id]
        match ty:
            case Type.IntLiteralType() | Type.FloatLiteralType():
                return True
            case Type.ArrayType(element_type=element_type):
                return self.is_literal_type(element_type)
            case Type.TupleType(element_types=element_types):
                return all(self.is_literal_type(element_type) for element_type in element_types)
            case _:
                return False

    def finalize(self) -> None:
        """
        Final check and preparation of the type space before code generation.

        1. Check self-referential types.
        """
        self.__check_self_referential_types()

    def __check_self_referential_types(self) -> None:
        """
        Check for self-referential types that would cause infinite recursion during code generation.
        For example, a struct that contains itself directly or indirectly.

        This is a simple DFS-based cycle detection in the type graph.
        """
        visited: set[int] = set()
        stack: list[int] = []
        in_stack: set[int] = set()

        def visit(type_id: int) -> None:
            if type_id in in_stack:
                chain = " -> ".join(self.get_name(tid) for tid in stack) + f" -> {self.get_name(type_id)}"
                raise CompilerError(f"Self-referential type detected: {chain}")
            if type_id in visited:
                return
            visited.add(type_id)
            stack.append(type_id)
            in_stack.add(type_id)

            try:
                ty = self.__space[type_id]
                match ty:
                    case Type.ArrayType(element_type=element_type):
                        visit(element_type)
                    case Type.TupleType(element_types=element_types):
                        for elem_id in element_types:
                            visit(elem_id)
                    case Type.StructType():
                        for field in ty.get_fields(self):
                            visit(field.type_id)
                    case Type.EnumType():
                        for variant in ty.get_variants(self):
                            if variant.payload_type is not None:
                                visit(variant.payload_type)
                    case _:
                        pass
            finally:
                stack.pop()
                in_stack.remove(type_id)

        for type_id in list(self.__space):
            visit(type_id)

    def is_instance(self, template_id: int, generic_args: list[int], target_id: int) -> bool:
        """
        Check if the instantiated type of `template_id` with `generic_args` is the same as `target_id`.
        """
        template_ty = self.__space[template_id]
        if not isinstance(template_ty, Type.CustomType):
            raise CompilerError(f"Type ID {template_id} is not a custom type and cannot be instantiated")

        target_ty = self.__space[target_id]
        if not isinstance(target_ty, Type.CustomType):
            raise CompilerError(f"Type ID {target_id} is not a custom type and cannot be compared for instance")

        if id(template_ty.custom_def) != id(target_ty.custom_def):
            return False

        for arg_id, target_arg_id in zip(generic_args, target_ty.generic_args):
            if arg_id != target_arg_id:
                return False

        return True

    def resolve_type(self, ty: ASTType, symbol_ctx: SymbolCtx) -> int:
        """
        Resolve an ASTType to a type ID in the type context.

        This is used during type checking to convert the types written in the source code (AST) to the internal type representation.
        """
        return self.__resolver.resolve(ty, symbol_ctx)

    def register_impl(self, span: SrcSpan, generics: list[int], target: int, trait: int | None) -> Impl:
        return self.__impl_registry.register_impl(span, generics, target, trait)

    def check_impls(self) -> None:
        """
        Check the validity of all registered impls.

        This should be called after all impls are registered.
        """
        self.__impl_registry.check_impls()

    def add_procedure(self, type_id: int, body: AST.Block, unit_id: int) -> None:
        ty = self.__space[type_id]
        if isinstance(ty, Type.FunctionType):
            def_id = id(ty.custom_def)
        elif isinstance(ty, Type.MethodType):
            def_id = id(ty.custom_def)
        else:
            raise CompilerError(f"Type ID {type_id} is not a function or method type and cannot be associated with a procedure")

        self.__procedures[def_id] = (body, unit_id)

    def get_procedure(self, type_id: int) -> tuple[AST.Block, int]:
        ty = self.__space[type_id]
        if isinstance(ty, Type.FunctionType):
            def_id = id(ty.custom_def)
        elif isinstance(ty, Type.MethodType):
            def_id = id(ty.custom_def)
        else:
            raise CompilerError(f"Type ID {type_id} is not a function or method type and cannot be associated with a procedure")

        if def_id in self.__procedures:
            return self.__procedures[def_id]
        else:
            raise CompilerError(f"No procedure found for type ID {type_id} with definition ID {def_id}")

    def method_lookup(self, receiver: HIR.Expr, method_name: str, generic_args: list[int] | None, args: list[HIR.Expr]) -> LookupResult | None:
        """
        Lookup a method for a given caller type. See details in `manual/impl.md`.
        """
        candidates: list[LookupResult] = []

        for impl in self.__impl_registry.iter_impls():
            if method_name not in impl.methods:
                continue

            receiver_inference = GenericInference(self, receiver.span)
            try:
                receiver_inference.constrain(impl.target, receiver.type_id)
                impl_substs = receiver_inference.substitutions()
            except AnalysisError:
                continue

            method_id = impl.methods[method_name]
            instantiated_method_id = self.instantiate(method_id, impl_substs)
            instantiated_method_ty = self[instantiated_method_id]
            assert isinstance(instantiated_method_ty, Type.MethodType)

            if generic_args is not None and len(generic_args) > 0:
                method_generics = instantiated_method_ty.custom_def.generics
                if len(generic_args) > len(method_generics):
                    continue
                explicit_generics = method_generics[len(method_generics) - len(generic_args):]
                explicit_substs = dict(zip(explicit_generics, generic_args))
                instantiated_method_id = self.instantiate(instantiated_method_id, explicit_substs)
                instantiated_method_ty = self[instantiated_method_id]
                assert isinstance(instantiated_method_ty, Type.MethodType)

            parameters = instantiated_method_ty.parameters(self)
            if len(parameters) != len(args):
                continue

            arg_inference = GenericInference(self, receiver.span)
            try:
                for param, arg in zip(parameters, args):
                    arg_inference.constrain(param.type_id, arg.type_id)
                arg_substs = arg_inference.substitutions()
            except AnalysisError:
                continue

            final_substs = impl_substs | arg_substs
            final_method_id = self.instantiate(instantiated_method_id, final_substs)
            candidates.append(LookupResult(method_id=final_method_id, deref_count=0, impl=impl))

        if len(candidates) == 1:
            return candidates[0]
        if len(candidates) > 1:
            raise AnalysisError(f"Ambiguous method '{method_name}' for type '{self.get_name(receiver.type_id)}'", receiver.span)
        return None

    def iter_item_type(self, iter_type_id: int) -> int:
        """
        Get the item type of an iterator type.

        This is used for desugaring for loops, where we need to know the item type of the iterator to type check the loop variable.
        """
        raise NotImplementedError("Iterator item type lookup is not implemented yet")


@dataclass
class LookupResult:
    """Result of method lookup"""
    method_id: int
    deref_count: int
    impl: Impl
