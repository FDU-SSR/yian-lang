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
        self.__i1: ir.IntType = ir.IntType(1)  # type: ignore
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

    def is_zst(self, type_id: int) -> bool:
        """Return whether a type is a Zero-Sized Type (carries no runtime info).

        Delegates to the type layer's authoritative predicate so codegen and
        analysis agree. Consistent with ``get_type_size(...) == 0`` for
        void/never/zero-length-arrays.
        """
        return self.__type_ctx.is_zst(type_id)

    # ------------------------------------------------------------------
    # type handlers
    # ------------------------------------------------------------------

    def __mangle_type(self, type_id: int) -> str:
        """Build a unique LLVM type name from the type definition's unit and name."""
        type_def = self.__type_ctx[type_id]
        assert isinstance(type_def, (Type.StructType, Type.EnumType))
        unit_name = self.__unit_names.get(type_def.unit_id, "unknown")
        return f"{unit_name}.{type_def.custom_def.name}.{type_id}"

    def __get_raw_type(self, type_id: int) -> ir.Type:
        if type_id in self.__storage:
            return self.__storage[type_id]

        ty_def = self.__type_ctx[type_id]
        match ty_def:
            case Type.VoidType():    result = self.__void
            case Type.NeverType():   result = self.__void
            case Type.BoolType():    result = self.__i1
            case Type.CharType():    result = self.__i32
            case Type.StrType():     result = self.__str_ll_type
            case Type.IntType():     result = self.__handle_int(ty_def)
            case Type.FloatType():   result = self.__handle_float(ty_def)
            case Type.PointerType(): result = self.__handle_pointer(ty_def)
            case Type.NullPtrType(): result = self.__ptr
            case Type.SliceType():   result = self.__handle_slice(ty_def)
            case Type.ArrayType():   result = self.__handle_array(ty_def)
            case Type.TupleType():   result = self.__handle_tuple(ty_def)
            case Type.StructType():  result = self.__handle_struct(type_id, ty_def)
            case Type.EnumType():    result = self.__handle_enum(type_id, ty_def)
            case Type.MethodType():  result = self.__handle_method(ty_def)
            case Type.FunctionType(): result = self.__handle_function(ty_def)
            case Type.FunctionPointerType(): result = self.__handle_function_pointer(ty_def)
            case _:
                raise ValueError(f"Type {type(ty_def).__name__} cannot be converted to LLVM IR type")

        self.__storage[type_id] = result
        return result

    def __handle_int(self, type_def: Type.IntType) -> ir.Type:
        return ir.IntType(type_def.size * 8)  # type: ignore

    def __handle_float(self, type_def: Type.FloatType) -> ir.Type:
        match type_def.size:
            case 2: return ir.HalfType()
            case 4: return ir.FloatType()
            case 8: return ir.DoubleType()
            case _: raise ValueError(f"Invalid float size: {type_def.size}")

    def __handle_pointer(self, type_def: Type.PointerType) -> ir.Type:
        return ir.PointerType(self.__get_raw_type(type_def.pointee_type))

    def __handle_slice(self, type_def: Type.SliceType) -> ir.Type:
        return ir.LiteralStructType([self.__get_raw_type(type_def.element_type).as_pointer(), self.__i64])

    def __handle_array(self, type_def: Type.ArrayType) -> ir.Type:
        length_ty = self.__type_ctx[type_def.length]
        assert isinstance(length_ty, Type.LiteralValueType)
        return ir.ArrayType(self.__get_raw_type(type_def.element_type), length_ty.value)

    def __handle_tuple(self, type_def: Type.TupleType) -> ir.Type:
        return ir.LiteralStructType([self.__get_raw_type(element_type) for element_type in type_def.element_types])

    def __handle_struct(self, type_id: int, _type_def: Type.StructType) -> ir.Type:
        identified = self.__module.context.get_identified_type(self.__mangle_type(type_id))  # type: ignore
        self.__storage[type_id] = identified
        identified.set_body(*[self.__get_raw_type(f.type_id) for f in self.__type_ctx.get_struct_fields(type_id)])  # type: ignore
        return identified  # type: ignore

    def __handle_enum(self, type_id: int, _type_def: Type.EnumType) -> ir.Type:
        identified = self.__module.context.get_identified_type(self.__mangle_type(type_id))  # type: ignore
        self.__storage[type_id] = identified
        max_size, max_align = 0, 1
        for variant in self.__type_ctx.get_enum_variants(type_id):
            if variant.payload_type is None:
                continue
            variant_size, variant_align = self.__stable_layout(variant.payload_type)
            max_size, max_align = max(max_size, variant_size), max(max_align, variant_align)
        pad = (max_size + max_align - 1) // max_align * max_align if max_size > 0 else 0
        identified.set_body(self.__i32, ir.ArrayType(self.__i8, pad))  # type: ignore
        return identified  # type: ignore

    def __build_function_type(self, ret_type_id: int, param_type_ids: list[int], receiver_type_id: int | None = None) -> ir.FunctionType:
        ret = self.__get_raw_type(ret_type_id)
        params = [self.__get_raw_type(param_type) for param_type in param_type_ids]
        if receiver_type_id is not None:
            params.insert(0, self.__get_raw_type(receiver_type_id).as_pointer())
        return ir.FunctionType(ret, params)

    def __handle_function(self, type_def: Type.FunctionType) -> ir.Type:
        return self.__build_function_type(
            type_def.return_type(self.__type_ctx),
            [param_type.type_id for param_type in type_def.parameters(self.__type_ctx)],
        )

    def __handle_method(self, type_def: Type.MethodType) -> ir.Type:
        receiver = None if type_def.is_static else type_def.receiver_type(self.__type_ctx)
        return self.__build_function_type(
            type_def.return_type(self.__type_ctx),
            [param_type.type_id for param_type in type_def.parameters(self.__type_ctx)],
            receiver,
        )

    def __handle_function_pointer(self, type_def: Type.FunctionPointerType) -> ir.Type:
        return self.__build_function_type(type_def.return_type, type_def.parameter_types).as_pointer()

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
        type_def = self.__type_ctx[type_id]

        if isinstance(type_def, (Type.VoidType, Type.NeverType)):
            result = (0, 1)
        elif isinstance(type_def, Type.BoolType):
            result = (1, 1)
        elif isinstance(type_def, Type.CharType):
            result = (4, 4)
        elif isinstance(type_def, Type.IntType):
            result = (type_def.size, type_def.size)
        elif isinstance(type_def, Type.FloatType):
            result = (type_def.size, type_def.size)
        elif isinstance(type_def, (Type.PointerType, Type.FunctionPointerType)):
            result = (self.__ptr.get_abi_size(self.__target_data), self.__ptr.get_abi_alignment(self.__target_data))  # type: ignore
        elif isinstance(type_def, Type.ArrayType):
            element_size, element_align = self.__stable_layout(type_def.element_type)
            length_ty = self.__type_ctx[type_def.length]
            assert isinstance(length_ty, Type.LiteralValueType)
            result = (element_size * length_ty.value, element_align)
        elif isinstance(type_def, Type.EnumType):
            max_size, max_align = 0, 1
            for variant in type_def.get_variants(self.__type_ctx):
                if variant.payload_type is None:
                    continue
                variant_size, variant_align = self.__stable_layout(variant.payload_type)
                max_size, max_align = max(max_size, variant_size), max(max_align, variant_align)
            payload_size = self.__align_up(max_size, max_align) if max_size > 0 else 0
            result = (self.__align_up(4 + payload_size, 4), 4)
        else:
            ll_type = self.__get_raw_type(type_id)
            result = (ll_type.get_abi_size(self.__target_data), ll_type.get_abi_alignment(self.__target_data))  # type: ignore

        self.__layout_cache[type_id] = result
        return result  # type: ignore
