from __future__ import annotations

from typing import List

from compiler.analysis.error import AnalysisError
from compiler.analysis.passes.expr_checker import ExprChecker
from compiler.analysis.passes.hir_builder import (build_block,
                                                  build_enum_match,
                                                  build_enum_match_arm,
                                                  build_if_chain, build_switch,
                                                  build_switch_arm)
from compiler.analysis.passes.sem_ctx import LoopFrame, LoopKind, SemCtx
from compiler.analysis.symbol.symbol import SymbolKind
from compiler.analysis.ty import ty as Type
from compiler.analysis.ty.context import TypeCtx
from compiler.analysis.unit import hir as HIR
from compiler.frontend.parse import ast as AST
from compiler.frontend.parse import ast_type as ASTTy
from compiler.frontend.parse.operator import BinaryOperator


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

        # Path 1: integer-like or C-style enum -> Switch
        if isinstance(value_type, Type.IntType) or isinstance(value_type, Type.CharType):
            self.__lower_match_as_switch(stmt, value_expr, out, ctx)
            return

        # Path 2/3: enums -> either C-like (no payloads) or payload-carrying variants
        if isinstance(value_type, Type.EnumType):
            variants = value_type.get_variants(ctx.type_ctx)
            if all(v.payload_type is None for v in variants):
                # C-like enum, can use switch lowering
                self.__lower_match_as_switch(stmt, value_expr, out, ctx)
                return
            # payload-carrying enum: need unpacking per-arm
            self.__lower_match_enum_unpack(stmt, value_expr, out, ctx)
            return

        # Fallback: try equality-based lowering using PartialEq (method calls)
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

    def __declare_local_symbol(self, name: AST.Identifier, type_id: int, ctx: SemCtx) -> int:
        assert ctx.symbol_ctx is not None

        symbol_id = ctx.symbol_ctx.add_symbol(name.name, SymbolKind.Variable, type_id)
        if symbol_id is None:
            raise AnalysisError(f"Variable '{name.name}' is already defined in the current scope", name.span)
        ctx.push_local(symbol_id)
        return symbol_id

    def __lower_match_as_switch(self, stmt: AST.Match, value_expr: HIR.Expr, out: List[HIR.Stmt], ctx: SemCtx) -> None:
        """Lower `match` to a `Switch` HIR when the scrutinee is integer-like or
        a C-style enum. This is a stub: implement pattern -> integer mapping and
        build `HIR.SwitchArm`s, then emit `hir_builder.build_switch`.
        """
        # Build switch arms from AST patterns. Support IntPattern, EnumPattern, WildcardPattern.
        arms: list[HIR.SwitchArm] = []

        # determine int width in bytes
        switch_ty = ctx.type_ctx[value_expr.type_id]
        if isinstance(switch_ty, Type.IntType):
            int_width = switch_ty.size
        elif isinstance(switch_ty, Type.CharType):
            int_width = 4  # unicode scalar values can be up to 4 bytes
        else:
            int_width = 4  # default width for enums / other types represented as integers

        for pat, arm_block in stmt.arms:
            # lower arm body
            body = self.check_block(arm_block, ctx)

            if isinstance(pat, AST.IntPattern):
                for lit in pat.values:
                    val = lit.value
                    arms.append(build_switch_arm(pat.span, val, int_width, body))
            elif isinstance(pat, AST.EnumPattern):
                # each variant name maps to a discriminant
                enum_ty = ctx.type_ctx[value_expr.type_id]
                assert isinstance(enum_ty, Type.EnumType)
                for ident in pat.variants:
                    variant = enum_ty.get_variant_by_name(ident.name, ctx.type_ctx)
                    if variant is None:
                        raise AnalysisError(f"Unknown enum variant '{ident.name}'", ident.span)
                    arms.append(build_switch_arm(pat.span, variant.discriminant, int_width, body))
            elif isinstance(pat, AST.WildcardPattern):
                # wildcard -> default arm
                arms.append(build_switch_arm(pat.span, None, int_width, body))
            else:
                # unsupported pattern for switch lowering; signal via AnalysisError
                raise AnalysisError(f"Pattern type {type(pat).__name__} not supported by switch lowering", pat.span)

        # Emit the switch HIR
        out.append(build_switch(stmt.span, value_expr, arms))

    def __lower_match_with_partial_eq(self, stmt: AST.Match, value_expr: HIR.Expr, out: List[HIR.Stmt], ctx: SemCtx) -> None:
        # Build condition -> block pairs for each arm, using PartialEq-based
        # tests for non-switchable patterns. Concrete equality construction
        # and pattern decomposition are delegated to helper interfaces below.
        cond_and_blocks: list[tuple[HIR.Expr, HIR.Block]] = []
        default_block: HIR.Block | None = None

        for pat, arm_block in stmt.arms:
            # wildcard becomes the default arm
            if isinstance(pat, AST.WildcardPattern):
                default_block = self.check_block(arm_block, ctx)
                continue

            # build a boolean-testing expression for this pattern using PartialEq
            cond_expr = self.__pattern_to_eq_cond(pat, value_expr, ctx)
            body = self.check_block(arm_block, ctx)
            cond_and_blocks.append((cond_expr, body))

        out.append(build_if_chain(stmt.span, cond_and_blocks, default_block))

    def __pattern_to_eq_cond(self, pat: AST.Pattern, value_expr: HIR.Expr, ctx: SemCtx) -> HIR.Expr:
        # Handle simple literal patterns by constructing HIR literal nodes
        # and using ExprChecker.call_eq to generate boolean expressions.
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
                # Payload patterns introduce bindings; equality-based lowering
                # cannot handle binding patterns here.
                raise AnalysisError("Payload patterns are not supported by PartialEq-based lowering", pat.span)
            case _:
                raise AnalysisError(f"Pattern type {type(pat).__name__} not supported by PartialEq lowering", pat.span)

        # combine conditions with logical OR if multiple alternatives
        if len(conds) == 0:
            # defensive: no condition built -> false
            return HIR.BoolLiteral(span=pat.span, value=False, type_id=TypeCtx.bool_id, is_place=False)

        expr = conds[0]
        for c in conds[1:]:
            expr = HIR.Binary(span=expr.span, op=BinaryOperator.LogicalOr, left=expr, right=c, type_id=TypeCtx.bool_id, is_place=False)
        return expr

    def __lower_match_enum_unpack(self, stmt: AST.Match, value_expr: HIR.Expr, out: List[HIR.Stmt], ctx: SemCtx) -> None:
        assert ctx.symbol_ctx is not None

        enum_ty = ctx.type_ctx[value_expr.type_id]
        assert isinstance(enum_ty, Type.EnumType)

        # To avoid re-evaluating the scrutinee, store it into a temporary local
        tmp_ident = AST.Identifier(span=stmt.span, name="%match")
        tmp_sym = self.__declare_local_symbol(tmp_ident, value_expr.type_id, ctx)
        tmp_var = HIR.Var(span=stmt.span, symbol_id=tmp_sym, type_id=value_expr.type_id, is_place=True)
        init_stmt = self.__expr.assign(stmt.span, tmp_var, value_expr)

        arms: list[HIR.MatchArm] = []

        for pat, arm_block in stmt.arms:
            if isinstance(pat, AST.WildcardPattern):
                body = self.check_block(arm_block, ctx)
                arms.append(build_enum_match_arm(pat.span, None, None, body))
                continue

            if isinstance(pat, AST.EnumPattern):
                for ident in pat.variants:
                    variant = enum_ty.get_variant_by_name(ident.name, ctx.type_ctx)
                    if variant is None:
                        raise AnalysisError(f"Unknown enum variant '{ident.name}'", ident.span)

                    # If variant has payload, but pattern did not bind fields,
                    # treat as matching discriminant only (ignore payload).
                    body = self.check_block(arm_block, ctx)
                    arms.append(build_enum_match_arm(pat.span, variant, None, body))
                continue

            # PayloadPattern: variant with explicit field bindings
            if isinstance(pat, AST.PayloadPattern):
                variant = enum_ty.get_variant_by_name(pat.variant.name, ctx.type_ctx)
                if variant is None:
                    raise AnalysisError(f"Unknown enum variant '{pat.variant.name}'", pat.variant.span)

                if variant.payload_type is None:
                    raise AnalysisError(f"Variant '{pat.variant.name}' has no payload to bind", pat.span)

                # Determine payload field types. Support tuple payloads or single-field payloads.
                payload_ty_id = variant.payload_type
                payload_ty = ctx.type_ctx[payload_ty_id]
                if isinstance(payload_ty, Type.TupleType):
                    field_types = payload_ty.element_types
                else:
                    field_types = [payload_ty_id]

                if len(pat.fields) != len(field_types):
                    raise AnalysisError(f"Pattern for variant '{pat.variant.name}' binds {len(pat.fields)} names but variant payload has {len(field_types)} fields", pat.span)

                # Enter a scope for the arm's bindings, declare symbols, lower body, then exit scope.
                ctx.enter_scope()
                try:
                    unpack_fields: list[int] = []
                    for ident, ftype in zip(pat.fields, field_types):
                        sym_id = self.__declare_local_symbol(ident, ftype, ctx)
                        unpack_fields.append(sym_id)

                    body = self.check_block(arm_block, ctx)
                    arms.append(build_enum_match_arm(pat.span, variant, unpack_fields, body))
                finally:
                    ctx.exit_scope()
                continue

            # Other patterns are unsupported in this path
            raise AnalysisError(f"Pattern type {type(pat).__name__} not supported by enum-unpack lowering (basic path)", pat.span)

        match_stmt = build_enum_match(stmt.span, tmp_var, arms)
        out.append(build_block(stmt.span, [init_stmt, match_stmt]))
