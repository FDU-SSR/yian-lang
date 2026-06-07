"""
CFG IR — a control-flow-graph intermediate representation between HIR and LLVM IR.

All variables and parameters are represented as pointers.  Pointer-producing
instructions (LocalPtr, FieldPtr, ElementPtr) compute addresses; value-producing
instructions (Lit, Load, Binary, …) compute actual data.  Store is the only
side-effecting non-terminator instruction.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TypeAlias

from compiler.frontend.parse.operator import BinaryOperator, UnaryOperator

# ---------------------------------------------------------------------------
# Top-level
# ---------------------------------------------------------------------------


@dataclass
class Param:
    name: str
    type_id: int


@dataclass
class Function:
    name: str
    params: list[Param]
    return_type: int            # YIAN type_id, void_id for procedures
    blocks: list[Block]
    entry: str                  # label of the first block


# ---------------------------------------------------------------------------
# Pointer-producing instructions  (result is a pointer)
# ---------------------------------------------------------------------------

@dataclass
class LocalPtr:
    """Address of a local variable:  %r = localptr x : T"""
    result: str
    var_name: str
    type_id: int                # T (not T*)


@dataclass
class FieldPtr:
    """Address of a struct field:  %r = fieldptr %base.field_name"""
    result: str
    base: str                   # pointer to struct
    field_name: str
    struct_type: int            # type_id of the struct


@dataclass
class ElementPtr:
    """Address of an array / tuple element:  %r = elementptr %base[%index]"""
    result: str
    base: str                   # pointer to array or tuple
    index: str                  # SSA value (i64) or integer literal
    element_type: int           # type_id of the element


# ---------------------------------------------------------------------------
# Value-producing instructions  (result is a value)
# ---------------------------------------------------------------------------

@dataclass
class Lit:
    """Literal constant:  %r = 42 : i32"""
    result: str
    value: int | float | bool
    type_id: int


@dataclass
class Load:
    """Read from a pointer:  %r = load T, %ptr"""
    result: str
    ptr: str
    type_id: int                # pointee type T


@dataclass
class Binary:
    """Binary operation:  %r = add i32 %lhs, %rhs"""
    result: str
    op: BinaryOperator
    lhs: str
    rhs: str
    type_id: int


@dataclass
class Unary:
    """Unary operation:  %r = neg i32 %x"""
    result: str
    op: UnaryOperator
    operand: str
    type_id: int


@dataclass
class Call:
    """Function call:  %r = call @callee(arg1, arg2)   (result=None for void)"""
    result: str | None
    callee: str                 # function name
    args: list[str]             # SSA value names
    type_id: int


@dataclass
class StructConstruct:
    """Struct literal:  %r = Point { x: %v1, y: %v2 }"""
    result: str
    struct_type: int
    fields: list[tuple[str, str]]   # [(field_name, ssa_value)]


@dataclass
class VariantConstruct:
    """Enum variant construction:  %r = Option::Some { val: %v }"""
    result: str
    enum_type: int
    variant_name: str
    payload: list[tuple[str, str]] | None   # None = no payload


@dataclass
class Cast:
    """Type cast:  %r = cast %v to i64"""
    result: str
    value: str
    target_type: int
    source_type: int


@dataclass
class Phi:
    """Phi node:  %r = φ [(%v1, block1), (%v2, block2)]"""
    result: str
    incoming: list[tuple[str, str]]     # [(ssa_value, block_label)]
    type_id: int


# ---------------------------------------------------------------------------
# Side-effecting instruction  (no result)
# ---------------------------------------------------------------------------

@dataclass
class Store:
    """Write to a pointer:  store %val, %ptr"""
    ptr: str
    value: str


# ---------------------------------------------------------------------------
# Terminators
# ---------------------------------------------------------------------------

@dataclass
class Ret:
    """Return from function."""
    value: str | None           # None = void return


@dataclass
class Br:
    """Unconditional branch."""
    target: str                 # block label


@dataclass
class CondBr:
    """Conditional branch:  br %cond, then, else"""
    cond: str
    then_block: str
    else_block: str


@dataclass
class Switch:
    """Multi-way branch on integer discriminant."""
    value: str
    default_block: str | None   # None = exhaustive (no default needed)
    cases: list[tuple[int, str]]  # [(discriminant, block_label)]


@dataclass
class Unreachable:
    """Marks an unreachable code path."""


# ---------------------------------------------------------------------------
# Type aliases
# ---------------------------------------------------------------------------

Stmt: TypeAlias = (
    LocalPtr | FieldPtr | ElementPtr
    | Lit | Load | Binary | Unary | Call
    | StructConstruct | VariantConstruct | Cast | Phi
    | Store
)

Terminator: TypeAlias = Ret | Br | CondBr | Switch | Unreachable


# ---------------------------------------------------------------------------
# Basic block
# ---------------------------------------------------------------------------

@dataclass
class Block:
    label: str
    stmts: list[Stmt] = field(default_factory=list[Stmt])
    terminator: Terminator | None = None   # None until the block is sealed
