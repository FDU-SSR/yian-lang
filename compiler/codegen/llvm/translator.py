"""
CFG → LLVM IR translator.
"""

from __future__ import annotations

from llvmlite import ir

from compiler.analysis.ty import ty as Type
from compiler.analysis.ty.context import TypeCtx
from compiler.codegen.cfg import ir as IR
from compiler.codegen.llvm.intrinsics import IntrinsicKind
from compiler.codegen.llvm.module import LLFunction, LLModule
from compiler.frontend.parse.operator import BinaryOperator, UnaryOperator


class LLTranslator:
    """CFG Functions → LLVM Module."""

    def __init__(self, type_ctx: TypeCtx, module: LLModule) -> None:
        self.__type_ctx = type_ctx
        self.__module = module

        # per-function state (reset in __build)
        self.__func: LLFunction | None = None
        self.__blocks: dict[str, ir.Block] = {}
        self.__regs: dict[str, ir.Value] = {}

    # ------------------------------------------------------------------
    # public
    # ------------------------------------------------------------------

    def run(self, functions: dict[str, IR.Function]) -> None:
        for f in functions.values():
            self.__module.declare(f)
        for f in functions.values():
            self.__build(f)

    def export(self) -> LLModule:
        return self.__module

    # ------------------------------------------------------------------
    # per-function build
    # ------------------------------------------------------------------

    def __build(self, cfg: IR.Function) -> None:
        func = self.__module.get_func(cfg.type_id)
        assert func is not None
        self.__func = func
        self.__blocks = {}
        self.__regs = {}
        f = func._ir
        tm = self.__module.type_mapper

        # ── entry block + allocas ──
        entry_bb = f.append_basic_block(".entry")
        func.entry_block = entry_bb
        eb = ir.IRBuilder(entry_bb)
        for symbol_id, vr in cfg.local_vars.items():
            func.var_allocas[symbol_id] = eb.alloca(tm.get_ll_type(vr.type_id), name=vr.name)

        for i, symbol_id in enumerate(cfg.params):
            if i < len(f.args):
                a = func.var_allocas.get(symbol_id)
                if a is not None:
                    eb.store(f.args[i], a)

        # ── create blocks ──
        for blk in cfg.blocks:
            if blk.label == cfg.entry.label:
                self.__blocks[blk.label] = entry_bb
            else:
                self.__blocks[blk.label] = f.append_basic_block(blk.label)

        # ── translate blocks ──
        for blk in cfg.blocks:
            bb = self.__blocks[blk.label]
            b = ir.IRBuilder(bb)

            # phi nodes
            if blk.phis:
                b.position_at_start(bb)
                for ps in blk.phis:
                    self.__h_phi_impl(b, ps)

            # regular statements + terminator
            b.position_at_end(bb)
            for s in blk.stmts:
                self.__translate(b, s)

            assert blk.terminator is not None
            self.__term(b, blk.terminator)

    # ------------------------------------------------------------------
    # dispatch
    # ------------------------------------------------------------------

    def __translate(self, builder: ir.IRBuilder, stmt: IR.Stmt) -> None:
        match stmt:
            case IR.VarPtr():
                res = self.__h_varptr(builder, stmt)
                self.__regs[stmt.result.name] = res
            case IR.Alloca():
                res = self.__h_alloca(builder, stmt)
                self.__regs[stmt.result.name] = res
            case IR.FieldPtr():
                res = self.__h_fieldptr(builder, stmt)
                self.__regs[stmt.result.name] = res
            case IR.Load():
                res = self.__h_load(builder, stmt)
                self.__regs[stmt.result.name] = res
            case IR.Store():
                self.__h_store(builder, stmt)
            case IR.Malloc():
                res = self.__h_malloc(builder, stmt)
                self.__regs[stmt.result.name] = res
            case IR.Binary():
                res = self.__h_binary(builder, stmt)
                self.__regs[stmt.result.name] = res
            case IR.Unary():
                res = self.__h_unary(builder, stmt)
                self.__regs[stmt.result.name] = res
            case IR.ExtractValue():
                res = self.__h_extractvalue(builder, stmt)
                self.__regs[stmt.result.name] = res
            case IR.Delete():
                self.__h_delete(builder, stmt)
            case IR.Call():
                res = self.__h_call(builder, stmt)
                self.__regs[stmt.result.name] = res
            case IR.Invoke():
                res = self.__h_invoke(builder, stmt)
                self.__regs[stmt.result.name] = res
            case IR.Cast():
                res = self.__h_cast(builder, stmt)
                self.__regs[stmt.result.name] = res
            case IR.SizeOf():
                res = self.__h_sizeof(builder, stmt)
                self.__regs[stmt.result.name] = res
            case IR.FuncPtr():
                res = self.__h_funcptr(builder, stmt)
                self.__regs[stmt.result.name] = res
            case IR.AggregateConstruct():
                res = self.__h_aggregate(builder, stmt)
                self.__regs[stmt.result.name] = res
            case IR.ArrayConstruct():
                res = self.__h_array(builder, stmt)
                self.__regs[stmt.result.name] = res
            case IR.VariantConstruct():
                res = self.__h_variant(builder, stmt)
                self.__regs[stmt.result.name] = res
            case IR.SysWrite():
                self.__h_syswrite(builder, stmt)
            case IR.SysRead():
                res = self.__h_sysread(builder, stmt)
                self.__regs[stmt.result.name] = res

    def __val(self, v: IR.Value) -> ir.Value:
        if isinstance(v, IR.Reg):
            return self.__regs[v.name]
        if isinstance(v, IR.IntLiteral):
            return ir.Constant(self.__module.type_mapper.get_ll_type(v.type_id), v.value)
        if isinstance(v, IR.FloatLiteral):
            return ir.Constant(self.__module.type_mapper.get_ll_type(v.type_id), v.value)
        if isinstance(v, IR.BoolLiteral):
            return ir.Constant(self.__module.type_mapper.get_ll_type(v.type_id), 1 if v.value else 0)
        if isinstance(v, IR.CharLiteral):
            return ir.Constant(self.__module.type_mapper.get_ll_type(v.type_id), ord(v.value))
        if isinstance(v, IR.StringLiteral):
            return self.__module.str_literal_val(v.value.encode("utf-8"))

    # ------------------------------------------------------------------
    # stmt handlers
    # ------------------------------------------------------------------

    def __h_varptr(self, b: ir.IRBuilder, s: IR.VarPtr) -> ir.Value:
        assert self.__func is not None
        a = self.__func.var_allocas.get(s.var_ref.symbol_id)
        assert a is not None
        return a

    def __h_alloca(self, b: ir.IRBuilder, s: IR.Alloca) -> ir.Value:
        assert self.__func is not None
        eb = ir.IRBuilder(self.__func.entry_block)
        ins = list(self.__func.entry_block.instructions)  # type: ignore[union-attr]
        if ins:
            eb.position_before(ins[0])
        a = eb.alloca(self.__module.type_mapper.get_ll_type(s.value.type_id), name=f"tmp.{s.result.name}")
        b.store(self.__val(s.value), a)
        return a

    def __h_fieldptr(self, b: ir.IRBuilder, s: IR.FieldPtr) -> ir.Value:
        return b.gep(self.__val(s.base), [ir.Constant(ir.IntType(32), 0), ir.Constant(ir.IntType(32), s.field_index)], inbounds=True)

    def __h_load(self, b: ir.IRBuilder, s: IR.Load) -> ir.Value:
        return b.load(self.__val(s.ptr), name=f"load.{s.result.name}")

    def __h_store(self, b: ir.IRBuilder, s: IR.Store) -> None:
        b.store(self.__val(s.value), self.__val(s.ptr))

    def __h_malloc(self, b: ir.IRBuilder, s: IR.Malloc) -> ir.Value:
        malloc = self.__module.intrinsics.get(IntrinsicKind.Malloc)
        raw = b.call(malloc, [self.__val(s.size)], name="malloc.raw")
        ptr_t = self.__module.type_mapper.get_ll_type(self.__type_ctx.alloc_pointer(s.type_id))
        return b.bitcast(raw, ptr_t, name=s.result.name)

    def __h_binary(self, b: ir.IRBuilder, s: IR.Binary) -> ir.Value:
        l, r = self.__val(s.lhs), self.__val(s.rhs)
        if s.op.is_comparison():
            return self.__cmp(b, s.op, l, r)
        return self.__arith(b, s.op, l, r, s.result.name)

    def __h_unary(self, b: ir.IRBuilder, s: IR.Unary) -> ir.Value:
        v = self.__val(s.operand)
        if s.op == UnaryOperator.Neg:
            return b.neg(v, name=s.result.name)
        if s.op == UnaryOperator.Not:
            return b.not_(v, name=s.result.name)
        if s.op == UnaryOperator.BitNot:
            return b.xor(v, ir.Constant(v.type, -1), name=s.result.name)  # type: ignore[union-attr]
        raise ValueError(f"Unsupported unary: {s.op}")

    def __h_extractvalue(self, b: ir.IRBuilder, s: IR.ExtractValue) -> ir.Value:
        return b.extract_value(self.__val(s.base), s.field_index, name=s.result.name)

    def __h_delete(self, b: ir.IRBuilder, s: IR.Delete) -> None:
        free = self.__module.intrinsics.get(IntrinsicKind.Free)
        b.call(free, [b.bitcast(self.__val(s.ptr), ir.PointerType(ir.IntType(8)))])

    def __h_call(self, b: ir.IRBuilder, s: IR.Call) -> ir.Value:
        callee = self.__module.get_func(s.callee_type)
        assert callee is not None, f"Function callee_type={s.callee_type} not declared"
        return b.call(callee._ir, [self.__val(a) for a in s.args], name=s.result.name)

    def __h_invoke(self, b: ir.IRBuilder, s: IR.Invoke) -> ir.Value:
        return b.call(self.__val(s.callee), [self.__val(a) for a in s.args], name=s.result.name)

    def __h_cast(self, b: ir.IRBuilder, s: IR.Cast) -> ir.Value:
        v = self.__val(s.value)
        src = self.__type_ctx[s.value.type_id]
        dst = self.__type_ctx[s.to_type]
        dt = self.__module.type_mapper.get_ll_type(s.to_type)

        if isinstance(src, (Type.IntType, Type.CharType, Type.BoolType)) and isinstance(dst, (Type.IntType, Type.CharType, Type.BoolType)):
            sw = self.__module.type_mapper.get_ll_type(s.value.type_id).width  # type: ignore[union-attr]
            dw = dt.width  # type: ignore[union-attr]
            if dw > sw:
                if isinstance(src, Type.CharType) or (isinstance(src, Type.IntType) and not src.signed):
                    return b.zext(v, dt, name=s.result.name)
                return b.sext(v, dt, name=s.result.name)
            if dw < sw:
                return b.trunc(v, dt, name=s.result.name)
            return v
        if isinstance(src, Type.IntType) and isinstance(dst, Type.FloatType):
            return b.sitofp(v, dt, name=s.result.name) if src.signed else b.uitofp(v, dt, name=s.result.name)
        if isinstance(src, Type.FloatType) and isinstance(dst, Type.IntType):
            return b.fptosi(v, dt, name=s.result.name) if dst.signed else b.fptoui(v, dt, name=s.result.name)
        if isinstance(src, Type.FloatType) and isinstance(dst, Type.FloatType):
            return b.fpext(v, dt, name=s.result.name) if src.size < dst.size else b.fptrunc(v, dt, name=s.result.name)
        if isinstance(src, Type.PointerType) and isinstance(dst, Type.PointerType):
            return b.bitcast(v, dt, name=s.result.name)
        raise ValueError(f"Unsupported cast: {type(src).__name__} → {type(dst).__name__}")

    def __h_sizeof(self, b: ir.IRBuilder, s: IR.SizeOf) -> ir.Value:
        return ir.Constant(ir.IntType(64), self.__module.type_mapper.get_type_size(s.type_id))

    def __h_funcptr(self, b: ir.IRBuilder, s: IR.FuncPtr) -> ir.Value:
        callee = self.__module.get_func(s.func_type_id)
        assert callee is not None, f"FuncPtr func_type_id={s.func_type_id} not declared"
        return callee._ir

    def __h_aggregate(self, b: ir.IRBuilder, s: IR.AggregateConstruct) -> ir.Value:
        r = ir.Constant(self.__module.type_mapper.get_ll_type(s.result.type_id), ir.Undefined)
        for i, fv in enumerate(s.fields):
            r = b.insert_value(r, self.__val(fv), i)
        return r

    def __h_array(self, b: ir.IRBuilder, s: IR.ArrayConstruct) -> ir.Value:
        r = ir.Constant(self.__module.type_mapper.get_ll_type(s.result.type_id), ir.Undefined)
        for i, ev in enumerate(s.elements):
            r = b.insert_value(r, self.__val(ev), i)
        return r

    def __h_variant(self, b: ir.IRBuilder, s: IR.VariantConstruct) -> ir.Value:
        tm = self.__module.type_mapper
        r = ir.Constant(tm.get_ll_type(s.result.type_id), ir.Undefined)
        r = b.insert_value(r, ir.Constant(ir.IntType(32), s.variant.discriminant), 0, "set.disc")
        if s.payload_fields is not None:
            pt = self.__type_ctx[s.variant.payload_type]
            assert isinstance(pt, Type.StructType)
            pv = ir.Constant(tm.get_ll_type(s.variant.payload_type), ir.Undefined)
            for i, fv in enumerate(s.payload_fields):
                pv = b.insert_value(pv, self.__val(fv), i)
            pay_arr = ir.ArrayType(ir.IntType(8), tm.get_type_size(s.variant.payload_type))
            r = b.insert_value(r, b.bitcast(pv, pay_arr, name="payload.bytes"), 1)
        return r

    def __h_syswrite(self, b: ir.IRBuilder, s: IR.SysWrite) -> None:
        buf = self.__val(s.buf)
        write = self.__module.intrinsics.get(IntrinsicKind.Write)
        b.call(write, [self.__val(s.fd), b.extract_value(buf, 0), b.extract_value(buf, 1)])

    def __h_sysread(self, b: ir.IRBuilder, s: IR.SysRead) -> ir.Value:
        buf = self.__val(s.buf)
        read = self.__module.intrinsics.get(IntrinsicKind.Read)
        return b.call(read, [self.__val(s.fd), b.extract_value(buf, 0), b.extract_value(buf, 1)], name=s.result.name)

    def __h_phi_impl(self, b: ir.IRBuilder, s: IR.Phi) -> None:
        phi = b.phi(self.__module.type_mapper.get_ll_type(s.result.type_id), name=s.result.name)
        self.__regs[s.result.name] = phi
        for src, val in s.incoming:
            phi.add_incoming(self.__val(val), self.__blocks[src.label])

    # ------------------------------------------------------------------
    # terminators
    # ------------------------------------------------------------------

    def __term(self, b: ir.IRBuilder, t: IR.Terminator) -> None:
        match t:
            case IR.Ret(value=v):
                b.ret(self.__val(v))
            case IR.RetVoid():
                b.ret_void()
            case IR.Br(target=tg):
                b.branch(self.__blocks[tg.label])
            case IR.CondBr(cond=c, then_block=th, else_block=el):
                b.cbranch(self.__val(c), self.__blocks[th.label], self.__blocks[el.label])
            case IR.Panic(message=m):
                self.__panic(b, m)
            case IR.Match():
                self.__match(b, t)
            case _:
                raise ValueError(f"Unknown terminator: {type(t).__name__}")

    def __panic(self, b: ir.IRBuilder, msg: IR.Value) -> None:
        mv = self.__val(msg)
        write = self.__module.intrinsics.get(IntrinsicKind.Write)
        b.call(write, [ir.Constant(ir.IntType(32), 2), b.extract_value(mv, 0), b.extract_value(mv, 1)])
        b.call(self.__module.intrinsics.get(IntrinsicKind.Exit), [ir.Constant(ir.IntType(32), 1)])
        b.unreachable()

    def __match(self, b: ir.IRBuilder, t: IR.Match) -> None:
        assert self.__func is not None
        mv = self.__val(t.value)
        mty = self.__type_ctx[t.value.type_id]
        default = self.__blocks[t.default.label] if t.default else None
        if default is None:
            default = self.__func._ir.append_basic_block("match.unreach")
            ir.IRBuilder(default).unreachable()

        if isinstance(mty, (Type.IntType, Type.CharType, Type.BoolType)):
            sw = b.switch(mv, default)
            for arm in t.arms:
                if isinstance(arm.pattern, IR.IntPattern):
                    sw.add_case(ir.Constant(self.__module.type_mapper.get_ll_type(arm.pattern.value.type_id), arm.pattern.value.value), self.__blocks[arm.body.label])
                elif isinstance(arm.pattern, IR.CharPattern):
                    sw.add_case(ir.Constant(self.__module.type_mapper.get_ll_type(arm.pattern.value.type_id), ord(arm.pattern.value.value)), self.__blocks[arm.body.label])

        elif isinstance(mty, Type.EnumType):
            disc = b.load(b.gep(mv, [ir.Constant(ir.IntType(32), 0), ir.Constant(ir.IntType(32), 0)], inbounds=True, name="disc.ptr"), name="disc.val")
            sw = b.switch(disc, default)
            for arm in t.arms:
                if isinstance(arm.pattern, IR.EnumPattern):
                    bb = self.__blocks[arm.body.label]
                    sw.add_case(ir.Constant(ir.IntType(32), arm.pattern.variant.discriminant), bb)
                    if arm.pattern.fields and arm.pattern.variant.payload_type is not None:
                        self.__unpack(bb, mv, arm.pattern)

    def __unpack(self, bb: ir.Block, mv: ir.Value, pat: IR.EnumPattern) -> None:
        assert self.__func is not None
        tm = self.__module.type_mapper
        cb = ir.IRBuilder(bb)
        ins = list(bb.instructions)
        if ins:
            cb.position_before(ins[0])

        pt = self.__type_ctx[pat.variant.payload_type]
        assert isinstance(pt, Type.StructType)
        pfields = pt.get_fields(self.__type_ctx)

        payload = cb.bitcast(
            cb.gep(mv, [ir.Constant(ir.IntType(32), 0), ir.Constant(ir.IntType(32), 1)], inbounds=True, name="payload.ptr"),
            ir.PointerType(tm.get_ll_type(pat.variant.payload_type)), name="payload.cast")

        for i, f in enumerate(pat.fields):
            if i >= len(pfields):
                break
            fv = cb.load(cb.gep(payload, [ir.Constant(ir.IntType(32), 0), ir.Constant(ir.IntType(32), i)], inbounds=True), name=f"field.{f.name}.val")
            a = self.__func.var_allocas.get(f.symbol_id)
            if a is not None:
                cb.store(fv, a)

    # ------------------------------------------------------------------
    # comparison / arithmetic
    # ------------------------------------------------------------------

    def __cmp(self, b: ir.IRBuilder, op: BinaryOperator, l: ir.Value, r: ir.Value) -> ir.Value:
        pred = {BinaryOperator.Eq: "==", BinaryOperator.Neq: "!=", BinaryOperator.Lt: "<", BinaryOperator.Gt: ">", BinaryOperator.Leq: "<=", BinaryOperator.Geq: ">="}[op]
        if isinstance(l.type, (ir.IntType, ir.PointerType)):  # type: ignore[union-attr]
            return b.icmp_signed(pred, l, r, name="cmp")  # type: ignore[arg-type]
        return b.fcmp_ordered(pred, l, r, name="cmp")  # type: ignore[arg-type]

    def __arith(self, b: ir.IRBuilder, op: BinaryOperator, l: ir.Value, r: ir.Value, name: str) -> ir.Value:
        ops = {BinaryOperator.Add: b.add, BinaryOperator.Sub: b.sub, BinaryOperator.Mul: b.mul,
               BinaryOperator.Div: b.sdiv, BinaryOperator.Mod: b.srem, BinaryOperator.BitAnd: b.and_,
               BinaryOperator.BitOr: b.or_, BinaryOperator.BitXor: b.xor,
               BinaryOperator.Shl: b.shl, BinaryOperator.Shr: b.ashr}
        return ops[op](l, r, name=name)
