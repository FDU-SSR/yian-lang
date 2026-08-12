"""
CFG IR builder — lowers a single HIR function/method body into a CFG Function.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from compiler.analysis.ty import ty as Type
from compiler.analysis.ty.context import TypeCtx
from compiler.analysis.ty.type_ops import default_literals
from compiler.analysis.unit import hir as HIR
from compiler.analysis.unit.def_point import DefPoint
from compiler.codegen.cfg import ir as IR
from compiler.codegen.error import CodegenError
from compiler.error import CompilerError
from compiler.frontend.parse.operator import BinaryOperator, UnaryOperator
from compiler.utils.log import CompilerLog


def ch_cfg():
    return CompilerLog.get("cfg.logical")


def ch_cfg_block():
    return CompilerLog.get("cfg.block")


@dataclass
class LoopCtx:
    header: IR.Block
    exit: IR.Block
    break_values: list[tuple[IR.Block, IR.Value]] = field(default_factory=list[tuple[IR.Block, IR.Value]])


class CfgBuilder:
    """Per-function builder that lowers HIR statements/expressions into CFG IR."""

    def __init__(self, type_ctx: TypeCtx, dp: DefPoint, func_name: str) -> None:
        self.__type_ctx = type_ctx
        self.__symbol_ctx = dp.symbol_ctx
        self.__dp = dp
        self.__func_name = func_name
        self.__counter = 0
        self.__loops: list[LoopCtx] = []
        self.__frame_lock: tuple[IR.Value, IR.Value] | None = None  # ⟨e_f, k_f⟩:函数入口帧锁实体化(t7,规则 3.7.1)
        self.__func: IR.Function = IR.Function(name="", type_id=0, blocks=[], entry=IR.Block(""))  # placeholder; replaced in build()

    # ------------------------------------------------------------------
    # public entry point
    # ------------------------------------------------------------------

    def build(self) -> IR.Function:
        dp = self.__dp
        entry_block = IR.Block("entry")
        self.__func = IR.Function(name=self.__func_name, type_id=dp.type_id, blocks=[entry_block], entry=entry_block)
        self.__current_block = entry_block

        # ── register parameters ──
        self.__func.params = dp.params.copy()

        # ── register body local variables ──
        for local_id in dp.locals:
            symbol = self.__symbol_ctx.get(local_id)
            self.__func.local_vars[local_id] = IR.VarRef(symbol.name, local_id, symbol.type_id)

        # ── translate the body ──
        assert dp.body is not None
        body_val = self.__translate_block(dp.body)

        if self.__current_block.terminator is None:
            func_type = self.__type_ctx[dp.type_id]
            assert isinstance(func_type, (Type.FunctionType, Type.MethodType))
            ret_ty = func_type.return_type(self.__type_ctx)
            if ret_ty != TypeCtx.void_id:
                self.__set_terminator(IR.Ret(body_val))

        # ── dead code elimination ──
        self.__eliminate_dead_code()

        # ── sort blocks in RPO for phi resolution ──
        self.__sort_blocks_rpo()

        # ── termination guard ──
        self.__guard_termination(dp)

        # ── 帧锁实体化标记(规则 3.7.1)──
        # 函数若实体化了帧锁(有帧锁 alloca),LLVM 层须在全部返回路径 ret 前
        # 补发 WriteLockSlot(e_f, SENTINEL)(规则 3.7.2 动作①,帧退出写哨兵),
        # 使栈悬垂访问经 live 键比较确定性 trap。标记随函数传给 LLTranslator。
        self.__func.frame_lock = self.__frame_lock

        return self.__func

    # ------------------------------------------------------------------
    # termination guard
    # ------------------------------------------------------------------

    def __eliminate_dead_code(self) -> None:
        """Remove blocks that are not reachable from the entry block.

        Performs a BFS from the entry block following all forward edges
        (terminator targets), then filters ``self.__func.blocks`` to only
        include reachable blocks.  Phi nodes in surviving blocks are
        cleaned up to remove incoming entries from deleted blocks.
        """
        # ── collect reachable blocks via BFS ──
        # Block is an unhashable dataclass, so track via id(…).
        reachable_ids: set[int] = set()
        worklist = [self.__func.entry]

        while worklist:
            block = worklist.pop()
            if id(block) in reachable_ids:
                continue
            reachable_ids.add(id(block))

            if block.terminator is None:
                continue

            term = block.terminator
            match term:
                case IR.Br():
                    worklist.append(term.target)
                case IR.CondBr():
                    worklist.append(term.then_block)
                    worklist.append(term.else_block)
                case IR.Match():
                    for arm in term.arms:
                        worklist.append(arm.body)
                    if term.default is not None:
                        worklist.append(term.default)
                case IR.Ret() | IR.Panic() | IR.YianExit():
                    pass

        # ── filter blocks ──
        self.__func.blocks = [b for b in self.__func.blocks if id(b) in reachable_ids]

        # ── clean up phi nodes ──
        for block in self.__func.blocks:
            surviving_phis: list[IR.Phi] = []
            for phi in block.phis:
                phi.incoming = [
                    (pred, val) for pred, val in phi.incoming if id(pred) in reachable_ids
                ]
                if phi.incoming:
                    surviving_phis.append(phi)
            block.phis = surviving_phis

    def __sort_blocks_rpo(self) -> None:
        """Reorder ``self.__func.blocks`` in reverse post-order.

        Reverse post-order guarantees that for every forward edge
        A -> B in the CFG, block A appears before block B in the
        ordered list.  This ensures that when the LLVM translator
        iterates blocks in list order, every phi node's predecessor
        values have already been registered.

        Back edges (edges that form cycles, e.g. loop back edges)
        are detected via an ``in_progress`` set and are skipped.
        This is safe because loop headers in the current lowering
        do not carry phi nodes that depend on back-edge values.
        """
        # ── build successor map (same pattern as __eliminate_dead_code) ──
        successors: dict[int, list[IR.Block]] = {}
        for block in self.__func.blocks:
            succs: list[IR.Block] = []
            if block.terminator is not None:
                match block.terminator:
                    case IR.Br(target=target):
                        succs.append(target)
                    case IR.CondBr(then_block=then, else_block=else_):
                        succs.append(then)
                        succs.append(else_)
                    case IR.Match(arms=arms, default=default):
                        for arm in arms:
                            succs.append(arm.body)
                        if default is not None:
                            succs.append(default)
                    case IR.Ret() | IR.Panic() | IR.YianExit():
                        pass
            successors[id(block)] = succs

        # ── add phi incoming edges ──
        # If block B has a phi with incoming from block P, P must appear
        # before B.  Add B as a successor of P so the DFS visits P first.
        for block in self.__func.blocks:
            for phi in block.phis:
                for pred, _ in phi.incoming:
                    successors.setdefault(id(pred), []).append(block)

        # ── DFS from entry, collecting postorder ──
        visited: set[int] = set()
        in_progress: set[int] = set()
        postorder: list[IR.Block] = []

        def dfs(block: IR.Block) -> None:
            bid = id(block)
            if bid in visited:
                return
            if bid in in_progress:
                return  # back edge — block already on the DFS stack, skip
            in_progress.add(bid)
            for succ in successors.get(bid, []):
                dfs(succ)
            in_progress.discard(bid)
            visited.add(bid)
            postorder.append(block)

        dfs(self.__func.entry)

        # RPO = reverse of postorder
        # After DCE every block is reachable from entry, so |rpo| == |blocks|
        self.__func.blocks = list(reversed(postorder))

    def __guard_termination(self, dp: DefPoint) -> None:
        """Ensure every block has a terminator.

        - void-returning functions: patch unterminated blocks with ``Ret(void_reg)``.
        - non-void-returning functions: raise ``CodegenError`` if any block is unterminated.
        """
        func_type = self.__type_ctx[dp.type_id]
        assert isinstance(func_type, (Type.FunctionType, Type.MethodType))
        return_type = func_type.return_type(self.__type_ctx)

        for block in self.__func.blocks:
            if block.terminator is not None:
                continue
            if return_type == TypeCtx.void_id:
                block.terminator = IR.Ret(self.__void_reg())
            else:
                raise CodegenError(
                    f"Function '{self.__func.name}' has unterminated block '{block.label}'; "
                    f"non-void functions must have explicit return in all control paths.",
                    dp.ast_body.span,
                )

    # ------------------------------------------------------------------
    # SSA names & block helpers
    # ------------------------------------------------------------------

    def __new_name(self) -> str:
        name = str(self.__counter)
        self.__counter += 1
        return name

    def __emit[StmtType: IR.Stmt](self, stmt: StmtType) -> StmtType:
        """Append *stmt* to the current block and return its result name."""
        self.__current_block.stmts.append(stmt)
        return stmt

    def __new_block(self, label: str) -> IR.Block:
        block = IR.Block(f"{label}.{self.__counter}")
        self.__counter += 1
        self.__func.blocks.append(block)
        ch_cfg_block().trace(lambda: f"new block {block.label}")
        return block

    def __set_terminator(self, term: IR.Terminator) -> None:
        ch_cfg_block().trace(lambda: f"{self.__current_block.label} <- {type(term).__name__}")
        self.__current_block.terminator = term

    def __switch_to(self, block: IR.Block) -> None:
        self.__current_block = block

    def __void_reg(self) -> IR.Value:
        return IR.Reg(name=self.__new_name(), type_id=TypeCtx.void_id)

    def __never_reg(self) -> IR.Value:
        return IR.Reg(name=self.__new_name(), type_id=TypeCtx.never_id)

    # ------------------------------------------------------------------
    # block translation
    # ------------------------------------------------------------------

    def __translate_block(self, block: HIR.Block) -> IR.Value:
        """Translate a block, returning the value of the last expression."""
        last_val: IR.Value = self.__void_reg()
        for stmt in block.stmts:
            last_val = self.__resolve_val(stmt)
            if self.__current_block.terminator is not None:
                # Mid-block terminator (return/break/continue/panic) → divergent.
                return last_val
        return last_val

    # ------------------------------------------------------------------
    # expression handlers
    # ------------------------------------------------------------------

    def __translate_return(self, stmt: HIR.Return) -> IR.Value:
        val = self.__resolve_val(stmt.value) if stmt.value is not None else self.__void_reg()
        self.__set_terminator(IR.Ret(val))
        return self.__never_reg()

    def __translate_if(self, stmt: HIR.If) -> IR.Value:
        cond_val = self.__resolve_val(stmt.cond)

        then_block = self.__new_block("if.then")
        else_block = self.__new_block("if.else") if stmt.else_branch else None
        merge_block = self.__new_block("if.merge")

        self.__set_terminator(IR.CondBr(cond_val, then_block, else_block or merge_block))

        self.__switch_to(then_block)
        then_val = self.__translate_block(stmt.then_branch)
        then_reaches_merge = self.__current_block.terminator is None
        if then_reaches_merge:
            self.__set_terminator(IR.Br(merge_block))
        then_end = self.__current_block
        incoming: list[tuple[IR.Block, IR.Value]] = []
        if then_reaches_merge:
            incoming.append((then_end, then_val))

        if stmt.else_branch is not None:
            assert else_block is not None
            self.__switch_to(else_block)
            else_val = self.__translate_block(stmt.else_branch)
            if self.__current_block.terminator is None:
                self.__set_terminator(IR.Br(merge_block))
                incoming.append((self.__current_block, else_val))

        self.__switch_to(merge_block)

        if not incoming:
            # All branches diverge — no phi needed.
            if stmt.type_id == TypeCtx.void_id:
                return self.__void_reg()
            return self.__never_reg()

        phi = self.__emit_phi(incoming)
        phi.type_id = stmt.type_id
        return phi

    def __translate_loop(self, stmt: HIR.Loop) -> IR.Value:
        body_block = self.__new_block("loop.body")
        exit_block = self.__new_block("loop.exit")

        self.__set_terminator(IR.Br(body_block))
        self.__loops.append(LoopCtx(header=body_block, exit=exit_block))

        self.__switch_to(body_block)
        self.__translate_block(stmt.body)
        if self.__current_block.terminator is None:
            self.__set_terminator(IR.Br(body_block))

        self.__switch_to(exit_block)

        # Build phi from break values (if any non-divergent breaks)
        loop = self.__loops.pop()
        if loop.break_values:
            phi = self.__emit_phi(loop.break_values)
            phi.type_id = stmt.type_id
            return phi
        if stmt.type_id == TypeCtx.void_id:
            return self.__void_reg()
        return self.__never_reg()

    def __translate_panic(self, stmt: HIR.Panic) -> IR.Value:
        msg_val = self.__resolve_val(stmt.message)
        self.__set_terminator(IR.Panic(msg_val))
        return self.__never_reg()

    def __translate_delete(self, stmt: HIR.Delete) -> IR.Value:
        ptr = self.__resolve_val(stmt.target)
        if self.__is_fat_pointer(ptr):
            # t7 检查插入:四前提 is_heap(p) ∧ live(p) ∧ is_raw(p)(规则 3.6.2;
            # 四项 = is_heap 纯位判定 + live 锁槽键比较 + is_raw 两分量
            # data=lock_ptr+H 与 index=0)
            self.__emit(IR.CheckDelete(ptr=ptr))
            # 动作①:锁槽写 SENTINEL(规则 3.6.2)——提取 lock_ptr 字段寻址
            lock_ptr = self.__extract_fat_field(ptr, IR.FAT_LOCK_PTR)
            sentinel = IR.IntLiteral(value=IR.SENTINEL, type_id=TypeCtx.u64_id)
            self.__emit(IR.WriteLockSlot(lock_ptr=lock_ptr, value=sentinel))
            ch_cfg_block().debug(lambda: "check insert Delete: is_heap(p) ∧ live(p) ∧ is_raw(p) (规则 3.6.2) + 锁槽写 SENTINEL")
        # 动作②:整块交还——t8 的 free() 提取 data 字段(释放范围 = 整块以 lock_ptr 寻址)
        self.__emit(IR.Delete(ptr))
        return self.__void_reg()

    def __translate_match(self, stmt: HIR.Match) -> IR.Value:
        """Unified lowering for NewMatch covering integer, char, and enum patterns."""
        val = self.__resolve_val(stmt.value)

        merge_block = self.__new_block("match.merge")
        default_block = None

        arms: list[IR.MatchArm] = []
        for arm in stmt.arms:
            if arm.pattern is None:
                default_block = self.__new_block("match.default")
                continue

            pattern = self.__hir_pattern_to_ir(arm.pattern)
            block = self.__new_block("match.arm")
            arms.append(IR.MatchArm(pattern=pattern, body=block))

        self.__set_terminator(IR.Match(value=val, arms=arms, default=default_block, is_ref=stmt.is_ref))

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

            self.__switch_to(current_block)
            arm_val = self.__translate_block(arm.body)
            if self.__current_block.terminator is None:
                self.__set_terminator(IR.Br(merge_block))
                arm_values.append((self.__current_block, arm_val))

        self.__switch_to(merge_block)

        if not arm_values:
            # All arms diverge — no phi needed.
            if stmt.type_id == TypeCtx.void_id:
                return self.__void_reg()
            return self.__never_reg()

        phi = self.__emit_phi(arm_values)
        phi.type_id = stmt.type_id
        return phi

    def __translate_break(self, stmt: HIR.Break) -> IR.Value:
        if stmt.value is not None:
            val = self.__resolve_val(stmt.value)
            self.__loops[-1].break_values.append((self.__current_block, val))
        self.__set_terminator(IR.Br(self.__loops[-1].exit))
        return self.__never_reg()

    def __translate_semi(self, stmt: HIR.Semi) -> IR.Value:
        self.__resolve_val(stmt.expr)
        if stmt.type_id == TypeCtx.never_id:
            return self.__never_reg()
        return self.__void_reg()

    def __translate_let(self, stmt: HIR.Let) -> IR.Value:
        if stmt.init is not None:
            self.__resolve_val(stmt.init)
        return self.__void_reg()

    # ------------------------------------------------------------------
    # Match helpers
    # ------------------------------------------------------------------

    def __hir_pattern_to_ir(self, pattern: HIR.Pattern) -> IR.Pattern:
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
            fields = [self.__func.local_vars[field] for field in pattern.unpack_fields]
        return IR.EnumPattern(
            variant=pattern.variant,
            fields=fields
        )

    # ------------------------------------------------------------------
    # expression lowering: resolve_val (value) / __resolve_addr (address)
    # ------------------------------------------------------------------

    def __resolve_val(self, expr: HIR.Expr) -> IR.Value:
        """Lower *expr* to a value."""
        match expr:
            # --- expression-oriented control flow ---
            case HIR.Block():
                return self.__translate_block(expr)
            case HIR.If():
                return self.__translate_if(expr)
            case HIR.Loop():
                return self.__translate_loop(expr)
            case HIR.Match():
                return self.__translate_match(expr)
            case HIR.Return():
                return self.__translate_return(expr)
            case HIR.Break():
                return self.__translate_break(expr)
            case HIR.Continue():
                self.__set_terminator(IR.Br(self.__loops[-1].header))
                return self.__void_reg()
            case HIR.Delete():
                return self.__translate_delete(expr)
            case HIR.Panic():
                return self.__translate_panic(expr)
            case HIR.Semi():
                return self.__translate_semi(expr)
            case HIR.Let():
                return self.__translate_let(expr)
            # --- original expressions ---
            case HIR.Binary():
                return self.__resolve_binary(expr)
            case HIR.Unary():
                return self.__resolve_unary(expr)
            case HIR.Call():
                return self.__resolve_call(expr)
            case HIR.StructConstruct():
                return self.__resolve_struct_construct(expr)
            case HIR.Invoke():
                return self.__resolve_invoke(expr)
            case HIR.Cast():
                return self.__resolve_cast(expr)
            case HIR.MethodCall():
                return self.__resolve_method_call(expr)
            case HIR.VariantConstruct():
                return self.__resolve_variant_construct(expr)
            case HIR.FieldAccess():
                return self.__resolve_field_access(expr)
            case HIR.TupleAccess():
                return self.__resolve_tuple_access(expr)
            case HIR.DynValue():
                return self.__resolve_dyn_value(expr)
            case HIR.DynBuffer():
                return self.__resolve_dyn_buffer(expr)
            case HIR.SizeOf():
                return self.__resolve_size_of(expr)
            case HIR.BitCast():
                return self.__resolve_bit_cast(expr)
            case HIR.BitCopy():
                return self.__resolve_val(expr.value)
            case HIR.AssumeInit():
                return self.__resolve_val(expr.value)
            case HIR.Nop():
                return self.__void_reg()
            case HIR.SysRead():
                return self.__resolve_sys_read(expr)
            case HIR.SysWrite():
                return self.__resolve_sys_write(expr)
            case HIR.MemCopy():
                return self.__resolve_mem_copy(expr)
            case HIR.Open():
                return self.__resolve_open(expr)
            case HIR.Close():
                return self.__resolve_close(expr)
            case HIR.YianArgc():
                return self.__resolve_yian_argc(expr)
            case HIR.YianArgvPtr():
                return self.__resolve_yian_argv_ptr(expr)
            case HIR.YianCstrlen():
                return self.__resolve_yian_cstrlen(expr)
            case HIR.YianExit():
                return self.__resolve_yian_exit(expr)
            case HIR.Tuple():
                return self.__resolve_tuple(expr)
            case HIR.Array():
                return self.__resolve_array(expr)
            case HIR.ArrayRepeat():
                return self.__resolve_array_repeat(expr)
            case HIR.Var():
                return self.__resolve_var(expr)
            case HIR.IntLiteral() | HIR.FloatLiteral() | HIR.CharLiteral() | HIR.BoolLiteral() | HIR.StrLiteral() | HIR.NullptrLiteral():
                return self.__resolve_literal(expr)
            case HIR.Ty():
                if self.__type_ctx.is_zst(expr.type_id):
                    return IR.Reg(name=self.__new_name(), type_id=expr.type_id)
                raise CodegenError(f"Cannot resolve type expression: {expr}", expr.span)
            case HIR.Closure():
                raise CompilerError(f"Closure lowering should have been completed before CFG building: {expr}")

    def __resolve_addr(self, expr: HIR.Expr) -> IR.Value:
        """Lower *expr* to an address.

        - for lvalue expressions, this is the address of the lvalue
        - for rvalue expressions, allocates a temporary and copies the value to it
        """
        if not expr.is_place:
            val = self.__resolve_val(expr)
            return self.__build_alloca(val)

        # resolve expr that produces an address
        match expr:
            case HIR.Unary() if expr.op == UnaryOperator.Deref:
                return self.__resolve_deref_addr(expr)
            case HIR.FieldAccess():
                return self.__resolve_field_access_addr(expr)
            case HIR.TupleAccess():
                return self.__resolve_tuple_access_addr(expr)
            case HIR.Var():
                return self.__resolve_var_addr(expr)
            case HIR.Ty():
                return self.__build_func_ptr(expr.type_id)
            case _:
                raise CodegenError(f"Cannot resolve address of expression: {expr}", expr.span)

    # ------------------------------------------------------------------
    # value resolvors
    # ------------------------------------------------------------------

    def __resolve_binary(self, expr: HIR.Binary) -> IR.Value:
        """
        Special cases:

        - logical operators should implement short-circuit evaluation
        - assignment => load address of lhs, load value from rhs, store value to lhs
        - compound assignments should be implemented as binary operation + store
        - `in` and `..` should not be handled here
        """
        if expr.op.is_logical():
            return self.__resolve_logical(expr)
        if expr.op == BinaryOperator.Assign:
            return self.__resolve_assign(expr)
        if expr.op.is_compound_assign():
            return self.__resolve_compound_assign(expr)
        if expr.op in (BinaryOperator.In, BinaryOperator.Range):
            raise CodegenError(f"Cannot resolve expression: {expr}", expr.span)

        # common case
        lhs = self.__resolve_val(expr.left)
        rhs = self.__resolve_val(expr.right)
        return self.__build_binary(expr.op, lhs, rhs, expr.type_id)

    def __resolve_compound_assign(self, expr: HIR.Binary) -> IR.Value:
        op = expr.op.compound_assign_to_binary()

        lhs_addr = self.__resolve_addr(expr.left)
        lhs_val = self.__build_load(lhs_addr)
        rhs_val = self.__resolve_val(expr.right)

        result = self.__build_binary(op, lhs_val, rhs_val, expr.type_id)
        self.__build_store(result, lhs_addr)
        return result

    def __resolve_assign(self, expr: HIR.Binary) -> IR.Value:
        lhs_addr = self.__resolve_addr(expr.left)
        rhs_val = self.__resolve_val(expr.right)
        if rhs_val.type_id != self.__type_ctx.never_id:
            self.__build_store(rhs_val, lhs_addr)
        return rhs_val

    def __resolve_logical(self, expr: HIR.Binary) -> IR.Value:
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
        cond_val = self.__resolve_val(expr.left)

        rhs_block = self.__new_block("logical.rhs")
        merge_block = self.__new_block("logical.merge")
        entry_block = self.__current_block

        if expr.op == BinaryOperator.LogicalAnd:
            # a && b: evaluate b only when a is true
            self.__set_terminator(IR.CondBr(cond_val, rhs_block, merge_block))
            short_circuit_value: IR.Value = IR.BoolLiteral(value=False, type_id=TypeCtx.bool_id)
        else:
            # a || b: evaluate b only when a is false
            self.__set_terminator(IR.CondBr(cond_val, merge_block, rhs_block))
            short_circuit_value = IR.BoolLiteral(value=True, type_id=TypeCtx.bool_id)

        # ── rhs block ──
        self.__switch_to(rhs_block)
        rhs_val = self.__resolve_val(expr.right)
        # __resolve_val may have switched current_block (nested logical).
        # The block that actually produced rhs_val is where we ended up.
        rhs_end_block = self.__current_block

        # Bridge rhs_end_block to merge if it doesn't already have a terminator.
        if rhs_end_block.terminator is None:
            self.__set_terminator(IR.Br(merge_block))

        # ── merge block ──
        self.__switch_to(merge_block)
        result = self.__emit_phi([
            (entry_block, short_circuit_value),
            (rhs_end_block, rhs_val),
        ])
        # The merge block needs a terminator so it is not left dangling.
        # Create a continuation block that callers can append to.
        cont_block = self.__new_block("logical.cont")
        self.__set_terminator(IR.Br(cont_block))
        self.__switch_to(cont_block)
        return result

    def __resolve_unary(self, expr: HIR.Unary) -> IR.Value:
        """
        Special cases:

        - dereference => resolve pointer value then load through it
        - addr of lvalue => get address
        """
        if expr.op == UnaryOperator.Deref:
            ptr_val = self.__resolve_val(expr.operand)
            return self.__build_load(ptr_val)
        if expr.op == UnaryOperator.AddrOf:
            return self.__resolve_addr(expr.operand)

        # common case
        val = self.__resolve_val(expr.operand)
        return self.__build_unary(expr.op, val, expr.type_id)

    def __resolve_call(self, expr: HIR.Call) -> IR.Value:
        arg_vals = [self.__resolve_val(arg) for arg in expr.args]
        result = self.__build_call(expr.func, arg_vals, expr.type_id)
        # If the callee returns never, control never returns — terminate block
        if expr.type_id == self.__type_ctx.never_id:
            self.__set_terminator(IR.Panic(IR.StringLiteral(value="unreachable: never-returning function returned", type_id=TypeCtx.str_id)))
        return result

    def __resolve_struct_construct(self, expr: HIR.StructConstruct) -> IR.Value:
        struct_type = self.__type_ctx[expr.struct_id]
        assert isinstance(struct_type, Type.StructType)
        fields = self.__type_ctx.get_struct_fields(expr.struct_id)
        field_vals = [self.__resolve_val(expr.field_values[field.name]) for field in fields]
        return self.__build_aggregate_construct(expr.struct_id, field_vals)

    def __resolve_invoke(self, expr: HIR.Invoke) -> IR.Value:
        callee = self.__resolve_val(expr.callable)
        arg_vals = [self.__resolve_val(arg) for arg in expr.args]
        resolved = self.__type_ctx.resolve_aliases(expr.callable.type_id)
        if isinstance(self.__type_ctx[resolved], Type.FunctionType):
            result = self.__build_call(resolved, arg_vals, expr.type_id)
            if expr.type_id == self.__type_ctx.never_id:
                self.__set_terminator(IR.Panic(IR.StringLiteral(value="unreachable: never-returning function returned", type_id=TypeCtx.str_id)))
            return result
        return self.__build_invoke(callee, arg_vals, expr.type_id)

    def __resolve_cast(self, expr: HIR.Cast) -> IR.Value:
        value = self.__resolve_val(expr.value)
        return self.__build_cast(value, expr.target_type)

    def __resolve_method_call(self, expr: HIR.MethodCall) -> IR.Value:
        method_type = self.__type_ctx[expr.method_id]
        assert isinstance(method_type, Type.MethodType)

        arg_vals = [self.__resolve_val(arg) for arg in expr.args]

        if method_type.custom_def.is_static:
            return self.__build_call(expr.method_id, arg_vals, expr.type_id)

        # non-static: pass receiver address as the first argument
        receiver_addr = self.__resolve_addr(expr.receiver)
        return self.__build_call(expr.method_id, [receiver_addr] + arg_vals, expr.type_id)

    def __resolve_variant_construct(self, expr: HIR.VariantConstruct) -> IR.Value:
        if expr.args is None:
            payload_fields = None
        else:
            assert expr.variant.payload_type is not None
            payload_type = self.__type_ctx[expr.variant.payload_type]
            assert isinstance(payload_type, Type.StructType)
            fields = self.__type_ctx.get_struct_fields(expr.variant.payload_type)
            payload_fields = [self.__resolve_val(expr.args[field.name]) for field in fields]

        return self.__build_variant_construct(expr.enum_id, expr.variant, payload_fields, expr.type_id)

    def __resolve_field_access(self, expr: HIR.FieldAccess) -> IR.Value:
        if expr.receiver.is_place:
            addr = self.__resolve_field_access_addr(expr)
            return self.__build_load(addr)

        value = self.__resolve_val(expr.receiver)
        return self.__build_extract_value(value, expr.field.index, expr.type_id)

    def __resolve_tuple_access(self, expr: HIR.TupleAccess) -> IR.Value:
        if expr.receiver.is_place:
            addr = self.__resolve_tuple_access_addr(expr)
            return self.__build_load(addr)

        value = self.__resolve_val(expr.receiver)
        return self.__build_extract_value(value, expr.index, expr.type_id)

    def __resolve_dyn_value(self, expr: HIR.DynValue) -> IR.Value:
        """
        1. malloc a buffer on the heap
        2. write the value to the buffer
        """
        size = IR.IntLiteral(1, type_id=TypeCtx.u64_id)
        value = self.__resolve_val(expr.value)
        buffer = self.__build_malloc(value.type_id, size)
        self.__build_store(value, buffer)
        return buffer

    def __resolve_dyn_buffer(self, expr: HIR.DynBuffer) -> IR.Value:
        size = self.__resolve_val(expr.length)
        buffer = self.__build_malloc(expr.element_type, size)
        return buffer

    def __resolve_size_of(self, expr: HIR.SizeOf) -> IR.Value:
        return self.__build_size_of(expr.target_type)

    def __resolve_bit_cast(self, expr: HIR.BitCast) -> IR.Value:
        value = self.__resolve_val(expr.value)
        return self.__build_cast(value, expr.type_id)

    def __resolve_sys_read(self, expr: HIR.SysRead) -> IR.Value:
        fd = self.__resolve_val(expr.fd)
        buf = self.__resolve_val(expr.buf)
        return self.__build_sys_read(fd, buf)

    def __resolve_sys_write(self, expr: HIR.SysWrite) -> IR.Value:
        fd = self.__resolve_val(expr.fd)
        buf = self.__resolve_val(expr.buf)
        return self.__build_sys_write(fd, buf)

    def __resolve_mem_copy(self, expr: HIR.MemCopy) -> IR.Value:
        dest = self.__resolve_val(expr.dest)
        src = self.__resolve_val(expr.src)
        count = self.__resolve_val(expr.count)
        self.__emit(IR.MemCopy(dest=dest, src=src, count=count))
        return self.__void_reg()

    def __resolve_open(self, expr: HIR.Open) -> IR.Value:
        path = self.__resolve_val(expr.path)
        flags = self.__resolve_val(expr.flags)
        return self.__build_open(path, flags)

    def __resolve_close(self, expr: HIR.Close) -> IR.Value:
        fd = self.__resolve_val(expr.fd)
        return self.__build_close(fd)

    def __resolve_yian_argc(self, expr: HIR.YianArgc) -> IR.Value:
        result = IR.Reg(name=self.__new_name(), type_id=TypeCtx.u64_id)
        return self.__emit(IR.YianArgc(result=result)).result

    def __resolve_yian_argv_ptr(self, expr: HIR.YianArgvPtr) -> IR.Value:
        index = self.__resolve_val(expr.index)
        result = IR.Reg(name=self.__new_name(), type_id=self.__type_ctx.alloc_pointer(self.__type_ctx.u8_id))
        return self.__emit(IR.YianArgvPtr(result=result, index=index)).result

    def __resolve_yian_cstrlen(self, expr: HIR.YianCstrlen) -> IR.Value:
        ptr = self.__resolve_val(expr.ptr)
        result = IR.Reg(name=self.__new_name(), type_id=TypeCtx.u64_id)
        return self.__emit(IR.YianCstrlen(result=result, ptr=ptr)).result

    def __resolve_yian_exit(self, expr: HIR.YianExit) -> IR.Value:
        code = self.__resolve_val(expr.code)
        self.__set_terminator(IR.YianExit(code=code))
        return self.__never_reg()

    def __resolve_tuple(self, expr: HIR.Tuple) -> IR.Value:
        field_vals = [self.__resolve_val(field) for field in expr.field_values]
        return self.__build_aggregate_construct(expr.type_id, field_vals)

    def __resolve_array(self, expr: HIR.Array) -> IR.Value:
        elements = [self.__resolve_val(element) for element in expr.elements]
        return self.__build_array_construct(expr.type_id, elements)

    def __resolve_array_repeat(self, expr: HIR.ArrayRepeat) -> IR.Value:
        elem_val = self.__resolve_val(expr.element)
        array_ty = self.__type_ctx[expr.type_id]
        assert isinstance(array_ty, Type.ArrayType)
        length_ty = self.__type_ctx[array_ty.length]
        assert isinstance(length_ty, Type.LiteralValueType), (
            f"array repeat count must be concrete at codegen, got {type(length_ty).__name__}"
        )
        elements = [elem_val] * length_ty.value
        return self.__build_array_construct(expr.type_id, elements)

    def __resolve_var(self, expr: HIR.Var) -> IR.Value:
        addr = self.__resolve_var_addr(expr)
        return self.__build_load(addr)

    def __resolve_literal(self, expr: HIR.Literal) -> IR.Value:
        match expr:
            case HIR.IntLiteral():
                type_id = default_literals(self.__type_ctx, expr.type_id)
                return IR.IntLiteral(value=expr.value, type_id=type_id)
            case HIR.FloatLiteral():
                type_id = default_literals(self.__type_ctx, expr.type_id)
                return IR.FloatLiteral(value=expr.value, type_id=type_id)
            case HIR.CharLiteral():
                return IR.CharLiteral(value=expr.value, type_id=TypeCtx.char_id)
            case HIR.BoolLiteral():
                return IR.BoolLiteral(value=expr.value, type_id=TypeCtx.bool_id)
            case HIR.NullptrLiteral():
                return IR.NullptrLiteral(type_id=expr.type_id)
            case HIR.StrLiteral():
                return IR.StringLiteral(value=expr.value, type_id=TypeCtx.str_id)

    # ------------------------------------------------------------------
    # addr resolvors
    # ------------------------------------------------------------------

    def __resolve_deref_addr(self, expr: HIR.Unary) -> IR.Value:
        return self.__resolve_val(expr.operand)

    def __resolve_field_access_addr(self, expr: HIR.FieldAccess) -> IR.Value:
        base_addr = self.__resolve_addr(expr.receiver)
        return self.__build_field_ptr(base_addr, expr.field.index, expr.type_id)

    def __resolve_tuple_access_addr(self, expr: HIR.TupleAccess) -> IR.Value:
        base_addr = self.__resolve_addr(expr.receiver)
        return self.__build_field_ptr(base_addr, expr.index, expr.type_id)

    def __resolve_var_addr(self, expr: HIR.Var) -> IR.Value:
        if expr.symbol_id not in self.__func.local_vars:
            raise CodegenError(f"Undefined variable: {expr.symbol_id}", expr.span)
        var_ref = self.__func.local_vars[expr.symbol_id]
        return self.__build_var_ptr(var_ref)

    # ------------------------------------------------------------------
    # ir building helpers
    # ------------------------------------------------------------------

    def __emit_frame_lock(self) -> tuple[IR.Value, IR.Value]:
        """帧锁实体化(§2.6、规则 3.7.1):k_f ← Gen()(栈键 MSB 0),alloca 一个
        u64 栈槽(锁槽),槽写键 μ⟨e_f⟩ := k_f 以 WriteLockSlot 表达。仅在首次
        取址(VarPtr)时惰性触发,实体化语句插入入口块语句最前——先于正文与
        终止符;无取址的函数不含帧锁节点。注意:Alloca 的初值为占位 0,t8
        下降时改为存 k_f(帧锁槽写键);VarPtr 的 frame_key 已是 GenKey 结果。
        帧退出写 SENTINEL(全部返回路径,规则 3.7.2 动作①)的发射属 t8。"""
        if self.__frame_lock is not None:
            return self.__frame_lock
        saved_block = self.__current_block
        self.__current_block = self.__func.entry
        k_f = self.__build_gen_key(is_heap=False)
        e_f = self.__build_alloca(k_f)
        self.__emit(IR.WriteLockSlot(lock_ptr=e_f, value=k_f))
        self.__current_block = saved_block
        entry = self.__func.entry
        frame_stmts = entry.stmts[-3:]
        del entry.stmts[-3:]
        entry.stmts[0:0] = frame_stmts
        self.__frame_lock = (e_f, k_f)
        return (e_f, k_f)

    def __is_fat_pointer(self, ptr: IR.Value) -> bool:
        """胖指针判定:PointerType 且 pointee 非 ZST。

        指针-to-ZST 保持 ZST(§7.6 风险 2),走既有快路径、无检查;
        FunctionPointerType 非数据指针、不含 5 字段元数据,排除在外。
        """
        ty = self.__type_ctx[ptr.type_id]
        if not isinstance(ty, Type.PointerType):
            return False
        return not self.__type_ctx.is_zst(ty.pointee_type)

    def __build_gen_key(self, is_heap: bool) -> IR.Value:
        """k ← Gen()(定义 10):堆键 MSB 1 / 栈键 MSB 0。"""
        result = IR.Reg(name=self.__new_name(), type_id=TypeCtx.u64_id)
        return self.__emit(IR.GenKey(result=result, is_heap=is_heap)).result

    def __extract_fat_field(self, ptr: IR.Value, field_index: int) -> IR.Value:
        """从 5 字段聚合提取字段(检查所需值提取:data/lock_ptr/key/index/size)。

        指针字段(data/lock_ptr)类型为 u8*(裸字节地址);整数字段为 u64。
        """
        if field_index in (IR.FAT_DATA, IR.FAT_LOCK_PTR):
            field_type = self.__type_ctx.alloc_pointer(self.__type_ctx.u8_id)
        else:
            field_type = TypeCtx.u64_id
        return self.__build_extract_value(ptr, field_index, field_type)

    def __build_var_ptr(self, var_ref: IR.VarRef) -> IR.Value:
        """取局部变量槽地址并合成 5 字段胖指针 ⟨a_x, e_f, k_f, 0, 1⟩(定义 15、规则 3.5.1)。

        data = 槽地址 a_x;lock_ptr/key = 当前帧锁 ⟨e_f, k_f⟩(首次取址时惰性
        实体化于函数入口);index = 0;size = 1(取址总是指向单个元素,含数组取址)。
        LLVM 下降属 t8。
        """
        e_f, k_f = self.__emit_frame_lock()
        result = IR.Reg(name=self.__new_name(), type_id=self.__type_ctx.alloc_pointer(var_ref.type_id))
        return self.__emit(IR.VarPtr(
            result=result, var_ref=var_ref, frame_lock_ptr=e_f, frame_key=k_f,
        )).result

    def __build_alloca(self, value: IR.Value) -> IR.Value:
        result = IR.Reg(name=self.__new_name(), type_id=self.__type_ctx.alloc_pointer(value.type_id))
        return self.__emit(IR.Alloca(result=result, value=value)).result

    def __build_field_ptr(self, base: IR.Value, field_index: int, field_type: int) -> IR.Value:
        # t7 检查插入:in_bounds(p_s, 1)(规则 3.5.2 重锚定前提,对 one-past-end 的 s 取字段 trap)
        if self.__is_fat_pointer(base):
            self.__emit(IR.CheckInBounds(ptr=base))
            ch_cfg_block().debug(lambda: "check insert FieldPtr: in_bounds(p_s,1) (规则 3.5.2 重锚定前提)")
        result = IR.Reg(name=self.__new_name(), type_id=self.__type_ctx.alloc_pointer(field_type))
        return self.__emit(IR.FieldPtr(result=result, base=base, field_index=field_index)).result

    def __build_load(self, ptr: IR.Value) -> IR.Value:
        ptr_type = self.__type_ctx[ptr.type_id]
        assert isinstance(ptr_type, Type.PointerType)
        # t7 检查插入:safe_access(p, 1) = live(p) ∧ in_bounds(p, 1) 前检(规则 3.2.1)
        if self.__is_fat_pointer(ptr):
            self.__emit(IR.CheckSafeAccess(ptr=ptr))
            ch_cfg_block().debug(lambda: "check insert Load: safe_access(p,1) = live(p) ∧ in_bounds(p,1) (规则 3.2.1)")
        result = IR.Reg(name=self.__new_name(), type_id=ptr_type.pointee_type)
        return self.__emit(IR.Load(result=result, ptr=ptr)).result

    def __build_store(self, value: IR.Value, ptr: IR.Value) -> None:
        # t7 检查插入:safe_access(p, 1)(规则 3.2.2,同 Load 的检查与地址折算)
        if self.__is_fat_pointer(ptr):
            self.__emit(IR.CheckSafeAccess(ptr=ptr))
            ch_cfg_block().debug(lambda: "check insert Store: safe_access(p,1) = live(p) ∧ in_bounds(p,1) (规则 3.2.2)")
        self.__emit(IR.Store(ptr=ptr, value=value))

    def __build_malloc(self, type_id: int, size: IR.Value) -> IR.Value:
        # t7:Malloc 块头锁槽写键 k ← Gen()(规则 3.6.1,堆键 MSB 1),返回
        # 5 字段聚合 ⟨data=b+H, lock_ptr=e, key=k, index=0, size=n⟩(t8 构造);
        # pointee 为 ZST 时维持快路径(undef,不写锁槽;key=None,§7.6 风险 2)。
        key: IR.Value | None = None
        if not self.__type_ctx.is_zst(type_id):
            key = self.__build_gen_key(is_heap=True)
        result = IR.Reg(name=self.__new_name(), type_id=self.__type_ctx.alloc_pointer(type_id))
        return self.__emit(IR.Malloc(result=result, type_id=type_id, size=size, key=key)).result

    def __build_binary(self, op: BinaryOperator, lhs: IR.Value, rhs: IR.Value, type_id: int) -> IR.Value:
        type_id = default_literals(self.__type_ctx, type_id)
        # ── route pointer arithmetic to dedicated instructions ──
        lhs_ty = self.__type_ctx[lhs.type_id]
        rhs_ty = self.__type_ctx[rhs.type_id]

        # LLVM requires shift operands to have the same integer width.
        if op.is_shift() and lhs.type_id != rhs.type_id:
            rhs = self.__build_cast(rhs, lhs.type_id)

        if op == BinaryOperator.Add:
            if isinstance(lhs_ty, Type.PointerType):
                return self.__build_element_ptr(lhs, rhs, type_id)
            if isinstance(rhs_ty, Type.PointerType):
                return self.__build_element_ptr(rhs, lhs, type_id)

        if op == BinaryOperator.Sub:
            if isinstance(lhs_ty, Type.PointerType) and isinstance(rhs_ty, Type.PointerType):
                return self.__build_ptr_diff(lhs, rhs)
            if isinstance(lhs_ty, Type.PointerType):
                # ptr - int → negate offset then ElementPtr
                zero = IR.IntLiteral(value=0, type_id=rhs.type_id)
                neg_offset = self.__build_binary(BinaryOperator.Sub, zero, rhs, rhs.type_id)
                return self.__build_element_ptr(lhs, neg_offset, type_id)

        # ── 指针比较字段化(规则 3.4.1-3.4.2,§7.6 风险 3,t10)──
        # 胖指针(双方 PointerType 且 pointee 非 ZST)的比较路由至 PtrCmp:
        # 序比较先插 CheckPtrCmp(data 相等前提,跨对象 trap);相等比较按
        # (data, index) 二元组。FunctionPointerType 非 PointerType,不参与。
        if op.is_comparison() and self.__is_fat_pointer(lhs) and self.__is_fat_pointer(rhs):
            return self.__build_ptr_cmp(op, lhs, rhs, type_id)

        result = IR.Reg(name=self.__new_name(), type_id=type_id)
        return self.__emit(IR.Binary(result=result, op=op, lhs=lhs, rhs=rhs)).result

    def __build_element_ptr(self, base: IR.Value, offset: IR.Value, result_type: int) -> IR.Value:
        # t7 检查插入:算术 → 良构检查(定义 13:0 ≤ index+n ≤ size;规则 3.3.1-3.3.2)
        if self.__is_fat_pointer(base):
            self.__emit(IR.CheckElementArith(base=base, offset=offset))
            ch_cfg_block().debug(lambda: "check insert ElementPtr: well_formed(p') (定义 13;规则 3.3.1-3.3.2)")
        result = IR.Reg(name=self.__new_name(), type_id=result_type)
        return self.__emit(IR.ElementPtr(result=result, base=base, offset=offset)).result

    def __build_ptr_diff(self, lhs: IR.Value, rhs: IR.Value) -> IR.Value:
        # t7 检查插入:data 相等 + 良构 + 无回绕(规则 3.3.3,异对象指针差 trap)
        if self.__is_fat_pointer(lhs) and self.__is_fat_pointer(rhs):
            self.__emit(IR.CheckPtrDiff(lhs=lhs, rhs=rhs))
            ch_cfg_block().debug(lambda: "check insert PtrDiff: data 相等 + 良构 + 无回绕 (规则 3.3.3)")
        # 定型规则 8.1.9:指针差结果类型为 i64(t8 与 op_builder.py:288 同步)
        result = IR.Reg(name=self.__new_name(), type_id=TypeCtx.i64_id)
        return self.__emit(IR.PtrDiff(result=result, lhs=lhs, rhs=rhs)).result

    def __build_ptr_cmp(self, op: BinaryOperator, lhs: IR.Value, rhs: IR.Value, type_id: int) -> IR.Value:
        # t10 检查插入:序比较先查 data 相等(规则 3.4.1 前提,跨对象序比较 trap);
        # 相等比较(规则 3.4.2)按 (data, index) 二元组、无前提检查。
        if op in (BinaryOperator.Lt, BinaryOperator.Gt, BinaryOperator.Leq, BinaryOperator.Geq):
            self.__emit(IR.CheckPtrCmp(lhs=lhs, rhs=rhs))
            ch_cfg_block().debug(lambda: "check insert PtrCmp: data 相等 (规则 3.4.1)")
        result = IR.Reg(name=self.__new_name(), type_id=type_id)
        return self.__emit(IR.PtrCmp(result=result, op=op, lhs=lhs, rhs=rhs)).result

    def __build_unary(self, op: UnaryOperator, operand: IR.Value, type_id: int) -> IR.Value:
        type_id = default_literals(self.__type_ctx, type_id)
        result = IR.Reg(name=self.__new_name(), type_id=type_id)
        return self.__emit(IR.Unary(result=result, op=op, operand=operand)).result

    def __build_extract_value(self, base: IR.Value, field_index: int, type_id: int) -> IR.Value:
        result = IR.Reg(name=self.__new_name(), type_id=type_id)
        return self.__emit(IR.ExtractValue(result=result, base=base, field_index=field_index)).result

    def __build_call(self, callee_type: int, args: list[IR.Value], result_type: int) -> IR.Value:
        result = IR.Reg(name=self.__new_name(), type_id=result_type)
        return self.__emit(IR.Call(result=result, callee_type=callee_type, args=args)).result

    def __build_invoke(self, callee: IR.Value, args: list[IR.Value], result_type: int) -> IR.Value:
        result = IR.Reg(name=self.__new_name(), type_id=result_type)
        return self.__emit(IR.Invoke(result=result, callee=callee, args=args)).result

    def __build_cast(self, value: IR.Value, to_type: int) -> IR.Value:
        # t7:Cast 指针→指针语义(§7.1)——ptr-to-T ↔ ptr-to-U(均非 ZST)= identity
        #   (5 字段结构重贴,LLVM 类型同为 {i8*,i8*,i64,i64,i64});涉及 ptr-to-ZST
        #   = undef 例外(消除 LLVM size 不匹配风险)。CFG 层定义语义,发射属 t8。
        to_resolved = self.__type_ctx.resolve_aliases(to_type)
        if isinstance(self.__type_ctx[to_resolved], Type.PointerType):
            ch_cfg_block().debug(lambda: "cast ptr→ptr: identity (5 字段重贴) / ptr-to-ZST 例外 = undef")
        result = IR.Reg(name=self.__new_name(), type_id=to_type)
        return self.__emit(IR.Cast(result=result, value=value, to_type=to_type)).result

    def __build_size_of(self, type_id: int) -> IR.Value:
        result = IR.Reg(name=self.__new_name(), type_id=TypeCtx.u64_id)
        return self.__emit(IR.SizeOf(result=result, type_id=type_id)).result

    def __build_aggregate_construct(self, type_id: int, fields: list[IR.Value]) -> IR.Value:
        result = IR.Reg(name=self.__new_name(), type_id=type_id)
        return self.__emit(IR.AggregateConstruct(result=result, type_id=type_id, fields=fields)).result

    def __build_array_construct(self, type_id: int, elements: list[IR.Value]) -> IR.Value:
        result = IR.Reg(name=self.__new_name(), type_id=type_id)
        return self.__emit(IR.ArrayConstruct(result=result, type_id=type_id, elements=elements)).result

    def __build_variant_construct(self, enum_type: int, variant: Type.EnumVariant, payload_fields: list[IR.Value] | None, result_type: int) -> IR.Value:
        result = IR.Reg(name=self.__new_name(), type_id=result_type)
        return self.__emit(IR.VariantConstruct(result=result, enum_type=enum_type, variant=variant, payload_fields=payload_fields)).result

    def __build_sys_read(self, fd: IR.Value, buf: IR.Value) -> IR.Value:
        result = IR.Reg(name=self.__new_name(), type_id=TypeCtx.str_id)
        return self.__emit(IR.SysRead(result=result, fd=fd, buf=buf)).result

    def __build_sys_write(self, fd: IR.Value, buf: IR.Value) -> IR.Value:
        self.__emit(IR.SysWrite(fd=fd, buf=buf))
        return self.__void_reg()

    def __build_open(self, path: IR.Value, flags: IR.Value) -> IR.Value:
        result = IR.Reg(name=self.__new_name(), type_id=TypeCtx.i32_id)
        return self.__emit(IR.Open(result=result, path=path, flags=flags)).result

    def __build_close(self, fd: IR.Value) -> IR.Value:
        result = IR.Reg(name=self.__new_name(), type_id=TypeCtx.i32_id)
        return self.__emit(IR.Close(result=result, fd=fd)).result

    def __emit_phi(self, incoming: list[tuple[IR.Block, IR.Value]]) -> IR.Value:
        """Emit a phi node into the current block's dedicated phi list."""
        result = IR.Reg(name=self.__new_name(), type_id=incoming[0][1].type_id)
        stmt = IR.Phi(result=result, incoming=incoming)
        self.__current_block.phis.append(stmt)
        return stmt.result

    def __build_func_ptr(self, func_type_id: int) -> IR.Value:
        func_ty = self.__type_ctx[func_type_id]
        assert isinstance(func_ty, Type.FunctionType)
        func_ptr_ty = func_ty.as_pointer(self.__type_ctx)
        result = IR.Reg(name=self.__new_name(), type_id=func_ptr_ty)
        return self.__emit(IR.FuncPtr(result=result, func_type_id=func_type_id)).result
