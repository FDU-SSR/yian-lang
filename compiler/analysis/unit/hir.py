"""
AST with semantic information, used for type checking and code generation.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TypeAlias

from compiler.analysis.ty import ty as Type
from compiler.builtins import BuiltinKind
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
class ComptimeIf:
    span: SrcSpan
    cond: Expr
    then_branch: Block
    else_branch: Block
    type_id: int
    is_place: bool


@dataclass
class CompileConfig:
    span: SrcSpan
    name: str
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
class Defer:
    """Deferred action registered in the current lexical block."""
    span: SrcSpan
    action: Expr
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
    """Ordered, expression-valued structural pattern match."""

    span: SrcSpan
    value: Expr
    arms: list[MatchArm]
    type_id: int
    is_place: bool
    is_ref: bool = False


@dataclass
class MatchArm:
    span: SrcSpan
    pattern: Pattern
    guard: Expr | None
    body: Block


@dataclass
class EnumPattern:
    span: SrcSpan
    variant: Type.EnumVariant
    type_id: int
    fields: list[tuple[int, Pattern]] | None


@dataclass
class WildcardPattern:
    span: SrcSpan
    type_id: int


@dataclass
class LiteralPattern:
    span: SrcSpan
    type_id: int
    value: int | str | bool
    condition: Expr | None = None
    condition_symbol: int | None = None


@dataclass
class RangePattern:
    span: SrcSpan
    type_id: int
    lower: int
    upper: int


@dataclass
class BindPattern:
    span: SrcSpan
    type_id: int
    symbol_id: int
    inner: Pattern


@dataclass
class OrPattern:
    span: SrcSpan
    type_id: int
    alternatives: list[Pattern]


@dataclass
class StructPattern:
    span: SrcSpan
    type_id: int
    fields: list[tuple[int, Pattern]]


@dataclass
class TuplePattern:
    span: SrcSpan
    type_id: int
    elements: list[Pattern]


@dataclass
class SequencePattern:
    span: SrcSpan
    type_id: int
    prefix: list[Pattern]
    suffix: list[Pattern]
    rest: bool


Pattern: TypeAlias = (
    EnumPattern | WildcardPattern
    | LiteralPattern | RangePattern | BindPattern | OrPattern
    | StructPattern | TuplePattern | SequencePattern
)


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
class BitCast:
    """Type-directed representation cast inserted during coercion."""

    span: SrcSpan
    value: Expr
    target_type: int
    type_id: int
    is_place: bool


@dataclass
class TraitObjectCoerce:
    """Construct a trait-object handle from a concrete typed reference."""

    span: SrcSpan
    value: Expr
    concrete_type_id: int
    trait_type_id: int
    method_ids: list[int]
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
class TraitObjectMethodCall:
    """Dynamically dispatched call through a trait-object vtable."""

    span: SrcSpan
    receiver: Expr
    trait_type_id: int
    method_id: int
    slot_index: int
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
    element: Expr | None  # initializer; None only when element_type is a ZST
    type_id: int
    is_place: bool


@dataclass
class Builtin:
    """A type-checked compiler-provided instruction."""

    span: SrcSpan
    kind: BuiltinKind
    type_args: list[int]
    args: list[Expr]
    type_id: int
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
class Ty:
    span: SrcSpan
    type_id: int
    is_place: bool


@dataclass
class Closure:
    """Closure value — carries captured expressions.

    ``type_id`` is the ClosureType type_id.  The variable binding keeps
    ClosureType so the CallDispatcher recognises it as callable. CFG lowering
    maps it to the generated capture-environment struct.
    """

    span: SrcSpan
    type_id: int              # ClosureType type_id
    captures: dict[str, Expr]  # capture_name → HIR expression for the captured value
    is_place: bool


Literal: TypeAlias = IntLiteral | FloatLiteral | CharLiteral | StrLiteral | BoolLiteral


Expr: TypeAlias = (
    Binary | Unary
    | Call | StructConstruct | Invoke | Cast | BitCast | TraitObjectCoerce
    | MethodCall | TraitObjectMethodCall | VariantConstruct | FieldAccess | TupleAccess
    | ArrayAccess | SliceAccess
    | DynValue | DynBuffer | Builtin
    | Tuple | Array | ArrayRepeat
    | Var | Literal | Ty | CompileConfig | Closure
    | Block
    | Return | Break | Continue | Defer
    | If | ComptimeIf | Loop
    | Delete
    | Match
    | Semi
    | Let
)
