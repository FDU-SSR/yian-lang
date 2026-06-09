"""
LLBuilder — wraps llvmlite ``ir.IRBuilder`` to hide LLVM-level details.
"""

from __future__ import annotations

from enum import Enum, auto

from llvmlite import ir  # type: ignore[import-untyped]

from compiler.analysis.ty import ty as Type
from compiler.analysis.ty.context import TypeCtx
from compiler.codegen.cfg import ir as IR
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

    def __init__(
        self, func: LLFunction, module: LLModule,
        ll_type_ctx: LLTypeCtx, type_ctx: TypeCtx,
    ) -> None:
        self.__func = func
        self.__module = module
        self.__ll_type_ctx = ll_type_ctx
        self.__type_ctx = type_ctx
        self.__builder: ir.IRBuilder

    # ------------------------------------------------------------------
    # fork
    # ------------------------------------------------------------------

    def fork(self, label: str, where: BuilderPosition = BuilderPosition.End) -> LLBuilder:
        forked = object.__new__(LLBuilder)
        forked.__func = self.__func
        forked.__module = self.__module
        forked.__ll_type_ctx = self.__ll_type_ctx
        forked.__type_ctx = self.__type_ctx
        forked.position_at(label, where)
        return forked

    def fork_block(self, block: ir.Block, where: BuilderPosition = BuilderPosition.End) -> LLBuilder:
        forked = object.__new__(LLBuilder)
        forked.__func = self.__func
        forked.__module = self.__module
        forked.__ll_type_ctx = self.__ll_type_ctx
        forked.__type_ctx = self.__type_ctx
        forked.__builder = ir.IRBuilder(block)
        if where == BuilderPosition.First:
            ins = list(block.instructions)
            if ins:
                forked.__builder.position_before(ins[0])
        elif where == BuilderPosition.Phi:
            forked.__builder.position_at_start(block)
        return forked

    # ------------------------------------------------------------------
    # resolve
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
            return LLValue(v.type_id, self.__module.str_literal_val(v.value.encode("utf-8")))
        raise ValueError(f"Unknown value: {type(v).__name__}")

    # ------------------------------------------------------------------
    # constants
    # ------------------------------------------------------------------

    def _const(self, type_id: int, value: int | float) -> LLValue:
        return LLValue(type_id, ir.Constant(self.__ll_type_ctx.get_ll_type(type_id), value))

    def i32(self, v: int) -> LLValue:
        return LLValue(-1, ir.Constant(ir.IntType(32), v))

    def i64(self, v: int) -> LLValue:
        return LLValue(-1, ir.Constant(ir.IntType(64), v))

    def i8_ptr_type(self) -> ir.PointerType:
        return ir.PointerType(ir.IntType(8))

    def undef(self, type_id: int) -> LLValue:
        return LLValue(type_id, ir.Constant(self.__ll_type_ctx.get_ll_type(type_id), ir.Undefined))

    def sizeof_const(self, type_id: int, result: str) -> None:
        self.__func.set_reg(result, LLValue(-1, ir.Constant(ir.IntType(64), self.__ll_type_ctx.get_type_size(type_id))))

    # ------------------------------------------------------------------
    # builder position
    # ------------------------------------------------------------------

    def position_at(self, label: str, where: BuilderPosition = BuilderPosition.End) -> None:
        bb = self.__func.block(label)
        self.__builder = ir.IRBuilder(bb)
        if where == BuilderPosition.Phi:
            self.__builder.position_at_start(bb)
        elif where == BuilderPosition.First:
            ins = list(bb.instructions)
            if ins:
                self.__builder.position_before(ins[0])

    # ------------------------------------------------------------------
    # statements (each maps to one __translate case)
    # ------------------------------------------------------------------

    def var_ptr(self, symbol_id: int, result: str) -> None:
        a = self.__func.get_var_ptr(symbol_id)
        self.__func.set_reg(result, a)

    def alloca(self, type_id: int) -> LLValue:
        return LLValue(type_id, self.__builder.alloca(self.__ll_type_ctx.get_ll_type(type_id)))

    def alloca_store(self, value: IR.Value, result: str) -> None:
        a = self.alloca(value.type_id)
        self.store(value, a)
        self.__func.set_reg(result, a)

    def malloc(self, type_id: int, size: IR.Value, result: str) -> None:
        raw = self._call_intrinsic(IntrinsicKind.Malloc, [size])
        ptr_type_id = self.__type_ctx.alloc_pointer(type_id)
        ptr_t = self.__ll_type_ctx.get_ll_type(ptr_type_id)
        ir_val = self.__builder.bitcast(raw, ptr_t)
        self.__func.set_reg(result, LLValue(ptr_type_id, ir_val))

    def delete(self, ptr: IR.Value) -> None:
        resolved = self.resolve(ptr)
        casted = self.__builder.bitcast(resolved.ir_val, ir.PointerType(ir.IntType(8)))
        self._call_intrinsic(IntrinsicKind.Free, [casted])

    # -- memory --

    def load(self, ptr: IR.Value, result: str) -> None:
        resolved = self.resolve(ptr)
        ptr_type = self.__type_ctx[resolved.type_id]
        assert isinstance(ptr_type, Type.PointerType)
        ir_val = self.__builder.load(resolved.ir_val)
        self.__func.set_reg(result, LLValue(ptr_type.pointee_type, ir_val))

    def store(self, value: IR.Value, ptr: IR.Value | LLValue) -> None:
        v = self.resolve(value)
        if isinstance(ptr, LLValue):
            self.__builder.store(v.ir_val, ptr.ir_val)
        else:
            p = self.resolve(ptr)
            self.__builder.store(v.ir_val, p.ir_val)

    def gep(self, base: IR.Value, indices: list[int], result: str) -> None:
        resolved = self.resolve(base)
        base_type = self.__type_ctx[resolved.type_id]
        if isinstance(base_type, Type.PointerType):
            result_type = base_type.pointee_type
        else:
            result_type = resolved.type_id
        idx_vals = [self.i32(i).ir_val for i in indices]
        ir_val = self.__builder.gep(resolved.ir_val, idx_vals, inbounds=True)
        self.__func.set_reg(result, LLValue(result_type, ir_val))

    # -- arithmetic / comparison --

    def binary(self, op: BinaryOperator, lhs: IR.Value, rhs: IR.Value, result: str) -> None:
        l, r = self.resolve(lhs), self.resolve(rhs)
        if op.is_comparison():
            ir_val = self.__cmp_impl(op, l.ir_val, r.ir_val)
            self.__func.set_reg(result, LLValue(-1, ir_val))
        else:
            ir_val = self.__arith_impl(op, l.ir_val, r.ir_val)
            self.__func.set_reg(result, LLValue(l.type_id, ir_val))

    def unary(self, op: UnaryOperator, operand: IR.Value, result: str) -> None:
        v = self.resolve(operand)
        if op == UnaryOperator.Neg:
            ir_val = self.__builder.neg(v.ir_val)
        elif op == UnaryOperator.Not:
            ir_val = self.__builder.not_(v.ir_val)
        elif op == UnaryOperator.BitNot:
            ir_val = self.__builder.xor(v.ir_val, ir.Constant(v.ir_val.type, -1))
        else:
            raise ValueError(f"Unsupported unary: {op}")
        self.__func.set_reg(result, LLValue(v.type_id, ir_val))

    # -- cast --

    def cast(self, value: IR.Value, to_type: int, result: str) -> None:
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
                if isinstance(src, Type.CharType) or (isinstance(src, Type.IntType) and not src.signed):
                    ir_val = self.__builder.zext(v.ir_val, dt)
                else:
                    ir_val = self.__builder.sext(v.ir_val, dt)
            elif dw < sw:
                ir_val = self.__builder.trunc(v.ir_val, dt)
            else:
                ir_val = v.ir_val
        elif isinstance(src, Type.IntType) and isinstance(dst, Type.FloatType):
            ir_val = self.__builder.sitofp(v.ir_val, dt) if src.signed else self.__builder.uitofp(v.ir_val, dt)
        elif isinstance(src, Type.FloatType) and isinstance(dst, Type.IntType):
            ir_val = self.__builder.fptosi(v.ir_val, dt) if dst.signed else self.__builder.fptoui(v.ir_val, dt)
        elif isinstance(src, Type.FloatType) and isinstance(dst, Type.FloatType):
            ir_val = self.__builder.fpext(v.ir_val, dt) if src.size < dst.size else self.__builder.fptrunc(v.ir_val, dt)
        elif isinstance(src, Type.PointerType) and isinstance(dst, Type.PointerType):
            ir_val = self.__builder.bitcast(v.ir_val, dt)
        else:
            raise ValueError(f"Unsupported cast: {type(src).__name__} → {type(dst).__name__}")
        self.__func.set_reg(result, LLValue(to_type, ir_val))

    # -- aggregate --

    def extract_value(self, base: IR.Value, index: int, result: str) -> None:
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
        self.__func.set_reg(result, LLValue(field_type, ir_val))

    def insert_value(self, agg: LLValue, value: IR.Value | LLValue, index: int) -> LLValue:
        if isinstance(value, LLValue):
            ir_val = self.__builder.insert_value(agg.ir_val, value.ir_val, index)
        else:
            v = self.resolve(value)
            ir_val = self.__builder.insert_value(agg.ir_val, v.ir_val, index)
        return LLValue(agg.type_id, ir_val)

    # -- call --

    def call(self, callee: LLFunction, args: list[IR.Value], result: str) -> None:
        resolved = [self.resolve(a).ir_val for a in args]
        ir_val = self.__builder.call(callee.__ir, resolved)
        self.__func.set_reg(result, LLValue(-1, ir_val))

    def call_func(self, callee_type: int, args: list[IR.Value], result: str) -> None:
        callee = self.__module.get_func(callee_type)
        assert callee is not None, f"Function callee_type={callee_type} not declared"
        self.call(callee, args, result)

    def call_value(self, callee: IR.Value, args: list[IR.Value], result: str) -> None:
        c = self.resolve(callee)
        resolved = [self.resolve(a).ir_val for a in args]
        ir_val = self.__builder.call(c.ir_val, resolved)
        self.__func.set_reg(result, LLValue(-1, ir_val))

    def func_ptr(self, func: LLFunction) -> LLValue:
        return LLValue(-1, func.__ir)

    def func_ptr_by_type(self, func_type_id: int, result: str) -> None:
        callee = self.__module.get_func(func_type_id)
        assert callee is not None, f"FuncPtr func_type_id={func_type_id} not declared"
        self.__func.set_reg(result, self.func_ptr(callee))

    # -- phi --

    def phi(self, type_id: int, incoming: list[tuple[IR.Block, IR.Value]], result: str) -> None:
        p = self.__builder.phi(self.__ll_type_ctx.get_ll_type(type_id))
        self.__func.set_reg(result, LLValue(type_id, p))
        for src, val in incoming:
            p.add_incoming(self.resolve(val).ir_val, self.__func.block(src.label))

    # -- aggregate construct --

    def aggregate(self, type_id: int, fields: list[IR.Value], result: str) -> None:
        r = self.undef(type_id)
        for i, fv in enumerate(fields):
            r = self.insert_value(r, fv, i)
        self.__func.set_reg(result, r)

    def array(self, type_id: int, elements: list[IR.Value], result: str) -> None:
        r = self.undef(type_id)
        for i, ev in enumerate(elements):
            r = self.insert_value(r, ev, i)
        self.__func.set_reg(result, r)

    def variant(self, stmt: IR.VariantConstruct, result: str) -> None:
        r = self.undef(stmt.result.type_id)
        r = self.insert_value(r, self.i32(stmt.variant.discriminant), 0)
        if stmt.payload_fields is not None:
            pt = self.__type_ctx[stmt.variant.payload_type]
            assert isinstance(pt, Type.StructType)
            pv = self.undef(stmt.variant.payload_type)
            for i, fv in enumerate(stmt.payload_fields):
                pv = self.insert_value(pv, fv, i)
            pay_arr = ir.ArrayType(ir.IntType(8), self.__ll_type_ctx.get_type_size(stmt.variant.payload_type))
            r = self.insert_value(r, LLValue(-1, self.__builder.bitcast(pv.ir_val, pay_arr)), 1)
        self.__func.set_reg(result, r)

    # -- sys --

    def sys_write(self, fd: IR.Value, buf: IR.Value) -> None:
        self._call_intrinsic(IntrinsicKind.Write, [
            fd, self._extract_value_raw(buf, 0), self._extract_value_raw(buf, 1),
        ])

    def sys_read(self, fd: IR.Value, buf: IR.Value, result: str) -> None:
        raw = self._call_intrinsic(IntrinsicKind.Read, [
            fd, self._extract_value_raw(buf, 0), self._extract_value_raw(buf, 1),
        ])
        self.__func.set_reg(result, LLValue(-1, raw))

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

    def switch(self, value: IR.Value, default_block: ir.Block) -> ir.SwitchInstr:
        return self.__builder.switch(self.resolve(value).ir_val, default_block)

    def add_case(self, sw: ir.SwitchInstr, case_value: LLValue, target_label: str) -> None:
        sw.add_case(case_value.ir_val, self.__func.block(target_label))

    def unreachable(self) -> None:
        self.__builder.unreachable()

    def panic(self, msg: IR.Value) -> None:
        self._call_intrinsic(IntrinsicKind.Write, [
            self.i32(2), self._extract_value_raw(msg, 0), self._extract_value_raw(msg, 1),
        ])
        self._call_intrinsic(IntrinsicKind.Exit, [self.i32(1)])
        self.__builder.unreachable()

    def match(self, t: IR.Match) -> None:
        mv = self.resolve(t.value)
        mty = self.__type_ctx[t.value.type_id]
        default = self.__func.block(t.default.label) if t.default else None
        if default is None:
            default = self.__func.new_block("match.unreach")
            ir.IRBuilder(default).unreachable()

        if isinstance(mty, (Type.IntType, Type.CharType, Type.BoolType)):
            sw = self.switch(t.value, default)
            for arm in t.arms:
                if isinstance(arm.pattern, IR.IntPattern):
                    self.add_case(sw, self.resolve(arm.pattern.value), arm.body.label)
                elif isinstance(arm.pattern, IR.CharPattern):
                    self.add_case(sw, self.resolve(arm.pattern.value), arm.body.label)

        elif isinstance(mty, Type.EnumType):
            disc = self.__builder.load(
                self.__builder.gep(mv.ir_val, [self.i32(0).ir_val, self.i32(0).ir_val], inbounds=True)
            )
            sw = self.__builder.switch(disc, default)
            for arm in t.arms:
                if isinstance(arm.pattern, IR.EnumPattern):
                    bb = self.__func.block(arm.body.label)
                    sw.add_case(self.i32(arm.pattern.variant.discriminant).ir_val, bb)
                    if arm.pattern.fields and arm.pattern.variant.payload_type is not None:
                        self._unpack(bb, mv.ir_val, arm.pattern)

    # ------------------------------------------------------------------
    # raw helpers
    # ------------------------------------------------------------------

    def _extract_value_raw(self, base: IR.Value, index: int) -> ir.Value:
        return self.__builder.extract_value(self.resolve(base).ir_val, index)

    def _call_intrinsic(self, kind: IntrinsicKind, args: list[IR.Value | ir.Value | LLValue]) -> ir.Value:
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

    def raw(self) -> ir.IRBuilder:
        return self.__builder

    # ------------------------------------------------------------------
    # unpack (enum payload extraction)
    # ------------------------------------------------------------------

    def _unpack(self, bb: ir.Block, mv: ir.Value, pat: IR.EnumPattern) -> None:
        pt = self.__type_ctx[pat.variant.payload_type]
        assert isinstance(pt, Type.StructType)
        pfields = pt.get_fields(self.__type_ctx)

        cb = ir.IRBuilder(bb)
        ins = list(bb.instructions)
        if ins:
            cb.position_before(ins[0])

        payload = cb.bitcast(
            cb.gep(mv, [self.i32(0).ir_val, self.i32(1).ir_val], inbounds=True),
            ir.PointerType(self.__ll_type_ctx.get_ll_type(pat.variant.payload_type)),
        )

        for i, f in enumerate(pat.fields):
            if i >= len(pfields):
                break
            fv = cb.load(cb.gep(payload, [self.i32(0).ir_val, self.i32(i).ir_val], inbounds=True))
            a = self.__func.get_var_ptr(f.symbol_id)
            if a is not None:
                cb.store(fv, a.ir_val)

    # ------------------------------------------------------------------
    # internal
    # ------------------------------------------------------------------

    def __cmp_impl(self, op: BinaryOperator, l: ir.Value, r: ir.Value) -> ir.Value:
        pred = {
            BinaryOperator.Eq: "==", BinaryOperator.Neq: "!=",
            BinaryOperator.Lt: "<", BinaryOperator.Gt: ">",
            BinaryOperator.Leq: "<=", BinaryOperator.Geq: ">=",
        }[op]
        if isinstance(l.type, (ir.IntType, ir.PointerType)):
            return self.__builder.icmp_signed(pred, l, r)
        return self.__builder.fcmp_ordered(pred, l, r)

    _ARITH_OPS = {
        BinaryOperator.Add: "add", BinaryOperator.Sub: "sub",
        BinaryOperator.Mul: "mul", BinaryOperator.Div: "sdiv",
        BinaryOperator.Mod: "srem", BinaryOperator.BitAnd: "and_",
        BinaryOperator.BitOr: "or_", BinaryOperator.BitXor: "xor",
        BinaryOperator.Shl: "shl", BinaryOperator.Shr: "ashr",
    }

    def __arith_impl(self, op: BinaryOperator, l: ir.Value, r: ir.Value) -> ir.Value:
        return getattr(self.__builder, self._ARITH_OPS[op])(l, r)
