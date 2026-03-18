from enum import Enum, auto

from lian.util.util import SimpleEnum

BLOCK_STMTS = [
    "block_start",
    "block_end"
]


# 作为标识符的所有合法字符
VAR_FIRST_CHAR = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ_"


YIAN_KEYWORDS = {
    # Import related
    "import", "from", "as",
    # Type related
    "typedef", "struct", "enum", "trait", "impl", "fn",
    # Attribute related
    "dyn", "pub", "static", "inline", "intrinsic",
    # Control flow related
    "if", "elif", "else",
    "match",
    "for", "while", "break", "continue",
    "return",
    "assert",
    # Operator related
    "in", "not in",
    "and", "or", "not",
    "typeof",
    "del",
    # Literal related
    "true", "false",
    # Underscore
    "_",
    # Types
    "void", "bool", "char", "str",
    "i8", "i16", "i32", "i64",
    "u8", "u16", "u32", "u64",
    "f16", "f32", "f64",
    "int", "uint", "float",
    # Self
    "self", "Self",
    # Built-in instructions
    "sizeof", "bitcast", "typeof", "panic", "byte_offset", "memcpy", "read", "write", "open", "close",
    # Not implemented yet
    # "union", "default", "do", "yield", "with",
    # "ok", "local", "shared", "volatile", "where",
}


class YianAttribute(Enum):
    Public = auto()
    Static = auto()
    Dyn = auto()
    Inline = auto()
    Intrinsic = auto()

    @classmethod
    def from_str(cls, s: str):
        match s:
            case "pub":
                return cls.Public
            case "static":
                return cls.Static
            case "dyn":
                return cls.Dyn
            case "inline":
                return cls.Inline
            case "intrinsic":
                return cls.Intrinsic
        raise ValueError(f"{s} is not a yian attribute")


class AccessMode(Enum):
    Private = auto()
    Public = auto()

    @classmethod
    def from_str(cls, s: str):
        match s:
            case "private":
                return cls.Private
            case "public":
                return cls.Public
        raise ValueError(f"{s} is not a access mode")


YIAN_ATTRS = SimpleEnum({
    "PUBLIC": "pub",
    "STATIC": "static",
    "SHARED": "shared",
    "MUTEX": "mutex",
    "INLINE": "inline",
    "INTRINSIC": "intrinsic"
})


class IntrinsicCustomType(Enum):
    Range = auto()
    Option = auto()
    Result = auto()
    SinglePtr = auto()
    MultiPtr = auto()
    FullPtr = auto()


class IntrinsicTrait(Enum):
    Add = auto()
    Sub = auto()
    Mul = auto()
    Div = auto()
    Rem = auto()
    Neg = auto()
    BitAnd = auto()
    BitOr = auto()
    BitXor = auto()
    BitNot = auto()
    Shl = auto()
    Shr = auto()
    PartialEq = auto()
    PartialOrd = auto()
    Index = auto()
    Contains = auto()
    Deref = auto()
    Delete = auto()
    Drop = auto()


class IntrinsicType(Enum):
    Void = auto()
    Bool = auto()
    Char = auto()
    Str = auto()
    I8 = auto()
    I16 = auto()
    I32 = auto()
    I64 = auto()
    U8 = auto()
    U16 = auto()
    U32 = auto()
    U64 = auto()
    F16 = auto()
    F32 = auto()
    F64 = auto()
    Int = auto()
    UInt = auto()
    Float = auto()

    @staticmethod
    def is_of(s: str) -> bool:
        return s in {
            "void",
            "bool",
            "char",
            "str",
            "i8",
            "i16",
            "i32",
            "i64",
            "u8",
            "u16",
            "u32",
            "u64",
            "f16",
            "f32",
            "f64",
            "int",
            "uint",
            "float",
        }

    @classmethod
    def from_str(cls, s: str):
        match s:
            case "void":
                return cls.Void
            case "bool":
                return cls.Bool
            case "char":
                return cls.Char
            case "str":
                return cls.Str
            case "i8":
                return cls.I8
            case "i16":
                return cls.I16
            case "i32":
                return cls.I32
            case "i64":
                return cls.I64
            case "u8":
                return cls.U8
            case "u16":
                return cls.U16
            case "u32":
                return cls.U32
            case "u64":
                return cls.U64
            case "f16":
                return cls.F16
            case "f32":
                return cls.F32
            case "f64":
                return cls.F64
            case "int":
                return cls.Int
            case "uint":
                return cls.UInt
            case "float":
                return cls.Float
        raise ValueError(f"{s} is not an intrinsic type")


class IntrinsicFunction(Enum):
    Free = auto()
    Malloc = auto()
    Write = auto()
    Read = auto()
    Open = auto()
    Close = auto()
    Exit = auto()
    StrCompare = auto()
    MemCopy = auto()
    MemCompare = auto()


STD_LIBS = {
    "builtin",
    "ops",
    "option",
    "ptr",
    "slice",
    "full_ptr",
    "raw_ptr",
    "convert",
    # "result",
    # "vector",
    "iterator",
    "mem",
}


PRIMITIVE_TYPE_ID_MAX = 200

TYPE_VALUE_DEF_KIND = SimpleEnum({
    "SYMBOL_DEF": 0,
    "CONSTANT_DATA": 1,
    "ANONYMOUS_DEF": 2,
})

LIB_KIND = SimpleEnum({
    "FIRST_PARTY": 0,
    "BUILTIN": 1,
    "EXTERNAL": 2,
})

SYSCALL_NUM = SimpleEnum({
    "SYS_OPEN": 2,
    "SYS_CLOSE": 3,
    "SYS_READ": 63,
    "SYS_WRITE": 64,
})

SYSCALL_NUM = SimpleEnum({
    "SYS_OPEN": 2,
    "SYS_CLOSE": 3,
    "SYS_READ": 63,
    "SYS_WRITE": 64,
})

ROOT_BLOCK_ID = 0


NO_TYPE = -1

NO_STMT_ID = -1

NO_PATH = "<no_path>"

SELF_SYMBOL_ID = -1
