from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import TypeAlias

from compiler.frontend.lex.position import SrcSpan


class KeywordKind(Enum):
    Import = "import"
    From = "from"
    As = "as"
    Let = "let"
    Typedef = "typedef"
    Struct = "struct"
    Enum = "enum"
    Trait = "trait"
    Impl = "impl"
    Fn = "fn"
    Dyn = "dyn"
    Pub = "pub"
    Static = "static"
    Const = "const"
    Comptime = "comptime"
    Inline = "inline"
    Intrinsic = "intrinsic"
    If = "if"
    Elif = "elif"
    Else = "else"
    Match = "match"
    For = "for"
    While = "while"
    Loop = "loop"
    Break = "break"
    Continue = "continue"
    Return = "return"
    Assert = "assert"
    In = "in"
    Typeof = "typeof"
    Sizeof = "sizeof"
    Bitcast = "bitcast"
    Del = "del"
    True_ = "true"
    False_ = "false"
    IsRawMode = "IS_RAW_MODE"
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

    @classmethod
    def try_from_str(cls, value: str) -> KeywordKind | None:
        return _KEYWORD_KIND_BY_VALUE.get(value)


_KEYWORD_KIND_BY_VALUE: dict[str, KeywordKind] = {member.value: member for member in KeywordKind}


@dataclass
class Keyword:
    """
    Represents a keyword token.
    """

    kind: KeywordKind
    span: SrcSpan

    def __repr__(self) -> str:
        return self.kind.value

    @classmethod
    def from_kind(cls, kind: KeywordKind) -> Keyword:
        return cls(kind, SrcSpan.empty())


@dataclass
class Identifier:
    """
    Represents an identifier token.
    """

    name: str
    span: SrcSpan

    def __repr__(self) -> str:
        return self.name


class PunctuatorKind(Enum):
    """
    Represents the kind of a punctuator token.
    """
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
    AmpersandAmpersand = "&&"
    Pipe = "|"
    PipePipe = "||"
    Tilde = "~"
    Exclamation = "!"
    Equal = "="
    Less = "<"
    Greater = ">"
    LAngle = "<angle>"   # Generic opening bracket — lexer emits when no preceding whitespace
    RAngle = ">angle>"   # Generic closing bracket — lexer emits when no preceding whitespace
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
    LessLess = "<<"
    LessLessEqual = "<<="
    GreaterGreater = ">>"
    GreaterGreaterEqual = ">>="
    DotDot = ".."
    At = "@"
    EOF = "<eof>"

    @classmethod
    def try_from_str(cls, value: str) -> PunctuatorKind | None:
        return _PUNCTUATOR_KIND_BY_VALUE.get(value)

    @classmethod
    def from_str(cls, value: str) -> PunctuatorKind:
        kind = _PUNCTUATOR_KIND_BY_VALUE.get(value)
        if kind is None:
            raise ValueError(f"{value} is not a valid punctuator") from None
        return kind


_PUNCTUATOR_KIND_BY_VALUE: dict[str, PunctuatorKind] = {member.value: member for member in PunctuatorKind}


@dataclass
class Punctuator:
    """
    Represents a punctuator token.
    """

    kind: PunctuatorKind
    span: SrcSpan

    def __repr__(self) -> str:
        return self.kind.value

    @classmethod
    def from_kind(cls, kind: PunctuatorKind) -> Punctuator:
        return cls(kind, SrcSpan.empty())


@dataclass
class IntLiteral:
    raw: str
    span: SrcSpan
    value: int
    suffix: str | None

    def __repr__(self) -> str:
        return self.raw


@dataclass
class FloatLiteral:
    raw: str
    span: SrcSpan
    value: float
    suffix: str | None

    def __repr__(self) -> str:
        return self.raw


@dataclass
class CharLiteral:
    raw: str
    span: SrcSpan
    value: str

    def __repr__(self) -> str:
        return self.raw


@dataclass
class StrLiteral:
    raw: str
    span: SrcSpan
    value: str

    def __repr__(self) -> str:
        return self.raw


@dataclass
class BoolLiteral:
    raw: str
    span: SrcSpan
    value: bool

    def __repr__(self) -> str:
        return self.raw


@dataclass
class FStrStart:
    span: SrcSpan
    raw: str

    def __repr__(self) -> str:
        return self.raw


@dataclass
class FStrLiteral:
    span: SrcSpan
    value: str

    def __repr__(self) -> str:
        return f"\"{self.value}\""


@dataclass
class FStrExprBegin:
    span: SrcSpan

    def __repr__(self) -> str:
        return "{"


@dataclass
class FStrExprEnd:
    span: SrcSpan

    def __repr__(self) -> str:
        return "}"


@dataclass
class FStrEnd:
    span: SrcSpan

    def __repr__(self) -> str:
        return "\""


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


def __parse_escape_sequence(raw: str) -> tuple[str, str]:
    if not raw:
        raise ValueError("Invalid escape sequence at end of literal")
    escape_char, raw = raw[0], raw[1:]
    if escape_char in ESCAPE_SEQUENCES:
        return ESCAPE_SEQUENCES[escape_char], raw
    if escape_char in {"x", "X"}:
        # hex character literal
        hex_digits, raw = raw[:2], raw[2:]
        if len(hex_digits) != 2:
            raise ValueError(f"Invalid hex escape sequence: \\{escape_char} must be followed by 2 hex digits")
        if any(c not in BASE_DIGITS[16] for c in hex_digits):
            raise ValueError(f"Invalid hex escape sequence: \\{escape_char}{hex_digits}")
        return chr(int(hex_digits, 16)), raw
    if escape_char in {"u", "U"}:
        # unicode character literal
        if raw[0] != "{":
            raise ValueError(f"Invalid unicode escape sequence: \\{escape_char} must be followed by {{")
        raw = raw[1:]  # skip the '{'
        unicode_digits = ""
        while raw and raw[0] != "}":
            unicode_digits += raw[0]
            raw = raw[1:]
        if not raw:
            raise ValueError("Invalid unicode escape sequence: missing closing }")
        if any(c not in BASE_DIGITS[16] for c in unicode_digits):
            raise ValueError(f"Invalid unicode escape sequence: \\{escape_char}{{{unicode_digits}}}")
        return chr(int(unicode_digits, 16)), raw[1:]  # skip the closing '}'
    raise ValueError(f"Invalid escape sequence: \\{escape_char}")


def parse_string_value(raw: str) -> str:
    # handle escape sequences
    result = ""
    raw = raw[1:-1]  # skip the opening and closing quotes
    while raw:
        c, raw = raw[0], raw[1:]
        if c == "\\":
            c, raw = __parse_escape_sequence(raw)
        result += c
    return result


def parse_char_value(raw: str) -> str:
    # handle escape sequences
    result = ""
    raw = raw[1:]  # skip the opening quote
    c, raw = raw[0], raw[1:]
    if c == "\\":
        c, raw = __parse_escape_sequence(raw)
    result += c
    raw = raw[1:]  # skip the closing quote
    if raw:
        raise ValueError(f"Char literal can only contain one character, but got {result} in {raw}")
    return result


def parse_byte_value(raw: str) -> int:
    # handle escape sequences
    result = 0
    raw = raw[2:]  # skip the b and opening quote
    if not raw:
        raise ValueError("Empty byte literal")
    c, raw = raw[0], raw[1:]
    if c == "\\":
        c, raw = __parse_escape_sequence(raw)
    result = ord(c)
    raw = raw[1:]  # skip the closing quote
    if raw:
        raise ValueError(f"Byte literal can only contain one character, but got {c} in {raw}")
    if result > 255:
        raise ValueError(f"Byte literal must be in range 0..255, but got {result}")
    return result


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


def parse_integer_value(raw: str) -> tuple[int, str | None]:
    # handle prefixes
    if raw.startswith(("0b", "0B")):
        base = 2
        raw = raw[2:]
    elif raw.startswith(("0o", "0O")):
        base = 8
        raw = raw[2:]
    elif raw.startswith(("0x", "0X")):
        base = 16
        raw = raw[2:]
    else:
        base = 10

    # handle suffixes
    suffix = None
    for int_suffix in INT_SUFFIXES:
        if raw.endswith(int_suffix):
            suffix = int_suffix
            raw = raw[:-len(int_suffix)]
            break

    # remove underscores
    raw = raw.replace("_", "")

    if any(c not in BASE_DIGITS[base] for c in raw):
        raise ValueError(f"Invalid integer literal: {raw} is not a valid base {base} integer")

    value = int(raw, base)

    # check int range based on suffix
    if suffix is not None:
        max_value = INT_SUFFIXES[suffix]
        if value > max_value:
            raise ValueError(f"Integer literal {raw} exceeds maximum value for type {suffix}")

    return value, suffix


def parse_float_value(raw: str) -> tuple[float, str | None]:
    # handle prefixes
    if raw.startswith(("0b", "0B")):
        raise ValueError("Binary float literals are not supported")
    if raw.startswith(("0o", "0O")):
        raise ValueError("Octal float literals are not supported")
    if raw.startswith(("0x", "0X")):
        base = 16
        raw = raw[2:]
    else:
        base = 10

    # handle suffixes
    suffix = None
    for float_suffix in FLOAT_SUFFIXES:
        if raw.endswith(float_suffix):
            suffix = float_suffix
            raw = raw[:-len(float_suffix)]
            break

    # remove underscores
    raw = raw.replace("_", "")

    if any(c not in BASE_DIGITS[base] and c not in ".eEpP+-" for c in raw):
        raise ValueError(f"Invalid float literal: {raw} is not a valid base {base} float")

    if base == 16:
        value = float.fromhex(raw)
    else:
        value = float(raw)

    return value, suffix


Literal: TypeAlias = IntLiteral | FloatLiteral | CharLiteral | StrLiteral | BoolLiteral

Token: TypeAlias = Keyword | Identifier | Punctuator | Literal | FStrStart | FStrLiteral | FStrExprBegin | FStrExprEnd | FStrEnd
