"""
CFG → LLVM IR translator.
"""

from __future__ import annotations

from llvmlite import ir

from compiler.analysis.ty.context import TypeCtx
from compiler.codegen.cfg import ir as IR
from compiler.codegen.llvm.builder import BuilderPosition, LLBuilder
from compiler.codegen.llvm.module import LLFunction, LLModule
from compiler.codegen.llvm.types import LLTypeCtx


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

    def run(self, functions: dict[str, IR.Function]) -> None:
        for function in functions.values():
            self.__module.declare(function)
        for function in functions.values():
            self.__build(function)

    def export(self) -> LLModule:
        return self.__module

    # ------------------------------------------------------------------
    # build
    # ------------------------------------------------------------------

    def __build(self, cfg: IR.Function) -> None:
        self.__func = self.__module.get_func(cfg.type_id)
        func = self.__func

        func.add_entry_block()
        builder = LLBuilder(func, self.__module, self.__ll_type_ctx, self.__type_ctx)

        # entry block + local var allocas
        builder.position_at(cfg.entry.label, where=BuilderPosition.First)
        for symbol_id, var_ref in cfg.local_vars.items():
            func.set_alloca(symbol_id, builder.alloca(var_ref.type_id))

        # store params
        builder.position_at(cfg.entry.label, where=BuilderPosition.End)
        func.store_params(cfg.params, builder)

        # create blocks
        for block in cfg.blocks:
            if block.label == cfg.entry.label:
                func.add_block(block.label, func.entry_block)
            else:
                func.add_block(block.label, func.new_block(block.label))

        # translate
        for block in cfg.blocks:
            # phi
            builder.position_at(block.label, where=BuilderPosition.Phi)
            for phi in block.phis:
                builder.phi(phi.result.type_id, phi.incoming, phi.result.name)

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
        match stmt:
            case IR.VarPtr():
                builder.var_ptr(stmt.var_ref.symbol_id, stmt.result.name)
            case IR.Alloca():
                builder.alloca_store(stmt.value, stmt.result.name)
            case IR.FieldPtr():
                builder.gep(stmt.base, [0, stmt.field_index], stmt.result.name)
            case IR.Load():
                builder.load(stmt.ptr, stmt.result.name)
            case IR.Store():
                builder.store(stmt.value, stmt.ptr)
            case IR.Malloc():
                builder.malloc(stmt.type_id, stmt.size, stmt.result.name)
            case IR.Binary():
                builder.binary(stmt.op, stmt.lhs, stmt.rhs, stmt.result.name)
            case IR.Unary():
                builder.unary(stmt.op, stmt.operand, stmt.result.name)
            case IR.ExtractValue():
                builder.extract_value(stmt.base, stmt.field_index, stmt.result.name)
            case IR.Delete():
                builder.delete(stmt.ptr)
            case IR.Call():
                builder.call_func(stmt.callee_type, stmt.args, stmt.result.name)
            case IR.Invoke():
                builder.call_value(stmt.callee, stmt.args, stmt.result.name)
            case IR.Cast():
                builder.cast(stmt.value, stmt.to_type, stmt.result.name)
            case IR.SizeOf():
                builder.sizeof_const(stmt.type_id, stmt.result.name)
            case IR.FuncPtr():
                builder.func_ptr_by_type(stmt.func_type_id, stmt.result.name)
            case IR.AggregateConstruct():
                builder.aggregate(stmt.result.type_id, stmt.fields, stmt.result.name)
            case IR.ArrayConstruct():
                builder.array(stmt.result.type_id, stmt.elements, stmt.result.name)
            case IR.VariantConstruct():
                builder.variant(stmt, stmt.result.name)
            case IR.SysWrite():
                builder.sys_write(stmt.fd, stmt.buf)
            case IR.SysRead():
                builder.sys_read(stmt.fd, stmt.buf, stmt.result.name)

    # ------------------------------------------------------------------
    # terminators
    # ------------------------------------------------------------------

    def __terminator(self, builder: LLBuilder, terminator: IR.Terminator) -> None:
        match terminator:
            case IR.Ret(value=value):
                builder.ret(value)
            case IR.RetVoid():
                builder.ret(None)
            case IR.Br(target=target):
                builder.br(target.label)
            case IR.CondBr(cond=cond, then_block=then_block, else_block=else_block):
                builder.condbr(cond, then_block.label, else_block.label)
            case IR.Panic(message=message):
                builder.panic(message)
            case IR.Match():
                builder.match(terminator)
