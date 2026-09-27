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
from compiler.frontend.parse.ast_traversal import AstRewriter, AstVisitor
from compiler.frontend.parse.operator import BinaryOperator, UnaryOperator
from compiler.utils.log import CompilerLog


def ch_desugar():
    return CompilerLog.get("desugar")


class _BlockPassVisitor(AstVisitor):
    def __init__(self, processor: Callable[[AST.Block], None]) -> None:
        self.__processor = processor

    def leave_expr(self, expr: AST.Expr) -> None:
        if isinstance(expr, AST.Block):
            self.__processor(expr)


class _ExprDesugarRewriter(AstRewriter):
    def rewrite_expr(self, expr: AST.Expr) -> AST.Expr:
        super().rewrite_expr(expr)
        if isinstance(expr, AST.Binary):
            if expr.op == BinaryOperator.Range:
                return AST.Call(
                    span=expr.span,
                    callee=AST.Identifier(span=expr.span, name="Range"),
                    args=[
                        AST.Arg(span=expr.left.span, name=None, value=expr.left),
                        AST.Arg(span=expr.right.span, name=None, value=expr.right),
                    ],
                )
            if expr.op == BinaryOperator.In:
                return AST.MethodCall(
                    span=expr.span,
                    receiver=expr.right,
                    method_name=AST.Identifier(span=expr.span, name="contains"),
                    generics=[],
                    args=[AST.Arg(span=expr.left.span, name=None, value=expr.left)],
                )
        return expr


class Desugar:
    def __init__(self, program: AST.Program):
        self.__program = program

    def run(self) -> None:
        """Lower control flow, then rewrite range and membership expressions."""
        for item in self.__program.items:
            match item:
                case AST.FuncDef():
                    self.__desugar_body(item.body)
                case AST.Impl():
                    for method in item.items:
                        self.__desugar_body(method.body)
                case AST.TraitDef():
                    for trait_item in item.items:
                        if isinstance(trait_item, AST.MethodDef):
                            self.__desugar_body(trait_item.body)
                case _:
                    continue

    def __desugar_body(self, body: AST.Block) -> None:
        _BlockPassVisitor(self.__process_control_flow).visit_expr(body)
        _BlockPassVisitor(self.__process_expr_rewrite).visit_expr(body)

    # ------------------------------------------------------------------
    # Pass 1 — control-flow lowering
    # ------------------------------------------------------------------

    def __process_control_flow(self, block: AST.Block) -> None:
        """Lower for, while, assert, and elif chains in a single traversal."""
        desugared_stmts: list[AST.Expr] = []

        for stmt in block.stmts:
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
        rewriter = _ExprDesugarRewriter()
        for stmt in block.stmts:
            # Unwrap Semi to apply expression rewriting to the inner expression
            target = stmt.expr if isinstance(stmt, AST.Semi) else stmt
            rewriter.rewrite_statement_expressions(target)

    def __desugar_assert(self, stmt: AST.Assert) -> AST.If:
        message = stmt.message
        if message is None:
            message_value = f"assertion failed at {stmt.span}"
            message = AST.Literal(
                span=stmt.span,
                literal=Tok.StrLiteral(raw=f"\"{message_value}\"", span=stmt.span, value=message_value),
            )

        panic_call = AST.Builtin(
            span=stmt.span,
            kind=AST.BuiltinKind.Panic,
            type_args=[],
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
        some_arm = AST.MatchArm(
            span=stmt.var_name.span,
            pattern=AST.ConstructPattern(
                span=stmt.var_name.span,
                name=AST.Identifier(span=stmt.var_name.span, name="Some"),
                qualifier=None,
                positional=[AST.NamePattern(span=stmt.var_name.span, name=stmt.var_name)],
                named=None,
            ),
            guard=None,
            body=stmt.body,
        )
        none_arm = AST.MatchArm(
            span=stmt.span,
            pattern=AST.NamePattern(
                span=stmt.span,
                name=AST.Identifier(span=stmt.span, name="None"),
            ),
            guard=None,
            body=AST.Block(span=stmt.span, stmts=[AST.Break(span=stmt.span)]),
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
