from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional

from compiler.config.constants import AccessMode, IntrinsicTrait, YianAttribute
from compiler.config.defs import StmtId, TypeId, TypeInstantiator
from compiler.utils.errors import SemanticError


class YianType(ABC):
    """
    Base class for all types in the Yian language.
    Attributes:
        type_id (TypeId): The unique identifier of the type.
    """

    def __init__(self):
        self.type_id = -1

    @property
    def is_arithmetic(self) -> bool:
        return isinstance(self, (IntType, FloatType))

    def expect_basic(self) -> "BasicType":
        if not isinstance(self, BasicType):
            raise TypeError(f"Expected basic type, got {type(self)}")
        return self

    def expect_void(self) -> "VoidType":
        if not isinstance(self, VoidType):
            raise TypeError(f"Expected void type, got {type(self)}")
        return self

    def expect_bool(self) -> "BoolType":
        if not isinstance(self, BoolType):
            raise TypeError(f"Expected bool type, got {type(self)}")
        return self

    def expect_char(self) -> "CharType":
        if not isinstance(self, CharType):
            raise TypeError(f"Expected char type, got {type(self)}")
        return self

    def expect_int(self) -> "IntType":
        if not isinstance(self, IntType):
            raise TypeError(f"Expected int type, got {type(self)}")
        return self

    def expect_float(self) -> "FloatType":
        if not isinstance(self, FloatType):
            raise TypeError(f"Expected float type, got {type(self)}")
        return self

    def expect_str(self) -> "StrType":
        if not isinstance(self, StrType):
            raise TypeError(f"Expected string type, got {type(self)}")
        return self

    def expect_array(self) -> "ArrayType":
        if not isinstance(self, ArrayType):
            raise TypeError(f"Expected array type, got {type(self)}")
        return self

    def expect_pointer(self) -> "PointerType":
        if not isinstance(self, PointerType):
            raise TypeError(f"Expected pointer type, got {type(self)}")
        return self

    def expect_slice(self) -> "SliceType":
        if not isinstance(self, SliceType):
            raise TypeError(f"Expected slice type, got {type(self)}")
        return self

    def expect_method(self) -> "MethodType":
        if not isinstance(self, MethodType):
            raise TypeError(f"Expected method type, got {type(self)}")
        return self

    def expect_function(self) -> "FunctionType":
        if not isinstance(self, FunctionType):
            raise TypeError(f"Expected function type, got {type(self)}")
        return self

    def expect_trait(self) -> "TraitType":
        if not isinstance(self, TraitType):
            raise TypeError(f"Expected trait type, got {type(self)}")
        return self

    def expect_generic(self) -> "GenericType":
        if not isinstance(self, GenericType):
            raise TypeError(f"Expected generic type, got {type(self)}")
        return self

    def expect_instantiated(self) -> "InstantiatedType":
        if not isinstance(self, InstantiatedType):
            raise TypeError(f"Expected instantiated type, got {type(self)}")
        return self

    def expect_struct(self) -> "StructType":
        if not isinstance(self, StructType):
            raise TypeError(f"Expected struct type, got {type(self)}")
        return self

    def expect_enum(self) -> "EnumType":
        if not isinstance(self, EnumType):
            raise TypeError(f"Expected enum type, got {type(self)}")
        return self

    def expect_function_pointer(self) -> "FunctionPointerType":
        if not isinstance(self, FunctionPointerType):
            raise TypeError(f"Expected function pointer type, got {type(self)}")
        return self

    def expect_tuple(self) -> "TupleType":
        if not isinstance(self, TupleType):
            raise TypeError(f"Expected tuple type, got {type(self)}")
        return self

    def expect_callable(self) -> "MethodType | FunctionType":
        if not isinstance(self, (MethodType, FunctionType)):
            raise TypeError(f"Expected callable type, got {type(self)}")
        return self


@dataclass
class BasicType(YianType):
    """
    Base class for all basic types in the Yian language.

    `BASIC` means that the type does not contain any other type.
    """

    def __post_init__(self):
        super().__init__()


@dataclass
class DerivedType(YianType):
    """
    Base class for all derived types in the Yian language.

    `DERIVED` means that the type is constructed from other types.
    """

    def __post_init__(self):
        super().__init__()


@dataclass
class VoidType(BasicType):
    """
    Represents the void type in the Yian language.
    """


@dataclass
class BoolType(BasicType):
    """
    Represents the bool type in the Yian language.
    """


@dataclass
class CharType(BasicType):
    """
    Represents the char type in the Yian language.

    Supports UTF-8 encoding, which means that a char takes 4 bytes.
    """


@dataclass
class StrType(BasicType):
    """
    Represents the type of string literals in the Yian language.

    The string literal will be treated as a fat pointer containing a pointer to the string data and the length of the string(in bytes).
    """


@dataclass
class IntType(BasicType):
    """
    Represents the int type in the Yian language.

    Attributes:
        size (int): The size of the int in `bytes`.
        is_signed (bool): Whether the int is signed or not.
    """
    size: int
    is_signed: bool

    @property
    def value_range(self) -> tuple[int, int]:
        if self.is_signed:
            min_value = -(1 << (self.size * 8 - 1))
            max_value = (1 << (self.size * 8 - 1)) - 1
        else:
            min_value = 0
            max_value = (1 << (self.size * 8)) - 1
        return min_value, max_value


@dataclass
class FloatType(BasicType):
    """
    Represents the float type in the Yian language.
    Attributes:
        size (int): The size of the float in `bytes`.
    """
    size: int


@dataclass
class GenericType(YianType):
    """
    Represents the generic type in the Yian language.
    Attributes:
        name (str): The name of the generic type.
        index (int): The index of the generic type in the generic parameter list.
        stmt_id (StmtId): The statement id where the generic type is defined.
    """
    name: str
    index: int
    stmt_id: StmtId

    def __post_init__(self):
        super().__init__()


@dataclass
class ArrayType(DerivedType):
    """
    Represents the stack array type in the Yian language.
    Attributes:
        element_type (TypeId): The type id of the element type.
        length (int): The length of the array.
    """
    element_type: TypeId
    length: int


@dataclass
class TupleType(DerivedType):
    """
    Represents the tuple type in the Yian language.
    Attributes:
        element_types (list[TypeId]): The type ids of the element types.
    """
    element_types: list[TypeId]


@dataclass
class PointerType(DerivedType):
    """
    Represents the pointer type in the Yian language.
    Attributes:
        pointee_type (TypeId): The type id of the pointee type.
    """
    pointee_type: TypeId

    @property
    def value_range(self) -> tuple[int, int]:
        min_value = 1
        max_value = (1 << 64) - 1
        return min_value, max_value


@dataclass
class SliceType(DerivedType):
    """
    Represents the slice type in the Yian language.
    Attributes:
        element_type (TypeId): The type id of the element type.
    """
    element_type: TypeId


@dataclass(eq=False)
class CustomDef(ABC):
    """
    Represents the user-defined type in the Yian language.

    User-defined type:
    - Has a stmt id
    - Has a user-defined name
    - Can have generic parameters
    - Can have attributes
    """
    unit_id: int
    stmt_id: int
    name: str
    generics: list[TypeId] = field(default_factory=list)
    attributes: set[YianAttribute] = field(default_factory=set)

    @property
    def is_heap(self):
        return YianAttribute.Dyn in self.attributes

    @property
    def is_public(self):
        return YianAttribute.Public in self.attributes

    @property
    def is_private(self):
        return YianAttribute.Public not in self.attributes

    @property
    def is_inline(self):
        return YianAttribute.Inline in self.attributes

    @property
    def is_static(self):
        return YianAttribute.Static in self.attributes

    @property
    def is_template(self):
        return len(self.generics) > 0

    def add_attribute(self, attr: YianAttribute):
        if attr in self.attributes:
            raise ValueError(f"Attribute {attr} already exists in {self}")
        self.attributes.add(attr)


@dataclass
class StructField:
    name: str
    type_id: TypeId
    access_mode: AccessMode
    index: int


@dataclass(eq=False)
class StructDef(CustomDef):
    fields: dict[str, StructField] = field(default_factory=dict)

    @property
    def contains_private_field(self):
        return any(fld.access_mode == AccessMode.Private for fld in self.fields.values())

    def add_field(self, name: str, type_id: TypeId, access_mode: AccessMode):
        if name in self.fields:
            raise ValueError(f"Field {name} already exists in {self}")
        fld = StructField(name, type_id, access_mode, index=len(self.fields))
        self.fields[name] = fld

    def get_field_by_name(self, name: str) -> StructField | None:
        return self.fields.get(name)

    def has_field(self, name: str) -> bool:
        return name in self.fields


@dataclass
class EnumVariant:
    name: str
    payload: Optional[TypeId]
    discriminant: int


@dataclass(eq=False)
class EnumDef(CustomDef):
    variants: dict[str, EnumVariant] = field(default_factory=dict)

    def add_variant(self, name: str, payload: TypeId | None):
        if name in self.variants:
            raise ValueError(f"Variant {name} already exists in {self}")
        variant = EnumVariant(name, payload, discriminant=len(self.variants))
        self.variants[name] = variant

    def get_variant_by_name(self, name: str) -> EnumVariant | None:
        return self.variants.get(name)

    def has_variant(self, name: str) -> bool:
        return name in self.variants


@dataclass(eq=False)
class InstantiatedType(YianType):
    """
    Base class for types that are instantiated from a definition.
    Attributes:
        def_id (int): The ID of the template.
        generic_args (list[TypeId]): The type ids of the generic arguments.
    """
    def_id: int
    generic_args: list[TypeId]

    def __post_init__(self):
        super().__init__()

    @property
    @abstractmethod
    def name(self) -> str:
        pass


@dataclass(eq=False)
class StructType(InstantiatedType):
    """
    Represents the struct type in the Yian language.
    """
    struct_def: StructDef

    @property
    def name(self) -> str:
        return self.struct_def.name

    def get_field_by_name(self, name: str, instantiator: TypeInstantiator) -> StructField | None:
        field = self.struct_def.get_field_by_name(name)
        if field is None:
            return None
        substs = dict(zip(self.struct_def.generics, self.generic_args))
        instantiated_type_id = instantiator(field.type_id, substs)
        return StructField(field.name, instantiated_type_id, field.access_mode, field.index)

    def has_field(self, name: str) -> bool:
        return self.struct_def.has_field(name)


@dataclass(eq=False)
class EnumType(InstantiatedType):
    """
    Represents the enum type in the Yian language.
    """
    enum_def: EnumDef

    @property
    def name(self) -> str:
        return self.enum_def.name

    def get_variant_by_name(self, name: str, instantiator: TypeInstantiator) -> EnumVariant | None:
        variant = self.enum_def.get_variant_by_name(name)
        if variant is None:
            return None
        instantiated_payload = None
        if variant.payload is not None:
            substs = dict(zip(self.enum_def.generics, self.generic_args))
            instantiated_payload = instantiator(variant.payload, substs)
        return EnumVariant(variant.name, instantiated_payload, variant.discriminant)

    def has_variant(self, name: str) -> bool:
        return self.enum_def.has_variant(name)

    @property
    def variant_count(self) -> int:
        return len(self.enum_def.variants)


@dataclass(eq=False)
class Parameter:
    stmt_id: int
    name: str
    type_id: TypeId


@dataclass(eq=False)
class MethodDef(CustomDef):
    """
    Represents the method type in the Yian language.
    """
    receiver_type: TypeId = -1
    parameters: list[Parameter] = field(default_factory=list)
    return_type: TypeId = -1
    is_header: bool = True


@dataclass(eq=False)
class MethodType(InstantiatedType):
    """
    Represents the method type in the Yian language.
    """
    method_def: MethodDef

    @property
    def name(self) -> str:
        return self.method_def.name

    @property
    def is_static(self) -> bool:
        return YianAttribute.Static in self.method_def.attributes

    def receiver_type(self, instantiator: TypeInstantiator) -> TypeId:
        substs = dict(zip(self.method_def.generics, self.generic_args))
        return instantiator(self.method_def.receiver_type, substs)

    def return_type(self, instantiator: TypeInstantiator) -> TypeId:
        substs = dict(zip(self.method_def.generics, self.generic_args))
        return instantiator(self.method_def.return_type, substs)

    def parameter_types(self, instantiator: TypeInstantiator) -> list[TypeId]:
        substs = dict(zip(self.method_def.generics, self.generic_args))
        return [
            instantiator(param.type_id, substs)
            for param in self.method_def.parameters
        ]

    def parameters(self, instantiator: TypeInstantiator) -> list[Parameter]:
        instantiated_params = []
        substs = dict(zip(self.method_def.generics, self.generic_args))
        for param in self.method_def.parameters:
            instantiated_type_id = instantiator(param.type_id, substs)
            instantiated_param = Parameter(param.stmt_id, param.name, instantiated_type_id)
            instantiated_params.append(instantiated_param)
        return instantiated_params


@dataclass(eq=False)
class FunctionDef(CustomDef):
    """
    Represents the function type in the Yian language.
    """
    parameters: list[Parameter] = field(default_factory=list)
    return_type: TypeId = -1


@dataclass(eq=False)
class FunctionType(InstantiatedType):
    """
    Represents the function type in the Yian language.
    """
    function_def: FunctionDef

    @property
    def name(self) -> str:
        return self.function_def.name

    def return_type(self, instantiator: TypeInstantiator) -> TypeId:
        substs = dict(zip(self.function_def.generics, self.generic_args))
        return instantiator(self.function_def.return_type, substs)

    def parameter_types(self, instantiator: TypeInstantiator) -> list[TypeId]:
        substs = dict(zip(self.function_def.generics, self.generic_args))
        return [
            instantiator(param.type_id, substs)
            for param in self.function_def.parameters
        ]

    def parameters(self, instantiator: TypeInstantiator) -> list[Parameter]:
        instantiated_params = []
        substs = dict(zip(self.function_def.generics, self.generic_args))
        for param in self.function_def.parameters:
            instantiated_type_id = instantiator(param.type_id, substs)
            instantiated_param = Parameter(param.stmt_id, param.name, instantiated_type_id)
            instantiated_params.append(instantiated_param)
        return instantiated_params


@dataclass(eq=False)
class TraitDef(CustomDef):
    """
    Represents the trait type in the Yian language.
    """
    method_ids: dict[str, TypeId] = field(default_factory=dict)

    @property
    def is_builtin(self):
        return self.name in IntrinsicTrait.__members__

    def add_method(self, name: str, method_id: TypeId):
        if name in self.method_ids:
            raise ValueError(f"Method {name} already exists in {self}")
        self.method_ids[name] = method_id


@dataclass(eq=False)
class TraitType(InstantiatedType):
    """
    Represents the trait type in the Yian language.
    """
    trait_def: TraitDef

    @property
    def name(self) -> str:
        return self.trait_def.name

    @property
    def method_names(self) -> set[str]:
        return set(self.trait_def.method_ids.keys())

    def get_method_by_name(self, name: str, instantiator: TypeInstantiator) -> TypeId:
        if name not in self.trait_def.method_ids:
            raise SemanticError(f"Method {name} does not exist in {self}")
        method_id = self.trait_def.method_ids[name]
        substs = dict(zip(self.trait_def.generics, self.generic_args))
        return instantiator(method_id, substs)


@dataclass
class FunctionPointerType(DerivedType):
    """
    Represents the function pointer type in the Yian language.
    """
    parameter_types: list[TypeId]
    return_type: TypeId
