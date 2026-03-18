from compiler.backend.utils.def_info import DefInfo
from compiler.backend.utils.func_obj import FuncObjManager
from compiler.backend.utils.ll_block import LowLevelBlockManager
from compiler.backend.utils.ll_type import LowLevelTypeManager
from compiler.backend.utils.ll_value import LLFunction, LLValue
from compiler.backend.utils.operation import Operation
from compiler.backend.utils.setup import LowLevelSetup
from compiler.config.constants import NO_PATH
from compiler.config.defs import IRHandlerMap, StmtId, SymbolId, TypeId, UnitId
from compiler.unit_data import UnitData
from compiler.utils import is_user_defined_name, ty
from compiler.utils.errors import CompilerError, ErrorReporter
from compiler.utils.IR import (ArrayLiteral, TupleLiteral, BooleanLiteral, CharLiteral, DefPoint, FloatLiteral, IntegerLiteral,
                               Operator, StringLiteral, TypedValue, Variable)
from compiler.utils.IR import cgir as cir
from compiler.utils.ty import MethodRegistry, TypeSpace
from llvmlite import ir


class LLVMCtx:
    def __init__(
        self,
        type_space: TypeSpace,
        method_registry: MethodRegistry,
        unit_datas: dict[UnitId, UnitData],
    ):
        self.__space = type_space
        self.__method_registry = method_registry
        self.__unit_datas = unit_datas

        self.__setup = LowLevelSetup()

        self.__def_info: DefInfo | None = None
        self.__def_info_collection: dict[DefPoint, DefInfo] = {}
        self.__module = self.__setup.generate_module()

        self.__ll_type = LowLevelTypeManager(self.__space, self.__module, unit_datas)
        self.__func_obj = FuncObjManager(self.__module, self.__ll_type, self.__space, self.__method_registry, self.__unit_datas)
        self.__operation = Operation(self.__space, self.__ll_type)
        self.__ll_block = LowLevelBlockManager()

        self.__global_key = 0
        self.__string_literal_table: dict[bytes, ir.GlobalVariable] = {}

        self.__npo_payload_symbols: set[SymbolId] = set()

        self.__error_reporter = ErrorReporter(NO_PATH)

    def process_block(self, block_id: StmtId, handlers: IRHandlerMap[cir.CheckedGIR]):
        if self.__def_info is None:
            raise RuntimeError("DefCtx is not set in LLVMCtx")

        block = self.__def_info.def_point.get(block_id).expect_block()
        for stmt_id in block.statements:
            stmt = self.__def_info.def_point.get(stmt_id)
            try:
                if type(stmt) in handlers:
                    handlers[type(stmt)](stmt)
                else:
                    raise CompilerError(f"No handler for IR statement type: {type(stmt)}")
            except Exception as e:
                self.__error_reporter.report(stmt, e)

    def ty_get(self, type_id: TypeId) -> ty.YianType:
        return self.__space[type_id]

    def ty_size(self, type_id: TypeId) -> int:
        """
        Get the size of the type in bytes.
        """
        return self.__ll_type.get_type_size(type_id)

    def ty_instantiate(self, type_id: TypeId, substs: dict[TypeId, TypeId]) -> TypeId:
        return self.__space.instantiate(type_id, substs)

    def set_def_info(self, def_point: DefPoint) -> None:
        """
        Set the current def_info from the def_info_collection based on the given def_point.
        """
        if def_point not in self.__def_info_collection:
            raise CompilerError(f"DefInfo not found for DefPoint: {def_point}")
        self.__def_info = self.__def_info_collection[def_point]

    def ir_export(self, path: str) -> None:
        """
        Export the generated LLVM IR to a file.
        """
        with open(path, "w", encoding="utf-8") as f:
            f.write(str(self.__module))
        print(f"Exported LLVM IR to {path}")

    def ir_alloc_var(self, var: cir.Variable) -> None:
        if self.__def_info is None:
            raise CompilerError("DefCtx is not set in LLVMCtx")

        symbol_id: SymbolId = var.symbol_id
        if symbol_id in self.__def_info.symbol_table:
            return  # Already allocated (e.g. parameter or pre-allocated local)

        # Ensure symbol is defined in the DefPoint
        if var.symbol_id not in self.__def_info.def_point.symbol_table:
            raise CompilerError(f"Symbol {var.symbol_id} ({var.name}) not found in DefPoint {self.__def_info.def_point} symbol table")

        # Check for NPO payload type: allocate with inner type instead of SomePayload
        npo_inner_type = self.__space.resolve_npo_payload_type(var.type_id)
        if npo_inner_type is not None:
            ll_type = self.__ll_type.get_ll_type(npo_inner_type)
            self.__npo_payload_symbols.add(symbol_id)
        else:
            ll_type = self.__ll_type.get_ll_type(var.type_id)

        if var.lvalue and not is_user_defined_name(var.name):
            ll_type = ll_type.as_pointer()

        builder = self.__def_info.builder
        with builder.goto_block(self.__def_info.entry_block):
            var_obj = builder.alloca(ll_type, name=var.name)

        self.__def_info.symbol_table[symbol_id] = var_obj

    def ir_constant(self, type_id: TypeId, value: int | float | str) -> LLValue:
        """
        create an constant LLValue of the given type_id and value. Supported types:

        - int
        - float
        - char
        """
        ir_type = self.__ll_type.get_ll_type(type_id)

        # bool is a subclass of int in Python, so handle it first if needed
        if isinstance(value, bool):
            return LLValue(type_id, ir.Constant(ir_type, 1 if value else 0))

        # char
        if isinstance(value, str):
            if len(value) != 1:
                raise CompilerError(f"Invalid char literal: {value!r}")
            return LLValue(type_id, ir.Constant(ir_type, ord(value)))

        # int
        if isinstance(value, int):
            return LLValue(type_id, ir.Constant(ir_type, value))
        # float
        if isinstance(value, float):
            return LLValue(type_id, ir.Constant(ir_type, value))

        raise CompilerError(f"Unsupported constant value {value!r} for type_id={type_id} (ll_type={ir_type})")

    def ir_value(self, typed_value: TypedValue) -> LLValue:
        """
        load the value of typed_value
        """
        if self.__def_info is None:
            raise CompilerError("DefCtx is not set in LLVMCtx")

        builder = self.__def_info.builder
        type_id = typed_value.type_id

        match typed_value:
            case Variable():
                # lvalue variable
                if typed_value.lvalue and not is_user_defined_name(typed_value.name):
                    if typed_value.symbol_id not in self.__def_info.symbol_table:
                        raise CompilerError(f"Unknown lvalue variable '{typed_value.name}' (symbol_id={typed_value.symbol_id})")
                    addr_of_addr = self.__def_info.symbol_table[typed_value.symbol_id]
                    addr = builder.load(addr_of_addr, name=f"{typed_value.name}.addr")
                    value = builder.load(addr, name=f"{typed_value.name}.val")
                    return LLValue(type_id, value)

                # normal
                if typed_value.symbol_id in self.__def_info.symbol_table:
                    addr = self.__def_info.symbol_table[typed_value.symbol_id]
                    loaded_value = builder.load(addr, name=f"{typed_value.name}.val")
                    return LLValue(type_id, loaded_value)

                # self object
                if typed_value.name == "self" and self.__def_info.self_obj is not None:
                    loaded_value = builder.load(self.__def_info.self_obj, name="self.val")
                    return LLValue(type_id, loaded_value)

                raise CompilerError(f"Unknown variable '{typed_value.name}' (symbol_id={typed_value.symbol_id})")

            case IntegerLiteral() | FloatLiteral():
                ir_type = self.__ll_type.get_ll_type(type_id)
                loaded_value = ir.Constant(ir_type, typed_value.value)
                return LLValue(type_id, loaded_value)

            case BooleanLiteral():
                ir_type = self.__ll_type.get_ll_type(type_id)
                if typed_value.value:
                    loaded_value = ir.Constant(ir_type, 1)
                else:
                    loaded_value = ir.Constant(ir_type, 0)
                return LLValue(type_id, loaded_value)

            case CharLiteral():
                ir_type = self.__ll_type.get_ll_type(type_id)
                loaded_value = ir.Constant(ir_type, ord(typed_value.value))
                return LLValue(type_id, loaded_value)

            case StringLiteral():
                return LLValue(type_id, self.__str_slice(typed_value.value))

            case ArrayLiteral():
                ir_type = self.__ll_type.get_ll_type(type_id)
                element_values = [self.ir_value(elem) for elem in typed_value.elements]
                array_const = ir.Constant(ir_type, [elem.value for elem in element_values])
                return LLValue(type_id, array_const)

            case TupleLiteral():
                ir_type = self.__ll_type.get_ll_type(type_id)
                element_values = [self.ir_value(elem) for elem in typed_value.elements]
                tuple_const = ir.Constant(ir_type, [elem.value for elem in element_values])
                return LLValue(type_id, tuple_const)

            case _:
                raise CompilerError(f"Unsupported typed value: {typed_value}")

        raise CompilerError(f"Unsupported typed value: {typed_value}")

    def __str_slice(self, value: bytes) -> ir.Constant:
        """
        Get str slice (i8*, u64) from string literal

        Args:
            value (bytes): string value (utf-8 encoded)
        """
        str_ptr = self.__alloc_static_str(value)
        str_len = ir.Constant(ir.IntType(64), len(value))
        return ir.Constant.literal_struct([str_ptr, str_len])

    def __alloc_static_str(self, value: bytes) -> ir.Constant:
        """
        Allocate static string literal and return i8* pointer(const gep)

        Args:
            value (bytes): string value (utf-8 encoded)
        """
        if value in self.__string_literal_table:
            global_str = self.__string_literal_table[value]
        else:
            str_bytes = bytearray(value)
            str_type = ir.ArrayType(ir.IntType(8), len(str_bytes))
            global_str = ir.GlobalVariable(self.__module, str_type, name=f"str.{self.__global_key}")
            self.__global_key += 1
            global_str.linkage = "private"
            global_str.global_constant = True
            global_str.unnamed_addr = True
            global_str.initializer = ir.Constant(str_type, str_bytes)  # type: ignore[arg-type]
            self.__string_literal_table[value] = global_str

        return global_str.gep([ir.Constant(ir.IntType(32), 0), ir.Constant(ir.IntType(32), 0)])

    def ir_address(self, var: Variable) -> LLValue:
        """
        - if var is an lvalue, return value of the variable directly
        - otherwise, return the address of the variable
        """
        if self.__def_info is None:
            raise CompilerError("DefCtx is not set in LLVMCtx")
        builder = self.__def_info.builder

        if var.name == "self":
            if self.__def_info.self_obj is None:
                raise CompilerError("self object is not set in DefInfo")
            return LLValue(self.__space.alloc_pointer(var.type_id), self.__def_info.self_obj)

        var_type = self.ty_get(var.type_id)

        if isinstance(var_type, ty.FunctionType):
            func_obj = self.__func_obj.get_func_obj(var.type_id)
            return LLValue(var.type_id, func_obj)

        symbol_id = var.symbol_id
        if symbol_id not in self.__def_info.symbol_table:
            raise CompilerError(f"Variable '{var.name}' (symbol_id={symbol_id}) not allocated")

        var_obj = self.__def_info.symbol_table[symbol_id]

        if var.lvalue and not is_user_defined_name(var.name):
            addr = builder.load(var_obj)
            return LLValue(var.type_id, addr)

        return LLValue(var.type_id, var_obj)

    def ir_assign_cast(self, target_type: TypeId, value: LLValue) -> LLValue:
        """
        see `calc_assign_value` for details
        """
        if self.__def_info is None:
            raise CompilerError("DefCtx is not set in LLVMCtx")

        if target_type == value.type_id:
            return value

        builder = self.__def_info.builder

        src_type_id = value.type_id
        src_type = self.ty_get(src_type_id)
        dst_type = self.ty_get(target_type)

        # T[] ==> T*
        if isinstance(dst_type, ty.PointerType) and isinstance(src_type, ty.ArrayType):
            arr_ptr = value.value
            if not isinstance(arr_ptr.type, ir.PointerType):  # type: ignore
                raise CompilerError("T[] => T* requires an array lvalue address; rvalue arrays are not supported here.")

            elem_ptr = builder.gep(
                arr_ptr,
                [ir.Constant(ir.IntType(32), 0), ir.Constant(ir.IntType(32), 0)],
                inbounds=True,
                name="array.decay.ptr",
            )

            dst_ll_type = self.__ll_type.get_ll_type(target_type)
            if elem_ptr.type != dst_ll_type:
                elem_ptr = builder.bitcast(elem_ptr, dst_ll_type, name="array.decay.cast")
            return LLValue(target_type, elem_ptr)  # type: ignore

        raise CompilerError(f"Unsupported assign cast: {src_type} => {dst_type}")

    def ir_binary_op(self, operator: Operator, left: LLValue, right: LLValue) -> LLValue:
        """
        perform binary operation and return the result value
        """
        if self.__def_info is None:
            raise CompilerError("DefCtx is not set in LLVMCtx")
        return self.__operation.binary_op(operator, left, right, self.__def_info.builder)

    def ir_unary_op(self, operator: Operator, operand: LLValue) -> LLValue:
        """
        perform unary operation and return the result value
        """
        if self.__def_info is None:
            raise CompilerError("DefCtx is not set in LLVMCtx")
        return self.__operation.unary_op(operator, operand, self.__def_info.builder)

    def ir_delete(self, value: LLValue) -> None:
        """
        delete the heap-allocated object pointed by value
        """
        if self.__def_info is None:
            raise CompilerError("DefCtx is not set in LLVMCtx")
        self.__operation.delete(value, self.__def_info.builder)

    def ir_heap_alloc(self, size_in_bytes: LLValue, data_type: TypeId) -> LLValue:
        """
        allocate heap memory of size_in_bytes and return the pointer to the allocated memory
        """
        if self.__def_info is None:
            raise CompilerError("DefCtx is not set in LLVMCtx")
        return self.__operation.heap_alloc(size_in_bytes, data_type, self.__def_info.builder)

    def ir_cast(self, value: LLValue, to_type: TypeId) -> LLValue:
        """
        explicitly cast value to to_type and return the casted value
        """
        if self.__def_info is None:
            raise CompilerError("DefCtx is not set in LLVMCtx")
        return self.__operation.cast(value, self.__space[to_type], self.__def_info.builder)

    def ir_bitcast(self, value: LLValue, to_type: TypeId) -> LLValue:
        """
        bitcast value to to_type and return the casted value

        - only for pointer types
        - does not check the validity of the bitcast (e.g. whether the source and target types have the same size)
        """
        if self.__def_info is None:
            raise CompilerError("DefCtx is not set in LLVMCtx")
        return self.__operation.bitcast(value, self.__space[to_type], self.__def_info.builder)

    def ir_byte_offset(self, ptr_value: LLValue, offset_value: LLValue) -> LLValue:
        """
        offset the pointer value by the given byte offset and return the resulting pointer value

        - only for pointer types
        - does not check the validity of the resulting pointer (e.g. whether it points to a valid memory region)
        """
        if self.__def_info is None:
            raise CompilerError("DefCtx is not set in LLVMCtx")
        return self.__operation.byte_offset(ptr_value, offset_value, self.__def_info.builder)

    def ir_mem_copy(self, dest_addr: LLValue, src_addr: LLValue, size_value: LLValue) -> None:
        """
        copy memory from src_addr to dest_addr with the given size in bytes

        - only for pointer types
        - does not check the validity of the source and destination pointers (e.g. whether they point to valid memory regions, whether they overlap, etc.)
        """
        if self.__def_info is None:
            raise CompilerError("DefCtx is not set in LLVMCtx")
        self.__operation.mem_copy(dest_addr, src_addr, size_value, self.__def_info.builder)

    def ir_store(self, target: Variable, value: LLValue) -> None:
        """
        store value into target variable
        """
        if self.__def_info is None:
            raise CompilerError("DefCtx is not set in LLVMCtx")

        target_addr = self.ir_address(target)
        self.__def_info.builder.store(value.value, target_addr.value)
        # builder = self.__def_info.builder

        # if target.symbol_id in self.__def_info.symbol_table:
        #     dst_addr = self.__def_info.symbol_table[target.symbol_id]
        # elif target.name == "self" and self.__def_info.self_obj is not None:
        #     dst_addr = self.__def_info.self_obj
        # else:
        #     raise CompilerError(f"Unknown store target '{target.name}' (symbol_id={target.symbol_id})")

    def ir_store_addr(self, address: LLValue, value: LLValue) -> None:
        """
        store value into the address directly
        """
        if self.__def_info is None:
            raise CompilerError("DefCtx is not set in LLVMCtx")

        builder = self.__def_info.builder
        builder.store(value.value, address.value)

    def ir_load_addr(self, address: LLValue) -> LLValue:
        """
        load value from the address directly
        """
        if self.__def_info is None:
            raise CompilerError("DefCtx is not set in LLVMCtx")

        builder = self.__def_info.builder
        address_type = self.__space[address.type_id]

        if not isinstance(address_type, ty.PointerType):
            raise CompilerError(f"Address must be a pointer type, got {address_type}")

        pointee_type = address_type.pointee_type
        loaded_value = builder.load(address.value)
        return LLValue(pointee_type, loaded_value)

    def ir_lvalue_store(self, target: Variable, address: LLValue) -> None:
        """
        - if target is a compiler-generated variable, store the address into it
        - otherwise, store the value at the address into target variable
        """
        if self.__def_info is None:
            raise CompilerError("DefCtx is not set in LLVMCtx")

        if target.symbol_id not in self.__def_info.symbol_table:
            raise CompilerError(f"Target symbol {target.symbol_id} not allocated: {target}")

        builder = self.__def_info.builder
        target_obj = self.__def_info.symbol_table[target.symbol_id]
        addr_ptr = address.value

        if target.lvalue and not is_user_defined_name(target.name):
            addr_val = addr_ptr

            builder.store(addr_val, target_obj)
            return

        loaded_val = builder.load(addr_ptr)
        builder.store(loaded_val, target_obj)

    def ir_index_access(self, lhs: Variable, index: TypedValue) -> LLValue:
        """
        - for array/slice/pointer, get the address of the element at index
        - for tuple, get the address of the element at index, make sure index is a constant integer
        """
        if self.__def_info is None:
            raise CompilerError("DefCtx is not set in LLVMCtx")
        builder = self.__def_info.builder

        lhs_addr = self.ir_address(lhs)
        index_value = self.ir_value(index)

        lhs_type = self.ty_get(lhs.type_id)
        match lhs_type:
            case ty.ArrayType():
                array_ll_type = self.__ll_type.get_ll_type(lhs_type.type_id)
                array_ptr = builder.bitcast(lhs_addr.value, array_ll_type.as_pointer(), name="array.ptr")
                element_ptr = builder.gep(
                    array_ptr,
                    [ir.Constant(ir.IntType(32), 0), index_value.value],
                    inbounds=True,
                    name="element.ptr"
                )
                element_type_id = lhs_type.element_type

            case ty.PointerType():
                ptr_value = builder.load(lhs_addr.value)  # Load the pointer value from the variable
                element_ptr = builder.gep(
                    ptr_value,
                    [index_value.value],
                    inbounds=True,
                    name="element.ptr"
                )
                element_type_id = lhs_type.pointee_type

            case ty.SliceType():
                slice_ll_type = self.__ll_type.get_ll_type(lhs_type.type_id)
                slice_ptr = builder.bitcast(lhs_addr.value, slice_ll_type.as_pointer(), name="slice.ptr")
                data_ptr = builder.gep(
                    slice_ptr,
                    [ir.Constant(ir.IntType(32), 0), ir.Constant(ir.IntType(32), 0)],
                    inbounds=True,
                    name="slice.data.ptr"
                )
                data_ptr_value = builder.load(data_ptr, name="slice.data.load")
                element_ptr = builder.gep(
                    data_ptr_value,
                    [index_value.value],
                    inbounds=True,
                    name="element.ptr"
                )
                element_type_id = lhs_type.element_type

            case ty.TupleType():
                assert isinstance(index, IntegerLiteral)
                tuple_ll_type = self.__ll_type.get_ll_type(lhs_type.type_id)
                tuple_ptr = builder.bitcast(lhs_addr.value, tuple_ll_type.as_pointer(), name="tuple.ptr")
                element_ptr = builder.gep(
                    tuple_ptr,
                    [ir.Constant(ir.IntType(32), 0), index_value.value],
                    inbounds=True,
                    name="element.ptr"
                )
                element_type_id = lhs_type.element_types[index.value]

            case _:
                raise CompilerError(f"Index access not supported for type: {lhs_type}")

        return LLValue(element_type_id, element_ptr)

    def ir_range_index_access(self, lhs: Variable, index: TypedValue) -> LLValue:
        """
        - index must be Range<u64>
        - lhs can be array/slice/pointer
        """
        if self.__def_info is None:
            raise CompilerError("DefCtx is not set in LLVMCtx")
        builder = self.__def_info.builder

        lhs_addr = self.ir_address(lhs)
        lhs_type = self.ty_get(lhs.type_id)

        range_val = self.ir_value(index)

        start_val = builder.extract_value(range_val.value, 0, name="range.start")
        end_val = builder.extract_value(range_val.value, 1, name="range.end")

        length_val = builder.sub(end_val, start_val, name="slice.len")

        match lhs_type:
            case ty.ArrayType():
                array_ll_type = self.__ll_type.get_ll_type(lhs_type.type_id)
                array_ptr = builder.bitcast(lhs_addr.value, array_ll_type.as_pointer(), name="array.ptr")
                element_ptr = builder.gep(
                    array_ptr,
                    [ir.Constant(ir.IntType(32), 0), start_val],
                    inbounds=True,
                    name="range.data.ptr"
                )
                element_type_id = lhs_type.element_type

            case ty.SliceType():
                slice_ll_type = self.__ll_type.get_ll_type(lhs_type.type_id)
                slice_ptr = builder.bitcast(lhs_addr.value, slice_ll_type.as_pointer(), name="slice.ptr")

                data_ptr_ptr = builder.gep(
                    slice_ptr,
                    [ir.Constant(ir.IntType(32), 0), ir.Constant(ir.IntType(32), 0)],
                    inbounds=True,
                    name="slice.data.ptr.ptr"
                )
                data_ptr = builder.load(data_ptr_ptr, name="slice.data.load")

                element_ptr = builder.gep(
                    data_ptr,
                    [start_val],
                    inbounds=True,
                    name="range.data.ptr"
                )
                element_type_id = lhs_type.element_type

            case ty.PointerType():
                ptr_value = builder.load(lhs_addr.value, name="ptr.load")
                element_ptr = builder.gep(
                    ptr_value,
                    [start_val],
                    inbounds=True,
                    name="range.data.ptr"
                )
                element_type_id = lhs_type.pointee_type

            case _:
                raise CompilerError(f"Range access not supported for type: {lhs_type}")

        slice_type_id = self.__space.alloc_slice(element_type_id)
        slice_ll_type = self.__ll_type.get_ll_type(slice_type_id)

        slice_res = ir.Constant(slice_ll_type, ir.Undefined)
        slice_res = builder.insert_value(slice_res, element_ptr, 0, name="slice.res.ptr")
        slice_res = builder.insert_value(slice_res, length_val, 1, name="slice.res.len")

        return LLValue(slice_type_id, slice_res)

    def ir_field_access(self, receiver: Variable, field: ty.StructField) -> LLValue:
        """
        get the address of the field at field_index from receiver object
        """
        if self.__def_info is None:
            raise CompilerError("DefCtx is not set in LLVMCtx")
        receiver_addr = self.ir_address(receiver)

        # For NPO payload variables, the receiver address is already the address of the val field.
        if receiver.symbol_id in self.__npo_payload_symbols:
            return LLValue(field.type_id, receiver_addr.value)

        field_addr = self.__def_info.builder.gep(
            receiver_addr.value,
            [ir.Constant(ir.IntType(32), 0), ir.Constant(ir.IntType(32), field.index)],
            inbounds=True,
            name=f"field{field.index}.addr"
        )

        return LLValue(field.type_id, field_addr)

    def ir_slice_length(self, slice_value: LLValue) -> LLValue:
        """
        get the length of the slice
        """
        if self.__def_info is None:
            raise CompilerError("DefCtx is not set in LLVMCtx")

        builder = self.__def_info.builder

        slice_len = builder.extract_value(slice_value.value, 1, name="slice.len")
        return LLValue(TypeSpace.u64_id, slice_len)

    def ir_func_obj(self, func: TypeId) -> LLFunction:
        """
        get the LLFunction object of the function according to func TypeId

        - LLFunction object can be function or method
        """
        func_obj = self.__func_obj.get_func_obj(func)
        return LLFunction(type_id=func, function=func_obj)

    def ir_func_call(self, func: LLFunction, args: list[LLValue]) -> LLValue | None:
        """
        call the function and return the result value (if any)

        - do not handle sret
        - do not handle self
        - return an LLValue iff the llvm function has a non-void return type
        """
        if self.__def_info is None:
            raise CompilerError("DefCtx is not set in LLVMCtx")

        builder = self.__def_info.builder
        call_args = [arg.value for arg in args]
        ty_def = self.ty_get(func.type_id)
        return_type: ty.YianType | None = None
        if isinstance(ty_def, (ty.MethodType, ty.FunctionType)):
            return_type = self.ty_get(ty_def.return_type(self.ty_instantiate))
        elif isinstance(ty_def, ty.FunctionPointerType):
            return_type = self.ty_get(ty_def.return_type)
        else:
            raise CompilerError(f"TypeId {func.type_id} is not a callable type: {ty_def}")

        has_ret = not isinstance(return_type, (ty.VoidType, ty.StructType, ty.ArrayType, ty.EnumType, ty.TupleType))

        call_res = builder.call(func.function, call_args)
        if has_ret:
            return LLValue(return_type.type_id, call_res)
        return

    def ir_struct_construct(self, struct_type: ty.StructType, field_values: dict[str, LLValue], target_addr: LLValue) -> None:
        """
        construct a struct object at target_addr with field_values
        """
        if self.__def_info is None:
            raise CompilerError("DefCtx is not set in LLVMCtx")

        expected_fields = set(struct_type.struct_def.fields.keys())
        provided_fields = set(field_values.keys())
        if expected_fields != provided_fields:
            raise CompilerError(f"Struct construct field mismatch for {struct_type.name}")

        builder = self.__def_info.builder
        struct_ll_type = self.__ll_type.get_ll_type(struct_type.type_id)
        struct_ptr = target_addr.value

        expected_ptr_ty = struct_ll_type.as_pointer()
        if target_addr.type_id != struct_type.type_id:
            struct_ptr = builder.bitcast(struct_ptr, expected_ptr_ty, name="struct.ptr")

        fields = sorted(struct_type.struct_def.fields.values(), key=lambda f: f.index)
        for field in fields:
            value = field_values[field.name]
            substs = dict(zip(struct_type.struct_def.generics, struct_type.generic_args))
            field_ty_id = self.__space.instantiate(field.type_id, substs)
            if value.type_id != field_ty_id:
                value = self.ir_assign_cast(field_ty_id, value)

            field_ptr = builder.gep(
                struct_ptr,
                [ir.Constant(ir.IntType(32), 0), ir.Constant(ir.IntType(32), field.index)],
                inbounds=True,
                name=f"field{field.index}.addr",
            )

            store_val = value.value
            if value.type_id != field_ty_id:
                store_val = builder.bitcast(store_val, self.__ll_type.get_ll_type(field_ty_id), name=f"{field.name}.cast")

            builder.store(store_val, field_ptr)

    def ir_store_at_field_path(self, struct_type_id: TypeId, target_addr: LLValue, field_path: list[int], value: LLValue) -> None:
        """
        Store a value into a nested field of a struct, navigating through the given field_path.
        Each element of field_path is a field index. Only the final field is written; intermediate
        structs are traversed via GEP without initializing other fields.
        """
        if self.__def_info is None:
            raise CompilerError("DefCtx is not set in LLVMCtx")

        builder = self.__def_info.builder
        current_ptr = target_addr.value
        current_type_id = struct_type_id

        for i, field_index in enumerate(field_path):
            current_type = self.ty_get(current_type_id).expect_struct()
            struct_ll_type = self.__ll_type.get_ll_type(current_type_id)
            expected_ptr_ty = struct_ll_type.as_pointer()
            if current_ptr.type != expected_ptr_ty:
                current_ptr = builder.bitcast(current_ptr, expected_ptr_ty, name=f"npo.struct{i}.ptr")

            field_ptr = builder.gep(
                current_ptr,
                [ir.Constant(ir.IntType(32), 0), ir.Constant(ir.IntType(32), field_index)],
                inbounds=True,
                name=f"npo.field{field_index}.addr",
            )

            fields = sorted(current_type.struct_def.fields.values(), key=lambda f: f.index)
            field = fields[field_index]
            substs = dict(zip(current_type.struct_def.generics, current_type.generic_args))
            field_ty_id = self.__space.instantiate(field.type_id, substs)

            if i == len(field_path) - 1:
                builder.store(value.value, field_ptr)
            else:
                current_ptr = field_ptr
                current_type_id = field_ty_id

    def ir_enum_payload_address(self, enum_type: ty.EnumType, enum_addr: LLValue, variant: ty.EnumVariant) -> LLValue:
        """
        get the payload address of the enum object at enum_addr
        """
        if self.__def_info is None:
            raise CompilerError("Def info is not set in LLVMCtx")

        payload_ptr = self.__def_info.builder.gep(
            enum_addr.value,
            [ir.Constant(ir.IntType(32), 0), ir.Constant(ir.IntType(32), 1)],
            inbounds=True,
            name="payload.addr",
        )

        assert variant.payload is not None
        payload_ll_type = self.__ll_type.get_ll_type(variant.payload)
        payload_ptr = self.__def_info.builder.bitcast(
            payload_ptr,
            payload_ll_type.as_pointer(),
            name="payload.cast.ptr",
        )

        return LLValue(self.__space.alloc_pointer(variant.payload), payload_ptr)  # type: ignore

    def ir_enum_discriminant_addr(self, enum_type: ty.EnumType, enum_addr: LLValue) -> LLValue:
        """
        get the discriminant address of the enum object at enum_addr

        For optimized Option<T*> (null pointer optimization):
        - null pointer (None) -> discriminant = 0
        - non-null pointer (Some) -> discriminant = 1

        For optimized Option<S> where S is a struct with nullable first field:
        - Access the nullable field through the field path
        - Use that field's value to determine Some/None

        For normal enums:
        - a simple bitcast to i32* is enough
        """
        if self.__def_info is None:
            raise CompilerError("Def info is not set in LLVMCtx")

        builder = self.__def_info.builder

        if self.null_pointer_optimizable(enum_type.type_id):
            opt_type_id = enum_type.generic_args[0]
            opt_type = self.ty_get(opt_type_id)

            if isinstance(opt_type, ty.PointerType):
                pointer_value = builder.load(enum_addr.value, name="optimizable.ptr")
                return LLValue(opt_type_id, pointer_value)
            elif isinstance(opt_type, ty.StructType):
                field_path = self.get_npo_field_path(opt_type_id)
                current_ptr = enum_addr.value
                current_type_id = opt_type_id

                for field_index in field_path:
                    current_type = self.ty_get(current_type_id).expect_struct()

                    field_ptr = builder.gep(
                        current_ptr,
                        [ir.Constant(ir.IntType(32), 0), ir.Constant(ir.IntType(32), field_index)],
                        inbounds=True,
                        name=f"field{field_index}.addr"
                    )

                    fields = sorted(current_type.struct_def.fields.values(), key=lambda f: f.index)
                    if field_index >= len(fields):
                        raise CompilerError(f"Invalid field index {field_index} in struct {current_type.name}")
                    field = fields[field_index]
                    generic_args = current_type.generic_args
                    substs = dict(zip(current_type.struct_def.generics, generic_args))
                    current_type_id = self.__space.instantiate(field.type_id, substs)

                    if field_index == field_path[-1]:
                        pointer_value = builder.load(field_ptr, name="optimizable.ptr")
                        return LLValue(current_type_id, pointer_value)
                    else:
                        nested_struct_type = self.__ll_type.get_ll_type(current_type_id)
                        current_ptr = builder.bitcast(field_ptr, nested_struct_type.as_pointer(), name=f"nested{field_index}.ptr")

                raise CompilerError("Failed to navigate to nullable field")
            else:
                raise CompilerError(f"Unexpected optimizable type: {opt_type}")

        # Normal enum: simple bitcast to i32*
        enum_ptr = enum_addr.value
        tag_ptr = builder.bitcast(enum_ptr, ir.IntType(32).as_pointer(), name="tag.addr")
        return LLValue(self.__space.alloc_pointer(TypeSpace.i32_id), tag_ptr)  # type: ignore

    def ir_alloc_func_obj(self, def_point: DefPoint, func_ty: ty.FunctionType) -> None:
        """
        1. create `ir.Function` object according to func_ty
        2. generate `entry block` of the function
        3. store the `function params` into stack
        """
        func_obj = self.__func_obj.alloc_func_obj(func_ty.type_id)
        entry_block = func_obj.append_basic_block(".entry")
        builder = ir.IRBuilder(entry_block)

        # alloc param objs
        start_index = 0

        # handle sret
        return_type = self.ty_get(func_ty.return_type(self.__space.instantiate))
        if isinstance(return_type, (ty.StructType, ty.ArrayType, ty.EnumType, ty.TupleType)):
            sret_param = func_obj.args[start_index]
            sret_param.name = "sret.ptr"
            sret_param.add_attribute('sret')
            start_index += 1

        symbol_table: dict[SymbolId, ir.AllocaInstr] = {}

        # handle other params
        for i, param in enumerate(func_ty.parameters(self.__space.instantiate), start=start_index):
            param_ll_type = func_obj.args[i].type
            param_obj = builder.alloca(param_ll_type, name=param.name)
            builder.store(func_obj.args[i], param_obj)
            symbol_table[param.stmt_id] = param_obj

        # set def info
        self.__def_info = DefInfo(
            def_point=def_point,
            entry_block=entry_block,
            builder=builder,
            symbol_table=symbol_table,
            self_obj=None,
        )
        self.__def_info_collection[def_point] = self.__def_info

        # set report path
        self.__error_reporter.set_path(self.__unit_data.original_path)

    def ir_alloc_method_obj(self, def_point: DefPoint, method_ty: ty.MethodType) -> None:
        """
        1. create `ir.Function` object according to method_ty
        2. generate `entry block` of the function
        3. store the `method params` into stack
        """
        method_obj = self.__func_obj.alloc_method_obj(method_ty.type_id)
        entry_block = method_obj.append_basic_block(".entry")
        builder = ir.IRBuilder(entry_block)

        # alloc param objs
        start_index = 0

        # handle sret
        return_type = self.ty_get(method_ty.return_type(self.__space.instantiate))
        if isinstance(return_type, (ty.StructType, ty.ArrayType, ty.EnumType, ty.TupleType)):
            sret_param = method_obj.args[start_index]
            sret_param.name = "sret.ptr"
            sret_param.add_attribute('sret')
            start_index += 1

        symbol_table: dict[SymbolId, ir.AllocaInstr] = {}

        # handle self
        self_obj = None
        if not method_ty.is_static:
            self_ll_type = method_obj.args[start_index].type
            self_obj = builder.alloca(self_ll_type, name="self.ptr")
            builder.store(method_obj.args[start_index], self_obj)
            start_index += 1

        # handle other params
        for i, param in enumerate(method_ty.parameters(self.__space.instantiate), start=start_index):
            param_ll_type = method_obj.args[i].type
            param_obj = builder.alloca(param_ll_type, name=param.name)
            builder.store(method_obj.args[i], param_obj)
            symbol_table[param.stmt_id] = param_obj

        # set def info
        self.__def_info = DefInfo(
            def_point=def_point,
            entry_block=entry_block,
            builder=builder,
            symbol_table=symbol_table,
            self_obj=self_obj,
        )
        self.__def_info_collection[def_point] = self.__def_info

        # set report path
        self.__error_reporter.set_path(self.__unit_data.original_path)

    def ir_enter_block(self):
        self.__ll_block.enter_block()

    def ir_enter_loop(self, continue_bb: ir.Block):
        self.__ll_block.enter_loop(continue_bb)

    def ir_enter_switch(self, cond_value: LLValue):
        if self.__def_info is None:
            raise CompilerError("DefCtx is not set in LLVMCtx")
        self.__ll_block.enter_switch(self.__def_info.builder, cond_value.value)

    def ir_generate_bb(self) -> ir.Block:
        if self.__def_info is None:
            raise CompilerError("DefCtx is not set in LLVMCtx")
        return self.__ll_block.generate_bb(self.__def_info.builder)

    def ir_end_bb(self) -> ir.Block:
        if self.__def_info is None:
            raise CompilerError("DefCtx is not set in LLVMCtx")
        return self.__ll_block.end_bb(self.__def_info.builder)

    def ir_branch_end_bb(self):
        if self.__def_info is None:
            raise CompilerError("DefCtx is not set in LLVMCtx")
        self.__ll_block.branch_end_bb(self.__def_info.builder)

    def ir_set_current_bb(self, bb: ir.Block):
        if self.__def_info is None:
            raise CompilerError("DefCtx is not set in LLVMCtx")
        self.__def_info.builder.position_at_end(bb)

    def ir_set_default_bb(self):
        if self.__def_info is None:
            raise CompilerError("DefCtx is not set in LLVMCtx")
        self.__ll_block.set_default_bb(self.__def_info.builder)

    def ir_cbranch(self, condition: LLValue, true_bb: ir.Block, false_bb: ir.Block):
        if self.__def_info is None:
            raise CompilerError("DefCtx is not set in LLVMCtx")
        builder = self.__def_info.builder
        builder.cbranch(condition.value, true_bb, false_bb)

    def ir_branch(self, target_bb: ir.Block):
        if self.__def_info is None:
            raise CompilerError("DefCtx is not set in LLVMCtx")
        self.__def_info.builder.branch(target_bb)

    def ir_break(self):
        if self.__def_info is None:
            raise CompilerError("DefCtx is not set in LLVMCtx")
        self.__ll_block.do_break(self.__def_info.builder)

    def ir_continue(self):
        if self.__def_info is None:
            raise CompilerError("DefCtx is not set in LLVMCtx")
        self.__ll_block.do_continue(self.__def_info.builder)

    def ir_ret_void(self):
        if self.__def_info is None:
            raise CompilerError("DefCtx is not set in LLVMCtx")
        self.__def_info.builder.ret_void()

    def ir_ret(self, value: LLValue):
        if self.__def_info is None:
            raise CompilerError("DefCtx is not set in LLVMCtx")

        # perform cast if needed
        func_type = self.ty_get(self.__def_info.def_point.type_id).expect_callable()
        return_type = self.ty_get(func_type.return_type(self.__space.instantiate))
        value = self.ir_assign_cast(return_type.type_id, value)

        # handle sret
        if isinstance(return_type, (ty.StructType, ty.ArrayType, ty.EnumType, ty.TupleType)):
            func_obj = self.__func_obj.get_func_obj(self.__def_info.def_point.type_id)
            sret_ptr = func_obj.args[0]
            self.__def_info.builder.store(value.value, sret_ptr)
            self.__def_info.builder.ret_void()
        else:
            self.__def_info.builder.ret(value.value)

    def ir_exit_ctx(self):
        if self.__def_info is None:
            raise CompilerError("DefCtx is not set in LLVMCtx")
        self.__ll_block.exit_ctx(self.__def_info.builder)

    def ir_add_case(self, case_value: LLValue, case_bb: ir.Block):
        assert isinstance(case_value.value, ir.Constant)
        self.__ll_block.add_case(case_value.value, case_bb)

    def ir_print(self, value: LLValue) -> None:
        """
        print the value to stdout
        """
        if self.__def_info is None:
            raise CompilerError("DefCtx is not set in LLVMCtx")
        self.__operation.print_value(value, self.__def_info.builder)

    def ir_write(self, fd: LLValue, value: LLValue) -> None:
        """
        write the str value to the given fd
        """
        if self.__def_info is None:
            raise CompilerError("DefCtx is not set in LLVMCtx")
        self.__operation.write_to_fd(fd, value, self.__def_info.builder)

    def ir_read(self, fd: LLValue, buf_addr: LLValue, buf_type_id: TypeId) -> LLValue:
        """
        read from the given fd into the buffer at buf_addr,
        returns a str whose length is the number of bytes actually read
        """
        if self.__def_info is None:
            raise CompilerError("DefCtx is not set in LLVMCtx")
        buf_size = self.__ll_type.get_type_size(buf_type_id)
        return self.__operation.read_value(fd, buf_addr, buf_size, self.__def_info.builder)

    def ir_open(self, path: LLValue, flags: LLValue) -> LLValue:
        """
        open a file with the given flags, returns i32 file descriptor.
        """
        if self.__def_info is None:
            raise CompilerError("DefCtx is not set in LLVMCtx")
        return self.__operation.open_file(path, flags, self.__def_info.builder)

    def ir_close(self, fd: LLValue) -> None:
        """
        close a file descriptor
        """
        if self.__def_info is None:
            raise CompilerError("DefCtx is not set in LLVMCtx")
        self.__operation.close_file(fd, self.__def_info.builder)

    def ir_panic(self) -> None:
        """
        generate panic call at current position
        """
        if self.__def_info is None:
            raise CompilerError("DefCtx is not set in LLVMCtx")
        self.__operation.panic(self.__def_info.builder)

    def ir_unreachable(self):
        """
        generate unreachable instruction at current position
        """
        if self.__def_info is None:
            raise CompilerError("DefCtx is not set in LLVMCtx")
        self.__def_info.builder.unreachable()

    def ir_finalize_functions(self):
        """
        finalize all functions/methods in the module by inserting `ret void` to those without explicit return
        """
        self.__func_obj.finalize_functions()

    @property
    def __unit_data(self) -> UnitData:
        if self.__def_info is None:
            raise RuntimeError("DefCtx is not set in LLVMCtx")
        return self.__unit_datas[self.__def_info.def_point.unit_id]

    def null_pointer_optimizable(self, type_id: TypeId) -> bool:
        """
        Check whether the type can be null pointer optimized.
        Delegates to TypeSpace's cached NPO info.
        """
        return self.__space.null_pointer_optimizable(type_id)

    def get_npo_field_path(self, type_id: TypeId) -> list[int]:
        """
        Get the field path to the nullable field used for optimization.
        Delegates to TypeSpace's cached NPO info.
        """
        return self.__space.get_npo_field_path(type_id)

    def ty_alloc_pointer(self, pointee_type: TypeId) -> TypeId:
        """
        Allocate a pointer type for the given pointee type.
        """
        return self.__space.alloc_pointer(pointee_type)

    def ir_niche(self, type_id: TypeId) -> LLValue:
        """
        Get the niche value for the given type_id.

        Returns:
            LLValue: The niche value as an LLValue.
        """
        if self.__def_info is None:
            raise CompilerError("DefCtx is not set in LLVMCtx")
        opt_type = self.ty_get(type_id)
        if isinstance(opt_type, ty.PointerType):
            null_ptr = ir.Constant(self.__ll_type.get_ll_type(type_id), None)
            return LLValue(type_id, null_ptr)
        else:
            # struct types
            field_path = self.get_npo_field_path(type_id)
            if len(field_path) == 0:
                raise CompilerError(f"Invalid field path for type {type_id}")

            current_type_id = type_id
            for field_index in field_path:
                current_type = self.ty_get(current_type_id).expect_struct()
                fields = sorted(current_type.struct_def.fields.values(), key=lambda f: f.index)
                if field_index >= len(fields):
                    raise CompilerError(f"Invalid field index {field_index} in struct {current_type.name}")
                field = fields[field_index]
                generic_args = current_type.generic_args
                substs = dict(zip(current_type.struct_def.generics, generic_args))
                current_type_id = self.__space.instantiate(field.type_id, substs)

            final_type = self.ty_get(current_type_id)
            if not isinstance(final_type, ty.PointerType):
                raise CompilerError(f"Expected pointer type at end of field path, got {final_type}")

            null_ptr = ir.Constant(self.__ll_type.get_ll_type(current_type_id), None)
            return LLValue(current_type_id, null_ptr)
