from __future__ import annotations

from dataclasses import dataclass
from typing import TypeAlias

from compiler.utils.IR.position import SrcSpan


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
class NamedType:
    span: SrcSpan
    name: str

    def __repr__(self) -> str:
        return self.name


@dataclass
class ArrayType:
    span: SrcSpan
    element_type: ASTType
    size: int

    def __repr__(self) -> str:
        return f"{self.element_type}[{self.size}]"


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
        return f"*{self.pointee_type}"


@dataclass
class SliceType:
    span: SrcSpan
    element_type: ASTType

    def __repr__(self) -> str:
        return f"{self.element_type}[]"


@dataclass
class InstantiatedType:
    span: SrcSpan
    base: ASTType
    generic_args: list[ASTType]

    def __repr__(self) -> str:
        if len(self.generic_args) == 0:
            return repr(self.base)
        return f"{repr(self.base)}<{', '.join(repr(arg) for arg in self.generic_args)}>"


@dataclass
class FunctionType:
    span: SrcSpan
    param_types: list[ASTType]
    return_type: ASTType

    def __repr__(self) -> str:
        param_str = ", ".join(repr(t) for t in self.param_types)
        return f"fn({param_str}) -> {self.return_type}"


ASTType: TypeAlias = (
    IntType | FloatType | BoolType | StrType | CharType | VoidType
    | ArrayType | TupleType | PointerType | SliceType
    | NamedType
    | InstantiatedType
    | FunctionType
)
