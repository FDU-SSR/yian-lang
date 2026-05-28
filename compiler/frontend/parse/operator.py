from __future__ import annotations

from enum import Enum
from typing import TYPE_CHECKING

from compiler.frontend.lex.token import Keyword, KeywordKind, Punctuator, PunctuatorKind

if TYPE_CHECKING:
    from compiler.frontend.parse.stream import TokenStream


class BinaryOperator(Enum):
    # Arithmetic
    Add = (10, 11)
    Sub = (10, 11)
    Mul = (11, 12)
    Div = (11, 12)
    Mod = (11, 12)
    # Bitwise
    BitAnd = (6, 7)
    BitOr = (4, 5)
    BitXor = (5, 6)
    Shl = (9, 10)
    Shr = (9, 10)
    # Comparison
    Eq = (7, 8)
    Neq = (7, 8)
    Lt = (8, 9)
    Gt = (8, 9)
    Leq = (8, 9)
    Geq = (8, 9)
    # Logical
    LogicalAnd = (3, 4)
    LogicalOr = (2, 3)
    # Assignment
    Assign = (1, 1)
    # Arithmetic assignment
    AddAssign = (1, 1)
    SubAssign = (1, 1)
    MulAssign = (1, 1)
    DivAssign = (1, 1)
    ModAssign = (1, 1)
    # Bitwise assignment
    BitAndAssign = (1, 1)
    BitOrAssign = (1, 1)
    BitXorAssign = (1, 1)
    ShlAssign = (1, 1)
    ShrAssign = (1, 1)
    # Mem
    Index = (999, 999)
    # Membership
    In = (999, 999)
    NotIn = (999, 999)
    # Range
    Range = (999, 999)

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
            case Keyword(kind=KeywordKind.And):
                return cls.LogicalAnd, 1
            case Keyword(kind=KeywordKind.Or):
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
            case Keyword(kind=KeywordKind.Not):
                next_token = stream.peek_nth(1)
                next_next_token = stream.peek_nth(2)
                if isinstance(next_token, Punctuator) and next_token.kind == PunctuatorKind.Space and isinstance(next_next_token, Keyword) and next_next_token.kind == KeywordKind.In:
                    return cls.NotIn, 3
                return None
            case Punctuator(kind=PunctuatorKind.DotDot):
                return cls.Range, 1
            case _:
                return None

    @property
    def lbp(self) -> int:
        """Left binding power (precedence) of the operator."""
        return self.value[0]

    @property
    def rbp(self) -> int:
        """Right binding power of the operator (for right-associative operators)."""
        return self.value[1]

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
                return "and"
            case BinaryOperator.LogicalOr:
                return "or"
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
            case BinaryOperator.NotIn:
                return "not in"
            case BinaryOperator.Range:
                return ".."


class UnaryOperator(Enum):
    # Arithmetic
    Neg = 13
    # Bitwise
    BitNot = 13
    # Logical
    LogicalNot = 13
    # Mem
    Deref = 14
    AddrOf = 14

    @classmethod
    def try_from_token(cls, stream: TokenStream) -> tuple[UnaryOperator, int] | None:
        token = stream.peek()

        match token:
            case Punctuator(kind=PunctuatorKind.Minus):
                return cls.Neg, 1
            case Punctuator(kind=PunctuatorKind.Tilde):
                return cls.BitNot, 1
            case Keyword(kind=KeywordKind.Not):
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
        return self.value

    def __str__(self) -> str:
        match self:
            case UnaryOperator.Neg:
                return "-"
            case UnaryOperator.BitNot:
                return "~"
            case UnaryOperator.LogicalNot:
                return "not "
            case UnaryOperator.Deref:
                return "*"
            case UnaryOperator.AddrOf:
                return "&"
