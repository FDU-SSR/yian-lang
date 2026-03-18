"""
This module defines structures of `Checked GIR`, an intermediate representation used in the Yian compiler.
"""

from abc import abstractmethod
from typing import TYPE_CHECKING

from compiler.config.defs import StmtId, TypeId
from compiler.utils.errors import CompilerError

from . import gir as IR
from .meta import SrcPosition, StmtMetadata
from .operator import Operator
from .typed_value import TypedValue, Variable

if TYPE_CHECKING:
    from compiler.utils import ty


class CheckedGIR:
    """
    Base class for all statements.

    Attributes:
        stmt_metadata (StmtMetadata): Metadata for the statement.
        pos (SrcPosition): Source code position of the statement.
    """

    def __init__(self, metadata: StmtMetadata, pos: SrcPosition):
        self.metadata = metadata
        self.pos = pos

    @abstractmethod
    def __repr__(self) -> str:
        pass

    @property
    def stmt_id(self) -> StmtId:
        """
        A quick access to the statement ID.
        """
        return self.metadata.stmt_id

    def expect_var_decl(self) -> "VarDecl":
        if not isinstance(self, VarDecl):
            raise CompilerError(f"Expected VarDecl, got {self}")
        return self

    def expect_binary_op_assign(self) -> "BinaryOpAssign":
        if not isinstance(self, BinaryOpAssign):
            raise CompilerError(f"Expected BinaryOpAssign, got {self}")
        return self

    def expect_unary_op_assign(self) -> "UnaryOpAssign":
        if not isinstance(self, UnaryOpAssign):
            raise CompilerError(f"Expected UnaryOpAssign, got {self}")
        return self

    def expect_assign(self) -> "Assign":
        if not isinstance(self, Assign):
            raise CompilerError(f"Expected Assign, got {self}")
        return self

    def expect_return(self) -> "Return":
        if not isinstance(self, Return):
            raise CompilerError(f"Expected Return, got {self}")
        return self

    def expect_func_call(self) -> "FuncCall":
        if not isinstance(self, FuncCall):
            raise CompilerError(f"Expected FuncCall, got {self}")
        return self

    def expect_struct_construct(self) -> "StructConstruct":
        if not isinstance(self, StructConstruct):
            raise CompilerError(f"Expected StructConstruct, got {self}")
        return self

    def expect_invoke(self) -> "Invoke":
        if not isinstance(self, Invoke):
            raise CompilerError(f"Expected Invoke, got {self}")
        return self

    def expect_cast(self) -> "Cast":
        if not isinstance(self, Cast):
            raise CompilerError(f"Expected Cast, got {self}")
        return self

    def expect_method_call(self) -> "MethodCall":
        if not isinstance(self, MethodCall):
            raise CompilerError(f"Expected MethodCall, got {self}")
        return self

    def expect_static_method_call(self) -> "StaticMethodCall":
        if not isinstance(self, StaticMethodCall):
            raise CompilerError(f"Expected StaticMethodCall, got {self}")
        return self

    def expect_variant_construct(self) -> "VariantConstruct":
        if not isinstance(self, VariantConstruct):
            raise CompilerError(f"Expected VariantConstruct, got {self}")
        return self

    def expect_if(self) -> "If":
        if not isinstance(self, If):
            raise CompilerError(f"Expected If, got {self}")
        return self

    def expect_for(self) -> "For":
        if not isinstance(self, For):
            raise CompilerError(f"Expected For, got {self}")
        return self

    def expect_match(self) -> "Match":
        if not isinstance(self, Match):
            raise CompilerError(f"Expected Match, got {self}")
        return self

    def expect_block(self) -> "Block":
        if not isinstance(self, Block):
            raise CompilerError(f"Expected Block, got {self}")
        return self

    def expect_break(self) -> "Break":
        if not isinstance(self, Break):
            raise CompilerError(f"Expected Break, got {self}")
        return self

    def expect_continue(self) -> "Continue":
        if not isinstance(self, Continue):
            raise CompilerError(f"Expected Continue, got {self}")
        return self

    def expect_assert(self) -> "Assert":
        if not isinstance(self, Assert):
            raise CompilerError(f"Expected Assert, got {self}")
        return self

    def expect_delete(self) -> "Delete":
        if not isinstance(self, Delete):
            raise CompilerError(f"Expected Delete, got {self}")
        return self

    def expect_dyn_type(self) -> "DynType":
        if not isinstance(self, DynType):
            raise CompilerError(f"Expected DynType, got {self}")
        return self

    def expect_dyn_value(self) -> "DynValue":
        if not isinstance(self, DynValue):
            raise CompilerError(f"Expected DynValue, got {self}")
        return self

    def expect_dyn_array(self) -> "DynArray":
        if not isinstance(self, DynArray):
            raise CompilerError(f"Expected DynArray, got {self}")
        return self

    def expect_default_case(self) -> "DefaultCase":
        if not isinstance(self, DefaultCase):
            raise CompilerError(f"Expected DefaultCase, got {self}")
        return self


class VarDecl(CheckedGIR):
    """
    Represents a local variable declaration.

    Attributes:
        var (Variable): The variable being declared.
    """

    def __init__(
        self,
        stmt_metadata: StmtMetadata,
        pos: SrcPosition,
        var: Variable,
    ):
        super().__init__(stmt_metadata, pos)
        self.var = var

    def __repr__(self) -> str:
        return f"{self.stmt_id}: let {self.var}"

    @classmethod
    def from_gir(cls, gir_stmt: IR.VariableDeclStmt, var: Variable) -> 'VarDecl':
        return cls(
            gir_stmt.metadata,
            gir_stmt.pos,
            var,
        )


class BinaryOpAssign(CheckedGIR):
    """
    Represents a binary operation assignment.

    Attributes:
        target (Variable): The target variable.
        operator (Operator): The operator used.
        lhs (TypedValue): The left-hand side operand.
        rhs (TypedValue): The right-hand side operand.
    """

    def __init__(
        self,
        stmt_metadata: StmtMetadata,
        pos: SrcPosition,
        target: Variable,
        operator: Operator,
        lhs: TypedValue,
        rhs: TypedValue,
    ):
        super().__init__(stmt_metadata, pos)
        self.target = target
        self.operator = operator
        self.lhs = lhs
        self.rhs = rhs

    def __repr__(self) -> str:
        if self.operator == Operator.Index:
            return f"{self.stmt_id}: {self.target} <- {self.lhs}[{self.rhs}]"
        else:
            return (f"{self.stmt_id}: {self.target} <- {self.lhs} {self.operator} {self.rhs}")

    @classmethod
    def from_gir(
        cls,
        gir_stmt: IR.AssignStmt,
        target: Variable,
        lhs: TypedValue,
        rhs: TypedValue,
    ) -> 'BinaryOpAssign':
        if gir_stmt.operator is None:
            raise CompilerError(f"Expected binary operator in AssignStmt, got {gir_stmt}")
        return cls(
            gir_stmt.metadata,
            gir_stmt.pos,
            target,
            gir_stmt.operator,
            lhs,
            rhs,
        )


class UnaryOpAssign(CheckedGIR):
    """
    Represents a unary operation assignment.

    Attributes:
        target (Variable): The target variable.
        operator (Operator): The operator used.
        operand (TypedValue): The operand.
    """

    def __init__(
        self,
        stmt_metadata: StmtMetadata,
        pos: SrcPosition,
        target: Variable,
        operator: Operator,
        operand: TypedValue,
    ):
        super().__init__(stmt_metadata, pos)
        self.target = target
        self.operator = operator
        self.operand = operand

    def __repr__(self) -> str:
        return (f"{self.stmt_id}: {self.target} <- {self.operator}{self.operand}")

    @classmethod
    def from_gir(
        cls,
        gir_stmt: IR.AssignStmt,
        target: Variable,
        operand: TypedValue,
    ) -> 'UnaryOpAssign':
        if gir_stmt.operator is None:
            raise CompilerError(f"Expected unary operator in AssignStmt, got {gir_stmt}")
        return cls(
            gir_stmt.metadata,
            gir_stmt.pos,
            target,
            gir_stmt.operator,
            operand,
        )


class Assign(CheckedGIR):
    """
    Represents a simple assignment.

    Attributes:
        target (Variable): The target variable.
        value (TypedValue): The value being assigned.
    """

    def __init__(
        self,
        stmt_metadata: StmtMetadata,
        pos: SrcPosition,
        target: Variable,
        value: TypedValue,
    ):
        super().__init__(stmt_metadata, pos)
        self.target = target
        self.value = value

    def __repr__(self) -> str:
        return (f"{self.stmt_id}: {self.target} <- {self.value}")

    @classmethod
    def from_gir(
        cls,
        gir_stmt: IR.AssignStmt,
        target: Variable,
        value: TypedValue,
    ) -> 'Assign':
        return cls(
            gir_stmt.metadata,
            gir_stmt.pos,
            target,
            value,
        )


class Return(CheckedGIR):
    """
    Represents a return statement.

    Attributes:
        value (TypedValue | None): The return value, if any.
    """

    def __init__(
        self,
        stmt_metadata: StmtMetadata,
        pos: SrcPosition,
        value: TypedValue | None,
    ):
        super().__init__(stmt_metadata, pos)
        self.value = value

    def __repr__(self) -> str:
        if self.value is not None:
            return f"{self.stmt_id}: return {self.value}"
        else:
            return f"{self.stmt_id}: return"

    @classmethod
    def from_gir(
        cls,
        gir_stmt: IR.ReturnStmt,
        value: TypedValue | None,
    ) -> 'Return':
        return cls(
            gir_stmt.metadata,
            gir_stmt.pos,
            value,
        )


class FuncCall(CheckedGIR):
    """
    Represents a function call.

    Attributes:
        func (TypeId): The type ID of the function being called.
        target (Variable | None): The variable to store the result, if any.
        arguments (list[TypedValue]): Arguments passed to the function.
    """

    def __init__(
        self,
        stmt_metadata: StmtMetadata,
        pos: SrcPosition,
        func: TypeId,
        func_name: str,
        target: Variable | None,
        arguments: list[TypedValue],
    ):
        super().__init__(stmt_metadata, pos)
        self.func = func
        self.func_name = func_name
        self.target = target
        self.arguments = arguments

    def __repr__(self) -> str:
        if self.target is not None:
            return f"{self.stmt_id}: {self.target} <- call {self.func_name}({', '.join(repr(arg) for arg in self.arguments)})"
        else:
            return f"{self.stmt_id}: call {self.func_name}({', '.join(repr(arg) for arg in self.arguments)})"

    @classmethod
    def from_gir(
        cls,
        gir_stmt: IR.CallStmt,
        func: TypeId,
        target: Variable | None,
        arguments: list[TypedValue],
    ) -> 'FuncCall':
        return cls(
            gir_stmt.metadata,
            gir_stmt.pos,
            func,
            gir_stmt.name,
            target,
            arguments,
        )


class StructConstruct(CheckedGIR):
    """
    Represents a struct construction.

    Attributes:
        struct_type (TypeId): The type ID of the struct.
        target (Variable): The variable to store the constructed struct.
        field_values (dict[str, TypedValue]): Values for the struct fields.
    """

    def __init__(
        self,
        stmt_metadata: StmtMetadata,
        pos: SrcPosition,
        struct_type: TypeId,
        struct_name: str,
        target: Variable,
        field_values: dict[str, TypedValue],
    ):
        super().__init__(stmt_metadata, pos)
        self.struct_type = struct_type
        self.struct_name = struct_name
        self.target = target
        self.field_values = field_values

    def __repr__(self) -> str:
        return f"{self.stmt_id}: {self.target} <- construct {self.struct_name}({', '.join(f'{field}={value}' for field, value in self.field_values.items())})"

    @classmethod
    def from_gir(
        cls,
        gir_stmt: IR.CallStmt,
        struct_type: TypeId,
        target: Variable,
        field_values: dict[str, TypedValue],
    ) -> 'StructConstruct':
        return cls(
            gir_stmt.metadata,
            gir_stmt.pos,
            struct_type,
            gir_stmt.name,
            target,
            field_values,
        )


class Invoke(CheckedGIR):
    """
    Represents an invocation of a callable (e.g. function pointer).

    Attributes:
        invoked (Variable): The variable being invoked.
        target (Variable | None): The variable to store the result, if any.
        arguments (list[TypedValue]): Arguments passed to the invocation.
    """

    def __init__(
        self,
        stmt_metadata: StmtMetadata,
        pos: SrcPosition,
        invoked: Variable,
        target: Variable | None,
        arguments: list[TypedValue],
    ):
        super().__init__(stmt_metadata, pos)
        self.invoked = invoked
        self.target = target
        self.arguments = arguments

    def __repr__(self) -> str:
        return f"{self.stmt_id}: {self.target} <- invoke {self.invoked}({', '.join(repr(arg) for arg in self.arguments)})"

    @classmethod
    def from_gir(
        cls,
        gir_stmt: IR.CallStmt,
        invoked: Variable,
        target: Variable | None,
        arguments: list[TypedValue],
    ) -> 'Invoke':
        return cls(
            gir_stmt.metadata,
            gir_stmt.pos,
            invoked,
            target,
            arguments,
        )


class Cast(CheckedGIR):
    """
    Represents a type cast.

    Attributes:
        target (Variable): The variable to store the cast result.
        value (TypedValue): The value being cast.
        to_type (TypeId): The target type ID.
    """

    def __init__(
        self,
        stmt_metadata: StmtMetadata,
        pos: SrcPosition,
        target: Variable,
        value: TypedValue,
        to_type: TypeId,
        to_type_name: str,
    ):
        super().__init__(stmt_metadata, pos)
        self.target = target
        self.value = value
        self.to_type = to_type
        self.to_type_name = to_type_name

    def __repr__(self) -> str:
        return f"{self.stmt_id}: {self.target} <- cast {self.to_type_name}({self.value})"

    @classmethod
    def from_gir(
        cls,
        gir_stmt: IR.CallStmt,
        target: Variable,
        value: TypedValue,
        to_type: TypeId,
    ) -> 'Cast':
        return cls(
            gir_stmt.metadata,
            gir_stmt.pos,
            target,
            value,
            to_type,
            gir_stmt.name,
        )


class MethodCall(CheckedGIR):
    """
    Represents a method call.

    Attributes:
        method (TypeId): The type ID of the method being called.
        generic_args (list[TypeId] | None): Generic arguments for the call.
        target (Variable | None): The variable to store the result, if any.
        receiver (TypedValue): The receiver object.
        arguments (list[TypedValue]): Arguments passed to the method.
    """

    def __init__(
        self,
        stmt_metadata: StmtMetadata,
        pos: SrcPosition,
        method: TypeId,
        method_name: str,
        target: Variable | None,
        receiver: TypedValue,
        arguments: list[TypedValue],
    ):
        super().__init__(stmt_metadata, pos)
        self.method = method
        self.method_name = method_name
        self.target = target
        self.receiver = receiver
        self.arguments = arguments

    def __repr__(self) -> str:
        if self.target is not None:
            return f"{self.stmt_id}: {self.target} <- call {self.receiver}.{self.method_name}({', '.join(repr(arg) for arg in self.arguments)})"
        else:
            return f"{self.stmt_id}: call {self.receiver}.{self.method_name}({', '.join(repr(arg) for arg in self.arguments)})"

    @classmethod
    def from_gir(
        cls,
        gir_stmt: IR.CallStmt,
        method: TypeId,
        target: Variable | None,
        receiver: TypedValue,
        arguments: list[TypedValue],
    ) -> 'MethodCall':
        return cls(
            gir_stmt.metadata,
            gir_stmt.pos,
            method,
            gir_stmt.name,
            target,
            receiver,
            arguments,
        )


class StaticMethodCall(CheckedGIR):
    """
    Represents a static method call.

    Attributes:
        method (TypeId): The type ID of the method being called.
        generic_args (list[TypeId] | None): Generic arguments for the call.
        target (Variable | None): The variable to store the result, if any.
        arguments (list[TypedValue]): Arguments passed to the method.
    """

    def __init__(
        self,
        stmt_metadata: StmtMetadata,
        pos: SrcPosition,
        receiver_name: str,
        method: TypeId,
        method_name: str,
        target: Variable | None,
        arguments: list[TypedValue],
    ):
        super().__init__(stmt_metadata, pos)
        self.receiver_name = receiver_name
        self.method = method
        self.method_name = method_name
        self.target = target
        self.arguments = arguments

    def __repr__(self) -> str:
        if self.target is not None:
            return f"{self.stmt_id}: {self.target} <- call {self.receiver_name}::{self.method_name}({', '.join(repr(arg) for arg in self.arguments)})"
        else:
            return f"{self.stmt_id}: call {self.receiver_name}::{self.method_name}({', '.join(repr(arg) for arg in self.arguments)})"

    @classmethod
    def from_gir(
        cls,
        gir_stmt: IR.CallStmt,
        method: TypeId,
        target: Variable | None,
        arguments: list[TypedValue],
    ) -> 'StaticMethodCall':
        assert gir_stmt.receiver is not None
        return cls(
            gir_stmt.metadata,
            gir_stmt.pos,
            gir_stmt.receiver,
            method,
            gir_stmt.name,
            target,
            arguments,
        )


class VariantConstruct(CheckedGIR):
    """
    Represents a variant construction.

    Attributes:
        enum_type (TypeId): The type ID of the enum.
        variant (ty.EnumVariant): The variant of the enum.
        target (Variable): The variable to store the constructed variant.
        field_values (dict[str, TypedValue] | None): Values for the variant fields, if any.
    """

    def __init__(
        self,
        stmt_metadata: StmtMetadata,
        pos: SrcPosition,
        enum_type: TypeId,
        enum_name: str,
        variant: "ty.EnumVariant",
        target: Variable,
        field_values: dict[str, TypedValue] | None,
    ):
        super().__init__(stmt_metadata, pos)
        self.enum_type = enum_type
        self.enum_name = enum_name
        self.variant = variant
        self.target = target
        self.field_values = field_values

    def __repr__(self) -> str:
        if self.field_values is not None:
            field_values_str = ', '.join(f'{field}={value}' for field, value in self.field_values.items())
            return f"{self.stmt_id}: {self.target} <- construct {self.enum_name}::{self.variant.name}({field_values_str})"
        else:
            return f"{self.stmt_id}: {self.target} <- construct {self.enum_name}::{self.variant.name}"

    @classmethod
    def from_gir(
        cls,
        gir_stmt: IR.CallStmt | IR.AssignStmt,
        enum_type: TypeId,
        variant: "ty.EnumVariant",
        target: Variable,
        field_values: dict[str, TypedValue] | None,
    ) -> 'VariantConstruct':
        if isinstance(gir_stmt, IR.CallStmt):
            assert gir_stmt.receiver is not None
            enum_name = gir_stmt.receiver
        else:
            enum_name = gir_stmt.lhs
        return cls(
            gir_stmt.metadata,
            gir_stmt.pos,
            enum_type,
            enum_name,
            variant,
            target,
            field_values,
        )


class If(CheckedGIR):
    """
    Represents an if statement.

    Attributes:
        condition (TypedValue): The condition expression.
        then_body (StmtId): The statement ID of the 'then' block.
        else_body (StmtId | None): The statement ID of the 'else' block, if any.
    """

    def __init__(
        self,
        stmt_metadata: StmtMetadata,
        pos: SrcPosition,
        condition: TypedValue,
        then_body: StmtId,
        else_body: StmtId | None,
    ):
        super().__init__(stmt_metadata, pos)
        self.condition = condition
        self.then_body = then_body
        self.else_body = else_body

    def __repr__(self) -> str:
        if self.else_body is not None:
            return f"{self.stmt_id}: if {self.condition} then {self.then_body} else {self.else_body}"
        else:
            return f"{self.stmt_id}: if {self.condition} then {self.then_body}"

    @classmethod
    def from_gir(
        cls,
        gir_stmt: IR.IfStmt,
        condition: TypedValue,
    ) -> 'If':
        return cls(
            gir_stmt.metadata,
            gir_stmt.pos,
            condition,
            gir_stmt.then_body,
            gir_stmt.else_body,
        )


class For(CheckedGIR):
    """
    Represents a for loop.

    Attributes:
        init_body (StmtId | None): The statement ID of the initialization block.
        condition_prebody (StmtId | None): The statement ID of the pre-condition block.
        condition (TypedValue): The condition expression.
        body (StmtId): The statement ID of the loop body.
        update_body (StmtId | None): The statement ID of the update block.
    """

    def __init__(
        self,
        stmt_metadata: StmtMetadata,
        pos: SrcPosition,
        init_body: StmtId | None,
        condition_prebody: StmtId | None,
        condition: TypedValue,
        body: StmtId,
        update_body: StmtId | None,
    ):
        super().__init__(stmt_metadata, pos)
        self.init_body = init_body
        self.condition_prebody = condition_prebody
        self.condition = condition
        self.body = body
        self.update_body = update_body

    def __repr__(self) -> str:
        parts: list[str] = []
        if self.init_body is not None:
            parts.append(f"init {self.init_body}")
        if self.condition_prebody is not None:
            parts.append(f"cond_pre {self.condition_prebody}")
        parts.append(f"cond {self.condition}")
        parts.append(f"body {self.body}")
        if self.update_body is not None:
            parts.append(f"update {self.update_body}")
        return f"{self.stmt_id}: for ({', '.join(parts)})"

    @classmethod
    def from_gir(
        cls,
        gir_stmt: IR.ForStmt,
        condition: TypedValue,
    ) -> 'For':
        return cls(
            gir_stmt.metadata,
            gir_stmt.pos,
            gir_stmt.init_body,
            gir_stmt.condition_prebody,
            condition,
            gir_stmt.body,
            gir_stmt.update_body,
        )


class Loop(CheckedGIR):
    """
    Represents a loop statement.

    Attributes:
        body (StmtId): The statement ID of the loop body.
    """

    def __init__(
        self,
        stmt_metadata: StmtMetadata,
        pos: SrcPosition,
        body: StmtId,
    ):
        super().__init__(stmt_metadata, pos)
        self.body = body

    def __repr__(self) -> str:
        return f"{self.stmt_id}: loop {self.body}"

    @classmethod
    def from_gir(
        cls,
        gir_stmt: IR.LoopStmt,
    ) -> 'Loop':
        return cls(
            gir_stmt.metadata,
            gir_stmt.pos,
            gir_stmt.body,
        )


class Match(CheckedGIR):
    """
    Represents a match statement.

    Attributes:
        match_value (TypedValue): The value being matched.
        has_default (bool): Whether there is a default case.
        body (StmtId): The statement ID of the match body.
    """

    def __init__(
        self,
        stmt_metadata: StmtMetadata,
        pos: SrcPosition,
        match_value: TypedValue,
        has_default: bool,
        body: StmtId,
    ):
        super().__init__(stmt_metadata, pos)
        self.match_value = match_value
        self.has_default = has_default
        self.body = body

    def __repr__(self) -> str:
        return f"{self.stmt_id}: match {self.match_value} {{ {self.body} }}"

    @classmethod
    def from_gir(
        cls,
        gir_stmt: IR.SwitchStmt,
        match_value: TypedValue,
        has_default: bool,
    ) -> 'Match':
        return cls(
            gir_stmt.metadata,
            gir_stmt.pos,
            match_value,
            has_default,
            gir_stmt.body,
        )


class Block(CheckedGIR):
    """
    Represents a block of statements.

    Attributes:
        statements (list[StmtId]): The list of statement IDs in the block.
    """

    def __init__(
        self,
        stmt_metadata: StmtMetadata,
        pos: SrcPosition,
        statements: list[StmtId],
    ):
        super().__init__(stmt_metadata, pos)
        self.statements = statements

    def __repr__(self) -> str:
        return f"{self.stmt_id}: block {{ {', '.join(str(stmt) for stmt in self.statements)} }}"

    @classmethod
    def from_gir(
        cls,
        gir_stmt: IR.BlockStmt,
    ) -> 'Block':
        return cls(
            gir_stmt.metadata,
            gir_stmt.pos,
            [],
        )


class Break(CheckedGIR):
    """
    Represents a break statement.
    """

    def __init__(
        self,
        stmt_metadata: StmtMetadata,
        pos: SrcPosition,
    ):
        super().__init__(stmt_metadata, pos)

    def __repr__(self) -> str:
        return f"{self.stmt_id}: break"

    @classmethod
    def from_gir(
        cls,
        gir_stmt: IR.BreakStmt,
    ) -> 'Break':
        return cls(
            gir_stmt.metadata,
            gir_stmt.pos,
        )


class Continue(CheckedGIR):
    """
    Represents a continue statement.
    """

    def __init__(
        self,
        stmt_metadata: StmtMetadata,
        pos: SrcPosition,
    ):
        super().__init__(stmt_metadata, pos)

    def __repr__(self) -> str:
        return f"{self.stmt_id}: continue"

    @classmethod
    def from_gir(
        cls,
        gir_stmt: IR.ContinueStmt,
    ) -> 'Continue':
        return cls(
            gir_stmt.metadata,
            gir_stmt.pos,
        )


class Assert(CheckedGIR):
    """
    Represents an assert statement.

    Attributes:
        condition (TypedValue): The condition expression.
        message (TypedValue): The assertion message.
    """

    def __init__(
        self,
        stmt_metadata: StmtMetadata,
        pos: SrcPosition,
        condition: TypedValue,
        message: TypedValue,
    ):
        super().__init__(stmt_metadata, pos)
        self.condition = condition
        self.message = message

    def __repr__(self) -> str:
        return f"{self.stmt_id}: assert {self.condition} : {self.message}"

    @classmethod
    def from_gir(
        cls,
        gir_stmt: IR.AssertStmt,
        condition: TypedValue,
        message: TypedValue,
    ) -> 'Assert':
        return cls(

            gir_stmt.metadata,
            gir_stmt.pos,
            condition,
            message,
        )


class Delete(CheckedGIR):
    """
    Represents a delete statement.

    Attributes:
        target (TypedValue): The target to be deleted.
    """

    def __init__(
        self,
        stmt_metadata: StmtMetadata,
        pos: SrcPosition,
        target: TypedValue,
    ):
        super().__init__(stmt_metadata, pos)
        self.target = target

    def __repr__(self) -> str:
        return f"{self.stmt_id}: delete {self.target}"

    @classmethod
    def from_gir(
        cls,
        gir_stmt: IR.DeleteStmt,
        target: TypedValue,
    ) -> 'Delete':
        return cls(
            gir_stmt.metadata,
            gir_stmt.pos,
            target,
        )


class DynType(CheckedGIR):
    """
    Represents a dynamic type check/cast.

    Attributes:
        target (Variable): The target variable.
        data_type (TypeId): The type ID.
    """

    def __init__(
        self,
        stmt_metadata: StmtMetadata,
        pos: SrcPosition,
        target: Variable,
        data_type: TypeId,
        data_type_name: str,
    ):
        super().__init__(stmt_metadata, pos)
        self.target = target
        self.data_type = data_type
        self.data_type_name = data_type_name

    def __repr__(self) -> str:
        return f"{self.stmt_id}: {self.target} <- dyn {self.data_type_name}"

    @classmethod
    def from_gir(
        cls,
        gir_stmt: IR.NewObjectStmt,
        target: Variable,
        data_type: TypeId,
    ) -> 'DynType':
        assert gir_stmt.data_type is not None
        return cls(
            gir_stmt.metadata,
            gir_stmt.pos,
            target,
            data_type,
            gir_stmt.data_type,
        )


class DynValue(CheckedGIR):
    """
    Represents a dynamic value creation.

    Attributes:
        target (Variable): The target variable.
        value (TypedValue): The value.
    """

    def __init__(
        self,
        stmt_metadata: StmtMetadata,
        pos: SrcPosition,
        target: Variable,
        value: TypedValue,
    ):
        super().__init__(stmt_metadata, pos)
        self.target = target
        self.value = value

    def __repr__(self) -> str:
        return f"{self.stmt_id}: {self.target} <- dyn {self.value}"

    @classmethod
    def from_gir(
        cls,
        gir_stmt: IR.NewObjectStmt,
        target: Variable,
        value: TypedValue,
    ) -> 'DynValue':
        return cls(
            gir_stmt.metadata,
            gir_stmt.pos,
            target,
            value,
        )


class DynArray(CheckedGIR):
    """
    Represents a dynamic array creation.

    Attributes:
        target (Variable): The target variable.
        element_type (TypeId): The element type ID.
        length (TypedValue): The length of the array.
    """

    def __init__(
        self,
        stmt_metadata: StmtMetadata,
        pos: SrcPosition,
        target: Variable,
        element_type: TypeId,
        element_type_name: str,
        length: TypedValue,
    ):
        super().__init__(stmt_metadata, pos)
        self.target = target
        self.element_type = element_type
        self.element_type_name = element_type_name
        self.length = length

    def __repr__(self) -> str:
        return f"{self.stmt_id}: {self.target} <- dyn {self.element_type_name}[{self.length}]"

    @classmethod
    def from_gir(
        cls,
        gir_stmt: IR.NewArrayStmt,
        target: Variable,
        element_type: TypeId,
        length: TypedValue,
    ) -> 'DynArray':
        return cls(
            gir_stmt.metadata,
            gir_stmt.pos,
            target,
            element_type,
            gir_stmt.data_type,
            length,
        )


class IntCase(CheckedGIR):
    """
    Represents an integer case in a match statement.

    Attributes:
        case_values (list[int]): The case values.
        body (StmtId): The statement ID of the case body.
    """

    def __init__(
        self,
        stmt_metadata: StmtMetadata,
        pos: SrcPosition,
        case_values: list[int],
        body: StmtId,
    ):
        super().__init__(stmt_metadata, pos)
        self.case_values = case_values
        self.body = body

    def __repr__(self) -> str:
        return f"{self.stmt_id}: case {self.case_values} {{ {self.body} }}"

    @classmethod
    def from_gir(
        cls,
        gir_stmt: IR.CaseStmt,
        case_values: list[int],
    ) -> 'IntCase':
        return cls(
            gir_stmt.metadata,
            gir_stmt.pos,
            case_values,
            gir_stmt.body,
        )


class CharCase(CheckedGIR):
    """
    Represents a character case in a match statement.

    Attributes:
        case_values (list[str]): The case values.
        body (StmtId): The statement ID of the case body.
    """

    def __init__(
        self,
        stmt_metadata: StmtMetadata,
        pos: SrcPosition,
        case_values: list[str],
        body: StmtId,
    ):
        super().__init__(stmt_metadata, pos)
        self.case_values = case_values
        self.body = body

    def __repr__(self) -> str:
        return f"{self.stmt_id}: case {self.case_values} {{ {self.body} }}"

    @classmethod
    def from_gir(
        cls,
        gir_stmt: IR.CaseStmt,
        case_values: list[str],
    ) -> 'CharCase':
        return cls(
            gir_stmt.metadata,
            gir_stmt.pos,
            case_values,
            gir_stmt.body,
        )


class EnumCase(CheckedGIR):
    """
    Represents an enum case in a match statement.

    Attributes:
        case_values (list[str]): The case values.
        body (StmtId): The statement ID of the case body.
    """

    def __init__(
        self,
        stmt_metadata: StmtMetadata,
        pos: SrcPosition,
        case_values: list[str],
        body: StmtId,
    ):
        super().__init__(stmt_metadata, pos)
        self.case_values = case_values
        self.body = body

    def __repr__(self) -> str:
        return f"{self.stmt_id}: case {self.case_values} {{ {self.body} }}"

    @classmethod
    def from_gir(
        cls,
        gir_stmt: IR.CaseStmt,
        case_values: list[str],
    ) -> 'EnumCase':
        return cls(
            gir_stmt.metadata,
            gir_stmt.pos,
            case_values,
            gir_stmt.body,
        )


class EnumPayloadCase(CheckedGIR):
    """
    Represents an enum payload case in a match statement.

    Attributes:
        case_value (str): The case value.
        payload (Variable): The payload variable.
        body (StmtId): The statement ID of the case body.
    """

    def __init__(
        self,
        stmt_metadata: StmtMetadata,
        pos: SrcPosition,
        case_value: str,
        payload: Variable,
        body: StmtId,
    ):
        super().__init__(stmt_metadata, pos)
        self.case_value = case_value
        self.payload = payload
        self.body = body

    def __repr__(self) -> str:
        return f"{self.stmt_id}: case {self.case_value} as {self.payload} {{ {self.body} }}"

    @classmethod
    def from_gir(
        cls,
        gir_stmt: IR.CaseStmt,
        case_value: str,
        payload: Variable,
    ) -> 'EnumPayloadCase':
        return cls(
            gir_stmt.metadata,
            gir_stmt.pos,
            case_value,
            payload,
            gir_stmt.body,
        )


class DefaultCase(CheckedGIR):
    """
    Represents a default case in a match statement.

    Attributes:
        body (StmtId | None): The statement ID of the default body.
    """

    def __init__(
        self,
        stmt_metadata: StmtMetadata,
        pos: SrcPosition,
        body: StmtId | None,
    ):
        super().__init__(stmt_metadata, pos)
        self.body = body

    def __repr__(self) -> str:
        return f"{self.stmt_id}: default {{ {self.body} }}"

    @classmethod
    def from_gir(
        cls,
        gir_stmt: IR.DefaultStmt,
    ) -> 'DefaultCase':
        return cls(
            gir_stmt.metadata,
            gir_stmt.pos,
            gir_stmt.body,
        )


class Read(CheckedGIR):
    """
    Represents a read statement.

    Attributes:
        target (Variable): The target variable to store the read result.
        fd (TypedValue): The file descriptor to read from.
        buffer (Variable): The array variable used as read buffer.
    """

    def __init__(
        self,
        stmt_metadata: StmtMetadata,
        pos: SrcPosition,
        target: Variable,
        fd: TypedValue,
        buffer: Variable,
    ):
        super().__init__(stmt_metadata, pos)
        self.target = target
        self.fd = fd
        self.buffer = buffer

    def __repr__(self) -> str:
        return f"{self.stmt_id}: {self.target} <- read({self.fd}, {self.buffer})"

    @classmethod
    def from_gir(
        cls,
        gir_stmt: IR.CallStmt,
        target: Variable,
        fd: TypedValue,
        buffer: Variable,
    ) -> 'Read':
        return cls(
            gir_stmt.metadata,
            gir_stmt.pos,
            target,
            fd,
            buffer,
        )


class Open(CheckedGIR):
    """
    Represents an open statement.

    Attributes:
        target (Variable): The target variable to store the file descriptor.
        path (TypedValue): The file path (str) to open.
        flags (TypedValue): The flags for open mode (i32).
    """

    def __init__(
        self,
        stmt_metadata: StmtMetadata,
        pos: SrcPosition,
        target: Variable,
        path: TypedValue,
        flags: TypedValue,
    ):
        super().__init__(stmt_metadata, pos)
        self.target = target
        self.path = path
        self.flags = flags

    def __repr__(self) -> str:
        return f"{self.stmt_id}: {self.target} <- open({self.path}, {self.flags})"

    @classmethod
    def from_gir(
        cls,
        gir_stmt: IR.CallStmt,
        target: Variable,
        path: TypedValue,
        flags: TypedValue,
    ) -> 'Open':
        return cls(
            gir_stmt.metadata,
            gir_stmt.pos,
            target,
            path,
            flags,
        )


class Close(CheckedGIR):
    """
    Represents a close statement.

    Attributes:
        fd (TypedValue): The file descriptor to close.
    """

    def __init__(
        self,
        stmt_metadata: StmtMetadata,
        pos: SrcPosition,
        fd: TypedValue,
    ):
        super().__init__(stmt_metadata, pos)
        self.fd = fd

    def __repr__(self) -> str:
        return f"{self.stmt_id}: close({self.fd})"

    @classmethod
    def from_gir(
        cls,
        gir_stmt: IR.CallStmt,
        fd: TypedValue,
    ) -> 'Close':
        return cls(
            gir_stmt.metadata,
            gir_stmt.pos,
            fd,
        )


class Write(CheckedGIR):
    """
    Represents a write statement.

    Attributes:
        fd (TypedValue): The file descriptor to write to.
        value (TypedValue): The str value to write.
    """

    def __init__(
        self,
        stmt_metadata: StmtMetadata,
        pos: SrcPosition,
        fd: TypedValue,
        value: TypedValue,
    ):
        super().__init__(stmt_metadata, pos)
        self.fd = fd
        self.value = value

    def __repr__(self) -> str:
        return f"{self.stmt_id}: write({self.fd}, {self.value})"

    @classmethod
    def from_gir(
        cls,
        gir_stmt: IR.CallStmt,
        fd: TypedValue,
        value: TypedValue,
    ) -> 'Write':
        return cls(
            gir_stmt.metadata,
            gir_stmt.pos,
            fd,
            value,
        )


class Panic(CheckedGIR):
    """
    Represents a panic statement.

    Attributes:
        message (TypedValue): The panic message.
    """

    def __init__(
        self,
        stmt_metadata: StmtMetadata,
        pos: SrcPosition,
        message: TypedValue,
    ):
        super().__init__(stmt_metadata, pos)
        self.message = message

    def __repr__(self) -> str:
        return f"{self.stmt_id}: panic {self.message}"

    @classmethod
    def from_gir(
        cls,
        gir_stmt: IR.CallStmt,
        message: TypedValue,
    ) -> 'Panic':
        return cls(
            gir_stmt.metadata,
            gir_stmt.pos,
            message,
        )


class SizeOf(CheckedGIR):
    """
    Represents a sizeof expression.

    Attributes:
        target (Variable): The target variable.
        data_type (TypeId): The type ID to get the size of.
    """

    def __init__(
        self,
        stmt_metadata: StmtMetadata,
        pos: SrcPosition,
        target: Variable,
        data_type: TypeId,
        data_type_name: str,
    ):
        super().__init__(stmt_metadata, pos)
        self.target = target
        self.data_type = data_type
        self.data_type_name = data_type_name

    def __repr__(self) -> str:
        return f"{self.stmt_id}: {self.target} <- sizeof {self.data_type_name}"

    @classmethod
    def from_gir(
        cls,
        gir_stmt: IR.CallStmt,
        target: Variable,
        data_type: TypeId,
    ) -> 'SizeOf':
        return cls(
            gir_stmt.metadata,
            gir_stmt.pos,
            target,
            data_type,
            gir_stmt.positional_arguments[0],
        )


class BitCast(CheckedGIR):
    """
    Represents a bitcast operation.

    Attributes:
        target (Variable): The target variable.
        value (TypedValue): The value being bitcast.
        to_type (TypeId): The target type ID.
    """

    def __init__(
        self,
        stmt_metadata: StmtMetadata,
        pos: SrcPosition,
        target: Variable,
        value: TypedValue,
        to_type: TypeId,
        to_type_name: str,
    ):
        super().__init__(stmt_metadata, pos)
        self.target = target
        self.value = value
        self.to_type = to_type
        self.to_type_name = to_type_name

    def __repr__(self) -> str:
        return f"{self.stmt_id}: {self.target} <- bitcast {self.value} to {self.to_type_name}"

    @classmethod
    def from_gir(
        cls,
        gir_stmt: IR.CallStmt,
        target: Variable,
        value: TypedValue,
        to_type: TypeId,
    ) -> 'BitCast':
        return cls(
            gir_stmt.metadata,
            gir_stmt.pos,
            target,
            value,
            to_type,
            gir_stmt.type_arguments[0],
        )


class ByteOffset(CheckedGIR):
    """
    Represents a byte offset calculation.

    Attributes:
        target (Variable): The target variable.
        base (Variable): The base variable.
        offset (TypedValue): The offset value.
    """

    def __init__(
        self,
        stmt_metadata: StmtMetadata,
        pos: SrcPosition,
        target: Variable,
        base: Variable,
        offset: TypedValue,
    ):
        super().__init__(stmt_metadata, pos)
        self.target = target
        self.base = base
        self.offset = offset

    def __repr__(self) -> str:
        return f"{self.stmt_id}: {self.target} <- byte_offset {self.base} + {self.offset}"

    @classmethod
    def from_gir(
        cls,
        gir_stmt: IR.CallStmt,
        target: Variable,
        base: Variable,
        offset: TypedValue,
    ) -> 'ByteOffset':
        return cls(
            gir_stmt.metadata,
            gir_stmt.pos,
            target,
            base,
            offset,
        )


class MemCopy(CheckedGIR):
    """
    Represents a memory copy operation.

    Attributes:
        target (Variable): The target variable.
        source (Variable): The source variable.
        size (TypedValue): The size of the memory to copy.
    """

    def __init__(
        self,
        stmt_metadata: StmtMetadata,
        pos: SrcPosition,
        target: Variable,
        source: Variable,
        size: TypedValue,
    ):
        super().__init__(stmt_metadata, pos)
        self.target = target
        self.source = source
        self.size = size

    def __repr__(self) -> str:
        return f"{self.stmt_id}: memcpy {self.target} <- {self.source} for {self.size} bytes"

    @classmethod
    def from_gir(
        cls,
        gir_stmt: IR.CallStmt,
        target: Variable,
        source: Variable,
        size: TypedValue,
    ) -> 'MemCopy':
        return cls(
            gir_stmt.metadata,
            gir_stmt.pos,
            target,
            source,
            size,
        )


class FieldAccess(CheckedGIR):
    """
    Represents a field access.

    Attributes:
        target (Variable): The target variable.
        receiver (Variable): The receiver object.
        field (ty.StructField): The field being accessed.
    """

    def __init__(
        self,
        stmt_metadata: StmtMetadata,
        pos: SrcPosition,
        target: Variable,
        receiver: Variable,
        field: "ty.StructField",
    ):
        super().__init__(stmt_metadata, pos)
        self.target = target
        self.receiver = receiver
        self.field = field

    def __repr__(self) -> str:
        return f"{self.stmt_id}: {self.target} <- {self.receiver}.{self.field.name}"

    @classmethod
    def from_gir(
        cls,
        gir_stmt: IR.AssignStmt,
        target: Variable,
        receiver: Variable,
        field: "ty.StructField",
    ) -> 'FieldAccess':
        return cls(
            gir_stmt.metadata,
            gir_stmt.pos,
            target,
            receiver,
            field,
        )
