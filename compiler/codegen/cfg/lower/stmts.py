"""Statement and control-flow lowering from HIR to CFG."""
from __future__ import annotations

from dataclasses import dataclass, field

from compiler.analysis.ty.context import TypeCtx
from compiler.analysis.unit import hir as HIR
from compiler.codegen.cfg import ir as IR
from compiler.codegen.cfg.lower.resolver import ExprResolver
from compiler.codegen.cfg.lower.state import FunctionState
from compiler.codegen.error import CodegenError
from compiler.runtime_error import parse_runtime_error_code


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

    def __init__(self, state: FunctionState, resolver: ExprResolver) -> None:
        self.__state = state
        self.__resolver = resolver
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
        # 整块交还——LLVM 层的 free() 提取 data 字段(释放范围 = 整块以 lock_ptr 寻址)
        self.__state.emitter.emit(IR.Delete(ptr))
        return self.__state.emitter.void_reg()

    def translate_match(self, stmt: HIR.Match) -> IR.Value:
        """Unified lowering for NewMatch covering integer, char, and enum patterns."""
        val = self.__resolver.resolve_val(stmt.value)

        merge_block = self.__state.emitter.new_block("match.merge")
        default_block = None

        arms: list[IR.MatchArm] = []
        for arm in stmt.arms:
            if arm.pattern is None:
                default_block = self.__state.emitter.new_block("match.default")
                continue

            pattern = self.hir_pattern_to_ir(arm.pattern)
            block = self.__state.emitter.new_block("match.arm")
            arms.append(IR.MatchArm(pattern=pattern, body=block))

        self.__state.emitter.terminate(IR.Match(value=val, arms=arms, default=default_block, is_ref=stmt.is_ref))

        # Translate arm bodies and collect values for phi (if expression-typed)
        arm_values: list[tuple[IR.Block, IR.Value]] = []
        index = 0
        for arm in stmt.arms:
            if arm.pattern is None:
                assert default_block is not None
                current_block = default_block
            else:
                current_block = arms[index].body
                index += 1

            self.__state.emitter.position(current_block)
            arm_val = self.translate_block(arm.body)
            if self.__state.emitter.current_block.terminator is None:
                self.__state.emitter.terminate(IR.Br(merge_block))
                arm_values.append((self.__state.emitter.current_block, arm_val))

        self.__state.emitter.position(merge_block)

        if not arm_values:
            # All arms diverge — no phi needed.
            if stmt.type_id == TypeCtx.void_id:
                return self.__state.emitter.void_reg()
            return self.__state.emitter.never_reg()

        phi = self.__state.emitter.emit_phi(arm_values)
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

    # ------------------------------------------------------------------
    # Match helpers
    # ------------------------------------------------------------------

    def hir_pattern_to_ir(self, pattern: HIR.Pattern) -> IR.Pattern:
        """Convert a HIR Pattern to (discriminant, IR.Pattern)."""
        if isinstance(pattern, HIR.IntPattern):
            return IR.IntPattern(
                value=IR.IntLiteral(value=pattern.value, type_id=pattern.type_id)
            )
        if isinstance(pattern, HIR.CharPattern):
            return IR.CharPattern(
                value=IR.CharLiteral(value=pattern.value, type_id=TypeCtx.char_id)
            )
        fields = None
        if pattern.unpack_fields is not None:
            fields = [self.__state.emitter.func.local_vars[field] for field in pattern.unpack_fields]
        return IR.EnumPattern(
            variant=pattern.variant,
            fields=fields
        )

    # ------------------------------------------------------------------
    # expression lowering: resolve_val (value) / __resolve_addr (address)
    # ------------------------------------------------------------------
