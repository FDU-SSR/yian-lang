from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum, auto
from typing import TYPE_CHECKING, TypeAlias

from compiler.frontend.lex.position import SrcSpan

if TYPE_CHECKING:
    from compiler.analysis.ty.context import TypeCtx


class AccessMode(Enum):
    Private = auto()
    Public = auto()

    @classmethod
    def from_str(cls, s: str):
        match s:
            case "private":
                return cls.Private
            case "public":
                return cls.Public
            case _:
                raise ValueError(f"{s} is not a access mode")


@dataclass
class VoidType:
    type_id: int


@dataclass
class NeverType:
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
class IntLiteralType:
    type_id: int


@dataclass
class FloatLiteralType:
    type_id: int


@dataclass
class GenericType:
    type_id: int
    name: str


@dataclass
class ConstGenericType:
    type_id: int
    name: str
    value_type: int      # 常量的类型，如 u64_id


@dataclass
class LiteralValueType:
    type_id: int
    value: int | bool     # 常量的具体值
    value_type: int       # 值的类型，如 u64_id


@dataclass
class ArrayType:
    type_id: int
    element_type: int
    length: int           # TypeId → ConstGenericType | LiteralValueType


@dataclass
class TupleType:
    type_id: int
    element_types: list[int]


@dataclass
class PointerType:
    type_id: int
    pointee_type: int


@dataclass
class RefType:
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
    span: SrcSpan
    generics: list[int] = field(default_factory=list[int])
    fields: list[StructField] = field(default_factory=list[StructField])
    unit_id: int = -1
    is_bitcopy: bool = False


@dataclass
class StructType:
    type_id: int
    custom_def: StructDef
    generic_args: list[int] = field(default_factory=list[int])

    @property
    def unit_id(self) -> int:
        return self.custom_def.unit_id

    def get_fields(self, context: TypeCtx) -> list[StructField]:
        substs = dict(zip(self.custom_def.generics, self.generic_args))
        fields: list[StructField] = []
        for field_ in self.custom_def.fields:
            fields.append(StructField(
                name=field_.name,
                type_id=context.instantiate(field_.type_id, substs),
                access_mode=field_.access_mode,
                index=field_.index,
            ))
        return fields

    def get_field_by_name(self, name: str, context: TypeCtx) -> StructField | None:
        for field_ in self.custom_def.fields:
            if field_.name == name:
                substs = dict(zip(self.custom_def.generics, self.generic_args))
                return StructField(
                    name=field_.name,
                    type_id=context.instantiate(field_.type_id, substs),
                    access_mode=field_.access_mode,
                    index=field_.index,
                )
        return None


@dataclass
class EnumVariant:
    name: str
    payload_type: int | None
    discriminant: int


@dataclass
class EnumDef:
    name: str
    span: SrcSpan
    generics: list[int] = field(default_factory=list[int])
    variants: list[EnumVariant] = field(default_factory=list[EnumVariant])
    unit_id: int = -1
    is_bitcopy: bool = False


@dataclass
class EnumType:
    type_id: int
    custom_def: EnumDef
    generic_args: list[int] = field(default_factory=list[int])

    @property
    def unit_id(self) -> int:
        return self.custom_def.unit_id

    def get_variants(self, context: TypeCtx) -> list[EnumVariant]:
        substs = dict(zip(self.custom_def.generics, self.generic_args))
        variants: list[EnumVariant] = []
        for variant in self.custom_def.variants:
            payload_type = None
            if variant.payload_type is not None:
                payload_type = context.instantiate(variant.payload_type, substs)
            variants.append(EnumVariant(
                name=variant.name,
                payload_type=payload_type,
                discriminant=variant.discriminant,
            ))
        return variants

    def get_variant_by_name(self, name: str, context: TypeCtx) -> EnumVariant | None:
        for variant in self.custom_def.variants:
            if variant.name == name:
                substs = dict(zip(self.custom_def.generics, self.generic_args))
                payload_type = None
                if variant.payload_type is not None:
                    payload_type = context.instantiate(variant.payload_type, substs)
                return EnumVariant(
                    name=variant.name,
                    payload_type=payload_type,
                    discriminant=variant.discriminant,
                )
        return None


@dataclass
class Parameter:
    name: str
    type_id: int


@dataclass
class FunctionDef:
    name: str
    span: SrcSpan
    generics: list[int] = field(default_factory=list[int])
    parameters: list[Parameter] = field(default_factory=list[Parameter])
    return_type: int = -1


@dataclass
class FunctionType:
    type_id: int
    custom_def: FunctionDef
    generic_args: list[int] = field(default_factory=list[int])

    def return_type(self, context: TypeCtx) -> int:
        substs = dict(zip(self.custom_def.generics, self.generic_args))
        return context.instantiate(self.custom_def.return_type, substs)

    def parameters(self, context: TypeCtx) -> list[Parameter]:
        substs = dict(zip(self.custom_def.generics, self.generic_args))
        parameters: list[Parameter] = []
        for param in self.custom_def.parameters:
            parameters.append(Parameter(
                name=param.name,
                type_id=context.instantiate(param.type_id, substs),
            ))
        return parameters

    def as_pointer(self, context: TypeCtx) -> int:
        param_types = [p.type_id for p in self.parameters(context)]
        ret_type = self.return_type(context)
        return context.alloc_function_pointer(param_types, ret_type)


@dataclass
class MethodDef:
    name: str
    span: SrcSpan
    generics: list[int] = field(default_factory=list[int])
    receiver_type: int = -1
    parameters: list[Parameter] = field(default_factory=list[Parameter])
    return_type: int = -1
    is_static: bool = False
    is_header: bool = True


@dataclass
class MethodType:
    type_id: int
    custom_def: MethodDef
    generic_args: list[int] = field(default_factory=list[int])

    @property
    def is_static(self) -> bool:
        return self.custom_def.is_static

    def receiver_type(self, context: TypeCtx) -> int:
        substs = dict(zip(self.custom_def.generics, self.generic_args))
        return context.instantiate(self.custom_def.receiver_type, substs)

    def return_type(self, context: TypeCtx) -> int:
        substs = dict(zip(self.custom_def.generics, self.generic_args))
        return context.instantiate(self.custom_def.return_type, substs)

    def parameters(self, context: TypeCtx) -> list[Parameter]:
        substs = dict(zip(self.custom_def.generics, self.generic_args))
        parameters: list[Parameter] = []
        for param in self.custom_def.parameters:
            parameters.append(Parameter(
                name=param.name,
                type_id=context.instantiate(param.type_id, substs),
            ))
        return parameters


@dataclass
class TraitDef:
    name: str
    span: SrcSpan
    generics: list[int] = field(default_factory=list[int])
    methods: dict[str, int] = field(default_factory=dict[str, int])  # method name -> method type id


@dataclass
class TraitType:
    type_id: int
    custom_def: TraitDef
    generic_args: list[int] = field(default_factory=list[int])

    def get_methods(self, context: TypeCtx) -> dict[str, int]:
        substs = dict(zip(self.custom_def.generics, self.generic_args))
        methods: dict[str, int] = {}
        for method_name, method_type_id in self.custom_def.methods.items():
            methods[method_name] = context.instantiate(method_type_id, substs)
        return methods

    def get_method_by_name(self, name: str, context: TypeCtx) -> int | None:
        substs = dict(zip(self.custom_def.generics, self.generic_args))
        for method_name, method_type_id in self.custom_def.methods.items():
            if method_name == name:
                return context.instantiate(method_type_id, substs)
        return None


@dataclass
class FunctionPointerType:
    type_id: int
    parameter_types: list[int]
    return_type: int


@dataclass
class AliasDef:
    name: str
    span: SrcSpan
    generics: list[int] = field(default_factory=list[int])
    aliased_type: int = -1


@dataclass
class AliasType:
    type_id: int
    custom_def: AliasDef
    generic_args: list[int] = field(default_factory=list[int])


BasicType: TypeAlias = (
    VoidType | NeverType | BoolType | CharType | StrType
    | IntType | FloatType
    | IntLiteralType | FloatLiteralType
)

DerivedType: TypeAlias = (
    ArrayType | TupleType | PointerType | RefType | SliceType | FunctionPointerType
)


@dataclass
class CapturedVar:
    name: str
    type_id: int


@dataclass
class ClosureType:
    type_id: int
    captured_vars: list[CapturedVar] = field(default_factory=list[CapturedVar])
    parameters: list[Parameter] = field(default_factory=list[Parameter])
    return_type: int = -1
    span: SrcSpan | None = None
    struct_type_id: int = -1        # anonymous struct, set during lowering
    call_method_type_id: int = -1   # call method, set during lowering


CustomType: TypeAlias = (
    StructType | EnumType | TraitType
    | MethodType | FunctionType | AliasType
)

Ty: TypeAlias = BasicType | DerivedType | CustomType | ClosureType | GenericType | ConstGenericType | LiteralValueType


class IntrinsicType(Enum):
    Never = "!"
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

    @classmethod
    def from_str(cls, s: str) -> IntrinsicType | None:
        match s:
            case "void":
                return cls.Void
            case "bool":
                return cls.Bool
            case "char":
                return cls.Char
            case "str":
                return cls.Str
            case "i8":
                return cls.I8
            case "i16":
                return cls.I16
            case "i32":
                return cls.I32
            case "i64":
                return cls.I64
            case "u8":
                return cls.U8
            case "u16":
                return cls.U16
            case "u32":
                return cls.U32
            case "u64":
                return cls.U64
            case "f16":
                return cls.F16
            case "f32":
                return cls.F32
            case "f64":
                return cls.F64
            case "int":
                return cls.Int
            case "uint":
                return cls.UInt
            case "float":
                return cls.Float
            case _:
                return None


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

    AddAssign = "AddAssign"
    SubAssign = "SubAssign"
    MulAssign = "MulAssign"
    DivAssign = "DivAssign"
    RemAssign = "RemAssign"
    BitAndAssign = "BitAndAssign"
    BitOrAssign = "BitOrAssign"
    BitXorAssign = "BitXorAssign"
    ShlAssign = "ShlAssign"
    ShrAssign = "ShrAssign"

    Index = "Index"
    Contains = "Contains"
    Deref = "Deref"
    Delete = "Delete"
    Drop = "Drop"

    Range = "Range"
    Option = "Option"
    Result = "Result"
