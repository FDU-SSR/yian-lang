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


class PunctuatorKind(Enum):
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
    DotDot = ".."
    Endl = "endl"

    @classmethod
    def try_from_str(cls, value: str) -> "PunctuatorKind | None":
        try:
            return cls(value)
        except ValueError:
            return None

    @classmethod
    def from_str(cls, value: str) -> "PunctuatorKind":
        try:
            return cls(value)
        except ValueError as exc:
            raise ValueError(f"{value} is not a valid punctuator") from exc


class Punctuator(Token):
    """
    Represents a punctuator token.
    """

    def __init__(self, kind: PunctuatorKind, span: SrcSpan):
        super().__init__(span)
        self.kind = kind

    def __repr__(self) -> str:
        return f"Punctuator({self.kind.value})"


class LiteralKind(Enum):
    """
    No boolean literals since we can just use keywords
    """

    String = auto()
    Char = auto()
    Integer = auto()
    Float = auto()


class Literal(Token):
    """
    Represents a literal token.
    """

    def __init__(self, raw: str, span: SrcSpan):
        super().__init__(span)
        self.raw = raw

        self.suffix: str | None = None
        self.kind = self.__parse_kind()
        self.value: str | int | float | bool = self.__parse_value()

    def __repr__(self) -> str:
        return f"Literal({self.kind.name}, {self.raw})"

    def __parse_value(self) -> str | int | float | bool:
        match self.kind:
            case LiteralKind.String:
                return self.__parse_string_value()
            case LiteralKind.Char:
                return self.__parse_char_value()
            case LiteralKind.Integer:
                return self.__parse_integer_value()
            case LiteralKind.Float:
                return self.__parse_float_value()

    def __parse_string_value(self) -> str:
        # handle escape sequences
        result = ""
        i = 1  # skip the opening quote
        while i < len(self.raw) - 1:  # skip the closing quote
            c = self.raw[i]
            if c == "\\":
                i, c = self.__parse_escape_sequence(i)
            result += c
            i += 1
        return result

    def __parse_char_value(self) -> str:
        # handle escape sequences
        result = ""
        i = 1  # skip the opening quote
        c = self.raw[i]
        if c == "\\":
            i, c = self.__parse_escape_sequence(i)
        result += c
        i += 1
        if i != len(self.raw) - 1:
            raise ValueError("Char literals must be a single character")
        return result

    ESCAPE_SEQUENCES = {
        "n": "\n",
        "t": "\t",
        "r": "\r",
        "\\": "\\",
        "'": "'",
        '"': '"',
        "0": "\0",
    }

    BASE_DIGITS = {
        2: "01",
        8: "01234567",
        10: "0123456789",
        16: "0123456789abcdefABCDEF",
    }

    def __parse_escape_sequence(self, i: int) -> tuple[int, str]:
        i += 1  # skip the backslash
        if i >= len(self.raw) - 1:
            raise ValueError("Invalid escape sequence at end of literal")
        escape_char = self.raw[i]
        if escape_char in self.ESCAPE_SEQUENCES:
            return i, self.ESCAPE_SEQUENCES[escape_char]
        elif escape_char in {"x", "X"}:
            # hex character literal
            hex_digits = self.raw[i + 1: i + 3]
            i += 2
            if any(c not in self.BASE_DIGITS[16] for c in hex_digits):
                raise ValueError(f"Invalid hex escape sequence: \\{escape_char}{hex_digits}")
            return i, chr(int(hex_digits, 16))
        elif escape_char in {"u", "U"}:
            # unicode character literal
            if self.raw[i + 1] != "{":
                raise ValueError(f"Invalid unicode escape sequence: \\{escape_char} must be followed by {{")
            i += 2  # skip the 'u' and the '{'
            unicode_digits = ""
            while i < len(self.raw) - 1 and self.raw[i] != "}":
                unicode_digits += self.raw[i]
                i += 1
            if i >= len(self.raw) - 1:
                raise ValueError("Invalid unicode escape sequence: missing closing }")
            if any(c not in self.BASE_DIGITS[16] for c in unicode_digits):
                raise ValueError(f"Invalid unicode escape sequence: \\{escape_char}{{{unicode_digits}}}")
            return i, chr(int(unicode_digits, 16))
        else:
            raise ValueError(f"Invalid escape sequence: \\{escape_char}")

    INT_SUFFIXES = {
        "i8": 2**7 - 1,
        "i16": 2**15 - 1,
        "i32": 2**31 - 1,
        "i64": 2**63 - 1,
        "u8": 2**8 - 1,
        "u16": 2**16 - 1,
        "u32": 2**32 - 1,
        "u64": 2**64 - 1,
        "int": 2**31 - 1,
        "uint": 2**32 - 1,
    }

    FLOAT_SUFFIXES = {"f16", "f32", "f64", "float"}

    def __parse_integer_value(self) -> int:
        # handle prefixes
        if self.raw.startswith(("0b", "0B")):
            base = 2
            raw = self.raw[2:]
        elif self.raw.startswith(("0o", "0O")):
            base = 8
            raw = self.raw[2:]
        elif self.raw.startswith(("0x", "0X")):
            base = 16
            raw = self.raw[2:]
        else:
            base = 10
            raw = self.raw

        # handle suffixes
        for suffix in self.INT_SUFFIXES:
            if raw.endswith(suffix):
                self.suffix = suffix
                raw = raw[:-len(suffix)]
                break

        # remove underscores
        raw = raw.replace("_", "")

        if any(c not in self.BASE_DIGITS[base] for c in raw):
            raise ValueError(f"Invalid integer literal: {self.raw} is not a valid base {base} integer")

        value = int(raw, base)

        # check int range based on suffix
        if self.suffix is not None:
            max_value = self.INT_SUFFIXES[self.suffix]
            if value > max_value:
                raise ValueError(f"Integer literal {self.raw} exceeds maximum value for type {self.suffix}")

        return value

    def __parse_float_value(self) -> float:
        # handle prefixes
        if self.raw.startswith(("0b", "0B")):
            raise ValueError("Binary float literals are not supported")
        elif self.raw.startswith(("0o", "0O")):
            raise ValueError("Octal float literals are not supported")
        elif self.raw.startswith(("0x", "0X")):
            base = 16
            raw = self.raw[2:]
        else:
            base = 10
            raw = self.raw

        # handle suffixes
        for suffix in self.FLOAT_SUFFIXES:
            if raw.endswith(suffix):
                self.suffix = suffix
                raw = raw[:-len(suffix)]
                break

        # remove underscores
        raw = raw.replace("_", "")

        if any(c not in self.BASE_DIGITS[base] and c not in ".eEpP+-" for c in raw):
            raise ValueError(f"Invalid float literal: {self.raw} is not a valid base {base} float")

        if base == 16:
            value = float.fromhex(raw)
        else:
            value = float(raw)

        return value

    def __parse_kind(self) -> LiteralKind:
        if self.raw.startswith("'") and self.raw.endswith("'"):
            return LiteralKind.Char
        if self.raw.startswith('"') and self.raw.endswith('"'):
            return LiteralKind.String
        if self.raw[0] in "0123456789":
            if any(ch in self.raw for ch in ".eEpP") or self.raw.endswith(("f16", "f32", "f64")):
                return LiteralKind.Float
            return LiteralKind.Integer
        if self.raw.startswith("b'") and self.raw.endswith("'"):
            return LiteralKind.Integer
        raise ValueError(f"Error parsing literal: {self.raw} is not a valid literal")


class EOF(Token):
    """
    Represents the end of file token.
    """

    def __repr__(self) -> str:
        return "EOF"
