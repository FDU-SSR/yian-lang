from __future__ import annotations

from enum import Enum, auto
from typing import TYPE_CHECKING

from compiler.frontend.lex.token import (Keyword, KeywordKind, Punctuator,
                                         PunctuatorKind)

if TYPE_CHECKING:
    from compiler.frontend.parse.stream import TokenStream


class BinaryOperator(Enum):
    # Arithmetic
    Add = auto()
    Sub = auto()
    Mul = auto()
    Div = auto()
    Mod = auto()
    # Bitwise
    BitAnd = auto()
    BitOr = auto()
    BitXor = auto()
    Shl = auto()
    Shr = auto()
    # Comparison
    Eq = auto()
    Neq = auto()
    Lt = auto()
    Gt = auto()
    Leq = auto()
    Geq = auto()
    # Logical
    LogicalAnd = auto()
    LogicalOr = auto()
    # Assignment
    Assign = auto()
    # Arithmetic assignment
    AddAssign = auto()
    SubAssign = auto()
    MulAssign = auto()
    DivAssign = auto()
    ModAssign = auto()
    # Bitwise assignment
    BitAndAssign = auto()
    BitOrAssign = auto()
    BitXorAssign = auto()
    ShlAssign = auto()
    ShrAssign = auto()
    # Mem
    Index = auto()
    # Membership
    In = auto()
    # Range
    Range = auto()

    @classmethod
    def try_from_token(cls, stream: TokenStream) -> tuple[BinaryOperator, int] | None:
        """Try to parse a binary operator from the token stream. Returns the operator and its token length if successful, or None if the next token is not a binary operator."""
        token = stream.peek()

        match token:
            case Punctuator(kind=PunctuatorKind.Plus):
                return cls.Add, 1
            case Punctuator(kind=PunctuatorKind.Minus):
                return cls.Sub, 1
            case Punctuator(kind=PunctuatorKind.Star):
                return cls.Mul, 1
            case Punctuator(kind=PunctuatorKind.Slash):
                return cls.Div, 1
            case Punctuator(kind=PunctuatorKind.Percent):
                return cls.Mod, 1
            case Punctuator(kind=PunctuatorKind.Ampersand):
                return cls.BitAnd, 1
            case Punctuator(kind=PunctuatorKind.Pipe):
                return cls.BitOr, 1
            case Punctuator(kind=PunctuatorKind.Caret):
                return cls.BitXor, 1
            case Punctuator(kind=PunctuatorKind.LessLess):
                return cls.Shl, 1
            case Punctuator(kind=PunctuatorKind.EqualEqual):
                return cls.Eq, 1
            case Punctuator(kind=PunctuatorKind.NotEqual):
                return cls.Neq, 1
            case Punctuator(kind=PunctuatorKind.Less):
                return cls.Lt, 1
            case Punctuator(kind=PunctuatorKind.Greater):
                next_token = stream.peek_nth(1)
                if not (isinstance(next_token, Punctuator) and next_token.kind == PunctuatorKind.Greater):
                    return cls.Gt, 1
                next_next_token = stream.peek_nth(2)
                if isinstance(next_next_token, Punctuator) and next_next_token.kind == PunctuatorKind.Equal:
                    return cls.ShrAssign, 3
                return cls.Shr, 2
            case Punctuator(kind=PunctuatorKind.LessEqual):
                return cls.Leq, 1
            case Punctuator(kind=PunctuatorKind.GreaterEqual):
                return cls.Geq, 1
            case Punctuator(kind=PunctuatorKind.AmpersandAmpersand):
                return cls.LogicalAnd, 1
            case Punctuator(kind=PunctuatorKind.PipePipe):
                return cls.LogicalOr, 1
            case Punctuator(kind=PunctuatorKind.Equal):
                return cls.Assign, 1
            case Punctuator(kind=PunctuatorKind.PlusEqual):
                return cls.AddAssign, 1
            case Punctuator(kind=PunctuatorKind.MinusEqual):
                return cls.SubAssign, 1
            case Punctuator(kind=PunctuatorKind.StarEqual):
                return cls.MulAssign, 1
            case Punctuator(kind=PunctuatorKind.SlashEqual):
                return cls.DivAssign, 1
            case Punctuator(kind=PunctuatorKind.PercentEqual):
                return cls.ModAssign, 1
            case Punctuator(kind=PunctuatorKind.AmpersandEqual):
                return cls.BitAndAssign, 1
            case Punctuator(kind=PunctuatorKind.PipeEqual):
                return cls.BitOrAssign, 1
            case Punctuator(kind=PunctuatorKind.CaretEqual):
                return cls.BitXorAssign, 1
            case Punctuator(kind=PunctuatorKind.LessLessEqual):
                return cls.ShlAssign, 1
            case Keyword(kind=KeywordKind.In):
                return cls.In, 1
            case Punctuator(kind=PunctuatorKind.DotDot):
                return cls.Range, 1
            case _:
                return None

    @property
    def lbp(self) -> int:
        """Left binding power (precedence) of the operator."""
        return BINARY_PRECEDENCE[self][0]

    @property
    def rbp(self) -> int:
        """Right binding power of the operator (for right-associative operators)."""
        return BINARY_PRECEDENCE[self][1]

    def __str__(self) -> str:
        match self:
            case BinaryOperator.Add:
                return "+"
            case BinaryOperator.Sub:
                return "-"
            case BinaryOperator.Mul:
                return "*"
            case BinaryOperator.Div:
                return "/"
            case BinaryOperator.Mod:
                return "%"
            case BinaryOperator.BitAnd:
                return "&"
            case BinaryOperator.BitOr:
                return "|"
            case BinaryOperator.BitXor:
                return "^"
            case BinaryOperator.Shl:
                return "<<"
            case BinaryOperator.Shr:
                return ">>"
            case BinaryOperator.Eq:
                return "=="
            case BinaryOperator.Neq:
                return "!="
            case BinaryOperator.Lt:
                return "<"
            case BinaryOperator.Gt:
                return ">"
            case BinaryOperator.Leq:
                return "<="
            case BinaryOperator.Geq:
                return ">="
            case BinaryOperator.LogicalAnd:
                return "&&"
            case BinaryOperator.LogicalOr:
                return "||"
            case BinaryOperator.Assign:
                return "="
            case BinaryOperator.AddAssign:
                return "+="
            case BinaryOperator.SubAssign:
                return "-="
            case BinaryOperator.MulAssign:
                return "*="
            case BinaryOperator.DivAssign:
                return "/="
            case BinaryOperator.ModAssign:
                return "%="
            case BinaryOperator.BitAndAssign:
                return "&="
            case BinaryOperator.BitOrAssign:
                return "|="
            case BinaryOperator.BitXorAssign:
                return "^="
            case BinaryOperator.ShlAssign:
                return "<<="
            case BinaryOperator.ShrAssign:
                return ">>="
            case BinaryOperator.Index:
                return "[]"
            case BinaryOperator.In:
                return "in"
            case BinaryOperator.Range:
                return ".."


BINARY_PRECEDENCE = {
    BinaryOperator.Add: (10, 11),
    BinaryOperator.Sub: (10, 11),
    BinaryOperator.Mul: (11, 12),
    BinaryOperator.Div: (11, 12),
    BinaryOperator.Mod: (11, 12),
    BinaryOperator.BitAnd: (6, 7),
    BinaryOperator.BitOr: (4, 5),
    BinaryOperator.BitXor: (5, 6),
    BinaryOperator.Shl: (9, 10),
    BinaryOperator.Shr: (9, 10),
    BinaryOperator.Eq: (7, 8),
    BinaryOperator.Neq: (7, 8),
    BinaryOperator.Lt: (8, 9),
    BinaryOperator.Gt: (8, 9),
    BinaryOperator.Leq: (8, 9),
    BinaryOperator.Geq: (8, 9),
    BinaryOperator.LogicalAnd: (3, 4),
    BinaryOperator.LogicalOr: (2, 3),
    BinaryOperator.Assign: (1, 1),
    BinaryOperator.AddAssign: (1, 1),
    BinaryOperator.SubAssign: (1, 1),
    BinaryOperator.MulAssign: (1, 1),
    BinaryOperator.DivAssign: (1, 1),
    BinaryOperator.ModAssign: (1, 1),
    BinaryOperator.BitAndAssign: (1, 1),
    BinaryOperator.BitOrAssign: (1, 1),
    BinaryOperator.BitXorAssign: (1, 1),
    BinaryOperator.ShlAssign: (1, 1),
    BinaryOperator.ShrAssign: (1, 1),
    BinaryOperator.Index: (999, 999),
    BinaryOperator.In: (999, 999),
    BinaryOperator.Range: (999, 999),
}


class UnaryOperator(Enum):
    # Arithmetic
    Neg = auto()
    # Bitwise
    BitNot = auto()
    # Logical
    LogicalNot = auto()
    # Mem
    Deref = auto()
    AddrOf = auto()

    @classmethod
    def try_from_token(cls, stream: TokenStream) -> tuple[UnaryOperator, int] | None:
        token = stream.peek()

        match token:
            case Punctuator(kind=PunctuatorKind.Minus):
                return cls.Neg, 1
            case Punctuator(kind=PunctuatorKind.Tilde):
                return cls.BitNot, 1
            case Punctuator(kind=PunctuatorKind.Exclamation):
                return cls.LogicalNot, 1
            case Punctuator(kind=PunctuatorKind.Star):
                return cls.Deref, 1
            case Punctuator(kind=PunctuatorKind.Ampersand):
                return cls.AddrOf, 1
            case _:
                return None

    @property
    def rbp(self) -> int:
        """Right binding power of the unary operator."""
        return UNARY_PRECEDENCE[self]

    def __str__(self) -> str:
        match self:
            case UnaryOperator.Neg:
                return "-"
            case UnaryOperator.BitNot:
                return "~"
            case UnaryOperator.LogicalNot:
                return "!"
            case UnaryOperator.Deref:
                return "*"
            case UnaryOperator.AddrOf:
                return "&"


UNARY_PRECEDENCE = {
    UnaryOperator.Neg: 13,
    UnaryOperator.BitNot: 13,
    UnaryOperator.LogicalNot: 13,
    UnaryOperator.Deref: 14,
    UnaryOperator.AddrOf: 14,
}
