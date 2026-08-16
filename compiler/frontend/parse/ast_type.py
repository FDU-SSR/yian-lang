from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, TypeAlias

if TYPE_CHECKING:
    from compiler.frontend.lex.position import SrcSpan
    from compiler.frontend.lex.token import Token
    from compiler.frontend.parse.ast import Identifier


@dataclass
class LiteralConstExpr:
    """字面量常量，如 T[5]、Array<T, 3> 中的 5、3"""
    span: SrcSpan
    literal: Token             # IntLiteral / BoolLiteral 等

    def __repr__(self) -> str:
        return str(self.literal)


@dataclass
class GenericConstExpr:
    """泛型常量引用，如 T[N]、Array<T, N> 中的 N"""
    span: SrcSpan
    name: Identifier

    def __repr__(self) -> str:
        return self.name.name


ConstExpr: TypeAlias = LiteralConstExpr | GenericConstExpr


@dataclass
class IntType:
    span: SrcSpan
    signed: bool
    width: int  # in bytes

    def __repr__(self) -> str:
        sign_str = "i" if self.signed else "u"
        return f"{sign_str}{self.width * 8}"


@dataclass
class FloatType:
    span: SrcSpan
    width: int  # in bytes

    def __repr__(self) -> str:
        return f"f{self.width * 8}"


@dataclass
class BoolType:
    span: SrcSpan

    def __repr__(self) -> str:
        return "bool"


@dataclass
class StrType:
    span: SrcSpan

    def __repr__(self) -> str:
        return "str"


@dataclass
class CharType:
    span: SrcSpan

    def __repr__(self) -> str:
        return "char"


@dataclass
class VoidType:
    span: SrcSpan

    def __repr__(self) -> str:
        return "void"


@dataclass
class NeverType:
    span: SrcSpan

    def __repr__(self) -> str:
        return "!"


@dataclass
class NamedType:
    span: SrcSpan
    name: Identifier

    def __repr__(self) -> str:
        return self.name.name


@dataclass
class ArrayType:
    span: SrcSpan
    element_type: ASTType
    size: ConstExpr

    def __repr__(self) -> str:
        match self.size:
            case LiteralConstExpr(literal=lit):
                return f"{self.element_type}[{lit}]"
            case GenericConstExpr(name=name):
                return f"{self.element_type}[{name.name}]"


@dataclass
class TupleType:
    span: SrcSpan
    element_types: list[ASTType]

    def __repr__(self) -> str:
        return f"({', '.join(repr(t) for t in self.element_types)})"


@dataclass
class PointerType:
    span: SrcSpan
    pointee_type: ASTType

    def __repr__(self) -> str:
        return f"{self.pointee_type}*"


@dataclass
class RefType:
    span: SrcSpan
    pointee_type: ASTType

    def __repr__(self) -> str:
        return f"{self.pointee_type}&"


@dataclass
class SliceType:
    span: SrcSpan
    element_type: ASTType

    def __repr__(self) -> str:
        return f"{self.element_type}[]"


@dataclass
class InstanceType:
    span: SrcSpan
    base: ASTType
    generic_args: list[ASTType | ConstExpr]

    def __repr__(self) -> str:
        if len(self.generic_args) == 0:
            return repr(self.base)
        parts: list[str] = []
        for arg in self.generic_args:
            match arg:
                case LiteralConstExpr(literal=lit):
                    parts.append(str(lit))
                case GenericConstExpr(name=name):
                    parts.append(name.name)
                case _:
                    parts.append(repr(arg))
        return f"{repr(self.base)}<{', '.join(parts)}>"


@dataclass
class FunctionType:
    span: SrcSpan
    param_types: list[ASTType]
    return_type: ASTType

    def __repr__(self) -> str:
        param_str = ", ".join(repr(t) for t in self.param_types)
        return f"fn({param_str}) -> {self.return_type}"


@dataclass
class DeducedType:
    span: SrcSpan

    def __repr__(self) -> str:
        return "_"


ASTType: TypeAlias = (
    IntType | FloatType | BoolType | StrType | CharType | VoidType | NeverType
    | ArrayType | TupleType | PointerType | RefType | SliceType
    | NamedType
    | InstanceType
    | FunctionType
    | DeducedType
)
