from enum import Enum, auto


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
    NotIn = auto()
    # Range
    Range = auto()


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
