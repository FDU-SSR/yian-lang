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
        """Create a new builder sharing context, positioned at *label*.

        *where* is one of ``"end"``, ``"phi"``, ``"first"``.
        """
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
    # resolve IR.Value → ir.Value
    # ------------------------------------------------------------------

    def resolve(self, v: IR.Value) -> ir.Value:
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
            return self.__module.str_literal_val(v.value.encode("utf-8"))
        raise ValueError(f"Unknown value: {type(v).__name__}")

    # ------------------------------------------------------------------
    # constants
    # ------------------------------------------------------------------

    def _const(self, type_id: int, value: int | float) -> ir.Constant:
        return ir.Constant(self.__ll_type_ctx.get_ll_type(type_id), value)

    def i32(self, v: int) -> ir.Constant:
        return ir.Constant(ir.IntType(32), v)

    def i64(self, v: int) -> ir.Constant:
        return ir.Constant(ir.IntType(64), v)

    def i8_ptr_type(self) -> ir.PointerType:
        return ir.PointerType(ir.IntType(8))

    def undef(self, type_id: int) -> ir.Constant:
        return ir.Constant(self.__ll_type_ctx.get_ll_type(type_id), ir.Undefined)

    def sizeof_const(self, type_id: int) -> ir.Constant:
        return ir.Constant(ir.IntType(64), self.__ll_type_ctx.get_type_size(type_id))

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
        """Position the internal builder at *label*.

        *where*:
          ``"end"``   — after all instructions (default)
          ``"phi"``   — at block start, before any instruction (for phi nodes)
          ``"first"`` — before the first instruction (for entry allocas)
        """
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

    def phi(self, type_id: int, name: str) -> ir.PhiInstr:
        p = self.__builder.phi(self.__ll_type_ctx.get_ll_type(type_id), name=name)
        self.__func.set_reg(name, p)
        return p

    def add_incoming(self, phi: ir.PhiInstr, value: IR.Value, src_label: str) -> None:
        phi.add_incoming(self.resolve(value), self.__func.block(src_label))

    # ------------------------------------------------------------------
    # memory
    # ------------------------------------------------------------------

    def alloca(self, type_id: int, name: str = "") -> ir.AllocaInstr:
        return self.__builder.alloca(self.__ll_type_ctx.get_ll_type(type_id), name=name)

    def load(self, ptr: IR.Value, name: str = "") -> ir.Value:
        return self.__builder.load(self.resolve(ptr), name=name)

    def store(self, value: IR.Value, ptr: IR.Value) -> None:
        self.__builder.store(self.resolve(value), self.resolve(ptr))

    def gep(
        self, base: IR.Value, indices: list[int], inbounds: bool = True, name: str = ""
    ) -> ir.Value:
        idx_vals = [self.i32(i) for i in indices]
        return self.__builder.gep(self.resolve(base), idx_vals, inbounds=inbounds, name=name)

    # raw-value gep for unpack patterns
    def gep_raw(
        self, base: ir.Value, indices: list[int], inbounds: bool = True, name: str = ""
    ) -> ir.Value:
        idx_vals = [self.i32(i) for i in indices]
        return self.__builder.gep(base, idx_vals, inbounds=inbounds, name=name)

    def load_raw(self, ptr: ir.Value, name: str = "") -> ir.Value:
        return self.__builder.load(ptr, name=name)

    def store_raw(self, value: ir.Value, ptr: ir.Value) -> None:
        self.__builder.store(value, ptr)

    # ------------------------------------------------------------------
    # arithmetic / comparison
    # ------------------------------------------------------------------

    def binary(self, op: BinaryOperator, lhs: IR.Value, rhs: IR.Value, name: str) -> ir.Value:
        l, r = self.resolve(lhs), self.resolve(rhs)
        if op.is_comparison():
            return self.__cmp_impl(op, l, r)
        return self.__arith_impl(op, l, r, name)

    def unary(self, op: UnaryOperator, operand: IR.Value, name: str) -> ir.Value:
        v = self.resolve(operand)
        if op == UnaryOperator.Neg:
            return self.__builder.neg(v, name=name)
        if op == UnaryOperator.Not:
            return self.__builder.not_(v, name=name)
        if op == UnaryOperator.BitNot:
            return self.__builder.xor(v, ir.Constant(v.type, -1), name=name)
        raise ValueError(f"Unsupported unary: {op}")

    # ------------------------------------------------------------------
    # cast
    # ------------------------------------------------------------------

    def cast(self, value: IR.Value, to_type: int, name: str) -> ir.Value:
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
                    return self.__builder.zext(v, dt, name=name)
                return self.__builder.sext(v, dt, name=name)
            if dw < sw:
                return self.__builder.trunc(v, dt, name=name)
            return v
        if isinstance(src, Type.IntType) and isinstance(dst, Type.FloatType):
            return (
                self.__builder.sitofp(v, dt, name=name)
                if src.signed
                else self.__builder.uitofp(v, dt, name=name)
            )
        if isinstance(src, Type.FloatType) and isinstance(dst, Type.IntType):
            return (
                self.__builder.fptosi(v, dt, name=name)
                if dst.signed
                else self.__builder.fptoui(v, dt, name=name)
            )
        if isinstance(src, Type.FloatType) and isinstance(dst, Type.FloatType):
            return (
                self.__builder.fpext(v, dt, name=name)
                if src.size < dst.size
                else self.__builder.fptrunc(v, dt, name=name)
            )
        if isinstance(src, Type.PointerType) and isinstance(dst, Type.PointerType):
            return self.__builder.bitcast(v, dt, name=name)
        raise ValueError(f"Unsupported cast: {type(src).__name__} → {type(dst).__name__}")

    # ------------------------------------------------------------------
    # aggregate
    # ------------------------------------------------------------------

    def extract_value(self, base: IR.Value, index: int, name: str = "") -> ir.Value:
        return self.__builder.extract_value(self.resolve(base), index, name=name)

    def insert_value(self, agg: ir.Value, value: IR.Value, index: int) -> ir.Value:
        return self.__builder.insert_value(agg, self.resolve(value), index)

    # ------------------------------------------------------------------
    # call
    # ------------------------------------------------------------------

    def call(self, callee: ir.Function, args: list[IR.Value], name: str = "") -> ir.Value:
        return self.__builder.call(callee, [self.resolve(a) for a in args], name=name)

    def call_value(self, callee: IR.Value, args: list[IR.Value], name: str = "") -> ir.Value:
        return self.__builder.call(self.resolve(callee), [self.resolve(a) for a in args], name=name)

    def call_intrinsic(
        self, kind: IntrinsicKind, args: list[IR.Value | ir.Value], name: str = ""
    ) -> ir.Value:
        callee = self.__module.intrinsics.get(kind)
        resolved: list[ir.Value] = []
        for a in args:
            if isinstance(a, IR.Reg) or isinstance(a, IR.IntLiteral) or not isinstance(a, ir.Value):
                resolved.append(self.resolve(a))  # type: ignore[arg-type]
            else:
                resolved.append(a)
        return self.__builder.call(callee, resolved, name=name)

    # ------------------------------------------------------------------
    # terminators
    # ------------------------------------------------------------------

    def ret(self, value: IR.Value | None) -> None:
        if value is None:
            self.__builder.ret_void()
        else:
            self.__builder.ret(self.resolve(value))

    def br(self, target_label: str) -> None:
        self.__builder.branch(self.__func.block(target_label))

    def condbr(self, cond: IR.Value, then_label: str, else_label: str) -> None:
        self.__builder.cbranch(
            self.resolve(cond),
            self.__func.block(then_label),
            self.__func.block(else_label),
        )

    def switch(
        self, value: IR.Value, default_block: ir.Block
    ) -> ir.SwitchInstr:
        return self.__builder.switch(self.resolve(value), default_block)

    def add_case(
        self, sw: ir.SwitchInstr, case_value: ir.Constant, target_label: str
    ) -> None:
        sw.add_case(case_value, self.__func.block(target_label))

    def unreachable(self) -> None:
        self.__builder.unreachable()

    # ------------------------------------------------------------------
    # bitcast
    # ------------------------------------------------------------------

    def bitcast(self, value: IR.Value | ir.Value, type_id: int, name: str = "") -> ir.Value:
        v = value if isinstance(value, ir.Value) else self.resolve(value)
        return self.__builder.bitcast(v, self.__ll_type_ctx.get_ll_type(type_id), name=name)

    def bitcast_ptr(self, value: ir.Value, ptr_type: ir.PointerType, name: str = "") -> ir.Value:
        return self.__builder.bitcast(value, ptr_type, name=name)

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
            return self.__builder.icmp_signed(pred, l, r, name="cmp")  # type: ignore[arg-type]
        return self.__builder.fcmp_ordered(pred, l, r, name="cmp")  # type: ignore[arg-type]

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

    def __arith_impl(self, op: BinaryOperator, l: ir.Value, r: ir.Value, name: str) -> ir.Value:
        method_name = self._ARITH_OPS[op]
        return getattr(self.__builder, method_name)(l, r, name=name)
