"""Evaluation of typed, side-effect-free compile-time expressions."""

from __future__ import annotations

import math
import struct
from collections.abc import Callable
from typing import NoReturn, TypeAlias

from compiler.analysis.error import AnalysisError
from compiler.analysis.ty import ty as Type
from compiler.analysis.ty.context import TypeCtx
from compiler.analysis.unit import hir as HIR
from compiler.builtins import BuiltinKind
from compiler.frontend.parse.operator import BinaryOperator, UnaryOperator


ConstantValue: TypeAlias = int | bool | float | str


class ConstantExpressionEvaluator:
    """Evaluate typed HIR using the language's scalar operation semantics."""

    def __init__(
        self,
        type_ctx: TypeCtx,
        raw_pointers: bool,
        type_size: Callable[[int], int],
        error_context: str,
    ) -> None:
        self.__type_ctx = type_ctx
        self.__raw_pointers = raw_pointers
        self.__type_size = type_size
        self.__error_context = error_context

    def evaluate(self, expr: HIR.Expr) -> tuple[ConstantValue, int]:
        match expr:
            case HIR.BoolLiteral():
                return expr.value, expr.type_id
            case HIR.IntLiteral():
                return expr.value, expr.type_id
            case HIR.FloatLiteral():
                return self.__round_float(expr.value, expr.type_id), expr.type_id
            case HIR.CharLiteral() | HIR.StrLiteral():
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
            case HIR.Builtin():
                self.__not_evaluable(expr, f"'{expr.kind.spelling}' is not a compile-time value")
            case HIR.Cast():
                value, _ = self.evaluate(expr.value)
                return self.__cast_value(value, expr.target_type, expr)
            case HIR.Unary():
                value, _ = self.evaluate(expr.operand)
                return self.__evaluate_unary(expr.op, value, expr)
            case HIR.Binary():
                return self.__evaluate_binary(expr)
            case HIR.ComptimeIf():
                condition, _ = self.evaluate(expr.cond)
                if type(condition) is not bool:
                    self.__not_evaluable(expr.cond, "condition is not bool")
                branch = expr.then_branch if condition else expr.else_branch
                return self.__evaluate_block(branch)
            case HIR.Block():
                return self.__evaluate_block(expr)
            case _:
                self.__not_evaluable(expr, "condition depends on runtime state or has side effects")

    def __evaluate_block(self, block: HIR.Block) -> tuple[ConstantValue, int]:
        if len(block.stmts) != 1:
            self.__not_evaluable(block, "compile-time blocks must contain exactly one value expression")
        return self.evaluate(block.stmts[0])

    def __evaluate_unary(
        self, op: UnaryOperator, value: ConstantValue, expr: HIR.Unary,
    ) -> tuple[ConstantValue, int]:
        if op == UnaryOperator.LogicalNot:
            if type(value) is not bool:
                self.__not_evaluable(expr, "logical not requires bool")
            return not value, TypeCtx.bool_id
        if op == UnaryOperator.Neg:
            if type(value) is int:
                return self.__normalize_integer(-value, expr.type_id), expr.type_id
            if type(value) is float:
                return self.__round_float(-value, expr.type_id), expr.type_id
            self.__not_evaluable(expr, "arithmetic negation requires a number")
        if op == UnaryOperator.BitNot:
            if type(value) is bool:
                return not value, expr.type_id
            if type(value) is int:
                return self.__normalize_integer(~value, expr.type_id), expr.type_id
            self.__not_evaluable(expr, "bitwise not requires an integer or bool")
        self.__not_evaluable(expr, f"operator '{op}' is not compile-time evaluable")

    def __evaluate_binary(self, expr: HIR.Binary) -> tuple[ConstantValue, int]:
        left, left_type = self.evaluate(expr.left)
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

        right, right_type = self.evaluate(expr.right)
        if expr.op in (BinaryOperator.LogicalAnd, BinaryOperator.LogicalOr):
            if type(right) is not bool:
                self.__not_evaluable(expr.right, "logical operators require bool")
            return right, TypeCtx.bool_id

        if expr.op in (BinaryOperator.Eq, BinaryOperator.Neq):
            left_ty = self.__type_ctx[self.__type_ctx.resolve_aliases(left_type)]
            right_ty = self.__type_ctx[self.__type_ctx.resolve_aliases(right_type)]
            valid = (
                isinstance(left_ty, Type.BoolType) and isinstance(right_ty, Type.BoolType)
                or isinstance(left_ty, Type.IntType) and isinstance(right_ty, Type.IntType)
                or isinstance(left_ty, Type.FloatType) and isinstance(right_ty, Type.FloatType)
                or isinstance(left_ty, Type.CharType) and isinstance(right_ty, Type.CharType)
            )
            if not valid or type(left) is not type(right):
                self.__not_evaluable(expr, "comparison operands are not compile-time values")
            result = left == right
            return (result if expr.op == BinaryOperator.Eq else not result), TypeCtx.bool_id

        if expr.op in (BinaryOperator.Lt, BinaryOperator.Gt, BinaryOperator.Leq, BinaryOperator.Geq):
            if type(left) is int and type(right) is int:
                left_number = left
                right_number = right
            elif type(left) is float and type(right) is float:
                left_number = left
                right_number = right
            else:
                self.__not_evaluable(expr, "ordered comparison requires matching numeric operands")
            result = {
                BinaryOperator.Lt: left_number < right_number,
                BinaryOperator.Gt: left_number > right_number,
                BinaryOperator.Leq: left_number <= right_number,
                BinaryOperator.Geq: left_number >= right_number,
            }[expr.op]
            return result, TypeCtx.bool_id

        if type(left) is bool and type(right) is bool:
            bool_ops: dict[BinaryOperator, bool] = {
                BinaryOperator.BitAnd: left and right,
                BinaryOperator.BitOr: left or right,
                BinaryOperator.BitXor: left != right,
            }
            result = bool_ops.get(expr.op)
            if result is not None:
                return result, expr.type_id
            self.__not_evaluable(expr, f"operator '{expr.op}' is not valid for bool")

        if type(left) is float and type(right) is float:
            return self.__evaluate_float_binary(expr, left, right)

        if type(left) is not int or type(right) is not int:
            self.__not_evaluable(expr, "operator requires matching numeric operands")

        if expr.op in (BinaryOperator.Shl, BinaryOperator.Shr) and right < 0:
            self.__evaluation_failed(expr, "negative shift")
        if expr.op in (BinaryOperator.Div, BinaryOperator.Mod) and right == 0:
            self.__evaluation_failed(expr, "division by zero")
        if expr.op == BinaryOperator.Add:
            result = left + right
        elif expr.op == BinaryOperator.Sub:
            result = left - right
        elif expr.op == BinaryOperator.Mul:
            result = left * right
        elif expr.op == BinaryOperator.Div:
            result = self.__truncate_division(left, right)
        elif expr.op == BinaryOperator.Mod:
            result = left - self.__truncate_division(left, right) * right
        elif expr.op == BinaryOperator.BitAnd:
            result = left & right
        elif expr.op == BinaryOperator.BitOr:
            result = left | right
        elif expr.op == BinaryOperator.BitXor:
            result = left ^ right
        elif expr.op == BinaryOperator.Shl:
            result = left << right
        elif expr.op == BinaryOperator.Shr:
            result = left >> right
        else:
            self.__not_evaluable(expr, f"operator '{expr.op}' is not compile-time evaluable")
        return self.__normalize_integer(result, expr.type_id), expr.type_id

    def __evaluate_float_binary(
        self, expr: HIR.Binary, left: float, right: float,
    ) -> tuple[ConstantValue, int]:
        if expr.op == BinaryOperator.Add:
            result = left + right
        elif expr.op == BinaryOperator.Sub:
            result = left - right
        elif expr.op == BinaryOperator.Mul:
            result = left * right
        elif expr.op == BinaryOperator.Div:
            result = self.__float_division(left, right)
        elif expr.op == BinaryOperator.Mod:
            if right == 0.0 or math.isinf(left):
                result = math.nan
            elif math.isinf(right):
                result = left
            else:
                result = math.fmod(left, right)
        else:
            self.__not_evaluable(expr, f"operator '{expr.op}' is not compile-time evaluable")
        return self.__round_float(result, expr.type_id), expr.type_id

    def __cast_value(
        self, value: ConstantValue, target_type: int, expr: HIR.Cast,
    ) -> tuple[ConstantValue, int]:
        target = self.__type_ctx[self.__type_ctx.resolve_aliases(target_type)]
        source = self.__type_ctx[self.__type_ctx.resolve_aliases(expr.value.type_id)]
        if isinstance(target, Type.IntType):
            if type(value) is int:
                result = value
            elif type(value) is float:
                try:
                    result = int(value)
                except (OverflowError, ValueError):
                    self.__not_evaluable(expr, "non-finite float cannot be cast to an integer")
            elif isinstance(source, Type.CharType) and type(value) is str:
                result = ord(value)
            elif isinstance(source, Type.BoolType) and type(value) is bool:
                result = int(value)
            else:
                self.__not_evaluable(expr, "integer cast requires a numeric or char value")
            return self.__normalize_integer(result, target_type), target_type
        if isinstance(target, Type.FloatType):
            if type(value) is int:
                try:
                    float_value = float(value)
                except OverflowError:
                    float_value = math.copysign(math.inf, value)
            elif type(value) is float:
                float_value = value
            else:
                self.__not_evaluable(expr, "float cast requires a numeric value")
            return self.__round_float(float_value, target_type), target_type
        if isinstance(target, Type.BoolType):
            if type(value) is not bool:
                self.__not_evaluable(expr, "bool cast requires a bool")
            return value, target_type
        if isinstance(target, Type.CharType):
            if type(value) is int:
                char_value = value % (1 << 32)
                try:
                    return chr(char_value), target_type
                except ValueError:
                    pass
            if isinstance(source, Type.CharType) and type(value) is str and len(value) == 1:
                return value, target_type
            if isinstance(source, Type.BoolType) and type(value) is bool:
                return chr(int(value)), target_type
            self.__not_evaluable(expr, "char cast requires a valid Unicode scalar value")
        self.__not_evaluable(expr, "cast is not compile-time evaluable")

    def __normalize_integer(self, value: int, type_id: int) -> int:
        ty = self.__type_ctx[self.__type_ctx.resolve_aliases(type_id)]
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

    def __round_float(self, value: float, type_id: int) -> float:
        ty = self.__type_ctx[self.__type_ctx.resolve_aliases(type_id)]
        if isinstance(ty, Type.FloatLiteralType):
            return value
        if not isinstance(ty, Type.FloatType):
            return value
        format_code = {2: "e", 4: "f", 8: "d"}[ty.size]
        try:
            return struct.unpack(f"={format_code}", struct.pack(f"={format_code}", value))[0]
        except OverflowError:
            return math.copysign(math.inf, value)

    @staticmethod
    def __float_division(left: float, right: float) -> float:
        if right != 0.0:
            return left / right
        if left == 0.0 or math.isnan(left):
            return math.nan
        return math.copysign(math.inf, left * math.copysign(1.0, right))

    @staticmethod
    def __truncate_division(left: int, right: int) -> int:
        quotient = abs(left) // abs(right)
        return -quotient if (left < 0) != (right < 0) else quotient

    def __evaluation_failed(self, expr: HIR.Expr, detail: str) -> NoReturn:
        raise AnalysisError(f"{self.__error_context} evaluation failed: {detail}", expr.span)

    def __not_evaluable(self, expr: HIR.Expr, detail: str) -> NoReturn:
        raise AnalysisError(
            f"{self.__error_context} is not compile-time evaluable: {detail}", expr.span
        )
