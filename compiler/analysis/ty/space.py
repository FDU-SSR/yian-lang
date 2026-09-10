from __future__ import annotations

from typing import TYPE_CHECKING

from compiler.analysis.ty import ty as Type
from compiler.error import CompilerError
from compiler.frontend.lex.position import SrcSpan

if TYPE_CHECKING:
    from compiler.analysis.ty.context import TypeCtx


class TypeSpace:
    def __init__(self, ctx: TypeCtx):
        self.__ctx = ctx
        self.__space: dict[int, Type.Ty] = {}
        self.__next_id = 500

        self.__pointer_cache: dict[int, int] = {}
        self.__slice_cache: dict[int, int] = {}
        self.__array_cache: dict[tuple[int, int], int] = {}
        self.__tuple_cache: dict[tuple[int, ...], int] = {}
        self.__function_pointer_cache: dict[tuple[tuple[int, ...], int], int] = {}
        self.__instance_cache: dict[tuple[int, tuple[int, ...]], int] = {}
        self.__literal_cache: dict[tuple[int | bool, int], int] = {}

        self.__add_intrinsic_types()

    def __iter__(self):
        return iter(self.__space)

    def __contains__(self, type_id: int) -> bool:
        return type_id in self.__space

    def __getitem__(self, type_id: int) -> Type.Ty:
        if type_id not in self.__space:
            raise CompilerError(f"Type ID {type_id} does not exist in the type context")
        return self.__space[type_id]

    def items(self):
        return self.__space.items()

    def __setitem__(self, type_id: int, ty: Type.Ty) -> None:
        self.__space[type_id] = ty

    def __force_add_type(self, ty: Type.Ty) -> None:
        self.__space[ty.type_id] = ty

    def __add_intrinsic_types(self) -> None:
        self.__force_add_type(Type.NeverType(type_id=self.__ctx.never_id))
        self.__force_add_type(Type.VoidType(type_id=self.__ctx.void_id))
        self.__force_add_type(Type.BoolType(type_id=self.__ctx.bool_id))
        self.__force_add_type(Type.CharType(type_id=self.__ctx.char_id))
        self.__force_add_type(Type.StrType(type_id=self.__ctx.str_id))

        self.__force_add_type(Type.IntType(type_id=self.__ctx.i8_id, size=1, signed=True))
        self.__force_add_type(Type.IntType(type_id=self.__ctx.i16_id, size=2, signed=True))
        self.__force_add_type(Type.IntType(type_id=self.__ctx.i32_id, size=4, signed=True))
        self.__force_add_type(Type.IntType(type_id=self.__ctx.i64_id, size=8, signed=True))

        self.__force_add_type(Type.IntType(type_id=self.__ctx.u8_id, size=1, signed=False))
        self.__force_add_type(Type.IntType(type_id=self.__ctx.u16_id, size=2, signed=False))
        self.__force_add_type(Type.IntType(type_id=self.__ctx.u32_id, size=4, signed=False))
        self.__force_add_type(Type.IntType(type_id=self.__ctx.u64_id, size=8, signed=False))

        self.__force_add_type(Type.FloatType(type_id=self.__ctx.f16_id, size=2))
        self.__force_add_type(Type.FloatType(type_id=self.__ctx.f32_id, size=4))
        self.__force_add_type(Type.FloatType(type_id=self.__ctx.f64_id, size=8))

        self.__force_add_type(Type.IntLiteralType(type_id=self.__ctx.int_literal_id))
        self.__force_add_type(Type.FloatLiteralType(type_id=self.__ctx.float_literal_id))

    def __add_type(self, ty: Type.Ty) -> int:
        if ty.type_id == -1:
            ty.type_id = self.__next_id
            self.__next_id += 1

        if ty.type_id in self.__space:
            raise CompilerError(f"Type ID {ty.type_id} already exists in the type context")

        self.__space[ty.type_id] = ty
        return ty.type_id

    def alloc_generic(self, name: str) -> int:
        return self.__add_type(Type.GenericType(type_id=-1, name=name))

    def alloc_const_generic(self, name: str, value_type: int) -> int:
        return self.__add_type(Type.ConstGenericType(type_id=-1, name=name, value_type=value_type))

    def alloc_literal_value(self, value: int | bool, value_type: int) -> int:
        key = (value, value_type)
        if key in self.__literal_cache:
            return self.__literal_cache[key]
        ty_id = self.__add_type(Type.LiteralValueType(type_id=-1, value=value, value_type=value_type))
        self.__literal_cache[key] = ty_id
        return ty_id

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
        # length is a TypeId: LiteralValueType (concrete) or ConstGenericType (generic)
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

    def alloc_alias(self, name: str, span: SrcSpan) -> int:
        alias_def = Type.AliasDef(name=name, span=span)
        alias_ty = Type.AliasType(type_id=-1, custom_def=alias_def)
        return self.__add_type(alias_ty)

    def alloc_struct(self, name: str, span: SrcSpan) -> int:
        struct_def = Type.StructDef(name=name, span=span)
        struct_ty = Type.StructType(type_id=-1, custom_def=struct_def)

        if name == "Range":
            struct_ty.type_id = self.__ctx.Range_id

        return self.__add_type(struct_ty)

    def alloc_unnamed_struct(self, owner: str, field_names: list[str], field_types: list[int], generics: list[int], span: SrcSpan) -> int:
        if len(field_names) != len(field_types):
            raise CompilerError(f"Field names and types count mismatch for unnamed struct in {owner}")

        struct_def = Type.StructDef(name=f"{owner}::{{unnamed}}", span=span)
        struct_def.generics = generics.copy()
        for index, (field_name, field_type) in enumerate(zip(field_names, field_types)):
            struct_def.fields.append(Type.StructField(name=field_name, type_id=field_type, access_mode=Type.AccessMode.Public, index=index))
        struct_ty = Type.StructType(type_id=-1, custom_def=struct_def, generic_args=generics.copy())
        return self.__add_type(struct_ty)

    def alloc_enum(self, name: str, span: SrcSpan) -> int:
        enum_def = Type.EnumDef(name=name, span=span)
        enum_ty = Type.EnumType(type_id=-1, custom_def=enum_def)

        if name == "Option":
            enum_ty.type_id = self.__ctx.Option_id
        elif name == "Result":
            enum_ty.type_id = self.__ctx.Result_id

        return self.__add_type(enum_ty)

    def alloc_trait(self, name: str, span: SrcSpan) -> int:
        trait_def = Type.TraitDef(name=name, span=span)
        trait_ty = Type.TraitType(type_id=-1, custom_def=trait_def)

        intrinsic_mapping = {
            "Add": self.__ctx.add_id,
            "Sub": self.__ctx.sub_id,
            "Mul": self.__ctx.mul_id,
            "Div": self.__ctx.div_id,
            "Rem": self.__ctx.rem_id,
            "Neg": self.__ctx.neg_id,
            "BitAnd": self.__ctx.bitand_id,
            "BitOr": self.__ctx.bitor_id,
            "BitXor": self.__ctx.bitxor_id,
            "BitNot": self.__ctx.bitnot_id,
            "Shl": self.__ctx.shl_id,
            "Shr": self.__ctx.shr_id,
            "PartialEq": self.__ctx.partial_eq_id,
            "PartialOrd": self.__ctx.partial_ord_id,
            "Index": self.__ctx.index_id,
            "Contains": self.__ctx.contains_id,
            "Deref": self.__ctx.deref_id,
            "Delete": self.__ctx.delete_id,
            "Drop": self.__ctx.drop_id,
            # Compound assignment
            "AddAssign": self.__ctx.add_assign_id,
            "SubAssign": self.__ctx.sub_assign_id,
            "MulAssign": self.__ctx.mul_assign_id,
            "DivAssign": self.__ctx.div_assign_id,
            "RemAssign": self.__ctx.rem_assign_id,
            "BitAndAssign": self.__ctx.bitand_assign_id,
            "BitOrAssign": self.__ctx.bitor_assign_id,
            "BitXorAssign": self.__ctx.bitxor_assign_id,
            "ShlAssign": self.__ctx.shl_assign_id,
            "ShrAssign": self.__ctx.shr_assign_id,
        }
        if name in intrinsic_mapping:
            trait_ty.type_id = intrinsic_mapping[name]

        return self.__add_type(trait_ty)

    def alloc_method(self, name: str, span: SrcSpan) -> int:
        method_def = Type.MethodDef(name=name, span=span)
        method_ty = Type.MethodType(type_id=-1, custom_def=method_def)
        return self.__add_type(method_ty)

    def alloc_function(self, name: str, span: SrcSpan) -> int:
        function_def = Type.FunctionDef(name=name, span=span)
        function_ty = Type.FunctionType(type_id=-1, custom_def=function_def)
        return self.__add_type(function_ty)

    def alloc_closure(self, captured_vars: list[Type.CapturedVar], parameters: list[Type.Parameter], return_type: int, span: SrcSpan) -> int:
        closure_ty = Type.ClosureType(
            type_id=-1,
            captured_vars=captured_vars,
            parameters=parameters,
            return_type=return_type,
            span=span,
        )
        return self.__add_type(closure_ty)

    def alloc_range(self, type_id: int) -> int:
        return self.alloc_instance(self.__ctx.Range_id, [type_id])

    def alloc_instance(self, type_id: int, generic_args: list[int]) -> int:
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
