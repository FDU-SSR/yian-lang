"""
Desugar ASTs by desugaring syntactic sugar into more fundamental constructs.

Rules:

- var declarations with initializers => var declarations + assignments
- for => loop
- while => loop
- assert => if + panic
"""


from __future__ import annotations

from typing import Callable

from compiler.frontend.parse import ast as AST
from compiler.frontend.parse.operator import BinaryOperator


class Desugar:
    def __init__(self, program: AST.Program):
        self.__program = program

        self.__processors: list[Callable[[AST.Block], None]] = [
            self.__process_var_decls,
            self.__process_for_loops,
            self.__process_while_loops,
            self.__process_asserts,
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

    def __process_var_decls(self, block: AST.Block) -> None:
        desugared_stmts: list[AST.Stmt] = []

        for stmt in block.stmts:
            self.__process_var_decls_in_stmt(stmt)

            if isinstance(stmt, AST.VarDecl) and stmt.init_expr is not None:
                init_expr = stmt.init_expr
                stmt.init_expr = None
                desugared_stmts.append(stmt)
                desugared_stmts.append(
                    AST.Binary(
                        span=stmt.name.span + init_expr.span,
                        op=BinaryOperator.Assign,
                        left=AST.Identifier(span=stmt.name.span, name=stmt.name.name),
                        right=init_expr,
                    )
                )
            else:
                desugared_stmts.append(stmt)

        block.stmts = desugared_stmts

    def __process_var_decls_in_stmt(self, stmt: AST.Stmt) -> None:
        match stmt:
            case AST.Block():
                self.__process_var_decls(stmt)
            case AST.If():
                self.__process_var_decls(stmt.then_branch)
                for _, elif_branch in stmt.elif_branches:
                    self.__process_var_decls(elif_branch)
                if stmt.else_branch is not None:
                    self.__process_var_decls(stmt.else_branch)
            case AST.For():
                self.__process_var_decls(stmt.body)
            case AST.While():
                self.__process_var_decls(stmt.body)
            case AST.Loop():
                self.__process_var_decls(stmt.body)
            case AST.Match():
                for _, arm_block in stmt.arms:
                    self.__process_var_decls(arm_block)
            case _:
                return

    def __process_for_loops(self, block: AST.Block) -> None:
        raise NotImplementedError()

    def __process_while_loops(self, block: AST.Block) -> None:
        raise NotImplementedError()

    def __process_asserts(self, block: AST.Block) -> None:
        raise NotImplementedError()
