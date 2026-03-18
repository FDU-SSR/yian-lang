from typing import Callable

from compiler.config.constants import IntrinsicFunction
from compiler.config.defs import TypeId, UnitId
from compiler.unit_data import UnitData
from compiler.utils import mangle_type, ty
from compiler.utils.errors import CodegenError
from compiler.utils.ty import TypeSpace
from llvmlite import ir
from llvmlite.binding import create_target_data


class LowLevelTypeManager:
    def __init__(self, type_space: TypeSpace, module: ir.Module, unit_datas: dict[UnitId, UnitData]):
        self.__space = type_space
        self.__storage: dict[TypeId, ir.Type] = {}
        self.__module = module

        self.__unit_id_to_name = {unit_id: ud.unit_name for unit_id, ud in unit_datas.items()}

        self.__void = ir.VoidType()
        self.__i8 = ir.IntType(8)
        self.__i32 = ir.IntType(32)
        self.__i64 = ir.IntType(64)
        self.__str_type = ir.LiteralStructType([self.__i8.as_pointer(), self.__i64])
        self.__ptr = ir.PointerType(self.__i8)

        self.__intrinsic_functions: dict[IntrinsicFunction, ir.Function] = self.__create_intrinsic_functions()

    def __create_intrinsic_functions(self) -> dict[IntrinsicFunction, ir.Function]:
        intrinsics: dict[IntrinsicFunction, ir.Function] = {}
        intrinsics[IntrinsicFunction.Exit] = ir.Function(
            self.__module,
            ir.FunctionType(self.__void, [self.__i32]),
            name="exit",
        )
        intrinsics[IntrinsicFunction.Free] = ir.Function(
            self.__module,
            ir.FunctionType(self.__void, [self.__ptr]),
            name="free",
        )
        intrinsics[IntrinsicFunction.Malloc] = ir.Function(
            self.__module,
            ir.FunctionType(self.__ptr, [self.__i64]),
            name="malloc",
        )
        intrinsics[IntrinsicFunction.Write] = ir.Function(
            self.__module,
            ir.FunctionType(self.__i64, [self.__i32, self.__ptr, self.__i64]),
            name="write",
        )
        intrinsics[IntrinsicFunction.Read] = ir.Function(
            self.__module,
            ir.FunctionType(self.__i64, [self.__i32, self.__ptr, self.__i64]),
            name="read",
        )
        intrinsics[IntrinsicFunction.Open] = ir.Function(
            self.__module,
            ir.FunctionType(self.__i32, [self.__ptr, self.__i32, self.__i32]),
            name="open",
        )
        intrinsics[IntrinsicFunction.Close] = ir.Function(
            self.__module,
            ir.FunctionType(self.__i32, [self.__i32]),
            name="close",
        )
        intrinsics[IntrinsicFunction.StrCompare] = ir.Function(
            self.__module,
            ir.FunctionType(self.__i32, [self.__ptr, self.__ptr]),
            name="strcmp",
        )
        intrinsics[IntrinsicFunction.MemCopy] = ir.Function(
            self.__module,
            ir.FunctionType(self.__void, [self.__ptr, self.__ptr, self.__i64]),
            name="memcpy",
        )
        intrinsics[IntrinsicFunction.MemCompare] = ir.Function(
            self.__module,
            ir.FunctionType(self.__i32, [self.__ptr, self.__ptr, self.__i64]),
            name="memcmp",
        )
        return intrinsics

    def get_ll_type(self, type_id: TypeId) -> ir.Type:
        if type_id in self.__storage:
            return self.__storage[type_id]

        ty_def = self.__space[type_id]
        type_handlers: dict[type, Callable[[], ir.Type]] = {
            ty.VoidType: lambda: ir.VoidType(),
            ty.BoolType: lambda: ir.IntType(1),
            ty.CharType: lambda: self.__i32,
            ty.StrType: lambda: self.__str_type,
            ty.IntType: lambda: self.__handle_int(type_id),
            ty.FloatType: lambda: self.__handle_float(type_id),
            ty.PointerType: lambda: self.__handle_pointer(type_id),
            ty.SliceType: lambda: self.__handle_slice(type_id),
            ty.ArrayType: lambda: self.__handle_array(type_id),
            ty.TupleType: lambda: self.__handle_tuple(type_id),
            ty.StructType: lambda: self.__handle_struct(type_id),
            ty.EnumType: lambda: self.__handle_enum(type_id),
            ty.MethodType: lambda: self.__handle_method(type_id),
            ty.FunctionType: lambda: self.__handle_function(type_id),
            ty.FunctionPointerType: lambda: self.__handle_function_pointer(type_id),
        }

        type_kind = type(ty_def)
        if type_kind in type_handlers:
            res = type_handlers[type_kind]()
            self.__storage[type_id] = res
            return res

        raise CodegenError(f"{ty_def} cannot be converted to llir type")

    def get_type_size(self, type_id: TypeId) -> int:
        """
        Get the size of the type in bytes.
        """
        ll_type = self.get_ll_type(type_id)
        return ll_type.get_abi_size(create_target_data(self.__module.data_layout))

    def get_type_align(self, type_id: TypeId) -> int:
        """
        Get the alignment of the type in bytes.
        """
        ll_type = self.get_ll_type(type_id)
        return ll_type.get_abi_alignment(create_target_data(self.__module.data_layout))

    def intrinsic_func(self, intrinsic: IntrinsicFunction) -> ir.Function:
        """
        Get the intrinsic function for the given intrinsic.
        """
        return self.__intrinsic_functions[intrinsic]

    # === handlers ===
    def __handle_int(self, type_id: TypeId) -> ir.Type:
        type_def = self.__space[type_id].expect_int()
        return ir.IntType(type_def.size * 8)

    def __handle_float(self, type_id: TypeId) -> ir.Type:
        type_def = self.__space[type_id].expect_float()
        match type_def.size:
            case 2:
                return ir.HalfType()
            case 4:
                return ir.FloatType()
            case 8:
                return ir.DoubleType()
            case _:
                raise CodegenError(f"Invalid float size: {type_def.size}")

    def __handle_pointer(self, type_id: TypeId) -> ir.Type:
        type_def = self.__space[type_id].expect_pointer()
        pointee_type = self.get_ll_type(type_def.pointee_type)
        return ir.PointerType(pointee_type)

    def __handle_slice(self, type_id: TypeId) -> ir.Type:
        type_def = self.__space[type_id].expect_slice()
        element_type = self.get_ll_type(type_def.element_type)
        return ir.LiteralStructType([element_type.as_pointer(), self.__i64])

    def __handle_array(self, type_id: TypeId) -> ir.Type:
        type_def = self.__space[type_id].expect_array()
        element_type = self.get_ll_type(type_def.element_type)
        return ir.ArrayType(element_type, type_def.length)

    def __handle_tuple(self, type_id: TypeId) -> ir.Type:
        type_def = self.__space[type_id].expect_tuple()
        identified = self.__module.context.get_identified_type(self.__space.get_name(type_id))
        self.__storage[type_id] = identified
        element_ll_types = [self.get_ll_type(et) for et in type_def.element_types]
        identified.set_body(*element_ll_types)
        return identified

    def __handle_struct(self, type_id: TypeId) -> ir.Type:
        type_def = self.__space[type_id].expect_struct()
        unit_name = self.__unit_id_to_name[type_def.struct_def.unit_id]
        identified = self.__module.context.get_identified_type(mangle_type(unit_name, type_id, self.__space))
        self.__storage[type_id] = identified

        generic_args = type_def.generic_args
        substs = dict(zip(type_def.struct_def.generics, generic_args))
        fields = sorted(type_def.struct_def.fields.values(), key=lambda f: f.index)
        field_type_ids = [self.__space.instantiate(f.type_id, substs) for f in fields]
        field_ll_types = [self.get_ll_type(ft) for ft in field_type_ids]
        identified.set_body(*field_ll_types)
        return identified

    def get_npo_field_path(self, type_id: TypeId) -> list[int]:
        """
        Get the field path to the nullable field used for optimization.
        Delegates to TypeSpace's cached NPO info.
        """
        return self.__space.get_npo_field_path(type_id)

    def null_pointer_optimizable(self, type_id: TypeId) -> bool:
        """
        Check whether the type can be null pointer optimized.
        """
        return self.__space.null_pointer_optimizable(type_id)

    def __handle_enum(self, type_id: TypeId) -> ir.Type:
        if self.null_pointer_optimizable(type_id):
            type_def = self.__space[type_id].expect_enum()
            opt_type_id = type_def.generic_args[0]
            return self.get_ll_type(opt_type_id)
        type_def = self.__space[type_id].expect_enum()
        unit_name = self.__unit_id_to_name[type_def.enum_def.unit_id]
        identified = self.__module.context.get_identified_type(mangle_type(unit_name, type_id, self.__space))
        self.__storage[type_id] = identified

        generic_args = type_def.generic_args
        substs = dict(zip(type_def.enum_def.generics, generic_args))
        payload_type_ids: list[TypeId] = []
        for v in type_def.enum_def.variants.values():
            if v.payload is None:
                continue
            payload_type_ids.append(self.__space.instantiate(v.payload, substs))

        max_size = 0
        max_align = 1
        for payload_type_id in payload_type_ids:
            size = self.get_type_size(payload_type_id)
            align = self.get_type_align(payload_type_id)
            max_size = max(max_size, size)
            max_align = max(max_align, align)

        array_size = (max_size + max_align - 1) // max_align * max_align if max_size > 0 else 0
        payload_type = ir.ArrayType(self.__i8, array_size)
        tag_type = self.__i32
        identified.set_body(tag_type, payload_type)
        return identified

    def __is_sret(self, type_id: TypeId) -> bool:
        ret_def = self.__space[type_id]
        return isinstance(ret_def, (ty.StructType, ty.ArrayType, ty.EnumType, ty.TupleType))

    def __handle_function(self, type_id: TypeId) -> ir.Type:
        type_def = self.__space[type_id].expect_function()

        ret_id = type_def.return_type(self.__space.instantiate)
        param_types: list[ir.Type] = []

        if self.__is_sret(ret_id):
            ll_ret: ir.Type = ir.VoidType()
            param_types.append(self.get_ll_type(ret_id).as_pointer())
        else:
            ll_ret = self.get_ll_type(ret_id)

        for pt in type_def.parameter_types(self.__space.instantiate):
            param_types.append(self.get_ll_type(pt))

        return ir.FunctionType(ll_ret, param_types)

    def __handle_method(self, type_id: TypeId) -> ir.Type:
        type_def = self.__space[type_id].expect_method()

        ret_id = type_def.return_type(self.__space.instantiate)
        param_types: list[ir.Type] = []

        if self.__is_sret(ret_id):
            ll_ret: ir.Type = ir.VoidType()
            param_types.append(self.get_ll_type(ret_id).as_pointer())
        else:
            ll_ret = self.get_ll_type(ret_id)

        if not type_def.is_static:
            recv_id = type_def.receiver_type(self.__space.instantiate)
            param_types.append(self.get_ll_type(recv_id).as_pointer())

        for pt in type_def.parameter_types(self.__space.instantiate):
            param_types.append(self.get_ll_type(pt))

        return ir.FunctionType(ll_ret, param_types)

    def __handle_function_pointer(self, type_id: TypeId) -> ir.Type:
        type_def = self.__space[type_id].expect_function_pointer()
        ret_id = type_def.return_type

        param_types: list[ir.Type] = []
        if self.__is_sret(ret_id):
            ll_ret: ir.Type = ir.VoidType()
            param_types.append(self.get_ll_type(ret_id).as_pointer())
        else:
            ll_ret = self.get_ll_type(ret_id)

        for pt in type_def.parameter_types:
            param_types.append(self.get_ll_type(pt))

        return ir.FunctionType(ll_ret, param_types).as_pointer()
