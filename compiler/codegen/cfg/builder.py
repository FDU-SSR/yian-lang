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

    def __emit[StmtType: IR.Stmt](self, stmt: StmtType) -> StmtType:
        """Append *stmt* to the current block and return its result name."""
        self.__current_block.stmts.append(stmt)
        return stmt

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
            case HIR.Match():
                self.__translate_match(stmt)
            case HIR.SysWrite():
                self.__translate_sys_write(stmt)
            case _:
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

    def __translate_match(self, stmt: HIR.Match) -> None:
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

        self.__set_terminator(IR.Match(value=val, arms=arms, default=default_block))

        # Translate arm bodys
        index = 0
        for arm in stmt.arms:
            if arm.pattern is None:
                # default arm
                assert default_block is not None
                current_block = default_block
            else:
                current_block = arms[index].body
                index += 1

            self.__switch_to(current_block)
            self.__translate_block(arm.body)
            if current_block.terminator is None:
                self.__set_terminator(IR.Br(merge_block))

    def __translate_sys_write(self, stmt: HIR.SysWrite) -> None:
        fd = self.__resolve_val(stmt.fd)
        buf = self.__resolve_val(stmt.buf)
        self.__emit(IR.SysWrite(fd, buf))

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
            fields = [self.__locals[field] for field in pattern.unpack_fields]
        return IR.EnumPattern(
            variant=pattern.variant,
            fields=fields
        )

    def __translate_expr_stmt(self, expr: HIR.Expr) -> None:
        # Evaluate for side effects, discard result
        self.__resolve_val(expr)

    # ------------------------------------------------------------------
    # expression lowering: resolve_val (value) / resolve_ptr (address)
    # ------------------------------------------------------------------

    def __resolve_val(self, expr: HIR.Expr) -> IR.Value:
        """Lower *expr* to a value."""
        raise NotImplementedError(f"resolve_val: {expr}")
