"""
LLBuilder — wraps llvmlite ``ir.IRBuilder`` to hide LLVM-level details.

The translator works exclusively through this builder; it never touches
``ir.IRBuilder``, ``ir.Constant``, ``ir.IntType``, or similar llvmlite
constructs directly.
"""

from __future__ import annotations

from llvmlite import ir  # type: ignore[import-untyped]

from compiler.analysis.ty import ty as Type
from compiler.analysis.ty.context import TypeCtx
from compiler.codegen.cfg import ir as IR
from compiler.codegen.llvm.intrinsics import IntrinsicKind
from compiler.codegen.llvm.module import LLFunction, LLModule
from compiler.codegen.llvm.types import LLTypeCtx
from compiler.codegen.llvm.value import LLValue
from compiler.frontend.parse.operator import BinaryOperator, UnaryOperator


class LLBuilder:
    """High-level builder that emits LLVM IR for a single function."""

    def __init__(
        self,
        func: LLFunction,
        module: LLModule,
        ll_type_ctx: LLTypeCtx,
        type_ctx: TypeCtx,
    ) -> None:
        self.__func = func
        self.__module = module
        self.__ll_type_ctx = ll_type_ctx
        self.__type_ctx = type_ctx
        self.__builder: ir.IRBuilder  # set by position_at_*

    # ------------------------------------------------------------------
    # factory: fork a builder for a different block
    # ------------------------------------------------------------------

    def fork(self, label: str, where: str = "end") -> LLBuilder:
        """Create a new builder sharing context, positioned at *label*."""
        forked = object.__new__(LLBuilder)
        forked.__func = self.__func
        forked.__module = self.__module
        forked.__ll_type_ctx = self.__ll_type_ctx
        forked.__type_ctx = self.__type_ctx
        forked.position_at(label, where)
        return forked

    def fork_block(self, block: ir.Block, where: str = "end") -> LLBuilder:
        """Create a new builder sharing context, positioned at *block*."""
        forked = object.__new__(LLBuilder)
        forked.__func = self.__func
        forked.__module = self.__module
        forked.__ll_type_ctx = self.__ll_type_ctx
        forked.__type_ctx = self.__type_ctx
        forked.__builder = ir.IRBuilder(block)
        if where == "first":
            ins = list(block.instructions)
            if ins:
                forked.__builder.position_before(ins[0])
        elif where == "phi":
            forked.__builder.position_at_start(block)
        return forked

    # ------------------------------------------------------------------
    # resolve IR.Value → LLValue
    # ------------------------------------------------------------------

    def resolve(self, v: IR.Value) -> LLValue:
        if isinstance(v, IR.Reg):
            return self.__func.reg(v.name)
        if isinstance(v, IR.IntLiteral):
            return self._const(v.type_id, v.value)
        if isinstance(v, IR.FloatLiteral):
            return self._const(v.type_id, v.value)
        if isinstance(v, IR.BoolLiteral):
            return self._const(v.type_id, 1 if v.value else 0)
        if isinstance(v, IR.CharLiteral):
            return self._const(v.type_id, ord(v.value))
        if isinstance(v, IR.StringLiteral):
            ir_val = self.__module.str_literal_val(v.value.encode("utf-8"))
            return LLValue(v.type_id, ir_val)
        raise ValueError(f"Unknown value: {type(v).__name__}")

    # ------------------------------------------------------------------
    # constants
    # ------------------------------------------------------------------

    def _const(self, type_id: int, value: int | float) -> LLValue:
        ir_val = ir.Constant(self.__ll_type_ctx.get_ll_type(type_id), value)
        return LLValue(type_id, ir_val)

    def i32(self, v: int) -> LLValue:
        return LLValue(-1, ir.Constant(ir.IntType(32), v))

    def i64(self, v: int) -> LLValue:
        return LLValue(-1, ir.Constant(ir.IntType(64), v))

    def i8_ptr_type(self) -> ir.PointerType:
        return ir.PointerType(ir.IntType(8))

    def undef(self, type_id: int) -> LLValue:
        ir_val = ir.Constant(self.__ll_type_ctx.get_ll_type(type_id), ir.Undefined)
        return LLValue(type_id, ir_val)

    def sizeof_const(self, type_id: int) -> LLValue:
        return LLValue(-1, ir.Constant(ir.IntType(64), self.__ll_type_ctx.get_type_size(type_id)))

    def padding_array(self, byte_size: int) -> ir.ArrayType:
        return ir.ArrayType(ir.IntType(8), byte_size)

    # ------------------------------------------------------------------
    # ll type helpers (delegate to LLTypeCtx)
    # ------------------------------------------------------------------

    def ll_type(self, type_id: int) -> ir.Type:
        return self.__ll_type_ctx.get_ll_type(type_id)

    # ------------------------------------------------------------------
    # builder position
    # ------------------------------------------------------------------

    def position_at(self, label: str, where: str = "end") -> None:
        bb = self.__func.block(label)
        self.__builder = ir.IRBuilder(bb)
        if where == "phi":
            self.__builder.position_at_start(bb)
        elif where == "first":
            ins = list(bb.instructions)
            if ins:
                self.__builder.position_before(ins[0])

    # ------------------------------------------------------------------
    # phi
    # ------------------------------------------------------------------

    def phi(self, type_id: int) -> LLValue:
        ir_val = self.__builder.phi(self.__ll_type_ctx.get_ll_type(type_id))
        return LLValue(type_id, ir_val)

    def add_incoming(self, phi: LLValue, value: LLValue, src_label: str) -> None:
        phi.ir_val.add_incoming(value.ir_val, self.__func.block(src_label))  # type: ignore[union-attr]

    # ------------------------------------------------------------------
    # memory
    # ------------------------------------------------------------------

    def alloca(self, type_id: int) -> LLValue:
        ir_val = self.__builder.alloca(self.__ll_type_ctx.get_ll_type(type_id))
        return LLValue(type_id, ir_val)

    def load(self, ptr: IR.Value) -> LLValue:
        resolved = self.resolve(ptr)
        ptr_type = self.__type_ctx[resolved.type_id]
        assert isinstance(ptr_type, Type.PointerType)
        ir_val = self.__builder.load(resolved.ir_val)
        return LLValue(ptr_type.pointee_type, ir_val)

    def store(self, value: IR.Value, ptr: IR.Value | LLValue) -> None:
        v = self.resolve(value)
        if isinstance(ptr, LLValue):
            self.__builder.store(v.ir_val, ptr.ir_val)
        else:
            p = self.resolve(ptr)
            self.__builder.store(v.ir_val, p.ir_val)

    def gep(self, base: IR.Value, indices: list[int], inbounds: bool = True) -> LLValue:
        resolved = self.resolve(base)
        base_type = self.__type_ctx[resolved.type_id]
        # Determine pointee type for result
        if isinstance(base_type, Type.PointerType):
            result_type = base_type.pointee_type
        else:
            result_type = resolved.type_id
        idx_vals = [self.i32(i).ir_val for i in indices]
        ir_val = self.__builder.gep(resolved.ir_val, idx_vals, inbounds=inbounds)
        return LLValue(result_type, ir_val)

    def gep_raw(self, base: ir.Value, indices: list[int], inbounds: bool = True) -> ir.Value:
        idx_vals = [self.i32(i).ir_val for i in indices]
        return self.__builder.gep(base, idx_vals, inbounds=inbounds)

    def load_raw(self, ptr: ir.Value) -> ir.Value:
        return self.__builder.load(ptr)

    def store_raw(self, value: ir.Value, ptr: ir.Value) -> None:
        self.__builder.store(value, ptr)

    # ------------------------------------------------------------------
    # arithmetic / comparison
    # ------------------------------------------------------------------

    def binary(self, op: BinaryOperator, lhs: IR.Value, rhs: IR.Value) -> LLValue:
        l = self.resolve(lhs)
        r = self.resolve(rhs)
        if op.is_comparison():
            ir_val = self.__cmp_impl(op, l.ir_val, r.ir_val)
            return LLValue(-1, ir_val)  # comparison results are always bool (i1 in LLVM)
        ir_val = self.__arith_impl(op, l.ir_val, r.ir_val)
        return LLValue(l.type_id, ir_val)

    def unary(self, op: UnaryOperator, operand: IR.Value) -> LLValue:
        v = self.resolve(operand)
        if op == UnaryOperator.Neg:
            ir_val = self.__builder.neg(v.ir_val)
        elif op == UnaryOperator.Not:
            ir_val = self.__builder.not_(v.ir_val)
        elif op == UnaryOperator.BitNot:
            ir_val = self.__builder.xor(v.ir_val, ir.Constant(v.ir_val.type, -1))
        else:
            raise ValueError(f"Unsupported unary: {op}")
        return LLValue(v.type_id, ir_val)

    # ------------------------------------------------------------------
    # cast
    # ------------------------------------------------------------------

    def cast(self, value: IR.Value, to_type: int) -> LLValue:
        v = self.resolve(value)
        src = self.__type_ctx[value.type_id]
        dst = self.__type_ctx[to_type]
        dt = self.__ll_type_ctx.get_ll_type(to_type)

        if isinstance(src, (Type.IntType, Type.CharType, Type.BoolType)) and isinstance(
            dst, (Type.IntType, Type.CharType, Type.BoolType)
        ):
            sw = self.__ll_type_ctx.get_ll_type(value.type_id).width
            dw = dt.width
            if dw > sw:
                if isinstance(src, Type.CharType) or (
                    isinstance(src, Type.IntType) and not src.signed
                ):
                    ir_val = self.__builder.zext(v.ir_val, dt)
                else:
                    ir_val = self.__builder.sext(v.ir_val, dt)
            elif dw < sw:
                ir_val = self.__builder.trunc(v.ir_val, dt)
            else:
                ir_val = v.ir_val
        elif isinstance(src, Type.IntType) and isinstance(dst, Type.FloatType):
            ir_val = (
                self.__builder.sitofp(v.ir_val, dt)
                if src.signed
                else self.__builder.uitofp(v.ir_val, dt)
            )
        elif isinstance(src, Type.FloatType) and isinstance(dst, Type.IntType):
            ir_val = (
                self.__builder.fptosi(v.ir_val, dt)
                if dst.signed
                else self.__builder.fptoui(v.ir_val, dt)
            )
        elif isinstance(src, Type.FloatType) and isinstance(dst, Type.FloatType):
            ir_val = (
                self.__builder.fpext(v.ir_val, dt)
                if src.size < dst.size
                else self.__builder.fptrunc(v.ir_val, dt)
            )
        elif isinstance(src, Type.PointerType) and isinstance(dst, Type.PointerType):
            ir_val = self.__builder.bitcast(v.ir_val, dt)
        else:
            raise ValueError(f"Unsupported cast: {type(src).__name__} → {type(dst).__name__}")
        return LLValue(to_type, ir_val)

    # ------------------------------------------------------------------
    # aggregate
    # ------------------------------------------------------------------

    def extract_value(self, base: IR.Value, index: int) -> LLValue:
        resolved = self.resolve(base)
        ir_val = self.__builder.extract_value(resolved.ir_val, index)
        base_type = self.__type_ctx[resolved.type_id]
        if isinstance(base_type, Type.StructType):
            fields = base_type.get_fields(self.__type_ctx)
            field_type = fields[index].type_id
        elif isinstance(base_type, Type.TupleType):
            field_type = base_type.element_types[index]
        else:
            field_type = -1
        return LLValue(field_type, ir_val)

    def insert_value(self, agg: LLValue, value: IR.Value | LLValue, index: int) -> LLValue:
        if isinstance(value, LLValue):
            ir_val = self.__builder.insert_value(agg.ir_val, value.ir_val, index)
        else:
            v = self.resolve(value)
            ir_val = self.__builder.insert_value(agg.ir_val, v.ir_val, index)
        return LLValue(agg.type_id, ir_val)

    # ------------------------------------------------------------------
    # call
    # ------------------------------------------------------------------

    def call(self, callee: LLFunction, args: list[IR.Value]) -> LLValue:
        resolved = [self.resolve(a).ir_val for a in args]
        ir_val = self.__builder.call(callee.__ir, resolved)
        func_type = self.__type_ctx[callee.__ir_type_id] if hasattr(callee, '__ir_type_id') else None
        # Look up return type from the function type
        from compiler.analysis.ty import ty as Type
        callee_ty = None
        # The callee LLFunction doesn't store type_id directly; derive from zero if void
        return LLValue(-1, ir_val)

    def func_ptr(self, func: LLFunction) -> LLValue:
        return LLValue(-1, func.__ir)

    def call_value(self, callee: IR.Value, args: list[IR.Value]) -> LLValue:
        c = self.resolve(callee)
        resolved = [self.resolve(a).ir_val for a in args]
        ir_val = self.__builder.call(c.ir_val, resolved)
        return LLValue(-1, ir_val)

    def call_intrinsic(
        self, kind: IntrinsicKind, args: list[IR.Value | ir.Value | LLValue]
    ) -> ir.Value:
        callee = self.__module.intrinsics.get(kind)
        resolved: list[ir.Value] = []
        for a in args:
            if isinstance(a, LLValue):
                resolved.append(a.ir_val)
            elif isinstance(a, (IR.Reg, IR.IntLiteral)) or not isinstance(a, ir.Value):
                resolved.append(self.resolve(a).ir_val)  # type: ignore[arg-type]
            else:
                resolved.append(a)
        return self.__builder.call(callee, resolved)

    # ------------------------------------------------------------------
    # terminators
    # ------------------------------------------------------------------

    def ret(self, value: IR.Value | None) -> None:
        if value is None:
            self.__builder.ret_void()
        else:
            self.__builder.ret(self.resolve(value).ir_val)

    def br(self, target_label: str) -> None:
        self.__builder.branch(self.__func.block(target_label))

    def condbr(self, cond: IR.Value, then_label: str, else_label: str) -> None:
        self.__builder.cbranch(
            self.resolve(cond).ir_val,
            self.__func.block(then_label),
            self.__func.block(else_label),
        )

    def switch(
        self, value: IR.Value, default_block: ir.Block
    ) -> ir.SwitchInstr:
        return self.__builder.switch(self.resolve(value).ir_val, default_block)

    def add_case(
        self, sw: ir.SwitchInstr, case_value: LLValue, target_label: str
    ) -> None:
        sw.add_case(case_value.ir_val, self.__func.block(target_label))

    def unreachable(self) -> None:
        self.__builder.unreachable()

    # ------------------------------------------------------------------
    # bitcast
    # ------------------------------------------------------------------

    def bitcast(self, value: IR.Value, type_id: int) -> LLValue:
        v = self.resolve(value)
        ir_val = self.__builder.bitcast(v.ir_val, self.__ll_type_ctx.get_ll_type(type_id))
        return LLValue(type_id, ir_val)

    def bitcast_ptr(self, value: ir.Value, ptr_type: ir.PointerType) -> ir.Value:
        return self.__builder.bitcast(value, ptr_type)

    # ------------------------------------------------------------------
    # raw builder access (for edge cases)
    # ------------------------------------------------------------------

    def raw(self) -> ir.IRBuilder:
        return self.__builder

    # ------------------------------------------------------------------
    # internal
    # ------------------------------------------------------------------

    def __cmp_impl(self, op: BinaryOperator, l: ir.Value, r: ir.Value) -> ir.Value:
        pred = {
            BinaryOperator.Eq: "==",
            BinaryOperator.Neq: "!=",
            BinaryOperator.Lt: "<",
            BinaryOperator.Gt: ">",
            BinaryOperator.Leq: "<=",
            BinaryOperator.Geq: ">=",
        }[op]
        if isinstance(l.type, (ir.IntType, ir.PointerType)):
            return self.__builder.icmp_signed(pred, l, r)
        return self.__builder.fcmp_ordered(pred, l, r)

    _ARITH_OPS = {
        BinaryOperator.Add: "add",
        BinaryOperator.Sub: "sub",
        BinaryOperator.Mul: "mul",
        BinaryOperator.Div: "sdiv",
        BinaryOperator.Mod: "srem",
        BinaryOperator.BitAnd: "and_",
        BinaryOperator.BitOr: "or_",
        BinaryOperator.BitXor: "xor",
        BinaryOperator.Shl: "shl",
        BinaryOperator.Shr: "ashr",
    }

    def __arith_impl(self, op: BinaryOperator, l: ir.Value, r: ir.Value) -> ir.Value:
        method_name = self._ARITH_OPS[op]
        return getattr(self.__builder, method_name)(l, r)
