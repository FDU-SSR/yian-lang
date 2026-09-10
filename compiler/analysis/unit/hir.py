"""
AST with semantic information, used for type checking and code generation.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TypeAlias

from compiler.analysis.ty import ty as Type
from compiler.frontend.lex.position import SrcSpan
from compiler.frontend.parse.operator import BinaryOperator, UnaryOperator


@dataclass
class Block:
    span: SrcSpan
    stmts: list[Expr]
    type_id: int
    is_place: bool


@dataclass
class Return:
    span: SrcSpan
    value: Expr | None
    type_id: int
    is_place: bool


@dataclass
class If:
    span: SrcSpan
    cond: Expr
    then_branch: Block
    else_branch: Block | None
    type_id: int
    is_place: bool


@dataclass
class Loop:
    span: SrcSpan
    body: Block
    type_id: int
    is_place: bool


@dataclass
class Break:
    span: SrcSpan
    type_id: int
    is_place: bool
    value: Expr | None = None


@dataclass
class Continue:
    span: SrcSpan
    type_id: int
    is_place: bool


@dataclass
class Panic:
    span: SrcSpan
    message: Expr
    type_id: int
    is_place: bool


@dataclass
class SysWrite:
    span: SrcSpan
    fd: Expr
    buf: Expr
    type_id: int
    is_place: bool


@dataclass
class Delete:
    span: SrcSpan
    target: Expr
    type_id: int
    is_place: bool


@dataclass
class Semi:
    """Expression statement: ``expr;`` — discards value, type is void."""
    span: SrcSpan
    expr: Expr
    type_id: int
    is_place: bool


@dataclass
class Let:
    """Let declaration expression — always returns void."""
    span: SrcSpan
    init: Expr | None
    type_id: int
    is_place: bool
    symbol_id: int | None = None


@dataclass
class Match:
    """
    Low-level match covering:

    1. Integer patterns (integer types, char via ord)
    2. Char patterns
    3. Enum patterns — C-like (no payload) or with payload unpacking
    """

    span: SrcSpan
    value: Expr
    arms: list[MatchArm]
    type_id: int
    is_place: bool
    is_ref: bool = False


@dataclass
class MatchArm:
    span: SrcSpan
    pattern: Pattern | None  # None means the default/wildcard case
    body: Block


@dataclass
class IntPattern:
    span: SrcSpan
    value: int
    type_id: int  # type_id of the matched integer


@dataclass
class CharPattern:
    span: SrcSpan
    value: str


@dataclass
class EnumPattern:
    span: SrcSpan
    variant: Type.EnumVariant
    unpack_fields: list[int] | None  # symbol ids of unpacked variables, None means not unpacking


Pattern: TypeAlias = IntPattern | CharPattern | EnumPattern


@dataclass
class Binary:
    span: SrcSpan
    op: BinaryOperator
    left: Expr
    right: Expr
    type_id: int
    is_place: bool


@dataclass
class Unary:
    span: SrcSpan
    op: UnaryOperator
    operand: Expr
    type_id: int
    is_place: bool


@dataclass
class Call:
    """Including both function calls and static method calls."""
    span: SrcSpan
    func: int  # type_id
    args: list[Expr]
    type_id: int
    is_place: bool


@dataclass
class StructConstruct:
    span: SrcSpan
    struct_id: int  # type_id
    field_values: dict[str, Expr]
    type_id: int
    is_place: bool


@dataclass
class Invoke:
    span: SrcSpan
    callable: Expr
    args: list[Expr]
    type_id: int
    is_place: bool


@dataclass
class Cast:
    span: SrcSpan
    value: Expr
    target_type: int  # type_id
    type_id: int
    is_place: bool


@dataclass
class MethodCall:
    span: SrcSpan
    receiver: Expr
    method_id: int  # type_id of the method
    args: list[Expr]
    type_id: int
    is_place: bool


@dataclass
class VariantConstruct:
    span: SrcSpan
    enum_id: int  # type_id of the enum
    variant: Type.EnumVariant
    args: dict[str, Expr] | None  # None means no payload
    type_id: int
    is_place: bool


@dataclass
class FieldAccess:
    span: SrcSpan
    receiver: Expr
    field: Type.StructField
    type_id: int
    is_place: bool


@dataclass
class TupleAccess:
    span: SrcSpan
    receiver: Expr
    index: int
    type_id: int
    is_place: bool


@dataclass
class ArrayAccess:
    """Access an element of a fixed-size array through an inline GEP."""

    span: SrcSpan
    array: Expr
    index: Expr
    element_type: int
    type_id: int
    length: int
    is_place: bool = True


@dataclass
class SliceAccess:
    """Access an element of a slice through its data pointer."""

    span: SrcSpan
    slice: Expr
    index: Expr
    element_type: int
    type_id: int
    is_place: bool = True


@dataclass
class DynValue:
    span: SrcSpan
    value: Expr
    type_id: int
    is_place: bool


@dataclass
class DynBuffer:
    span: SrcSpan
    element_type: int  # type_id of the buffer element type
    length: Expr
    type_id: int
    is_place: bool


@dataclass
class SizeOf:
    span: SrcSpan
    target_type: int  # type_id of the type to get size of
    type_id: int
    is_place: bool


@dataclass
class BitCast:
    span: SrcSpan
    value: Expr
    target_type: int  # type_id
    type_id: int
    is_place: bool


@dataclass
class SysRead:
    span: SrcSpan
    fd: Expr
    buf: Expr
    type_id: int
    is_place: bool


@dataclass
class Open:
    span: SrcSpan
    path: Expr   # str
    flags: Expr  # i32
    type_id: int  # i32
    is_place: bool


@dataclass
class Close:
    span: SrcSpan
    fd: Expr     # i32
    type_id: int  # i32
    is_place: bool


@dataclass
class YianArgc:
    span: SrcSpan
    type_id: int  # u64
    is_place: bool


@dataclass
class YianArgvPtr:
    span: SrcSpan
    index: Expr  # u64
    type_id: int  # *u8
    is_place: bool


@dataclass
class YianCstrlen:
    span: SrcSpan
    ptr: Expr    # *u8
    type_id: int  # u64
    is_place: bool


@dataclass
class YianExit:
    span: SrcSpan
    code: Expr   # i32
    type_id: int  # never
    is_place: bool


@dataclass
class Tuple:
    span: SrcSpan
    field_values: list[Expr]
    type_id: int
    is_place: bool


@dataclass
class Array:
    span: SrcSpan
    element_type: int  # type_id
    elements: list[Expr]
    type_id: int
    is_place: bool


@dataclass
class ArrayRepeat:
    """Array repeat literal ``[value; count]``.

    The repeat count is encoded in *type_id* → :class:`Type.ArrayType.length`
    so that both concrete (``LiteralValueType``) and generic
    (``ConstGenericType``) lengths work without a separate field.
    """

    span: SrcSpan
    element_type: int  # type_id
    element: Expr
    type_id: int
    is_place: bool


@dataclass
class Var:
    span: SrcSpan
    symbol_id: int
    type_id: int
    is_place: bool


@dataclass
class IntLiteral:
    span: SrcSpan
    value: int
    type_id: int
    is_place: bool


@dataclass
class FloatLiteral:
    span: SrcSpan
    value: float
    type_id: int  # type_id of the float literal
    is_place: bool


@dataclass
class CharLiteral:
    span: SrcSpan
    value: str  # should be a single character
    type_id: int
    is_place: bool


@dataclass
class StrLiteral:
    span: SrcSpan
    value: str
    type_id: int
    is_place: bool


@dataclass
class BoolLiteral:
    span: SrcSpan
    value: bool
    type_id: int
    is_place: bool


@dataclass
class AssumeInit:
    span: SrcSpan
    value: Expr
    type_id: int
    is_place: bool


@dataclass
class Ty:
    span: SrcSpan
    type_id: int
    is_place: bool


@dataclass
class Closure:
    """Closure value — carries captured expressions.

    ``type_id`` is the ClosureType type_id.  The variable binding keeps
    ClosureType so the CallDispatcher recognises it as callable.
    LLVM maps ClosureType to its struct_type_id for code generation.
    """

    span: SrcSpan
    type_id: int              # ClosureType type_id
    captures: dict[str, Expr]  # capture_name → HIR expression for the captured value
    is_place: bool


Literal: TypeAlias = IntLiteral | FloatLiteral | CharLiteral | StrLiteral | BoolLiteral


Expr: TypeAlias = (
    Binary | Unary
    | Call | StructConstruct | Invoke | Cast
    | MethodCall | VariantConstruct | FieldAccess | TupleAccess
    | ArrayAccess | SliceAccess
    | DynValue | DynBuffer
    | SizeOf | BitCast | SysRead | SysWrite | Open | Close
    | YianArgc | YianArgvPtr | YianCstrlen | YianExit
    | Tuple | Array | ArrayRepeat
    | Var | Literal | Ty | Closure
    | Block
    | Return | Break | Continue
    | If | Loop
    | Panic
    | Delete
    | Match
    | Semi
    | Let
    | AssumeInit
)
