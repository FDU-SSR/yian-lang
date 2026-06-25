"""
LLBuilder — wraps llvmlite ``ir.IRBuilder`` to hide LLVM-level details.
"""

from __future__ import annotations

from enum import Enum, auto

from llvmlite import ir

from compiler.analysis.ty import ty as Type
from compiler.analysis.ty.context import TypeCtx
from compiler.codegen.llvm.intrinsics import IntrinsicKind
from compiler.codegen.llvm.module import LLFunction, LLModule
from compiler.codegen.llvm.types import LLTypeCtx
from compiler.codegen.llvm.value import LLValue
from compiler.frontend.parse.operator import BinaryOperator, UnaryOperator


class BuilderPosition(Enum):
    End = auto()
    First = auto()
    Phi = auto()


class LLBuilder:
    """High-level builder that emits LLVM IR for a single function."""

    def __init__(self, func: LLFunction, module: LLModule, ll_type_ctx: LLTypeCtx, type_ctx: TypeCtx) -> None:
        self.__func = func
        self.__module = module
        self.__ll_type_ctx = ll_type_ctx
        self.__type_ctx = type_ctx
        self.__builder: ir.IRBuilder

    # ------------------------------------------------------------------
    # constants
    # ------------------------------------------------------------------

    def i32(self, v: int) -> LLValue:
        return LLValue(self.__type_ctx.u32_id, ir.Constant(ir.IntType(32), v))  # type: ignore

    def i64(self, v: int) -> LLValue:
        return LLValue(self.__type_ctx.u64_id, ir.Constant(ir.IntType(64), v))  # type: ignore

    def undef(self, type_id: int) -> LLValue:
        return LLValue(type_id, ir.Constant(self.__ll_type_ctx.get_ll_type(type_id).ir_type, ir.Undefined))  # type: ignore

    def sizeof_const(self, type_id: int, result: str) -> None:
        self.__func.set_reg(result, LLValue(self.__type_ctx.u64_id, ir.Constant(ir.IntType(64), self.__ll_type_ctx.get_type_size(type_id))))  # type: ignore

    def string_literal(self, value: str, type_id: int) -> LLValue:
        """Create a ``{i8*, i64}`` struct value for a string literal."""
        encoded = value.encode("utf-8")
        global_var = self.__module.get_string_global(encoded)
        ptr = global_var.gep([ir.Constant(ir.IntType(32), 0), ir.Constant(ir.IntType(32), 0)])  # type: ignore
        length = ir.Constant(ir.IntType(64), len(encoded))  # type: ignore
        return LLValue(type_id, ir.Constant.literal_struct([ptr, length]))  # type: ignore

    # ------------------------------------------------------------------
    # builder position
    # ------------------------------------------------------------------

    def position_at(self, label: str, where: BuilderPosition = BuilderPosition.End) -> None:
        block = self.__func.block(label)
        self.__builder = ir.IRBuilder(block)
        if where == BuilderPosition.Phi:
            self.__builder.position_at_start(block)  # type: ignore
        elif where == BuilderPosition.First:
            instructions = list(block.instructions)  # type: ignore
            if instructions:
                self.__builder.position_before(instructions[0])  # type: ignore

    @property
    def current_block_label(self) -> str:
        """Return the label of the block the builder is currently positioned in."""
        return self.__builder.block.name  # type: ignore

    # ------------------------------------------------------------------
    # statements
    # ------------------------------------------------------------------

    def var_ptr(self, symbol_id: int, result: str) -> None:
        alloca_ptr = self.__func.get_var_ptr(symbol_id)
        self.__func.set_reg(result, alloca_ptr)

    def alloca(self, type_id: int) -> LLValue:
        ptr_type_id = self.__type_ctx.alloc_pointer(type_id)
        return LLValue(ptr_type_id, self.__builder.alloca(self.__ll_type_ctx.get_ll_type(type_id).ir_type))  # type: ignore

    def alloca_store(self, value: LLValue, result: str) -> None:
        alloca_val = self.alloca(value.type_id)
        self.store(value, alloca_val)
        self.__func.set_reg(result, alloca_val)

    def malloc(self, type_id: int, size: LLValue, result: str) -> None:
        raw = self.__call_intrinsic(IntrinsicKind.Malloc, [size])
        ptr_type_id = self.__type_ctx.alloc_pointer(type_id)
        ptr_ll_type = self.__ll_type_ctx.get_ll_type(ptr_type_id).ir_type
        ir_val = self.__builder.bitcast(raw.ir_val, ptr_ll_type)  # type: ignore
        self.__func.set_reg(result, LLValue(ptr_type_id, ir_val))  # type: ignore

    def delete(self, ptr: LLValue) -> None:
        i8_ptr_type_id = self.__type_ctx.alloc_pointer(self.__type_ctx.u8_id)
        casted = self.__builder.bitcast(ptr.ir_val, ir.PointerType(ir.IntType(8)))  # type: ignore
        self.__call_intrinsic(IntrinsicKind.Free, [LLValue(i8_ptr_type_id, casted)])  # type: ignore

    # -- memory --

    def load(self, ptr: LLValue, result: str) -> LLValue:
        ptr_type = self.__type_ctx[ptr.type_id]
        assert isinstance(ptr_type, Type.PointerType)
        ir_val = self.__builder.load(ptr.ir_val)  # type: ignore
        result_val = LLValue(ptr_type.pointee_type, ir_val)
        self.__func.set_reg(result, result_val)
        return result_val

    def store(self, value: LLValue, ptr: LLValue) -> None:
        self.__builder.store(value.ir_val, ptr.ir_val)  # type: ignore

    def gep(self, base: LLValue, indices: list[int], result: str) -> LLValue:
        base_type = self.__type_ctx[base.type_id]
        if isinstance(base_type, Type.PointerType):
            pointee_type_id = base_type.pointee_type
        else:
            pointee_type_id = base.type_id

        # Apply indices after the implicit pointer dereference (index 0)
        # to compute the final pointee type.
        for idx in indices[1:]:
            ty = self.__type_ctx[pointee_type_id]
            if isinstance(ty, Type.StructType):
                fields = self.__type_ctx.get_struct_fields(ty.type_id)
                pointee_type_id = fields[idx].type_id
            elif isinstance(ty, Type.TupleType):
                pointee_type_id = ty.element_types[idx]
            elif isinstance(ty, Type.ArrayType):
                pointee_type_id = ty.element_type

        result_type_id = self.__type_ctx.alloc_pointer(pointee_type_id)
        idx_vals = [self.i32(i).ir_val for i in indices]
        ir_val = self.__builder.gep(base.ir_val, idx_vals, inbounds=True)  # type: ignore
        result_val = LLValue(result_type_id, ir_val)
        self.__func.set_reg(result, result_val)
        return result_val

    # -- arithmetic / comparison --

    def binary(self, op: BinaryOperator, lhs: LLValue, rhs: LLValue, result: str) -> LLValue:
        if op.is_comparison():
            ir_val = self.__cmp_impl(op, lhs.ir_val, rhs.ir_val, lhs.type_id)
            result_val = LLValue(self.__type_ctx.bool_id, ir_val)
        else:
            ir_val = self.__arith_impl(op, lhs.ir_val, rhs.ir_val, lhs.type_id)
            result_val = LLValue(lhs.type_id, ir_val)
        self.__func.set_reg(result, result_val)
        return result_val

    def element_ptr(self, base: LLValue, offset: LLValue, result: str) -> LLValue:
        """Pointer arithmetic: ptr + offset → gep ptr, offset"""
        ptr_ty = self.__type_ctx[base.type_id]
        assert isinstance(ptr_ty, Type.PointerType)
        ir_val = self.__builder.gep(base.ir_val, [offset.ir_val], inbounds=False)  # type: ignore
        result_val = LLValue(base.type_id, ir_val)
        self.__func.set_reg(result, result_val)
        return result_val

    def ptr_diff(self, lhs: LLValue, rhs: LLValue, result: str) -> LLValue:
        """Pointer difference: ptr - ptr → integer offset (in elements, not bytes)"""
        lhs_ty = self.__type_ctx[lhs.type_id]
        assert isinstance(lhs_ty, Type.PointerType)
        lhs_int = self.__builder.ptrtoint(lhs.ir_val, ir.IntType(64))  # type: ignore
        rhs_int = self.__builder.ptrtoint(rhs.ir_val, ir.IntType(64))  # type: ignore
        byte_diff = self.__builder.sub(lhs_int, rhs_int)  # type: ignore
        elem_size = self.__builder.udiv(  # type: ignore
            byte_diff,
            ir.Constant(ir.IntType(64), self.__ll_type_ctx.get_type_size(lhs_ty.pointee_type))  # type: ignore
        )
        result_val = LLValue(self.__type_ctx.u64_id, elem_size)  # type: ignore
        self.__func.set_reg(result, result_val)
        return result_val

    def unary(self, op: UnaryOperator, operand: LLValue, result: str) -> LLValue:
        if op == UnaryOperator.Neg:
            if isinstance(operand.ir_val.type, ir.types._BaseFloatType):  # type: ignore
                # llvmlite's neg() emits `sub` which is integer-only; use fsub for floats.
                ir_val = self.__builder.fsub(ir.Constant(operand.ir_val.type, 0.0), operand.ir_val)  # type: ignore
            else:
                ir_val = self.__builder.neg(operand.ir_val)  # type: ignore
        elif op == UnaryOperator.LogicalNot:
            ir_val = self.__builder.not_(operand.ir_val)  # type: ignore
        elif op == UnaryOperator.BitNot:
            ir_val = self.__builder.xor(operand.ir_val, ir.Constant(operand.ir_val.type, -1))  # type: ignore
        else:
            raise ValueError(f"Unsupported unary: {op}")
        result_val = LLValue(operand.type_id, ir_val)  # type: ignore
        self.__func.set_reg(result, result_val)
        return result_val

    # -- cast --

    def cast(self, value: LLValue, to_type: int, result: str) -> LLValue:
        src = self.__type_ctx[value.type_id]
        dst = self.__type_ctx[to_type]
        dest_ll_type = self.__ll_type_ctx.get_ll_type(to_type).ir_type

        if isinstance(src, (Type.IntType, Type.CharType, Type.BoolType)) and isinstance(
            dst, (Type.IntType, Type.CharType, Type.BoolType)
        ):
            src_width = self.__ll_type_ctx.get_ll_type(value.type_id).ir_type.width  # type: ignore
            dest_width = dest_ll_type.width  # type: ignore
            if dest_width > src_width:
                if isinstance(src, Type.CharType) or (isinstance(src, Type.IntType) and not src.signed):
                    ir_val = self.__builder.zext(value.ir_val, dest_ll_type)  # type: ignore
                else:
                    ir_val = self.__builder.sext(value.ir_val, dest_ll_type)  # type: ignore
            elif dest_width < src_width:
                ir_val = self.__builder.trunc(value.ir_val, dest_ll_type)  # type: ignore
            else:
                ir_val = value.ir_val
        elif isinstance(src, Type.IntType) and isinstance(dst, Type.FloatType):
            ir_val = self.__builder.sitofp(value.ir_val, dest_ll_type) if src.signed else self.__builder.uitofp(value.ir_val, dest_ll_type)  # type: ignore
        elif isinstance(src, Type.FloatType) and isinstance(dst, Type.IntType):
            if dst.signed:
                ir_val = self.__builder.fptosi(value.ir_val, dest_ll_type)  # type: ignore
            else:
                # Negative float → unsigned int should yield 0.
                zero_f = ir.Constant(value.ir_val.type, 0.0)  # type: ignore
                pos = self.__builder.fcmp_ordered(">=", value.ir_val, zero_f)  # type: ignore
                raw = self.__builder.fptoui(value.ir_val, dest_ll_type)  # type: ignore
                zero_i = ir.Constant(dest_ll_type, 0)  # type: ignore
                ir_val = self.__builder.select(pos, raw, zero_i)  # type: ignore
        elif isinstance(src, Type.FloatType) and isinstance(dst, Type.FloatType):
            ir_val = self.__builder.fpext(value.ir_val, dest_ll_type) if src.size < dst.size else self.__builder.fptrunc(value.ir_val, dest_ll_type)  # type: ignore
        elif isinstance(src, (Type.PointerType, Type.NullPtrType)) and isinstance(dst, Type.PointerType):
            ir_val = self.__builder.bitcast(value.ir_val, dest_ll_type)  # type: ignore
        else:
            raise ValueError(f"Unsupported cast: {type(src).__name__} → {type(dst).__name__}")
        result_val = LLValue(to_type, ir_val)  # type: ignore
        self.__func.set_reg(result, result_val)
        return result_val

    # -- aggregate --

    def extract_value(self, base: LLValue, index: int, result: str) -> LLValue:
        ir_val = self.__builder.extract_value(base.ir_val, index)  # type: ignore
        base_type = self.__type_ctx[base.type_id]
        if isinstance(base_type, Type.StructType):
            fields = self.__type_ctx.get_struct_fields(base.type_id)
            field_type = fields[index].type_id
        elif isinstance(base_type, Type.TupleType):
            field_type = base_type.element_types[index]
        elif isinstance(base_type, Type.EnumType):
            # Enum layout: { i32 discriminant, [pad x i8] payload }
            # Field 0 is always the i32 discriminant.
            field_type = self.__type_ctx.u32_id
        else:
            field_type = base.type_id
        result_val = LLValue(field_type, ir_val)
        self.__func.set_reg(result, result_val)
        return result_val

    def insert_value(self, agg: LLValue, value: LLValue, index: int) -> LLValue:
        ir_val = self.__builder.insert_value(agg.ir_val, value.ir_val, index)  # type: ignore
        return LLValue(agg.type_id, ir_val)

    # -- call --

    def call(self, callee: LLFunction, args: list[LLValue], result: str, return_type_id: int) -> LLValue:
        resolved = [a.ir_val for a in args]
        ir_val = self.__builder.call(callee.ir_func, resolved)  # type: ignore
        result_val = LLValue(return_type_id, ir_val)
        self.__func.set_reg(result, result_val)
        return result_val

    def call_func(self, callee_type: int, args: list[LLValue], result: str, return_type_id: int) -> LLValue:
        callee = self.__module.get_func(callee_type)
        assert callee is not None, f"Function callee_type={callee_type} not declared"
        return self.call(callee, args, result, return_type_id)

    def call_value(self, callee: LLValue, args: list[LLValue], result: str, return_type_id: int) -> LLValue:
        resolved_args = [a.ir_val for a in args]
        ir_val = self.__builder.call(callee.ir_val, resolved_args)  # type: ignore
        result_val = LLValue(return_type_id, ir_val)
        self.__func.set_reg(result, result_val)
        return result_val

    def func_ptr(self, func: LLFunction, func_ptr_type_id: int) -> LLValue:
        return LLValue(func_ptr_type_id, func.ir_func)  # type: ignore

    def func_ptr_by_type(self, func_type_id: int, func_ptr_type_id: int, result: str) -> None:
        callee = self.__module.get_func(func_type_id)
        assert callee is not None, f"FuncPtr func_type_id={func_type_id} not declared"
        self.__func.set_reg(result, self.func_ptr(callee, func_ptr_type_id))

    # -- phi --

    def phi(self, type_id: int, incoming: list[tuple[str, LLValue]], result: str) -> None:
        phi_node = self.__builder.phi(self.__ll_type_ctx.get_ll_type(type_id).ir_type)  # type: ignore
        self.__func.set_reg(result, LLValue(type_id, phi_node))
        for src_label, val in incoming:
            phi_node.add_incoming(val.ir_val, self.__func.block(src_label))  # type: ignore

    # -- aggregate construct --

    def __build_aggregate(self, type_id: int, field_values: list[LLValue]) -> LLValue:
        """Build an aggregate value by inserting each field value at its index."""
        val = self.undef(type_id)
        for i, fv in enumerate(field_values):
            val = self.insert_value(val, fv, i)
        return val

    def aggregate(self, type_id: int, fields: list[LLValue], result: str) -> None:
        self.__func.set_reg(result, self.__build_aggregate(type_id, fields))

    def array(self, type_id: int, elements: list[LLValue], result: str) -> None:
        self.__func.set_reg(result, self.__build_aggregate(type_id, elements))

    def construct_enum_variant(self, enum_type_id: int, discriminant: int, payload_type: int | None, payload_fields: list[LLValue] | None, result: str) -> None:
        """Construct an enum variant value via temporary alloca + store + load.

        Layout: { i32 discriminant, [pad x i8] payload }
        1. alloca the enum type
        2. store discriminant into field 0
        3. if payload: bitcast field 1 to the payload struct pointer, store payload fields
        4. load the complete enum value
        """
        tmp_ptr = self.__builder.alloca(self.__ll_type_ctx.get_ll_type(enum_type_id).ir_type)  # type: ignore

        # Store discriminant at field 0
        disc_ptr = self.__builder.gep(tmp_ptr, [self.i32(0).ir_val, self.i32(0).ir_val], inbounds=True)  # type: ignore
        self.__builder.store(self.i32(discriminant).ir_val, disc_ptr)  # type: ignore

        if payload_type is not None:
            assert payload_fields is not None
            payload_val = self.__build_aggregate(payload_type, payload_fields)
            # Bitcast the payload array pointer (field 1) to the payload struct pointer
            payload_arr_ptr = self.__builder.gep(tmp_ptr, [self.i32(0).ir_val, self.i32(1).ir_val], inbounds=True)  # type: ignore
            payload_typed_ptr = self.__builder.bitcast(payload_arr_ptr, self.__ll_type_ctx.get_ll_type(self.__type_ctx.alloc_pointer(payload_type)).ir_type)  # type: ignore
            self.__builder.store(payload_val.ir_val, payload_typed_ptr)  # type: ignore

        ir_val = self.__builder.load(tmp_ptr)  # type: ignore
        self.__func.set_reg(result, LLValue(enum_type_id, ir_val))

    def unpack_enum_payload(
        self, matched: LLValue, block_label: str,
        payload_type_id: int, fields: list[tuple[int, int]],
    ) -> None:
        """Unpack enum variant payload fields into variable allocas.

        ``fields`` is ``(field_index, symbol_id)`` pairs.
        The caller is responsible for positioning the builder appropriately
        (typically at the start of the arm block).
        """
        payload_type_def = self.__type_ctx[payload_type_id]
        assert isinstance(payload_type_def, Type.StructType)
        payload_fields = self.__type_ctx.get_struct_fields(payload_type_id)

        gep_val = self.__builder.gep(matched.ir_val, [self.i32(0).ir_val, self.i32(1).ir_val], inbounds=True)  # type: ignore
        payload_ptr_ll_type = self.__ll_type_ctx.get_ll_type(self.__type_ctx.alloc_pointer(payload_type_id)).ir_type  # type: ignore
        payload = self.__builder.bitcast(gep_val, payload_ptr_ll_type)  # type: ignore

        for field_index, symbol_id in fields:
            if field_index >= len(payload_fields):
                break
            field_ptr = self.__builder.gep(payload, [self.i32(0).ir_val, self.i32(field_index).ir_val], inbounds=True)  # type: ignore
            field_value = self.__builder.load(field_ptr)  # type: ignore
            alloca_ptr = self.__func.get_var_ptr(symbol_id)
            self.__builder.store(field_value, alloca_ptr.ir_val)  # type: ignore

    def unpack_enum_payload_ref(
        self, matched: LLValue, block_label: str,
        payload_type_id: int, fields: list[tuple[int, int]],
    ) -> None:
        """Unpack enum variant payload fields into reference variable allocas.

        ``matched`` is a pointer to the enum (type ``&E``, not a stack copy).
        ``fields`` is ``(field_index, symbol_id)`` pairs.

        Unlike ``unpack_enum_payload`` which loads field values and stores
        copies, this method GEPs to each field and stores the *pointer*
        directly into the variable's alloca. The resulting bindings are
        references (&T) pointing into the original enum value.
        """
        payload_type_def = self.__type_ctx[payload_type_id]
        assert isinstance(payload_type_def, Type.StructType)
        payload_fields = self.__type_ctx.get_struct_fields(payload_type_id)

        gep_val = self.__builder.gep(matched.ir_val, [self.i32(0).ir_val, self.i32(1).ir_val], inbounds=True)  # type: ignore
        payload_ptr_ll_type = self.__ll_type_ctx.get_ll_type(self.__type_ctx.alloc_pointer(payload_type_id)).ir_type  # type: ignore
        payload = self.__builder.bitcast(gep_val, payload_ptr_ll_type)  # type: ignore

        for field_index, symbol_id in fields:
            if field_index >= len(payload_fields):
                break
            field_ptr = self.__builder.gep(payload, [self.i32(0).ir_val, self.i32(field_index).ir_val], inbounds=True)  # type: ignore
            alloca_ptr = self.__func.get_var_ptr(symbol_id)
            self.__builder.store(field_ptr, alloca_ptr.ir_val)  # type: ignore

    # -- sys --

    def sys_write(self, fd: LLValue, buf: LLValue) -> None:
        self.__call_intrinsic(IntrinsicKind.Write, [
            fd, self.__extract_value_raw(buf, 0), self.__extract_value_raw(buf, 1),
        ])

    def sys_read(self, fd: LLValue, buf: LLValue, result: str) -> None:
        raw = self.__call_intrinsic(IntrinsicKind.Read, [
            fd, self.__extract_value_raw(buf, 0), self.__extract_value_raw(buf, 1),
        ])
        self.__func.set_reg(result, raw)

    # ------------------------------------------------------------------
    # terminators
    # ------------------------------------------------------------------

    def ret(self, value: LLValue | None) -> None:
        if value is None:
            self.__builder.ret_void()
        else:
            self.__builder.ret(value.ir_val)  # type: ignore

    def br(self, target_label: str) -> None:
        self.__builder.branch(self.__func.block(target_label))  # type: ignore

    def condbr(self, cond: LLValue, then_label: str, else_label: str) -> None:
        self.__builder.cbranch(  # type: ignore
            cond.ir_val,
            self.__func.block(then_label),
            self.__func.block(else_label),
        )

    def switch(self, value: LLValue, cases: list[tuple[LLValue, str]], default_label: str) -> None:
        """Emit a switch instruction. ``value`` must be integer/char type.
        ``cases`` are ``(case_value, target_label)`` pairs.
        """
        default_block = self.__func.block(default_label) if default_label else None
        if default_block is None:
            default_block = self.__func.new_block("match.unreach")
            ir.IRBuilder(default_block).unreachable()
        switch_instr = self.__builder.switch(value.ir_val, default_block)  # type: ignore
        for case_value, target_label in cases:
            switch_instr.add_case(case_value.ir_val, self.__func.block(target_label))  # type: ignore

    def unreachable(self) -> None:
        self.__builder.unreachable()

    def panic(self, msg: LLValue) -> None:
        self.__call_intrinsic(IntrinsicKind.Write, [
            self.i32(2), self.__extract_value_raw(msg, 0), self.__extract_value_raw(msg, 1),
        ])
        self.__call_intrinsic(IntrinsicKind.Exit, [self.i32(1)])
        self.__builder.unreachable()

    # ------------------------------------------------------------------
    # internal
    # ------------------------------------------------------------------

    def __extract_value_raw(self, base: LLValue, index: int) -> LLValue:
        ir_val = self.__builder.extract_value(base.ir_val, index)  # type: ignore
        return LLValue(base.type_id, ir_val)

    def __call_intrinsic(self, kind: IntrinsicKind, args: list[LLValue]) -> LLValue:
        callee = self.__module.intrinsics.get(kind)
        raw_args = [a.ir_val for a in args]
        result = self.__builder.call(callee, raw_args)  # type: ignore
        return LLValue(self.__intrinsic_return_type_id(kind), result)

    def __intrinsic_return_type_id(self, kind: IntrinsicKind) -> int:
        match kind:
            case IntrinsicKind.Malloc:
                return self.__type_ctx.alloc_pointer(self.__type_ctx.u8_id)
            case IntrinsicKind.Free | IntrinsicKind.Exit | IntrinsicKind.MemCopy:
                return self.__type_ctx.void_id
            case IntrinsicKind.Write | IntrinsicKind.Read:
                return self.__type_ctx.u64_id
            case IntrinsicKind.SysRandom:
                return self.__type_ctx.u32_id

    def __cmp_impl(self, op: BinaryOperator, lhs: ir.Value, rhs: ir.Value, type_id: int) -> ir.Value:
        predicate = {
            BinaryOperator.Eq: "==", BinaryOperator.Neq: "!=",
            BinaryOperator.Lt: "<", BinaryOperator.Gt: ">",
            BinaryOperator.Leq: "<=", BinaryOperator.Geq: ">=",
        }[op]
        if isinstance(lhs.type, (ir.IntType, ir.PointerType)):  # type: ignore
            ty = self.__type_ctx[type_id]
            if isinstance(ty, Type.IntType) and not ty.signed:
                return self.__builder.icmp_unsigned(predicate, lhs, rhs)  # type: ignore
            return self.__builder.icmp_signed(predicate, lhs, rhs)  # type: ignore
        return self.__builder.fcmp_ordered(predicate, lhs, rhs)  # type: ignore

    ARITH_OPS = {
        BinaryOperator.Add: "add", BinaryOperator.Sub: "sub",
        BinaryOperator.Mul: "mul", BinaryOperator.Div: "sdiv",
        BinaryOperator.Mod: "srem", BinaryOperator.BitAnd: "and_",
        BinaryOperator.BitOr: "or_", BinaryOperator.BitXor: "xor",
        BinaryOperator.Shl: "shl", BinaryOperator.Shr: "ashr",
    }

    FLOAT_ARITH_OPS = {
        BinaryOperator.Add: "fadd", BinaryOperator.Sub: "fsub",
        BinaryOperator.Mul: "fmul", BinaryOperator.Div: "fdiv",
        BinaryOperator.Mod: "frem",
    }

    def __arith_impl(self, op: BinaryOperator, lhs: ir.Value, rhs: ir.Value, type_id: int) -> ir.Value:
        if isinstance(lhs.type, ir.types._BaseFloatType):  # type: ignore
            return getattr(self.__builder, self.FLOAT_ARITH_OPS[op])(lhs, rhs)
        ty = self.__type_ctx[type_id]
        if isinstance(ty, Type.IntType) and not ty.signed:
            if op == BinaryOperator.Div:
                return self.__builder.udiv(lhs, rhs)  # type: ignore
            if op == BinaryOperator.Mod:
                return self.__builder.urem(lhs, rhs)  # type: ignore
            if op == BinaryOperator.Shr:
                return self.__builder.lshr(lhs, rhs)  # type: ignore
        return getattr(self.__builder, self.ARITH_OPS[op])(lhs, rhs)
