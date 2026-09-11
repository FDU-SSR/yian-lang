"""Specialize compile-time conditional expressions in typed HIR."""

from __future__ import annotations

from collections.abc import Callable
from typing import NoReturn

from compiler.analysis.error import AnalysisError
from compiler.analysis.ty import ty as Type
from compiler.analysis.ty.context import TypeCtx
from compiler.analysis.unit import hir as HIR
from compiler.analysis.unit.def_point import DefPoint
from compiler.frontend.parse.operator import BinaryOperator, UnaryOperator


CompileTimeValue = int | bool
CompileTimeResult = tuple[CompileTimeValue, int]


class ComptimeIfSpecializer:
    """Evaluate ``comptime if`` nodes and remove the unselected HIR branch."""

    def __init__(
        self,
        def_points: dict[int, DefPoint],
        type_ctx: TypeCtx,
        is_raw_mode: bool,
        type_size: Callable[[int], int],
    ) -> None:
        self.__def_points = def_points
        self.__type_ctx = type_ctx
        self.__is_raw_mode = is_raw_mode
        self.__type_size = type_size
        self.__edges: dict[int, set[int]] = {}
        self.__current_def_type_id = -1

    def run(self) -> None:
        for def_point in list(self.__def_points.values()):
            if def_point.body is None:
                continue
            self.__current_def_type_id = def_point.type_id
            def_point.body = self.__rewrite_block(def_point.body)

        self.__prune_unreachable_definitions()

    def __rewrite_block(self, block: HIR.Block) -> HIR.Block:
        block.stmts = [self.__rewrite_expr(stmt) for stmt in block.stmts]
        return block

    def __rewrite_expr(self, expr: HIR.Expr) -> HIR.Expr:
        match expr:
            case HIR.ComptimeIf():
                condition = self.__rewrite_expr(expr.cond)
                value, _ = self.__evaluate(condition)
                if type(value) is not bool:
                    self.__not_evaluable(expr.cond, "condition is not bool")
                branch = expr.then_branch if value else expr.else_branch
                return self.__rewrite_block(branch)
            case HIR.CompileConfig():
                if expr.name != "IS_RAW_MODE":
                    self.__not_evaluable(expr, f"unknown compile configuration '{expr.name}'")
                return HIR.BoolLiteral(
                    span=expr.span,
                    value=self.__is_raw_mode,
                    type_id=TypeCtx.bool_id,
                    is_place=False,
                )
            case HIR.Block():
                return self.__rewrite_block(expr)
            case HIR.Return(value=value) if value is not None:
                expr.value = self.__rewrite_expr(value)
            case HIR.Break(value=value) if value is not None:
                expr.value = self.__rewrite_expr(value)
            case HIR.If():
                expr.cond = self.__rewrite_expr(expr.cond)
                expr.then_branch = self.__rewrite_block(expr.then_branch)
                if expr.else_branch is not None:
                    expr.else_branch = self.__rewrite_block(expr.else_branch)
            case HIR.Loop():
                expr.body = self.__rewrite_block(expr.body)
            case HIR.Match():
                expr.value = self.__rewrite_expr(expr.value)
                for arm in expr.arms:
                    arm.body = self.__rewrite_block(arm.body)
            case HIR.Panic():
                expr.message = self.__rewrite_expr(expr.message)
            case HIR.Delete():
                expr.target = self.__rewrite_expr(expr.target)
            case HIR.Semi():
                expr.expr = self.__rewrite_expr(expr.expr)
            case HIR.Let() if expr.init is not None:
                expr.init = self.__rewrite_expr(expr.init)
            case HIR.Binary():
                expr.left = self.__rewrite_expr(expr.left)
                expr.right = self.__rewrite_expr(expr.right)
            case HIR.Unary():
                expr.operand = self.__rewrite_expr(expr.operand)
            case HIR.Call():
                self.__record_procedure(expr.func)
                expr.args = [self.__rewrite_expr(arg) for arg in expr.args]
            case HIR.StructConstruct():
                expr.field_values = {name: self.__rewrite_expr(value) for name, value in expr.field_values.items()}
            case HIR.Invoke():
                expr.callable = self.__rewrite_expr(expr.callable)
                self.__record_procedure(expr.callable.type_id)
                expr.args = [self.__rewrite_expr(arg) for arg in expr.args]
            case HIR.Cast():
                expr.value = self.__rewrite_expr(expr.value)
            case HIR.MethodCall():
                self.__record_procedure(expr.method_id)
                expr.receiver = self.__rewrite_expr(expr.receiver)
                expr.args = [self.__rewrite_expr(arg) for arg in expr.args]
            case HIR.VariantConstruct(args=args) if args is not None:
                expr.args = {name: self.__rewrite_expr(value) for name, value in args.items()}
            case HIR.FieldAccess():
                expr.receiver = self.__rewrite_expr(expr.receiver)
            case HIR.TupleAccess():
                expr.receiver = self.__rewrite_expr(expr.receiver)
            case HIR.ArrayAccess():
                expr.array = self.__rewrite_expr(expr.array)
                expr.index = self.__rewrite_expr(expr.index)
            case HIR.SliceAccess():
                expr.slice = self.__rewrite_expr(expr.slice)
                expr.index = self.__rewrite_expr(expr.index)
            case HIR.DynValue():
                expr.value = self.__rewrite_expr(expr.value)
            case HIR.DynBuffer():
                expr.length = self.__rewrite_expr(expr.length)
            case HIR.BitCast():
                expr.value = self.__rewrite_expr(expr.value)
            case HIR.SysWrite():
                expr.fd = self.__rewrite_expr(expr.fd)
                expr.buf = self.__rewrite_expr(expr.buf)
            case HIR.SysRead():
                expr.fd = self.__rewrite_expr(expr.fd)
                expr.buf = self.__rewrite_expr(expr.buf)
            case HIR.Open():
                expr.path = self.__rewrite_expr(expr.path)
                expr.flags = self.__rewrite_expr(expr.flags)
            case HIR.Close():
                expr.fd = self.__rewrite_expr(expr.fd)
            case HIR.Tuple():
                expr.field_values = [self.__rewrite_expr(value) for value in expr.field_values]
            case HIR.Array():
                expr.elements = [self.__rewrite_expr(value) for value in expr.elements]
            case HIR.ArrayRepeat():
                expr.element = self.__rewrite_expr(expr.element)
            case HIR.Ty():
                self.__record_procedure(expr.type_id)
            case HIR.Closure():
                self.__record_procedure(expr.type_id)
                expr.captures = {name: self.__rewrite_expr(value) for name, value in expr.captures.items()}
            case HIR.AssumeInit():
                expr.value = self.__rewrite_expr(expr.value)
            case HIR.Var():
                self.__record_procedure(expr.type_id)
            case _:
                pass
        return expr

    def __record_procedure(self, type_id: int) -> None:
        resolved = self.__type_ctx.resolve_aliases(type_id)
        ty = self.__type_ctx[resolved]
        if isinstance(ty, (Type.FunctionType, Type.MethodType, Type.ClosureType)):
            self.__edges.setdefault(self.__current_def_type_id, set()).add(type_id)

    def __prune_unreachable_definitions(self) -> None:
        old_def_points = dict(self.__def_points)
        key_by_type: dict[int, int] = {}
        for key, def_point in old_def_points.items():
            key_by_type[key] = key
            key_by_type[def_point.type_id] = key

        roots: list[int] = []
        for key, def_point in old_def_points.items():
            ty = self.__type_ctx[def_point.type_id]
            if isinstance(ty, Type.FunctionType) and ty.custom_def.name == "main":
                roots.append(key)

        reachable: set[int] = set()
        pending = roots.copy()
        while pending:
            key = pending.pop()
            if key in reachable:
                continue
            reachable.add(key)
            def_point = old_def_points[key]
            for referenced_type in self.__edges.get(def_point.type_id, set()):
                referenced_key = key_by_type.get(referenced_type)
                if referenced_key is not None and referenced_key not in reachable:
                    pending.append(referenced_key)

        self.__def_points.clear()
        for key, def_point in old_def_points.items():
            if key in reachable:
                self.__def_points[key] = def_point

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
                    return self.__is_raw_mode, TypeCtx.bool_id
                self.__not_evaluable(expr, f"unknown compile configuration '{expr.name}'")
            case HIR.SizeOf():
                return self.__type_size(expr.target_type), expr.type_id
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
