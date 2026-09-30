"""Statement and control-flow lowering from HIR to CFG."""
from __future__ import annotations

from dataclasses import dataclass, field

from compiler.analysis.ty.context import TypeCtx
from compiler.analysis.ty import ty as Type
from compiler.analysis.unit import hir as HIR
from compiler.codegen.cfg import ir as IR
from compiler.codegen.cfg.lower.resolver import ExprResolver
from compiler.codegen.cfg.lower.values import ValueLowerer
from compiler.codegen.cfg.lower.memory import MemoryOps
from compiler.codegen.cfg.lower.state import FunctionState
from compiler.codegen.error import CodegenError
from compiler.runtime_error import parse_runtime_error_code
from compiler.frontend.parse.operator import BinaryOperator


@dataclass
class LoopCtx:
    header: IR.Block
    exit: IR.Block
    # Number of active defer scopes outside this loop.  Break/continue clean
    # up scopes introduced by the current iteration, but leave outer scopes
    # for the enclosing control-flow construct to handle.
    defer_depth: int
    break_values: list[tuple[IR.Block, IR.Value]] = field(default_factory=list[tuple[IR.Block, IR.Value]])


class StmtLowerer:
    """语句/控制流下降器：持有循环栈与 defer 作用域栈。"""

    def __init__(self, state: FunctionState, resolver: ExprResolver, values: ValueLowerer, memory: MemoryOps) -> None:
        self.__state = state
        self.__resolver = resolver
        self.__values = values
        self.__memory = memory
        self.__loops: list[LoopCtx] = []
        self.__defer_scopes: list[list[HIR.Expr]] = []

    def run(self, block: HIR.Block) -> IR.Value:
        """下降一个函数体块（构建器的入口）。"""
        return self.translate_block(block)

    def translate_block(self, block: HIR.Block) -> IR.Value:
        """Translate a block, returning the value of the last expression."""
        last_val: IR.Value = self.__state.emitter.void_reg()
        self.__defer_scopes.append([])
        try:
            for stmt in block.stmts:
                last_val = self.__resolver.resolve_val(stmt)
                if self.__state.emitter.current_block.terminator is not None:
                    # Mid-block terminator (return/break/continue/panic) → divergent.
                    return last_val

            # Preserve the block's tail value while expanding its deferred
            # actions in reverse registration order.
            self.emit_defers_to(len(self.__defer_scopes) - 1)
            return last_val
        finally:
            self.__defer_scopes.pop()

    def translate_defer(self, stmt: HIR.Defer) -> IR.Value:
        """Register a deferred action; the action is lowered at scope exit."""
        if not self.__defer_scopes:
            raise CodegenError("defer action is outside a lexical block", stmt.span)
        self.__defer_scopes[-1].append(stmt.action)
        return self.__state.emitter.void_reg()

    def emit_defers_to(self, depth: int) -> bool:
        """Emit active deferred actions down to *depth* (exclusive).

        The HIR is static and may be reached by multiple CFG paths, so scopes
        are not popped here; each path emits its own reverse-order sequence.
        Returns ``False`` when an action terminated the current block.
        """
        for scope_index in range(len(self.__defer_scopes) - 1, depth - 1, -1):
            for action in reversed(self.__defer_scopes[scope_index]):
                self.__resolver.resolve_val(action)
                if self.__state.emitter.current_block.terminator is not None:
                    return False
        return True

    # ------------------------------------------------------------------
    # expression handlers
    # ------------------------------------------------------------------

    def translate_return(self, stmt: HIR.Return) -> IR.Value:
        val = self.__resolver.resolve_val(stmt.value) if stmt.value is not None else self.__state.emitter.void_reg()
        if self.__state.emitter.current_block.terminator is not None:
            return self.__state.emitter.never_reg()
        self.emit_defers_to(0)
        if self.__state.emitter.current_block.terminator is None:
            self.__state.emitter.terminate(IR.Ret(val))
        return self.__state.emitter.never_reg()

    def translate_if(self, stmt: HIR.If) -> IR.Value:
        cond_val = self.__resolver.resolve_val(stmt.cond)

        then_block = self.__state.emitter.new_block("if.then")
        else_block = self.__state.emitter.new_block("if.else") if stmt.else_branch else None
        merge_block = self.__state.emitter.new_block("if.merge")

        self.__state.emitter.terminate(IR.CondBr(cond_val, then_block, else_block or merge_block))

        self.__state.emitter.position(then_block)
        then_val = self.translate_block(stmt.then_branch)
        then_reaches_merge = self.__state.emitter.current_block.terminator is None
        if then_reaches_merge:
            self.__state.emitter.terminate(IR.Br(merge_block))
        then_end = self.__state.emitter.current_block
        incoming: list[tuple[IR.Block, IR.Value]] = []
        if then_reaches_merge:
            incoming.append((then_end, then_val))

        if stmt.else_branch is not None:
            assert else_block is not None
            self.__state.emitter.position(else_block)
            else_val = self.translate_block(stmt.else_branch)
            if self.__state.emitter.current_block.terminator is None:
                self.__state.emitter.terminate(IR.Br(merge_block))
                incoming.append((self.__state.emitter.current_block, else_val))

        self.__state.emitter.position(merge_block)

        if not incoming:
            # All branches diverge — no phi needed.
            if stmt.type_id == TypeCtx.void_id:
                return self.__state.emitter.void_reg()
            return self.__state.emitter.never_reg()

        phi = self.__state.emitter.emit_phi(incoming)
        phi.type_id = stmt.type_id
        return phi

    def translate_loop(self, stmt: HIR.Loop) -> IR.Value:
        body_block = self.__state.emitter.new_block("loop.body")
        exit_block = self.__state.emitter.new_block("loop.exit")

        self.__state.emitter.terminate(IR.Br(body_block))
        self.__loops.append(LoopCtx(
            header=body_block,
            exit=exit_block,
            defer_depth=len(self.__defer_scopes),
        ))

        self.__state.emitter.position(body_block)
        self.translate_block(stmt.body)
        if self.__state.emitter.current_block.terminator is None:
            self.__state.emitter.terminate(IR.Br(body_block))

        self.__state.emitter.position(exit_block)

        # Build phi from break values (if any non-divergent breaks)
        loop = self.__loops.pop()
        if loop.break_values:
            phi = self.__state.emitter.emit_phi(loop.break_values)
            phi.type_id = stmt.type_id
            return phi
        if stmt.type_id == TypeCtx.void_id:
            return self.__state.emitter.void_reg()
        return self.__state.emitter.never_reg()

    def translate_panic(self, stmt: HIR.Builtin) -> IR.Value:
        msg_val = self.__resolver.resolve_val(stmt.args[0])
        self.__state.emitter.terminate(IR.Panic(msg_val))
        return self.__state.emitter.never_reg()

    def translate_runtime_fail(self, stmt: HIR.Builtin) -> IR.Value:
        arg = stmt.args[0]
        if not isinstance(arg, HIR.StrLiteral):
            raise CodegenError("runtime error code must be a string literal", stmt.span)
        code = parse_runtime_error_code(arg.value)
        if code is None:
            raise CodegenError(f"unknown runtime error code '{arg.value}'", stmt.span)
        self.__state.emitter.terminate(IR.RuntimeFail(code))
        return self.__state.emitter.never_reg()

    def translate_process_exit(self, stmt: HIR.Builtin) -> IR.Value:
        code = self.__resolver.resolve_val(stmt.args[0])
        self.__state.emitter.terminate(IR.ProcessExit(code=code))
        return self.__state.emitter.never_reg()

    def translate_delete(self, stmt: HIR.Delete) -> IR.Value:
        ptr = self.__resolver.resolve_val(stmt.target)
        # Delete 四前提 is_heap(p) ∧ live(p) ∧ is_raw(p) 与其后的检查状态失效由
        # 检查插入 pass 在 `IR.Delete` 处依目标形态决定；这里只发释放节点。
        # 整块交还——LLVM 层的 free() 提取 data 字段(释放范围 = 整块以 word 中的锁表索引寻址)
        self.__state.emitter.emit(IR.Delete(ptr))
        return self.__state.emitter.void_reg()

    def translate_match(self, stmt: HIR.Match) -> IR.Value:
        """Test arms in source order against one materialized scrutinee."""
        value = self.__resolver.resolve_val(stmt.value)
        address = value if stmt.is_ref else self.__values.build_alloca(value, fat=False)
        merge_block = self.__state.emitter.new_block("match.merge")
        incoming: list[tuple[IR.Block, IR.Value]] = []
        for arm in stmt.arms:
            next_block = self.__state.emitter.new_block("match.next")
            self.__emit_pattern(arm.pattern, address, next_block, stmt.is_ref)
            if arm.guard is not None:
                guard = self.__resolver.resolve_val(arm.guard)
                body_block = self.__state.emitter.new_block("match.body")
                if self.__state.emitter.current_block.terminator is None:
                    self.__state.emitter.terminate(IR.CondBr(guard, body_block, next_block))
                self.__state.emitter.position(body_block)
            result = self.translate_block(arm.body)
            if self.__state.emitter.current_block.terminator is None:
                self.__state.emitter.terminate(IR.Br(merge_block))
                incoming.append((self.__state.emitter.current_block, result))
            self.__state.emitter.position(next_block)
        self.__state.emitter.terminate(IR.Unreachable())
        self.__state.emitter.position(merge_block)
        if not incoming:
            self.__state.emitter.terminate(IR.Unreachable())
            return self.__state.emitter.never_reg()
        phi = self.__state.emitter.emit_phi(incoming)
        phi.type_id = stmt.type_id
        return phi

    def translate_break(self, stmt: HIR.Break) -> IR.Value:
        loop = self.__loops[-1]
        val: IR.Value | None = None
        if stmt.value is not None:
            val = self.__resolver.resolve_val(stmt.value)
            if self.__state.emitter.current_block.terminator is not None:
                return self.__state.emitter.never_reg()
        if not self.emit_defers_to(loop.defer_depth):
            return self.__state.emitter.never_reg()
        if val is not None:
            loop.break_values.append((self.__state.emitter.current_block, val))
        self.__state.emitter.terminate(IR.Br(loop.exit))
        return self.__state.emitter.never_reg()

    def translate_continue(self, _stmt: HIR.Continue) -> IR.Value:
        loop = self.__loops[-1]
        if not self.emit_defers_to(loop.defer_depth):
            return self.__state.emitter.never_reg()
        self.__state.emitter.terminate(IR.Br(loop.header))
        return self.__state.emitter.never_reg()

    def translate_semi(self, stmt: HIR.Semi) -> IR.Value:
        self.__resolver.resolve_val(stmt.expr)
        if stmt.type_id == TypeCtx.never_id:
            return self.__state.emitter.never_reg()
        return self.__state.emitter.void_reg()

    def translate_let(self, stmt: HIR.Let) -> IR.Value:
        if stmt.init is not None:
            self.__resolver.resolve_val(stmt.init)
        return self.__state.emitter.void_reg()

    def translate_pattern_let(self, stmt: HIR.PatternLet) -> IR.Value:
        value = self.__resolver.resolve_val(stmt.value)
        address = self.__values.build_alloca(value, fat=False)
        failure = self.__state.emitter.new_block("pattern.let.fail")
        self.__emit_pattern(stmt.pattern, address, failure, False)
        success = self.__state.emitter.current_block
        self.__state.emitter.position(failure)
        if stmt.else_branch is not None:
            self.translate_block(stmt.else_branch)
        if self.__state.emitter.current_block.terminator is None:
            self.__state.emitter.terminate(IR.Unreachable())
        self.__state.emitter.position(success)
        return self.__state.emitter.void_reg()

    # ------------------------------------------------------------------
    # Match helpers
    # ------------------------------------------------------------------

    def __branch_on(self, condition: IR.Value, failure: IR.Block) -> None:
        success = self.__state.emitter.new_block("match.test.ok")
        self.__state.emitter.terminate(IR.CondBr(condition, success, failure))
        self.__state.emitter.position(success)

    def __compare(self, op: BinaryOperator, left: IR.Value, right: IR.Value) -> IR.Value:
        result = IR.Reg(self.__state.emitter.new_name(), TypeCtx.bool_id)
        return self.__state.emitter.emit(IR.Binary(result, op, left, right)).result

    def __store_pattern_binding(self, symbol_id: int, value: IR.Value) -> None:
        var_ref = self.__state.emitter.func.local_vars[symbol_id]
        ptr = self.__values.build_var_ptr_raw(var_ref)
        self.__memory.build_store(value, ptr)

    def __emit_pattern(
        self, pattern: HIR.Pattern, address: IR.Value, failure: IR.Block, by_ref: bool,
    ) -> None:
        ctx = self.__state.session.type_ctx
        match pattern:
            case HIR.WildcardPattern():
                return
            case HIR.BindPattern():
                self.__emit_pattern(pattern.inner, address, failure, by_ref)
                bound = self.__values.build_cast(address, ctx.alloc_ref(pattern.type_id)) if by_ref else self.__memory.build_load(address)
                self.__store_pattern_binding(pattern.symbol_id, bound)
            case HIR.RefPattern():
                self.__emit_pattern(pattern.inner, self.__memory.build_load(address), failure, True)
            case HIR.OrPattern():
                done = self.__state.emitter.new_block("match.or.done")
                for alternative in pattern.alternatives:
                    next_alt = self.__state.emitter.new_block("match.or.next")
                    self.__emit_pattern(alternative, address, next_alt, by_ref)
                    self.__state.emitter.terminate(IR.Br(done))
                    self.__state.emitter.position(next_alt)
                self.__state.emitter.terminate(IR.Br(failure))
                self.__state.emitter.position(done)
            case HIR.LiteralPattern():
                value = self.__memory.build_load(address)
                if pattern.condition is not None:
                    assert pattern.condition_symbol is not None
                    self.__store_pattern_binding(pattern.condition_symbol, value)
                    condition = self.__resolver.resolve_val(pattern.condition)
                elif isinstance(pattern.value, bool):
                    condition = self.__compare(BinaryOperator.Eq, value, IR.BoolLiteral(pattern.value, TypeCtx.bool_id))
                elif isinstance(pattern.value, int):
                    condition = self.__compare(BinaryOperator.Eq, value, IR.IntLiteral(pattern.value, pattern.type_id))
                else:
                    condition = self.__compare(BinaryOperator.Eq, value, IR.CharLiteral(pattern.value, TypeCtx.char_id))
                self.__branch_on(condition, failure)
            case HIR.RangePattern():
                value = self.__memory.build_load(address)
                ty = ctx[pattern.type_id]
                lower: IR.Value = IR.CharLiteral(chr(pattern.lower), TypeCtx.char_id) if isinstance(ty, Type.CharType) else IR.IntLiteral(pattern.lower, pattern.type_id)
                upper: IR.Value = IR.CharLiteral(chr(pattern.upper), TypeCtx.char_id) if isinstance(ty, Type.CharType) else IR.IntLiteral(pattern.upper, pattern.type_id)
                self.__branch_on(self.__compare(BinaryOperator.Geq, value, lower), failure)
                self.__branch_on(self.__compare(BinaryOperator.Lt, self.__memory.build_load(address), upper), failure)
            case HIR.EnumPattern():
                is_variant = IR.Reg(self.__state.emitter.new_name(), TypeCtx.bool_id)
                condition = self.__state.emitter.emit(IR.EnumIsVariant(is_variant, address, pattern.variant)).result
                self.__branch_on(condition, failure)
                if pattern.fields is not None:
                    assert pattern.variant.payload_type is not None
                    fields = ctx.get_struct_fields(pattern.variant.payload_type)
                    for index, sub in pattern.fields:
                        ptr_type = ctx.alloc_pointer(fields[index].type_id)
                        ptr = IR.Reg(self.__state.emitter.new_name(), ptr_type)
                        field_addr = self.__state.emitter.emit(IR.EnumPayloadFieldPtr(
                            ptr, address, pattern.variant.payload_type, index,
                        )).result
                        self.__emit_pattern(sub, field_addr, failure, by_ref)
            case HIR.StructPattern():
                fields = ctx.get_struct_fields(pattern.type_id)
                for index, sub in pattern.fields:
                    field_addr = self.__memory.build_field_ptr(address, index, fields[index].type_id)
                    self.__emit_pattern(sub, field_addr, failure, by_ref)
            case HIR.TuplePattern():
                ty = ctx[pattern.type_id]
                assert isinstance(ty, Type.TupleType)
                for index, sub in enumerate(pattern.elements):
                    field_addr = self.__memory.build_field_ptr(address, index, ty.element_types[index])
                    self.__emit_pattern(sub, field_addr, failure, by_ref)
            case HIR.SequencePattern():
                self.__emit_sequence_pattern(pattern, address, failure, by_ref)

    def __emit_sequence_pattern(
        self, pattern: HIR.SequencePattern, address: IR.Value, failure: IR.Block, by_ref: bool,
    ) -> None:
        ctx = self.__state.session.type_ctx
        ty = ctx[pattern.type_id]
        assert isinstance(ty, (Type.ArrayType, Type.SliceType))
        needed = len(pattern.prefix) + len(pattern.suffix)
        elem_ptr_type = ctx.alloc_pointer(ty.element_type)
        if isinstance(ty, Type.ArrayType):
            length = ctx.try_extract_array_length(pattern.type_id)
            if length is None:
                raise CodegenError("Array pattern length must be concrete", pattern.span)
            if (length < needed) or (not pattern.rest and length != needed):
                self.__state.emitter.terminate(IR.Br(failure))
                self.__state.emitter.position(self.__state.emitter.new_block("match.array.impossible"))
                return
            base = self.__values.build_cast(address, ctx.alloc_pointer(pattern.type_id)) if isinstance(ctx[address.type_id], Type.RefType) else address
            data = self.__values.build_cast(base, elem_ptr_type)
            length_value: IR.Value = IR.IntLiteral(length, TypeCtx.u64_id)
        else:
            slice_value = self.__memory.build_load(address)
            length_value = self.__memory.build_extract_value(slice_value, 3, TypeCtx.u64_id)
            data = self.__memory.build_extract_value(slice_value, 0, elem_ptr_type)
            comparison = BinaryOperator.Geq if pattern.rest else BinaryOperator.Eq
            self.__branch_on(self.__compare(comparison, length_value, IR.IntLiteral(needed, TypeCtx.u64_id)), failure)
        for index, sub in enumerate(pattern.prefix):
            field_addr = self.__memory.build_element_ptr(data, IR.IntLiteral(index, TypeCtx.u64_id), elem_ptr_type)
            self.__emit_pattern(sub, field_addr, failure, by_ref)
        for index, sub in enumerate(pattern.suffix):
            offset = self.__compare_offset(length_value, len(pattern.suffix) - index)
            field_addr = self.__memory.build_element_ptr(data, offset, elem_ptr_type)
            self.__emit_pattern(sub, field_addr, failure, by_ref)

    def __compare_offset(self, length: IR.Value, subtract: int) -> IR.Value:
        result = IR.Reg(self.__state.emitter.new_name(), TypeCtx.u64_id)
        return self.__state.emitter.emit(IR.Binary(
            result, BinaryOperator.Sub, length, IR.IntLiteral(subtract, TypeCtx.u64_id),
        )).result

    # ------------------------------------------------------------------
    # expression lowering: resolve_val (value) / __resolve_addr (address)
    # ------------------------------------------------------------------
