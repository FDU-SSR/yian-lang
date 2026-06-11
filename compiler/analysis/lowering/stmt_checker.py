from __future__ import annotations

from typing import List

from compiler.analysis.error import AnalysisError
from compiler.analysis.lowering.expr_checker import ExprChecker
from compiler.analysis.lowering.hir_builder import (build_if_chain,
                                                    build_match,
                                                    build_match_arm)
from compiler.analysis.lowering.sem_ctx import LoopFrame, LoopKind, SemCtx
from compiler.analysis.symbol.symbol import SymbolKind
from compiler.analysis.ty import ty as Type
from compiler.analysis.ty.context import TypeCtx
from compiler.analysis.unit import hir as HIR
from compiler.frontend.parse import ast as AST
from compiler.frontend.parse import ast_type as ASTTy
from compiler.frontend.parse.operator import BinaryOperator

# Built-in instruction names that form statements (not expressions).
_BUILTIN_STMT_NAMES = frozenset({"panic"})


class StmtChecker:
    """Statement/block checker and lowering.

    Minimal stub that mirrors the structure of the previous `TypeCheck` statement
    handling. Concrete implementations should perform checks via `ExprChecker` and
    append constructed HIR nodes to output lists.
    """

    def __init__(self, expr_checker: ExprChecker):
        self.__expr = expr_checker

    def check_block(self, ast_block: AST.Block, ctx: SemCtx) -> HIR.Block:
        ctx.enter_scope()
        stmts: List[HIR.Stmt] = []
        try:
            for stmt in ast_block.stmts:
                self.check_stmt(stmt, stmts, ctx)
        finally:
            ctx.exit_scope()
        return HIR.Block(span=ast_block.span, stmts=stmts)

    def check_stmt(self, stmt: AST.Stmt, out: List[HIR.Stmt], ctx: SemCtx) -> None:
        match stmt:
            case AST.Block():
                out.append(self.check_block(stmt, ctx))
            case AST.VarDecl():
                self.check_var_decl(stmt, out, ctx)
            case AST.If():
                self.check_if(stmt, out, ctx)
            case AST.Loop():
                self.check_loop(stmt, out, ctx)
            case AST.Match():
                self.check_match(stmt, out, ctx)
            case AST.Return():
                self.check_return(stmt, out, ctx)
            case AST.Break():
                self.check_break(stmt, out, ctx)
            case AST.Continue():
                self.check_continue(stmt, out, ctx)
            case AST.Delete():
                self.check_delete(stmt, out, ctx)
            case AST.For() | AST.While() | AST.Assert():
                # These statements are desugared in an earlier pass; encountering them here is an invariant violation.
                raise AnalysisError(f"Unexpected statement type {type(stmt).__name__} after desugaring", stmt.span)
            case _ if self.__is_builtin_stmt_call(stmt):
                assert isinstance(stmt, AST.Call)
                self.__check_builtin_stmt(stmt, out, ctx)
            case _:
                out.append(self.__expr.value(stmt))

    def check_var_decl(self, stmt: AST.VarDecl, out: List[HIR.Stmt], ctx: SemCtx) -> None:
        assert ctx.symbol_ctx is not None

        if isinstance(stmt.var_type, ASTTy.DeducedType):
            if stmt.init_expr is None:
                raise AnalysisError("cannot infer the type of a variable without an initializer", stmt.span)
            init_expr = self.__expr.value(stmt.init_expr)
            var_type_id = init_expr.type_id
        else:
            var_type_id = ctx.resolve_type(stmt.var_type)
            init_expr = self.__expr.coerce(self.__expr.value(stmt.init_expr), var_type_id) if stmt.init_expr is not None else None

        symbol_id = self.__declare_local_symbol(stmt.name, var_type_id, ctx)

        if init_expr is not None:
            var = HIR.Var(span=stmt.name.span, symbol_id=symbol_id, type_id=var_type_id, is_place=True)
            out.append(self.__expr.assign(stmt.span, var, init_expr))

    def check_if(self, stmt: AST.If, out: List[HIR.Stmt], ctx: SemCtx) -> None:
        assert ctx.symbol_ctx is not None

        if stmt.elif_branches:
            raise AnalysisError("Unexpected elif branches after desugaring", stmt.span)

        cond_expr = self.__expr.coerce(self.__expr.value(stmt.condition), TypeCtx.bool_id)
        then_block = self.check_block(stmt.then_branch, ctx)
        else_block = self.check_block(stmt.else_branch, ctx) if stmt.else_branch is not None else None

        out.append(HIR.If(span=stmt.span, cond=cond_expr, then_branch=then_block, else_branch=else_block))

    def check_loop(self, stmt: AST.Loop, out: List[HIR.Stmt], ctx: SemCtx) -> None:
        ctx.push_loop(LoopFrame(span=stmt.span, kind=LoopKind.Loop))
        try:
            body_block = self.check_block(stmt.body, ctx)
            out.append(HIR.Loop(span=stmt.span, body=body_block))
        finally:
            ctx.pop_loop()

    def check_match(self, stmt: AST.Match, out: List[HIR.Stmt], ctx: SemCtx) -> None:
        assert ctx.symbol_ctx is not None

        # evaluate the scrutinee expression first
        value_expr = self.__expr.value(stmt.expr)
        value_type = ctx.type_ctx[value_expr.type_id]

        # Path 1: integer-like, char, or enum -> NewMatch
        if isinstance(value_type, (Type.IntType, Type.CharType, Type.EnumType)):
            self.__lower_match_new_match(stmt, value_expr, out, ctx)
            return

        # Fallback: equality-based lowering using PartialEq -> if-chain
        self.__lower_match_with_partial_eq(stmt, value_expr, out, ctx)

    def check_return(self, stmt: AST.Return, out: List[HIR.Stmt], ctx: SemCtx) -> None:
        assert ctx.symbol_ctx is not None

        return_type_id = ctx.current_return_type()
        if return_type_id is None:
            raise AnalysisError("return statement is not allowed outside of a function or method", stmt.span)

        if stmt.expr is None:
            if return_type_id != TypeCtx.void_id:
                raise AnalysisError("missing return value", stmt.span)
            out.append(HIR.Return(span=stmt.span, value=None))
            return

        if return_type_id == TypeCtx.void_id:
            raise AnalysisError("void function cannot return a value", stmt.expr.span)

        value_expr = self.__expr.coerce(self.__expr.value(stmt.expr), return_type_id)
        out.append(HIR.Return(span=stmt.span, value=value_expr))

    def check_break(self, stmt: AST.Break, out: List[HIR.Stmt], ctx: SemCtx) -> None:
        if not ctx.loop_stack:
            raise AnalysisError("'break' is only allowed inside a loop", stmt.span)

        loop_frame = ctx.loop_stack[-1]
        if not loop_frame.break_allowed:
            raise AnalysisError("'break' is not allowed in the current loop", stmt.span)

        out.append(HIR.Break(span=stmt.span))

    def check_continue(self, stmt: AST.Continue, out: List[HIR.Stmt], ctx: SemCtx) -> None:
        if not ctx.loop_stack:
            raise AnalysisError("'continue' is only allowed inside a loop", stmt.span)

        loop_frame = ctx.loop_stack[-1]
        if not loop_frame.continue_allowed:
            raise AnalysisError("'continue' is not allowed in the current loop", stmt.span)

        out.append(HIR.Continue(stmt.span))

    def check_delete(self, stmt: AST.Delete, out: List[HIR.Stmt], ctx: SemCtx) -> None:
        assert ctx.symbol_ctx is not None

        target_expr = self.__expr.value(stmt.target)
        target_type = ctx.type_ctx[target_expr.type_id]
        if not isinstance(target_type, Type.PointerType):
            raise AnalysisError("delete target must be a pointer expression", stmt.target.span)
        out.append(HIR.Delete(stmt.span, target_expr))

    def __is_builtin_stmt_call(self, stmt: AST.Stmt) -> bool:
        """Return True if `stmt` is an AST.Call to a built-in statement name."""
        if not isinstance(stmt, AST.Call):
            return False
        if not isinstance(stmt.callee, AST.Identifier):
            return False
        return stmt.callee.name in _BUILTIN_STMT_NAMES

    def __check_builtin_stmt(self, stmt: AST.Call, out: List[HIR.Stmt], ctx: SemCtx) -> None:
        """Lower a call to a built-in statement into the appropriate HIR node."""
        callee = stmt.callee
        assert isinstance(callee, AST.Identifier)
        if callee.name == "panic":
            self.__check_panic(stmt, out, ctx)

    def __check_panic(self, stmt: AST.Call, out: List[HIR.Stmt], ctx: SemCtx) -> None:
        """Lower `panic(message)` into HIR.Panic."""
        if any(arg.name is not None for arg in stmt.args):
            raise AnalysisError("named arguments are not supported for 'panic'", stmt.span)
        if len(stmt.args) != 1:
            raise AnalysisError(f"'panic' expects exactly 1 argument, got {len(stmt.args)}", stmt.span)

        message = self.__expr.value(stmt.args[0].value)
        message = self.__expr.coerce(message, TypeCtx.str_id)
        out.append(HIR.Panic(span=stmt.span, message=message))

    def __declare_local_symbol(self, name: AST.Identifier, type_id: int, ctx: SemCtx) -> int:
        assert ctx.symbol_ctx is not None

        symbol_id = ctx.symbol_ctx.add_symbol(name.name, SymbolKind.Variable, type_id)
        if symbol_id is None:
            raise AnalysisError(f"Variable '{name.name}' is already defined in the current scope", name.span)
        ctx.push_local(symbol_id)
        return symbol_id

    def __lower_match_new_match(self, stmt: AST.Match, value_expr: HIR.Expr, out: List[HIR.Stmt], ctx: SemCtx) -> None:
        """Lower `match` to a `NewMatch` HIR for integer-like, char, and enum scrutinees."""
        assert ctx.symbol_ctx is not None

        arms: list[HIR.MatchArm] = []

        for pat, arm_block in stmt.arms:
            match pat:
                case AST.IntPattern():
                    body = self.check_block(arm_block, ctx)
                    for lit in pat.values:
                        pattern: HIR.Pattern | None = HIR.IntPattern(pat.span, lit.value, value_expr.type_id)
                        arms.append(build_match_arm(pat.span, pattern, body))
                case AST.CharPattern():
                    body = self.check_block(arm_block, ctx)
                    for lit in pat.values:
                        pattern = HIR.CharPattern(pat.span, lit.value)
                        arms.append(build_match_arm(pat.span, pattern, body))
                case AST.EnumPattern():
                    body = self.check_block(arm_block, ctx)
                    for ident in pat.variants:
                        variant = self.__resolve_enum_variant(ident, value_expr.type_id, ctx)
                        pattern = HIR.EnumPattern(pat.span, variant, None)
                        arms.append(build_match_arm(pat.span, pattern, body))
                case AST.PayloadPattern():
                    variant = self.__resolve_enum_variant(pat.variant, value_expr.type_id, ctx)
                    if variant.payload_type is None:
                        raise AnalysisError(f"Variant '{pat.variant.name}' has no payload to bind", pat.span)
                    payload_ty = ctx.type_ctx[variant.payload_type]
                    assert isinstance(payload_ty, Type.StructType)
                    field_types = [f.type_id for f in ctx.type_ctx.get_struct_fields(payload_ty.type_id)]
                    if len(pat.fields) != len(field_types):
                        raise AnalysisError(
                            f"Pattern for variant '{pat.variant.name}' binds {len(pat.fields)} names but variant payload has {len(field_types)} fields",
                            pat.span,
                        )
                    ctx.enter_scope()
                    try:
                        unpack_fields: list[int] = []
                        for ident, ftype in zip(pat.fields, field_types):
                            sym_id = self.__declare_local_symbol(ident, ftype, ctx)
                            unpack_fields.append(sym_id)
                        body = self.check_block(arm_block, ctx)
                        pattern = HIR.EnumPattern(pat.span, variant, unpack_fields)
                        arms.append(build_match_arm(pat.span, pattern, body))
                    finally:
                        ctx.exit_scope()
                case AST.WildcardPattern():
                    body = self.check_block(arm_block, ctx)
                    arms.append(build_match_arm(pat.span, None, body))
                case _:
                    raise AnalysisError(f"Unsupported pattern type {type(pat).__name__}", pat.span)

        out.append(build_match(stmt.span, value_expr, arms))

    def __resolve_enum_variant(self, ident: AST.Identifier, enum_type_id: int, ctx: SemCtx) -> Type.EnumVariant:
        """Resolve an enum variant name to its EnumVariant definition."""
        enum_ty = ctx.type_ctx[enum_type_id]
        assert isinstance(enum_ty, Type.EnumType)
        variant = enum_ty.get_variant_by_name(ident.name, ctx.type_ctx)
        if variant is None:
            raise AnalysisError(f"Unknown enum variant '{ident.name}'", ident.span)
        return variant

    def __lower_match_with_partial_eq(self, stmt: AST.Match, value_expr: HIR.Expr, out: List[HIR.Stmt], ctx: SemCtx) -> None:
        """Lower `match` to an if-chain using PartialEq comparisons."""
        cond_and_blocks: list[tuple[HIR.Expr, HIR.Block]] = []
        default_block: HIR.Block | None = None

        for pat, arm_block in stmt.arms:
            if isinstance(pat, AST.WildcardPattern):
                default_block = self.check_block(arm_block, ctx)
                continue

            cond_expr = self.__pattern_to_eq_cond(pat, value_expr, ctx)
            body = self.check_block(arm_block, ctx)
            cond_and_blocks.append((cond_expr, body))

        out.append(build_if_chain(stmt.span, cond_and_blocks, default_block))

    def __pattern_to_eq_cond(self, pat: AST.Pattern, value_expr: HIR.Expr, ctx: SemCtx) -> HIR.Expr:
        """Build a boolean expression testing *value_expr* against *pat* using PartialEq."""
        conds: list[HIR.Expr] = []

        match pat:
            case AST.IntPattern():
                for lit in pat.values:
                    rhs = HIR.IntLiteral(span=lit.span, value=lit.value, type_id=value_expr.type_id, is_place=False)
                    eq_res = self.__expr.call_eq(value_expr, rhs)
                    conds.append(eq_res)
            case AST.CharPattern():
                for lit in pat.values:
                    rhs = HIR.CharLiteral(span=lit.span, value=lit.value, type_id=value_expr.type_id, is_place=False)
                    eq_res = self.__expr.call_eq(value_expr, rhs)
                    conds.append(eq_res)
            case AST.StrPattern():
                for lit in pat.values:
                    rhs = HIR.StrLiteral(span=lit.span, value=lit.value, type_id=value_expr.type_id, is_place=False)
                    eq_res = self.__expr.call_eq(value_expr, rhs)
                    conds.append(eq_res)
            case AST.EnumPattern():
                enum_ty = ctx.type_ctx[value_expr.type_id]
                assert isinstance(enum_ty, Type.EnumType)
                for ident in pat.variants:
                    variant = enum_ty.get_variant_by_name(ident.name, ctx.type_ctx)
                    if variant is None:
                        raise AnalysisError(f"Unknown enum variant '{ident.name}'", ident.span)
                    rhs = HIR.VariantConstruct(span=ident.span, enum_id=value_expr.type_id, variant=variant, args=None, type_id=value_expr.type_id, is_place=False)
                    eq_res = self.__expr.call_eq(value_expr, rhs)
                    conds.append(eq_res)
            case AST.PayloadPattern():
                raise AnalysisError("Payload patterns are not supported by PartialEq-based lowering", pat.span)
            case _:
                raise AnalysisError(f"Pattern type {type(pat).__name__} not supported by PartialEq lowering", pat.span)

        if len(conds) == 0:
            return HIR.BoolLiteral(span=pat.span, value=False, type_id=TypeCtx.bool_id, is_place=False)

        expr = conds[0]
        for c in conds[1:]:
            expr = HIR.Binary(span=expr.span, op=BinaryOperator.LogicalOr, left=expr, right=c, type_id=TypeCtx.bool_id, is_place=False)
        return expr
