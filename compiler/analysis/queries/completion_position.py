"""Token and caret context; no type inference or analysis is performed here."""

from __future__ import annotations

from pathlib import Path
from compiler.analysis.facts.names import NameRef
from compiler.analysis.queries.navigation import Navigator
from compiler.analysis.symbol.symbol import AliasSymbol, Symbol, SymbolKind
from compiler.frontend.lex import token as Tok
from compiler.frontend.lex.position import SrcPosition, SrcSpan
from compiler.analysis.queries.completion_model import CONTEXT_IMPORT_NAME, CONTEXT_IMPORT_PATH, CONTEXT_MEMBER, CONTEXT_TYPE, CONTEXT_VALUE, TYPE_INTRODUCERS

def prefix_at(
    path: Path, text: str, row: int, col: int
) -> tuple[str, SrcSpan | None]:
    """The identifier ending at the caret, and the span it replaces."""
    line = __line(text, row)
    index = min(max(col, 0), len(line))
    start = index
    while start > 0 and __identifier_char(line[start - 1]):
        start -= 1
    prefix = line[start:index]
    if not prefix:
        return "", None
    return prefix, SrcSpan(position(path, row, start), position(path, row, index))


def receiver_type(reference: NameRef) -> tuple[int | None, bool]:
    """The receiver's type, and whether the access is static.

    A reference to a type declaration is static: `Pair<Meters>.of` reaches
    static methods, not fields. The recorded expression type includes applied
    generic arguments even when the declaration is a transparent alias.
    """
    target = reference.target
    if isinstance(target, int):
        return target, True
    if isinstance(target, AliasSymbol):
        return reference.expression_type, True
    if isinstance(target, Symbol):
        if target.kind is SymbolKind.Type:
            return reference.expression_type or target.type_id, True
        return reference.expression_type, False
    return reference.expression_type, False


def context_at(tokens: tuple[Tok.Token, ...], row: int, col: int) -> str:
    """Classify the position from the tokens before it."""
    before = tokens_before(tokens, row, col)
    if not before:
        return CONTEXT_VALUE
    statement = statement_tokens(before)
    import_index = __last_index(statement, Tok.KeywordKind.Import)
    from_index = __last_index(statement, Tok.KeywordKind.From)
    if from_index is not None and import_index is None:
        # Still writing the path: `from sample.|`
        return CONTEXT_IMPORT_PATH
    if import_index is not None:
        if from_index is not None:
            # `from a.b import <here>` names things; `from a.<here>` a path.
            last_before = statement[-1].span.start.col
            if last_before < statement[import_index].span.start.col:
                return CONTEXT_IMPORT_PATH
            return CONTEXT_IMPORT_NAME
        return CONTEXT_IMPORT_PATH
    last = before[-1]
    if isinstance(last, Tok.Punctuator):
        if last.kind == Tok.PunctuatorKind.Dot:
            return CONTEXT_MEMBER
        if last.kind in TYPE_INTRODUCERS:
            return CONTEXT_TYPE
    return CONTEXT_VALUE


def tokens_before(tokens: tuple[Tok.Token, ...], row: int, col: int) -> list[Tok.Token]:
    # The token tuple already belongs to one path, selected by the snapshot view.
    return [
        token
        for token in tokens
        if (token.span.end.row, token.span.end.col) <= (row, col)
    ]


def statement_tokens(before: list[Tok.Token]) -> list[Tok.Token]:
    """The tokens of the statement the caret is in (back to the last ``;``)."""
    for index in range(len(before) - 1, -1, -1):
        token = before[index]
        if isinstance(token, Tok.Punctuator) and token.kind == Tok.PunctuatorKind.Semicolon:
            return before[index + 1 :]
    return before


def __last_index(tokens: list[Tok.Token], keyword: Tok.KeywordKind) -> int | None:
    for index in range(len(tokens) - 1, -1, -1):
        token = tokens[index]
        if isinstance(token, Tok.Keyword) and token.kind == keyword:
            return index
    return None


def call_at(
    tokens: tuple[Tok.Token, ...], row: int, col: int
) -> tuple[SrcSpan, int] | None:
    """The callee's name span and the argument index, for the call being written.

    Commas inside aggregates, generic arguments and nested calls do not advance
    the surrounding call. A callee must immediately precede its opening token.
    """
    pairs = {
        Tok.PunctuatorKind.RParen: Tok.PunctuatorKind.LParen,
        Tok.PunctuatorKind.RBracket: Tok.PunctuatorKind.LBracket,
        Tok.PunctuatorKind.RBrace: Tok.PunctuatorKind.LBrace,
        Tok.PunctuatorKind.RAngle: Tok.PunctuatorKind.LAngle,
    }
    stack: list[Tok.PunctuatorKind] = []
    arguments = 0
    open_index: int | None = None
    before = tokens_before(tokens, row, col)
    for index in range(len(before) - 1, -1, -1):
        token = before[index]
        if not isinstance(token, Tok.Punctuator):
            continue
        if token.kind in pairs:
            stack.append(pairs[token.kind])
        elif token.kind in pairs.values():
            if not stack and token.kind == Tok.PunctuatorKind.LParen:
                open_index = index
                break
            if stack and stack[-1] == token.kind:
                stack.pop()
        elif token.kind == Tok.PunctuatorKind.Comma and not stack:
            arguments += 1
    if open_index is None:
        return None
    index = open_index - 1
    previous = before[index] if index >= 0 else None
    if isinstance(previous, Tok.Punctuator) and previous.kind == Tok.PunctuatorKind.RAngle:
        depth = 1
        index -= 1
        while index >= 0:
            token = before[index]
            if isinstance(token, Tok.Punctuator):
                if token.kind == Tok.PunctuatorKind.RAngle:
                    depth += 1
                elif token.kind == Tok.PunctuatorKind.LAngle:
                    depth -= 1
                    if depth == 0:
                        index -= 1
                        break
            index -= 1
    if index >= 0 and isinstance(before[index], Tok.Identifier):
        return before[index].span, arguments
    return None


def __line(text: str, row: int) -> str:
    lines = text.splitlines()
    return lines[row] if 0 <= row < len(lines) else ""


def __identifier_char(character: str) -> bool:
    return character.isalnum() or character == "_"


def position(path: Path, row: int, col: int) -> SrcPosition:
    return SrcPosition(row, col, path)


def visible_here(span: SrcSpan | None, path: Path, navigator: Navigator) -> bool:
    """True when a private declaration is in the file being completed."""
    return span is not None and navigator.canonical_path(span.path) == navigator.canonical_path(path)
