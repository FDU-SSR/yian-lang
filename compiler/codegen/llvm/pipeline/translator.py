"""
CFG → LLVM IR translator.
"""

from __future__ import annotations

from llvmlite import ir

from compiler.analysis.ty.context import TypeCtx
from compiler.codegen.cfg import ir as IR
from compiler.codegen.llvm.pipeline.builder import LLBuilder
from compiler.codegen.llvm.function.core import BuilderPosition
from compiler.codegen.llvm.base.module import LLFunction, LLModule, apply_target
from compiler.codegen.llvm.base.types import LLTypeCtx
from compiler.codegen.llvm.base.value import LLValue
from compiler.utils.log import CompilerLog


def ch_llvm():
    return CompilerLog.get("llvm")


class LLTranslator:
    """CFG Functions → LLVM Module."""

    def __init__(self, type_ctx: TypeCtx, unit_names: dict[int, str], raw_pointers: bool = False,
                 entry_type_id: int | None = None) -> None:
        self.__type_ctx = type_ctx
        self.__raw_pointers = raw_pointers

        ll_module = ir.Module(name="yian.module")
        apply_target(ll_module)

        self.__ll_type_ctx = LLTypeCtx(type_ctx, ll_module, unit_names, raw_pointers)
        self.__module = LLModule(ll_module, type_ctx, self.__ll_type_ctx, entry_type_id)
        self.__func: LLFunction | None = None

    # ------------------------------------------------------------------
    # public
    # ------------------------------------------------------------------

    def run(self, functions: dict[int, IR.Function]) -> None:
        for function in functions.values():
            self.__module.declare(function)
        for function in functions.values():
            self.__build(function)

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
        if isinstance(value, IR.CharLiteral):
            return LLValue(value.type_id,
                           ir.Constant(self.__ll_type_ctx.get_ll_type(value.type_id).ir_type, ord(value.value)))
        return builder.string_literal(value.value, value.type_id)

    def __bind_result(self, result: IR.Reg, value: LLValue) -> None:
        if not self.__ll_type_ctx.is_zst(result.type_id):
            self.func.set_reg(result.name, value)

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

        builder = LLBuilder(func, self.__module, self.__ll_type_ctx, self.__type_ctx, self.__raw_pointers)
        if cfg.frame_lock is not None:
            # 帧退出写 SENTINEL 并弹出影子栈
            builder.safety.set_frame_lock(cfg.frame_lock[0])

        # entry block + local var allocas
        builder.position_at(cfg.entry.label, where=BuilderPosition.First)
        for symbol_id, var_ref in cfg.local_vars.items():
            func.set_alloca(symbol_id, builder.memory.alloca(var_ref.type_id))

        # store params — zero-sized params are dropped from the LLVM signature,
        # so walk the real args and skip ZST params to keep alignment.
        builder.position_at(cfg.entry.label, where=BuilderPosition.End)
        ll_args = func.ir_func.args
        arg_idx = 0
        for symbol_id in cfg.params:
            type_id = cfg.local_vars[symbol_id].type_id
            if self.__ll_type_ctx.is_zst(type_id):
                continue  # no LLVM argument for a zero-sized param
            builder.memory.store(LLValue(type_id, ll_args[arg_idx]), func.get_var_ptr(symbol_id))
            arg_idx += 1

        # translate
        for block in cfg.blocks:
            # phi
            builder.position_at(block.label, where=BuilderPosition.Phi)
            for phi in block.phis:
                if self.__ll_type_ctx.is_zst(phi.result.type_id):
                    continue  # zero-sized phis have no LLVM representation
                incoming = [
                    (source.label, self.__resolve(builder, value).ir_val)
                    for source, value in phi.incoming
                ]
                phi_value = builder.flow.phi(phi.result.type_id, incoming)
                self.__bind_result(phi.result, LLValue(phi.result.type_id, phi_value))

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
            case IR.LiveKnownBegin() | IR.LiveKnownEnd():
                raise ValueError("live-known window marker reached LLVM lowering")
            case IR.CheckRequest():
                # 标记应由 passes/insert_checks.py 物化；到这里说明管线漏了插入 pass。
                raise ValueError(f"unmaterialized check request: {stmt.kind}")
            case IR.VarPtr():
                value = builder.memory.var_ptr(
                    stmt.var_ref.symbol_id,
                    self.__resolve(builder, stmt.frame_word) if stmt.frame_word is not None else None,
                    stmt.raw,
                )
                self.__bind_result(stmt.result, value)
            case IR.Alloca():
                value = builder.memory.alloca_store(
                    self.__resolve(builder, stmt.value),
                    self.__resolve(builder, stmt.frame_word) if stmt.frame_word is not None else None,
                    stmt.raw,
                )
                self.__bind_result(stmt.result, value)
            case IR.FieldPtr():
                value = builder.memory.gep(self.__resolve(builder, stmt.base), [0, stmt.field_index])
                self.__bind_result(stmt.result, value)
            case IR.ElementPtr():
                value = builder.pointer_ops.element_ptr(self.__resolve(builder, stmt.base), self.__resolve(builder, stmt.offset))
                self.__bind_result(stmt.result, value)
            case IR.PtrDiff():
                value = builder.pointer_ops.ptr_diff(self.__resolve(builder, stmt.lhs), self.__resolve(builder, stmt.rhs))
                self.__bind_result(stmt.result, value)
            case IR.PtrCmp():
                value = builder.pointer_ops.ptr_cmp(stmt.op, self.__resolve(builder, stmt.lhs), self.__resolve(builder, stmt.rhs))
                self.__bind_result(stmt.result, value)
            case IR.Load():
                value = builder.memory.load(self.__resolve(builder, stmt.ptr))
                self.__bind_result(stmt.result, value)
            case IR.Store():
                builder.memory.store(self.__resolve(builder, stmt.value), self.__resolve(builder, stmt.ptr))
            case IR.Malloc():
                key = self.__resolve(builder, stmt.key) if stmt.key is not None else None
                value = builder.allocation.malloc(stmt.type_id, self.__resolve(builder, stmt.size), key)
                self.__bind_result(stmt.result, value)
            case IR.Realloc():
                value = builder.allocation.realloc(
                    stmt.type_id,
                    self.__resolve(builder, stmt.ptr),
                    self.__resolve(builder, stmt.size),
                )
                self.__bind_result(stmt.result, value)
            case IR.Binary():
                value = builder.values.binary(
                    stmt.op, self.__resolve(builder, stmt.lhs), self.__resolve(builder, stmt.rhs)
                )
                self.__bind_result(stmt.result, value)
            case IR.Unary():
                value = builder.values.unary(stmt.op, self.__resolve(builder, stmt.operand))
                self.__bind_result(stmt.result, value)
            case IR.ExtractValue():
                value = builder.aggregates.extract_value(self.__resolve(builder, stmt.base), stmt.field_index)
                self.__bind_result(stmt.result, value)
            case IR.EnumIsVariant():
                value = builder.aggregates.enum_is_variant(self.__resolve(builder, stmt.address), stmt.variant)
                self.__bind_result(stmt.result, value)
            case IR.EnumPayloadFieldPtr():
                value = builder.aggregates.enum_payload_field_ptr(
                    self.__resolve(builder, stmt.address), stmt.payload_type, stmt.field_index,
                )
                self.__bind_result(stmt.result, value)
            case IR.Delete():
                builder.allocation.delete(self.__resolve(builder, stmt.ptr))
            case IR.GenKey():
                value = builder.safety.gen_key()
                self.__bind_result(stmt.result, value)
            case IR.AcquireFrameLock():
                value = builder.safety.acquire_frame_lock(self.__resolve(builder, stmt.key))
                self.__bind_result(stmt.result, value)
            case IR.CheckSafeAccess():
                builder.safety.check_safe_access(self.__resolve(builder, stmt.ptr), stmt.live)
            case IR.CheckViewAccess():
                builder.safety.check_view_access(self.__resolve(builder, stmt.view), stmt.live)
            case IR.CheckInBounds():
                builder.safety.check_in_bounds(self.__resolve(builder, stmt.ptr))
            case IR.CheckSliceNonEmpty():
                builder.safety.check_slice_nonempty(self.__resolve(builder, stmt.ptr))
            case IR.CheckRefAccess():
                builder.safety.check_ref_access(self.__resolve(builder, stmt.ptr))
            case IR.CheckElementArith():
                builder.safety.check_element_arith(self.__resolve(builder, stmt.base), self.__resolve(builder, stmt.offset))
            case IR.CheckElementAccess():
                builder.safety.check_element_access(
                    self.__resolve(builder, stmt.base),
                    self.__resolve(builder, stmt.offset),
                    self.__resolve(builder, stmt.ptr),
                    stmt.live,
                )
            case IR.CheckRawBounds():
                builder.safety.check_raw_bounds(self.__resolve(builder, stmt.index), stmt.length)
            case IR.CheckPtrDiff():
                builder.safety.check_ptrdiff(self.__resolve(builder, stmt.lhs), self.__resolve(builder, stmt.rhs))
            case IR.CheckPtrCmp():
                builder.safety.check_ptr_cmp(self.__resolve(builder, stmt.lhs), self.__resolve(builder, stmt.rhs))
            case IR.CheckDelete():
                builder.safety.check_delete(self.__resolve(builder, stmt.ptr))
            case IR.Call():
                value = builder.calls.call_func(
                    stmt.callee_type,
                    [self.__resolve(builder, a) for a in stmt.args if not self.__ll_type_ctx.is_zst(a.type_id)],
                    stmt.result.type_id
                )
                self.__bind_result(stmt.result, value)
            case IR.Invoke():
                value = builder.calls.call_value(
                    self.__resolve(builder, stmt.callee),
                    [self.__resolve(builder, a) for a in stmt.args if not self.__ll_type_ctx.is_zst(a.type_id)],
                    stmt.result.type_id
                )
                self.__bind_result(stmt.result, value)
            case IR.TraitObjectConstruct():
                value = builder.calls.trait_object_construct(
                    self.__resolve(builder, stmt.reference),
                    stmt.result.type_id,
                    stmt.concrete_type_id,
                    stmt.trait_type_id,
                )
                self.__bind_result(stmt.result, value)
            case IR.TraitObjectInvoke():
                value = builder.calls.trait_object_call(
                    self.__resolve(builder, stmt.receiver),
                    stmt.trait_type_id,
                    stmt.method_id,
                    stmt.slot_index,
                    [
                        self.__resolve(builder, argument)
                        for argument in stmt.args
                    ],
                    stmt.result.type_id,
                )
                self.__bind_result(stmt.result, value)
            case IR.Cast():
                value = builder.values.cast(self.__resolve(builder, stmt.value), stmt.to_type, stmt.raw)
                self.__bind_result(stmt.result, value)
            case IR.SizeOf():
                self.__bind_result(stmt.result, builder.sizeof_const(stmt.type_id))
            case IR.Undef():
                self.__bind_result(stmt.result, builder.undef(stmt.type_id))
            case IR.Dangling():
                self.__bind_result(stmt.result, builder.dangling_value(stmt.type_id))
            case IR.FuncPtr():
                value = builder.calls.func_ptr_by_type(stmt.func_type_id, stmt.result.type_id)
                self.__bind_result(stmt.result, value)
            case IR.AggregateConstruct():
                value = builder.aggregates.build(
                    stmt.result.type_id, [self.__resolve(builder, field) for field in stmt.fields]
                )
                self.__bind_result(stmt.result, value)
            case IR.ArrayConstruct():
                value = builder.aggregates.array(
                    stmt.result.type_id, [self.__resolve(builder, element) for element in stmt.elements]
                )
                self.__bind_result(stmt.result, value)
            case IR.VariantConstruct():
                fields = [self.__resolve(builder, f) for f in stmt.payload_fields] if stmt.payload_fields else None
                value = builder.aggregates.construct_enum_variant(
                    stmt.result.type_id, stmt.variant.discriminant, stmt.variant.payload_type, fields
                )
                self.__bind_result(stmt.result, value)
            case IR.SysWrite():
                builder.system.sys_write(self.__resolve(builder, stmt.fd), self.__resolve(builder, stmt.buf))
            case IR.SysWriteBytes():
                self.__bind_result(
                    stmt.result,
                    builder.system.sys_write_bytes(self.__resolve(builder, stmt.fd), self.__resolve(builder, stmt.buf)),
                )
            case IR.MemCopy():
                builder.memory.mem_copy(self.__resolve(builder, stmt.dest), self.__resolve(builder, stmt.src), self.__resolve(builder, stmt.count))
            case IR.FfiAddr():
                self.__bind_result(stmt.result, builder.ffi.addr(self.__resolve(builder, stmt.reference), stmt.result.type_id))
            case IR.FfiParts():
                self.__bind_result(stmt.result, builder.ffi.parts(self.__resolve(builder, stmt.view), stmt.result.type_id))
            case IR.FfiNull():
                self.__bind_result(stmt.result, builder.ffi.null(stmt.result.type_id))
            case IR.FfiPtrCast():
                self.__bind_result(stmt.result, builder.ffi.ptr_cast(self.__resolve(builder, stmt.pointer), stmt.result.type_id))
            case IR.FfiCopy():
                builder.ffi.copy(self.__resolve(builder, stmt.destination), self.__resolve(builder, stmt.source), self.__resolve(builder, stmt.count), stmt.from_c)
            case IR.MemSetPattern():
                builder.memory.mem_set_pattern(self.__resolve(builder, stmt.dest), self.__resolve(builder, stmt.value), self.__resolve(builder, stmt.count))
            case IR.SysRead():
                value = builder.system.sys_read(
                    self.__resolve(builder, stmt.fd), self.__resolve(builder, stmt.buf)
                )
                self.__bind_result(stmt.result, value)
            case IR.Open():
                value = builder.system.open(
                    self.__resolve(builder, stmt.path), self.__resolve(builder, stmt.flags)
                )
                self.__bind_result(stmt.result, value)
            case IR.Close():
                value = builder.system.close(self.__resolve(builder, stmt.fd))
                self.__bind_result(stmt.result, value)
            case IR.Sqrt():
                value = builder.system.sqrt(self.__resolve(builder, stmt.value))
                self.__bind_result(stmt.result, value)
            case IR.Sin():
                value = builder.system.sin(self.__resolve(builder, stmt.value))
                self.__bind_result(stmt.result, value)
            case IR.Cos():
                value = builder.system.cos(self.__resolve(builder, stmt.value))
                self.__bind_result(stmt.result, value)
            case IR.ArgCount():
                value = builder.system.arg_count()
                self.__bind_result(stmt.result, value)
            case IR.ArgBytes():
                value = builder.system.arg_bytes(self.__resolve(builder, stmt.index))
                self.__bind_result(stmt.result, value)

    # ------------------------------------------------------------------
    # terminators
    # ------------------------------------------------------------------

    def __terminator(self, builder: LLBuilder, terminator: IR.Terminator) -> None:
        match terminator:
            case IR.Ret(value=value):
                builder.safety.release_frame_lock()
                if self.__ll_type_ctx.is_zst(value.type_id):
                    builder.flow.ret(None)  # zero-sized return: `ret void`
                else:
                    returned = builder.pointers.promote_fat(self.__resolve(builder, value))
                    builder.flow.ret(returned.ir_val)
            case IR.Br(target=target):
                builder.flow.branch(target.label)
            case IR.CondBr(cond=cond, then_block=then_block, else_block=else_block):
                builder.flow.cond_branch(
                    self.__resolve(builder, cond).ir_val, then_block.label, else_block.label
                )
            case IR.Panic(message=message):
                builder.system.panic(self.__resolve(builder, message))
            case IR.RuntimeFail(code=code):
                builder.flow.runtime_fail(code)
            case IR.ProcessExit(code=code):
                builder.system.process_exit(self.__resolve(builder, code))
            case IR.Unreachable():
                builder.flow.builder.unreachable()
