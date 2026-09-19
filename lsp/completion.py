"""Completion and signature help as the protocol wants them.

The compiler side decides what the candidates are
(:mod:`compiler.analysis.completion`); this module turns them into protocol
items — kinds, snippet text, and the range the client replaces — and the
signature payload for the call being written.
"""

from __future__ import annotations

from lsprotocol import types

from compiler.analysis.completion import (
    Completion,
    CompletionKind,
    CompletionResult,
    SignatureInfo,
)
from compiler.analysis.navigation import Navigator
from compiler.analysis.positions import to_lsp_range
from compiler.frontend.lex.position import SrcSpan

__all__ = ["completion_list", "signature_help"]

KIND_OF_ITEM = {
    CompletionKind.FUNCTION: types.CompletionItemKind.Function,
    CompletionKind.METHOD: types.CompletionItemKind.Method,
    CompletionKind.FIELD: types.CompletionItemKind.Field,
    CompletionKind.VARIANT: types.CompletionItemKind.EnumMember,
    CompletionKind.STRUCT: types.CompletionItemKind.Struct,
    CompletionKind.ENUM: types.CompletionItemKind.Enum,
    CompletionKind.TRAIT: types.CompletionItemKind.Interface,
    CompletionKind.ALIAS: types.CompletionItemKind.Class,
    CompletionKind.VARIABLE: types.CompletionItemKind.Variable,
    CompletionKind.PARAMETER: types.CompletionItemKind.Variable,
    CompletionKind.MODULE: types.CompletionItemKind.Module,
    CompletionKind.PACKAGE: types.CompletionItemKind.Module,
    CompletionKind.PRIMITIVE: types.CompletionItemKind.Keyword,
    CompletionKind.TEXT: types.CompletionItemKind.Text,
}


def completion_list(
    result: CompletionResult, navigator: Navigator
) -> types.CompletionList:
    """The candidates as a protocol completion list.

    Each item carries a `text_edit` over the text being replaced, which is what
    makes completing inside a dotted path (an import, a member access) replace
    the right span instead of the client's guess at the current word.
    """
    items: list[types.CompletionItem] = []
    rendered_range = __range(result.span, navigator)
    for item in result.items:
        inserted, snippet = __insert_text(item)
        items.append(
            types.CompletionItem(
                label=item.label,
                kind=KIND_OF_ITEM.get(item.kind, types.CompletionItemKind.Text),
                detail=item.detail,
                insert_text_format=(
                    types.InsertTextFormat.Snippet
                    if snippet
                    else types.InsertTextFormat.PlainText
                ),
                # `insertText` carries the snippet whenever the range is unknown;
                # `textEdit` takes precedence when there is one.
                insert_text=inserted,
                text_edit=None
                if rendered_range is None
                else types.TextEdit(range=rendered_range, new_text=inserted),
            )
        )
    return types.CompletionList(is_incomplete=False, items=items)


def signature_help(info: SignatureInfo) -> types.SignatureHelp:
    """The callable's signature, with the argument being written marked active."""
    signatures: list[types.SignatureInformation] = [
        types.SignatureInformation(
            label=signature.label,
            parameters=[
                types.ParameterInformation(label=f"{name}: {type_name}")
                for name, type_name in signature.parameters
            ],
        )
        for signature in info.signatures
    ]
    return types.SignatureHelp(
        signatures=signatures,
        active_signature=info.active_signature,
        active_parameter=info.active_parameter,
    )


def __insert_text(item: Completion) -> tuple[str, bool]:
    """What the editor inserts for *item*, and whether it has placeholders.

    Snippet syntax belongs to the client: the compiler reports the candidate and
    the parameter names it resolved, and this module decides that accepting a
    callable should put the caret in its first parameter rather than leave a bare
    name behind (补全项的插入文本与参数占位符).
    """
    if not item.parameters:
        return item.label, False
    placeholders = ", ".join(
        f"${{{index}:{name}}}" for index, name in enumerate(item.parameters, start=1)
    )
    return f"{item.label}({placeholders})", True


def __range(span: SrcSpan | None, navigator: Navigator) -> types.Range | None:
    if span is None:
        return None
    rendered = to_lsp_range(span, navigator.text_of(span.path))
    return types.Range(
        start=types.Position(
            line=rendered["start"]["line"], character=rendered["start"]["character"]
        ),
        end=types.Position(line=rendered["end"]["line"], character=rendered["end"]["character"]),
    )
