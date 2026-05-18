from __future__ import annotations

from enum import Enum

from compiler.frontend.lex.token import Keyword, KeywordKind, Punctuator, PunctuatorKind
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
    def try_from_token(cls, stream: TokenStream) -> BinaryOperator | None:
        token = stream.peek()

        match token:
            case Punctuator(kind=PunctuatorKind.Plus):
                stream.consume_punctuator(PunctuatorKind.Plus)
                return cls.Add
            case Punctuator(kind=PunctuatorKind.Minus):
                stream.consume_punctuator(PunctuatorKind.Minus)
                return cls.Sub
            case Punctuator(kind=PunctuatorKind.Star):
                stream.consume_punctuator(PunctuatorKind.Star)
                return cls.Mul
            case Punctuator(kind=PunctuatorKind.Slash):
                stream.consume_punctuator(PunctuatorKind.Slash)
                return cls.Div
            case Punctuator(kind=PunctuatorKind.Percent):
                stream.consume_punctuator(PunctuatorKind.Percent)
                return cls.Mod
            case Punctuator(kind=PunctuatorKind.Ampersand):
                stream.consume_punctuator(PunctuatorKind.Ampersand)
                return cls.BitAnd
            case Punctuator(kind=PunctuatorKind.Pipe):
                stream.consume_punctuator(PunctuatorKind.Pipe)
                return cls.BitOr
            case Punctuator(kind=PunctuatorKind.Caret):
                stream.consume_punctuator(PunctuatorKind.Caret)
                return cls.BitXor
            case Punctuator(kind=PunctuatorKind.LessLess):
                stream.consume_punctuator(PunctuatorKind.LessLess)
                return cls.Shl
            case Punctuator(kind=PunctuatorKind.EqualEqual):
                stream.consume_punctuator(PunctuatorKind.EqualEqual)
                return cls.Eq
            case Punctuator(kind=PunctuatorKind.NotEqual):
                stream.consume_punctuator(PunctuatorKind.NotEqual)
                return cls.Neq
            case Punctuator(kind=PunctuatorKind.Less):
                stream.consume_punctuator(PunctuatorKind.Less)
                return cls.Lt
            case Punctuator(kind=PunctuatorKind.Greater):
                stream.consume_punctuator(PunctuatorKind.Greater)
                next_token = stream.peek()
                if not (isinstance(next_token, Punctuator) and next_token.kind == PunctuatorKind.Greater):
                    return cls.Gt
                stream.consume_punctuator(PunctuatorKind.Greater)
                next_next_token = stream.peek()
                if isinstance(next_next_token, Punctuator) and next_next_token.kind == PunctuatorKind.Equal:
                    stream.consume_punctuator(PunctuatorKind.Equal)
                    return cls.ShrAssign
                return cls.Shr
            case Punctuator(kind=PunctuatorKind.LessEqual):
                stream.consume_punctuator(PunctuatorKind.LessEqual)
                return cls.Leq
            case Punctuator(kind=PunctuatorKind.GreaterEqual):
                stream.consume_punctuator(PunctuatorKind.GreaterEqual)
                return cls.Geq
            case Keyword(kind=KeywordKind.And):
                stream.consume_keyword(KeywordKind.And)
                return cls.LogicalAnd
            case Keyword(kind=KeywordKind.Or):
                stream.consume_keyword(KeywordKind.Or)
                return cls.LogicalOr
            case Punctuator(kind=PunctuatorKind.Equal):
                stream.consume_punctuator(PunctuatorKind.Equal)
                return cls.Assign
            case Punctuator(kind=PunctuatorKind.PlusEqual):
                stream.consume_punctuator(PunctuatorKind.PlusEqual)
                return cls.AddAssign
            case Punctuator(kind=PunctuatorKind.MinusEqual):
                stream.consume_punctuator(PunctuatorKind.MinusEqual)
                return cls.SubAssign
            case Punctuator(kind=PunctuatorKind.StarEqual):
                stream.consume_punctuator(PunctuatorKind.StarEqual)
                return cls.MulAssign
            case Punctuator(kind=PunctuatorKind.SlashEqual):
                stream.consume_punctuator(PunctuatorKind.SlashEqual)
                return cls.DivAssign
            case Punctuator(kind=PunctuatorKind.PercentEqual):
                stream.consume_punctuator(PunctuatorKind.PercentEqual)
                return cls.ModAssign
            case Punctuator(kind=PunctuatorKind.AmpersandEqual):
                stream.consume_punctuator(PunctuatorKind.AmpersandEqual)
                return cls.BitAndAssign
            case Punctuator(kind=PunctuatorKind.PipeEqual):
                stream.consume_punctuator(PunctuatorKind.PipeEqual)
                return cls.BitOrAssign
            case Punctuator(kind=PunctuatorKind.CaretEqual):
                stream.consume_punctuator(PunctuatorKind.CaretEqual)
                return cls.BitXorAssign
            case Punctuator(kind=PunctuatorKind.LessLessEqual):
                stream.consume_punctuator(PunctuatorKind.LessLessEqual)
                return cls.ShlAssign
            case Keyword(kind=KeywordKind.In):
                stream.consume_keyword(KeywordKind.In)
                return cls.In
            case Keyword(kind=KeywordKind.Not):
                stream.consume_keyword(KeywordKind.Not)
                stream.consume_spaces()
                stream.consume_keyword(KeywordKind.In)
                return cls.NotIn
            case Punctuator(kind=PunctuatorKind.DotDot):
                stream.consume_punctuator(PunctuatorKind.DotDot)
                return cls.Range
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

    def __repr__(self) -> str:
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
    def try_from_token(cls, stream: TokenStream) -> UnaryOperator | None:
        token = stream.peek()

        match token:
            case Punctuator(kind=PunctuatorKind.Minus):
                stream.consume_punctuator(PunctuatorKind.Minus)
                return cls.Neg
            case Punctuator(kind=PunctuatorKind.Tilde):
                stream.consume_punctuator(PunctuatorKind.Tilde)
                return cls.BitNot
            case Keyword(kind=KeywordKind.Not):
                stream.consume_keyword(KeywordKind.Not)
                return cls.LogicalNot
            case Punctuator(kind=PunctuatorKind.Star):
                stream.consume_punctuator(PunctuatorKind.Star)
                return cls.Deref
            case Punctuator(kind=PunctuatorKind.Ampersand):
                stream.consume_punctuator(PunctuatorKind.Ampersand)
                return cls.AddrOf
            case _:
                return None

    @property
    def rbp(self) -> int:
        """Right binding power of the unary operator."""
        return self.value

    def __repr__(self) -> str:
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
