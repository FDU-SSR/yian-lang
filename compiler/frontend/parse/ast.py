from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, TypeAlias

from compiler.frontend.parse.ast_export import export_program
from compiler.frontend.parse.operator import BinaryOperator, UnaryOperator

if TYPE_CHECKING:
    from compiler.frontend.lex.token import CharLiteral, IntLiteral
    from compiler.frontend.lex.token import Literal as LexLiteral
    from compiler.frontend.lex.token import StrLiteral
    from compiler.frontend.parse.ast_type import ASTType
    from compiler.utils.IR.position import SrcSpan


@dataclass
class Program:
    span: SrcSpan
    items: list[ProgramItem]

    def export(self) -> str:
        return export_program(self)


@dataclass
class Import:
    span: SrcSpan
    paths: list[Identifier]
    target: Identifier
    alias: Identifier | None

    def __repr__(self) -> str:
        paths_str = ".".join(path.name for path in self.paths)
        if self.alias:
            return f"from {paths_str} import {self.target.name} as {self.alias.name}"
        else:
            return f"from {paths_str} import {self.target.name}"


@dataclass
class Alias:
    span: SrcSpan
    attrs: list[Attr]
    name: Identifier
    generics: list[Identifier]
    target: ASTType

    def __repr__(self) -> str:
        generics_str = f"<{', '.join(gen.name for gen in self.generics)}>" if self.generics else ""
        return f"typedef {self.name.name}{generics_str} = {self.target}"


@dataclass
class VarInfo:
    span: SrcSpan
    var_type: ASTType
    name: Identifier

    def __repr__(self) -> str:
        return f"{self.var_type} {self.name.name}"


class AttrKind(Enum):
    Pub = "pub"
    Static = "static"


@dataclass
class Attr:
    span: SrcSpan
    kind: AttrKind

    def __repr__(self) -> str:
        return self.kind.value


@dataclass
class FuncDef:
    span: SrcSpan
    attrs: list[Attr]
    name: Identifier
    generics: list[Identifier]
    params: list[VarInfo]
    ret_type: ASTType | None
    body: Block

    def __repr__(self) -> str:
        attrs_str = " ".join(str(attr) for attr in self.attrs)
        generics_str = f"<{', '.join(gen.name for gen in self.generics)}>" if self.generics else ""
        params_str = ", ".join(str(param) for param in self.params)
        ret_type_str = f" -> {self.ret_type}" if self.ret_type else ""
        return f"{attrs_str} fn {self.name.name}{generics_str}({params_str}){ret_type_str}"


@dataclass
class FieldInfo:
    span: SrcSpan
    attrs: list[Attr]
    field_type: ASTType
    name: Identifier

    def __repr__(self) -> str:
        attrs_str = " ".join(str(attr) for attr in self.attrs)
        return f"{attrs_str} {self.field_type} {self.name.name}"


@dataclass
class StructDef:
    span: SrcSpan
    attrs: list[Attr]
    name: Identifier
    generics: list[Identifier]
    fields: list[FieldInfo]

    def __repr__(self) -> str:
        attrs_str = " ".join(str(attr) for attr in self.attrs)
        generics_str = f"<{', '.join(gen.name for gen in self.generics)}>" if self.generics else ""
        fields_str = ", ".join(str(field) for field in self.fields)
        return f"{attrs_str} struct {self.name.name}{generics_str} {{ {fields_str} }}"


@dataclass
class VariantInfo:
    span: SrcSpan
    name: Identifier
    fields: list[VarInfo]

    def __repr__(self) -> str:
        fields_str = ", ".join(str(field) for field in self.fields)
        return f"{self.name.name}({fields_str})" if self.fields else self.name.name


@dataclass
class EnumDef:
    span: SrcSpan
    attrs: list[Attr]
    name: Identifier
    generics: list[Identifier]
    variants: list[VariantInfo]

    def __repr__(self) -> str:
        attrs_str = " ".join(str(attr) for attr in self.attrs)
        generics_str = f"<{', '.join(gen.name for gen in self.generics)}>" if self.generics else ""
        variants_str = ", ".join(str(variant) for variant in self.variants)
        return f"{attrs_str} enum {self.name.name}{generics_str} {{ {variants_str} }}"


@dataclass
class Impl:
    span: SrcSpan
    generics: list[Identifier]
    target: ASTType
    trait: ASTType | None
    items: list[MethodDef]

    def __repr__(self) -> str:
        generics_str = f"<{', '.join(gen.name for gen in self.generics)}>" if self.generics else ""
        if self.trait:
            return f"impl{generics_str} {self.trait} for {self.target} {{ ... }}"
        else:
            return f"impl{generics_str} {self.target} {{ ... }}"


@dataclass
class TraitDef:
    span: SrcSpan
    attrs: list[Attr]
    name: Identifier
    generics: list[Identifier]
    items: list[TraitItem]

    def __repr__(self) -> str:
        attrs_str = " ".join(str(attr) for attr in self.attrs)
        generics_str = f"<{', '.join(gen.name for gen in self.generics)}>" if self.generics else ""
        return f"{attrs_str} trait {self.name.name}{generics_str} {{ ... }}"


@dataclass
class MethodDef:
    span: SrcSpan
    decl: MethodDecl
    body: Block

    def __repr__(self) -> str:
        return f"{self.decl} {{ ... }}"


@dataclass
class MethodDecl:
    span: SrcSpan
    attrs: list[Attr]
    name: Identifier
    generics: list[Identifier]
    params: list[VarInfo]
    ret_type: ASTType | None

    def __repr__(self) -> str:
        attrs_str = " ".join(str(attr) for attr in self.attrs)
        generics_str = f"<{', '.join(gen.name for gen in self.generics)}>" if self.generics else ""
        params_str = ", ".join(str(param) for param in self.params)
        ret_type_str = f" -> {self.ret_type}" if self.ret_type else ""
        return f"{attrs_str} fn {self.name.name}{generics_str}({params_str}){ret_type_str}"


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

    def __repr__(self) -> str:
        init_str = f" = {self.init_expr}" if self.init_expr else ""
        return f"var {self.var_type} {self.name.name}{init_str}"


@dataclass
class Return:
    span: SrcSpan
    expr: Expr | None

    def __repr__(self) -> str:
        expr_str = f" {self.expr}" if self.expr else ""
        return f"return{expr_str}"


@dataclass
class If:
    span: SrcSpan
    condition: Expr
    then_branch: Block
    elif_branches: list[tuple[Expr, Block]]
    else_branch: Block | None

    def __repr__(self) -> str:
        elif_str = "".join(f" elif {cond} {{ ... }}" for cond, _ in self.elif_branches)
        else_str = " else { ... }" if self.else_branch else ""
        return f"if {self.condition} {{ ... }}{elif_str}{else_str}"


@dataclass
class For:
    span: SrcSpan
    var_name: Identifier
    iterable: Expr
    body: Block

    def __repr__(self) -> str:
        return f"for {self.var_name.name} in {self.iterable} {{ ... }}"


@dataclass
class While:
    span: SrcSpan
    condition: Expr
    body: Block

    def __repr__(self) -> str:
        return f"while {self.condition} {{ ... }}"


@dataclass
class Loop:
    span: SrcSpan
    body: Block

    def __repr__(self) -> str:
        return "loop { ... }"


@dataclass
class Match:
    span: SrcSpan
    expr: Expr
    arms: list[tuple[Pattern, Block]]

    def __repr__(self) -> str:
        arms_str = " | ".join(f"{pat} => {{ ... }}" for pat, _ in self.arms)
        return f"match {self.expr} {{ {arms_str} }}"


@dataclass
class Break:
    span: SrcSpan

    def __repr__(self) -> str:
        return "break"


@dataclass
class Continue:
    span: SrcSpan

    def __repr__(self) -> str:
        return "continue"


@dataclass
class Assert:
    span: SrcSpan
    condition: Expr
    message: Expr | None

    def __repr__(self) -> str:
        message_str = f": {self.message}" if self.message else ""
        return f"assert {self.condition}{message_str}"


@dataclass
class Delete:
    span: SrcSpan
    target: Expr

    def __repr__(self) -> str:
        return f"del {self.target}"


@dataclass
class IntPattern:
    span: SrcSpan
    values: list[IntLiteral]

    def __repr__(self) -> str:
        values_str = " | ".join(str(value) for value in self.values)
        return f"{values_str}"


@dataclass
class CharPattern:
    span: SrcSpan
    values: list[CharLiteral]

    def __repr__(self) -> str:
        values_str = " | ".join(str(value) for value in self.values)
        return f"{values_str}"


@dataclass
class StrPattern:
    span: SrcSpan
    values: list[StrLiteral]

    def __repr__(self) -> str:
        values_str = " | ".join(str(value) for value in self.values)
        return f"{values_str}"


@dataclass
class EnumPattern:
    span: SrcSpan
    variants: list[Identifier]

    def __repr__(self) -> str:
        variants_str = " | ".join(variant.name for variant in self.variants)
        return f"{variants_str}"


@dataclass
class PayloadPattern:
    span: SrcSpan
    variant: Identifier
    fields: list[Identifier]

    def __repr__(self) -> str:
        fields_str = ", ".join(field.name for field in self.fields)
        return f"{self.variant.name}({fields_str})"


@dataclass
class WildcardPattern:
    span: SrcSpan

    def __repr__(self) -> str:
        return "_"


@dataclass
class Binary:
    span: SrcSpan
    op: BinaryOperator
    left: Expr
    right: Expr

    def __repr__(self) -> str:
        if self.op == BinaryOperator.Index:
            return f"{self.left}[{self.right}]"
        else:
            return f"({self.left} {self.op} {self.right})"


@dataclass
class Unary:
    span: SrcSpan
    op: UnaryOperator
    operand: Expr

    def __repr__(self) -> str:
        return f"({self.op}{self.operand})"


@dataclass
class Arg:
    span: SrcSpan
    name: Identifier | None
    value: Expr

    def __repr__(self) -> str:
        if self.name:
            return f"{self.name.name}={self.value}"
        else:
            return str(self.value)


@dataclass
class Call:
    span: SrcSpan
    callee: Expr
    args: list[Arg]

    def __repr__(self) -> str:
        args_str = ", ".join(str(arg) for arg in self.args)
        return f"{self.callee}({args_str})"


@dataclass
class MethodCall:
    span: SrcSpan
    receiver: Expr
    method_name: Identifier
    generics: list[ASTType]
    args: list[Arg]

    def __repr__(self) -> str:
        generics_str = f"<{', '.join(str(gen) for gen in self.generics)}>" if self.generics else ""
        args_str = ", ".join(str(arg) for arg in self.args)
        return f"{self.receiver}.{self.method_name.name}{generics_str}({args_str})"


@dataclass
class FieldAccess:
    span: SrcSpan
    receiver: Expr
    field_name: Identifier

    def __repr__(self) -> str:
        return f"{self.receiver}.{self.field_name.name}"


@dataclass
class DynValue:
    span: SrcSpan
    value: Expr

    def __repr__(self) -> str:
        return f"dyn {self.value}"


@dataclass
class DynBuffer:
    span: SrcSpan
    target_type: ASTType
    size: Expr

    def __repr__(self) -> str:
        return f"dyn[{self.size}] {self.target_type}"


@dataclass
class TypeItem:
    """
    Represents stuff like `Option<T>`, `Foo<i32*, String>`, etc.
    """

    span: SrcSpan
    name: Identifier
    generics: list[ASTType]

    def __repr__(self) -> str:
        generics_str = f"<{', '.join(str(gen) for gen in self.generics)}>" if self.generics else ""
        return f"{self.name.name}{generics_str}"


@dataclass
class Tuple:
    span: SrcSpan
    elements: list[Expr]

    def __repr__(self) -> str:
        elements_str = ", ".join(str(elem) for elem in self.elements)
        return f"({elements_str})"


@dataclass
class Array:
    span: SrcSpan
    elements: list[Expr]

    def __repr__(self) -> str:
        elements_str = ", ".join(str(elem) for elem in self.elements)
        return f"[{elements_str}]"


@dataclass
class Identifier:
    span: SrcSpan
    name: str

    def __repr__(self) -> str:
        return self.name


@dataclass
class Literal:
    span: SrcSpan
    literal: LexLiteral

    def __repr__(self) -> str:
        return str(self.literal)


ProgramItem: TypeAlias = (
    Import
    | Alias
    | FuncDef
    | StructDef | EnumDef | TraitDef
    | Impl
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
