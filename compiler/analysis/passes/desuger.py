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


class Desugar:
    def __init__(self, program: AST.Program):
        self.__program = program

        self.__processors: list[Callable[[AST.Block], None]] = [
            self.__process_for_loops,
            self.__process_while_loops,
            self.__process_asserts,
            self.__process_if_chains,
            self.__process_range_exprs,
            self.__process_member_tests,
        ]

    def run(self) -> None:
        for processor in self.__processors:
            for item in self.__program.items:
                match item:
                    case AST.FuncDef():
                        processor(item.body)
                    case AST.Impl():
                        for method in item.items:
                            processor(method.body)
                    case AST.TraitDef():
                        for trait_item in item.items:
                            if isinstance(trait_item, AST.MethodDef):
                                processor(trait_item.body)
                    case _:
                        continue

    def __process_nested_blocks(self, stmt: AST.Stmt, processor: Callable[[AST.Block], None]) -> None:
        match stmt:
            case AST.Block():
                processor(stmt)
            case AST.If():
                processor(stmt.then_branch)
                for _, elif_branch in stmt.elif_branches:
                    processor(elif_branch)
                if stmt.else_branch is not None:
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
            case _:
                return

    def __process_for_loops(self, block: AST.Block) -> None:
        desugared_stmts: list[AST.Stmt] = []

        for stmt in block.stmts:
            self.__process_nested_blocks(stmt, self.__process_for_loops)

            if isinstance(stmt, AST.For):
                desugared_stmts.append(self.__desugar_for(stmt))
            else:
                desugared_stmts.append(stmt)

        block.stmts = desugared_stmts

    def __process_while_loops(self, block: AST.Block) -> None:
        desugared_stmts: list[AST.Stmt] = []

        for stmt in block.stmts:
            self.__process_nested_blocks(stmt, self.__process_while_loops)

            if isinstance(stmt, AST.While):
                desugared_stmts.append(self.__desugar_while(stmt))
            else:
                desugared_stmts.append(stmt)

        block.stmts = desugared_stmts

    def __process_asserts(self, block: AST.Block) -> None:
        desugared_stmts: list[AST.Stmt] = []

        for stmt in block.stmts:
            self.__process_nested_blocks(stmt, self.__process_asserts)

            if isinstance(stmt, AST.Assert):
                desugared_stmts.append(self.__desugar_assert(stmt))
            else:
                desugared_stmts.append(stmt)

        block.stmts = desugared_stmts

    def __process_if_chains(self, block: AST.Block) -> None:
        desugared_stmts: list[AST.Stmt] = []

        for stmt in block.stmts:
            self.__process_nested_blocks(stmt, self.__process_if_chains)

            if isinstance(stmt, AST.If) and stmt.elif_branches:
                desugared_stmts.append(self.__desugar_if_chain(stmt))
            else:
                desugared_stmts.append(stmt)

        block.stmts = desugared_stmts

    def __process_range_exprs(self, block: AST.Block) -> None:
        desugared_stmts: list[AST.Stmt] = []

        for stmt in block.stmts:
            self.__process_nested_blocks(stmt, self.__process_range_exprs)

            if isinstance(stmt, AST.Binary) and stmt.op == BinaryOperator.Range:
                desugared_stmts.append(self.__desugar_range(stmt))
            else:
                desugared_stmts.append(stmt)

        block.stmts = desugared_stmts

    def __process_member_tests(self, block: AST.Block) -> None:
        desugared_stmts: list[AST.Stmt] = []

        for stmt in block.stmts:
            self.__process_nested_blocks(stmt, self.__process_member_tests)

            if isinstance(stmt, AST.Binary) and stmt.op in [BinaryOperator.In, BinaryOperator.NotIn]:
                desugared_stmts.append(self.__desugar_member_test(stmt))
            else:
                desugared_stmts.append(stmt)

        block.stmts = desugared_stmts

    def __desugar_assert(self, stmt: AST.Assert) -> AST.If:
        message = stmt.message
        if message is None:
            message_value = f"assertion failed at {stmt.span}"
            message = AST.Literal(
                span=stmt.span,
                literal=Tok.StrLiteral(raw=f"\"{message_value}\"", span=stmt.span, value=message_value),
            )

        panic_call = AST.Call(
            span=stmt.span,
            callee=AST.Identifier(span=stmt.span, name="panic"),
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

    def __desugar_range(self, stmt: AST.Binary) -> AST.Call:
        return AST.Call(
            span=stmt.span,
            callee=AST.Identifier(span=stmt.span, name="Range"),
            args=[
                AST.Arg(span=stmt.left.span, name=None, value=stmt.left),
                AST.Arg(span=stmt.right.span, name=None, value=stmt.right),
            ],
        )

    def __desugar_member_test(self, stmt: AST.Binary) -> AST.MethodCall:
        method_name = "contains" if stmt.op == BinaryOperator.In else "not_contains"
        return AST.MethodCall(
            span=stmt.span,
            receiver=stmt.right,
            method_name=AST.Identifier(span=stmt.span, name=method_name),
            generics=[],
            args=[AST.Arg(span=stmt.left.span, name=None, value=stmt.left)],
        )
