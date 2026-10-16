"""Owned completion and signature payloads, independent of snapshot success."""

from __future__ import annotations

from enum import Enum
from dataclasses import dataclass
from yianc.analysis.queries.availability import Completeness
from yianc.analysis.queries.index import DeclarationKind
from yianc.frontend.lex import token as Tok
from yianc.frontend.lex.position import SrcSpan

class CompletionKind(Enum):
    """What an item is; the LSP layer maps these onto protocol kinds."""

    FUNCTION = "function"
    METHOD = "method"
    FIELD = "field"
    VARIANT = "variant"
    STRUCT = "struct"
    ENUM = "enum"
    TRAIT = "trait"
    ALIAS = "alias"
    CONSTANT = "constant"
    VARIABLE = "variable"
    PARAMETER = "parameter"
    TYPE_PARAMETER = "type_parameter"
    MODULE = "module"
    PACKAGE = "package"
    PRIMITIVE = "primitive"
    #: A name offered without the analysis behind it (see :func:`lexical_items`).
    TEXT = "text"


@dataclass(frozen=True)
class Completion:
    """One candidate: what it is, and the facts an editor shows for it."""

    label: str
    kind: CompletionKind
    #: Rendered type or signature, shown next to the label.
    detail: str | None = None
    #: Parameter names of a callable, in order.  How an editor inserts them — a
    #: snippet with placeholders, a plain name, a signature preview — is its own
    #: decision; the compiler reports the names it knows.
    parameters: tuple[str, ...] = ()


@dataclass(frozen=True)
class CompletionResult:
    """The candidates, plus the text range they replace."""

    items: tuple[Completion, ...] = ()
    #: The identifier (or dotted path) being typed, which the client replaces.
    span: SrcSpan | None = None
    completeness: Completeness = Completeness.COMPLETE


@dataclass(frozen=True)
class Signature:
    """One callable's parameters, for signature help."""

    label: str
    parameters: tuple[tuple[str, str], ...]  # (name, rendered type)


@dataclass(frozen=True)
class SignatureInfo:
    """Signature help for one call site, with the argument being written."""

    signatures: tuple[Signature, ...]
    active_parameter: int
    active_signature: int = 0


CONTEXT_VALUE = "value"


CONTEXT_MEMBER = "member"


CONTEXT_TYPE = "type"


CONTEXT_IMPORT_PATH = "import_path"


CONTEXT_IMPORT_NAME = "import_name"


EXPRESSION_BOUNDARIES = frozenset(
    {
        Tok.PunctuatorKind.Equal,
        Tok.PunctuatorKind.LParen,
        Tok.PunctuatorKind.Comma,
        Tok.PunctuatorKind.Semicolon,
        Tok.PunctuatorKind.LBrace,
        Tok.PunctuatorKind.RBrace,
        Tok.PunctuatorKind.Colon,
        Tok.PunctuatorKind.FatArrow,
        Tok.PunctuatorKind.Plus,
        Tok.PunctuatorKind.Minus,
        Tok.PunctuatorKind.Star,
        Tok.PunctuatorKind.Slash,
    }
)


TYPE_INTRODUCERS = frozenset(
    {Tok.PunctuatorKind.Colon, Tok.PunctuatorKind.Arrow, Tok.PunctuatorKind.LAngle}
)


KIND_OF_DECLARATION = {
    DeclarationKind.FUNCTION: CompletionKind.FUNCTION,
    DeclarationKind.METHOD: CompletionKind.METHOD,
    DeclarationKind.FIELD: CompletionKind.FIELD,
    DeclarationKind.VARIANT: CompletionKind.VARIANT,
    DeclarationKind.STRUCT: CompletionKind.STRUCT,
    DeclarationKind.ENUM: CompletionKind.ENUM,
    DeclarationKind.TRAIT: CompletionKind.TRAIT,
    DeclarationKind.ALIAS: CompletionKind.ALIAS,
    DeclarationKind.CONSTANT: CompletionKind.CONSTANT,
    DeclarationKind.VARIABLE: CompletionKind.VARIABLE,
    DeclarationKind.PARAMETER: CompletionKind.PARAMETER,
}


IMPORTABLE_KINDS = frozenset(
    {
        DeclarationKind.FUNCTION,
        DeclarationKind.STRUCT,
        DeclarationKind.ENUM,
        DeclarationKind.TRAIT,
        DeclarationKind.ALIAS,
        DeclarationKind.CONSTANT,
    }
)


PRIMITIVE_TYPE_NAMES = (
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
)


KIND_BY_TYPE = {
    "StructType": CompletionKind.STRUCT,
    "EnumType": CompletionKind.ENUM,
    "TraitType": CompletionKind.TRAIT,
}
