"""
CFG → LLVM IR translator.
"""

from __future__ import annotations

from llvmlite import ir

from compiler.analysis.ty import ty as Type
from compiler.analysis.ty.context import TypeCtx
from compiler.codegen.cfg import ir as IR
from compiler.codegen.llvm.builder import BuilderPosition, LLBuilder
from compiler.codegen.llvm.module import LLFunction, LLModule
from compiler.codegen.llvm.types import LLTypeCtx
from compiler.codegen.llvm.value import LLValue
from compiler.utils.log import CompilerLog


def ch_llvm():
    return CompilerLog.get("llvm")


class LLTranslator:
    """CFG Functions → LLVM Module."""

    def __init__(self, type_ctx: TypeCtx, unit_names: dict[int, str]) -> None:
        self.__type_ctx = type_ctx

        ll_module = ir.Module(name="yian.module")
        ll_module.triple = "x86_64-unknown-linux-gnu"

        self.__ll_type_ctx = LLTypeCtx(type_ctx, ll_module, unit_names)
        self.__module = LLModule(ll_module, self.__ll_type_ctx)
        self.__func: LLFunction | None = None

    # ------------------------------------------------------------------
    # public
    # ------------------------------------------------------------------

    def run(self, functions: dict[int, IR.Function]) -> None:
        for function in functions.values():
            self.__module.declare(function)
        for function in functions.values():
            self.__build(function)
        if self.__module.has_yian_main:
            self.__module.emit_wrapper_main()

    def export(self) -> LLModule:
        return self.__module

    @property
    def func(self) -> LLFunction:
        assert self.__func is not None
        return self.__func

    # ------------------------------------------------------------------
    # resolve
    # ------------------------------------------------------------------

    def __resolve(self, builder: LLBuilder, value: IR.Value) -> LLValue:
        if isinstance(value, IR.Reg):
            if self.__ll_type_ctx.is_zst(value.type_id):
                # Zero-sized values have no LLVM representation — return an
                # erased placeholder. Consumers of ZST values skip before
                # ever emitting with it, so this is never materialized.
                ll_type = self.__ll_type_ctx.get_ll_type(value.type_id)
                return LLValue(value.type_id, ir.Constant(ll_type.ir_type, ir.Undefined))  # type: ignore
            return self.func.reg(value.name)
        if isinstance(value, IR.IntLiteral):
            return LLValue(value.type_id,
                           ir.Constant(self.__ll_type_ctx.get_ll_type(value.type_id).ir_type, value.value))
        if isinstance(value, IR.FloatLiteral):
            return LLValue(value.type_id,
                           ir.Constant(self.__ll_type_ctx.get_ll_type(value.type_id).ir_type, value.value))
        if isinstance(value, IR.BoolLiteral):
            return LLValue(value.type_id,
                           ir.Constant(self.__ll_type_ctx.get_ll_type(value.type_id).ir_type, 1 if value.value else 0))
        if isinstance(value, IR.NullptrLiteral):
            return LLValue(value.type_id,
                           ir.Constant(self.__ll_type_ctx.get_ll_type(value.type_id).ir_type, None))
        if isinstance(value, IR.CharLiteral):
            return LLValue(value.type_id,
                           ir.Constant(self.__ll_type_ctx.get_ll_type(value.type_id).ir_type, ord(value.value)))
        return builder.string_literal(value.value, value.type_id)

    # ------------------------------------------------------------------
    # build
    # ------------------------------------------------------------------

    def __build(self, cfg: IR.Function) -> None:
        self.__func = self.__module.get_func(cfg.type_id)
        func = self.__func

        func.add_entry_block()

        # create blocks first — must be done before any position_at calls
        for block in cfg.blocks:
            if block.label == cfg.entry.label:
                func.add_block(block.label, func.entry_block)
            else:
                func.add_block(block.label, func.new_block(block.label))

        builder = LLBuilder(func, self.__module, self.__ll_type_ctx, self.__type_ctx)

        # entry block + local var allocas
        builder.position_at(cfg.entry.label, where=BuilderPosition.First)
        for symbol_id, var_ref in cfg.local_vars.items():
            func.set_alloca(symbol_id, builder.alloca(var_ref.type_id))

        # store params — zero-sized params are dropped from the LLVM signature,
        # so walk the real args and skip ZST params to keep alignment.
        builder.position_at(cfg.entry.label, where=BuilderPosition.End)
        ll_args = func.ir_func.args
        arg_idx = 0
        for symbol_id in cfg.params:
            type_id = cfg.local_vars[symbol_id].type_id
            if self.__ll_type_ctx.is_zst(type_id):
                continue  # no LLVM argument for a zero-sized param
            builder.store(LLValue(type_id, ll_args[arg_idx]), func.get_var_ptr(symbol_id))
            arg_idx += 1

        # translate
        for block in cfg.blocks:
            # phi
            builder.position_at(block.label, where=BuilderPosition.Phi)
            for phi in block.phis:
                if self.__ll_type_ctx.is_zst(phi.result.type_id):
                    continue  # zero-sized phis have no LLVM representation
                builder.phi(phi.result.type_id,
                            [(src.label, self.__resolve(builder, val)) for src, val in phi.incoming],
                            phi.result.name)

            # statements
            builder.position_at(block.label, where=BuilderPosition.End)
            for stmt in block.stmts:
                self.__translate(builder, stmt)

            # terminator
            assert block.terminator is not None
            self.__terminator(builder, block.terminator)

    # ------------------------------------------------------------------
    # dispatch
    # ------------------------------------------------------------------

    def __translate(self, builder: LLBuilder, stmt: IR.Stmt) -> None:
        ch_llvm().trace(lambda: type(stmt).__name__)
        match stmt:
            case IR.VarPtr():
                builder.var_ptr(stmt.var_ref.symbol_id, stmt.result.name)
            case IR.Alloca():
                builder.alloca_store(self.__resolve(builder, stmt.value), stmt.result.name)
            case IR.FieldPtr():
                builder.gep(self.__resolve(builder, stmt.base), [0, stmt.field_index], stmt.result.name)
            case IR.ElementPtr():
                builder.element_ptr(self.__resolve(builder, stmt.base), self.__resolve(builder, stmt.offset), stmt.result.name)
            case IR.PtrDiff():
                builder.ptr_diff(self.__resolve(builder, stmt.lhs), self.__resolve(builder, stmt.rhs), stmt.result.name)
            case IR.Load():
                builder.load(self.__resolve(builder, stmt.ptr), stmt.result.name)
            case IR.Store():
                builder.store(self.__resolve(builder, stmt.value), self.__resolve(builder, stmt.ptr))
            case IR.Malloc():
                builder.malloc(stmt.type_id, self.__resolve(builder, stmt.size), stmt.result.name)
            case IR.Binary():
                builder.binary(stmt.op, self.__resolve(builder, stmt.lhs), self.__resolve(builder, stmt.rhs), stmt.result.name)
            case IR.Unary():
                builder.unary(stmt.op, self.__resolve(builder, stmt.operand), stmt.result.name)
            case IR.ExtractValue():
                builder.extract_value(self.__resolve(builder, stmt.base), stmt.field_index, stmt.result.name)
            case IR.Delete():
                builder.delete(self.__resolve(builder, stmt.ptr))
            case IR.Call():
                builder.call_func(
                    stmt.callee_type,
                    [self.__resolve(builder, a) for a in stmt.args if not self.__ll_type_ctx.is_zst(a.type_id)],
                    stmt.result.name,
                    stmt.result.type_id
                )
            case IR.Invoke():
                builder.call_value(
                    self.__resolve(builder, stmt.callee),
                    [self.__resolve(builder, a) for a in stmt.args if not self.__ll_type_ctx.is_zst(a.type_id)],
                    stmt.result.name,
                    stmt.result.type_id
                )
            case IR.Cast():
                builder.cast(self.__resolve(builder, stmt.value), stmt.to_type, stmt.result.name)
            case IR.SizeOf():
                builder.sizeof_const(stmt.type_id, stmt.result.name)
            case IR.FuncPtr():
                builder.func_ptr_by_type(stmt.func_type_id, stmt.result.type_id, stmt.result.name)
            case IR.AggregateConstruct():
                builder.aggregate(stmt.result.type_id,
                                  [self.__resolve(builder, f) for f in stmt.fields], stmt.result.name)
            case IR.ArrayConstruct():
                builder.array(stmt.result.type_id,
                              [self.__resolve(builder, e) for e in stmt.elements], stmt.result.name)
            case IR.VariantConstruct():
                fields = [self.__resolve(builder, f) for f in stmt.payload_fields] if stmt.payload_fields else None
                builder.construct_enum_variant(stmt.result.type_id, stmt.variant.discriminant, stmt.variant.payload_type, fields, stmt.result.name)
            case IR.SysWrite():
                builder.sys_write(self.__resolve(builder, stmt.fd), self.__resolve(builder, stmt.buf))
            case IR.SysRead():
                builder.sys_read(self.__resolve(builder, stmt.fd), self.__resolve(builder, stmt.buf), stmt.result.name)
            case IR.Open():
                builder.open(self.__resolve(builder, stmt.path), self.__resolve(builder, stmt.flags), stmt.result.name)
            case IR.Close():
                builder.close(self.__resolve(builder, stmt.fd), stmt.result.name)
            case IR.YianArgc():
                builder.yian_argc(stmt.result.name)
            case IR.YianArgvPtr():
                builder.yian_argv_ptr(self.__resolve(builder, stmt.index), stmt.result.name)
            case IR.YianCstrlen():
                builder.yian_cstrlen(self.__resolve(builder, stmt.ptr), stmt.result.name)

    # ------------------------------------------------------------------
    # terminators
    # ------------------------------------------------------------------

    def __terminator(self, builder: LLBuilder, terminator: IR.Terminator) -> None:
        match terminator:
            case IR.Ret(value=value):
                if self.__ll_type_ctx.is_zst(value.type_id):
                    builder.ret(None)  # zero-sized return: `ret void`
                else:
                    builder.ret(self.__resolve(builder, value))
            case IR.Br(target=target):
                builder.br(target.label)
            case IR.CondBr(cond=cond, then_block=then_block, else_block=else_block):
                builder.condbr(self.__resolve(builder, cond), then_block.label, else_block.label)
            case IR.Panic(message=message):
                builder.panic(self.__resolve(builder, message))
            case IR.YianExit(code=code):
                builder.yian_exit(self.__resolve(builder, code))
            case IR.Match():
                self.__emit_match(builder, terminator)

    # ------------------------------------------------------------------
    # match
    # ------------------------------------------------------------------

    def __emit_match(self, builder: LLBuilder, t: IR.Match) -> None:
        matched = self.__resolve(builder, t.value)
        matched_type = self.__type_ctx[t.value.type_id]
        default_label = t.default.label if t.default else ""

        is_enum_ref = t.is_ref
        inner_type = matched_type
        if is_enum_ref:
            assert isinstance(matched_type, Type.PointerType)
            inner_type = self.__type_ctx[matched_type.pointee_type]

        if isinstance(inner_type, (Type.IntType, Type.CharType, Type.BoolType)):
            cases = [(self.__resolve(builder, arm.pattern.value), arm.body.label)
                     for arm in t.arms
                     if isinstance(arm.pattern, (IR.IntPattern, IR.CharPattern))]
            builder.switch(matched, cases, default_label)

        elif isinstance(inner_type, Type.EnumType):
            if is_enum_ref:
                # matched is a pointer to the enum (&E). Load it to extract discriminant.
                enum_val = builder.load(matched, "enum_val_ref")
                disc = builder.extract_value(enum_val, 0, "disc_ref")
            else:
                disc = builder.extract_value(matched, 0, "disc")

            cases = [(builder.i32(arm.pattern.variant.discriminant), arm.body.label)
                     for arm in t.arms
                     if isinstance(arm.pattern, IR.EnumPattern)]

            if not is_enum_ref:
                # Alloca the matched value so unpack_enum_payload can GEP on a pointer.
                # Must happen before switch terminates the block.
                matched_ptr = builder.alloca(matched.type_id)
                builder.store(matched, matched_ptr)
            else:
                # Ref mode: matched IS already a pointer to the enum; no copy needed.
                matched_ptr = matched

            builder.switch(disc, cases, default_label)

            # Save position: switch terminates this block, unpack must go into arm blocks.
            saved_label = builder.current_block_label

            for arm in t.arms:
                if isinstance(arm.pattern, IR.EnumPattern) and arm.pattern.fields \
                        and arm.pattern.variant.payload_type is not None:
                    field_pairs = [(i, f.symbol_id) for i, f in enumerate(arm.pattern.fields)]
                    builder.position_at(arm.body.label, where=BuilderPosition.First)
                    if is_enum_ref:
                        builder.unpack_enum_payload_ref(
                            matched_ptr, arm.body.label,
                            arm.pattern.variant.payload_type, field_pairs)
                    else:
                        builder.unpack_enum_payload(
                            matched_ptr, arm.body.label,
                            arm.pattern.variant.payload_type, field_pairs)

            # Restore builder to original block so __build can continue correctly.
            builder.position_at(saved_label)
