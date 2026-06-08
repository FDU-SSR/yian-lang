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
    # expression lowering: resolve_val (value) / __resolve_addr (address)
    # ------------------------------------------------------------------

    def __resolve_val(self, expr: HIR.Expr) -> IR.Value:
        """Lower *expr* to a value."""
        match expr:
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
            case HIR.SysRead():
                return self.__resolve_sys_read(expr)
            case HIR.Tuple():
                return self.__resolve_tuple(expr)
            case HIR.Array():
                return self.__resolve_array(expr)
            case HIR.Var():
                return self.__resolve_var(expr)
            case HIR.IntLiteral() | HIR.FloatLiteral() | HIR.CharLiteral() | HIR.BoolLiteral() | HIR.StrLiteral():
                return self.__resolve_literal(expr)
            case HIR.Ty():
                raise CodegenError(f"Cannot resolve type expression: {expr}", expr.span)

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
            case HIR.Binary() if expr.op == BinaryOperator.Index:
                return self.__resolve_index_addr(expr)
            case HIR.Unary() if expr.op == UnaryOperator.Deref:
                return self.__resolve_deref_addr(expr)
            case HIR.FieldAccess():
                return self.__resolve_field_access_addr(expr)
            case HIR.TupleAccess():
                return self.__resolve_tuple_access_addr(expr)
            case HIR.Var():
                return self.__resolve_var_addr(expr)
            case _:
                raise CodegenError(f"Cannot resolve address of expression: {expr}", expr.span)

    # ------------------------------------------------------------------
    # value resolvors
    # ------------------------------------------------------------------

    def __resolve_binary(self, expr: HIR.Binary) -> IR.Value:
        """
        Special cases:

        - logical operators should implement short-circuit evaluation
        - indexing should implement runtime bounds check
        - assignment => load address of lhs, load value from rhs, store value to lhs
        - compound assignments should be implemented as binary operation + store
        - `in` and `..` should not be handled here
        """
        if expr.op.is_logical():
            return self.__resolve_logical(expr)
        if expr.op == BinaryOperator.Index:
            return self.__resolve_index(expr)
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
        self.__build_store(rhs_val, lhs_addr)
        return rhs_val

    def __resolve_index(self, expr: HIR.Binary) -> IR.Value:
        addr = self.__resolve_index_addr(expr)
        return self.__build_load(addr)

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
        if rhs_block.terminator is None:
            self.__set_terminator(IR.Br(merge_block))

        # ── merge block ──
        self.__switch_to(merge_block)
        return self.__build_phi([
            (entry_block, short_circuit_value),
            (rhs_block, rhs_val),
        ])

    def __resolve_unary(self, expr: HIR.Unary) -> IR.Value:
        """
        Special cases:

        - dereference => load address then load value
        - addr of lvalue => get address
        """
        if expr.op == UnaryOperator.Deref:
            addr = self.__resolve_addr(expr.operand)
            return self.__build_load(addr)
        if expr.op == UnaryOperator.AddrOf:
            return self.__resolve_addr(expr.operand)

        # common case
        val = self.__resolve_val(expr.operand)
        return self.__build_unary(expr.op, val, expr.type_id)

    def __resolve_call(self, expr: HIR.Call) -> IR.Value:
        raise NotImplementedError

    def __resolve_struct_construct(self, expr: HIR.StructConstruct) -> IR.Value:
        raise NotImplementedError

    def __resolve_invoke(self, expr: HIR.Invoke) -> IR.Value:
        raise NotImplementedError

    def __resolve_cast(self, expr: HIR.Cast) -> IR.Value:
        raise NotImplementedError

    def __resolve_method_call(self, expr: HIR.MethodCall) -> IR.Value:
        raise NotImplementedError

    def __resolve_variant_construct(self, expr: HIR.VariantConstruct) -> IR.Value:
        raise NotImplementedError

    def __resolve_field_access(self, expr: HIR.FieldAccess) -> IR.Value:
        raise NotImplementedError

    def __resolve_tuple_access(self, expr: HIR.TupleAccess) -> IR.Value:
        raise NotImplementedError

    def __resolve_dyn_value(self, expr: HIR.DynValue) -> IR.Value:
        raise NotImplementedError

    def __resolve_dyn_buffer(self, expr: HIR.DynBuffer) -> IR.Value:
        raise NotImplementedError

    def __resolve_size_of(self, expr: HIR.SizeOf) -> IR.Value:
        raise NotImplementedError

    def __resolve_bit_cast(self, expr: HIR.BitCast) -> IR.Value:
        raise NotImplementedError

    def __resolve_sys_read(self, expr: HIR.SysRead) -> IR.Value:
        raise NotImplementedError

    def __resolve_tuple(self, expr: HIR.Tuple) -> IR.Value:
        raise NotImplementedError

    def __resolve_array(self, expr: HIR.Array) -> IR.Value:
        raise NotImplementedError

    def __resolve_var(self, expr: HIR.Var) -> IR.Value:
        raise NotImplementedError

    def __resolve_literal(self, expr: HIR.Literal) -> IR.Value:
        raise NotImplementedError

    # ------------------------------------------------------------------
    # addr resolvors
    # ------------------------------------------------------------------

    def __resolve_index_addr(self, expr: HIR.Binary) -> IR.Value:
        raise NotImplementedError

    def __resolve_deref_addr(self, expr: HIR.Unary) -> IR.Value:
        raise NotImplementedError

    def __resolve_field_access_addr(self, expr: HIR.FieldAccess) -> IR.Value:
        raise NotImplementedError

    def __resolve_tuple_access_addr(self, expr: HIR.TupleAccess) -> IR.Value:
        raise NotImplementedError

    def __resolve_var_addr(self, expr: HIR.Var) -> IR.Value:
        raise NotImplementedError

    # ------------------------------------------------------------------
    # ir building helpers
    # ------------------------------------------------------------------

    def __build_alloca(self, value: IR.Value) -> IR.Value:
        result = IR.Reg(name=self.__new_name(), type_id=self.__type_ctx.alloc_pointer(value.type_id))
        return self.__emit(IR.Alloca(result=result, value=value)).result

    def __build_load(self, ptr: IR.Value) -> IR.Value:
        ptr_type = self.__type_ctx[ptr.type_id]
        assert isinstance(ptr_type, Type.PointerType)
        result = IR.Reg(name=self.__new_name(), type_id=ptr_type.pointee_type)
        return self.__emit(IR.Load(result=result, ptr=ptr)).result

    def __build_store(self, value: IR.Value, ptr: IR.Value) -> None:
        self.__emit(IR.Store(ptr=ptr, value=value))

    def __build_binary(self, op: BinaryOperator, lhs: IR.Value, rhs: IR.Value, type_id: int) -> IR.Value:
        result = IR.Reg(name=self.__new_name(), type_id=type_id)
        return self.__emit(IR.Binary(result=result, op=op, lhs=lhs, rhs=rhs)).result

    def __build_unary(self, op: UnaryOperator, operand: IR.Value, type_id: int) -> IR.Value:
        result = IR.Reg(name=self.__new_name(), type_id=type_id)
        return self.__emit(IR.Unary(result=result, op=op, operand=operand)).result

    def __build_phi(self, incoming: list[tuple[IR.Block, IR.Value]]) -> IR.Value:
        result = IR.Reg(name=self.__new_name(), type_id=incoming[0][1].type_id)
        return self.__emit(IR.Phi(result=result, incoming=incoming)).result
