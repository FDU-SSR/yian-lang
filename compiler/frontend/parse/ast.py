from __future__ import annotations

from dataclasses import dataclass
from typing import TypeAlias

from compiler.frontend.lex.token import Literal as LexLiteral
from compiler.frontend.parse.ast_type import ASTType
from compiler.frontend.parse.operator import BinaryOperator, UnaryOperator
from compiler.utils.IR.position import SrcSpan


@dataclass
class Program:
    span: SrcSpan
    items: list[ProgramItem]


@dataclass
class Import:
    span: SrcSpan
    paths: list[str]
    target: str
    alias: str | None


@dataclass
class Alias:
    span: SrcSpan
    name: str
    generics: list[str]
    target: ASTType


@dataclass
class VarInfo:
    span: SrcSpan
    var_type: ASTType
    name: str


@dataclass
class FuncDef:
    span: SrcSpan
    attrs: list[str]
    name: str
    params: list[VarInfo]
    ret_type: ASTType | None
    body: Block


@dataclass
class FieldInfo:
    span: SrcSpan
    is_pub: bool
    field_type: ASTType
    name: str


@dataclass
class StructDef:
    span: SrcSpan
    attrs: list[str]
    name: str
    generics: list[str]
    fields: list[FieldInfo]


@dataclass
class VariantInfo:
    span: SrcSpan
    name: str
    fields: list[VarInfo]


@dataclass
class EnumDef:
    span: SrcSpan
    attrs: list[str]
    name: str
    generics: list[str]
    variants: list[VariantInfo]


@dataclass
class Impl:
    span: SrcSpan
    generics: list[str]
    target: ASTType
    trait: ASTType | None
    items: list[MethodDef]


@dataclass
class TraitDef:
    span: SrcSpan
    attrs: list[str]
    name: str
    generics: list[str]
    items: list[TraitItem]


@dataclass
class MethodDef:
    span: SrcSpan
    attrs: list[str]
    name: str
    generics: list[str]
    params: list[VarInfo]
    ret_type: ASTType | None
    body: Block


@dataclass
class MethodDecl:
    span: SrcSpan
    attrs: list[str]
    name: str
    generics: list[str]
    params: list[VarInfo]
    ret_type: ASTType | None
    body: Block


@dataclass
class Block:
    span: SrcSpan
    stmts: list[Stmt]


@dataclass
class VarDecl:
    span: SrcSpan
    name: str
    var_type: ASTType
    init_expr: Expr | None


@dataclass
class Return:
    span: SrcSpan
    expr: Expr | None


@dataclass
class If:
    span: SrcSpan
    condition: Expr
    then_branch: Block
    else_branch: Block | None


@dataclass
class For:
    span: SrcSpan
    var_name: str
    iterable: Expr
    body: Block


@dataclass
class While:
    span: SrcSpan
    condition: Expr
    body: Block


@dataclass
class Loop:
    span: SrcSpan
    body: Block


@dataclass
class Match:
    span: SrcSpan
    expr: Expr
    arms: list[tuple[Pattern, Block]]


@dataclass
class Break:
    span: SrcSpan


@dataclass
class Continue:
    span: SrcSpan


@dataclass
class Assert:
    span: SrcSpan
    condition: Expr
    message: str | None


@dataclass
class Delete:
    span: SrcSpan
    target: Expr


@dataclass
class IntPattern:
    span: SrcSpan
    values: list[int]
    block: Block


@dataclass
class CharPattern:
    span: SrcSpan
    values: list[str]
    block: Block


@dataclass
class StrPattern:
    span: SrcSpan
    values: list[str]
    block: Block


@dataclass
class EnumPattern:
    span: SrcSpan
    variants: list[str]
    block: Block


@dataclass
class PayloadPattern:
    span: SrcSpan
    variant: str
    fields: list[str]
    block: Block


@dataclass
class WildcardPattern:
    span: SrcSpan
    block: Block


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
    span: SrcSpan
    callee: Expr
    positional_args: list[Expr]
    named_args: dict[str, Expr]


@dataclass
class MethodCall:
    span: SrcSpan
    receiver: Expr
    method_name: str
    generics: list[ASTType]
    args: list[Expr]


@dataclass
class FieldAccess:
    span: SrcSpan
    receiver: Expr
    field_name: str


@dataclass
class DynValue:
    span: SrcSpan
    value: Expr


@dataclass
class DynBuffer:
    span: SrcSpan
    target_type: ASTType
    size: Expr


@dataclass
class TypeItem:
    """
    Represents stuff like `Option<T>`, `i32[10]`, `Foo<i32*, String>`, etc.
    """

    span: SrcSpan
    name: str
    generics: list[ASTType]


@dataclass
class Tuple:
    span: SrcSpan
    elements: list[Expr]


@dataclass
class Array:
    span: SrcSpan
    elements: list[Expr]


@dataclass
class Identifier:
    span: SrcSpan
    name: str


@dataclass
class Literal:
    span: SrcSpan
    literal: LexLiteral


ProgramItem: TypeAlias = (
    Import
    | Alias
    | FuncDef
    | StructDef | EnumDef | TraitDef
    | Impl
    | VarDecl
)


TraitItem: TypeAlias = (
    MethodDecl | MethodDef
)


Stmt: TypeAlias = (
    Block
    | VarDecl
    | If | For | While | Loop | Match
    | Return | Break | Continue | Assert
    | Delete
)


Expr: TypeAlias = (
    Binary | Unary | FieldAccess
    | Call | MethodCall
    | DynValue | DynBuffer
    | TypeItem | Identifier | Literal
    | Tuple | Array
)


Pattern: TypeAlias = (
    IntPattern | CharPattern | EnumPattern
    | StrPattern
    | PayloadPattern
)
