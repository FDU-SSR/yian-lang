"""
Yian type → LLVM IR type mapping.
"""

from __future__ import annotations

from llvmlite import ir
from llvmlite.binding import create_target_data  # type: ignore

from compiler.analysis.ty import ty as Type
from compiler.analysis.ty.context import TypeCtx
from compiler.codegen.llvm.value import LLType


class LLTypeCtx:
    """Maps Yian ``TypeCtx`` type IDs to ``ir.Type`` objects."""

    def __init__(self, type_ctx: TypeCtx, module: ir.Module, unit_names: dict[int, str]) -> None:
        self.__type_ctx = type_ctx
        self.__module = module
        self.__unit_names = unit_names

        self.__storage: dict[int, ir.Type] = {}
        self.__void = ir.VoidType()
        self.__i8: ir.IntType = ir.IntType(8)  # type: ignore
        self.__i32: ir.IntType = ir.IntType(32)  # type: ignore
        self.__i64: ir.IntType = ir.IntType(64)  # type: ignore
        self.__ptr: ir.PointerType = ir.PointerType(self.__i8)  # type: ignore
        self.__str_ll_type: ir.LiteralStructType = ir.LiteralStructType([self.__ptr, self.__i64])  # type: ignore
        self.__target_data = create_target_data(self.__module.data_layout)
        self.__layout_cache: dict[int, tuple[int, int]] = {}  # type_id → (size, align)

    # ------------------------------------------------------------------
    # public
    # ------------------------------------------------------------------

    def get_ll_type(self, type_id: int) -> LLType:
        return LLType(type_id, self.__get_raw_type(type_id))

    def get_type_size(self, type_id: int) -> int:
        size, _ = self.__stable_layout(type_id)
        return size

    # ------------------------------------------------------------------
    # type handlers
    # ------------------------------------------------------------------

    def __mangle_type(self, type_id: int) -> str:
        """Build a unique LLVM type name from the type definition's unit and name."""
        td = self.__type_ctx[type_id]
        assert isinstance(td, (Type.StructType, Type.EnumType))
        unit_name = self.__unit_names.get(td.unit_id, "unknown")
        return f"{unit_name}.{td.custom_def.name}.{type_id}"

    def __get_raw_type(self, type_id: int) -> ir.Type:
        if type_id in self.__storage:
            return self.__storage[type_id]

        ty_def = self.__type_ctx[type_id]
        match ty_def:
            case Type.VoidType():    res = self.__void
            case Type.BoolType():    res = self.__i8
            case Type.CharType():    res = self.__i32
            case Type.StrType():     res = self.__str_ll_type
            case Type.IntType():     res = self.__handle_int(ty_def)
            case Type.FloatType():   res = self.__handle_float(ty_def)
            case Type.PointerType(): res = self.__handle_pointer(ty_def)
            case Type.SliceType():   res = self.__handle_slice(ty_def)
            case Type.ArrayType():   res = self.__handle_array(ty_def)
            case Type.TupleType():   res = self.__handle_tuple(ty_def)
            case Type.StructType():  res = self.__handle_struct(type_id, ty_def)
            case Type.EnumType():    res = self.__handle_enum(type_id, ty_def)
            case Type.MethodType():  res = self.__handle_method(ty_def)
            case Type.FunctionType(): res = self.__handle_function(ty_def)
            case Type.FunctionPointerType(): res = self.__handle_function_pointer(ty_def)
            case _:
                raise ValueError(f"Type {type(ty_def).__name__} cannot be converted to LLVM IR type")

        self.__storage[type_id] = res
        return res

    def __handle_int(self, td: Type.IntType) -> ir.Type:
        return ir.IntType(td.size * 8)  # type: ignore

    def __handle_float(self, td: Type.FloatType) -> ir.Type:
        match td.size:
            case 2: return ir.HalfType()
            case 4: return ir.FloatType()
            case 8: return ir.DoubleType()
            case _: raise ValueError(f"Invalid float size: {td.size}")

    def __handle_pointer(self, td: Type.PointerType) -> ir.Type:
        return ir.PointerType(self.__get_raw_type(td.pointee_type))

    def __handle_slice(self, td: Type.SliceType) -> ir.Type:
        return ir.LiteralStructType([self.__get_raw_type(td.element_type).as_pointer(), self.__i64])

    def __handle_array(self, td: Type.ArrayType) -> ir.Type:
        length_ty = self.__type_ctx[td.length]
        assert isinstance(length_ty, Type.LiteralValueType)
        return ir.ArrayType(self.__get_raw_type(td.element_type), length_ty.value)

    def __handle_tuple(self, td: Type.TupleType) -> ir.Type:
        return ir.LiteralStructType([self.__get_raw_type(et) for et in td.element_types])

    def __handle_struct(self, type_id: int, td: Type.StructType) -> ir.Type:
        identified = self.__module.context.get_identified_type(self.__mangle_type(type_id))  # type: ignore
        self.__storage[type_id] = identified
        identified.set_body(*[self.__get_raw_type(f.type_id) for f in td.get_fields(self.__type_ctx)])  # type: ignore
        return identified  # type: ignore

    def __handle_enum(self, type_id: int, td: Type.EnumType) -> ir.Type:
        identified = self.__module.context.get_identified_type(self.__mangle_type(type_id))  # type: ignore
        self.__storage[type_id] = identified
        max_size, max_align = 0, 1
        for v in td.get_variants(self.__type_ctx):
            if v.payload_type is None:
                continue
            s, a = self.__stable_layout(v.payload_type)
            max_size, max_align = max(max_size, s), max(max_align, a)
        pad = (max_size + max_align - 1) // max_align * max_align if max_size > 0 else 0
        identified.set_body(self.__i32, ir.ArrayType(self.__i8, pad))
        return identified

    def __build_function_type(
        self, ret_type_id: int, param_type_ids: list[int], receiver_type_id: int | None = None
    ) -> ir.FunctionType:
        ret = self.__get_raw_type(ret_type_id)
        params = [self.__get_raw_type(pt) for pt in param_type_ids]
        if receiver_type_id is not None:
            params.insert(0, self.__get_raw_type(receiver_type_id).as_pointer())
        return ir.FunctionType(ret, params)

    def __handle_function(self, td: Type.FunctionType) -> ir.Type:
        return self.__build_function_type(
            td.return_type(self.__type_ctx),
            [pt.type_id for pt in td.parameters(self.__type_ctx)],
        )

    def __handle_method(self, td: Type.MethodType) -> ir.Type:
        receiver = None if td.is_static else td.receiver_type(self.__type_ctx)
        return self.__build_function_type(
            td.return_type(self.__type_ctx),
            [pt.type_id for pt in td.parameters(self.__type_ctx)],
            receiver,
        )

    def __handle_function_pointer(self, td: Type.FunctionPointerType) -> ir.Type:
        return self.__build_function_type(td.return_type, td.parameter_types).as_pointer()

    # ------------------------------------------------------------------
    # stable layout
    # ------------------------------------------------------------------

    @staticmethod
    def __align_up(value: int, align: int) -> int:
        return (value + align - 1) // align * align if align > 1 else value

    def __stable_layout(self, type_id: int) -> tuple[int, int]:
        cached = self.__layout_cache.get(type_id)
        if cached is not None:
            return cached
        td = self.__type_ctx[type_id]

        if isinstance(td, Type.VoidType):
            result = (0, 1)
        elif isinstance(td, Type.BoolType):
            result = (1, 1)
        elif isinstance(td, Type.CharType):
            result = (4, 4)
        elif isinstance(td, Type.IntType):
            result = (td.size, td.size)
        elif isinstance(td, Type.FloatType):
            result = (td.size, td.size)
        elif isinstance(td, (Type.PointerType, Type.FunctionPointerType)):
            result = (self.__ptr.get_abi_size(self.__target_data), self.__ptr.get_abi_alignment(self.__target_data))
        elif isinstance(td, Type.ArrayType):
            es, ea = self.__stable_layout(td.element_type)
            length_ty = self.__type_ctx[td.length]
            assert isinstance(length_ty, Type.LiteralValueType)
            result = (es * length_ty.value, ea)
        elif isinstance(td, Type.EnumType):
            ms, ma = 0, 1
            for v in td.get_variants(self.__type_ctx):
                if v.payload_type is None:
                    continue
                s, a = self.__stable_layout(v.payload_type)
                ms, ma = max(ms, s), max(ma, a)
            ps = self.__align_up(ms, ma) if ms > 0 else 0
            result = (self.__align_up(4 + ps, 4), 4)
        else:
            lt = self.__get_raw_type(type_id)
            result = (lt.get_abi_size(self.__target_data), lt.get_abi_alignment(self.__target_data))

        self.__layout_cache[type_id] = result
        return result
