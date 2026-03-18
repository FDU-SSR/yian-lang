from collections import defaultdict

from compiler.config import config
from compiler.config.constants import IntrinsicCustomType, IntrinsicTrait, IntrinsicType
from compiler.config.defs import StmtId, TypeId
from compiler.utils.errors import CompilerError

from .yian_types import (ArrayType, BoolType, CharType, EnumDef, EnumType, FloatType, FunctionDef, FunctionPointerType,
                         FunctionType, GenericType, InstantiatedType, IntType, MethodDef, MethodType, PointerType,
                         SliceType, StrType, StructDef, StructType, TraitDef, TraitType, TupleType, VoidType, YianType)


class TypeSpace:
    """
    A type space that stores the types in the Yian language.
    """
    # intrinsic basic type IDs
    void_id: TypeId = 10
    bool_id: TypeId = 11
    char_id: TypeId = 12
    str_id: TypeId = 13

    i8_id: TypeId = 14
    i16_id: TypeId = 15
    i32_id: TypeId = 16
    i64_id: TypeId = 17

    u8_id: TypeId = 18
    u16_id: TypeId = 19
    u32_id: TypeId = 20
    u64_id: TypeId = 21

    f16_id: TypeId = 22
    f32_id: TypeId = 23
    f64_id: TypeId = 24

    # intrinsic trait IDs
    add_id: TypeId = 50
    sub_id: TypeId = 51
    mul_id: TypeId = 52
    div_id: TypeId = 53
    rem_id: TypeId = 54
    neg_id: TypeId = 55
    bitand_id: TypeId = 56
    bitor_id: TypeId = 57
    bitxor_id: TypeId = 58
    bitnot_id: TypeId = 59
    shl_id: TypeId = 60
    shr_id: TypeId = 61

    partial_eq_id: TypeId = 70
    partial_ord_id: TypeId = 71

    index_id: TypeId = 80
    contains_id: TypeId = 81
    deref_id: TypeId = 82
    delete_id: TypeId = 83
    drop_id: TypeId = 84

    # intrinsic struct/enum IDs
    range_id: TypeId = 100
    Option_id: TypeId = 101
    Result_id: TypeId = 102

    single_ptr_id: TypeId = 200
    multi_ptr_id: TypeId = 201
    full_ptr_id: TypeId = 202

    def __init__(self):
        # The space that stores all the types.
        self.__space: dict[int, YianType] = {}

        self.__global_type_id = config.CUSTOM_MIN_TYPE_ID

        self.__add_global_types()
        self.__basic_type_convertibility_table = self.__build_basic_type_convertibility_table()

        # === caches to avoid duplicate allocations ===
        self.__generic_cache: dict[tuple[str, TypeId, StmtId], TypeId] = {}  # (name, ref_type, stmt_id) -> type_id
        self.__pointer_cache: dict[TypeId, TypeId] = {}  # pointee_type -> type_id
        self.__slice_cache: dict[TypeId, TypeId] = {}  # element_type -> type_id
        self.__array_cache: dict[tuple[TypeId, int], TypeId] = {}  # (element_type, size) -> type_id
        self.__tuple_cache: dict[tuple[TypeId, ...], TypeId] = {}  # (element_types) -> type_id
        self.__function_pointer_cache: dict[tuple[tuple[TypeId, ...], TypeId], TypeId] = {}  # (param_types, return_type) -> type_id
        # =============================================

        # === cache from template to instance ===
        self.__instance_cache: dict[tuple[int, tuple[TypeId, ...]], TypeId] = {}  # (def_id, generic_arg_type_ids) -> type_id
        # =======================================

        # === NPO (Null Pointer Optimization) cache ===
        self.__npo_cache: dict[TypeId, tuple[bool, list[int]]] = {}
        self.__npo_payload_inner_type: dict[TypeId, TypeId] = {}
        # ==============================================

    def __add_global_types(self):
        # add built-in types
        self.__space[self.void_id] = VoidType()
        self.__space[self.bool_id] = BoolType()
        self.__space[self.char_id] = CharType()
        self.__space[self.str_id] = StrType()
        self.__space[self.i8_id] = IntType(1, True)
        self.__space[self.i16_id] = IntType(2, True)
        self.__space[self.i32_id] = IntType(4, True)
        self.__space[self.i64_id] = IntType(8, True)
        self.__space[self.u8_id] = IntType(1, False)
        self.__space[self.u16_id] = IntType(2, False)
        self.__space[self.u32_id] = IntType(4, False)
        self.__space[self.u64_id] = IntType(8, False)
        self.__space[self.f16_id] = FloatType(2)
        self.__space[self.f32_id] = FloatType(4)
        self.__space[self.f64_id] = FloatType(8)

        # set the global type id
        for type_id, ty in self.__space.items():
            ty.type_id = type_id

    def __build_basic_type_convertibility_table(self) -> dict[TypeId, set[TypeId]]:
        table: dict[TypeId, set[TypeId]] = defaultdict(set)

        all_ints = {self.i8_id, self.i16_id, self.i32_id, self.i64_id, self.u8_id, self.u16_id, self.u32_id, self.u64_id}
        all_floats = {self.f16_id, self.f32_id, self.f64_id}
        all_numeric = all_ints | all_floats

        # all numeric types are convertible to each other
        for from_type in all_numeric:
            table[from_type] |= all_numeric

        # char type convertible to all integer types
        table[self.char_id] |= all_ints

        # u8/u16/u32 type convertible to char type
        table[self.u8_id].add(self.char_id)
        table[self.u16_id].add(self.char_id)
        table[self.u32_id].add(self.char_id)

        return table

    def __add_type(self, type: YianType) -> TypeId:
        """
        Add a type to the type space.

        Automatically allocates a type id for the type if the type id is not specified.
        Args:
            type (YianType): The type to be added.
        Returns:
            TypeId: The type id of the type.
        """
        if type.type_id == -1:
            type.type_id = self.__global_type_id
            self.__global_type_id += 1
        if type.type_id in self.__space:
            raise CompilerError(f"Type {type.type_id} already exists in type space")
        self.__space[type.type_id] = type
        return type.type_id

    def __getitem__(self, type_id: TypeId) -> YianType:
        if type_id in self.__space:
            return self.__space[type_id]
        else:
            raise CompilerError(f"Type {type_id} not found in type space")

    def __contains__(self, type_id: TypeId) -> bool:
        return type_id in self.__space

    @classmethod
    def intrinsic_type(cls, intrinsic: IntrinsicType) -> TypeId:
        """
        Get the type id of the intrinsic type.
        """
        INTRINSIC_TYPE_DICT: dict[IntrinsicType, TypeId] = {
            IntrinsicType.Void: cls.void_id,
            IntrinsicType.Bool: cls.bool_id,
            IntrinsicType.Char: cls.char_id,
            IntrinsicType.Str: cls.str_id,
            IntrinsicType.I8: cls.i8_id,
            IntrinsicType.I16: cls.i16_id,
            IntrinsicType.I32: cls.i32_id,
            IntrinsicType.I64: cls.i64_id,
            IntrinsicType.U8: cls.u8_id,
            IntrinsicType.U16: cls.u16_id,
            IntrinsicType.U32: cls.u32_id,
            IntrinsicType.U64: cls.u64_id,
            IntrinsicType.F16: cls.f16_id,
            IntrinsicType.F32: cls.f32_id,
            IntrinsicType.F64: cls.f64_id,
            IntrinsicType.Int: cls.i32_id,
            IntrinsicType.UInt: cls.u32_id,
            IntrinsicType.Float: cls.f64_id,
        }
        return INTRINSIC_TYPE_DICT[intrinsic]

    @classmethod
    def intrinsic_trait(cls, intrinsic: IntrinsicTrait) -> TypeId:
        INTRINSIC_TRAIT_DICT: dict[IntrinsicTrait, TypeId] = {
            IntrinsicTrait.Add: cls.add_id,
            IntrinsicTrait.Sub: cls.sub_id,
            IntrinsicTrait.Mul: cls.mul_id,
            IntrinsicTrait.Div: cls.div_id,
            IntrinsicTrait.Rem: cls.rem_id,
            IntrinsicTrait.Neg: cls.neg_id,
            IntrinsicTrait.BitAnd: cls.bitand_id,
            IntrinsicTrait.BitOr: cls.bitor_id,
            IntrinsicTrait.BitXor: cls.bitxor_id,
            IntrinsicTrait.BitNot: cls.bitnot_id,
            IntrinsicTrait.Shl: cls.shl_id,
            IntrinsicTrait.Shr: cls.shr_id,
            IntrinsicTrait.PartialEq: cls.partial_eq_id,
            IntrinsicTrait.PartialOrd: cls.partial_ord_id,
            IntrinsicTrait.Index: cls.index_id,
            IntrinsicTrait.Contains: cls.contains_id,
            IntrinsicTrait.Deref: cls.deref_id,
            IntrinsicTrait.Delete: cls.delete_id,
            IntrinsicTrait.Drop: cls.drop_id,
        }
        return INTRINSIC_TRAIT_DICT[intrinsic]

    @classmethod
    def intrinsic_custom_type(cls, intrinsic: IntrinsicCustomType) -> TypeId:
        INTRINSIC_CUSTOM_TYPE_DICT: dict[IntrinsicCustomType, TypeId] = {
            IntrinsicCustomType.Range: cls.range_id,
            IntrinsicCustomType.Option: cls.Option_id,
            IntrinsicCustomType.Result: cls.Result_id,
            IntrinsicCustomType.SinglePtr: cls.single_ptr_id,
            IntrinsicCustomType.MultiPtr: cls.multi_ptr_id,
            IntrinsicCustomType.FullPtr: cls.full_ptr_id,
        }
        return INTRINSIC_CUSTOM_TYPE_DICT[intrinsic]

    def alloc_generic(self, name: str, index: int, stmt_id: StmtId) -> TypeId:
        if (name, index, stmt_id) in self.__generic_cache:
            return self.__generic_cache[(name, index, stmt_id)]
        type_id = self.__add_type(GenericType(name, index, stmt_id))
        self.__generic_cache[(name, index, stmt_id)] = type_id
        return type_id

    def alloc_pointer(self, pointee_type: TypeId) -> TypeId:
        if pointee_type in self.__pointer_cache:
            return self.__pointer_cache[pointee_type]

        pointee_ty = self.__space[pointee_type]
        if isinstance(pointee_ty, FunctionType):
            type_id = self.alloc_function_pointer(pointee_ty.parameter_types(self.instantiate), pointee_ty.return_type(self.instantiate))
        else:
            type_id = self.__add_type(PointerType(pointee_type))

        self.__pointer_cache[pointee_type] = type_id
        return type_id

    def alloc_slice(self, element_type: TypeId) -> TypeId:
        if element_type in self.__slice_cache:
            return self.__slice_cache[element_type]

        type_id = self.__add_type(SliceType(element_type))

        self.__slice_cache[element_type] = type_id
        return type_id

    def alloc_array(self, element_type: TypeId, size: int) -> TypeId:
        if (element_type, size) in self.__array_cache:
            return self.__array_cache[(element_type, size)]
        type_id = self.__add_type(ArrayType(element_type, size))
        self.__array_cache[(element_type, size)] = type_id
        return type_id

    def alloc_tuple(self, element_types: list[TypeId]) -> TypeId:
        key = tuple(element_types)
        if key in self.__tuple_cache:
            return self.__tuple_cache[key]
        type_id = self.__add_type(TupleType(element_types))
        self.__tuple_cache[key] = type_id
        return type_id

    def alloc_struct(self, unit_id: int, stmt_id: StmtId, name: str, generics: list[str]) -> TypeId:
        struct_def = StructDef(unit_id, stmt_id, name)

        generic_types = [self.alloc_generic(generic_name, index, stmt_id) for index, generic_name in enumerate(generics)]
        struct_def.generics = generic_types

        ty = StructType(id(struct_def), struct_def.generics.copy(), struct_def)
        ty.generic_args = generic_types.copy()

        intrinsic_mapping: dict[str, TypeId] = {
            "Range": self.range_id,
            "RawPtr": self.single_ptr_id,
            "Slice": self.multi_ptr_id,
            "FullPtr": self.full_ptr_id,
        }
        if name in intrinsic_mapping:
            ty.type_id = intrinsic_mapping[name]

        type_id = self.__add_type(ty)

        return type_id

    def alloc_enum(self, unit_id: int, stmt_id: StmtId, name: str, generics: list[str]) -> TypeId:
        enum_def = EnumDef(unit_id, stmt_id, name)

        generic_types = [self.alloc_generic(generic_name, index, stmt_id) for index, generic_name in enumerate(generics)]
        enum_def.generics = generic_types

        ty = EnumType(id(enum_def), enum_def.generics.copy(), enum_def)
        ty.generic_args = generic_types.copy()

        intrinsic_mapping: dict[str, TypeId] = {
            "Option": self.Option_id,
            "Result": self.Result_id,
        }
        if name in intrinsic_mapping:
            ty.type_id = intrinsic_mapping[name]

        type_id = self.__add_type(ty)
        return type_id

    def alloc_trait(self, unit_id: int, stmt_id: StmtId, name: str, generics: list[str]) -> TypeId:
        trait_def = TraitDef(unit_id, stmt_id, name)

        generic_types = [self.alloc_generic(generic_name, index, stmt_id) for index, generic_name in enumerate(generics)]
        trait_def.generics = generic_types

        ty = TraitType(id(trait_def), trait_def.generics.copy(), trait_def)
        ty.generic_args = generic_types.copy()

        # map some intrinsic traits
        mapping: dict[str, TypeId] = {
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
        if name in mapping:
            ty.type_id = mapping[name]

        type_id = self.__add_type(ty)
        return type_id

    def alloc_method(self, unit_id: int, stmt_id: StmtId, name: str) -> TypeId:
        method_def = MethodDef(unit_id, stmt_id, name)
        ty = MethodType(id(method_def), method_def.generics.copy(), method_def)
        type_id = self.__add_type(ty)
        return type_id

    def alloc_function(self, unit_id: int, stmt_id: StmtId, name: str, generics: list[str]) -> TypeId:
        function_def = FunctionDef(unit_id, stmt_id, name)

        generic_types = [self.alloc_generic(generic_name, index, stmt_id) for index, generic_name in enumerate(generics)]
        function_def.generics = generic_types

        ty = FunctionType(id(function_def), function_def.generics.copy(), function_def)
        ty.generic_args = generic_types.copy()

        type_id = self.__add_type(ty)
        return type_id

    def alloc_function_pointer(self, param_types: list[TypeId], return_type: TypeId) -> TypeId:
        key = (tuple(param_types), return_type)
        if key in self.__function_pointer_cache:
            return self.__function_pointer_cache[key]
        type_id = self.__add_type(FunctionPointerType(param_types, return_type))
        self.__function_pointer_cache[key] = type_id
        return type_id

    def alloc_range(self, ty: TypeId) -> TypeId:
        return self.alloc_instantiated(TypeSpace.range_id, [ty])

    def alloc_single_ptr(self, pointee_type: TypeId):
        return self.alloc_instantiated(TypeSpace.single_ptr_id, [pointee_type])

    def alloc_multi_ptr(self, element_type: TypeId):
        return self.alloc_instantiated(TypeSpace.multi_ptr_id, [element_type])

    def alloc_full_ptr(self, pointee_type: TypeId):
        return self.alloc_instantiated(TypeSpace.full_ptr_id, [pointee_type])

    def alloc_instantiated(self, ty: TypeId, generic_args: list[TypeId]) -> TypeId:
        """
        Given an uninstantiated type Ty<T1, T2, ..., Tn>, and a list of generic argument type IDs [A1, A2, ..., An],
        allocate an instantiated type Ty<A1, A2, ..., An>.
        """
        ty_def = self.__space[ty].expect_instantiated()
        if len(ty_def.generic_args) != len(generic_args):
            raise CompilerError(f"Template type {ty_def} expects {len(ty_def.generic_args)} generic arguments, but got {len(generic_args)}")
        match ty_def:
            case StructType(def_id=def_id, generic_args=_, struct_def=struct_def):
                if len(struct_def.generics) == 0:
                    return ty
                key = (id(struct_def), tuple(generic_args))
                if key in self.__instance_cache:
                    return self.__instance_cache[key]
                instance_type = StructType(def_id=def_id, generic_args=generic_args, struct_def=struct_def)
            case EnumType(def_id=def_id, generic_args=_, enum_def=enum_def):
                if len(enum_def.generics) == 0:
                    return ty
                key = (id(enum_def), tuple(generic_args))
                if key in self.__instance_cache:
                    return self.__instance_cache[key]
                instance_type = EnumType(def_id=def_id, generic_args=generic_args, enum_def=enum_def)
            case TraitType(def_id=def_id, generic_args=_, trait_def=trait_def):
                if len(trait_def.generics) == 0:
                    return ty
                key = (id(trait_def), tuple(generic_args))
                if key in self.__instance_cache:
                    return self.__instance_cache[key]
                instance_type = TraitType(def_id=def_id, generic_args=generic_args, trait_def=trait_def)
            case MethodType(def_id=def_id, generic_args=_, method_def=method_def):
                if len(method_def.generics) == 0:
                    return ty
                key = (id(method_def), tuple(generic_args))
                if key in self.__instance_cache:
                    return self.__instance_cache[key]
                instance_type = MethodType(def_id=def_id, generic_args=generic_args, method_def=method_def)
            case FunctionType(def_id=def_id, generic_args=_, function_def=function_def):
                if len(function_def.generics) == 0:
                    return ty
                key = (id(function_def), tuple(generic_args))
                if key in self.__instance_cache:
                    return self.__instance_cache[key]
                instance_type = FunctionType(def_id=def_id, generic_args=generic_args, function_def=function_def)
            case _:
                raise CompilerError(f"Type {ty} is not a template type")
        type_id = self.__add_type(instance_type)
        self.__instance_cache[key] = type_id
        return type_id

    def instantiate(self, ty: TypeId, substs: dict[TypeId, TypeId]) -> TypeId:
        """
        Given a type `ty` that may contain generic type parameters, and a substitution map `substs`,
        return a new type where the generic type parameters are replaced by the corresponding types in `substs`.
        """
        if len(substs) == 0:
            return ty

        def resolve(t: TypeId) -> TypeId:
            cur = t
            visited: set[TypeId] = set()
            while cur in substs and substs[cur] != cur and cur not in visited:
                visited.add(cur)
                cur = substs[cur]
            return cur

        ty_def = self.__space[ty]
        if isinstance(ty_def, GenericType):
            return resolve(ty) if ty in substs else ty
        if isinstance(ty_def, InstantiatedType):
            instantiated_args = [self.instantiate(arg, substs) for arg in ty_def.generic_args]
            return self.alloc_instantiated(ty, instantiated_args)
        if isinstance(ty_def, ArrayType):
            element_type = self.instantiate(ty_def.element_type, substs)
            return self.alloc_array(element_type, ty_def.length)
        if isinstance(ty_def, TupleType):
            element_types = [self.instantiate(et, substs) for et in ty_def.element_types]
            return self.alloc_tuple(element_types)
        if isinstance(ty_def, PointerType):
            pointee_type = self.instantiate(ty_def.pointee_type, substs)
            return self.alloc_pointer(pointee_type)
        if isinstance(ty_def, SliceType):
            element_type = self.instantiate(ty_def.element_type, substs)
            return self.alloc_slice(element_type)
        if isinstance(ty_def, FunctionPointerType):
            param_types = [self.instantiate(pt, substs) for pt in ty_def.parameter_types]
            return_type = self.instantiate(ty_def.return_type, substs)
            return self.alloc_function_pointer(param_types, return_type)
        return ty

    def __compute_npo_info(self, type_id: TypeId) -> tuple[bool, list[int]]:
        """
        Compute and cache null pointer optimization info for an inner type (the T in Option<T>).

        Returns:
            (is_optimizable, field_path):
            - PointerType: (True, [])
            - StructType with NPO-able first field chain: (True, [field0_index, ...])
            - Otherwise: (False, [])
        """
        if type_id in self.__npo_cache:
            return self.__npo_cache[type_id]

        type_def = self.__space[type_id]

        if isinstance(type_def, PointerType):
            result = (True, [])
        elif isinstance(type_def, StructType):
            fields = sorted(type_def.struct_def.fields.values(), key=lambda f: f.index)
            if len(fields) == 0:
                result = (False, [])
            else:
                first_field = fields[0]
                substs = dict(zip(type_def.struct_def.generics, type_def.generic_args))
                first_field_type_id = self.instantiate(first_field.type_id, substs)

                inner_opt, inner_path = self.__compute_npo_info(first_field_type_id)
                if inner_opt:
                    result = (True, [first_field.index] + inner_path)
                else:
                    result = (False, [])
        else:
            result = (False, [])

        self.__npo_cache[type_id] = result
        return result

    def null_pointer_optimizable(self, type_id: TypeId) -> bool:
        """
        Check whether the type can be null pointer optimized.
        """
        if type_id not in self.__space:
            return False
        type_def = self.__space[type_id]
        if not isinstance(type_def, EnumType):
            return False

        if self.Option_id not in self.__space:
            return False
        option_def = self.__space[self.Option_id]
        if not isinstance(option_def, InstantiatedType):
            return False
        if type_def.def_id != option_def.def_id:
            return False
        if len(type_def.generic_args) != 1:
            return False
        opt_type_id = type_def.generic_args[0]
        return self.__compute_npo_info(opt_type_id)[0]

    def get_npo_field_path(self, type_id: TypeId) -> list[int]:
        """
        Get the field path to the nullable pointer field used for null pointer optimization.
        """
        optimizable, field_path = self.__compute_npo_info(type_id)
        if not optimizable:
            raise CompilerError(f"Type {type_id} is not null-pointer-optimizable")
        return field_path

    def resolve_npo_payload_type(self, type_id: TypeId) -> TypeId | None:
        return self.__npo_payload_inner_type.get(type_id)

    def basic_type_convertible(self, from_type: TypeId, to_type: TypeId) -> bool:
        """
        Check if a basic type can be converted to another basic type.
        """
        if from_type == to_type:
            return True

        return to_type in self.__basic_type_convertibility_table[from_type]

    def get_name(self, type_id: int) -> str:
        """
        Get the name of the type by its type id.
        """
        type_def = self.__space[type_id]
        match type_def:
            case VoidType():
                return "void"
            case BoolType():
                return "bool"
            case CharType():
                return "char"
            case StrType():
                return "str"
            case IntType(size=size, is_signed=is_signed):
                prefix = "i" if is_signed else "u"
                return f"{prefix}{size * 8}"
            case FloatType(size=size):
                return f"f{size * 8}"
            case GenericType(name=name):
                return name
            case TupleType(element_types=element_types):
                element_names = ", ".join(self.get_name(elem) for elem in element_types)
                return f"({element_names})"
            case ArrayType(element_type=element_type, length=length):
                return f"{self.get_name(element_type)}[{length}]"
            case PointerType(pointee_type=pointee_type):
                return f"{self.get_name(pointee_type)}*"
            case SliceType(element_type=element_type):
                return f"{self.get_name(element_type)}[]"
            case FunctionPointerType(parameter_types=parameter_types, return_type=return_type):
                params = ", ".join(self.get_name(param) for param in parameter_types)
                return f"fn<{self.get_name(return_type)}({params})>"
            case StructType():
                struct_name = type_def.name
                if len(type_def.generic_args) > 0:
                    generic_names = ", ".join(self.get_name(arg) for arg in type_def.generic_args)
                    struct_name += f"<{generic_names}>"
                return struct_name
            case EnumType():
                enum_name = type_def.name
                if len(type_def.generic_args) > 0:
                    generic_names = ", ".join(self.get_name(arg) for arg in type_def.generic_args)
                    enum_name += f"<{generic_names}>"
                return enum_name
            case TraitType():
                trait_name = type_def.name
                if len(type_def.generic_args) > 0:
                    generic_names = ", ".join(self.get_name(arg) for arg in type_def.generic_args)
                    trait_name += f"<{generic_names}>"
                return trait_name
            case MethodType():
                method_name = type_def.name
                if len(type_def.generic_args) > 0:
                    generic_names = ", ".join(self.get_name(arg) for arg in type_def.generic_args)
                    method_name += f"<{generic_names}>"
                return method_name
            case FunctionType():
                function_name = type_def.name
                if len(type_def.generic_args) > 0:
                    generic_names = ", ".join(self.get_name(arg) for arg in type_def.generic_args)
                    function_name += f"<{generic_names}>"
                return function_name
            case _:
                raise CompilerError(f"UnknownType: {type_def}")

    def finalize(self):
        """
        Final check and preparation of the type space before code generation.

        1. Check self-referential types.
        2. Compute NPO payload type mappings.
        """
        # compute NPO payload type mappings
        for type_id in list(self.__space):
            type_def = self.__space[type_id]
            if isinstance(type_def, EnumType):
                if self.null_pointer_optimizable(type_id):
                    opt_type_id = type_def.generic_args[0]
                    substs = dict(zip(type_def.enum_def.generics, type_def.generic_args))
                    for variant in type_def.enum_def.variants.values():
                        if variant.payload is not None:
                            instantiated_payload = self.instantiate(variant.payload, substs)
                            self.__npo_payload_inner_type[instantiated_payload] = opt_type_id

        # check self-referential types
        for type_id in list(self.__space):
            visited: set[TypeId] = set()

            def dfs(current_type_id: TypeId):
                if current_type_id in visited:
                    raise CompilerError(f"Self-referential type detected: {self.get_name(type_id)}")
                visited.add(current_type_id)
                ty = self.__space[current_type_id]
                match ty:
                    case ArrayType(element_type=element_type):
                        dfs(element_type)
                    case TupleType(element_types=element_types):
                        for et in element_types:
                            dfs(et)
                    case StructType():
                        substs = dict(zip(ty.struct_def.generics, ty.generic_args))
                        for field in ty.struct_def.fields.values():
                            field_ty = self.instantiate(field.type_id, substs)
                            dfs(field_ty)
                    case EnumType():
                        substs = dict(zip(ty.enum_def.generics, ty.generic_args))
                        for variant in ty.enum_def.variants.values():
                            if variant.payload is not None:
                                payload_ty = self.instantiate(variant.payload, substs)
                                dfs(payload_ty)
                    case _:
                        pass
                visited.remove(current_type_id)

            dfs(type_id)

    def is_instantiated(self, template_type: TypeId, generic_args: list[TypeId], target_type: TypeId) -> bool:
        """
        Check if the target_type is an instantiated type of the template_type with the given generic_args.
        """
        target_ty = self.__space[target_type].expect_instantiated()
        template_ty = self.__space[template_type].expect_instantiated()
        if target_ty.def_id != template_ty.def_id:
            return False
        if len(target_ty.generic_args) != len(generic_args):
            return False
        for targ_arg, gen_arg in zip(target_ty.generic_args, generic_args):
            if targ_arg != gen_arg:
                return False
        return True
