from __future__ import annotations

from dataclasses import dataclass, field
from typing import TypeAlias

from compiler.analysis.ty.ty import EnumVariant
from compiler.frontend.parse.operator import BinaryOperator, UnaryOperator

# ---------------------------------------------------------------------------
# Statements
# ---------------------------------------------------------------------------


@dataclass
class VarPtr:
    """Get pointer to a local variable"""
    result: Reg
    var_ref: VarRef


@dataclass
class Alloca:
    """Store a local variable in the stack"""
    result: Reg
    value: Value


@dataclass
class FieldPtr:
    """Given ptr to a struct/tuple, get pointer to a field of it"""
    result: Reg
    base: Value
    field_index: int


@dataclass
class ElementPtr:
    """Given ptr, get ptr + offset"""
    result: Reg
    base: Value
    offset: Value


@dataclass
class PtrDiff:
    """Get the offset between two pointers"""
    result: Reg
    lhs: Value
    rhs: Value


@dataclass
class Load:
    """Load value from pointer"""
    result: Reg
    ptr: Value


@dataclass
class Store:
    """Write value to a pointer"""
    ptr: Value
    value: Value


@dataclass
class Malloc:
    """Allocate memory on the heap"""
    result: Reg
    type_id: int
    size: Value


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
class ExtractValue:
    """Extract a field from a struct/tuple"""
    result: Reg
    base: Value
    field_index: int


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
class Invoke:
    """Invoke a callable value (function pointer, closure, etc.) with a return value"""
    result: Reg
    callee: Value
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
class SizeOf:
    """Get the size of a type in bytes"""
    result: Reg
    type_id: int


@dataclass
class AggregateConstruct:
    """Construct an aggregate value (struct or tuple) from field values."""
    result: Reg
    type_id: int
    fields: list[Value]


@dataclass
class ArrayConstruct:
    """Construct an array from element values."""
    result: Reg
    type_id: int
    elements: list[Value]


@dataclass
class VariantConstruct:
    """Construct an enum variant"""
    result: Reg
    enum_type: int
    variant: EnumVariant
    payload_fields: list[Value] | None  # None = no payload


@dataclass
class SysWrite:
    fd: Value
    buf: Value


@dataclass
class SysRead:
    result: Reg
    fd: Value
    buf: Value


@dataclass
class Open:
    result: Reg
    path: Value
    flags: Value


@dataclass
class Close:
    result: Reg
    fd: Value


@dataclass
class FuncPtr:
    """Create a function pointer from a function type."""
    result: Reg
    func_type_id: int


@dataclass
class Phi:
    """Phi node"""
    result: Reg
    incoming: list[tuple[Block, Value]]


Stmt: TypeAlias = (
    VarPtr | FieldPtr | ElementPtr | PtrDiff | Alloca | Malloc
    | Load | Store
    | Binary | Unary | ExtractValue | Delete
    | Call | Invoke
    | Cast | SizeOf | FuncPtr
    | AggregateConstruct | ArrayConstruct | VariantConstruct
    | SysWrite | SysRead | Open | Close
)

# ---------------------------------------------------------------------------
# Terminators
# ---------------------------------------------------------------------------


@dataclass
class Ret:
    """Return value from function."""
    value: Value


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
    is_ref: bool = False


@dataclass
class Panic:
    """Panic statement"""
    message: Value  # must be `str` type


Terminator: TypeAlias = Ret | Br | CondBr | Match | Panic

# ---------------------------------------------------------------------------
# Basic Data Structures
# ---------------------------------------------------------------------------


@dataclass
class Block:
    """Basic block"""
    label: str
    phis: list[Phi] = field(default_factory=list[Phi])
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
class CharPattern:
    """Char pattern"""
    value: CharLiteral


@dataclass
class EnumPattern:
    """Enum pattern"""
    variant: EnumVariant
    fields: list[VarRef] | None  # None = no payload


Pattern: TypeAlias = IntPattern | CharPattern | EnumPattern


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
    local_vars: dict[int, VarRef] = field(default_factory=dict[int, VarRef])  # symbol id -> VarRef for all local variables (including parameters)
    params: list[int] = field(default_factory=list[int])  # symbol ids of parameters, in order
