"""表达式下降与 HIR 分派（HIR → CFG IR）。

原构建器的最后一簇：`resolve_val` 分派器与各表达式解析
（binary/logical/assign/compound_assign/unary/field_access/tuple_access/dyn_value/
dyn_buffer/alloc/var/literal/array_access/slice_access）。

它位于各簇之上：语句下降经 `StmtHost.resolve_val` 回调进来，本簇经 `ExprHost`
调用语句簇与值/内存/调用/系统四个下降簇——构建器只做装配。
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from compiler.frontend.parse.operator import BinaryOperator, UnaryOperator
from compiler.analysis.ty import ty as Type
from compiler.analysis.ty.type_ops import default_literals
from compiler.analysis.ty.context import TypeCtx
from compiler.analysis.unit import hir as HIR
from compiler.builtins import BuiltinKind
from compiler.codegen.cfg import ir as IR
from compiler.codegen.cfg.lower.cfg_ctx import CfgCtx
from compiler.codegen.cfg.lower.calls import CallsLowerer
from compiler.codegen.cfg.lower.memory import MemoryLowerer
from compiler.codegen.cfg.lower.stmts import StmtLowerer
from compiler.codegen.cfg.lower.sys import SysLowerer
from compiler.codegen.cfg.lower.values import ValueLowerer
from compiler.codegen.cfg.lower.checks import CheckState
from compiler.codegen.cfg.lower.emitter import FunctionEmitter
from compiler.codegen.error import CodegenError
from compiler.error import CompilerError
from compiler.utils.log import CompilerLog


def ch_cfg():
    """cfg.logical 日志通道（分派器的分支诊断）。"""
    return CompilerLog.get("cfg.logical")


@dataclass(frozen=True)
class ExprHost:
    """表达式下降向其下各簇与构建器借用的能力。"""

    emitter: FunctionEmitter
    checks: CheckState
    ctx: CfgCtx
    stmts: StmtLowerer
    values: ValueLowerer
    memory: MemoryLowerer
    calls: CallsLowerer
    sys: SysLowerer
    set_terminator: Callable[[IR.Terminator], None]
    switch_to: Callable[[IR.Block], None]


class ExprLowerer:
    """表达式下降器（含 HIR 分派器）。"""

    def __init__(self, host: ExprHost) -> None:
        self.__host = host
        self.__builtin_handlers: dict[BuiltinKind, Callable[[HIR.Builtin], IR.Value]] = {
            BuiltinKind.SizeOf: self.__resolve_size_of,
            BuiltinKind.Undef: self.__resolve_undef,
            BuiltinKind.Dangling: self.__resolve_dangling,
            BuiltinKind.BitCast: self.__resolve_bit_cast,
            BuiltinKind.Alloc: self.__resolve_alloc,
            BuiltinKind.Realloc: self.__resolve_realloc,
            BuiltinKind.Panic: self.__resolve_panic,
            BuiltinKind.RuntimeFail: self.__resolve_runtime_fail,
            BuiltinKind.AssumeInit: self.__resolve_assume_init,
            BuiltinKind.MemCopy: self.__resolve_mem_copy,
            BuiltinKind.SliceFromParts: self.__resolve_slice_from_parts,
            BuiltinKind.SliceGetPtr: self.__resolve_slice_get_ptr,
            BuiltinKind.SliceGetLen: self.__resolve_slice_get_len,
            BuiltinKind.StrFromParts: self.__resolve_str_from_parts,
            BuiltinKind.StrGetPtr: self.__resolve_str_get_ptr,
            BuiltinKind.StrGetLen: self.__resolve_str_get_len,
            BuiltinKind.SysRead: self.__resolve_sys_read,
            BuiltinKind.SysWrite: self.__resolve_sys_write,
            BuiltinKind.Open: self.__resolve_open,
            BuiltinKind.Close: self.__resolve_close,
            BuiltinKind.Sqrt: self.__resolve_sqrt,
            BuiltinKind.Sin: self.__resolve_sin,
            BuiltinKind.Cos: self.__resolve_cos,
            BuiltinKind.Argc: self.__resolve_argc,
            BuiltinKind.ArgBytes: self.__resolve_arg_bytes,
            BuiltinKind.Exit: self.__resolve_exit,
        }

    def resolve_val(self, expr: HIR.Expr) -> IR.Value:
        """Lower *expr* to a value."""
        match expr:
            # --- expression-oriented control flow ---
            case HIR.Block():
                return self.__host.stmts.translate_block(expr)
            case HIR.If():
                return self.__host.stmts.translate_if(expr)
            case HIR.ComptimeIf() | HIR.CompileConfig():
                raise CodegenError("compile-time conditional was not specialized before CFG lowering", expr.span)
            case HIR.Loop():
                return self.__host.stmts.translate_loop(expr)
            case HIR.Match():
                return self.__host.stmts.translate_match(expr)
            case HIR.Return():
                return self.__host.stmts.translate_return(expr)
            case HIR.Break():
                return self.__host.stmts.translate_break(expr)
            case HIR.Continue():
                return self.__host.stmts.translate_continue(expr)
            case HIR.Defer():
                return self.__host.stmts.translate_defer(expr)
            case HIR.Delete():
                return self.__host.stmts.translate_delete(expr)
            case HIR.Semi():
                return self.__host.stmts.translate_semi(expr)
            case HIR.Let():
                return self.__host.stmts.translate_let(expr)
            # --- original expressions ---
            case HIR.Binary():
                return self.resolve_binary(expr)
            case HIR.Unary():
                return self.resolve_unary(expr)
            case HIR.Call():
                return self.__host.calls.resolve_call(expr)
            case HIR.StructConstruct():
                return self.__host.values.resolve_struct_construct(expr)
            case HIR.Invoke():
                return self.__host.calls.resolve_invoke(expr)
            case HIR.Cast():
                return self.__host.values.resolve_cast(expr)
            case HIR.MethodCall():
                return self.__host.calls.resolve_method_call(expr)
            case HIR.VariantConstruct():
                return self.__host.values.resolve_variant_construct(expr)
            case HIR.FieldAccess():
                return self.resolve_field_access(expr)
            case HIR.TupleAccess():
                return self.resolve_tuple_access(expr)
            case HIR.ArrayAccess():
                return self.resolve_array_access(expr)
            case HIR.SliceAccess():
                return self.resolve_slice_access(expr)
            case HIR.DynValue():
                return self.resolve_dyn_value(expr)
            case HIR.DynBuffer():
                return self.resolve_dyn_buffer(expr)
            case HIR.BitCast():
                return self.__host.values.resolve_bit_cast(expr)
            case HIR.Builtin():
                return self.resolve_builtin(expr)
            case HIR.Tuple():
                return self.__host.values.resolve_tuple(expr)
            case HIR.Array():
                return self.__host.values.resolve_array(expr)
            case HIR.ArrayRepeat():
                return self.__host.values.resolve_array_repeat(expr)
            case HIR.Var():
                return self.resolve_var(expr)
            case HIR.IntLiteral() | HIR.FloatLiteral() | HIR.CharLiteral() | HIR.BoolLiteral() | HIR.StrLiteral():
                return self.resolve_literal(expr)
            case HIR.Ty():
                if self.__host.ctx.type_ctx.is_zst(expr.type_id):
                    return IR.Reg(name=self.__host.emitter.new_name(), type_id=expr.type_id)
                raise CodegenError(f"Cannot resolve type expression: {expr}", expr.span)
            case HIR.Closure():
                raise CompilerError(f"Closure lowering should have been completed before CFG building: {expr}")
    def resolve_binary(self, expr: HIR.Binary) -> IR.Value:
        """
        Special cases:

        - logical operators should implement short-circuit evaluation
        - assignment => load address of lhs, load value from rhs, store value to lhs
        - compound assignments should be implemented as binary operation + store
        - `in` and `..` should not be handled here
        """
        if expr.op.is_logical():
            return self.resolve_logical(expr)
        if expr.op == BinaryOperator.Assign:
            return self.resolve_assign(expr)
        if expr.op.is_compound_assign():
            return self.resolve_compound_assign(expr)
        if expr.op in (BinaryOperator.In, BinaryOperator.Range):
            raise CodegenError(f"Cannot resolve expression: {expr}", expr.span)

        # common case
        lhs = self.resolve_val(expr.left)
        rhs = self.resolve_val(expr.right)
        return self.__host.memory.build_binary(expr.op, lhs, rhs, expr.type_id)
    def resolve_compound_assign(self, expr: HIR.Binary) -> IR.Value:
        op = expr.op.compound_assign_to_binary()

        lhs_addr = self.__host.values.resolve_addr(expr.left)
        lhs_val = self.__host.memory.build_load(lhs_addr)
        rhs_val = self.resolve_val(expr.right)

        result = self.__host.memory.build_binary(op, lhs_val, rhs_val, expr.type_id)
        self.__host.memory.build_store(result, lhs_addr)
        return result
    def resolve_assign(self, expr: HIR.Binary) -> IR.Value:
        lhs_addr = self.__host.values.resolve_addr(expr.left)
        rhs_val = self.resolve_val(expr.right)
        if rhs_val.type_id != self.__host.ctx.type_ctx.never_id:
            self.__host.memory.build_store(rhs_val, lhs_addr)
        return rhs_val
    def resolve_logical(self, expr: HIR.Binary) -> IR.Value:
        """Lower short-circuit logical operators (&&, ||).

        For ``a && b``::

            br %a, rhs, merge      ; if false → short-circuit to false
          rhs:
            %b = resolve(b)
            br merge
          merge:
            %r = phi [(entry, false), (rhs, %b)]

        For ``a || b``::

            br %a, merge, rhs      ; if true → short-circuit to true
          rhs:
            %b = resolve(b)
            br merge
          merge:
            %r = phi [(entry, true), (rhs, %b)]
        """
        ch_cfg().trace(lambda: f"logical {expr.op} at {expr.span}")
        cond_val = self.resolve_val(expr.left)

        rhs_block = self.__host.emitter.new_block("logical.rhs")
        merge_block = self.__host.emitter.new_block("logical.merge")
        entry_block = self.__host.emitter.current_block

        if expr.op == BinaryOperator.LogicalAnd:
            # a && b: evaluate b only when a is true
            self.__host.set_terminator(IR.CondBr(cond_val, rhs_block, merge_block))
            short_circuit_value: IR.Value = IR.BoolLiteral(value=False, type_id=TypeCtx.bool_id)
        else:
            # a || b: evaluate b only when a is false
            self.__host.set_terminator(IR.CondBr(cond_val, merge_block, rhs_block))
            short_circuit_value = IR.BoolLiteral(value=True, type_id=TypeCtx.bool_id)

        # ── rhs block ──
        self.__host.switch_to(rhs_block)
        rhs_val = self.resolve_val(expr.right)
        # __resolve_val may have switched current_block (nested logical).
        # The block that actually produced rhs_val is where we ended up.
        rhs_end_block = self.__host.emitter.current_block

        # Bridge rhs_end_block to merge if it doesn't already have a terminator.
        if rhs_end_block.terminator is None:
            self.__host.set_terminator(IR.Br(merge_block))

        # ── merge block ──
        self.__host.switch_to(merge_block)
        result = self.__host.emitter.emit_phi([
            (entry_block, short_circuit_value),
            (rhs_end_block, rhs_val),
        ])
        # The merge block needs a terminator so it is not left dangling.
        # Create a continuation block that callers can append to.
        cont_block = self.__host.emitter.new_block("logical.cont")
        self.__host.set_terminator(IR.Br(cont_block))
        self.__host.switch_to(cont_block)
        return result
    def resolve_unary(self, expr: HIR.Unary) -> IR.Value:
        """
        Special cases:

        - dereference => resolve pointer value then load through it
        - addr of lvalue => get address
        """
        if expr.op == UnaryOperator.Deref:
            ptr_val = self.resolve_val(expr.operand)
            return self.__host.memory.build_load(ptr_val)
        if expr.op == UnaryOperator.AddrOf:
            return self.__host.values.resolve_addr_fat(expr.operand)

        # common case
        val = self.resolve_val(expr.operand)
        return self.__host.memory.build_unary(expr.op, val, expr.type_id)
    def resolve_field_access(self, expr: HIR.FieldAccess) -> IR.Value:
        if expr.receiver.is_place:
            addr = self.__host.values.resolve_field_access_addr(expr)
            return self.__host.memory.build_load(addr)

        value = self.resolve_val(expr.receiver)
        return self.__host.memory.build_extract_value(value, expr.field.index, expr.type_id)
    def resolve_tuple_access(self, expr: HIR.TupleAccess) -> IR.Value:
        if expr.receiver.is_place:
            receiver_ty = self.__host.ctx.type_ctx[expr.receiver.type_id]
            if isinstance(receiver_ty, (Type.SliceType, Type.StrType)):
                # 分级指针表示:slice/str 字段须从值提取(fieldptr 只能取裸字段地址,
                # 无法携带锁元数据)。读整个值再 extract_value。
                value = self.resolve_val(expr.receiver)
                return self.__host.memory.build_extract_value(value, expr.index, expr.type_id)
            addr = self.__host.values.resolve_tuple_access_addr(expr)
            return self.__host.memory.build_load(addr)

        value = self.resolve_val(expr.receiver)
        return self.__host.memory.build_extract_value(value, expr.index, expr.type_id)
    def resolve_dyn_value(self, expr: HIR.DynValue) -> IR.Value:
        """
        1. malloc a buffer on the heap
        2. write the value to the buffer
        """
        size = IR.IntLiteral(1, type_id=TypeCtx.u64_id)
        value = self.resolve_val(expr.value)
        buffer = self.__host.memory.build_malloc(value.type_id, size)
        self.__host.memory.build_store(value, buffer)
        return buffer
    def resolve_dyn_buffer(self, expr: HIR.DynBuffer) -> IR.Value:
        """``dyn[n] value``: allocate ``n`` elements and bit-copy ``value`` into each.

        The initializer is evaluated exactly once before the fill operation;
        ``element is None`` is the ZST-only bare form, which has nothing to
        initialize. The typed pattern intrinsic accepts a runtime ``n``.
        """
        count = self.resolve_val(expr.length)
        buffer = self.__host.memory.build_malloc(expr.element_type, count)
        if expr.element is None:
            return buffer
        value = self.resolve_val(expr.element)
        if self.__host.ctx.type_ctx.is_zst(expr.element_type):
            return buffer

        # Malloc lowering can split its CFG block for allocation checks, so isolate the
        # fill in a fresh block; subsequent branches then use the actual LLVM predecessor.
        pattern_block = self.__host.emitter.new_block("dyn.fill.pattern")
        self.__host.set_terminator(IR.Br(pattern_block))
        self.__host.switch_to(pattern_block)
        self.__host.emitter.emit(IR.MemSetPattern(dest=buffer, value=value, count=count))
        return buffer

    def resolve_builtin(self, expr: HIR.Builtin) -> IR.Value:
        handler = self.__builtin_handlers.get(expr.kind)
        if handler is None:
            raise CodegenError(f"unregistered builtin instruction '{expr.kind.spelling}'", expr.span)
        return handler(expr)

    def __resolve_size_of(self, expr: HIR.Builtin) -> IR.Value:
        return self.__host.values.resolve_size_of(expr)

    def __resolve_undef(self, expr: HIR.Builtin) -> IR.Value:
        return self.__host.values.resolve_undef(expr)

    def __resolve_dangling(self, expr: HIR.Builtin) -> IR.Value:
        return self.__host.values.resolve_dangling(expr)

    def __resolve_bit_cast(self, expr: HIR.Builtin) -> IR.Value:
        return self.__host.values.resolve_bit_cast(expr)

    def __resolve_alloc(self, expr: HIR.Builtin) -> IR.Value:
        return self.__host.memory.build_malloc(expr.type_args[0], self.resolve_val(expr.args[0]))

    def __resolve_realloc(self, expr: HIR.Builtin) -> IR.Value:
        return self.__host.memory.build_realloc(
            expr.type_args[0], self.resolve_val(expr.args[0]), self.resolve_val(expr.args[1]),
        )

    def __resolve_panic(self, expr: HIR.Builtin) -> IR.Value:
        return self.__host.stmts.translate_panic(expr)

    def __resolve_runtime_fail(self, expr: HIR.Builtin) -> IR.Value:
        return self.__host.stmts.translate_runtime_fail(expr)

    def __resolve_assume_init(self, expr: HIR.Builtin) -> IR.Value:
        return self.resolve_val(expr.args[0])

    def __resolve_mem_copy(self, expr: HIR.Builtin) -> IR.Value:
        return self.__host.sys.resolve_mem_copy(expr)

    def __resolve_slice_from_parts(self, expr: HIR.Builtin) -> IR.Value:
        aggregate = HIR.Tuple(span=expr.span, field_values=expr.args, type_id=expr.type_id, is_place=False)
        return self.__host.values.resolve_tuple(aggregate)

    def __resolve_slice_get_ptr(self, expr: HIR.Builtin) -> IR.Value:
        value = self.resolve_val(expr.args[0])
        return self.__host.memory.build_extract_value(value, 0, expr.type_id)

    def __resolve_slice_get_len(self, expr: HIR.Builtin) -> IR.Value:
        value = self.resolve_val(expr.args[0])
        return self.__host.memory.build_extract_value(value, 3, expr.type_id)

    def __resolve_str_from_parts(self, expr: HIR.Builtin) -> IR.Value:
        aggregate = HIR.Tuple(span=expr.span, field_values=expr.args, type_id=expr.type_id, is_place=False)
        return self.__host.values.resolve_tuple(aggregate)

    def __resolve_str_get_ptr(self, expr: HIR.Builtin) -> IR.Value:
        value = self.resolve_val(expr.args[0])
        return self.__host.memory.build_extract_value(value, 0, expr.type_id)

    def __resolve_str_get_len(self, expr: HIR.Builtin) -> IR.Value:
        value = self.resolve_val(expr.args[0])
        return self.__host.memory.build_extract_value(value, 3, expr.type_id)

    def __resolve_sys_read(self, expr: HIR.Builtin) -> IR.Value:
        return self.__host.sys.resolve_sys_read(expr)

    def __resolve_sys_write(self, expr: HIR.Builtin) -> IR.Value:
        return self.__host.sys.resolve_sys_write(expr)

    def __resolve_open(self, expr: HIR.Builtin) -> IR.Value:
        return self.__host.sys.resolve_open(expr)

    def __resolve_close(self, expr: HIR.Builtin) -> IR.Value:
        return self.__host.sys.resolve_close(expr)

    def __resolve_sqrt(self, expr: HIR.Builtin) -> IR.Value:
        return self.__host.sys.resolve_sqrt(expr)

    def __resolve_sin(self, expr: HIR.Builtin) -> IR.Value:
        return self.__host.sys.resolve_sin(expr)

    def __resolve_cos(self, expr: HIR.Builtin) -> IR.Value:
        return self.__host.sys.resolve_cos(expr)

    def __resolve_argc(self, expr: HIR.Builtin) -> IR.Value:
        return self.__host.sys.resolve_arg_count(expr)

    def __resolve_arg_bytes(self, expr: HIR.Builtin) -> IR.Value:
        return self.__host.sys.resolve_arg_bytes(expr)

    def __resolve_exit(self, expr: HIR.Builtin) -> IR.Value:
        return self.__host.stmts.translate_process_exit(expr)

    def resolve_var(self, expr: HIR.Var) -> IR.Value:
        addr = self.__host.values.resolve_var_addr(expr)
        return self.__host.memory.build_load(addr)
    def resolve_literal(self, expr: HIR.Literal) -> IR.Value:
        match expr:
            case HIR.IntLiteral():
                type_id = default_literals(self.__host.ctx.type_ctx, expr.type_id)
                return IR.IntLiteral(value=expr.value, type_id=type_id)
            case HIR.FloatLiteral():
                type_id = default_literals(self.__host.ctx.type_ctx, expr.type_id)
                return IR.FloatLiteral(value=expr.value, type_id=type_id)
            case HIR.CharLiteral():
                return IR.CharLiteral(value=expr.value, type_id=TypeCtx.char_id)
            case HIR.BoolLiteral():
                return IR.BoolLiteral(value=expr.value, type_id=TypeCtx.bool_id)
            case HIR.StrLiteral():
                return IR.StringLiteral(value=expr.value, type_id=TypeCtx.str_id)

    # ------------------------------------------------------------------
    # addr resolvors
    # ------------------------------------------------------------------
    def resolve_array_access(self, expr: HIR.ArrayAccess) -> IR.Value:
        addr = self.__host.values.resolve_array_access_addr(expr)
        return self.__host.memory.build_load(addr)
    def resolve_slice_access(self, expr: HIR.SliceAccess) -> IR.Value:
        addr = self.__host.values.resolve_slice_access_addr(expr)
        return self.__host.memory.build_load(addr)
