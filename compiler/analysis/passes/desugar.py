"""Normalize parameter patterns and lower surface control flow into core AST nodes."""
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


class _ClosureParamNormalizer(AstVisitor):
    def __init__(self, normalize: Callable[[list[AST.VarInfo | AST.PatternParam], AST.Block], list[AST.VarInfo | AST.PatternParam]]) -> None:
        self.__normalize = normalize

    def enter_expr(self, expr: AST.Expr) -> bool:
        if isinstance(expr, AST.ClosureExpr):
            expr.params = self.__normalize(expr.params, expr.body)
        return True


class _ControlFlowRewriter(AstRewriter):
    def __init__(self, transform: Callable[[AST.Expr], AST.Expr]) -> None:
        self.__transform = transform

    def rewrite_expr(self, expr: AST.Expr) -> AST.Expr:
        super().rewrite_expr(expr)
        transformed = self.__transform(expr)
        return expr if transformed is expr else self.rewrite_expr(transformed)


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
        """Normalize callable parameters and lower control-flow syntax."""
        for item in self.__program.items:
            match item:
                case AST.FuncDef():
                    item.params = self.__normalize_params(item.params, item.body)
                    self.__desugar_body(item.body)
                case AST.Impl():
                    for method in item.items:
                        method.decl.params = self.__normalize_params(method.decl.params, method.body)
                        self.__desugar_body(method.body)
                case AST.TraitDef():
                    for trait_item in item.items:
                        if isinstance(trait_item, AST.MethodDef):
                            trait_item.decl.params = self.__normalize_params(trait_item.decl.params, trait_item.body)
                            self.__desugar_body(trait_item.body)
                case _:
                    continue

    def __desugar_body(self, body: AST.Block) -> None:
        _ClosureParamNormalizer(self.__normalize_params).visit_expr(body)
        _ControlFlowRewriter(self.__transform_control_flow).rewrite_block(body)
        _ExprDesugarRewriter().rewrite_block(body)

    def __normalize_params(
        self, params: list[AST.VarInfo | AST.PatternParam], body: AST.Block,
    ) -> list[AST.VarInfo | AST.PatternParam]:
        normalized: list[AST.VarInfo | AST.PatternParam] = []
        for index, param in enumerate(params):
            if isinstance(param, AST.VarInfo):
                normalized.append(param)
                continue
            hidden = AST.Identifier(span=param.span, name=f"%arg_{index}", synthetic=True)
            normalized.append(AST.VarInfo(param.span, hidden, param.var_type))
            body.parameter_bindings.append(AST.PatternLet(
                span=param.span, pattern=param.pattern,
                var_type=param.var_type, init_expr=hidden, is_parameter=True,
            ))
        return normalized

    # ------------------------------------------------------------------
    # Control-flow lowering
    # ------------------------------------------------------------------

    def __transform_control_flow(self, expr: AST.Expr) -> AST.Expr:
        if isinstance(expr, AST.If) and expr.elif_branches:
            return self.__desugar_if_chain(expr)
        if isinstance(expr, AST.If) and isinstance(expr.condition, AST.LetCondition):
            return self.__desugar_if_let(expr)
        if isinstance(expr, AST.While):
            return self.__desugar_while(expr)
        if isinstance(expr, AST.For):
            return self.__desugar_for(expr)
        if isinstance(expr, AST.Assert):
            return self.__desugar_assert(expr)
        return expr

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
        if isinstance(stmt.condition, AST.LetCondition):
            condition = stmt.condition
            matched = AST.Match(
                span=condition.span, expr=condition.value,
                arms=[
                    AST.MatchArm(condition.pattern.span, condition.pattern, None, stmt.body, AST.MatchArmOrigin.CONDITION),
                    AST.MatchArm(stmt.span, AST.WildcardPattern(stmt.span), None,
                                 AST.Block(stmt.span, [AST.Break(stmt.span)]), AST.MatchArmOrigin.SYNTHETIC),
                ],
            )
            return AST.Loop(stmt.span, AST.Block(stmt.body.span, [matched]))
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

    def __desugar_if_let(self, stmt: AST.If) -> AST.Match:
        condition = stmt.condition
        assert isinstance(condition, AST.LetCondition)
        fallback = stmt.else_branch or AST.Block(stmt.span, [])
        return AST.Match(
            span=stmt.span, expr=condition.value,
            arms=[
                AST.MatchArm(condition.pattern.span, condition.pattern, None, stmt.then_branch, AST.MatchArmOrigin.CONDITION),
                AST.MatchArm(fallback.span, AST.WildcardPattern(fallback.span), None,
                             fallback, AST.MatchArmOrigin.SYNTHETIC),
            ],
        )

    def __desugar_for(self, stmt: AST.For) -> AST.Block:
        iter_name = AST.Identifier(span=stmt.span, name="%iter", synthetic=True)
        iter_init = AST.MethodCall(
            span=stmt.iterable.span,
            receiver=stmt.iterable,
            method_name=AST.Identifier(span=stmt.iterable.span, name="into_iter", synthetic=True),
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
            receiver=AST.Identifier(span=stmt.span, name=iter_name.name, synthetic=True),
            method_name=AST.Identifier(span=stmt.span, name="next", synthetic=True),
            generics=[],
            args=[],
        )
        some_arm = AST.MatchArm(
            span=stmt.pattern.span,
            pattern=AST.ConstructPattern(
                span=stmt.pattern.span,
                name=AST.Identifier(span=stmt.pattern.span, name="Some", synthetic=True),
                qualifier=None,
                positional=[stmt.pattern],
                named=None,
            ),
            guard=None,
            body=stmt.body,
            origin=AST.MatchArmOrigin.FOR_ITEM,
        )
        none_arm = AST.MatchArm(
            span=stmt.span,
            pattern=AST.NamePattern(
                span=stmt.span,
                name=AST.Identifier(span=stmt.span, name="None", synthetic=True),
            ),
            guard=None,
            body=AST.Block(span=stmt.span, stmts=[AST.Break(span=stmt.span)]),
            origin=AST.MatchArmOrigin.SYNTHETIC,
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
