"""Specialize compile-time conditional expressions in typed HIR."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import NoReturn

from compiler.analysis.error import AnalysisError
from compiler.analysis.ty import ty as Type
from compiler.analysis.ty.context import TypeCtx
from compiler.analysis.unit import hir as HIR
from compiler.analysis.unit.def_point import DefPoint
from compiler.analysis.unit.hir_traversal import HirRewriter
from compiler.builtins import BuiltinKind

from compiler.frontend.parse.operator import BinaryOperator, UnaryOperator


CompileTimeValue = int | bool
CompileTimeResult = tuple[CompileTimeValue, int]


class ComptimeIfSpecializer(HirRewriter):
    """Evaluate ``comptime if`` nodes and remove the unselected HIR branch."""

    def __init__(self, def_points: Mapping[int, DefPoint], type_ctx: TypeCtx,
                 raw_pointers: bool, type_size: Callable[[int], int]) -> None:
        self.__def_points = def_points
        self.__type_ctx = type_ctx
        self.__raw_pointers = raw_pointers
        self.__type_size = type_size
        self.__edges: dict[int, set[int]] = {}
        self.__current_def_type_id = -1

    def run(self, *, recover: bool = False) -> tuple[AnalysisError, ...]:
        errors: list[AnalysisError] = []
        for def_point in list(self.__def_points.values()):
            if def_point.body is None:
                continue
            self.__current_def_type_id = def_point.type_id
            try:
                def_point.body = self.rewrite_block(def_point.body)
            except AnalysisError as error:
                if not recover:
                    raise
                errors.append(error)
                def_point.body = None
        return tuple(errors)

    def generated_definitions(self, candidates: dict[int, DefPoint], entry_type_id: int | None) -> dict[int, DefPoint]:
        """Select the specialized procedures reachable from the actual entry."""
        if entry_type_id is None:
            return {}
        key_by_type: dict[int, int] = {}
        for key, def_point in candidates.items():
            key_by_type[key] = key
            key_by_type[def_point.type_id] = key

        entry_key = key_by_type.get(entry_type_id)
        if entry_key is None:
            return {}
        reachable: set[int] = set()
        pending = [entry_key]
        while pending:
            key = pending.pop()
            if key in reachable:
                continue
            reachable.add(key)
            for referenced_type in self.__edges.get(candidates[key].type_id, set()):
                referenced_key = key_by_type.get(referenced_type)
                if referenced_key is not None and referenced_key not in reachable:
                    pending.append(referenced_key)
        return {key: def_point for key, def_point in candidates.items() if key in reachable}

    def rewrite_expr(self, expr: HIR.Expr) -> HIR.Expr:
        if isinstance(expr, HIR.ComptimeIf):
            condition = self.rewrite_expr(expr.cond)
            value, _ = self.__evaluate(condition)
            if type(value) is not bool:
                self.__not_evaluable(expr.cond, "condition is not bool")
            branch = expr.then_branch if value else expr.else_branch
            return self.rewrite_block(branch)
        if isinstance(expr, HIR.CompileConfig):
            if expr.name != "IS_RAW_MODE":
                self.__not_evaluable(expr, f"unknown compile configuration '{expr.name}'")
            return HIR.BoolLiteral(
                span=expr.span,
                value=self.__raw_pointers,
                type_id=TypeCtx.bool_id,
                is_place=False,
            )

        if isinstance(expr, HIR.Call):
            self.__record_procedure(expr.func)
        elif isinstance(expr, HIR.MethodCall):
            self.__record_procedure(expr.method_id)
        elif isinstance(expr, HIR.TraitObjectCoerce):
            for method_id in expr.method_ids:
                self.__record_procedure(method_id)
        elif isinstance(expr, (HIR.Ty, HIR.Closure, HIR.Var)):
            self.__record_procedure(expr.type_id)

        return super().rewrite_expr(expr)

    def rewrite_children(self, expr: HIR.Expr) -> None:
        if isinstance(expr, HIR.Invoke):
            expr.callable = self.rewrite_expr(expr.callable)
            self.__record_procedure(expr.callable.type_id)
            expr.args = [self.rewrite_expr(arg) for arg in expr.args]
            return
        super().rewrite_children(expr)

    def __record_procedure(self, type_id: int) -> None:
        resolved = self.__type_ctx.resolve_aliases(type_id)
        ty = self.__type_ctx[resolved]
        if isinstance(ty, (Type.FunctionType, Type.MethodType, Type.ClosureType)):
            self.__edges.setdefault(self.__current_def_type_id, set()).add(type_id)

    def __evaluate(self, expr: HIR.Expr) -> CompileTimeResult:
        match expr:
            case HIR.BoolLiteral():
                return expr.value, expr.type_id
            case HIR.IntLiteral():
                return expr.value, expr.type_id
            case HIR.Ty():
                ty = self.__type_ctx[expr.type_id]
                if isinstance(ty, Type.LiteralValueType):
                    return ty.value, ty.value_type
                self.__not_evaluable(expr, "constant generic is not instantiated")
            case HIR.CompileConfig():
                if expr.name == "IS_RAW_MODE":
                    return self.__raw_pointers, TypeCtx.bool_id
                self.__not_evaluable(expr, f"unknown compile configuration '{expr.name}'")
            case HIR.Builtin(kind=BuiltinKind.SizeOf):
                return self.__type_size(expr.type_args[0]), expr.type_id
            case HIR.Builtin(kind=BuiltinKind.Undef | BuiltinKind.Dangling):
                self.__not_evaluable(expr, f"'{expr.kind.spelling}' is not a compile-time value")
            case HIR.Cast():
                value, _ = self.__evaluate(expr.value)
                return self.__cast_value(value, expr.target_type, expr)
            case HIR.Unary():
                value, _ = self.__evaluate(expr.operand)
                return self.__evaluate_unary(expr.op, value, expr)
            case HIR.Binary():
                return self.__evaluate_binary(expr)
            case _:
                self.__not_evaluable(expr, "condition depends on runtime state or has side effects")

    def __evaluate_unary(self, op: UnaryOperator, value: CompileTimeValue, expr: HIR.Unary) -> CompileTimeResult:
        if op == UnaryOperator.LogicalNot:
            if type(value) is not bool:
                self.__not_evaluable(expr, "logical not requires bool")
            return not value, TypeCtx.bool_id
        if op == UnaryOperator.Neg:
            if type(value) is not int:
                self.__not_evaluable(expr, "arithmetic negation requires an integer")
            return self.__normalize_integer(-value, expr.type_id), expr.type_id
        if op == UnaryOperator.BitNot:
            if type(value) is bool:
                return not value, expr.type_id
            if type(value) is not int:
                self.__not_evaluable(expr, "bitwise not requires an integer or bool")
            return self.__normalize_integer(~value, expr.type_id), expr.type_id
        self.__not_evaluable(expr, f"operator '{op}' is not compile-time evaluable")

    def __evaluate_binary(self, expr: HIR.Binary) -> CompileTimeResult:
        left, _ = self.__evaluate(expr.left)
        if expr.op == BinaryOperator.LogicalAnd:
            if type(left) is not bool:
                self.__not_evaluable(expr.left, "logical and requires bool")
            if not left:
                return False, TypeCtx.bool_id
        elif expr.op == BinaryOperator.LogicalOr:
            if type(left) is not bool:
                self.__not_evaluable(expr.left, "logical or requires bool")
            if left:
                return True, TypeCtx.bool_id

        right, _ = self.__evaluate(expr.right)
        if expr.op in (BinaryOperator.LogicalAnd, BinaryOperator.LogicalOr):
            if type(right) is not bool:
                self.__not_evaluable(expr.right, "logical operators require bool")
            return right, TypeCtx.bool_id

        if expr.op in (BinaryOperator.Eq, BinaryOperator.Neq):
            if type(left) is bool and type(right) is bool:
                result = left == right
            elif type(left) is int and type(right) is int:
                result = left == right
            else:
                self.__not_evaluable(expr, "comparison operands are not compile-time values")
            return (result if expr.op == BinaryOperator.Eq else not result), TypeCtx.bool_id

        if type(left) is not int or type(right) is not int:
            self.__not_evaluable(expr, "operator requires integer operands")

        if expr.op in (BinaryOperator.Lt, BinaryOperator.Gt, BinaryOperator.Leq, BinaryOperator.Geq):
            result = {
                BinaryOperator.Lt: left < right,
                BinaryOperator.Gt: left > right,
                BinaryOperator.Leq: left <= right,
                BinaryOperator.Geq: left >= right,
            }[expr.op]
            return result, TypeCtx.bool_id

        if expr.op == BinaryOperator.Add:
            result = left + right
        elif expr.op == BinaryOperator.Sub:
            result = left - right
        elif expr.op == BinaryOperator.Mul:
            result = left * right
        elif expr.op == BinaryOperator.Div:
            if right == 0:
                raise AnalysisError("comptime if condition evaluation failed: division by zero", expr.span)
            result = self.__truncate_division(left, right)
        elif expr.op == BinaryOperator.Mod:
            if right == 0:
                raise AnalysisError("comptime if condition evaluation failed: division by zero", expr.span)
            result = left - self.__truncate_division(left, right) * right
        elif expr.op == BinaryOperator.BitAnd:
            result = left & right
        elif expr.op == BinaryOperator.BitOr:
            result = left | right
        elif expr.op == BinaryOperator.BitXor:
            result = left ^ right
        elif expr.op == BinaryOperator.Shl:
            if right < 0:
                raise AnalysisError("comptime if condition evaluation failed: negative shift", expr.span)
            result = left << right
        elif expr.op == BinaryOperator.Shr:
            if right < 0:
                raise AnalysisError("comptime if condition evaluation failed: negative shift", expr.span)
            result = left >> right
        else:
            self.__not_evaluable(expr, f"operator '{expr.op}' is not compile-time evaluable")
        return self.__normalize_integer(result, expr.type_id), expr.type_id

    def __cast_value(self, value: CompileTimeValue, target_type: int, expr: HIR.Cast) -> CompileTimeResult:
        target = self.__type_ctx[target_type]
        if isinstance(target, Type.IntType):
            if type(value) is not int:
                self.__not_evaluable(expr, "integer cast requires an integer")
            return self.__normalize_integer(value, target_type), target_type
        if isinstance(target, Type.BoolType):
            if type(value) is not bool:
                self.__not_evaluable(expr, "bool cast requires a bool")
            return value, target_type
        self.__not_evaluable(expr, "cast is not compile-time evaluable")

    def __normalize_integer(self, value: int, type_id: int) -> int:
        ty = self.__type_ctx[type_id]
        if isinstance(ty, Type.LiteralValueType):
            return self.__normalize_integer(value, ty.value_type)
        if not isinstance(ty, Type.IntType):
            return value
        bits = ty.size * 8
        modulus = 1 << bits
        normalized = value % modulus
        if ty.signed and normalized >= (1 << (bits - 1)):
            normalized -= modulus
        return normalized

    @staticmethod
    def __truncate_division(left: int, right: int) -> int:
        quotient = abs(left) // abs(right)
        return -quotient if (left < 0) != (right < 0) else quotient

    @staticmethod
    def __not_evaluable(expr: HIR.Expr, detail: str) -> NoReturn:
        raise AnalysisError(f"comptime if condition is not compile-time evaluable: {detail}", expr.span)
