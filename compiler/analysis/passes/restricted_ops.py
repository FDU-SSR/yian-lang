"""Reject compiler primitives that are reserved for the trusted stdlib.

The regular language surface must not be able to forge pointer metadata,
bypass definite-assignment analysis, or cross the raw system-call boundary.
This pass runs on the source AST before type checking, so the restriction is
independent of overload resolution and generic lowering.
"""

from __future__ import annotations

from collections.abc import Iterable

from compiler.analysis.error import AnalysisError
from compiler.frontend.parse import ast as AST
from compiler.frontend.parse.ast_traversal import AstVisitor
from compiler.analysis.unit.unit_data import UnitData


RESTRICTED_BUILTINS = frozenset(
    {
        AST.BuiltinKind.SysRead,
        AST.BuiltinKind.SysWrite,
        AST.BuiltinKind.SysWriteBytes,
        AST.BuiltinKind.Open,
        AST.BuiltinKind.Close,
        AST.BuiltinKind.Sqrt,
        AST.BuiltinKind.Sin,
        AST.BuiltinKind.Cos,
        AST.BuiltinKind.BitCast,
        AST.BuiltinKind.Alloc,
        AST.BuiltinKind.Realloc,
        AST.BuiltinKind.AssumeInit,
        AST.BuiltinKind.MemCopy,
        AST.BuiltinKind.Undef,
        AST.BuiltinKind.Dangling,
        AST.BuiltinKind.SliceFromParts,
        AST.BuiltinKind.SliceGetPtr,
        AST.BuiltinKind.SliceGetLen,
        AST.BuiltinKind.StrFromParts,
        AST.BuiltinKind.StrGetPtr,
        AST.BuiltinKind.StrGetLen,
        AST.BuiltinKind.RuntimeFail,
        AST.BuiltinKind.Argc,
        AST.BuiltinKind.ArgBytes,
        AST.BuiltinKind.Exit,
    }
)

class RestrictedOpsChecker(AstVisitor):
    def enter_expr(self, expr: AST.Expr) -> bool:
        if isinstance(expr, AST.Builtin) and expr.kind in RESTRICTED_BUILTINS:
            raise AnalysisError(
                f"restricted operation '{expr.kind.spelling}' is only allowed in the standard library",
                expr.span,
            )
        return True


def check_restricted_ops(units: Iterable[UnitData]) -> None:
    """Reject restricted builtins outside trusted source roots."""
    for unit in units:
        if not unit.allows_restricted_ops:
            RestrictedOpsChecker().visit_program(unit.program)
