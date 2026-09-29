"""Versioned frontend observations for compiler differential checks."""

from __future__ import annotations

import json
import struct
from dataclasses import fields, is_dataclass
from enum import Enum
from pathlib import Path
from typing import TypeAlias, TypedDict, cast

from compiler.analysis.diagnostics import Stage, diagnostic_from_error
from compiler.analysis.passes.desugar import Desugar
from compiler.frontend.lex import token as Tok
from compiler.frontend.lex.lexer import LexError, Lexer
from compiler.frontend.lex.position import SrcPosition, SrcSpan
from compiler.frontend.parse import ast as AST
from compiler.frontend.parse import ast_type as ASTTy
from compiler.frontend.parse.error import ParseError
from compiler.frontend.parse.parser import Parser


class TokenView(TypedDict):
    kind: str
    raw: str
    value: str
    suffix: str
    bits: str
    start: list[int]
    end: list[int]


class FileView(TypedDict):
    path: str
    tokens: list[TokenView]


class AstFileView(TypedDict):
    path: str
    ast: JsonValue


class DiagnosticView(TypedDict, total=False):
    stage: str
    path: str
    code: str
    severity: str
    message: str
    start: list[int]
    end: list[int]


class Snapshot(TypedDict):
    version: int
    phase: str
    files: list[FileView]
    diagnostic: DiagnosticView | None


JsonValue: TypeAlias = str | int | bool | None | list["JsonValue"] | dict[str, "JsonValue"]
AstInput: TypeAlias = (
    AST.Program | AST.ProgramItem | AST.Expr | AST.Pattern | AST.ASTType | AST.GenericParam
    | AST.Attr | AST.VarInfo | AST.PatternParam | AST.FieldInfo | AST.VariantInfo
    | AST.MethodDecl | AST.MethodDef | AST.MatchArm | AST.Arg | AST.CaptureItem
    | AST.FieldPattern | AST.LetCondition | AST.ExternFuncDecl | ASTTy.ConstExpr
    | Tok.Token | SrcPosition | SrcSpan | Enum | str | int | float | bool | None
    | list["AstInput"] | tuple["AstInput", ...]
)


def __raw(text: str, span: SrcSpan) -> str:
    starts = [0]
    for index, char in enumerate(text):
        if char == "\n":
            starts.append(index + 1)
    if span.start.row >= len(starts) or span.end.row >= len(starts):
        return ""
    return text[starts[span.start.row] + span.start.col:starts[span.end.row] + span.end.col]


def __token_view(token: Tok.Token, text: str) -> TokenView:
    raw = __raw(text, token.span)
    kind: str
    value = ""
    suffix = ""
    bits = "0"
    match token:
        case Tok.Keyword():
            kind = "Keyword"
            value = token.kind.value
        case Tok.Identifier():
            kind = "Identifier"
            value = token.name
        case Tok.Punctuator():
            kind = "EndOfFile" if token.kind is Tok.PunctuatorKind.EOF else "Punctuator"
            value = token.kind.value
        case Tok.IntLiteral():
            kind = "Integer"
            value = str(token.value)
            suffix = token.suffix or ""
        case Tok.FloatLiteral():
            kind = "Float"
            bits = str(struct.unpack(">Q", struct.pack(">d", token.value))[0])
            suffix = token.suffix or ""
        case Tok.CharLiteral():
            kind = "Character"
            value = token.value
        case Tok.StrLiteral():
            kind = "StringLiteral"
            value = token.value
        case Tok.BoolLiteral():
            kind = "Boolean"
            value = token.raw
        case Tok.FStrStart():
            kind = "FStrStart"
            raw = token.raw
            value = token.raw
        case Tok.FStrLiteral():
            kind = "FStrLiteral"
            value = token.value
        case Tok.FStrExprBegin():
            kind = "FStrExprBegin"
            raw = "{"
        case Tok.FStrExprEnd():
            kind = "FStrExprEnd"
            raw = "}"
        case Tok.FStrEnd():
            kind = "FStrEnd"
            raw = '"'
    return {
        "kind": kind,
        "raw": raw,
        "value": value,
        "suffix": suffix,
        "bits": bits,
        "start": [token.span.start.row, token.span.start.col],
        "end": [token.span.end.row, token.span.end.col],
    }


def observe_tokens(paths: list[Path]) -> tuple[int, str]:
    """Observe the first lexical failure or the complete ordered token stream."""
    snapshot: Snapshot = {"version": 1, "phase": "tokens", "files": [], "diagnostic": None}
    for path in paths:
        try:
            source = path.read_text()
        except (OSError, UnicodeError) as error:
            snapshot["diagnostic"] = {"stage": "io", "path": str(path), "message": str(error)}
            return 1, json.dumps(snapshot, ensure_ascii=False, separators=(",", ":"))
        lexer = Lexer(path, source)
        try:
            lexer.lex()
        except LexError as error:
            diagnostic = diagnostic_from_error(error, stage=Stage.LEX)
            snapshot["diagnostic"] = {
                "stage": "lex", "path": str(path), "code": diagnostic.code,
                "severity": "error",
                "message": diagnostic.message,
                "start": [diagnostic.span.start.row, diagnostic.span.start.col],
                "end": [diagnostic.span.end.row, diagnostic.span.end.col],
            }
            return 1, json.dumps(snapshot, ensure_ascii=False, separators=(",", ":"))
        snapshot["files"].append({
            "path": str(path),
            "tokens": [__token_view(token, source) for token in lexer.export()],
        })
    return 0, json.dumps(snapshot, ensure_ascii=False, separators=(",", ":"))


def __ast_value(value: AstInput) -> JsonValue:
    if value is None or isinstance(value, (str, bool)):
        return value
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return str(struct.unpack(">Q", struct.pack(">d", value))[0])
    if isinstance(value, SrcPosition):
        return [value.row, value.col]
    if isinstance(value, (list, tuple)):
        return [__ast_value(element) for element in value]
    if isinstance(value, Enum):
        return value.value if isinstance(value.value, str) else str(value)
    if isinstance(value, SrcSpan):
        return {"start": [value.start.row, value.start.col], "end": [value.end.row, value.end.col]}
    if not is_dataclass(value):
        raise TypeError(f"Cannot observe {type(value).__name__}")
    span = value.span
    field_values: list[JsonValue] = []
    if isinstance(value, Tok.FloatLiteral):
        field_values.append(["raw", value.raw])
        field_values.append(["bits", __ast_value(value.value)])
        field_values.append(["suffix", value.suffix or ""])
    elif isinstance(value, Tok.IntLiteral):
        field_values.append(["raw", value.raw])
        field_values.append(["value", str(value.value)])
        field_values.append(["suffix", value.suffix or ""])
    else:
        for item in fields(value):
            if item.name == "span":
                continue
            field_values.append([item.name, __ast_value(cast(AstInput, getattr(value, item.name)))])
    return {
        "tag": type(value).__name__,
        "start": [span.start.row, span.start.col],
        "end": [span.end.row, span.end.col],
        "fields": field_values,
    }


def observe_ast(paths: list[Path], *, desugared: bool) -> tuple[int, str]:
    phase = "desugared" if desugared else "ast"
    files: list[AstFileView] = []
    diagnostic: DiagnosticView | None = None
    for path in paths:
        try:
            source = path.read_text()
        except (OSError, UnicodeError) as error:
            diagnostic = {"stage": "io", "path": str(path), "message": str(error)}
            break
        lexer = Lexer(path, source)
        try:
            lexer.lex()
            program = Parser(lexer.export()).parse()
            if desugared:
                Desugar(program).run()
        except (LexError, ParseError) as error:
            stage = Stage.LEX if isinstance(error, LexError) else Stage.PARSE
            detail = diagnostic_from_error(error, stage=stage)
            diagnostic = {
                "stage": stage.value, "path": str(path), "code": detail.code,
                "severity": "error",
                "message": detail.message,
                "start": [detail.span.start.row, detail.span.start.col],
                "end": [detail.span.end.row, detail.span.end.col],
            }
            break
        files.append({"path": str(path), "ast": __ast_value(program)})
    result: dict[str, JsonValue] = {
        "version": 1, "phase": phase, "files": cast(JsonValue, files),
        "diagnostic": cast(JsonValue, diagnostic),
    }
    return (1 if diagnostic is not None else 0), json.dumps(result, ensure_ascii=False, separators=(",", ":"))
