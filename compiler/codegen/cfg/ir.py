from __future__ import annotations

from dataclasses import dataclass, field
from typing import TypeAlias

from compiler.analysis.ty.ty import EnumVariant
from compiler.frontend.parse.operator import BinaryOperator, UnaryOperator

# ---------------------------------------------------------------------------
# Top Level
# ---------------------------------------------------------------------------


@dataclass
class Function:
    """Functions and methods are all lowwered to this node."""
    name: str
    type_id: int  # type id of the function/method
    blocks: list[Block]  # all basic blocks in the function/method
    entry: Block  # entry block of the function/method, also included in `blocks`

# ---------------------------------------------------------------------------
# Statements
# ---------------------------------------------------------------------------


@dataclass
class VarPtr:
    """Get pointer to a local variable"""
    result: Reg
    var_ref: VarRef


@dataclass
class FieldPtr:
    """Given ptr to a struct/tuple, get pointer to a field of it"""
    result: Reg
    base: Reg
    field_index: int


@dataclass
class ElementPtr:
    """Given ptr to an array, get pointer to an element of it"""
    result: Reg
    base: Reg
    index: Value


@dataclass
class Load:
    """Load value from pointer"""
    result: Reg
    ptr: Reg


@dataclass
class Store:
    """Write value to a pointer"""
    ptr: Reg
    value: Value


@dataclass
class Binary:
    """Binary operation"""
    result: Reg
    op: BinaryOperator
    lhs: Value
    rhs: Value


@dataclass
class Unary:
    """Unary operation"""
    result: Reg
    op: UnaryOperator
    operand: Value


@dataclass
class Delete:
    """Delete a pointer"""
    ptr: Value


@dataclass
class Call:
    """Call with a return value"""
    result: Reg
    callee_type: int  # type id of the function/method
    args: list[Value]


@dataclass
class VoidCall:
    """Call without a return value"""
    callee_type: int  # type id of the function/method
    args: list[Value]


@dataclass
class Cast:
    """Cast a value to a different type

    - integer/char <=> integer/char
    - integer/float <=> integer/float
    - pointer <=> pointer
    """
    result: Reg
    value: Value
    to_type: int


@dataclass
class StructConstruct:
    """Construct a struct"""
    result: Reg
    struct_type: int
    fields: list[Value]


@dataclass
class VariantConstruct:
    """Construct an enum variant"""
    result: Reg
    enum_type: int
    variant: EnumVariant
    payload_fields: list[Value] | None  # None = no payload


@dataclass
class Phi:
    """Phi node"""
    result: Reg
    incoming: list[tuple[Block, Value]]


Stmt: TypeAlias = (
    VarPtr | FieldPtr | ElementPtr
    | Load | Store
    | Binary | Unary | Delete
    | Call | VoidCall | Cast | StructConstruct | VariantConstruct
    | Phi
)

# ---------------------------------------------------------------------------
# Terminators
# ---------------------------------------------------------------------------


@dataclass
class Ret:
    """Return value from function."""
    value: Value


@dataclass
class RetVoid:
    """Return void from function."""


@dataclass
class Br:
    """Unconditional branch."""
    target: Block


@dataclass
class CondBr:
    """Conditional branch:  br %cond, then, else"""
    cond: Value  # must be bool
    then_block: Block
    else_block: Block


@dataclass
class Match:
    """Match statement"""
    value: Value  # must be integer/char/enum
    arms: list[MatchArm]
    default: Block | None


@dataclass
class Panic:
    """Panic statement"""
    message: Value  # must be `str` type


Terminator: TypeAlias = Ret | RetVoid | Br | CondBr | Match | Panic

# ---------------------------------------------------------------------------
# Basic Data Structures
# ---------------------------------------------------------------------------


@dataclass
class Block:
    """Basic block"""
    label: str
    stmts: list[Stmt] = field(default_factory=list[Stmt])
    terminator: Terminator | None = None


@dataclass
class VarRef:
    """Reference to a local variable"""
    name: str
    symbol_id: int
    type_id: int


@dataclass
class Reg:
    """SSA register"""
    name: str
    type_id: int


@dataclass
class IntLiteral:
    """Integer literal"""
    value: int
    type_id: int


@dataclass
class FloatLiteral:
    """Float literal"""
    value: float
    type_id: int


@dataclass
class BoolLiteral:
    """Bool literal"""
    value: bool
    type_id: int


@dataclass
class CharLiteral:
    """Char literal"""
    value: str
    type_id: int


@dataclass
class StringLiteral:
    """String literal"""
    value: str
    type_id: int


Literal: TypeAlias = IntLiteral | FloatLiteral | BoolLiteral | CharLiteral | StringLiteral

Value: TypeAlias = Literal | Reg

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


@dataclass
class MatchArm:
    """Match arm"""
    pattern: Pattern
    body: Block


@dataclass
class IntPattern:
    """Integer pattern"""
    value: IntLiteral


@dataclass
class EnumPattern:
    """Enum pattern"""
    variant: EnumVariant
    fields: list[VarRef] | None  # None = no payload


Pattern: TypeAlias = IntPattern | EnumPattern
