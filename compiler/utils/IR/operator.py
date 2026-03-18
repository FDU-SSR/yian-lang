from enum import Enum, auto

from compiler.utils.errors import CompilerError


class Operator(Enum):
    Add = auto()        # +
    Minus = auto()      # -
    Star = auto()       # *
    Slash = auto()      # /
    Percent = auto()    # %
    Ampersand = auto()  # &
    Pipe = auto()       # |
    Caret = auto()      # ^
    Tilde = auto()      # ~
    Shl = auto()        # <<
    Shr = auto()        # >>
    Eq = auto()         # ==
    Neq = auto()        # !=
    Gt = auto()         # >
    Lt = auto()         # <
    Ge = auto()         # >=
    Le = auto()         # <=
    And = auto()        # and
    Or = auto()         # or
    Not = auto()        # not
    Index = auto()      # []
    In = auto()         # in
    NotIn = auto()      # not in
    Dot = auto()        # .
    Range = auto()      # ..

    @staticmethod
    def from_str(op_str: str) -> "Operator":
        if op_str not in _OP_STR_TO_ENUM:
            raise CompilerError(f"Unknown operator string: {op_str}")
        return _OP_STR_TO_ENUM[op_str]

    def __repr__(self) -> str:
        return _OP_ENUM_TO_STR[self]

    def __str__(self) -> str:
        return self.__repr__()

    def __format__(self, format_spec: str) -> str:
        # Respect format specs while using the operator display string.
        return format(str(self), format_spec)

    @property
    def is_arithmetic(self) -> bool:
        return self in {
            Operator.Add,
            Operator.Minus,
            Operator.Star,
            Operator.Slash,
            Operator.Percent,
        }

    @property
    def is_comparison(self) -> bool:
        return self in {
            Operator.Eq,
            Operator.Neq,
            Operator.Gt,
            Operator.Lt,
            Operator.Ge,
            Operator.Le,
        }

    @property
    def is_bitwise(self) -> bool:
        return self in {
            Operator.Ampersand,
            Operator.Pipe,
            Operator.Caret,
            Operator.Tilde,
            Operator.Shl,
            Operator.Shr,
        }

    @property
    def is_logical(self) -> bool:
        return self in {
            Operator.And,
            Operator.Or,
            Operator.Not,
        }

    @property
    def is_membership(self) -> bool:
        return self in {
            Operator.In,
            Operator.NotIn,
        }


_OP_STR_TO_ENUM = {
    "+": Operator.Add,
    "-": Operator.Minus,
    "*": Operator.Star,
    "/": Operator.Slash,
    "%": Operator.Percent,
    "&": Operator.Ampersand,
    "|": Operator.Pipe,
    "^": Operator.Caret,
    "~": Operator.Tilde,
    "<<": Operator.Shl,
    ">>": Operator.Shr,
    "==": Operator.Eq,
    "!=": Operator.Neq,
    ">": Operator.Gt,
    "<": Operator.Lt,
    ">=": Operator.Ge,
    "<=": Operator.Le,
    "and": Operator.And,
    "or": Operator.Or,
    "not": Operator.Not,
    "[]": Operator.Index,
    "in": Operator.In,
    "not in": Operator.NotIn,
    ".": Operator.Dot,
    "..": Operator.Range,
}

_OP_ENUM_TO_STR = {value: key for key, value in _OP_STR_TO_ENUM.items()}
