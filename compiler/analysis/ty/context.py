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
        self.__function_pointer_cache: dict[tuple[int, ...], int] = {}  # (param type ids, return type id) -> function pointer type id
        # =============================================

        # === cache from template to instance ===
        self.__instance_cache: dict[tuple[int, tuple[int, ...]], int] = {}  # (template type id, generic arg type ids) -> instance type id
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
