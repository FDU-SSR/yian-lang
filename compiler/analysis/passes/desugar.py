"""
Desugar ASTs by desugaring syntactic sugar into more fundamental constructs.

Rules:

- for item in iterable { body } => { iter = iterable.into_iter(); loop { match iter.next() { Some(item) { body }, None { break } } } }
- while cond { body } => loop { if not cond { break } body }
- assert => if + panic
- if cond { body } elif cond2 { body2 } else { body3 } => nested ifs
- range expressions (a..b) => Range(a, b)
- member test (x in y) => y.contains(x)
"""
from __future__ import annotations


from typing import Callable

from compiler.frontend.lex import token as Tok
from compiler.frontend.parse import ast as AST
from compiler.frontend.parse import ast_type as ASTTy
from compiler.frontend.parse.operator import BinaryOperator, UnaryOperator
from compiler.utils.log import CompilerLog


def ch_desugar():
    return CompilerLog.get("desugar")


class Desugar:
    def __init__(self, program: AST.Program):
        self.__program = program

    def run(self) -> None:
        """Apply desugaring in two passes (down from six).

        Pass 1 — control-flow lowering: for, while, assert, elif chains.
        Pass 2 — expression rewriting: range (a..b), member test (x in y).

        Each pass walks the entire AST once, applying all transformations
        that share the same traversal pattern.
        """
        for item in self.__program.items:
            match item:
                case AST.FuncDef():
                    self.__process_control_flow(item.body)
                    self.__process_expr_rewrite(item.body)
                case AST.Impl():
                    for method in item.items:
                        self.__process_control_flow(method.body)
                        self.__process_expr_rewrite(method.body)
                case AST.TraitDef():
                    for trait_item in item.items:
                        if isinstance(trait_item, AST.MethodDef):
                            self.__process_control_flow(trait_item.body)
                            self.__process_expr_rewrite(trait_item.body)
                case _:
                    continue

    # ------------------------------------------------------------------
    # shared traversal helpers
    # ------------------------------------------------------------------

    def __recurse_blocks(self, stmt: AST.Expr, processor: Callable[[AST.Block], None]) -> None:
        """Apply *processor* to every nested block inside *stmt*."""
        match stmt:
            case AST.Block():
                processor(stmt)
            case AST.If():
                processor(stmt.then_branch)
                for _, elif_branch in stmt.elif_branches:
                    processor(elif_branch)
                if stmt.else_branch is not None:
                    processor(stmt.else_branch)
            case AST.ComptimeIf():
                processor(stmt.then_branch)
                processor(stmt.else_branch)
            case AST.For():
                processor(stmt.body)
            case AST.While():
                processor(stmt.body)
            case AST.Loop():
                processor(stmt.body)
            case AST.Match():
                for _, arm_block in stmt.arms:
                    processor(arm_block)
            case AST.Semi():
                # A trailing semicolon wraps the statement; recurse through it
                # so nested control flow is still desugared.
                self.__recurse_blocks(stmt.expr, processor)
            case AST.Defer():
                self.__recurse_blocks(stmt.action, processor)
            case _:
                return

    # ------------------------------------------------------------------
    # Pass 1 — control-flow lowering
    # ------------------------------------------------------------------

    def __process_control_flow(self, block: AST.Block) -> None:
        """Lower for, while, assert, and elif chains in a single traversal."""
        desugared_stmts: list[AST.Expr] = []

        for stmt in block.stmts:
            self.__recurse_blocks(stmt, self.__process_control_flow)

            # Unwrap Semi to check for desugar-able constructs inside
            inner = stmt.expr if isinstance(stmt, AST.Semi) else stmt
            semi = isinstance(stmt, AST.Semi)
            desugared: AST.Expr | None = None

            if isinstance(inner, AST.For):
                desugared = self.__desugar_for(inner)
            elif isinstance(inner, AST.While):
                desugared = self.__desugar_while(inner)
            elif isinstance(inner, AST.Assert):
                desugared = self.__desugar_assert(inner)
            elif isinstance(inner, AST.If) and inner.elif_branches:
                desugared = self.__desugar_if_chain(inner)
            elif isinstance(inner, AST.Defer):
                action = inner.action
                if isinstance(action, AST.For):
                    inner.action = self.__desugar_for(action)
                elif isinstance(action, AST.While):
                    inner.action = self.__desugar_while(action)
                elif isinstance(action, AST.Assert):
                    inner.action = self.__desugar_assert(action)
                elif isinstance(action, AST.If) and action.elif_branches:
                    inner.action = self.__desugar_if_chain(action)

            if desugared is not None:
                ch_desugar().trace(lambda: f"desugar {type(inner).__name__}")
                desugared_stmts.append(AST.Semi(span=stmt.span, expr=desugared) if semi else desugared)
            else:
                desugared_stmts.append(stmt)

        block.stmts = desugared_stmts

    # ------------------------------------------------------------------
    # Pass 2 — expression rewriting
    # ------------------------------------------------------------------

    def __process_expr_rewrite(self, block: AST.Block) -> None:
        """Desugar range expressions and member tests in a single traversal."""
        for stmt in block.stmts:
            self.__recurse_blocks(stmt, self.__process_expr_rewrite)
            # Unwrap Semi to apply expression rewriting to the inner expression
            target = stmt.expr if isinstance(stmt, AST.Semi) else stmt
            self.__rewrite_exprs_in_stmt(target)

    def __rewrite_exprs_in_stmt(self, stmt: AST.Expr) -> None:
        """Apply both range and member-test desugaring to all expressions in a statement."""
        self.__apply_expr_visitor(stmt, self.__desugar_range)
        self.__apply_expr_visitor(stmt, self.__desugar_member_test)

    def __desugar_assert(self, stmt: AST.Assert) -> AST.If:
        message = stmt.message
        if message is None:
            message_value = f"assertion failed at {stmt.span}"
            message = AST.Literal(
                span=stmt.span,
                literal=Tok.StrLiteral(raw=f"\"{message_value}\"", span=stmt.span, value=message_value),
            )

        panic_call = AST.BuiltinCall(
            span=stmt.span,
            kind=AST.BuiltinKind.Panic,
            args=[AST.Arg(span=message.span, name=None, value=message)],
        )

        return AST.If(
            span=stmt.span,
            condition=AST.Unary(span=stmt.condition.span, op=UnaryOperator.LogicalNot, operand=stmt.condition),
            then_branch=AST.Block(span=stmt.span, stmts=[panic_call]),
            elif_branches=[],
            else_branch=None,
        )

    def __desugar_while(self, stmt: AST.While) -> AST.Loop:
        break_if = AST.If(
            span=stmt.span,
            condition=AST.Unary(span=stmt.condition.span, op=UnaryOperator.LogicalNot, operand=stmt.condition),
            then_branch=AST.Block(span=stmt.span, stmts=[AST.Break(span=stmt.span)]),
            elif_branches=[],
            else_branch=None,
        )

        return AST.Loop(
            span=stmt.span,
            body=AST.Block(span=stmt.body.span, stmts=[break_if, stmt.body]),
        )

    def __desugar_for(self, stmt: AST.For) -> AST.Block:
        iter_name = AST.Identifier(span=stmt.span, name="%iter")
        iter_init = AST.MethodCall(
            span=stmt.iterable.span,
            receiver=stmt.iterable,
            method_name=AST.Identifier(span=stmt.iterable.span, name="into_iter"),
            generics=[],
            args=[],
        )
        iter_decl = AST.VarDecl(
            span=stmt.span,
            var_type=ASTTy.DeducedType(span=stmt.span),
            name=iter_name,
            init_expr=iter_init,
        )

        next_call = AST.MethodCall(
            span=stmt.span,
            receiver=AST.Identifier(span=stmt.span, name=iter_name.name),
            method_name=AST.Identifier(span=stmt.span, name="next"),
            generics=[],
            args=[],
        )
        some_arm = (
            AST.PayloadPattern(
                span=stmt.var_name.span,
                variant=AST.Identifier(span=stmt.var_name.span, name="Some"),
                fields=[stmt.var_name],
            ),
            stmt.body,
        )
        none_arm = (
            AST.EnumPattern(
                span=stmt.span,
                variants=[AST.Identifier(span=stmt.span, name="None")],
            ),
            AST.Block(span=stmt.span, stmts=[AST.Break(span=stmt.span)]),
        )

        return AST.Block(
            span=stmt.span,
            stmts=[
                iter_decl,
                AST.Loop(
                    span=stmt.span,
                    body=AST.Block(
                        span=stmt.body.span,
                        stmts=[AST.Match(span=stmt.span, expr=next_call, arms=[some_arm, none_arm])],
                    ),
                ),
            ],
        )

    def __desugar_if_chain(self, stmt: AST.If) -> AST.If:
        current_else = stmt.else_branch
        for elif_cond, elif_branch in reversed(stmt.elif_branches):
            current_else = AST.Block(
                span=elif_branch.span,
                stmts=[AST.If(
                    span=elif_branch.span,
                    condition=elif_cond,
                    then_branch=elif_branch,
                    elif_branches=[],
                    else_branch=current_else,
                )],
            )
        return AST.If(
            span=stmt.span,
            condition=stmt.condition,
            then_branch=stmt.then_branch,
            elif_branches=[],
            else_branch=current_else,
        )

    def __desugar_range(self, expr: AST.Expr) -> AST.Expr:
        """Recursively walk an expression tree and desugar any Binary(Range) nodes."""
        self.__walk_expr_children(expr, self.__desugar_range)

        if isinstance(expr, AST.Binary) and expr.op == BinaryOperator.Range:
            return AST.Call(
                span=expr.span,
                callee=AST.Identifier(span=expr.span, name="Range"),
                args=[
                    AST.Arg(span=expr.left.span, name=None, value=expr.left),
                    AST.Arg(span=expr.right.span, name=None, value=expr.right),
                ],
            )
        return expr

    def __desugar_member_test(self, expr: AST.Expr) -> AST.Expr:
        """Recursively walk an expression tree and desugar Binary(In) nodes to .contains() calls."""
        self.__walk_expr_children(expr, self.__desugar_member_test)

        if isinstance(expr, AST.Binary) and expr.op == BinaryOperator.In:
            return AST.MethodCall(
                span=expr.span,
                receiver=expr.right,
                method_name=AST.Identifier(span=expr.span, name="contains"),
                generics=[],
                args=[AST.Arg(span=expr.left.span, name=None, value=expr.left)],
            )
        return expr

    def __walk_expr_children(self, expr: AST.Expr, visitor: Callable[[AST.Expr], AST.Expr]) -> None:
        """Walk the immediate sub-expressions of an expression and apply the visitor to each."""
        match expr:
            case AST.Block():
                expr.stmts = [visitor(stmt) for stmt in expr.stmts]
            case AST.Semi():
                expr.expr = visitor(expr.expr)
            case AST.VarDecl() if expr.init_expr is not None:
                expr.init_expr = visitor(expr.init_expr)
            case AST.Return() if expr.expr is not None:
                expr.expr = visitor(expr.expr)
            case AST.Break() if expr.expr is not None:
                expr.expr = visitor(expr.expr)
            case AST.If():
                expr.condition = visitor(expr.condition)
                expr.elif_branches = [(visitor(cond), branch) for cond, branch in expr.elif_branches]
                expr.then_branch.stmts = [visitor(stmt) for stmt in expr.then_branch.stmts]
                for _, branch in expr.elif_branches:
                    branch.stmts = [visitor(stmt) for stmt in branch.stmts]
                if expr.else_branch is not None:
                    expr.else_branch.stmts = [visitor(stmt) for stmt in expr.else_branch.stmts]
            case AST.ComptimeIf():
                expr.condition = visitor(expr.condition)
                expr.then_branch.stmts = [visitor(stmt) for stmt in expr.then_branch.stmts]
                expr.else_branch.stmts = [visitor(stmt) for stmt in expr.else_branch.stmts]
            case AST.For():
                expr.iterable = visitor(expr.iterable)
                expr.body.stmts = [visitor(stmt) for stmt in expr.body.stmts]
            case AST.While():
                expr.condition = visitor(expr.condition)
                expr.body.stmts = [visitor(stmt) for stmt in expr.body.stmts]
            case AST.Loop():
                expr.body.stmts = [visitor(stmt) for stmt in expr.body.stmts]
            case AST.Match():
                expr.expr = visitor(expr.expr)
                for _, branch in expr.arms:
                    branch.stmts = [visitor(stmt) for stmt in branch.stmts]
            case AST.Assert():
                expr.condition = visitor(expr.condition)
                if expr.message is not None:
                    expr.message = visitor(expr.message)
            case AST.Delete():
                expr.target = visitor(expr.target)
            case AST.Defer():
                expr.action = visitor(expr.action)
            case AST.ClosureExpr():
                for capture in expr.captures:
                    capture.expr = visitor(capture.expr)
                expr.body.stmts = [visitor(stmt) for stmt in expr.body.stmts]
            case AST.Binary():
                expr.left = visitor(expr.left)
                expr.right = visitor(expr.right)
            case AST.Unary():
                expr.operand = visitor(expr.operand)
            case AST.Call():
                expr.callee = visitor(expr.callee)
                for arg in expr.args:
                    arg.value = visitor(arg.value)
            case AST.BuiltinCall():
                for arg in expr.args:
                    arg.value = visitor(arg.value)
            case AST.BitCast():
                expr.value = visitor(expr.value)
            case AST.MethodCall():
                expr.receiver = visitor(expr.receiver)
                for arg in expr.args:
                    arg.value = visitor(arg.value)
            case AST.FieldAccess():
                expr.receiver = visitor(expr.receiver)
            case AST.DynValue():
                expr.value = visitor(expr.value)
            case AST.DynBuffer():
                expr.size = visitor(expr.size)
                expr.element = visitor(expr.element)
            case AST.Alloc():
                expr.count = visitor(expr.count)
            case AST.Tuple():
                expr.elements = [visitor(e) for e in expr.elements]
            case AST.Array():
                expr.elements = [visitor(e) for e in expr.elements]
            case AST.ArrayRepeat():
                expr.element = visitor(expr.element)
                expr.count = visitor(expr.count)
            case _:
                pass

    def __apply_expr_visitor(self, stmt: AST.Expr, expr_visitor: Callable[[AST.Expr], AST.Expr]) -> None:
        """Apply expr_visitor to all expression fields within a statement."""
        match stmt:
            case AST.VarDecl(init_expr=expr) if expr is not None:
                stmt.init_expr = expr_visitor(expr)
            case AST.Return(expr=expr) if expr is not None:
                stmt.expr = expr_visitor(expr)
            case AST.If():
                stmt.condition = expr_visitor(stmt.condition)
                for i in range(len(stmt.elif_branches)):
                    cond, body = stmt.elif_branches[i]
                    stmt.elif_branches[i] = (expr_visitor(cond), body)
            case AST.ComptimeIf():
                stmt.condition = expr_visitor(stmt.condition)
            case AST.While():
                stmt.condition = expr_visitor(stmt.condition)
            case AST.Match():
                stmt.expr = expr_visitor(stmt.expr)
            case AST.Assert():
                stmt.condition = expr_visitor(stmt.condition)
                if stmt.message is not None:
                    stmt.message = expr_visitor(stmt.message)
            case AST.Delete():
                stmt.target = expr_visitor(stmt.target)
            case AST.For():
                stmt.iterable = expr_visitor(stmt.iterable)
            case AST.Binary():
                stmt.left = expr_visitor(stmt.left)
                stmt.right = expr_visitor(stmt.right)
            case AST.Unary():
                stmt.operand = expr_visitor(stmt.operand)
            case AST.Call():
                stmt.callee = expr_visitor(stmt.callee)
                for arg in stmt.args:
                    arg.value = expr_visitor(arg.value)
            case AST.BuiltinCall():
                for arg in stmt.args:
                    arg.value = expr_visitor(arg.value)
            case AST.BitCast():
                stmt.value = expr_visitor(stmt.value)
            case AST.MethodCall():
                stmt.receiver = expr_visitor(stmt.receiver)
                for arg in stmt.args:
                    arg.value = expr_visitor(arg.value)
            case AST.FieldAccess():
                stmt.receiver = expr_visitor(stmt.receiver)
            case AST.Tuple():
                stmt.elements = [expr_visitor(e) for e in stmt.elements]
            case AST.Array():
                stmt.elements = [expr_visitor(e) for e in stmt.elements]
            case AST.ArrayRepeat():
                stmt.element = expr_visitor(stmt.element)
                stmt.count = expr_visitor(stmt.count)
            case AST.DynValue():
                stmt.value = expr_visitor(stmt.value)
            case AST.DynBuffer():
                stmt.size = expr_visitor(stmt.size)
                stmt.element = expr_visitor(stmt.element)
            case AST.Alloc():
                stmt.count = expr_visitor(stmt.count)
            case AST.Defer():
                stmt.action = expr_visitor(stmt.action)
            case _:
                pass
