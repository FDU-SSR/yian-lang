"""
CFG → LLVM IR translator.
"""

from __future__ import annotations

from llvmlite import ir

from compiler.analysis.ty import ty as Type
from compiler.analysis.ty.context import TypeCtx
from compiler.codegen.cfg import ir as IR
from compiler.codegen.llvm.builder import LLBuilder
from compiler.codegen.llvm.intrinsics import IntrinsicKind
from compiler.codegen.llvm.module import LLFunction, LLModule
from compiler.codegen.llvm.types import LLTypeCtx
from compiler.codegen.llvm.value import LLValue


class LLTranslator:
    """CFG Functions → LLVM Module."""

    def __init__(self, type_ctx: TypeCtx, unit_names: dict[int, str]) -> None:
        self.__type_ctx = type_ctx

        ll_module = ir.Module(name="yian.module")
        ll_module.triple = "x86_64-unknown-linux-gnu"

        self.__ll_type_ctx = LLTypeCtx(type_ctx, ll_module, unit_names)
        self.__module = LLModule(ll_module, self.__ll_type_ctx)

        # per-function state (reset in __build)
        self.__func: LLFunction | None = None

    # ------------------------------------------------------------------
    # public
    # ------------------------------------------------------------------

    def run(self, functions: dict[str, IR.Function]) -> None:
        for function in functions.values():
            self.__module.declare(function)
        for function in functions.values():
            self.__build(function)

    def export(self) -> LLModule:
        return self.__module

    # ------------------------------------------------------------------
    # per-function build
    # ------------------------------------------------------------------

    def __build(self, cfg: IR.Function) -> None:
        self.__func = self.__module.get_func(cfg.type_id)

        # ── entry block + allocas ──
        self.__func.add_entry_block()
        builder = LLBuilder(self.__func, self.__module, self.__ll_type_ctx, self.__type_ctx)
        builder.position_at(cfg.entry.label, where="first")
        for symbol_id, vr in cfg.local_vars.items():
            self.__func.set_alloca(symbol_id, builder.alloca(vr.type_id))

        builder.position_at(cfg.entry.label, where="end")
        self.__func.store_params(cfg.params, builder)

        # ── create blocks ──
        for blk in cfg.blocks:
            if blk.label == cfg.entry.label:
                self.__func.add_block(blk.label, self.__func.entry_block)
            else:
                self.__func.add_block(blk.label, self.__func.new_block(blk.label))

        # ── translate blocks ──
        for blk in cfg.blocks:
            builder.position_at(blk.label, where="phi")

            # phi nodes
            for ps in blk.phis:
                self.__h_phi_impl(builder, ps)

            # regular statements + terminator
            builder.position_at(blk.label, where="end")
            for s in blk.stmts:
                self.__translate(builder, s)

            assert blk.terminator is not None
            self.__term(builder, blk.terminator)

    # ------------------------------------------------------------------
    # dispatch
    # ------------------------------------------------------------------

    def __translate(self, builder: LLBuilder, stmt: IR.Stmt) -> None:
        match stmt:
            case IR.VarPtr():
                res = self.__h_varptr(stmt)
                self.__func.set_reg(stmt.result.name, res)
            case IR.Alloca():
                res = self.__h_alloca(builder, stmt)
                self.__func.set_reg(stmt.result.name, res)
            case IR.FieldPtr():
                res = self.__h_fieldptr(builder, stmt)
                self.__func.set_reg(stmt.result.name, res)
            case IR.Load():
                res = self.__h_load(builder, stmt)
                self.__func.set_reg(stmt.result.name, res)
            case IR.Store():
                self.__h_store(builder, stmt)
            case IR.Malloc():
                res = self.__h_malloc(builder, stmt)
                self.__func.set_reg(stmt.result.name, res)
            case IR.Binary():
                res = self.__h_binary(builder, stmt)
                self.__func.set_reg(stmt.result.name, res)
            case IR.Unary():
                res = self.__h_unary(builder, stmt)
                self.__func.set_reg(stmt.result.name, res)
            case IR.ExtractValue():
                res = self.__h_extractvalue(builder, stmt)
                self.__func.set_reg(stmt.result.name, res)
            case IR.Delete():
                self.__h_delete(builder, stmt)
            case IR.Call():
                res = self.__h_call(builder, stmt)
                self.__func.set_reg(stmt.result.name, res)
            case IR.Invoke():
                res = self.__h_invoke(builder, stmt)
                self.__func.set_reg(stmt.result.name, res)
            case IR.Cast():
                res = self.__h_cast(builder, stmt)
                self.__func.set_reg(stmt.result.name, res)
            case IR.SizeOf():
                res = self.__h_sizeof(builder, stmt)
                self.__func.set_reg(stmt.result.name, res)
            case IR.FuncPtr():
                res = self.__h_funcptr(builder, stmt)
                self.__func.set_reg(stmt.result.name, res)
            case IR.AggregateConstruct():
                res = self.__h_aggregate(builder, stmt)
                self.__func.set_reg(stmt.result.name, res)
            case IR.ArrayConstruct():
                res = self.__h_array(builder, stmt)
                self.__func.set_reg(stmt.result.name, res)
            case IR.VariantConstruct():
                res = self.__h_variant(builder, stmt)
                self.__func.set_reg(stmt.result.name, res)
            case IR.SysWrite():
                self.__h_syswrite(builder, stmt)
            case IR.SysRead():
                res = self.__h_sysread(builder, stmt)
                self.__func.set_reg(stmt.result.name, res)

    # ------------------------------------------------------------------
    # stmt handlers
    # ------------------------------------------------------------------

    def __h_varptr(self, stmt: IR.VarPtr) -> LLValue:
        a = self.__func.alloca(stmt.var_ref.symbol_id)
        assert a is not None
        return a

    def __h_alloca(self, builder: LLBuilder, stmt: IR.Alloca) -> LLValue:
        assert self.__func is not None
        eb = builder.fork_block(self.__func.entry_block, where="first")
        a = eb.alloca(stmt.value.type_id)
        builder.store(stmt.value, a)
        return a

    def __h_fieldptr(self, builder: LLBuilder, stmt: IR.FieldPtr) -> LLValue:
        return builder.gep(stmt.base, [0, stmt.field_index])

    def __h_load(self, builder: LLBuilder, stmt: IR.Load) -> LLValue:
        return builder.load(stmt.ptr)

    def __h_store(self, builder: LLBuilder, stmt: IR.Store) -> None:
        builder.store(stmt.value, stmt.ptr)

    def __h_malloc(self, builder: LLBuilder, stmt: IR.Malloc) -> LLValue:
        raw = builder.call_intrinsic(IntrinsicKind.Malloc, [stmt.size])
        ptr_t = self.__ll_type_ctx.get_ll_type(self.__type_ctx.alloc_pointer(stmt.type_id))
        return LLValue(stmt.result.type_id, builder.bitcast_ptr(raw, ptr_t))

    def __h_binary(self, builder: LLBuilder, stmt: IR.Binary) -> LLValue:
        return builder.binary(stmt.op, stmt.lhs, stmt.rhs)

    def __h_unary(self, builder: LLBuilder, stmt: IR.Unary) -> LLValue:
        return builder.unary(stmt.op, stmt.operand)

    def __h_extractvalue(self, builder: LLBuilder, stmt: IR.ExtractValue) -> LLValue:
        return builder.extract_value(stmt.base, stmt.field_index)

    def __h_delete(self, builder: LLBuilder, stmt: IR.Delete) -> None:
        builder.call_intrinsic(
            IntrinsicKind.Free,
            [builder.bitcast_ptr(builder.resolve(stmt.ptr).ir_val, builder.i8_ptr_type())],
        )

    def __h_call(self, builder: LLBuilder, stmt: IR.Call) -> LLValue:
        callee = self.__module.get_func(stmt.callee_type)
        assert callee is not None, f"Function callee_type={stmt.callee_type} not declared"
        return builder.call(callee, stmt.args)

    def __h_invoke(self, builder: LLBuilder, stmt: IR.Invoke) -> LLValue:
        return builder.call_value(stmt.callee, stmt.args)

    def __h_cast(self, builder: LLBuilder, stmt: IR.Cast) -> LLValue:
        return builder.cast(stmt.value, stmt.to_type)

    def __h_sizeof(self, builder: LLBuilder, stmt: IR.SizeOf) -> LLValue:
        return builder.sizeof_const(stmt.type_id)

    def __h_funcptr(self, builder: LLBuilder, stmt: IR.FuncPtr) -> LLValue:
        callee = self.__module.get_func(stmt.func_type_id)
        assert callee is not None, f"FuncPtr func_type_id={stmt.func_type_id} not declared"
        return builder.func_ptr(callee)

    def __h_aggregate(self, builder: LLBuilder, stmt: IR.AggregateConstruct) -> LLValue:
        r = builder.undef(stmt.result.type_id)
        for i, fv in enumerate(stmt.fields):
            r = builder.insert_value(r, fv, i)
        return r

    def __h_array(self, builder: LLBuilder, stmt: IR.ArrayConstruct) -> LLValue:
        r = builder.undef(stmt.result.type_id)
        for i, ev in enumerate(stmt.elements):
            r = builder.insert_value(r, ev, i)
        return r

    def __h_variant(self, builder: LLBuilder, stmt: IR.VariantConstruct) -> LLValue:
        r = builder.undef(stmt.result.type_id)
        r = builder.insert_value(r, builder.i32(stmt.variant.discriminant), 0)
        if stmt.payload_fields is not None:
            pt = self.__type_ctx[stmt.variant.payload_type]
            assert isinstance(pt, Type.StructType)
            pv = builder.undef(stmt.variant.payload_type)
            for i, fv in enumerate(stmt.payload_fields):
                pv = builder.insert_value(pv, fv, i)
            pay_arr = builder.padding_array(
                self.__ll_type_ctx.get_type_size(stmt.variant.payload_type)
            )
            r = builder.insert_value(
                r, LLValue(-1, builder.bitcast_ptr(pv.ir_val, pay_arr)), 1
            )
        return r

    def __h_syswrite(self, builder: LLBuilder, stmt: IR.SysWrite) -> None:
        builder.call_intrinsic(
            IntrinsicKind.Write,
            [stmt.fd, builder.extract_value(stmt.buf, 0), builder.extract_value(stmt.buf, 1)],
        )

    def __h_sysread(self, builder: LLBuilder, stmt: IR.SysRead) -> LLValue:
        raw = builder.call_intrinsic(
            IntrinsicKind.Read,
            [stmt.fd, builder.extract_value(stmt.buf, 0), builder.extract_value(stmt.buf, 1)],
        )
        return LLValue(stmt.result.type_id, raw)

    def __h_phi_impl(self, builder: LLBuilder, stmt: IR.Phi) -> None:
        phi = builder.phi(stmt.result.type_id)
        self.__func.set_reg(stmt.result.name, phi)
        for src, val in stmt.incoming:
            builder.add_incoming(phi, builder.resolve(val), src.label)

    # ------------------------------------------------------------------
    # terminators
    # ------------------------------------------------------------------

    def __term(self, builder: LLBuilder, t: IR.Terminator) -> None:
        match t:
            case IR.Ret(value=v):
                builder.ret(v)
            case IR.RetVoid():
                builder.ret(None)
            case IR.Br(target=tg):
                builder.br(tg.label)
            case IR.CondBr(cond=c, then_block=th, else_block=el):
                builder.condbr(c, th.label, el.label)
            case IR.Panic(message=m):
                self.__panic(builder, m)
            case IR.Match():
                self.__match(builder, t)
            case _:
                raise ValueError(f"Unknown terminator: {type(t).__name__}")

    def __panic(self, builder: LLBuilder, msg: IR.Value) -> None:
        builder.call_intrinsic(
            IntrinsicKind.Write,
            [builder.i32(2), builder.extract_value(msg, 0), builder.extract_value(msg, 1)],
        )
        builder.call_intrinsic(IntrinsicKind.Exit, [builder.i32(1)])
        builder.unreachable()

    def __match(self, builder: LLBuilder, t: IR.Match) -> None:
        mv = builder.resolve(t.value)
        mty = self.__type_ctx[t.value.type_id]
        default = self.__func.block(t.default.label) if t.default else None
        if default is None:
            default = self.__func.new_block("match.unreach")
            ir.IRBuilder(default).unreachable()

        if isinstance(mty, (Type.IntType, Type.CharType, Type.BoolType)):
            sw = builder.switch(t.value, default)
            for arm in t.arms:
                if isinstance(arm.pattern, IR.IntPattern):
                    builder.add_case(
                        sw, builder.resolve(arm.pattern.value), arm.body.label
                    )
                elif isinstance(arm.pattern, IR.CharPattern):
                    builder.add_case(
                        sw, builder.resolve(arm.pattern.value), arm.body.label
                    )

        elif isinstance(mty, Type.EnumType):
            disc = builder.load_raw(
                builder.gep_raw(mv, [0, 0])
            )
            sw = builder.raw().switch(disc, default)
            for arm in t.arms:
                if isinstance(arm.pattern, IR.EnumPattern):
                    bb = self.__func.block(arm.body.label)
                    sw.add_case(builder.i32(arm.pattern.variant.discriminant), bb)
                    if arm.pattern.fields and arm.pattern.variant.payload_type is not None:
                        self.__unpack(builder, bb, mv, arm.pattern)

    def __unpack(
        self, builder: LLBuilder, bb: ir.Block, mv: ir.Value, pat: IR.EnumPattern
    ) -> None:
        pt = self.__type_ctx[pat.variant.payload_type]
        assert isinstance(pt, Type.StructType)
        pfields = pt.get_fields(self.__type_ctx)

        cb = builder.fork_block(bb, where="first")

        payload = cb.bitcast_ptr(
            cb.gep_raw(mv, [0, 1]),
            ir.PointerType(self.__ll_type_ctx.get_ll_type(pat.variant.payload_type)),
        )

        for i, f in enumerate(pat.fields):
            if i >= len(pfields):
                break
            fv = cb.load_raw(
                cb.gep_raw(payload, [0, i])
            )
            a = self.__func.alloca(f.symbol_id)
            if a is not None:
                cb.store_raw(fv, a.ir_val)
