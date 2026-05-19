from compiler.analysis.ty import ty as Type
from compiler.utils.errors.yian_error import CompilerError


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

        # === NPO (Null Pointer Optimization) cache ===
        self.__npo_cache: dict[int, tuple[bool, list[int]]] = {}
        self.__npo_payload_inner_type: dict[int, int] = {}
        # ==============================================

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

    def alloc_struct(self, name: str) -> int:
        struct_def = Type.StructDef(name=name)
        struct_ty = Type.StructType(type_id=-1, struct_def=struct_def)

        intrinsic_mapping = {
            "Range": self.Range_id,
        }
        if name in intrinsic_mapping:
            struct_ty.type_id = intrinsic_mapping[name]

        type_id = self.__add_type(struct_ty)
        return type_id

    def alloc_enum(self, name: str) -> int:
        enum_def = Type.EnumDef(name=name)
        enum_ty = Type.EnumType(type_id=-1, enum_def=enum_def)

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
        trait_ty = Type.TraitType(type_id=-1, trait_def=trait_def)

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
        method_ty = Type.MethodType(type_id=-1, method_def=method_def)

        type_id = self.__add_type(method_ty)
        return type_id

    def alloc_function(self, name: str) -> int:
        function_def = Type.FunctionDef(name=name)
        function_ty = Type.FunctionType(type_id=-1, function_def=function_def)

        type_id = self.__add_type(function_ty)
        return type_id
