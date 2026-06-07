"""
CFG IR builder — lowers a single HIR function/method body into a CFG Function.
"""

from __future__ import annotations

from compiler.analysis.ty import ty as Type
from compiler.analysis.ty.context import TypeCtx
from compiler.analysis.unit import hir as HIR
from compiler.analysis.unit.def_point import DefPoint
from compiler.codegen.cfg import ir as IR
from compiler.codegen.error import CodegenError
from compiler.frontend.parse.operator import BinaryOperator, UnaryOperator

# ---------------------------------------------------------------------------
# Map compound-assign operators to their base arithmetic operators
# ---------------------------------------------------------------------------

COMPOUND_TO_BASE: dict[BinaryOperator, BinaryOperator] = {
    BinaryOperator.AddAssign: BinaryOperator.Add,
    BinaryOperator.SubAssign: BinaryOperator.Sub,
    BinaryOperator.MulAssign: BinaryOperator.Mul,
    BinaryOperator.DivAssign: BinaryOperator.Div,
    BinaryOperator.ModAssign: BinaryOperator.Mod,
    BinaryOperator.BitAndAssign: BinaryOperator.BitAnd,
    BinaryOperator.BitOrAssign: BinaryOperator.BitOr,
    BinaryOperator.BitXorAssign: BinaryOperator.BitXor,
    BinaryOperator.ShlAssign: BinaryOperator.Shl,
    BinaryOperator.ShrAssign: BinaryOperator.Shr,
}

ASSIGN_OPS = {
    BinaryOperator.Assign,
    BinaryOperator.AddAssign,
    BinaryOperator.SubAssign,
    BinaryOperator.MulAssign,
    BinaryOperator.DivAssign,
    BinaryOperator.ModAssign,
    BinaryOperator.BitAndAssign,
    BinaryOperator.BitOrAssign,
    BinaryOperator.BitXorAssign,
    BinaryOperator.ShlAssign,
    BinaryOperator.ShrAssign,
}


class CfgBuilder:
    """Per-function builder that lowers HIR statements/expressions into CFG IR."""

    def __init__(self, type_ctx: TypeCtx, dp: DefPoint, func_name: str) -> None:
        self.__type_ctx = type_ctx
        self.__symbol_ctx = dp.symbol_ctx
        self.__counter = 0
        # symbol_id → SSA pointer name
        self.__locals: dict[int, IR.VarRef] = {}
        # loop context stacks
        self.__loop_header: list[IR.Block] = []
        self.__loop_exit: list[IR.Block] = []

        entry_block = IR.Block("entry")
        self.__func = IR.Function(
            name=func_name,
            type_id=dp.type_id,
            blocks=[entry_block],
            entry=entry_block,
        )

        func_type = self.__type_ctx[dp.type_id]
        assert isinstance(func_type, (Type.FunctionType, Type.MethodType))
        # ── register parameters as local variables ──
        for param in dp.params:
            param_symbol = self.__symbol_ctx.get(param)
            self.__locals[param] = IR.VarRef(param_symbol.name, param, param_symbol.type_id)

        # ── translate the body ──
        assert dp.body is not None
        self.__translate_block(dp.body)

    # ------------------------------------------------------------------
    # public result
    # ------------------------------------------------------------------

    def build(self) -> IR.Function:
        return self.__func

    # ------------------------------------------------------------------
    # SSA names & block helpers
    # ------------------------------------------------------------------

    def __new_name(self) -> str:
        name = str(self.__counter)
        self.__counter += 1
        return name

    def __emit(self, stmt: IR.Stmt) -> str | None:
        """Append *stmt* to the current block and return its result name."""
        self.__current_block.stmts.append(stmt)
        if isinstance(stmt, IR.Store | IR.Delete):
            return None
        return stmt.result

    def __new_block(self, label: str) -> IR.Block:
        block = IR.Block(f"{label}.{self.__counter}")
        self.__func.blocks.append(block)
        return block

    def __set_terminator(self, term: IR.Terminator) -> None:
        self.__current_block.terminator = term

    def __switch_to(self, block: IR.Block) -> None:
        self.__current_block = block

    # ------------------------------------------------------------------
    # block translation
    # ------------------------------------------------------------------

    def __translate_block(self, block: HIR.Block) -> None:
        for stmt in block.stmts:
            self.__translate_stmt(stmt)
            # Stop if the current block was terminated
            if self.__current_block.terminator is not None:
                break

    # ------------------------------------------------------------------
    # statement translation
    # ------------------------------------------------------------------

    def __translate_stmt(self, stmt: HIR.Stmt) -> None:
        match stmt:
            case HIR.Block():
                self.__translate_block(stmt)
            case HIR.Return():
                self.__translate_return(stmt)
            case HIR.If():
                self.__translate_if(stmt)
            case HIR.Loop():
                self.__translate_loop(stmt)
            case HIR.Break():
                self.__set_terminator(IR.Br(self.__loop_exit[-1]))
            case HIR.Continue():
                self.__set_terminator(IR.Br(self.__loop_header[-1]))
            case HIR.Panic():
                self.__translate_panic(stmt)
            case HIR.Delete():
                self.__translate_delete(stmt)
            case HIR.Switch():
                self.__translate_switch(stmt)
            case HIR.Match():
                self.__translate_match(stmt)
            case _:
                # Expression statement
                self.__translate_expr_stmt(stmt)

    # ------------------------------------------------------------------
    # specific statement handlers
    # ------------------------------------------------------------------

    def __translate_return(self, stmt: HIR.Return) -> None:
        if stmt.value is not None:
            val = self.__resolve_val(stmt.value)
            self.__set_terminator(IR.Ret(val))
        else:
            self.__set_terminator(IR.RetVoid())

    def __translate_if(self, stmt: HIR.If) -> None:
        cond_val = self.__resolve_val(stmt.cond)

        then_block = self.__new_block("if.then")
        else_block = None
        if stmt.else_branch is not None:
            else_block = self.__new_block("if.else")
        merge_block = self.__new_block("if.merge")

        target_else = else_block if else_block else merge_block
        self.__set_terminator(IR.CondBr(cond_val, then_block, target_else))

        # then
        self.__switch_to(then_block)
        self.__translate_block(stmt.then_branch)
        if then_block.terminator is None:
            self.__set_terminator(IR.Br(merge_block))

        # else
        if stmt.else_branch is not None:
            assert else_block is not None
            self.__switch_to(else_block)
            self.__translate_block(stmt.else_branch)
            if else_block.terminator is None:
                self.__set_terminator(IR.Br(merge_block))

        self.__switch_to(merge_block)

    def __translate_loop(self, stmt: HIR.Loop) -> None:
        body_block = self.__new_block("loop.body")
        exit_block = self.__new_block("loop.exit")

        # Branch from current to the loop body
        self.__set_terminator(IR.Br(body_block))

        self.__loop_header.append(body_block)
        self.__loop_exit.append(exit_block)

        self.__switch_to(body_block)
        self.__translate_block(stmt.body)
        if body_block.terminator is None:
            self.__set_terminator(IR.Br(body_block))

        self.__switch_to(exit_block)

        self.__loop_header.pop()
        self.__loop_exit.pop()

    def __translate_panic(self, stmt: HIR.Panic) -> None:
        msg_val = self.__resolve_val(stmt.message)
        self.__set_terminator(IR.Panic(msg_val))

    def __translate_delete(self, stmt: HIR.Delete) -> None:
        ptr = self.__resolve_val(stmt.target)
        self.__emit(IR.Delete(ptr))

    def __translate_switch(self, stmt: HIR.Switch) -> None:
        switch_val = self.__resolve_val(stmt.value)

        default_block = None
        merge_block = self.__new_block("switch.merge")

        arms: list[IR.MatchArm] = []
        for arm in stmt.arms:
            if arm.pattern is None:
                default_block = self.__new_block("switch.default")
            else:
                arm_block = self.__new_block(f"switch.case{arm.pattern}")
        # # default_block is the first arm with pattern=None, or a new merge block
        # default_label: str | None = None
        # merge_block = self.__new_block("switch.merge")

        # # Create a block per non-default arm
        # arm_blocks: list[tuple[int, IR.Block]] = []
        # for arm in stmt.arms:
        #     if arm.pattern is None:
        #         default_label = "switch.default"
        #     else:
        #         label = f"switch.case{arm.pattern}"
        #         arm_blocks.append((arm.pattern, self.__new_block(label)))

        # if default_label is None:
        #     default_label = merge_block.label

        # cases: list[tuple[int, str]] = []
        # for pattern, block in arm_blocks:
        #     cases.append((pattern, block.label))

        # self.__set_terminator(IR.Switch(disc_val, default_label, cases))
        # switch_block = self.__current_block  # remember for patching later

        # # Translate each arm body
        # for pattern, block in arm_blocks:
        #     arm = next(a for a in stmt.arms if a.pattern == pattern)
        #     self.__switch_to(block)
        #     self.__translate_block(arm.body)
        #     if block.terminator is None:
        #         self.__set_terminator(IR.Br(merge_block.label))

        # # Translate default arm if present
        # default_arm = next((a for a in stmt.arms if a.pattern is None), None)
        # if default_arm is not None:
        #     default_block = self.__new_block("switch.default")
        #     if default_label == "switch.default":
        #         self.__switch_to(default_block)
        #         self.__translate_block(default_arm.body)
        #         if default_block.terminator is None:
        #             self.__set_terminator(IR.Br(merge_block.label))
        #         # Patch the Switch default to point to the actual default block
        #         if isinstance(switch_block.terminator, IR.Switch):
        #             old_switch = switch_block.terminator
        #             self.__current_block = switch_block
        #             self.__set_terminator(IR.Switch(old_switch.value, default_block.label, old_switch.cases))
        #         self.__switch_to(merge_block)
        #     else:
        #         self.__switch_to(merge_block)
        # else:
        #     self.__switch_to(merge_block)

    def __translate_match(self, stmt: HIR.Match) -> None:
        enum_val = self.__resolve_val(stmt.value)
        disc_val = self.__extract_discriminant(enum_val, stmt.value.type_id)

        merge_block = self.__new_block("match.merge")

        # Create blocks for each variant arm
        variant_blocks: list[tuple[int, IR.Block]] = []
        default_label: str | None = None

        for arm in stmt.arms:
            if arm.variant is None:
                default_label = "match.default"
            else:
                label = f"match.{arm.variant.name}"
                variant_blocks.append((arm.variant.discriminant, self.__new_block(label)))

        if default_label is None:
            default_label = merge_block.label

        cases = [(disc, blk.label) for disc, blk in variant_blocks]
        self.__set_terminator(IR.Switch(disc_val, default_label, cases))
        switch_block = self.__current_block  # remember for patching later

        # Translate each arm
        for arm in stmt.arms:
            if arm.variant is None:
                continue
            blk = next(b for d, b in variant_blocks if d == arm.variant.discriminant)
            self.__switch_to(blk)
            self.__translate_match_arm(enum_val, stmt.value.type_id, arm)
            if blk.terminator is None:
                self.__set_terminator(IR.Br(merge_block.label))

        # Translate default arm
        default_arm = next((a for a in stmt.arms if a.variant is None), None)
        if default_arm is not None:
            default_block = self.__new_block("match.default")
            self.__switch_to(default_block)
            self.__translate_match_arm(enum_val, stmt.value.type_id, default_arm)
            if default_block.terminator is None:
                self.__set_terminator(IR.Br(merge_block.label))
            # Patch the Switch default to point to the actual default block
            if isinstance(switch_block.terminator, IR.Switch):
                old_switch = switch_block.terminator
                self.__current_block = switch_block
                self.__set_terminator(IR.Switch(old_switch.value, default_block.label, old_switch.cases))
            self.__switch_to(merge_block)
        else:
            self.__switch_to(merge_block)

    def __translate_match_arm(self, enum_val: str, enum_type_id: int, arm: HIR.MatchArm) -> None:
        """Translate a match arm body, optionally unpacking payload fields.

        *enum_val* is the SSA value of the enum being matched.
        *enum_type_id* is the type_id of the enum.
        """
        if arm.unpack_fields is None or arm.variant is None or arm.variant.payload_type is None:
            self.__translate_block(arm.body)
            return

        payload_type_id = arm.variant.payload_type
        payload_ty = self.__type_ctx[payload_type_id]
        assert isinstance(payload_ty, Type.StructType)

        # Get a typed pointer to the payload area within the enum
        payload_ptr = self.__emit(
            IR.PayloadPtr(self.__new_name(), enum_val, enum_type_id, payload_type_id)
        )
        assert payload_ptr is not None

        payload_fields = payload_ty.get_fields(self.__type_ctx)

        # Each unpack field index corresponds to a position in payload_fields,
        # and arm.unpack_fields[i] is the symbol_id of the local variable.
        for field_idx, sym_id in enumerate(arm.unpack_fields):
            field = payload_fields[field_idx]

            # Get a pointer to this field within the payload
            field_ptr = self.__emit(
                IR.FieldPtr(self.__new_name(), payload_ptr, field.name, payload_type_id)
            )
            assert field_ptr is not None

            # Load the field value and store it into the local variable
            field_val = self.__emit(IR.Load(self.__new_name(), field_ptr, field.type_id))
            assert field_val is not None

            local_ptr = self.__locals[sym_id]
            self.__emit(IR.Store(local_ptr, field_val))

    # ------------------------------------------------------------------
    # expression-statement (used for both expression stmts and assign stmts)
    # ------------------------------------------------------------------

    def __translate_expr_stmt(self, expr: HIR.Expr) -> None:
        """Handle an expression used as a statement, including assignments."""
        match expr:
            case HIR.Binary(op=op, left=left, right=right) if op in ASSIGN_OPS:
                self.__translate_assign(op, left, right)
            case HIR.SysWrite():
                self.__translate_sys_write(expr)
            case _:
                # Evaluate for side effects, discard result
                self.__resolve_val(expr)

    def __translate_assign(self, op: BinaryOperator, left: HIR.Expr, right: HIR.Expr) -> None:
        ptr = self.__resolve_ptr(left)

        if op == BinaryOperator.Assign:
            val = self.__resolve_val(right)
        else:
            # Compound assignment: load old value, apply op, store
            left_type = left.type_id
            old_val = self.__emit(IR.Load(self.__new_name(), ptr, left_type))
            assert old_val is not None
            rhs_val = self.__resolve_val(right)
            base_op = COMPOUND_TO_BASE[op]
            val = self.__emit(IR.Binary(self.__new_name(), base_op, old_val, rhs_val, left_type))

        assert val is not None
        self.__emit(IR.Store(ptr, val))

    def __translate_sys_write(self, expr: HIR.SysWrite) -> None:
        fd = self.__resolve_val(expr.fd)
        buf = self.__resolve_val(expr.buf)
        self.__emit(IR.Call(None, "%sys_write", [fd, buf], TypeCtx.void_id))

    # ------------------------------------------------------------------
    # expression lowering: resolve_val (value) / resolve_ptr (address)
    # ------------------------------------------------------------------

    def __resolve_val(self, expr: HIR.Expr) -> IR.Value:
        """Lower *expr* to a value."""
        raise NotImplementedError(f"resolve_val: {expr}")

    def __resolve_ptr(self, expr: HIR.Expr) -> str:
        """Lower *expr* to an SSA pointer name (left-value address)."""
        match expr:
            case HIR.Var(symbol_id=sid):
                return self.__locals[sid]
            case HIR.FieldAccess(receiver=recv, field=f):
                base = self.__resolve_ptr_or_temp(recv)
                struct_type = recv.type_id
                return self.__emit(IR.FieldPtr(self.__new_name(), base, f.name, struct_type))
            case HIR.TupleAccess(receiver=recv, index=idx):
                base = self.__resolve_ptr_or_temp(recv)
                elem_type = expr.type_id
                return self.__emit(IR.ElementPtr(self.__new_name(), base, str(idx), elem_type))
            case HIR.Unary(op=UnaryOperator.Deref, operand=operand):
                # *ptr — the pointer value IS the address
                return self.__resolve_val(operand)
            case HIR.Binary(op=BinaryOperator.Index, left=base, right=index):
                base_ptr = self.__resolve_ptr_or_temp(base)
                idx_val = self.__resolve_val(index)
                elem_type = expr.type_id
                return self.__emit(IR.ElementPtr(self.__new_name(), base_ptr, idx_val, elem_type))
            case _:
                raise ValueError(f"Unsupported expression in resolve_ptr: {type(expr).__name__}")

    def __resolve_ptr_or_temp(self, expr: HIR.Expr) -> str:
        """Try to resolve *expr* as a place; if it is a pure value, materialize
        into a temporary and return a pointer to it."""
        if getattr(expr, "is_place", False):
            return self.__resolve_ptr(expr)
        return self.__materialize_temporary(expr)

    def __materialize_temporary(self, val_expr: HIR.Expr) -> str:
        """Evaluate *val_expr*, store it into a temporary local, and return
        a pointer to that temporary."""
        val = self.__resolve_val(val_expr)
        tmp_id = self.__new_name()
        tmp_ptr = self.__emit(
            IR.LocalPtr(tmp_id, f"__tmp_{tmp_id}", val_expr.type_id)
        )
        self.__emit(IR.Store(tmp_ptr, val))
        return tmp_ptr

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------

    def __resolve_discriminant(self, expr: HIR.Expr) -> str:
        """Resolve the integer discriminant of a switch/match scrutinee.

        - Integer types: the value itself is the discriminant.
        - Char types: the value itself (u32 scalar).
        - Enum types: emit a Discriminant instruction to extract the tag.
        """
        value_type = self.__type_ctx[expr.type_id]

        if isinstance(value_type, (Type.IntType, Type.CharType)):
            return self.__resolve_val(expr)

        if isinstance(value_type, Type.EnumType):
            val = self.__resolve_val(expr)
            result = self.__emit(IR.Discriminant(self.__new_name(), val, expr.type_id))
            assert result is not None
            return result

        raise CodegenError(
            f"Switch/Match scrutinee must be integer, char, or enum, got type_id={expr.type_id}",
            expr.span,
        )

    def __extract_discriminant(self, enum_val: str, enum_type_id: int) -> str:
        """Extract the integer discriminant from an enum value."""
        result = self.__emit(IR.Discriminant(self.__new_name(), enum_val, enum_type_id))
        assert result is not None
        return result

    def __get_callee_name(self, type_id: int) -> str:
        """Get the human-readable callee name from a function/method type_id."""
        return self.__type_ctx.get_name(type_id)
