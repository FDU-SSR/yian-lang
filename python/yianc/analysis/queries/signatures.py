"""Call-site signatures from reliable targets and receiver facts."""

from __future__ import annotations

from pathlib import Path
from yianc.analysis.queries.context import QueryContext
from yianc.analysis.queries.navigation import Navigator
from yianc.frontend.lex import token as Tok
from yianc.analysis.queries.completion_position import call_at
from yianc.analysis.queries.completion_candidates import callable_type_id, signature_of
from yianc.analysis.queries.completion_model import SignatureInfo
from yianc.analysis.queries.availability import Completeness, QueryResult, UnavailableReason


def query_signature_help(
    context: QueryContext, path: Path, row: int, col: int, *,
    std_root: Path | None = None, navigator: Navigator | None = None,
) -> QueryResult[SignatureInfo]:
    """A known declaration signature remains useful while its arguments are incomplete."""
    navigator = navigator or Navigator(context, std_root=std_root)
    state = context.documents.get(navigator.canonical_path(path))
    if state is not None and state.lexical_stop is not None and (row, col) >= (state.lexical_stop.row, state.lexical_stop.col):
        return QueryResult(reason=UnavailableReason.SYNTAX)
    value = __signature_help(context, path, row, col, std_root=std_root, navigator=navigator)
    if value is None:
        return QueryResult(reason=UnavailableReason.DECLARATION)
    return QueryResult(value=value, completeness=Completeness.COMPLETE)

def signature_help(
    context: QueryContext,
    path: Path,
    row: int,
    col: int,
    *,
    std_root: Path | None = None,
    navigator: Navigator | None = None,
) -> SignatureInfo | None:
    """Compatibility adapter for callers using the optional signature payload."""
    return query_signature_help(context, path, row, col, std_root=std_root, navigator=navigator).value


def __signature_help(
    context: QueryContext, path: Path, row: int, col: int, *,
    std_root: Path | None = None, navigator: Navigator | None = None,
) -> SignatureInfo | None:
    """Signature help for the call the position is inside, if any."""
    navigator = navigator or Navigator(context, std_root=std_root)
    tokens = navigator.tokens_of(path)
    call = call_at(tokens, row, col)
    if call is None:
        return None
    callee_span, active_parameter = call
    reference = navigator.reference_starting_at(
        path, callee_span.start.row, callee_span.start.col
    )
    type_id = None if reference is None else callable_type_id(reference.target, reference.expression_type)
    if type_id is None:
        # A call's receiver is checked before its arguments; unfinished or invalid
        # arguments must not erase an independently resolved method signature.
        index = next((index for index, token in enumerate(tokens) if token.span == callee_span), None)
        if index is not None and index >= 2:
            dot = tokens[index - 1]
            receiver = tokens[index - 2]
            if isinstance(dot, Tok.Punctuator) and dot.kind == Tok.PunctuatorKind.Dot:
                fact = navigator.view.expression_ending_at(path, receiver.span.end.row, receiver.span.end.col)
                if fact is not None:
                    token = tokens[index]
                    if isinstance(token, Tok.Identifier):
                        receivers = (fact.type_id,) if fact.is_static else navigator.view.receiver_types(fact.type_id)
                        for receiver_type in receivers:
                            candidates = {method_id for name, method_id in navigator.view.methods_of(receiver_type)
                                          if name == token.name and navigator.view.is_static_callable(method_id) == fact.is_static}
                            if candidates:
                                if len(candidates) == 1:
                                    type_id = candidates.pop()
                                break
    signature = signature_of(navigator.view, type_id)
    if signature is None:
        return None
    return SignatureInfo(signatures=(signature,), active_parameter=active_parameter)
