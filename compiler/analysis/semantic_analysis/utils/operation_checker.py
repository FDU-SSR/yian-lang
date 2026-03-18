"""
This module manages type analysis utilities for operations.
"""

from dataclasses import dataclass
from typing import Callable, cast

from compiler.config.constants import IntrinsicTrait, IntrinsicType
from compiler.config.defs import TypeId
from compiler.utils import IR, ty
from compiler.utils.errors import CompilerError, SemanticError, YianTypeError
from compiler.utils.ty import MethodRegistry, TypeSpace


@dataclass
class OperationResult:
    result_type: TypeId         # The resulting type after the operation
    method_type: TypeId | None  # If the operation is overloaded, the method type ID used
    lvalue: bool                # Whether the result is an lvalue


BinaryHandler = Callable[[IR.TypedValue, IR.TypedValue], OperationResult]
UnaryHandler = Callable[[IR.TypedValue], OperationResult]


class OperationChecker:
    def __init__(self, type_space: TypeSpace, method_registry: MethodRegistry):
        self.__space = type_space
        self.__method_registry = method_registry

        self.__binary_handlers: dict[IR.Operator, BinaryHandler] = {
            IR.Operator.Add: self.__handle_add,
            IR.Operator.Minus: self.__handle_sub,
            IR.Operator.Star: self.__handle_mul,
            IR.Operator.Slash: self.__handle_div,
            IR.Operator.Percent: self.__handle_rem,
            IR.Operator.Ampersand: self.__handle_bitand,
            IR.Operator.Pipe: self.__handle_bitor,
            IR.Operator.Caret: self.__handle_bitxor,
            IR.Operator.Shl: self.__handle_shl,
            IR.Operator.Shr: self.__handle_shr,
            IR.Operator.Eq: self.__handle_eq,
            IR.Operator.Neq: self.__handle_ne,
            IR.Operator.Gt: self.__handle_gt,
            IR.Operator.Lt: self.__handle_lt,
            IR.Operator.Ge: self.__handle_ge,
            IR.Operator.Le: self.__handle_le,
            IR.Operator.And: self.__handle_and,
            IR.Operator.Or: self.__handle_or,
            IR.Operator.Index: self.__handle_index,
            IR.Operator.In: self.__handle_in,
            IR.Operator.NotIn: self.__handle_not_in,
            IR.Operator.Range: self.__handle_range,
        }

        self.__unary_handlers: dict[IR.Operator, UnaryHandler] = {
            IR.Operator.Add: self.__handle_pos,
            IR.Operator.Minus: self.__handle_neg,
            IR.Operator.Star: self.__handle_deref,
            IR.Operator.Ampersand: self.__handle_address_of,
            IR.Operator.Tilde: self.__handle_bitnot,
            IR.Operator.Not: self.__handle_not,
        }

    def __assignable(self, target_type: TypeId, source_type: TypeId) -> None:
        if target_type != source_type:
            raise YianTypeError.mismatch(target_type, source_type, self.__space.get_name)

    def __variable_assignable(self, target_type: TypeId, source_value: IR.Variable) -> None:
        self.__assignable(target_type, source_value.type_id)

    def __literal_assignable(self, target_type: TypeId, source_value: IR.LiteralValue) -> None:
        target_ty_def = self.__space[target_type]

        # Determine the type based on the suffix
        suffix = getattr(source_value, "suffix", None)
        if suffix is not None:
            assert isinstance(suffix, str)
            literal_type = TypeSpace.intrinsic_type(IntrinsicType.from_str(suffix))
            self.__assignable(target_type, literal_type)
            return

        match source_value:
            case IR.IntegerLiteral():
                if isinstance(target_ty_def, ty.FloatType):
                    source_value.type_id = target_type
                    return

                if not isinstance(target_ty_def, ty.IntType):
                    raise SemanticError("Cannot assign integer literal to non-integer type")

                bits = target_ty_def.size * 8
                if target_ty_def.is_signed:
                    min_val = -(2 ** (bits - 1))
                    max_val = 2 ** (bits - 1) - 1
                else:
                    min_val = 0
                    max_val = 2 ** bits - 1

                if not (min_val <= source_value.value <= max_val):
                    raise SemanticError(
                        f"Integer literal {source_value} out of range for type {self.__space.get_name(target_type)} "
                        f"(expected {min_val}..{max_val})"
                    )

                source_value.type_id = target_type
                return

            case IR.FloatLiteral():
                if not isinstance(target_ty_def, ty.FloatType):
                    raise SemanticError("Cannot assign float literal to non-float type")
                # Note: We do not check float range here due to complexity.

                source_value.type_id = target_type
                return

            case IR.StringLiteral():
                if not isinstance(target_ty_def, ty.StrType):
                    raise SemanticError("Cannot assign string literal to non-string type")

                return

            case IR.BooleanLiteral():
                if not isinstance(target_ty_def, ty.BoolType):
                    raise SemanticError("Cannot assign boolean literal to non-boolean type")

                return

            case IR.CharLiteral():
                if not isinstance(target_ty_def, ty.CharType):
                    raise SemanticError("Cannot assign char literal to non-char type")

                return

            case IR.ArrayLiteral():
                if not isinstance(target_ty_def, ty.ArrayType):
                    raise SemanticError("Cannot assign array literal to non-array type")

                # length check
                if len(source_value.elements) != target_ty_def.length:
                    raise SemanticError(
                        f"Array literal length {len(source_value.elements)} does not match target array type length {target_ty_def.length}."
                    )

                # element type check
                for element in source_value.elements:
                    self.__literal_assignable(target_ty_def.element_type, element)

                source_value.type_id = target_type
                return

            case IR.TupleLiteral():
                if not isinstance(target_ty_def, ty.TupleType):
                    raise SemanticError("Cannot assign tuple literal to non-tuple type")

                # length check
                if len(source_value.elements) != len(target_ty_def.element_types):
                    raise SemanticError(
                        f"Tuple literal length {len(source_value.elements)} does not match target tuple type length {len(target_ty_def.element_types)}."
                    )

                # element type check
                for element, element_type in zip(source_value.elements, target_ty_def.element_types):
                    self.__literal_assignable(element_type, element)

                source_value.type_id = target_type
                return

    def assignable(self, target_type: TypeId, source_value: IR.TypedValue) -> None:
        if isinstance(source_value, IR.Variable):
            self.__variable_assignable(target_type, source_value)
        elif isinstance(source_value, IR.LiteralValue):
            self.__literal_assignable(target_type, source_value)

    def binary_op(self, op: IR.Operator, left: IR.TypedValue, right: IR.TypedValue) -> OperationResult:
        if op not in self.__binary_handlers:
            raise YianTypeError(f"Operator {op} is not a binary operator")

        handler = self.__binary_handlers[op]
        return handler(left, right)

    def unary_op(self, op: IR.Operator, operand: IR.TypedValue) -> OperationResult:
        if op not in self.__unary_handlers:
            raise YianTypeError(f"Operator {op} is not a unary operator")

        handler = self.__unary_handlers[op]
        return handler(operand)

    def __handle_add(self, left: IR.TypedValue, right: IR.TypedValue) -> OperationResult:
        # 1) both literal
        if isinstance(left, IR.LiteralValue) and isinstance(right, IR.LiteralValue):
            if not isinstance(left, (IR.IntegerLiteral, IR.FloatLiteral)) or not isinstance(right, (IR.IntegerLiteral, IR.FloatLiteral)):
                raise SemanticError(f"Unsupported literal types for addition: {left} + {right}")

            result_type = self.__analyze_literal_types([left, right])
            return OperationResult(result_type=result_type, method_type=None, lvalue=False)

        # 2) variable + literal / literal + variable
        if (isinstance(left, IR.Variable) and isinstance(right, IR.LiteralValue)) or (isinstance(left, IR.LiteralValue) and isinstance(right, IR.Variable)):
            var = left if isinstance(left, IR.Variable) else cast(IR.Variable, right)
            lit = right if isinstance(right, IR.LiteralValue) else cast(IR.LiteralValue, left)

            var_ty = self.__space[var.type_id]

            # 2.a) int/float add
            if isinstance(var_ty, (ty.IntType, ty.FloatType)):
                # Constrain literal by assignability check against variable type.
                self.__literal_assignable(var.type_id, lit)
                return OperationResult(result_type=var.type_id, method_type=None, lvalue=False)

            # 2.b) pointer add
            if isinstance(var_ty, ty.PointerType):
                # Constrain literal to be integer type.
                self.__literal_assignable(TypeSpace.u64_id, lit)
                return OperationResult(result_type=var.type_id, method_type=None, lvalue=False)

            # 2.c) overloaded addition
            method = self.__method_registry.trait_method_lookup(IntrinsicTrait.Add, var_ty.type_id, "add", [lit], self.assignable)
            if method is not None:
                method_ty = self.__space[method].expect_method()
                return_type = method_ty.return_type(self.__space.instantiate)
                return OperationResult(result_type=return_type, method_type=method, lvalue=False)

            # 2.d) error
            raise SemanticError(f"Unsupported variable type for addition: {self.__space.get_name(var.type_id)}")

        # 3) both variable
        if isinstance(left, IR.Variable) and isinstance(right, IR.Variable):
            left_ty = self.__space[left.type_id]
            right_ty = self.__space[right.type_id]

            # 3.a) int/float addition
            if isinstance(left_ty, (ty.IntType, ty.FloatType)) and isinstance(right_ty, (ty.IntType, ty.FloatType)):
                if left.type_id != right.type_id:
                    raise YianTypeError(
                        f"Binary operator {IR.Operator.Add} requires operands of the same type, got "
                        f"'{self.__space.get_name(left.type_id)}' and '{self.__space.get_name(right.type_id)}'."
                    )
                return OperationResult(result_type=left.type_id, method_type=None, lvalue=False)

            # 3.b) pointer addition
            if isinstance(left_ty, ty.PointerType) and isinstance(right_ty, ty.IntType):
                if right_ty.type_id != TypeSpace.u64_id:
                    raise YianTypeError(
                        f"Pointer addition requires the right operand to be of type 'u64', got '{self.__space.get_name(right.type_id)}'."
                    )
                return OperationResult(result_type=left.type_id, method_type=None, lvalue=False)
            if isinstance(right_ty, ty.PointerType) and isinstance(left_ty, ty.IntType):
                if left_ty.type_id != TypeSpace.u64_id:
                    raise YianTypeError(
                        f"Pointer addition requires the left operand to be of type 'u64', got '{self.__space.get_name(left.type_id)}'."
                    )
                return OperationResult(result_type=right.type_id, method_type=None, lvalue=False)

            # 3.c) overloaded addition
            method = self.__method_registry.trait_method_lookup(IntrinsicTrait.Add, left_ty.type_id, "add", [right], self.assignable)
            if method is not None:
                method_ty = self.__space[method].expect_method()
                return_type = method_ty.return_type(self.__space.instantiate)
                return OperationResult(result_type=return_type, method_type=method, lvalue=False)

            # 3.d) error
            raise SemanticError(f"Unsupported variable types for addition: {self.__space.get_name(left.type_id)} + {self.__space.get_name(right.type_id)}")

        raise CompilerError("Unhandled case in addition operation checking.")

    def __handle_sub(self, left: IR.TypedValue, right: IR.TypedValue) -> OperationResult:
        # 1) both literal
        if isinstance(left, IR.LiteralValue) and isinstance(right, IR.LiteralValue):
            if not isinstance(left, (IR.IntegerLiteral, IR.FloatLiteral)) or not isinstance(right, (IR.IntegerLiteral, IR.FloatLiteral)):
                raise SemanticError(f"Unsupported literal types for subtraction: {left} - {right}")

            result_type = self.__analyze_literal_types([left, right])
            return OperationResult(result_type=result_type, method_type=None, lvalue=False)

        # 2) variable + literal / literal + variable
        if (isinstance(left, IR.Variable) and isinstance(right, IR.LiteralValue)) or (
            isinstance(left, IR.LiteralValue) and isinstance(right, IR.Variable)
        ):
            var = left if isinstance(left, IR.Variable) else cast(IR.Variable, right)
            lit = right if isinstance(right, IR.LiteralValue) else cast(IR.LiteralValue, left)
            var_ty = self.__space[var.type_id]

            if isinstance(var_ty, (ty.IntType, ty.FloatType)):
                self.__literal_assignable(var.type_id, lit)
                return OperationResult(result_type=var.type_id, method_type=None, lvalue=False)

            if isinstance(var_ty, ty.PointerType):
                # Pointer - u64 -> Pointer
                if isinstance(left, IR.Variable) and isinstance(right, IR.LiteralValue):
                    self.__literal_assignable(TypeSpace.u64_id, lit)
                    return OperationResult(result_type=var.type_id, method_type=None, lvalue=False)
                # Int - Pointer -> Forbidden
                raise SemanticError(
                    f"Unsupported operand types for subtraction: {type(left).__name__} - {type(right).__name__}"
                )

            raise SemanticError(f"Unsupported variable type for subtraction: {self.__space.get_name(var.type_id)}")

        # 3) both variable
        if isinstance(left, IR.Variable) and isinstance(right, IR.Variable):
            left_ty = self.__space[left.type_id]
            right_ty = self.__space[right.type_id]

            if isinstance(left_ty, (ty.IntType, ty.FloatType)) and isinstance(right_ty, (ty.IntType, ty.FloatType)):
                if left.type_id != right.type_id:
                    raise YianTypeError(
                        f"Binary operator {IR.Operator.Minus} requires operands of the same type, got "
                        f"'{self.__space.get_name(left.type_id)}' and '{self.__space.get_name(right.type_id)}'."
                    )
                return OperationResult(result_type=left.type_id, method_type=None, lvalue=False)

            if isinstance(left_ty, ty.PointerType) and isinstance(right_ty, ty.IntType):
                if right_ty.type_id != TypeSpace.u64_id:
                    raise YianTypeError(
                        f"Pointer subtraction requires the right operand to be of type 'u64', got '{self.__space.get_name(right.type_id)}'."
                    )
                return OperationResult(result_type=left.type_id, method_type=None, lvalue=False)

            if isinstance(left_ty, ty.PointerType) and isinstance(right_ty, ty.PointerType):
                if left_ty.pointee_type != right_ty.pointee_type:
                    raise YianTypeError(
                        f"Pointer subtraction requires operands of the same pointer type, got "
                        f"'{self.__space.get_name(left.type_id)}' and '{self.__space.get_name(right.type_id)}'."
                    )
                return OperationResult(result_type=TypeSpace.i64_id, method_type=None, lvalue=False)

        # overloaded subtraction
            method = self.__method_registry.trait_method_lookup(IntrinsicTrait.Sub, left_ty.type_id, "sub", [right], self.assignable)
            if method is not None:
                method_ty = self.__space[method].expect_method()
                return_type = method_ty.return_type(self.__space.instantiate)
                return OperationResult(result_type=return_type, method_type=method, lvalue=False)

            raise SemanticError(f"Unsupported variable types for subtraction: {self.__space.get_name(left.type_id)} - {self.__space.get_name(right.type_id)}")

        raise CompilerError("Unhandled case in subtraction operation checking.")

    def __handle_mul(self, left: IR.TypedValue, right: IR.TypedValue) -> OperationResult:
        # 1) both literal
        if isinstance(left, IR.LiteralValue) and isinstance(right, IR.LiteralValue):
            if not isinstance(left, (IR.IntegerLiteral, IR.FloatLiteral)) or not isinstance(right, (IR.IntegerLiteral, IR.FloatLiteral)):
                raise SemanticError(f"Unsupported literal types for multiplication: {left} * {right}")

            result_type = self.__analyze_literal_types([left, right])
            return OperationResult(result_type=result_type, method_type=None, lvalue=False)

        # 2) variable + literal / literal + variable
        if (isinstance(left, IR.Variable) and isinstance(right, IR.LiteralValue)) or (
            isinstance(left, IR.LiteralValue) and isinstance(right, IR.Variable)
        ):
            var = left if isinstance(left, IR.Variable) else cast(IR.Variable, right)
            lit = right if isinstance(right, IR.LiteralValue) else cast(IR.LiteralValue, left)
            var_ty = self.__space[var.type_id]

            if isinstance(var_ty, (ty.IntType, ty.FloatType)):
                self.__literal_assignable(var.type_id, lit)
                return OperationResult(result_type=var.type_id, method_type=None, lvalue=False)

            raise SemanticError(f"Unsupported variable type for multiplication: {self.__space.get_name(var.type_id)}")

        # 3) both variable
        if isinstance(left, IR.Variable) and isinstance(right, IR.Variable):
            left_ty = self.__space[left.type_id]
            right_ty = self.__space[right.type_id]

            if isinstance(left_ty, (ty.IntType, ty.FloatType)) and isinstance(right_ty, (ty.IntType, ty.FloatType)):
                if left.type_id != right.type_id:
                    raise YianTypeError(
                        f"Binary operator {IR.Operator.Star} requires operands of the same type, got "
                        f"'{self.__space.get_name(left.type_id)}' and '{self.__space.get_name(right.type_id)}'."
                    )
                return OperationResult(result_type=left.type_id, method_type=None, lvalue=False)

            # overloaded multiplication
            method = self.__method_registry.trait_method_lookup(IntrinsicTrait.Mul, left_ty.type_id, "mul", [right], self.assignable)
            if method is not None:
                method_ty = self.__space[method].expect_method()
                return_type = method_ty.return_type(self.__space.instantiate)
                return OperationResult(result_type=return_type, method_type=method, lvalue=False)

            raise SemanticError(
                f"Unsupported variable types for multiplication: {self.__space.get_name(left.type_id)} * {self.__space.get_name(right.type_id)}"
            )

        raise CompilerError("Unhandled case in multiplication operation checking.")

    def __handle_div(self, left: IR.TypedValue, right: IR.TypedValue) -> OperationResult:
        # 1) both literal
        if isinstance(left, IR.LiteralValue) and isinstance(right, IR.LiteralValue):
            if not isinstance(left, (IR.IntegerLiteral, IR.FloatLiteral)) or not isinstance(right, (IR.IntegerLiteral, IR.FloatLiteral)):
                raise SemanticError(f"Unsupported literal types for division: {left} / {right}")

            result_type = self.__analyze_literal_types([left, right])
            return OperationResult(result_type=result_type, method_type=None, lvalue=False)

        # 2) variable + literal / literal + variable
        if (isinstance(left, IR.Variable) and isinstance(right, IR.LiteralValue)) or (
            isinstance(left, IR.LiteralValue) and isinstance(right, IR.Variable)
        ):
            var = left if isinstance(left, IR.Variable) else cast(IR.Variable, right)
            lit = right if isinstance(right, IR.LiteralValue) else cast(IR.LiteralValue, left)
            var_ty = self.__space[var.type_id]

            if isinstance(var_ty, (ty.IntType, ty.FloatType)):
                self.__literal_assignable(var.type_id, lit)
                return OperationResult(result_type=var.type_id, method_type=None, lvalue=False)

            raise SemanticError(f"Unsupported variable type for division: {self.__space.get_name(var.type_id)}")

        # 3) both variable
        if isinstance(left, IR.Variable) and isinstance(right, IR.Variable):
            left_ty = self.__space[left.type_id]
            right_ty = self.__space[right.type_id]

            if isinstance(left_ty, (ty.IntType, ty.FloatType)) and isinstance(right_ty, (ty.IntType, ty.FloatType)):
                if left.type_id != right.type_id:
                    raise YianTypeError(
                        f"Binary operator {IR.Operator.Slash} requires operands of the same type, got "
                        f"'{self.__space.get_name(left.type_id)}' and '{self.__space.get_name(right.type_id)}'."
                    )
                return OperationResult(result_type=left.type_id, method_type=None, lvalue=False)

            # overloaded division
            method = self.__method_registry.trait_method_lookup(IntrinsicTrait.Div, left_ty.type_id, "div", [right], self.assignable)
            if method is not None:
                method_ty = self.__space[method].expect_method()
                return_type = method_ty.return_type(self.__space.instantiate)
                return OperationResult(result_type=return_type, method_type=method, lvalue=False)

            raise SemanticError(
                f"Unsupported variable types for division: {self.__space.get_name(left.type_id)} / {self.__space.get_name(right.type_id)}"
            )

        raise CompilerError("Unhandled case in division operation checking.")

    def __handle_rem(self, left: IR.TypedValue, right: IR.TypedValue) -> OperationResult:
        # 1) both literal - Integers only
        if isinstance(left, IR.LiteralValue) and isinstance(right, IR.LiteralValue):
            if not (isinstance(left, IR.IntegerLiteral) and isinstance(right, IR.IntegerLiteral)):
                raise SemanticError(f"Unsupported literal types for remainder: {left} % {right}")

            result_type = self.__analyze_literal_types([left, right])
            return OperationResult(result_type=result_type, method_type=None, lvalue=False)

        # 2) variable + literal / literal + variable
        if (isinstance(left, IR.Variable) and isinstance(right, IR.LiteralValue)) or (
            isinstance(left, IR.LiteralValue) and isinstance(right, IR.Variable)
        ):
            var = left if isinstance(left, IR.Variable) else cast(IR.Variable, right)
            lit = right if isinstance(right, IR.LiteralValue) else cast(IR.LiteralValue, left)
            var_ty = self.__space[var.type_id]

            if isinstance(var_ty, ty.IntType):
                self.__literal_assignable(var.type_id, lit)
                return OperationResult(result_type=var.type_id, method_type=None, lvalue=False)

            raise SemanticError(f"Unsupported variable type for remainder: {self.__space.get_name(var.type_id)}")

        # 3) both variable
        if isinstance(left, IR.Variable) and isinstance(right, IR.Variable):
            left_ty = self.__space[left.type_id]
            right_ty = self.__space[right.type_id]

            if isinstance(left_ty, ty.IntType) and isinstance(right_ty, ty.IntType):
                if left.type_id != right.type_id:
                    raise YianTypeError(
                        f"Binary operator {IR.Operator.Percent} requires operands of the same type, got "
                        f"'{self.__space.get_name(left.type_id)}' and '{self.__space.get_name(right.type_id)}'."
                    )
                return OperationResult(result_type=left.type_id, method_type=None, lvalue=False)

            # overloaded remainder
            method = self.__method_registry.trait_method_lookup(IntrinsicTrait.Rem, left_ty.type_id, "rem", [right], self.assignable)
            if method is not None:
                method_ty = self.__space[method].expect_method()
                return_type = method_ty.return_type(self.__space.instantiate)
                return OperationResult(result_type=return_type, method_type=method, lvalue=False)

            raise SemanticError(
                f"Unsupported variable types for remainder: {self.__space.get_name(left.type_id)} % {self.__space.get_name(right.type_id)}"
            )

        raise CompilerError("Unhandled case in remainder operation checking.")

    def __handle_int_bitwise(self, op: IR.Operator, left: IR.TypedValue, right: IR.TypedValue, trait: IntrinsicTrait, method_name: str) -> OperationResult:
        # Common handler for &, |, ^
        # 1) both literal
        if isinstance(left, IR.LiteralValue) and isinstance(right, IR.LiteralValue):
            if not (isinstance(left, IR.IntegerLiteral) and isinstance(right, IR.IntegerLiteral)):
                raise SemanticError(f"Unsupported literal types for bitwise op {op}: {left}, {right}")

            result_type = self.__analyze_literal_types([left, right])
            return OperationResult(result_type=result_type, method_type=None, lvalue=False)

        # 2) variable + literal / literal + variable
        if (isinstance(left, IR.Variable) and isinstance(right, IR.LiteralValue)) or (
            isinstance(left, IR.LiteralValue) and isinstance(right, IR.Variable)
        ):
            var = left if isinstance(left, IR.Variable) else cast(IR.Variable, right)
            lit = right if isinstance(right, IR.LiteralValue) else cast(IR.LiteralValue, left)
            var_ty = self.__space[var.type_id]

            if isinstance(var_ty, ty.IntType):
                self.__literal_assignable(var.type_id, lit)
                return OperationResult(result_type=var.type_id, method_type=None, lvalue=False)
            raise SemanticError(f"Unsupported variable type for bitwise op {op}: {self.__space.get_name(var.type_id)}")

        # 3) both variable
        if isinstance(left, IR.Variable) and isinstance(right, IR.Variable):
            left_ty = self.__space[left.type_id]
            right_ty = self.__space[right.type_id]

            if isinstance(left_ty, ty.IntType) and isinstance(right_ty, ty.IntType):
                if left.type_id != right.type_id:
                    raise YianTypeError(
                        f"Binary operator {op} requires operands of the same type, got "
                        f"'{self.__space.get_name(left.type_id)}' and '{self.__space.get_name(right.type_id)}'."
                    )
                return OperationResult(result_type=left.type_id, method_type=None, lvalue=False)

            # overloaded bitwise
            method = self.__method_registry.trait_method_lookup(trait, left_ty.type_id, method_name, [right], self.assignable)
            if method is not None:
                method_ty = self.__space[method].expect_method()
                return_type = method_ty.return_type(self.__space.instantiate)
                return OperationResult(result_type=return_type, method_type=method, lvalue=False)

            raise SemanticError(
                f"Unsupported variable types for bitwise op {op}: {self.__space.get_name(left.type_id)}, {self.__space.get_name(right.type_id)}"
            )

        raise CompilerError(f"Unhandled case in bitwise op {op} checking.")

    def __handle_bitand(self, left: IR.TypedValue, right: IR.TypedValue) -> OperationResult:
        return self.__handle_int_bitwise(IR.Operator.Ampersand, left, right, IntrinsicTrait.BitAnd, "bit_and")

    def __handle_bitor(self, left: IR.TypedValue, right: IR.TypedValue) -> OperationResult:
        return self.__handle_int_bitwise(IR.Operator.Pipe, left, right, IntrinsicTrait.BitOr, "bit_or")

    def __handle_bitxor(self, left: IR.TypedValue, right: IR.TypedValue) -> OperationResult:
        return self.__handle_int_bitwise(IR.Operator.Caret, left, right, IntrinsicTrait.BitXor, "bit_xor")

    def __handle_shift(self, op: IR.Operator, left: IR.TypedValue, right: IR.TypedValue, trait: IntrinsicTrait, method_name: str) -> OperationResult:
        # Left: Integer (Var/Lit promote to i32)
        # Right: Integer (Var) or Integer Literal

        # 1) builtin shift
        # Left literal: promote to i32 (builtin only)
        # if isinstance(left, IR.LiteralValue):
        #     if not isinstance(left, IR.IntegerLiteral):
        #         raise SemanticError(f"Unsupported left literal type for shift: {left}")
        #     self.__literal_assignable(TypeSpace.i32_id, left)
        #     left_ty_id = TypeSpace.i32_id

        #     # Right must be integer type or integer literal
        #     if isinstance(right, IR.LiteralValue):
        #         if not isinstance(right, IR.IntegerLiteral):
        #             raise SemanticError(f"Unsupported right literal type for shift: {right}")
        #         self.__literal_assignable(TypeSpace.i32_id, right)
        #     else:
        #         right_ty_def = self.__space[right.type_id]
        #         if not isinstance(right_ty_def, ty.IntType):
        #             raise YianTypeError(
        #                 f"Shift operator {op} requires right operand to be an integer type, got '{self.__space.get_name(right.type_id)}'."
        #             )
        #     return OperationResult(result_type=left_ty_id, method_type=None, lvalue=False)

        # Left is a variable: try builtin int-shift first
        left_ty_id = left.type_id
        left_ty_def = self.__space[left_ty_id]
        if isinstance(left_ty_def, ty.IntType):
            if isinstance(right, IR.LiteralValue):
                if not isinstance(right, IR.IntegerLiteral):
                    raise SemanticError(f"Unsupported right literal type for shift: {right}")
                self.__literal_assignable(TypeSpace.i32_id, right)
            else:
                right_ty_def = self.__space[right.type_id]
                if not isinstance(right_ty_def, ty.IntType):
                    raise YianTypeError(
                        f"Shift operator {op} requires right operand to be an integer type, got '{self.__space.get_name(right.type_id)}'."
                    )
            return OperationResult(result_type=left_ty_id, method_type=None, lvalue=False)

        # 2) overloaded shift (Shl<Rhs, Output> / Shr<Rhs, Output>)
        method = self.__method_registry.trait_method_lookup(trait, left_ty_id, method_name, [right], self.assignable)
        if method is not None:
            method_ty = self.__space[method].expect_method()
            return_type = method_ty.return_type(self.__space.instantiate)
            return OperationResult(result_type=return_type, method_type=method, lvalue=False)

        # error
        if not isinstance(left_ty_def, ty.IntType):
            raise SemanticError(f"Unsupported left variable type for shift: {self.__space.get_name(left_ty_id)}")
        raise SemanticError(
            f"Unsupported operand types for shift op {op}: {self.__space.get_name(left_ty_id)}, {self.__space.get_name(right.type_id)}"
        )

    def __handle_shl(self, left: IR.TypedValue, right: IR.TypedValue) -> OperationResult:
        return self.__handle_shift(IR.Operator.Shl, left, right, IntrinsicTrait.Shl, "shl")

    def __handle_shr(self, left: IR.TypedValue, right: IR.TypedValue) -> OperationResult:
        return self.__handle_shift(IR.Operator.Shr, left, right, IntrinsicTrait.Shr, "shr")

    def __handle_cmp_overload(self, op: IR.Operator, left: IR.TypedValue, right: IR.TypedValue) -> OperationResult | None:
        # Per manual: only left operand can overload comparison.
        if not isinstance(left, IR.Variable):
            return None

        match op:
            case IR.Operator.Eq:
                method_name = "eq"
                trait = IntrinsicTrait.PartialEq
            case IR.Operator.Neq:
                method_name = "ne"
                trait = IntrinsicTrait.PartialEq
            case IR.Operator.Lt:
                method_name = "lt"
                trait = IntrinsicTrait.PartialOrd
            case IR.Operator.Gt:
                method_name = "gt"
                trait = IntrinsicTrait.PartialOrd
            case IR.Operator.Le:
                method_name = "le"
                trait = IntrinsicTrait.PartialOrd
            case IR.Operator.Ge:
                method_name = "ge"
                trait = IntrinsicTrait.PartialOrd
            case _:
                raise CompilerError(f"Unhandled comparison operator: {op}")

        method = self.__method_registry.trait_method_lookup(trait, left.type_id, method_name, [right], self.assignable)
        if method is not None:
            method_ty = self.__space[method].expect_method()
            return_type = method_ty.return_type(self.__space.instantiate)
            return OperationResult(result_type=return_type, method_type=method, lvalue=False)

        return None

    def __handle_cmp(self, op: IR.Operator, left: IR.TypedValue, right: IR.TypedValue, order: bool) -> OperationResult:
        # Eq/Ne: order=False. Lt/Gt/Le/Ge: order=True

        # 1) both literal
        if isinstance(left, IR.LiteralValue) and isinstance(right, IR.LiteralValue):
            cmp_type = self.__analyze_literal_types([left, right])
            if order and cmp_type not in (TypeSpace.i32_id, TypeSpace.f64_id):
                raise SemanticError(f"Unsupported literals for ordered comparison: {left}, {right}")
            return OperationResult(result_type=TypeSpace.bool_id, method_type=None, lvalue=False)

        # 2) var + lit / lit + var
        if (isinstance(left, IR.Variable) and isinstance(right, IR.LiteralValue)) or (
            isinstance(left, IR.LiteralValue) and isinstance(right, IR.Variable)
        ):
            var = left if isinstance(left, IR.Variable) else cast(IR.Variable, right)
            lit = right if isinstance(right, IR.LiteralValue) else cast(IR.LiteralValue, left)
            var_ty = self.__space[var.type_id]

            # Check if type supports operation
            is_valid = False
            if isinstance(var_ty, (ty.IntType, ty.FloatType, ty.PointerType)):
                is_valid = True
            elif not order and isinstance(var_ty, (ty.BoolType, ty.CharType)):
                is_valid = True

            if not is_valid:
                # Try overload only when the real left operand is a variable.
                overload_result = self.__handle_cmp_overload(op, left, right)
                if overload_result is not None:
                    return overload_result
                raise SemanticError(f"Unsupported variable type for comparison {op}: {self.__space.get_name(var.type_id)}")

            self.__literal_assignable(var.type_id, lit)
            return OperationResult(result_type=TypeSpace.bool_id, method_type=None, lvalue=False)

        # 3) both variable
        if isinstance(left, IR.Variable) and isinstance(right, IR.Variable):
            # If types differ, builtin comparison is invalid; try overload first (PartialEq/PartialOrd allow Rhs != Self).
            if left.type_id != right.type_id:
                overload_result = self.__handle_cmp_overload(op, left, right)
                if overload_result is not None:
                    return overload_result
                raise YianTypeError(
                    f"Operator {op} requires operands of the same type, got "
                    f"'{self.__space.get_name(left.type_id)}' and '{self.__space.get_name(right.type_id)}'."
                )
            var_ty = self.__space[left.type_id]
            is_valid = False
            if isinstance(var_ty, (ty.IntType, ty.FloatType, ty.PointerType)):
                is_valid = True
            elif not order and isinstance(var_ty, (ty.BoolType, ty.CharType)):
                is_valid = True

            if is_valid:
                return OperationResult(result_type=TypeSpace.bool_id, method_type=None, lvalue=False)

            overload_result = self.__handle_cmp_overload(op, left, right)
            if overload_result is not None:
                return overload_result

            raise SemanticError(f"Unsupported variable types for comparison {op}: {self.__space.get_name(left.type_id)}")

        raise CompilerError(f"Unhandled case in comparison {op} checking.")

    def __handle_eq(self, left: IR.TypedValue, right: IR.TypedValue) -> OperationResult:
        return self.__handle_cmp(IR.Operator.Eq, left, right, order=False)

    def __handle_ne(self, left: IR.TypedValue, right: IR.TypedValue) -> OperationResult:
        return self.__handle_cmp(IR.Operator.Neq, left, right, order=False)

    def __handle_gt(self, left: IR.TypedValue, right: IR.TypedValue) -> OperationResult:
        return self.__handle_cmp(IR.Operator.Gt, left, right, order=True)

    def __handle_lt(self, left: IR.TypedValue, right: IR.TypedValue) -> OperationResult:
        return self.__handle_cmp(IR.Operator.Lt, left, right, order=True)

    def __handle_ge(self, left: IR.TypedValue, right: IR.TypedValue) -> OperationResult:
        return self.__handle_cmp(IR.Operator.Ge, left, right, order=True)

    def __handle_le(self, left: IR.TypedValue, right: IR.TypedValue) -> OperationResult:
        return self.__handle_cmp(IR.Operator.Le, left, right, order=True)

    def __handle_logic(self, op: IR.Operator, left: IR.TypedValue, right: IR.TypedValue) -> OperationResult:
        # Bool only
        target = TypeSpace.bool_id
        if isinstance(left, IR.LiteralValue):
            self.__literal_assignable(target, left)
        elif left.type_id != target:
            raise YianTypeError(f"Operator {op} requires bool operands, got {self.__space.get_name(left.type_id)}")

        if isinstance(right, IR.LiteralValue):
            self.__literal_assignable(target, right)
        elif right.type_id != target:
            raise YianTypeError(f"Operator {op} requires bool operands, got {self.__space.get_name(right.type_id)}")

        return OperationResult(result_type=target, method_type=None, lvalue=False)

    def __handle_and(self, left: IR.TypedValue, right: IR.TypedValue) -> OperationResult:
        return self.__handle_logic(IR.Operator.And, left, right)

    def __handle_or(self, left: IR.TypedValue, right: IR.TypedValue) -> OperationResult:
        return self.__handle_logic(IR.Operator.Or, left, right)

    def __handle_index(self, left: IR.TypedValue, right: IR.TypedValue) -> OperationResult:
        if isinstance(left, IR.LiteralValue):
            raise SemanticError("Cannot index into a literal value.")

        left_ty_id = left.type_id
        left_ty = self.__space[left_ty_id]

        # 1) Array/Pointer/Slice indexing
        if isinstance(left_ty, (ty.ArrayType, ty.PointerType, ty.SliceType)):
            match left_ty:
                case ty.ArrayType():
                    element_type = left_ty.element_type
                case ty.PointerType():
                    element_type = left_ty.pointee_type
                case ty.SliceType():
                    element_type = left_ty.element_type

            if isinstance(right, IR.LiteralValue):
                # Must be integer literal assignable to u64
                if not isinstance(right, IR.IntegerLiteral):
                    raise YianTypeError(f"Index requires integer literal, got {right}")
                self.__literal_assignable(TypeSpace.u64_id, right)
                return OperationResult(result_type=element_type, method_type=None, lvalue=True)

            if right.type_id == TypeSpace.u64_id:
                # u64 variable is ok
                return OperationResult(result_type=element_type, method_type=None, lvalue=True)

            if self.__space.is_instantiated(TypeSpace.range_id, [TypeSpace.u64_id], right.type_id):
                # range<u64> is also ok, results in slice
                slice_type = self.__space.alloc_slice(element_type)
                return OperationResult(result_type=slice_type, method_type=None, lvalue=False)

            raise YianTypeError(f"Index requires type u64 or range<u64>, got '{self.__space.get_name(right.type_id)}'.")

        # 2) Tuple indexing
        if isinstance(left_ty, ty.TupleType):
            # Right operand MUST be an integer literal
            if not isinstance(right, IR.IntegerLiteral):
                raise YianTypeError("Tuple index must be an integer literal.")

            index_val = right.value
            if index_val < 0 or index_val >= len(left_ty.element_types):
                raise SemanticError(f"Tuple index {index_val} out of bounds for tuple of size {len(left_ty.element_types)}.")

            # Assign type to the index literal (i32 is safe assumption for index)
            self.__literal_assignable(TypeSpace.i32_id, right)

            elem_type = left_ty.element_types[index_val]
            return OperationResult(result_type=elem_type, method_type=None, lvalue=True)

        # 3) Index overload
        method = self.__method_registry.trait_method_lookup(IntrinsicTrait.Index, left_ty_id, "index", [right], self.assignable)
        if method is not None:
            method_ty = self.__space[method].expect_method()
            return_type = method_ty.return_type(self.__space.instantiate)
            return OperationResult(result_type=return_type, method_type=method, lvalue=True)

        raise YianTypeError(f"Cannot apply index operator {IR.Operator.Index} to type '{self.__space.get_name(left_ty_id)}'.")

    def __handle_in(self, left: IR.TypedValue, right: IR.TypedValue) -> OperationResult:
        raise NotImplementedError("The 'in' operator is not implemented yet.")

    def __handle_not_in(self, left: IR.TypedValue, right: IR.TypedValue) -> OperationResult:
        return self.__handle_in(left, right)

    def __handle_range(self, left: IR.TypedValue, right: IR.TypedValue) -> OperationResult:
        # 1) Both literal
        if isinstance(left, IR.LiteralValue) and isinstance(right, IR.LiteralValue):
            target_type = self.__analyze_literal_types([left, right])
            return_type = self.__space.alloc_range(target_type)
            return OperationResult(result_type=return_type, method_type=None, lvalue=False)

        # 2) var + lit / lit + var
        if (isinstance(left, IR.Variable) and isinstance(right, IR.LiteralValue)) or (
            isinstance(left, IR.LiteralValue) and isinstance(right, IR.Variable)
        ):
            var = left if isinstance(left, IR.Variable) else cast(IR.Variable, right)
            lit = right if isinstance(right, IR.LiteralValue) else cast(IR.LiteralValue, left)
            var_ty = self.__space[var.type_id]

            if isinstance(var_ty, (ty.IntType, ty.FloatType)):
                self.__literal_assignable(var.type_id, lit)
                range_ty = self.__space.alloc_range(var.type_id)
                return OperationResult(result_type=range_ty, method_type=None, lvalue=False)

            raise SemanticError(f"Unsupported variable type for range: {self.__space.get_name(var.type_id)}")

        # 3) both variable
        if isinstance(left, IR.Variable) and isinstance(right, IR.Variable):
            left_ty = self.__space[left.type_id]
            right_ty = self.__space[right.type_id]

            if isinstance(left_ty, (ty.IntType, ty.FloatType)) and isinstance(right_ty, (ty.IntType, ty.FloatType)):
                if left.type_id != right.type_id:
                    raise YianTypeError(
                        f"Range operator requires operands of the same type, got "
                        f"'{self.__space.get_name(left.type_id)}' and '{self.__space.get_name(right.type_id)}'."
                    )
                range_ty = self.__space.alloc_range(left.type_id)
                return OperationResult(result_type=range_ty, method_type=None, lvalue=False)

            raise SemanticError(
                f"Unsupported variable types for range: {self.__space.get_name(left.type_id)}, {self.__space.get_name(right.type_id)}"
            )

        raise CompilerError("Unhandled case in range operation checking.")

    def __handle_pos(self, operand: IR.TypedValue) -> OperationResult:
        # Arithmetic only
        if isinstance(operand, IR.LiteralValue):
            if isinstance(operand, IR.IntegerLiteral):
                self.__literal_assignable(TypeSpace.i32_id, operand)
                return OperationResult(result_type=TypeSpace.i32_id, method_type=None, lvalue=False)
            if isinstance(operand, IR.FloatLiteral):
                self.__literal_assignable(TypeSpace.f64_id, operand)
                return OperationResult(result_type=TypeSpace.f64_id, method_type=None, lvalue=False)
            raise SemanticError(f"Unsupported operand for unary +: {operand}")

        op_ty = self.__space[operand.type_id]
        if not isinstance(op_ty, (ty.IntType, ty.FloatType)):
            raise SemanticError(f"Unsupported operand type for unary +: {self.__space.get_name(operand.type_id)}")
        return OperationResult(result_type=operand.type_id, method_type=None, lvalue=False)

    def __handle_neg(self, operand: IR.TypedValue) -> OperationResult:
        # Arithmetic only
        if isinstance(operand, IR.LiteralValue):
            if isinstance(operand, IR.IntegerLiteral):
                self.__literal_assignable(TypeSpace.i32_id, operand)
                return OperationResult(result_type=TypeSpace.i32_id, method_type=None, lvalue=False)
            if isinstance(operand, IR.FloatLiteral):
                self.__literal_assignable(TypeSpace.f64_id, operand)
                return OperationResult(result_type=TypeSpace.f64_id, method_type=None, lvalue=False)
            raise SemanticError(f"Unsupported operand for unary -: {operand}")

        op_ty = self.__space[operand.type_id]
        if isinstance(op_ty, (ty.IntType, ty.FloatType)):
            return OperationResult(result_type=operand.type_id, method_type=None, lvalue=False)

        # overload
        method = self.__method_registry.trait_method_lookup(IntrinsicTrait.Neg, operand.type_id, "neg", [], self.assignable)
        if method is not None:
            method_ty = self.__space[method].expect_method()
            return_type = method_ty.return_type(self.__space.instantiate)
            return OperationResult(result_type=return_type, method_type=method, lvalue=False)

        raise SemanticError(f"Unsupported operand type for unary -: {self.__space.get_name(operand.type_id)}")

    def __handle_deref(self, operand: IR.TypedValue) -> OperationResult:
        # Pointer only.
        if isinstance(operand, IR.LiteralValue):
            raise SemanticError("Cannot dereference a literal.")

        op_ty = self.__space[operand.type_id]
        if isinstance(op_ty, ty.PointerType):
            return OperationResult(result_type=op_ty.pointee_type, method_type=None, lvalue=True)
            # raise SemanticError(f"Cannot dereference non-pointer type: {self.__space.get_name(operand.type_id)}")

        # overload
        method = self.__method_registry.trait_method_lookup(IntrinsicTrait.Deref, operand.type_id, "deref", [], self.assignable)
        if method is not None:
            method_ty = self.__space[method].expect_method()
            return_type = method_ty.return_type(self.__space.instantiate)
            return OperationResult(result_type=return_type, method_type=method, lvalue=True)

        raise SemanticError(
            f"Cannot dereference non-pointer type: {self.__space.get_name(operand.type_id)}"
        )

    def __handle_address_of(self, operand: IR.TypedValue) -> OperationResult:
        if not isinstance(operand, IR.Variable):
            raise SemanticError("Address-of operator requires a variable.")

        ptr_ty = self.__space.alloc_pointer(operand.type_id)
        return OperationResult(result_type=ptr_ty, method_type=None, lvalue=False)

    def __handle_bitnot(self, operand: IR.TypedValue) -> OperationResult:
        # Integer only
        if isinstance(operand, IR.LiteralValue):
            if isinstance(operand, IR.IntegerLiteral):
                self.__literal_assignable(TypeSpace.i32_id, operand)
                return OperationResult(result_type=TypeSpace.i32_id, method_type=None, lvalue=False)
            raise SemanticError(f"Unsupported literal for bitwise not: {operand}")

        op_ty = self.__space[operand.type_id]
        if isinstance(op_ty, ty.IntType):
            return OperationResult(result_type=operand.type_id, method_type=None, lvalue=False)

        # overload
        method = self.__method_registry.trait_method_lookup(IntrinsicTrait.BitNot, operand.type_id, "bit_not", [], self.assignable)
        if method is not None:
            method_ty = self.__space[method].expect_method()
            return_type = method_ty.return_type(self.__space.instantiate)
            return OperationResult(result_type=return_type, method_type=method, lvalue=False)

        raise SemanticError(f"Unsupported type for bitwise not: {self.__space.get_name(operand.type_id)}")

    def __handle_not(self, operand: IR.TypedValue) -> OperationResult:
        # Bool only
        if isinstance(operand, IR.LiteralValue):
            self.__literal_assignable(TypeSpace.bool_id, operand)  # Checks boolean literal
            return OperationResult(result_type=TypeSpace.bool_id, method_type=None, lvalue=False)

        if operand.type_id != TypeSpace.bool_id:
            raise SemanticError(f"Logical not requires bool, got {self.__space.get_name(operand.type_id)}")
        return OperationResult(result_type=TypeSpace.bool_id, method_type=None, lvalue=False)

    def __analyze_literal_types(self, literals: list[IR.LiteralValue]) -> TypeId:
        """
        Analyze a list of literals to determine a common type they can all conform to.

        1. If any literal has a suffix, use that type as target and ensure all literals can conform to it.
        2. If no suffixes, default to i32 for IntegerLiteral and f64 for FloatLiteral.
        """
        specific_ty: TypeId | None = None

        # First pass: check for suffixes
        for lit in literals:
            if isinstance(lit, IR.IntegerLiteral) and lit.suffix is not None:
                lit_ty = lit.type_id
                if specific_ty is not None and specific_ty != lit_ty:
                    raise YianTypeError(
                        f"Conflicting suffixes for integer literals: {self.__space.get_name(specific_ty)} and {self.__space.get_name(lit_ty)}."
                    )
                specific_ty = lit_ty
            elif isinstance(lit, IR.FloatLiteral) and lit.suffix is not None:
                lit_ty = lit.type_id
                if specific_ty is not None and specific_ty != lit_ty:
                    raise YianTypeError(
                        f"Conflicting suffixes for float literals: {self.__space.get_name(specific_ty)} and {self.__space.get_name(lit_ty)}."
                    )
                specific_ty = lit_ty

        # Second pass: assign types
        if specific_ty is not None:
            for lit in literals:
                self.__literal_assignable(specific_ty, lit)
            return specific_ty

        # No suffixes; assign default types
        # If float literal present, default to f64
        has_float = any(isinstance(lit, IR.FloatLiteral) for lit in literals)
        if has_float:
            for lit in literals:
                self.__literal_assignable(TypeSpace.f64_id, lit)
            return TypeSpace.f64_id
        # If integer literals present, default to i32
        has_int = any(isinstance(lit, IR.IntegerLiteral) for lit in literals)
        if has_int:
            for lit in literals:
                self.__literal_assignable(TypeSpace.i32_id, lit)
            return TypeSpace.i32_id
        # Other literal types (bool, char, str) do not need type assignment, check consistency
        first_ty: TypeId | None = None
        for lit in literals:
            lit_ty = lit.type_id
            if first_ty is None:
                first_ty = lit_ty
            elif first_ty != lit_ty:
                raise YianTypeError(
                    f"Conflicting literal types: {self.__space.get_name(first_ty)} and {self.__space.get_name(lit_ty)}."
                )
        assert first_ty is not None
        return first_ty
