"""
Yian type → LLVM IR type mapping.
"""

from __future__ import annotations

from llvmlite import ir  # type: ignore[import-untyped]
from llvmlite.binding import create_target_data  # type: ignore[import-untyped]

from compiler.analysis.ty import ty as Type
from compiler.analysis.ty.context import TypeCtx
from compiler.analysis.unit.unit_data import UnitData


def _mangle_type(unit_name: str, type_id: int, type_ctx: TypeCtx) -> str:
    name = type_ctx.get_name(type_id)
    return f"{unit_name}.{name}.{type_id}"


class LlvmTypeMapper:
    """Maps Yian ``TypeCtx`` type IDs to ``ir.Type`` objects."""

    def __init__(
        self,
        type_ctx: TypeCtx,
        raw_module: ir.Module,
        unit_datas: dict[int, UnitData],
    ) -> None:
        self.__type_ctx = type_ctx
        self.__mod = raw_module
        self.__unit_id_to_name = {uid: ud.path.stem for uid, ud in unit_datas.items()}

        self.__storage: dict[int, ir.Type] = {}
        self.__void = ir.VoidType()
        self.__i8 = ir.IntType(8)
        self.__i32 = ir.IntType(32)
        self.__i64 = ir.IntType(64)
        self.__str_ll_type = ir.LiteralStructType([self.__i8.as_pointer(), self.__i64])
        self.__ptr = ir.PointerType(self.__i8)
        self.__target_data = create_target_data(self.__mod.data_layout)  # type: ignore[no-untyped-call]
        self.__layout_cache: dict[int, tuple[int, int]] = {}

    # ------------------------------------------------------------------
    # public
    # ------------------------------------------------------------------

    def get_ll_type(self, type_id: int) -> ir.Type:
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
            case Type.TupleType():   res = self.__handle_tuple(type_id, ty_def)
            case Type.StructType():  res = self.__handle_struct(type_id, ty_def)
            case Type.EnumType():    res = self.__handle_enum(type_id, ty_def)
            case Type.MethodType():  res = self.__handle_method(ty_def)
            case Type.FunctionType(): res = self.__handle_function(ty_def)
            case Type.FunctionPointerType(): res = self.__handle_function_pointer(ty_def)
            case _:
                raise ValueError(f"Type {type(ty_def).__name__} cannot be converted to LLVM IR type")

        self.__storage[type_id] = res
        return res

    def get_type_size(self, type_id: int) -> int:
        size, _ = self.__stable_layout(type_id)
        return size

    # ------------------------------------------------------------------
    # type handlers
    # ------------------------------------------------------------------

    def __handle_int(self, td: Type.IntType) -> ir.Type:
        return ir.IntType(td.size * 8)

    def __handle_float(self, td: Type.FloatType) -> ir.Type:
        match td.size:
            case 2: return ir.HalfType()
            case 4: return ir.FloatType()
            case 8: return ir.DoubleType()
            case _: raise ValueError(f"Invalid float size: {td.size}")

    def __handle_pointer(self, td: Type.PointerType) -> ir.Type:
        return ir.PointerType(self.get_ll_type(td.pointee_type))

    def __handle_slice(self, td: Type.SliceType) -> ir.Type:
        return ir.LiteralStructType([self.get_ll_type(td.element_type).as_pointer(), self.__i64])

    def __handle_array(self, td: Type.ArrayType) -> ir.Type:
        length = self.__type_ctx.try_extract_array_length(td.type_id)
        assert length is not None
        return ir.ArrayType(self.get_ll_type(td.element_type), length)

    def __handle_tuple(self, type_id: int, td: Type.TupleType) -> ir.Type:
        identified = self.__mod.context.get_identified_type(_mangle_type("tuple", type_id, self.__type_ctx))
        self.__storage[type_id] = identified
        identified.set_body(*[self.get_ll_type(et) for et in td.element_types])
        return identified

    def __handle_struct(self, type_id: int, td: Type.StructType) -> ir.Type:
        unit_name = self.__unit_id_to_name.get(td.custom_def.unit_id, "unknown")
        identified = self.__mod.context.get_identified_type(_mangle_type(unit_name, type_id, self.__type_ctx))
        self.__storage[type_id] = identified
        substs = dict(zip(td.custom_def.generics, td.generic_args))
        fields = sorted(td.custom_def.fields, key=lambda f: f.index)
        identified.set_body(*[self.get_ll_type(self.__type_ctx.instantiate(f.type_id, substs)) for f in fields])
        return identified

    def __handle_enum(self, type_id: int, td: Type.EnumType) -> ir.Type:
        unit_name = self.__unit_id_to_name.get(getattr(td.custom_def, "unit_id", -1), "unknown")
        identified = self.__mod.context.get_identified_type(_mangle_type(unit_name, type_id, self.__type_ctx))
        self.__storage[type_id] = identified
        substs = dict(zip(td.custom_def.generics, td.generic_args))
        max_size, max_align = 0, 1
        for v in td.custom_def.variants:
            if v.payload_type is None: continue
            s, a = self.__stable_layout(self.__type_ctx.instantiate(v.payload_type, substs))
            max_size, max_align = max(max_size, s), max(max_align, a)
        pad = (max_size + max_align - 1) // max_align * max_align if max_size > 0 else 0
        identified.set_body(self.__i32, ir.ArrayType(self.__i8, pad))
        return identified

    def __handle_function(self, td: Type.FunctionType) -> ir.Type:
        ret = self.get_ll_type(td.return_type(self.__type_ctx))
        params = [self.get_ll_type(pt.type_id) for pt in td.parameters(self.__type_ctx)]
        return ir.FunctionType(ret, params)

    def __handle_method(self, td: Type.MethodType) -> ir.Type:
        ret = self.get_ll_type(td.return_type(self.__type_ctx))
        params: list[ir.Type] = []
        if not td.custom_def.is_static:
            params.append(self.get_ll_type(td.receiver_type(self.__type_ctx)).as_pointer())  # type: ignore[union-attr]
        params += [self.get_ll_type(pt.type_id) for pt in td.parameters(self.__type_ctx)]
        return ir.FunctionType(ret, params)

    def __handle_function_pointer(self, td: Type.FunctionPointerType) -> ir.Type:
        ret = self.get_ll_type(td.return_type)
        params = [self.get_ll_type(pt) for pt in td.parameter_types]
        return ir.FunctionType(ret, params).as_pointer()

    # ------------------------------------------------------------------
    # stable layout
    # ------------------------------------------------------------------

    @staticmethod
    def __align_up(value: int, align: int) -> int:
        return (value + align - 1) // align * align if align > 1 else value

    def __stable_layout(self, type_id: int, visiting: set[int] | None = None) -> tuple[int, int]:
        cached = self.__layout_cache.get(type_id)
        if cached is not None: return cached
        visiting = visiting or set()
        if type_id in visiting:
            raise ValueError(f"Recursive by-value layout: {self.__type_ctx.get_name(type_id)}")
        visiting.add(type_id)
        td = self.__type_ctx[type_id]

        if isinstance(td, Type.VoidType):        result = (0, 1)
        elif isinstance(td, Type.BoolType):       result = (1, 1)
        elif isinstance(td, Type.CharType):       result = (4, 4)
        elif isinstance(td, Type.StrType):
            result = (self.__str_ll_type.get_abi_size(self.__target_data), self.__str_ll_type.get_abi_alignment(self.__target_data))
        elif isinstance(td, Type.IntType):        result = (td.size, td.size)
        elif isinstance(td, Type.FloatType):      result = (td.size, td.size)
        elif isinstance(td, (Type.PointerType, Type.FunctionPointerType)):
            result = (self.__ptr.get_abi_size(self.__target_data), self.__ptr.get_abi_alignment(self.__target_data))
        elif isinstance(td, Type.SliceType):
            lt = self.get_ll_type(type_id)
            result = (lt.get_abi_size(self.__target_data), lt.get_abi_alignment(self.__target_data))
        elif isinstance(td, Type.ArrayType):
            es, ea = self.__stable_layout(td.element_type, visiting)
            result = (es * self.__type_ctx.try_extract_array_length(type_id), ea)  # type: ignore[operator]
        elif isinstance(td, Type.TupleType):
            off, ma = 0, 1
            for et in td.element_types:
                s, a = self.__stable_layout(et, visiting)
                off = self.__align_up(off, a) + s
                ma = max(ma, a)
            result = (self.__align_up(off, ma), ma)
        elif isinstance(td, Type.StructType):
            substs = dict(zip(td.custom_def.generics, td.generic_args))
            off, ma = 0, 1
            for f in sorted(td.custom_def.fields, key=lambda f: f.index):
                s, a = self.__stable_layout(self.__type_ctx.instantiate(f.type_id, substs), visiting)
                off = self.__align_up(off, a) + s
                ma = max(ma, a)
            result = (self.__align_up(off, ma), ma)
        elif isinstance(td, Type.EnumType):
            substs = dict(zip(td.custom_def.generics, td.generic_args))
            ms, ma = 0, 1
            for v in td.custom_def.variants:
                if v.payload_type is None: continue
                s, a = self.__stable_layout(self.__type_ctx.instantiate(v.payload_type, substs), visiting)
                ms, ma = max(ms, s), max(ma, a)
            ps = self.__align_up(ms, ma) if ms > 0 else 0
            result = (self.__align_up(4 + ps, 4), 4)
        else:
            lt = self.get_ll_type(type_id)
            result = (lt.get_abi_size(self.__target_data), lt.get_abi_alignment(self.__target_data))

        visiting.remove(type_id)
        self.__layout_cache[type_id] = result
        return result
