from __future__ import annotations

from typing import List

from compiler.analysis.error import AnalysisError
from compiler.analysis.passes.expr_checker import ExprChecker
from compiler.analysis.passes.hir_builder import build_block, build_enum_match, build_enum_match_arm, build_loop
from compiler.analysis.passes.sem_ctx import SemCtx
from compiler.analysis.symbol.symbol import SymbolKind
from compiler.analysis.ty import ty as Type
from compiler.analysis.ty.context import TypeCtx
from compiler.analysis.unit import hir as HIR
from compiler.frontend.parse import ast as AST


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
            case AST.For():
                self.check_for(stmt, out, ctx)
            case AST.While():
                self.check_while(stmt, out, ctx)
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
            case AST.Assert():
                self.check_assert(stmt, out, ctx)
            case AST.Delete():
                self.check_delete(stmt, out, ctx)
            case _:
                out.append(self.__expr.eval(stmt))

    def check_var_decl(self, stmt: AST.VarDecl, out: List[HIR.Stmt], ctx: SemCtx) -> None:
        assert ctx.symbol_ctx is not None

        var_type_id = ctx.resolve_type(stmt.var_type)
        symbol_id = self.__declare_local_symbol(stmt.name, var_type_id, ctx)

        if stmt.init_expr is not None:
            init_expr = self.__expr.value(stmt.init_expr, var_type_id)
            var = HIR.Var(span=stmt.name.span, symbol_id=symbol_id, type_id=var_type_id, is_place=True)
            out.append(self.__expr.assign(stmt.span, var, init_expr.hir))

    def check_if(self, stmt: AST.If, out: List[HIR.Stmt], ctx: SemCtx) -> None:
        assert ctx.symbol_ctx is not None

        cond_expr = self.__expr.value(stmt.condition, expected=TypeCtx.bool_id)
        then_block = self.check_block(stmt.then_branch, ctx)

        elif_blocks: list[tuple[HIR.Expr, HIR.Block]] = []
        for elif_branch in stmt.elif_branches:
            elif_cond_expr = self.__expr.value(elif_branch[0], expected=TypeCtx.bool_id)
            elif_block = self.check_block(elif_branch[1], ctx)
            elif_blocks.append((elif_cond_expr.hir, elif_block))

        else_block = self.check_block(stmt.else_branch, ctx) if stmt.else_branch is not None else None

        current_else_block = else_block
        for elif_cond_expr, elif_block in reversed(elif_blocks):
            current_else_block = build_block(
                elif_block.span,
                [HIR.If(
                    span=elif_cond_expr.span,
                    cond=elif_cond_expr,
                    then_branch=elif_block,
                    else_branch=current_else_block,
                )]
            )

        out.append(HIR.If(span=stmt.span, cond=cond_expr.hir, then_branch=then_block, else_branch=current_else_block))

    def check_for(self, stmt: AST.For, out: List[HIR.Stmt], ctx: SemCtx) -> None:
        assert ctx.symbol_ctx is not None

        ctx.enter_scope()
        try:
            iterable_expr = self.__expr.value(stmt.iterable)
            iter_expr = self.__expr.call_method(iterable_expr.hir, "into_iter", None, [])

            iter_var_type_id = iter_expr.type_id
            iter_symbol_id = self.__declare_local_symbol(AST.Identifier(span=stmt.var_name.span, name="%iter"), iter_var_type_id, ctx)
            iter_var = HIR.Var(span=stmt.span, symbol_id=iter_symbol_id, type_id=iter_var_type_id, is_place=True)
            iter_init = self.__expr.assign(stmt.span, iter_var, iter_expr.hir)

            item_type_id = ctx.type_ctx.iter_item_type(iter_var_type_id)
            item_symbol_id = self.__declare_local_symbol(stmt.var_name, item_type_id, ctx)
            body_block = self.check_block(stmt.body, ctx)
            next_method_call = self.__expr.call_method(iter_var, "next", None, [])
            some_variant, none_variant = self.__enum_variants(next_method_call.type_id, ["Some", "None"], ctx)

            some_arm = build_enum_match_arm(stmt.span, some_variant, [item_symbol_id], body_block)
            none_arm = build_enum_match_arm(stmt.span, none_variant, None, build_block(stmt.span, [HIR.Break(span=stmt.span)]))
            next_match_stmt = build_enum_match(stmt.span, next_method_call.hir, [some_arm, none_arm])
            loop_stmt = build_loop(stmt.span, [next_match_stmt])
            out.append(build_block(stmt.span, [iter_init, loop_stmt]))
        finally:
            ctx.exit_scope()

    def __declare_local_symbol(self, name: AST.Identifier, type_id: int, ctx: SemCtx) -> int:
        assert ctx.symbol_ctx is not None

        symbol_id = ctx.symbol_ctx.add_symbol(name.name, SymbolKind.Variable, type_id)
        if symbol_id is None:
            raise AnalysisError(f"Variable '{name.name}' is already defined in the current scope", name.span)
        ctx.push_local(symbol_id)
        return symbol_id

    def __enum_variants(self, type_id: int, variant_names: list[str], ctx: SemCtx) -> tuple[Type.EnumVariant, ...]:
        option_ty = ctx.type_ctx[type_id]
        assert isinstance(option_ty, Type.EnumType)

        variants: list[Type.EnumVariant] = []
        for variant_name in variant_names:
            variant = option_ty.get_variant_by_name(variant_name, ctx.type_ctx)
            assert variant is not None
            variants.append(variant)

        return tuple(variants)

    def check_while(self, stmt: AST.While, out: List[HIR.Stmt], ctx: SemCtx) -> None:
        assert ctx.symbol_ctx is not None

        cond_expr = self.__expr.value(stmt.condition, expected=TypeCtx.bool_id)
        body_block = self.check_block(stmt.body, ctx)

        not_cond_expr = self.__expr.logical_not(cond_expr.hir)
        break_stmt = HIR.Break(span=stmt.span)
        if_stmt = HIR.If(
            span=cond_expr.hir.span,
            cond=not_cond_expr.hir,
            then_branch=build_block(stmt.span, [break_stmt]),
            else_branch=None
        )
        out.append(build_loop(stmt.span, [if_stmt, body_block]))

    def check_loop(self, stmt: AST.Loop, out: List[HIR.Stmt], ctx: SemCtx) -> None:
        body_block = self.check_block(stmt.body, ctx)
        out.append(HIR.Loop(span=stmt.span, body=body_block))

    def check_match(self, stmt: AST.Match, out: List[HIR.Stmt], ctx: SemCtx) -> None:
        raise NotImplementedError()

    def check_return(self, stmt: AST.Return, out: List[HIR.Stmt], ctx: SemCtx) -> None:
        raise NotImplementedError()

    def check_break(self, stmt: AST.Break, out: List[HIR.Stmt], ctx: SemCtx) -> None:
        raise NotImplementedError()

    def check_continue(self, stmt: AST.Continue, out: List[HIR.Stmt], ctx: SemCtx) -> None:
        raise NotImplementedError()

    def check_assert(self, stmt: AST.Assert, out: List[HIR.Stmt], ctx: SemCtx) -> None:
        raise NotImplementedError()

    def check_delete(self, stmt: AST.Delete, out: List[HIR.Stmt], ctx: SemCtx) -> None:
        raise NotImplementedError()
