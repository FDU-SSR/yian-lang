from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import TypeAlias

from compiler.config.constants import AccessMode


@dataclass
class VoidType:
    type_id: int


@dataclass
class BoolType:
    type_id: int


@dataclass
class CharType:
    type_id: int


@dataclass
class StrType:
    type_id: int


@dataclass
class IntType:
    type_id: int
    size: int
    signed: bool


@dataclass
class FloatType:
    type_id: int
    size: int


@dataclass
class GenericType:
    type_id: int
    name: str


@dataclass
class ArrayType:
    type_id: int
    element_type: int
    length: int


@dataclass
class TupleType:
    type_id: int
    element_types: list[int]


@dataclass
class PointerType:
    type_id: int
    pointee_type: int


@dataclass
class SliceType:
    type_id: int
    element_type: int


@dataclass
class StructField:
    name: str
    type_id: int
    access_mode: AccessMode
    index: int


@dataclass
class StructDef:
    name: str
    generics: list[int] = field(default_factory=list[int])
    fields: list[StructField] = field(default_factory=list[StructField])


@dataclass
class StructType:
    type_id: int
    struct_def: StructDef


@dataclass
class EnumVariant:
    name: str
    payload_type: int | None
    discriminant: int


@dataclass
class EnumDef:
    name: str
    generics: list[int] = field(default_factory=list[int])
    variants: list[EnumVariant] = field(default_factory=list[EnumVariant])


@dataclass
class EnumType:
    type_id: int
    enum_def: EnumDef


@dataclass
class Parameter:
    name: str
    type_id: int


@dataclass
class FunctionDef:
    name: str
    generics: list[int] = field(default_factory=list[int])
    parameters: list[Parameter] = field(default_factory=list[Parameter])
    return_type: int = -1


@dataclass
class FunctionType:
    type_id: int
    function_def: FunctionDef


@dataclass
class MethodDef:
    name: str
    generics: list[int] = field(default_factory=list[int])
    receiver_type: int = -1
    parameters: list[Parameter] = field(default_factory=list[Parameter])
    return_type: int = -1
    is_static: bool = False
    is_header: bool = True


@dataclass
class MethodType:
    type_id: int
    method_def: MethodDef


@dataclass
class TraitDef:
    name: str
    generics: list[int] = field(default_factory=list[int])
    methods: dict[str, int] = field(default_factory=dict[str, int])  # method name -> method type id


@dataclass
class TraitType:
    type_id: int
    trait_def: TraitDef


@dataclass
class FunctionPointerType:
    type_id: int
    parameter_types: list[int]
    return_type: int


BasicType: TypeAlias = (
    VoidType | BoolType | CharType | StrType
    | IntType | FloatType
)

DerivedType: TypeAlias = (
    ArrayType | TupleType | PointerType | SliceType | FunctionPointerType
)


CustomType: TypeAlias = (
    StructType | EnumType | TraitType
    | MethodType | FunctionType
)

Ty: TypeAlias = BasicType | DerivedType | CustomType | GenericType


class IntrinsicType(Enum):
    Void = "void"
    Bool = "bool"
    Char = "char"
    Str = "str"

    I8 = "i8"
    I16 = "i16"
    I32 = "i32"
    I64 = "i64"

    U8 = "u8"
    U16 = "u16"
    U32 = "u32"
    U64 = "u64"

    F16 = "f16"
    F32 = "f32"
    F64 = "f64"

    Int = "int"
    UInt = "uint"
    Float = "float"


class IntrinsicCustomType(Enum):
    Add = "Add"
    Sub = "Sub"
    Mul = "Mul"
    Div = "Div"
    Rem = "Rem"
    Neg = "Neg"

    BitAnd = "BitAnd"
    BitOr = "BitOr"
    BitXor = "BitXor"
    BitNot = "BitNot"
    Shl = "Shl"
    Shr = "Shr"

    PartialEq = "PartialEq"
    PartialOrd = "PartialOrd"

    Index = "Index"
    Contains = "Contains"
    Deref = "Deref"
    Delete = "Delete"
    Drop = "Drop"

    Range = "Range"
    Option = "Option"
    Result = "Result"
