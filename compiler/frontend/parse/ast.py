from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, auto
from typing import TypeAlias

from compiler.frontend.lex.token import CharLiteral, IntLiteral
from compiler.frontend.lex.token import Literal as LexLiteral
from compiler.frontend.lex.token import StrLiteral
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
    paths: list[Identifier]
    target: Identifier
    alias: Identifier | None


@dataclass
class Alias:
    span: SrcSpan
    name: Identifier
    generics: list[Identifier]
    target: ASTType


@dataclass
class VarInfo:
    span: SrcSpan
    var_type: ASTType
    name: Identifier


class AttrKind(Enum):
    Pub = auto()
    Static = auto()


@dataclass
class Attr:
    span: SrcSpan
    kind: AttrKind


@dataclass
class FuncDef:
    span: SrcSpan
    attrs: list[Attr]
    name: Identifier
    generics: list[Identifier]
    params: list[VarInfo]
    ret_type: ASTType | None
    body: Block


@dataclass
class FieldInfo:
    span: SrcSpan
    attrs: list[Attr]
    field_type: ASTType
    name: Identifier


@dataclass
class StructDef:
    span: SrcSpan
    attrs: list[Attr]
    name: Identifier
    generics: list[Identifier]
    fields: list[FieldInfo]


@dataclass
class VariantInfo:
    span: SrcSpan
    name: Identifier
    fields: list[VarInfo]


@dataclass
class EnumDef:
    span: SrcSpan
    attrs: list[Attr]
    name: Identifier
    generics: list[Identifier]
    variants: list[VariantInfo]


@dataclass
class Impl:
    span: SrcSpan
    generics: list[Identifier]
    target: ASTType
    trait: ASTType | None
    items: list[MethodDef]


@dataclass
class TraitDef:
    span: SrcSpan
    attrs: list[Attr]
    name: Identifier
    generics: list[Identifier]
    items: list[TraitItem]


@dataclass
class MethodDef:
    span: SrcSpan
    decl: MethodDecl
    body: Block


@dataclass
class MethodDecl:
    span: SrcSpan
    attrs: list[Attr]
    name: Identifier
    generics: list[Identifier]
    params: list[VarInfo]
    ret_type: ASTType | None


@dataclass
class Block:
    span: SrcSpan
    stmts: list[Stmt]


@dataclass
class VarDecl:
    span: SrcSpan
    var_type: ASTType
    name: Identifier
    init_expr: Expr | None


@dataclass
class GlobalVarDecl:
    span: SrcSpan
    attrs: list[Attr]
    var_type: ASTType
    name: Identifier
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
    elif_branches: list[tuple[Expr, Block]]
    else_branch: Block | None


@dataclass
class For:
    span: SrcSpan
    var_name: Identifier
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
    message: Expr | None


@dataclass
class Delete:
    span: SrcSpan
    target: Expr


@dataclass
class IntPattern:
    span: SrcSpan
    values: list[IntLiteral]


@dataclass
class CharPattern:
    span: SrcSpan
    values: list[CharLiteral]


@dataclass
class StrPattern:
    span: SrcSpan
    values: list[StrLiteral]


@dataclass
class EnumPattern:
    span: SrcSpan
    variants: list[Identifier]


@dataclass
class PayloadPattern:
    span: SrcSpan
    variant: Identifier
    fields: list[Identifier]


@dataclass
class WildcardPattern:
    span: SrcSpan


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
    named_args: dict[Identifier, Expr]


@dataclass
class MethodCall:
    span: SrcSpan
    receiver: Expr
    method_name: Identifier
    generics: list[ASTType]
    args: list[Expr]


@dataclass
class FieldAccess:
    span: SrcSpan
    receiver: Expr
    field_name: Identifier


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
    name: Identifier
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
    | GlobalVarDecl
)


TraitItem: TypeAlias = (
    MethodDecl | MethodDef
)


Expr: TypeAlias = (
    Binary | Unary | FieldAccess
    | Call | MethodCall
    | DynValue | DynBuffer
    | TypeItem | Identifier | Literal
    | Tuple | Array
)


Stmt: TypeAlias = (
    Block
    | VarDecl
    | If | For | While | Loop | Match
    | Return | Break | Continue | Assert
    | Delete
    | Expr
)

Pattern: TypeAlias = (
    IntPattern | CharPattern | EnumPattern
    | StrPattern
    | PayloadPattern
    | WildcardPattern
)
