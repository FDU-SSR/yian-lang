"""
AST with semantic information, used for type checking and code generation.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TypeAlias

from compiler.analysis.ty import ty as Type
from compiler.frontend.parse.operator import BinaryOperator, UnaryOperator
from compiler.utils.IR.position import SrcSpan


@dataclass
class Block:
    span: SrcSpan
    stmts: list[Stmt]


@dataclass
class Return:
    span: SrcSpan
    value: Expr | None


@dataclass
class If:
    span: SrcSpan
    cond: Expr
    then_branch: Block
    else_branch: Block | None


@dataclass
class Loop:
    span: SrcSpan
    body: Block


@dataclass
class Break:
    span: SrcSpan


@dataclass
class Continue:
    span: SrcSpan


@dataclass
class Panic:
    span: SrcSpan
    message: Expr


@dataclass
class Delete:
    span: SrcSpan
    target: Expr


@dataclass
class Switch:
    """Switch for integer types."""
    span: SrcSpan
    value: Expr
    arms: list[SwitchArm]


@dataclass
class SwitchArm:
    span: SrcSpan
    pattern: int | None  # None means the default case
    int_width: int  # in bytes
    body: Block


@dataclass
class Match:
    """Match for types that are not integers, but support `==`."""
    span: SrcSpan
    value: Expr
    arms: list[MatchArm]


@dataclass
class MatchArm:
    span: SrcSpan
    pattern: Expr | None  # None means the default case
    body: Block


@dataclass
class EnumMatch:
    """Match for enum types."""
    span: SrcSpan
    value: Expr
    arms: list[EnumMatchArm]


@dataclass
class EnumMatchArm:
    span: SrcSpan
    variant: Type.EnumVariant | None  # None means the default case
    unpack_fields: list[str] | None  # None means not unpacking
    body: Block


@dataclass
class Binary:
    span: SrcSpan
    op: BinaryOperator
    left: Expr
    right: Expr


@dataclass
class Unary:
    span: SrcSpan
    op: UnaryOperator
    operand: Expr


@dataclass
class Call:
    """Including both function calls and static method calls."""
    span: SrcSpan
    func: int  # type_id
    args: list[Expr]


@dataclass
class StructConstruct:
    span: SrcSpan
    struct_id: int  # type_id
    field_values: dict[str, Expr]


@dataclass
class Invoke:
    span: SrcSpan
    callable: Expr
    args: list[Expr]


@dataclass
class Cast:
    span: SrcSpan
    value: Expr
    target_type: int  # type_id


@dataclass
class MethodCall:
    span: SrcSpan
    receiver: Expr
    method_id: int  # type_id of the method
    args: list[Expr]


@dataclass
class VariantConstruct:
    span: SrcSpan
    enum_id: int  # type_id of the enum
    variant: Type.EnumVariant
    args: dict[str, Expr] | None  # None means no payload


@dataclass
class FieldAccess:
    span: SrcSpan
    receiver: Expr
    field: Type.StructField


@dataclass
class DynValue:
    span: SrcSpan
    value: Expr


@dataclass
class DynBuffer:
    span: SrcSpan
    type_id: int  # type_id of the buffer element type
    length: Expr


@dataclass
class SizeOf:
    span: SrcSpan
    type_id: int  # type_id of the type to get size of


@dataclass
class BitCast:
    span: SrcSpan
    value: Expr
    target_type: int  # type_id


@dataclass
class Tuple:
    span: SrcSpan
    field_values: list[Expr]


@dataclass
class Array:
    span: SrcSpan
    element_type: int  # type_id
    elements: list[Expr]


@dataclass
class Var:
    span: SrcSpan
    symbol_id: int


@dataclass
class IntLiteral:
    span: SrcSpan
    value: int
    type_id: int  # type_id of the integer literal


@dataclass
class FloatLiteral:
    span: SrcSpan
    value: float
    type_id: int  # type_id of the float literal


@dataclass
class CharLiteral:
    span: SrcSpan
    value: str  # should be a single character


@dataclass
class StrLiteral:
    span: SrcSpan
    value: str


@dataclass
class BoolLiteral:
    span: SrcSpan
    value: bool


Literal: TypeAlias = IntLiteral | FloatLiteral | CharLiteral | StrLiteral | BoolLiteral


Expr: TypeAlias = (
    Binary | Unary
    | Call | StructConstruct | Invoke | Cast
    | MethodCall | VariantConstruct | FieldAccess
    | DynValue | DynBuffer
    | SizeOf | BitCast
    | Tuple | Array
    | Var | Literal
)


Stmt: TypeAlias = (
    Return | Break | Continue
    | If | Loop
    | Panic
    | Delete
    | Switch | Match | EnumMatch
    | Block
    | Expr
)
