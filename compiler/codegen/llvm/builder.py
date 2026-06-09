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
        global_var = self.__module.get_string_global(value.encode("utf-8"))
        ptr = global_var.gep([ir.Constant(ir.IntType(32), 0), ir.Constant(ir.IntType(32), 0)])  # type: ignore
        length = ir.Constant(ir.IntType(64), len(value))  # type: ignore
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

    # ------------------------------------------------------------------
    # statements
    # ------------------------------------------------------------------

    def var_ptr(self, symbol_id: int, result: str) -> None:
        alloca_ptr = self.__func.get_var_ptr(symbol_id)
        self.__func.set_reg(result, alloca_ptr)

    def alloca(self, type_id: int) -> LLValue:
        return LLValue(type_id, self.__builder.alloca(self.__ll_type_ctx.get_ll_type(type_id).ir_type))  # type: ignore

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
            result_type = base_type.pointee_type
        else:
            result_type = base.type_id
        idx_vals = [self.i32(i).ir_val for i in indices]
        ir_val = self.__builder.gep(base.ir_val, idx_vals, inbounds=True)  # type: ignore
        result_val = LLValue(result_type, ir_val)
        self.__func.set_reg(result, result_val)
        return result_val

    # -- arithmetic / comparison --

    def binary(self, op: BinaryOperator, lhs: LLValue, rhs: LLValue, result: str) -> LLValue:
        if op.is_comparison():
            ir_val = self.__cmp_impl(op, lhs.ir_val, rhs.ir_val)
            result_val = LLValue(self.__type_ctx.bool_id, ir_val)
        else:
            ir_val = self.__arith_impl(op, lhs.ir_val, rhs.ir_val)
            result_val = LLValue(lhs.type_id, ir_val)
        self.__func.set_reg(result, result_val)
        return result_val

    def unary(self, op: UnaryOperator, operand: LLValue, result: str) -> LLValue:
        if op == UnaryOperator.Neg:
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
            ir_val = self.__builder.fptosi(value.ir_val, dest_ll_type) if dst.signed else self.__builder.fptoui(value.ir_val, dest_ll_type)  # type: ignore
        elif isinstance(src, Type.FloatType) and isinstance(dst, Type.FloatType):
            ir_val = self.__builder.fpext(value.ir_val, dest_ll_type) if src.size < dst.size else self.__builder.fptrunc(value.ir_val, dest_ll_type)  # type: ignore
        elif isinstance(src, Type.PointerType) and isinstance(dst, Type.PointerType):
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
            fields = base_type.get_fields(self.__type_ctx)
            field_type = fields[index].type_id
        elif isinstance(base_type, Type.TupleType):
            field_type = base_type.element_types[index]
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
        ir_val = self.__builder.call(callee.__ir, resolved)  # type: ignore
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
        return LLValue(func_ptr_type_id, func.__ir)  # type: ignore

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

    def aggregate(self, type_id: int, fields: list[LLValue], result: str) -> None:
        aggregate_val = self.undef(type_id)
        for i, field_value in enumerate(fields):
            aggregate_val = self.insert_value(aggregate_val, field_value, i)
        self.__func.set_reg(result, aggregate_val)

    def array(self, type_id: int, elements: list[LLValue], result: str) -> None:
        array_val = self.undef(type_id)
        for i, element_value in enumerate(elements):
            array_val = self.insert_value(array_val, element_value, i)
        self.__func.set_reg(result, array_val)

    def construct_enum_variant(
        self, enum_type_id: int, discriminant: int,
        payload_type_id: int, payload_fields: list[LLValue],
        result: str,
    ) -> None:
        """Construct an enum variant value. Emits multiple LLVM IR internally."""
        variant_val = self.undef(enum_type_id)
        variant_val = self.insert_value(variant_val, self.i32(discriminant), 0)
        payload_val = self.undef(payload_type_id)
        for i, fv in enumerate(payload_fields):
            payload_val = self.insert_value(payload_val, fv, i)
        payload_size = self.__ll_type_ctx.get_type_size(payload_type_id)
        payload_arr = ir.ArrayType(ir.IntType(8), payload_size)  # type: ignore
        bitcast_ir = self.__builder.bitcast(payload_val.ir_val, payload_arr)  # type: ignore
        ir_val = self.__builder.insert_value(variant_val.ir_val, bitcast_ir, 1)  # type: ignore
        self.__func.set_reg(result, LLValue(enum_type_id, ir_val))

    def unpack_enum_payload(
        self, matched: LLValue, block_label: str,
        payload_type_id: int, fields: list[tuple[int, int]],
    ) -> None:
        """Unpack enum variant payload fields into variable allocas.

        Inserts instructions at the beginning of ``block_label``.
        ``fields`` is ``(field_index, symbol_id)`` pairs.
        """
        self.position_at(block_label, where=BuilderPosition.First)

        payload_type_def = self.__type_ctx[payload_type_id]
        assert isinstance(payload_type_def, Type.StructType)
        payload_fields = payload_type_def.get_fields(self.__type_ctx)

        gep_val = self.__builder.gep(matched.ir_val, [self.i32(0).ir_val, self.i32(1).ir_val], inbounds=True)  # type: ignore
        payload_ptr_ll_type = ir.PointerType(self.__ll_type_ctx.get_ll_type(payload_type_id).ir_type)  # type: ignore
        payload = self.__builder.bitcast(gep_val, payload_ptr_ll_type)  # type: ignore

        for field_index, symbol_id in fields:
            if field_index >= len(payload_fields):
                break
            field_ptr = self.__builder.gep(payload, [self.i32(0).ir_val, self.i32(field_index).ir_val], inbounds=True)  # type: ignore
            field_value = self.__builder.load(field_ptr)  # type: ignore
            alloca_ptr = self.__func.get_var_ptr(symbol_id)
            self.__builder.store(field_value, alloca_ptr.ir_val)  # type: ignore

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

    def __cmp_impl(self, op: BinaryOperator, lhs: ir.Value, rhs: ir.Value) -> ir.Value:
        predicate = {
            BinaryOperator.Eq: "==", BinaryOperator.Neq: "!=",
            BinaryOperator.Lt: "<", BinaryOperator.Gt: ">",
            BinaryOperator.Leq: "<=", BinaryOperator.Geq: ">=",
        }[op]
        if isinstance(lhs.type, (ir.IntType, ir.PointerType)):  # type: ignore
            return self.__builder.icmp_signed(predicate, lhs, rhs)  # type: ignore
        return self.__builder.fcmp_ordered(predicate, lhs, rhs)  # type: ignore

    _ARITH_OPS = {
        BinaryOperator.Add: "add", BinaryOperator.Sub: "sub",
        BinaryOperator.Mul: "mul", BinaryOperator.Div: "sdiv",
        BinaryOperator.Mod: "srem", BinaryOperator.BitAnd: "and_",
        BinaryOperator.BitOr: "or_", BinaryOperator.BitXor: "xor",
        BinaryOperator.Shl: "shl", BinaryOperator.Shr: "ashr",
    }

    def __arith_impl(self, op: BinaryOperator, lhs: ir.Value, rhs: ir.Value) -> ir.Value:
        return getattr(self.__builder, self._ARITH_OPS[op])(lhs, rhs)
