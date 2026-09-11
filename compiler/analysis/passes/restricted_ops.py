"""Reject compiler primitives that are reserved for the trusted stdlib.

The regular language surface must not be able to forge pointer metadata,
bypass definite-assignment analysis, or cross the raw system-call boundary.
This pass runs on the source AST before type checking, so the restriction is
independent of overload resolution and generic lowering.
"""
from __future__ import annotations

from pathlib import Path

from compiler.analysis.error import AnalysisError
from compiler.frontend.lex.position import SrcSpan
from compiler.frontend.parse import ast as AST

RESTRICTED_BUILTIN_NAMES = frozenset(
    {
        "sys_read",
        "sys_write",
        "open",
        "close",
        "assume_init",
        "__memcpy",
    }
)

# Restricted stdlib function names: unchecked size-field construction.
# The fat-pointer primitives forge slice/str values from raw {ptr, len}
# parts and are therefore confined to the standard library, exactly like
# `from_raw_parts`.
RESTRICTED_STDLIB_FUNCS = frozenset(
    {
        "from_raw_parts",
        "__slice_from_parts",
        "__slice_get_ptr",
        "__slice_get_len",
        "__str_from_parts",
        "__str_get_ptr",
        "__str_get_len",
    }
)

RESTRICTED_CALL_NAMES = RESTRICTED_BUILTIN_NAMES | RESTRICTED_STDLIB_FUNCS


def __is_stdlib_file(path: Path) -> bool:
    return "lib" in path.resolve().parts


def __is_test_harness_file(path: Path) -> bool:
    parts = path.resolve().parts
    return any(part == "tests" and parts[i + 1] == "std" for i, part in enumerate(parts[:-1]))


class RestrictedOpsChecker:
    def __init__(self) -> None:
        self.__error: AnalysisError | None = None

    def check(self, program: AST.Program) -> None:
        for item in program.items:
            match item:
                case AST.FuncDef():
                    self.__scan_block(item.body)
                case AST.Impl():
                    for method in item.items:
                        self.__scan_block(method.body)
                case _:
                    pass

    def __report(self, span: SrcSpan, name: str) -> None:
        if self.__error is None:
            self.__error = AnalysisError(
                f"restricted operation '{name}' is only allowed in the standard library",
                span,
            )

    def __scan_block(self, block: AST.Block) -> None:
        if self.__error is not None:
            return
        for stmt in block.stmts:
            self.__scan_expr(stmt)
            if self.__error is not None:
                return

    def __scan_expr(self, expr: AST.Expr) -> None:
        if self.__error is not None:
            return
        match expr:
            case AST.BitCast():
                self.__report(expr.span, "bitcast")
            case AST.Call():
                if isinstance(expr.callee, AST.Identifier) and expr.callee.name in RESTRICTED_CALL_NAMES:
                    self.__report(expr.span, expr.callee.name)
                self.__scan_expr(expr.callee)
                for arg in expr.args:
                    self.__scan_expr(arg.value)
            case AST.MethodCall():
                self.__scan_expr(expr.receiver)
                for arg in expr.args:
                    self.__scan_expr(arg.value)
            case AST.Binary():
                self.__scan_expr(expr.left)
                self.__scan_expr(expr.right)
            case AST.Unary():
                self.__scan_expr(expr.operand)
            case AST.FieldAccess():
                self.__scan_expr(expr.receiver)
            case AST.DynValue():
                self.__scan_expr(expr.value)
            case AST.DynBuffer():
                self.__scan_expr(expr.size)
            case AST.Tuple():
                for element in expr.elements:
                    self.__scan_expr(element)
            case AST.Array():
                for element in expr.elements:
                    self.__scan_expr(element)
            case AST.ArrayRepeat():
                self.__scan_expr(expr.element)
                self.__scan_expr(expr.count)
            case AST.Block():
                self.__scan_block(expr)
            case AST.VarDecl():
                if expr.init_expr is not None:
                    self.__scan_expr(expr.init_expr)
            case AST.If():
                self.__scan_expr(expr.condition)
                self.__scan_block(expr.then_branch)
                for cond, branch in expr.elif_branches:
                    self.__scan_expr(cond)
                    self.__scan_block(branch)
                if expr.else_branch is not None:
                    self.__scan_block(expr.else_branch)
            case AST.ComptimeIf():
                self.__scan_expr(expr.condition)
                self.__scan_block(expr.then_branch)
                self.__scan_block(expr.else_branch)
            case AST.For():
                self.__scan_expr(expr.iterable)
                self.__scan_block(expr.body)
            case AST.While():
                self.__scan_expr(expr.condition)
                self.__scan_block(expr.body)
            case AST.Loop():
                self.__scan_block(expr.body)
            case AST.Match():
                self.__scan_expr(expr.expr)
                for _, arm_block in expr.arms:
                    self.__scan_block(arm_block)
            case AST.Return():
                if expr.expr is not None:
                    self.__scan_expr(expr.expr)
            case AST.Break():
                if expr.expr is not None:
                    self.__scan_expr(expr.expr)
            case AST.Assert():
                self.__scan_expr(expr.condition)
                if expr.message is not None:
                    self.__scan_expr(expr.message)
            case AST.Delete():
                self.__scan_expr(expr.target)
            case AST.Semi():
                self.__scan_expr(expr.expr)
            case AST.ClosureExpr():
                self.__scan_block(expr.body)
            case _:
                pass

    def take_error(self) -> AnalysisError | None:
        return self.__error


def check_restricted_ops(programs: list[AST.Program], src_files: list[Path]) -> None:
    """Reject restricted constructs outside the stdlib and test harness."""
    for src_file, program in zip(src_files, programs):
        if __is_stdlib_file(src_file) or __is_test_harness_file(src_file):
            continue
        checker = RestrictedOpsChecker()
        checker.check(program)
        error = checker.take_error()
        if error is not None:
            raise error
