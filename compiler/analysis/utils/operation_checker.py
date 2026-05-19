"""
This module manages type analysis utilities for operations.
"""

from dataclasses import dataclass
from typing import Callable

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

    def __assignable_check(self, target_type: TypeId, source_type: TypeId) -> None:
        if target_type != source_type:
            raise YianTypeError.mismatch(target_type, source_type, self.__space.get_name)

    def __variable_assignable_check(self, target_type: TypeId, source_value: IR.Variable) -> None:
        self.__assignable_check(target_type, source_value.type_id)

    def __literal_assignable_check(self, target_type: TypeId, source_value: IR.LiteralValue) -> None:
        """
        Check if a literal value can be assigned to a target type.

        If check passes, also set the type_id of the literal value to the target type (for later codegen use).
        If check fails, no side effect is performed on the literal value.
        """
        target_ty_def = self.__space[target_type]

        # Determine the type based on the suffix
        suffix = getattr(source_value, "suffix", None)
        if suffix is not None:
            assert isinstance(suffix, str)
            literal_type = TypeSpace.intrinsic_type(IntrinsicType.from_str(suffix))
            self.__assignable_check(target_type, literal_type)
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
                    self.__literal_assignable_check(target_ty_def.element_type, element)

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
                    self.__literal_assignable_check(element_type, element)

                source_value.type_id = target_type
                return

    def assignable_check(self, target_type: TypeId, source_value: IR.TypedValue) -> None:
        if isinstance(source_value, IR.Variable):
            self.__variable_assignable_check(target_type, source_value)
        elif isinstance(source_value, IR.LiteralValue):
            self.__literal_assignable_check(target_type, source_value)

    def assignable(self, target_type: TypeId, source_value: IR.TypedValue) -> bool:
        try:
            self.assignable_check(target_type, source_value)
            return True
        except YianTypeError:
            return False

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

    def __try_trait_method_result(self, trait: IntrinsicTrait, receiver_type: TypeId, method_name: str, args: list[IR.TypedValue], lvalue: bool) -> OperationResult | None:
        method = self.__method_registry.trait_method_lookup(trait, receiver_type, method_name, args, self.assignable_check)
        if method is None:
            return None

        method_ty = self.__space[method].expect_method()
        return_type = method_ty.return_type(self.__space.instantiate)
        return OperationResult(result_type=return_type, method_type=method, lvalue=lvalue)

    def __handle_add(self, left: IR.TypedValue, right: IR.TypedValue) -> OperationResult:
        # 1) numeric add(float/int)
        unified_type = self.__try_builtin_numeric_type(left, right)
        if unified_type is not None:
            return OperationResult(result_type=unified_type, method_type=None, lvalue=False)

        left_ty = self.__space[left.type_id]
        right_ty = self.__space[right.type_id]

        # 2) pointer + int
        if isinstance(left_ty, ty.PointerType) and self.assignable(TypeSpace.u64_id, right):
            return OperationResult(result_type=left.type_id, method_type=None, lvalue=False)

        if isinstance(right_ty, ty.PointerType) and self.assignable(TypeSpace.u64_id, left):
            return OperationResult(result_type=right.type_id, method_type=None, lvalue=False)

        # 3) overloaded add
        overload_result = self.__try_trait_method_result(IntrinsicTrait.Add, left.type_id, "add", [right], lvalue=False)
        if overload_result is not None:
            return overload_result

        raise SemanticError(f"Unsupported operand types for addition: {self.__space.get_name(left.type_id)} + {self.__space.get_name(right.type_id)}")

    def __handle_sub(self, left: IR.TypedValue, right: IR.TypedValue) -> OperationResult:
        # 1) numeric sub(float/int)
        unified_type = self.__try_builtin_numeric_type(left, right)
        if unified_type is not None:
            return OperationResult(result_type=unified_type, method_type=None, lvalue=False)

        left_ty = self.__space[left.type_id]
        right_ty = self.__space[right.type_id]

        # 2) pointer - int
        if isinstance(left_ty, ty.PointerType) and self.assignable(TypeSpace.u64_id, right):
            return OperationResult(result_type=left.type_id, method_type=None, lvalue=False)

        # 3) pointer - pointer
        if isinstance(left_ty, ty.PointerType) and isinstance(right_ty, ty.PointerType):
            if left_ty.pointee_type != right_ty.pointee_type:
                raise YianTypeError(
                    f"Pointer subtraction requires operands of the same pointer type, got "
                    f"'{self.__space.get_name(left.type_id)}' and '{self.__space.get_name(right.type_id)}'."
                )
            return OperationResult(result_type=TypeSpace.i64_id, method_type=None, lvalue=False)

        # 4) overloaded sub
        overload_result = self.__try_trait_method_result(IntrinsicTrait.Sub, left.type_id, "sub", [right], lvalue=False)
        if overload_result is not None:
            return overload_result

        raise SemanticError(f"Unsupported operand types for subtraction: {self.__space.get_name(left.type_id)} - {self.__space.get_name(right.type_id)}")

    def __handle_mul(self, left: IR.TypedValue, right: IR.TypedValue) -> OperationResult:
        # 1) numeric mul(float/int)
        unified_type = self.__try_builtin_numeric_type(left, right)
        if unified_type is not None:
            return OperationResult(result_type=unified_type, method_type=None, lvalue=False)

        # 2) overloaded mul
        overload_result = self.__try_trait_method_result(IntrinsicTrait.Mul, left.type_id, "mul", [right], lvalue=False)
        if overload_result is not None:
            return overload_result

        raise SemanticError(
            f"Unsupported operand types for multiplication: {self.__space.get_name(left.type_id)} * {self.__space.get_name(right.type_id)}"
        )

    def __handle_div(self, left: IR.TypedValue, right: IR.TypedValue) -> OperationResult:
        # 1) numeric div(float/int)
        unified_type = self.__try_builtin_numeric_type(left, right)
        if unified_type is not None:
            return OperationResult(result_type=unified_type, method_type=None, lvalue=False)

        # 2) overloaded div
        overload_result = self.__try_trait_method_result(IntrinsicTrait.Div, left.type_id, "div", [right], lvalue=False)
        if overload_result is not None:
            return overload_result

        raise SemanticError(
            f"Unsupported operand types for division: {self.__space.get_name(left.type_id)} / {self.__space.get_name(right.type_id)}"
        )

    def __handle_rem(self, left: IR.TypedValue, right: IR.TypedValue) -> OperationResult:
        # 1) integer rem
        unified_type = self.__try_builtin_integer_type(left, right)
        if unified_type is not None:
            return OperationResult(result_type=unified_type, method_type=None, lvalue=False)

        # 2) overloaded rem
        overload_result = self.__try_trait_method_result(IntrinsicTrait.Rem, left.type_id, "rem", [right], lvalue=False)
        if overload_result is not None:
            return overload_result

        raise SemanticError(
            f"Unsupported operand types for remainder: {self.__space.get_name(left.type_id)} % {self.__space.get_name(right.type_id)}"
        )

    def __handle_int_bitwise(self, op: IR.Operator, left: IR.TypedValue, right: IR.TypedValue, trait: IntrinsicTrait, method_name: str) -> OperationResult:
        # 1) builtin bitwise integer op
        unified_type = self.__try_builtin_integer_type(left, right)
        if unified_type is not None:
            return OperationResult(result_type=unified_type, method_type=None, lvalue=False)

        # 2) overloaded bitwise op
        overload_result = self.__try_trait_method_result(trait, left.type_id, method_name, [right], lvalue=False)
        if overload_result is not None:
            return overload_result

        raise SemanticError(
            f"Unsupported operand types for bitwise op {op}: {self.__space.get_name(left.type_id)}, {self.__space.get_name(right.type_id)}"
        )

    def __handle_bitand(self, left: IR.TypedValue, right: IR.TypedValue) -> OperationResult:
        return self.__handle_int_bitwise(IR.Operator.Ampersand, left, right, IntrinsicTrait.BitAnd, "bit_and")

    def __handle_bitor(self, left: IR.TypedValue, right: IR.TypedValue) -> OperationResult:
        return self.__handle_int_bitwise(IR.Operator.Pipe, left, right, IntrinsicTrait.BitOr, "bit_or")

    def __handle_bitxor(self, left: IR.TypedValue, right: IR.TypedValue) -> OperationResult:
        return self.__handle_int_bitwise(IR.Operator.Caret, left, right, IntrinsicTrait.BitXor, "bit_xor")

    def __handle_shift(self, op: IR.Operator, left: IR.TypedValue, right: IR.TypedValue, trait: IntrinsicTrait, method_name: str) -> OperationResult:
        left_ty = self.__space[left.type_id]

        # 1) builtin shift (only for integers, right operand must be integer)
        if isinstance(left_ty, ty.IntType):
            if isinstance(right, IR.LiteralValue):
                if not isinstance(right, IR.IntegerLiteral):
                    raise SemanticError(f"Unsupported right literal type for shift: {right}")
                # TODO: make sure the literal value is non-negative and within reasonable range
                self.__literal_assignable_check(TypeSpace.u64_id, right)
            else:
                right_ty = self.__space[right.type_id]
                if not isinstance(right_ty, ty.IntType):
                    raise YianTypeError(
                        f"Shift operator {op} requires right operand to be an integer type, got '{self.__space.get_name(right.type_id)}'."
                    )
            return OperationResult(result_type=left.type_id, method_type=None, lvalue=False)

        # 2) overloaded shift
        overload_result = self.__try_trait_method_result(trait, left.type_id, method_name, [right], lvalue=False)
        if overload_result is not None:
            return overload_result

        raise SemanticError(
            f"Unsupported operand types for shift op {op}: {self.__space.get_name(left.type_id)}, {self.__space.get_name(right.type_id)}"
        )

    def __handle_shl(self, left: IR.TypedValue, right: IR.TypedValue) -> OperationResult:
        return self.__handle_shift(IR.Operator.Shl, left, right, IntrinsicTrait.Shl, "shl")

    def __handle_shr(self, left: IR.TypedValue, right: IR.TypedValue) -> OperationResult:
        return self.__handle_shift(IR.Operator.Shr, left, right, IntrinsicTrait.Shr, "shr")

    def __cmp_builtin_type_ok(self, unified_type: TypeId, ordered: bool) -> bool:
        ty_def = self.__space[unified_type]
        if isinstance(ty_def, (ty.IntType, ty.FloatType, ty.PointerType)):
            return True
        if not ordered and isinstance(ty_def, (ty.BoolType, ty.CharType)):
            return True
        if not ordered and isinstance(ty_def, ty.EnumType) and ty_def.tag_only:
            return True
        return False

    def __handle_cmp(self, op: IR.Operator, left: IR.TypedValue, right: IR.TypedValue) -> OperationResult:
        # 1) builtin comparison
        unified_type = self.__type_unify(left, right)
        if unified_type is not None and self.__cmp_builtin_type_ok(unified_type, ordered=False):
            return OperationResult(result_type=TypeSpace.bool_id, method_type=None, lvalue=False)

        # 2) overload
        match op:
            case IR.Operator.Eq:
                method_name = "eq"
            case IR.Operator.Neq:
                method_name = "ne"
            case _:
                raise CompilerError(f"Unhandled comparison operator: {op}")
        overload_result = self.__try_trait_method_result(IntrinsicTrait.PartialEq, left.type_id, method_name, [right], lvalue=False)
        if overload_result is not None:
            return overload_result

        raise SemanticError(
            f"Unsupported operand types for comparison {op}: {self.__space.get_name(left.type_id)}, {self.__space.get_name(right.type_id)}"
        )

    def __handle_cmp_ordered(self, op: IR.Operator, left: IR.TypedValue, right: IR.TypedValue) -> OperationResult:
        # 1) builtin ordered comparison
        unified_type = self.__type_unify(left, right)
        if unified_type is not None and self.__cmp_builtin_type_ok(unified_type, ordered=True):
            return OperationResult(result_type=TypeSpace.bool_id, method_type=None, lvalue=False)

        # 2) overload
        match op:
            case IR.Operator.Gt:
                method_name = "gt"
            case IR.Operator.Lt:
                method_name = "lt"
            case IR.Operator.Ge:
                method_name = "ge"
            case IR.Operator.Le:
                method_name = "le"
            case _:
                raise CompilerError(f"Unhandled ordered comparison operator: {op}")

        overload_result = self.__try_trait_method_result(IntrinsicTrait.PartialOrd, left.type_id, method_name, [right], lvalue=False)
        if overload_result is not None:
            return overload_result

        raise SemanticError(
            f"Unsupported operand types for ordered comparison {op}: {self.__space.get_name(left.type_id)}, {self.__space.get_name(right.type_id)}"
        )

    def __handle_eq(self, left: IR.TypedValue, right: IR.TypedValue) -> OperationResult:
        return self.__handle_cmp(IR.Operator.Eq, left, right)

    def __handle_ne(self, left: IR.TypedValue, right: IR.TypedValue) -> OperationResult:
        return self.__handle_cmp(IR.Operator.Neq, left, right)

    def __handle_gt(self, left: IR.TypedValue, right: IR.TypedValue) -> OperationResult:
        return self.__handle_cmp_ordered(IR.Operator.Gt, left, right)

    def __handle_lt(self, left: IR.TypedValue, right: IR.TypedValue) -> OperationResult:
        return self.__handle_cmp_ordered(IR.Operator.Lt, left, right)

    def __handle_ge(self, left: IR.TypedValue, right: IR.TypedValue) -> OperationResult:
        return self.__handle_cmp_ordered(IR.Operator.Ge, left, right)

    def __handle_le(self, left: IR.TypedValue, right: IR.TypedValue) -> OperationResult:
        return self.__handle_cmp_ordered(IR.Operator.Le, left, right)

    def __handle_logic(self, op: IR.Operator, left: IR.TypedValue, right: IR.TypedValue) -> OperationResult:
        # Bool only
        if left.type_id != TypeSpace.bool_id:
            raise SemanticError(f"Logical operator {op} requires left operand to be bool, got {self.__space.get_name(left.type_id)}")
        if right.type_id != TypeSpace.bool_id:
            raise SemanticError(f"Logical operator {op} requires right operand to be bool, got {self.__space.get_name(right.type_id)}")

        return OperationResult(result_type=TypeSpace.bool_id, method_type=None, lvalue=False)

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
                self.__literal_assignable_check(TypeSpace.u64_id, right)
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
            self.__literal_assignable_check(TypeSpace.i32_id, right)

            elem_type = left_ty.element_types[index_val]
            return OperationResult(result_type=elem_type, method_type=None, lvalue=True)

        # 3) Index overload
        overload_result = self.__try_trait_method_result(IntrinsicTrait.Index, left_ty_id, "index", [right], lvalue=True)
        if overload_result is not None:
            return overload_result

        raise YianTypeError(f"Cannot apply index operator {IR.Operator.Index} to type '{self.__space.get_name(left_ty_id)}'.")

    def __handle_in(self, left: IR.TypedValue, right: IR.TypedValue) -> OperationResult:
        raise NotImplementedError("The 'in' operator is not implemented yet.")

    def __handle_not_in(self, left: IR.TypedValue, right: IR.TypedValue) -> OperationResult:
        return self.__handle_in(left, right)

    def __handle_range(self, left: IR.TypedValue, right: IR.TypedValue) -> OperationResult:
        # 1) numeric range construction
        target_type = self.__try_builtin_numeric_type(left, right)
        if target_type is not None:
            return_type = self.__space.alloc_range(target_type)
            return OperationResult(result_type=return_type, method_type=None, lvalue=False)

        raise SemanticError(
            f"Unsupported operand types for range: {self.__space.get_name(left.type_id)} .. {self.__space.get_name(right.type_id)}"
        )

    def __handle_pos(self, operand: IR.TypedValue) -> OperationResult:
        op_ty = self.__space[operand.type_id]
        if not isinstance(op_ty, (ty.IntType, ty.FloatType)):
            raise SemanticError(f"Unsupported operand type for unary +: {self.__space.get_name(operand.type_id)}")
        return OperationResult(result_type=operand.type_id, method_type=None, lvalue=False)

    def __handle_neg(self, operand: IR.TypedValue) -> OperationResult:
        op_ty = self.__space[operand.type_id]
        if isinstance(op_ty, (ty.IntType, ty.FloatType)):
            return OperationResult(result_type=operand.type_id, method_type=None, lvalue=False)

        # overload
        overload_result = self.__try_trait_method_result(IntrinsicTrait.Neg, operand.type_id, "neg", [], lvalue=False)
        if overload_result is not None:
            return overload_result

        raise SemanticError(f"Unsupported operand type for unary -: {self.__space.get_name(operand.type_id)}")

    def __handle_deref(self, operand: IR.TypedValue) -> OperationResult:
        # Pointer only.
        op_ty = self.__space[operand.type_id]
        if isinstance(op_ty, ty.PointerType):
            return OperationResult(result_type=op_ty.pointee_type, method_type=None, lvalue=True)

        # overload
        overload_result = self.__try_trait_method_result(IntrinsicTrait.Deref, operand.type_id, "deref", [], lvalue=True)
        if overload_result is not None:
            return overload_result

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
        op_ty = self.__space[operand.type_id]
        if isinstance(op_ty, ty.IntType):
            return OperationResult(result_type=operand.type_id, method_type=None, lvalue=False)

        # overload
        overload_result = self.__try_trait_method_result(IntrinsicTrait.BitNot, operand.type_id, "bit_not", [], lvalue=False)
        if overload_result is not None:
            return overload_result

        raise SemanticError(f"Unsupported type for bitwise not: {self.__space.get_name(operand.type_id)}")

    def __handle_not(self, operand: IR.TypedValue) -> OperationResult:
        # Bool only
        if operand.type_id == TypeSpace.bool_id:
            return OperationResult(result_type=TypeSpace.bool_id, method_type=None, lvalue=False)
        raise SemanticError(f"Logical not requires bool, got {self.__space.get_name(operand.type_id)}")

    def __type_unify(self, left: IR.TypedValue, right: IR.TypedValue) -> TypeId | None:
        """
        Unify the types of two values.

        1) If both are literals, analyze their types to find a common type.
        2) If one is a variable and the other is a literal, use the variable's type as the target and check if the literal can conform to it.
        3) If both are variables, their types must match exactly.

        If unification fails, return None to indicate incompatibility.
        """
        if isinstance(left, IR.LiteralValue) and isinstance(right, IR.LiteralValue):
            left_ty = left.get_determined_type_id()
            right_ty = right.get_determined_type_id()

            if left_ty is not None and right_ty is not None:
                return left_ty if left_ty == right_ty else None

            if left_ty is not None or right_ty is not None:
                target_ty = left_ty if left_ty is not None else right_ty
                assert target_ty is not None
                try:
                    if left_ty is None:
                        self.__literal_assignable_check(target_ty, left)
                    if right_ty is None:
                        self.__literal_assignable_check(target_ty, right)
                except (SemanticError, YianTypeError, CompilerError):
                    return None
                return target_ty

            if isinstance(left, IR.FloatLiteral) or isinstance(right, IR.FloatLiteral):
                try:
                    self.__literal_assignable_check(TypeSpace.f64_id, left)
                    self.__literal_assignable_check(TypeSpace.f64_id, right)
                except (SemanticError, YianTypeError, CompilerError):
                    return None
                return TypeSpace.f64_id

            if isinstance(left, IR.IntegerLiteral) or isinstance(right, IR.IntegerLiteral):
                try:
                    self.__literal_assignable_check(TypeSpace.i32_id, left)
                    self.__literal_assignable_check(TypeSpace.i32_id, right)
                except (SemanticError, YianTypeError, CompilerError):
                    return None
                return TypeSpace.i32_id

            try:
                return left.type_id if left.type_id == right.type_id else None
            except CompilerError:
                return None

        if isinstance(left, IR.Variable) and isinstance(right, IR.LiteralValue):
            try:
                self.__literal_assignable_check(left.type_id, right)
            except (SemanticError, YianTypeError, CompilerError):
                return None
            return left.type_id

        if isinstance(left, IR.LiteralValue) and isinstance(right, IR.Variable):
            try:
                self.__literal_assignable_check(right.type_id, left)
            except (SemanticError, YianTypeError, CompilerError):
                return None
            return right.type_id

        if isinstance(left, IR.Variable) and isinstance(right, IR.Variable):
            if left.type_id != right.type_id:
                return None
            return left.type_id

        raise CompilerError("Unhandled case in type unification.")

    def __try_builtin_unified_type(self, left: IR.TypedValue, right: IR.TypedValue, predicate: Callable[[object], bool]) -> TypeId | None:
        unified_type = self.__type_unify(left, right)
        if unified_type is None:
            return None

        if predicate(self.__space[unified_type]):
            return unified_type

        return None

    def __try_builtin_numeric_type(self, left: IR.TypedValue, right: IR.TypedValue) -> TypeId | None:
        return self.__try_builtin_unified_type(left, right, lambda ty_def: isinstance(ty_def, (ty.IntType, ty.FloatType)))

    def __try_builtin_integer_type(self, left: IR.TypedValue, right: IR.TypedValue) -> TypeId | None:
        return self.__try_builtin_unified_type(left, right, lambda ty_def: isinstance(ty_def, ty.IntType))
