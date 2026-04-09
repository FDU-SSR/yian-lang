from compiler.backend.utils.ll_type import LowLevelTypeManager
from compiler.backend.utils.ll_value import LLValue
from compiler.config.constants import IntrinsicFunction
from compiler.config.defs import TypeId
from compiler.utils import ty
from compiler.utils.errors import CompilerError
from compiler.utils.IR import Operator
from compiler.utils.ty import TypeSpace
from llvmlite import ir


class Operation:
    def __init__(self, type_space: TypeSpace, ll_type: LowLevelTypeManager):
        self.__space = type_space
        self.__ll_type = ll_type

    def binary_op(self, operator: Operator, left: LLValue, right: LLValue, builder: ir.IRBuilder) -> LLValue:
        """
        perform binary operation and return the result value
        """
        match operator:
            case Operator.Add:
                return self.__add(left, right, builder)
            case Operator.Minus:
                return self.__subtract(left, right, builder)
            case Operator.Star:
                return self.__multiply(left, right, builder)
            case Operator.Slash:
                return self.__divide(left, right, builder)
            case Operator.Percent:
                return self.__modulus(left, right, builder)
            case Operator.Ampersand:
                return self.__bitwise_and(left, right, builder)
            case Operator.Pipe:
                return self.__bitwise_or(left, right, builder)
            case Operator.Caret:
                return self.__bitwise_xor(left, right, builder)
            case Operator.Shl:
                return self.__shift_left(left, right, builder)
            case Operator.Shr:
                return self.__shift_right(left, right, builder)
            case Operator.Eq:
                return self.__equal(left, right, builder)
            case Operator.Neq:
                return self.__not_equal(left, right, builder)
            case Operator.Gt:
                return self.__greater_than(left, right, builder)
            case Operator.Lt:
                return self.__less_than(left, right, builder)
            case Operator.Ge:
                return self.__greater_equal(left, right, builder)
            case Operator.Le:
                return self.__less_equal(left, right, builder)
            case Operator.And:
                return self.__logical_and(left, right, builder)
            case Operator.Or:
                return self.__logical_or(left, right, builder)
            case _:
                raise CompilerError(f"Unsupported binary operator: {operator}")

    def unary_op(self, operator: Operator, operand: LLValue, builder: ir.IRBuilder) -> LLValue:
        """
        perform unary operation and return the result value
        """
        match operator:
            case Operator.Add:
                return operand  # unary plus, no change
            case Operator.Minus:
                return self.__negate(operand, builder)
            case Operator.Tilde:
                return self.__bitwise_not(operand, builder)
            case Operator.Not:
                return self.__logical_not(operand, builder)
            case _:
                raise CompilerError(f"Unsupported unary operator: {operator}")

    def delete(self, value: LLValue, builder: ir.IRBuilder) -> None:
        """
        delete the heap-allocated object pointed by value

        TODO: consider custom deleter functions for complex types
        """
        ptr_type = self.__space[value.type_id]
        if not isinstance(ptr_type, ty.PointerType):
            raise CompilerError(f"Cannot delete non-pointer type: {ptr_type}")
        free_func = self.__ll_type.intrinsic_func(IntrinsicFunction.Free)
        i8_value = builder.bitcast(value.value, ir.IntType(8).as_pointer())
        builder.call(free_func, [i8_value])

    def heap_alloc(self, size_in_bytes: LLValue, data_type: TypeId, builder: ir.IRBuilder) -> LLValue:
        """
        allocate heap memory of size_in_bytes and return the pointer to the allocated memory
        """
        size = size_in_bytes.value
        malloc_func = self.__ll_type.intrinsic_func(IntrinsicFunction.Malloc)
        raw_ptr = builder.call(malloc_func, [size])
        ptr_type = self.__space.alloc_pointer(data_type)
        casted_ptr = builder.bitcast(raw_ptr, self.__ll_type.get_ll_type(ptr_type))
        return LLValue(ptr_type, casted_ptr)  # type: ignore

    def print_value(self, value: LLValue, builder: ir.IRBuilder) -> None:
        """
        print the value to stdout
        """
        str_slice = value.value
        str_ptr = builder.extract_value(str_slice, 0)
        str_len = builder.extract_value(str_slice, 1)
        fd_stdout = ir.Constant(ir.IntType(32), 1)
        write_func = self.__ll_type.intrinsic_func(IntrinsicFunction.Write)
        builder.call(write_func, [fd_stdout, str_ptr, str_len])

    def write_to_fd(self, fd: LLValue, value: LLValue, builder: ir.IRBuilder) -> None:
        """
        write the str value to the given fd
        """
        str_slice = value.value
        str_ptr = builder.extract_value(str_slice, 0)
        str_len = builder.extract_value(str_slice, 1)
        write_func = self.__ll_type.intrinsic_func(IntrinsicFunction.Write)
        builder.call(write_func, [fd.value, str_ptr, str_len])

    def read_value(self, fd: LLValue, buf_addr: LLValue, buf_size: int, builder: ir.IRBuilder) -> LLValue:
        """
        read from the given fd into the buffer at buf_addr with buf_size bytes,
        returns a str whose length is the number of bytes actually read
        """
        buf_ptr = builder.bitcast(buf_addr.value, ir.IntType(8).as_pointer())
        buf_len = ir.Constant(ir.IntType(64), buf_size)

        read_func = self.__ll_type.intrinsic_func(IntrinsicFunction.Read)
        bytes_read = builder.call(read_func, [fd.value, buf_ptr, buf_len])

        str_ll_type = self.__ll_type.get_ll_type(TypeSpace.str_id)
        str_res = ir.Constant(str_ll_type, ir.Undefined)
        str_res = builder.insert_value(str_res, buf_ptr, 0, name="read.res.ptr")
        str_res = builder.insert_value(str_res, bytes_read, 1, name="read.res.len")
        return LLValue(TypeSpace.str_id, str_res)

    def open_file(self, path: LLValue, flags: LLValue, builder: ir.IRBuilder) -> LLValue:
        """
        open a file given a str path and flags,
        returns i32 file descriptor.
        mode defaults to 0644 (420).
        """
        # Extract pointer and length from str
        str_ptr = builder.extract_value(path.value, 0, name="open.str.ptr")
        str_len = builder.extract_value(path.value, 1, name="open.str.len")

        # Allocate stack buffer for null-terminated C string: length + 1
        one = ir.Constant(ir.IntType(64), 1)
        cstr_len = builder.add(str_len, one, name="open.cstr.len")
        cstr_buf = builder.alloca(ir.IntType(8), cstr_len, name="open.cstr.buf")

        # memcpy the string data
        memcpy_func = self.__ll_type.intrinsic_func(IntrinsicFunction.MemCopy)
        builder.call(memcpy_func, [cstr_buf, str_ptr, str_len])

        # Null-terminate
        null_pos = builder.gep(cstr_buf, [str_len], name="open.null.pos")
        builder.store(ir.Constant(ir.IntType(8), 0), null_pos)

        # mode = 0644 (420) for file creation, harmless for read-only
        mode = ir.Constant(ir.IntType(32), 420)

        open_func = self.__ll_type.intrinsic_func(IntrinsicFunction.Open)
        fd = builder.call(open_func, [cstr_buf, flags.value, mode], name="open.fd")
        return LLValue(TypeSpace.i32_id, fd)

    def close_file(self, fd: LLValue, builder: ir.IRBuilder) -> None:
        """
        close a file descriptor
        """
        close_func = self.__ll_type.intrinsic_func(IntrinsicFunction.Close)
        builder.call(close_func, [fd.value])

    def panic(self, builder: ir.IRBuilder) -> None:
        """
        panic the program execution
        """
        exit_func = self.__ll_type.intrinsic_func(IntrinsicFunction.Exit)
        exit_code = ir.Constant(ir.IntType(32), 1)
        builder.call(exit_func, [exit_code])

    def cast(self, value: LLValue, to_type: ty.YianType, builder: ir.IRBuilder) -> LLValue:
        """
        explicitly cast value to to_type and return the casted value
        """
        match to_type:
            case ty.IntType():
                return self.__cast_to_int(value, to_type, builder)
            case ty.FloatType():
                return self.__cast_to_float(value, to_type, builder)
            case ty.CharType():
                return self.__cast_to_char(value, to_type, builder)
            case _:
                raise CompilerError(f"Unsupported cast to type: {to_type}")

    def bitcast(self, value: LLValue, to_type: ty.YianType, builder: ir.IRBuilder) -> LLValue:
        """
        perform bitcast of value to to_type and return the casted value

        Note: bitcast is a low-level operation that reinterprets the bits of the value as the target type.
        It does not perform any actual data conversion, so it should only be used when the source and target types
        have the same size and compatible representations (e.g., pointer types).
        """
        src_type = self.__space[value.type_id]
        to_ll_type = self.__ll_type.get_ll_type(to_type.type_id)

        # Handle integer <-> pointer explicitly.
        if isinstance(src_type, ty.IntType) and isinstance(to_type, ty.PointerType):
            result = builder.inttoptr(value.value, to_ll_type)
            return LLValue(to_type.type_id, result)  # type: ignore

        if isinstance(src_type, ty.PointerType) and isinstance(to_type, ty.IntType):
            result = builder.ptrtoint(value.value, to_ll_type)
            return LLValue(to_type.type_id, result)  # type: ignore

        result = builder.bitcast(value.value, to_ll_type)
        return LLValue(to_type.type_id, result)  # type: ignore

    def byte_offset(self, ptr_value: LLValue, offset_value: LLValue, builder: ir.IRBuilder) -> LLValue:
        """
        perform byte offset on ptr_value by offset_value and return the resulting pointer

        Note: byte_offset is a low-level operation that computes a new pointer by adding a byte offset to the original pointer.
        It does not perform any bounds checking or type safety checks, so it should be used with caution.
        """
        i8_ptr = builder.bitcast(ptr_value.value, ir.IntType(8).as_pointer())
        offset_ptr = builder.gep(i8_ptr, [offset_value.value])
        result = builder.bitcast(offset_ptr, self.__ll_type.get_ll_type(ptr_value.type_id))
        return LLValue(ptr_value.type_id, result)  # type: ignore

    def mem_copy(self, dest: LLValue, src: LLValue, size: LLValue, builder: ir.IRBuilder) -> None:
        """
        perform memory copy from src to dest with the given size in bytes

        Note: mem_copy is a low-level operation that copies raw bytes from the source address to the destination address.
        It does not perform any safety checks, so it should be used with caution.
        """
        dest_i8_ptr = builder.bitcast(dest.value, ir.IntType(8).as_pointer())
        src_i8_ptr = builder.bitcast(src.value, ir.IntType(8).as_pointer())
        memcpy_func = self.__ll_type.intrinsic_func(IntrinsicFunction.MemCopy)
        builder.call(memcpy_func, [dest_i8_ptr, src_i8_ptr, size.value])

    def __add(self, left: LLValue, right: LLValue, builder: ir.IRBuilder) -> LLValue:
        ltype = self.__space[left.type_id]
        rtype = self.__space[right.type_id]
        if isinstance(ltype, ty.IntType) and isinstance(rtype, ty.IntType):
            assert ltype.type_id == rtype.type_id
            result: ir.Value = builder.add(left.value, right.value)  # type: ignore
            return LLValue(ltype.type_id, result)
        elif isinstance(ltype, ty.FloatType) and isinstance(rtype, ty.FloatType):
            assert ltype.type_id == rtype.type_id
            result = builder.fadd(left.value, right.value)  # type: ignore
            return LLValue(ltype.type_id, result)
        elif isinstance(ltype, ty.PointerType) and isinstance(rtype, ty.IntType):
            assert rtype.size == 8 and not rtype.is_signed
            result = builder.gep(left.value, [right.value])
            return LLValue(ltype.type_id, result)
        elif isinstance(ltype, ty.IntType) and isinstance(rtype, ty.PointerType):
            assert ltype.size == 8 and not ltype.is_signed
            result = builder.gep(right.value, [left.value])
            return LLValue(rtype.type_id, result)
        else:
            raise CompilerError(f"Unsupported types for addition: {ltype}, {rtype}")

    def __subtract(self, left: LLValue, right: LLValue, builder: ir.IRBuilder) -> LLValue:
        ltype = self.__space[left.type_id]
        rtype = self.__space[right.type_id]
        if isinstance(ltype, ty.IntType) and isinstance(rtype, ty.IntType):
            assert ltype.type_id == rtype.type_id
            result: ir.Value = builder.sub(left.value, right.value)  # type: ignore
            return LLValue(ltype.type_id, result)
        elif isinstance(ltype, ty.FloatType) and isinstance(rtype, ty.FloatType):
            assert ltype.type_id == rtype.type_id
            result = builder.fsub(left.value, right.value)  # type: ignore
            return LLValue(ltype.type_id, result)
        elif isinstance(ltype, ty.PointerType) and isinstance(rtype, ty.IntType):
            assert rtype.size == 8 and not rtype.is_signed
            neg_right = builder.neg(right.value)
            result = builder.gep(left.value, [neg_right])
            return LLValue(ltype.type_id, result)
        elif isinstance(ltype, ty.PointerType) and isinstance(rtype, ty.PointerType):
            assert ltype.pointee_type == rtype.pointee_type
            elem_size = self.__ll_type.get_type_size(ltype.pointee_type)
            left_int = builder.ptrtoint(left.value, ir.IntType(64))
            right_int = builder.ptrtoint(right.value, ir.IntType(64))
            diff = builder.sub(left_int, right_int)
            div: ir.Value = builder.sdiv(diff, ir.Constant(ir.IntType(64), elem_size))  # type: ignore
            return LLValue(TypeSpace.i64_id, div)
        else:
            raise CompilerError(f"Unsupported types for subtraction: {ltype}, {rtype}")

    def __multiply(self, left: LLValue, right: LLValue, builder: ir.IRBuilder) -> LLValue:
        ltype = self.__space[left.type_id]
        rtype = self.__space[right.type_id]
        if isinstance(ltype, ty.IntType) and isinstance(rtype, ty.IntType):
            assert ltype.type_id == rtype.type_id
            result: ir.Value = builder.mul(left.value, right.value)  # type: ignore
            return LLValue(ltype.type_id, result)
        elif isinstance(ltype, ty.FloatType) and isinstance(rtype, ty.FloatType):
            assert ltype.type_id == rtype.type_id
            result = builder.fmul(left.value, right.value)  # type: ignore
            return LLValue(ltype.type_id, result)
        else:
            raise CompilerError(f"Unsupported types for multiplication: {ltype}, {rtype}")

    def __divide(self, left: LLValue, right: LLValue, builder: ir.IRBuilder) -> LLValue:
        ltype = self.__space[left.type_id]
        rtype = self.__space[right.type_id]
        if isinstance(ltype, ty.IntType) and isinstance(rtype, ty.IntType):
            assert ltype.type_id == rtype.type_id
            if ltype.is_signed:
                result: ir.Value = builder.sdiv(left.value, right.value)  # type: ignore
            else:
                result: ir.Value = builder.udiv(left.value, right.value)  # type: ignore
            return LLValue(ltype.type_id, result)
        elif isinstance(ltype, ty.FloatType) and isinstance(rtype, ty.FloatType):
            assert ltype.type_id == rtype.type_id
            result = builder.fdiv(left.value, right.value)  # type: ignore
            return LLValue(ltype.type_id, result)
        else:
            raise CompilerError(f"Unsupported types for division: {ltype}, {rtype}")

    def __modulus(self, left: LLValue, right: LLValue, builder: ir.IRBuilder) -> LLValue:
        ltype = self.__space[left.type_id]
        rtype = self.__space[right.type_id]
        if isinstance(ltype, ty.IntType) and isinstance(rtype, ty.IntType):
            assert ltype.type_id == rtype.type_id
            if ltype.is_signed:
                result: ir.Value = builder.srem(left.value, right.value)  # type: ignore
            else:
                result: ir.Value = builder.urem(left.value, right.value)  # type: ignore
            return LLValue(ltype.type_id, result)
        elif isinstance(ltype, ty.FloatType) and isinstance(rtype, ty.FloatType):
            assert ltype.type_id == rtype.type_id
            result = builder.frem(left.value, right.value)  # type: ignore
            return LLValue(ltype.type_id, result)
        else:
            raise CompilerError(f"Unsupported types for modulus: {ltype}, {rtype}")

    def __bitwise_and(self, left: LLValue, right: LLValue, builder: ir.IRBuilder) -> LLValue:
        ltype = self.__space[left.type_id]
        rtype = self.__space[right.type_id]
        if isinstance(ltype, ty.IntType) and isinstance(rtype, ty.IntType):
            assert ltype.type_id == rtype.type_id
            result: ir.Value = builder.and_(left.value, right.value)  # type: ignore
            return LLValue(ltype.type_id, result)
        else:
            raise CompilerError(f"Unsupported types for bitwise AND: {ltype}, {rtype}")

    def __bitwise_or(self, left: LLValue, right: LLValue, builder: ir.IRBuilder) -> LLValue:
        ltype = self.__space[left.type_id]
        rtype = self.__space[right.type_id]
        if isinstance(ltype, ty.IntType) and isinstance(rtype, ty.IntType):
            assert ltype.type_id == rtype.type_id
            result: ir.Value = builder.or_(left.value, right.value)  # type: ignore
            return LLValue(ltype.type_id, result)
        else:
            raise CompilerError(f"Unsupported types for bitwise OR: {ltype}, {rtype}")

    def __bitwise_xor(self, left: LLValue, right: LLValue, builder: ir.IRBuilder) -> LLValue:
        ltype = self.__space[left.type_id]
        rtype = self.__space[right.type_id]
        if isinstance(ltype, ty.IntType) and isinstance(rtype, ty.IntType):
            assert ltype.type_id == rtype.type_id
            result: ir.Value = builder.xor(left.value, right.value)  # type: ignore
            return LLValue(ltype.type_id, result)
        else:
            raise CompilerError(f"Unsupported types for bitwise XOR: {ltype}, {rtype}")

    def __shift_left(self, left: LLValue, right: LLValue, builder: ir.IRBuilder) -> LLValue:
        ltype = self.__space[left.type_id]
        rtype = self.__space[right.type_id]
        if isinstance(ltype, ty.IntType) and isinstance(rtype, ty.IntType):
            rhs_val = right.value
            if rtype.size < ltype.size:
                rhs_val = builder.zext(rhs_val, left.value.type)  # type: ignore
            elif rtype.size > ltype.size:
                rhs_val = builder.trunc(rhs_val, left.value.type)  # type: ignore

            result: ir.Value = builder.shl(left.value, rhs_val)  # type: ignore
            return LLValue(ltype.type_id, result)
        else:
            raise CompilerError(f"Unsupported types for shift left: {ltype}, {rtype}")

    def __shift_right(self, left: LLValue, right: LLValue, builder: ir.IRBuilder) -> LLValue:
        ltype = self.__space[left.type_id]
        rtype = self.__space[right.type_id]
        if isinstance(ltype, ty.IntType) and isinstance(rtype, ty.IntType):
            rhs_val = right.value
            if rtype.size < ltype.size:
                rhs_val = builder.zext(rhs_val, left.value.type)  # type: ignore
            elif rtype.size > ltype.size:
                rhs_val = builder.trunc(rhs_val, left.value.type)  # type: ignore

            if ltype.is_signed:
                result: ir.Value = builder.ashr(left.value, rhs_val)  # type: ignore
            else:
                result: ir.Value = builder.lshr(left.value, rhs_val)  # type: ignore
            return LLValue(ltype.type_id, result)
        else:
            raise CompilerError(f"Unsupported types for shift right: {ltype}, {rtype}")

    def __eq_util(self, left: LLValue, right: LLValue, builder: ir.IRBuilder, cmp_op: str) -> LLValue:
        ltype = self.__space[left.type_id]
        rtype = self.__space[right.type_id]
        if isinstance(ltype, ty.IntType) and isinstance(rtype, ty.IntType):
            assert ltype.type_id == rtype.type_id
            result: ir.Value = builder.icmp_signed(cmp_op, left.value, right.value)  # type: ignore
            return LLValue(TypeSpace.bool_id, result)
        elif isinstance(ltype, ty.FloatType) and isinstance(rtype, ty.FloatType):
            assert ltype.type_id == rtype.type_id
            result = builder.fcmp_ordered(cmp_op, left.value, right.value)  # type: ignore
            return LLValue(TypeSpace.bool_id, result)
        elif isinstance(ltype, ty.BoolType) and isinstance(rtype, ty.BoolType):
            result: ir.Value = builder.icmp_unsigned(cmp_op, left.value, right.value)  # type: ignore
            return LLValue(TypeSpace.bool_id, result)
        elif isinstance(ltype, ty.CharType) and isinstance(rtype, ty.CharType):
            result: ir.Value = builder.icmp_unsigned(cmp_op, left.value, right.value)  # type: ignore
            return LLValue(TypeSpace.bool_id, result)
        elif isinstance(ltype, ty.PointerType) and isinstance(rtype, ty.PointerType):
            assert ltype.pointee_type == rtype.pointee_type
            result: ir.Value = builder.icmp_unsigned(cmp_op, left.value, right.value)  # type: ignore
            return LLValue(TypeSpace.bool_id, result)
        elif isinstance(ltype, ty.EnumType) and isinstance(rtype, ty.EnumType):
            left_tag = builder.extract_value(left.value, 0)
            right_tag = builder.extract_value(right.value, 0)
            result: ir.Value = builder.icmp_unsigned(cmp_op, left_tag, right_tag)  # type: ignore
            return LLValue(TypeSpace.bool_id, result)
        else:
            raise CompilerError(f"Unsupported types for equality comparison: {self.__space.get_name(ltype.type_id)}, {self.__space.get_name(rtype.type_id)}")

    def __equal(self, left: LLValue, right: LLValue, builder: ir.IRBuilder) -> LLValue:
        return self.__eq_util(left, right, builder, "==")

    def __not_equal(self, left: LLValue, right: LLValue, builder: ir.IRBuilder) -> LLValue:
        return self.__eq_util(left, right, builder, "!=")

    def __ord_util(self, left: LLValue, right: LLValue, builder: ir.IRBuilder, cmp_op: str) -> LLValue:
        ltype = self.__space[left.type_id]
        rtype = self.__space[right.type_id]

        if isinstance(ltype, ty.IntType) and isinstance(rtype, ty.IntType):
            assert ltype.type_id == rtype.type_id
            if ltype.is_signed:
                result: ir.Value = builder.icmp_signed(cmp_op, left.value, right.value)  # type: ignore
            else:
                result: ir.Value = builder.icmp_unsigned(cmp_op, left.value, right.value)  # type: ignore
        elif isinstance(ltype, ty.FloatType) and isinstance(rtype, ty.FloatType):
            assert ltype.type_id == rtype.type_id
            result = builder.fcmp_ordered(cmp_op, left.value, right.value)  # type: ignore
        elif isinstance(ltype, ty.PointerType) and isinstance(rtype, ty.PointerType):
            assert ltype.pointee_type == rtype.pointee_type
            result: ir.Value = builder.icmp_unsigned(cmp_op, left.value, right.value)  # type: ignore
        else:
            raise CompilerError(f"Unsupported types for ordering comparison: {ltype}, {rtype}")

        return LLValue(TypeSpace.bool_id, result)

    def __greater_than(self, left: LLValue, right: LLValue, builder: ir.IRBuilder) -> LLValue:
        return self.__ord_util(left, right, builder, ">")

    def __less_than(self, left: LLValue, right: LLValue, builder: ir.IRBuilder) -> LLValue:
        return self.__ord_util(left, right, builder, "<")

    def __greater_equal(self, left: LLValue, right: LLValue, builder: ir.IRBuilder) -> LLValue:
        return self.__ord_util(left, right, builder, ">=")

    def __less_equal(self, left: LLValue, right: LLValue, builder: ir.IRBuilder) -> LLValue:
        return self.__ord_util(left, right, builder, "<=")

    def __logical_and(self, left: LLValue, right: LLValue, builder: ir.IRBuilder) -> LLValue:
        """
        TODO: ESSENTIALS: this implementation lacks short-circuit evaluation
        """
        ltype = self.__space[left.type_id]
        rtype = self.__space[right.type_id]
        if isinstance(ltype, ty.BoolType) and isinstance(rtype, ty.BoolType):
            result: ir.Value = builder.and_(left.value, right.value)  # type: ignore
            return LLValue(TypeSpace.bool_id, result)
        else:
            raise CompilerError(f"Unsupported types for logical AND: {ltype}, {rtype}")

    def __logical_or(self, left: LLValue, right: LLValue, builder: ir.IRBuilder) -> LLValue:
        """
        TODO: ESSENTIALS: this implementation lacks short-circuit evaluation
        """
        ltype = self.__space[left.type_id]
        rtype = self.__space[right.type_id]
        if isinstance(ltype, ty.BoolType) and isinstance(rtype, ty.BoolType):
            result: ir.Value = builder.or_(left.value, right.value)  # type: ignore
            return LLValue(TypeSpace.bool_id, result)
        else:
            raise CompilerError(f"Unsupported types for logical OR: {ltype}, {rtype}")

    def __negate(self, operand: LLValue, builder: ir.IRBuilder) -> LLValue:
        otype = self.__space[operand.type_id]
        if isinstance(otype, ty.IntType):
            result: ir.Value = builder.neg(operand.value)  # type: ignore
            return LLValue(otype.type_id, result)
        elif isinstance(otype, ty.FloatType):
            result = builder.fneg(operand.value)  # type: ignore
            return LLValue(otype.type_id, result)
        else:
            raise CompilerError(f"Unsupported type for negation: {otype}")

    def __bitwise_not(self, operand: LLValue, builder: ir.IRBuilder) -> LLValue:
        otype = self.__space[operand.type_id]
        if isinstance(otype, ty.IntType):
            result: ir.Value = builder.not_(operand.value)  # type: ignore
            return LLValue(otype.type_id, result)
        else:
            raise CompilerError(f"Unsupported type for bitwise NOT: {otype}")

    def __logical_not(self, operand: LLValue, builder: ir.IRBuilder) -> LLValue:
        otype = self.__space[operand.type_id]
        if isinstance(otype, ty.BoolType):
            result: ir.Value = builder.not_(operand.value)  # type: ignore
            return LLValue(otype.type_id, result)
        else:
            raise CompilerError(f"Unsupported type for logical NOT: {otype}")

    def __cast_to_int(self, value: LLValue, to_type: ty.IntType, builder: ir.IRBuilder) -> LLValue:
        from_type = self.__space[value.type_id]
        to_ll_type = self.__ll_type.get_ll_type(to_type.type_id)
        if isinstance(from_type, ty.IntType):
            if from_type.size < to_type.size:
                # extend
                if from_type.is_signed:
                    result: ir.Value = builder.sext(value.value, to_ll_type)  # type: ignore
                else:
                    result: ir.Value = builder.zext(value.value, to_ll_type)  # type: ignore
                return LLValue(to_type.type_id, result)
            elif from_type.size > to_type.size:
                # truncate
                result: ir.Value = builder.trunc(value.value, to_ll_type)  # type: ignore
                return LLValue(to_type.type_id, result)
            else:
                # same size, no change
                return LLValue(to_type.type_id, value.value)
        elif isinstance(from_type, ty.FloatType):
            # float to int
            if to_type.is_signed:
                result: ir.Value = builder.fptosi(value.value, to_ll_type)  # type: ignore
            else:
                # saturating cast: negative floats become 0
                zero = ir.Constant(value.value.type, 0.0)  # type: ignore
                is_neg = builder.fcmp_ordered("<", value.value, zero)
                safe_val = builder.select(is_neg, zero, value.value)
                result: ir.Value = builder.fptoui(safe_val, to_ll_type)  # type: ignore
            return LLValue(to_type.type_id, result)
        elif isinstance(from_type, ty.CharType):
            # char to int
            if to_type.size > 4:
                result: ir.Value = builder.zext(value.value, to_ll_type)  # type: ignore
            elif to_type.size < 4:
                result: ir.Value = builder.trunc(value.value, to_ll_type)  # type: ignore
            else:
                result: ir.Value = value.value
            return LLValue(to_type.type_id, result)
        else:
            raise CompilerError(f"Unsupported cast from {from_type} to {to_type}")

    def __cast_to_float(self, value: LLValue, to_type: ty.FloatType, builder: ir.IRBuilder) -> LLValue:
        from_type = self.__space[value.type_id]
        to_ll_type = self.__ll_type.get_ll_type(to_type.type_id)
        if isinstance(from_type, ty.FloatType):
            if from_type.size < to_type.size:
                # extend
                result: ir.Value = builder.fpext(value.value, to_ll_type)  # type: ignore
                return LLValue(to_type.type_id, result)
            elif from_type.size > to_type.size:
                # truncate
                result: ir.Value = builder.fptrunc(value.value, to_ll_type)  # type: ignore
                return LLValue(to_type.type_id, result)
            else:
                # same size, no change
                return LLValue(to_type.type_id, value.value)
        elif isinstance(from_type, ty.IntType):
            # int to float
            if from_type.is_signed:
                result: ir.Value = builder.sitofp(value.value, to_ll_type)  # type: ignore
            else:
                result: ir.Value = builder.uitofp(value.value, to_ll_type)  # type: ignore
            return LLValue(to_type.type_id, result)
        else:
            raise CompilerError(f"Unsupported cast from {from_type} to {to_type}")

    def __cast_to_char(self, value: LLValue, to_type: ty.CharType, builder: ir.IRBuilder) -> LLValue:
        from_type = self.__space[value.type_id]
        to_ll_type = self.__ll_type.get_ll_type(to_type.type_id)
        if isinstance(from_type, ty.CharType):
            # same type, no change
            return LLValue(to_type.type_id, value.value)
        elif isinstance(from_type, ty.IntType):
            assert not from_type.is_signed and from_type.size <= 4
            if from_type.size < 4:
                result: ir.Value = builder.zext(value.value, to_ll_type)  # type: ignore
            else:
                result = value.value
            return LLValue(to_type.type_id, result)
        else:
            raise CompilerError(f"Unsupported cast from {from_type} to {to_type}")
