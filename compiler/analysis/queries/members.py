"""Receiver-based member candidates using the shared semantic type rules."""

from __future__ import annotations

from pathlib import Path
from compiler.analysis.queries.navigation import Navigator
from compiler.analysis.ty import ty as Type
from compiler.frontend.lex import token as Tok
from compiler.analysis.queries.completion_position import tokens_before, visible_here
from compiler.analysis.queries.completion_candidates import from_callable
from compiler.analysis.queries.completion_model import Completion, CompletionKind

def member_items(
    navigator: Navigator, path: Path, row: int, col: int
) -> tuple[Completion, ...]:
    """Fields and methods of the expression to the left of the dot.

    The receiver's type comes from the recorded reference for that expression, so
    the list matches the *static* type the analysis computed rather than what the
    name looks like (member completion follows the receiver's static type).
    """
    view = navigator.view
    if not view.has_types:
        return ()
    tokens = view.tokens_of(path)
    before = tokens_before(tokens, row, col)
    if not before or not isinstance(before[-1], Tok.Punctuator):
        return ()
    dot = before[-1]
    if dot.kind != Tok.PunctuatorKind.Dot:
        return ()
    previous = before[-2] if len(before) > 1 else None
    expression = None if previous is None else view.expression_ending_at(path, previous.span.end.row, previous.span.end.col)
    if expression is not None:
        type_id, static = expression.type_id, expression.is_static
    else:
        return ()
    receivers = (type_id,) if static else view.receiver_types(type_id)
    resolved = type_id
    if not static:
        while isinstance(view.type_of(resolved), (Type.PointerType, Type.RefType)):
            inner = view.deref_type(resolved)
            if inner is None or inner == resolved:
                break
            resolved = inner
    items: dict[str, Completion] = {}
    resolved_ty = view.type_of(resolved)
    if isinstance(resolved_ty, Type.StructType) and not static:
        for field in view.fields_of(resolved):
            if field.access_mode is Type.AccessMode.Private and not visible_here(
                field.span, path, navigator
            ):
                continue
            items.setdefault(
                field.name,
                Completion(
                    label=field.name,
                    kind=CompletionKind.FIELD,
                    detail=view.type_name(field.type_id),
                ),
            )
    methods: dict[str, set[int]] = {}
    for receiver in receivers:
        for name, method_id in view.methods_of(receiver):
            method_ty = view.type_of(method_id)
            if isinstance(method_ty, Type.MethodType) and method_ty.custom_def.is_static != static:
                continue
            methods.setdefault(name, set()).add(method_id)
    for name, candidates in methods.items():
        item = (from_callable(view, name, next(iter(candidates)), CompletionKind.METHOD)
                if len(candidates) == 1 else Completion(label=name, kind=CompletionKind.METHOD))
        items.setdefault(name, item)
    if static and isinstance(resolved_ty, Type.EnumType):
        for variant in view.variants_of(resolved):
            items.setdefault(
                variant.name,
                Completion(
                    label=variant.name,
                    kind=CompletionKind.VARIANT,
                    detail=None
                    if variant.payload_type is None
                    else view.type_name(variant.payload_type),
                ),
            )
    return tuple(items.values())
