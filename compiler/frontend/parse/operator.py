from __future__ import annotations

from enum import Enum, auto

from compiler.frontend.lex.token import (Keyword, KeywordKind, Punctuator,
                                         PunctuatorKind, Token)


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
    def try_from_token(cls, token: Token) -> BinaryOperator | None:
        """Try to parse a binary operator from a token. Returns the operator if successful, or None."""
        if isinstance(token, Punctuator):
            return BINARY_PUNCTUATOR_MAP.get(token.kind)
        if isinstance(token, Keyword) and token.kind == KeywordKind.In:
            return cls.In
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

    def is_logical(self) -> bool:
        return self in (BinaryOperator.LogicalAnd, BinaryOperator.LogicalOr)

    def is_compound_assign(self) -> bool:
        return self in (BinaryOperator.AddAssign, BinaryOperator.SubAssign,
                        BinaryOperator.MulAssign, BinaryOperator.DivAssign,
                        BinaryOperator.ModAssign, BinaryOperator.BitAndAssign,
                        BinaryOperator.BitOrAssign, BinaryOperator.BitXorAssign,
                        BinaryOperator.ShlAssign, BinaryOperator.ShrAssign)

    def compound_assign_to_binary(self) -> BinaryOperator:
        return BINARY_COMPOUND_ASSIGN_TO_BINARY[self]


BINARY_COMPOUND_ASSIGN_TO_BINARY = {
    BinaryOperator.AddAssign: BinaryOperator.Add,
    BinaryOperator.SubAssign: BinaryOperator.Sub,
    BinaryOperator.MulAssign: BinaryOperator.Mul,
    BinaryOperator.DivAssign: BinaryOperator.Div,
    BinaryOperator.ModAssign: BinaryOperator.Mod,
    BinaryOperator.BitAndAssign: BinaryOperator.BitAnd,
    BinaryOperator.BitOrAssign: BinaryOperator.BitOr,
    BinaryOperator.BitXorAssign: BinaryOperator.BitXor,
    BinaryOperator.ShlAssign: BinaryOperator.Shl,
    BinaryOperator.ShrAssign: BinaryOperator.Shr,
}


BINARY_PUNCTUATOR_MAP: dict[PunctuatorKind, BinaryOperator] = {
    PunctuatorKind.Plus: BinaryOperator.Add,
    PunctuatorKind.Minus: BinaryOperator.Sub,
    PunctuatorKind.Star: BinaryOperator.Mul,
    PunctuatorKind.Slash: BinaryOperator.Div,
    PunctuatorKind.Percent: BinaryOperator.Mod,
    PunctuatorKind.Ampersand: BinaryOperator.BitAnd,
    PunctuatorKind.Pipe: BinaryOperator.BitOr,
    PunctuatorKind.Caret: BinaryOperator.BitXor,
    PunctuatorKind.LessLess: BinaryOperator.Shl,
    PunctuatorKind.EqualEqual: BinaryOperator.Eq,
    PunctuatorKind.NotEqual: BinaryOperator.Neq,
    PunctuatorKind.Less: BinaryOperator.Lt,
    PunctuatorKind.Greater: BinaryOperator.Gt,
    PunctuatorKind.GreaterGreater: BinaryOperator.Shr,
    PunctuatorKind.GreaterGreaterEqual: BinaryOperator.ShrAssign,
    PunctuatorKind.LessEqual: BinaryOperator.Leq,
    PunctuatorKind.GreaterEqual: BinaryOperator.Geq,
    PunctuatorKind.AmpersandAmpersand: BinaryOperator.LogicalAnd,
    PunctuatorKind.PipePipe: BinaryOperator.LogicalOr,
    PunctuatorKind.Equal: BinaryOperator.Assign,
    PunctuatorKind.PlusEqual: BinaryOperator.AddAssign,
    PunctuatorKind.MinusEqual: BinaryOperator.SubAssign,
    PunctuatorKind.StarEqual: BinaryOperator.MulAssign,
    PunctuatorKind.SlashEqual: BinaryOperator.DivAssign,
    PunctuatorKind.PercentEqual: BinaryOperator.ModAssign,
    PunctuatorKind.AmpersandEqual: BinaryOperator.BitAndAssign,
    PunctuatorKind.PipeEqual: BinaryOperator.BitOrAssign,
    PunctuatorKind.CaretEqual: BinaryOperator.BitXorAssign,
    PunctuatorKind.LessLessEqual: BinaryOperator.ShlAssign,
    PunctuatorKind.DotDot: BinaryOperator.Range,
}


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
    def try_from_token(cls, token: Token) -> UnaryOperator | None:
        """Try to parse a unary operator from a token. Returns the operator if successful, or None."""
        if isinstance(token, Punctuator):
            return UNARY_PUNCTUATOR_MAP.get(token.kind)
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


UNARY_PUNCTUATOR_MAP: dict[PunctuatorKind, UnaryOperator] = {
    PunctuatorKind.Minus: UnaryOperator.Neg,
    PunctuatorKind.Tilde: UnaryOperator.BitNot,
    PunctuatorKind.Exclamation: UnaryOperator.LogicalNot,
    PunctuatorKind.Star: UnaryOperator.Deref,
    PunctuatorKind.Ampersand: UnaryOperator.AddrOf,
}


UNARY_PRECEDENCE = {
    UnaryOperator.Neg: 13,
    UnaryOperator.BitNot: 13,
    UnaryOperator.LogicalNot: 13,
    UnaryOperator.Deref: 14,
    UnaryOperator.AddrOf: 14,
}
