from abc import ABC
from enum import Enum, auto

from compiler.frontend.lex.position import SrcSpan


class Token(ABC):
    """
    Base class for all tokens.
    """

    def __init__(self, span: SrcSpan):
        self.span = span


class KeywordKind(Enum):
    Import = "import"
    From = "from"
    As = "as"
    Typedef = "typedef"
    Struct = "struct"
    Enum = "enum"
    Trait = "trait"
    Impl = "impl"
    Fn = "fn"
    Dyn = "dyn"
    Pub = "pub"
    Static = "static"
    Inline = "inline"
    Intrinsic = "intrinsic"
    If = "if"
    Elif = "elif"
    Else = "else"
    Match = "match"
    For = "for"
    While = "while"
    Break = "break"
    Continue = "continue"
    Return = "return"
    Assert = "assert"
    In = "in"
    And = "and"
    Or = "or"
    Not = "not"
    Typeof = "typeof"
    Del = "del"
    True_ = "true"
    False_ = "false"
    Underscore = "_"
    Void = "void"
    Bool = "bool"
    Char = "char"
    Str = "str"
    I8 = "i8"
    I16 = "i16"
    I32 = "i32"
    I64 = "i64"
    U8 = "u8"
    U16 = "u16"
    U32 = "u32"
    U64 = "u64"
    F16 = "f16"
    F32 = "f32"
    F64 = "f64"
    Int = "int"
    Uint = "uint"
    Float = "float"
    Self_ = "self"
    SelfType = "Self"

    @classmethod
    def try_from_str(cls, value: str) -> "KeywordKind | None":
        try:
            return cls(value)
        except ValueError:
            return None


class Keyword(Token):
    """
    Represents a keyword token.
    """

    def __init__(self, kind: KeywordKind, span: SrcSpan):
        super().__init__(span)
        self.kind = kind

    def __repr__(self) -> str:
        return f"Keyword({self.kind.value})"


class Identifier(Token):
    """
    Represents an identifier token.
    """

    def __init__(self, name: str, span: SrcSpan):
        super().__init__(span)
        self.name = name

    def __repr__(self) -> str:
        return f"Identifier({self.name})"


class DelimiterKind(Enum):
    LParen = "("
    RParen = ")"
    LBrace = "{"
    RBrace = "}"
    LBracket = "["
    RBracket = "]"
    Comma = ","
    Colon = ":"
    Semicolon = ";"
    Dot = "."
    Arrow = "->"
    FatArrow = "=>"
    Endl = "endl"

    @classmethod
    def from_str(cls, value: str) -> "DelimiterKind":
        try:
            return cls(value)
        except ValueError as exc:
            raise ValueError(f"{value} is not a valid delimiter") from exc


class Delimiter(Token):
    """
    Represents a delimiter token.
    """

    def __init__(self, kind: DelimiterKind, span: SrcSpan):
        super().__init__(span)
        self.kind = kind

    def __repr__(self) -> str:
        return f"Delimiter({self.kind.value})"


class OperatorKind(Enum):
    Plus = "+"
    Minus = "-"
    Star = "*"
    Slash = "/"
    Percent = "%"
    Caret = "^"
    Ampersand = "&"
    Pipe = "|"
    Tilde = "~"
    Exclamation = "!"
    Equal = "="
    Less = "<"
    Greater = ">"
    PlusEqual = "+="
    MinusEqual = "-="
    StarEqual = "*="
    SlashEqual = "/="
    PercentEqual = "%="
    CaretEqual = "^="
    AmpersandEqual = "&="
    PipeEqual = "|="
    EqualEqual = "=="
    NotEqual = "!="
    LessEqual = "<="
    GreaterEqual = ">="

    @classmethod
    def from_str(cls, value: str) -> "OperatorKind":
        try:
            return cls(value)
        except ValueError as exc:
            raise ValueError(f"{value} is not a valid operator") from exc


class Operator(Token):
    """
    Represents an operator token.
    """

    def __init__(self, kind: OperatorKind, span: SrcSpan):
        super().__init__(span)
        self.kind = kind

    def __repr__(self) -> str:
        return f"Operator({self.kind.value})"


class LiteralKind(Enum):
    String = auto()
    Char = auto()
    Integer = auto()
    Float = auto()
    Boolean = auto()


class Literal(Token):
    """
    Represents a literal token.
    """

    def __init__(self, raw: str, span: SrcSpan):
        super().__init__(span)
        self.raw = raw

        self.kind = self.__parse_kind()
        self.value: str | int | float | bool = self.__parse_value()

    def __repr__(self) -> str:
        return f"Literal({self.kind.name}, {self.raw})"

    def __parse_value(self) -> str | int | float | bool:
        # TODO
        return self.raw

    def __parse_kind(self) -> LiteralKind:
        # TODO
        return LiteralKind.String


class EOF(Token):
    """
    Represents the end of file token.
    """

    def __repr__(self) -> str:
        return "EOF"
