"""Current-snapshot completion availability and compatibility adapters."""

from __future__ import annotations

from pathlib import Path
from compiler.analysis.queries.context import QueryContext
from compiler.analysis.queries.availability import Completeness, QueryResult, UnavailableReason
from compiler.analysis.queries.navigation import Navigator
from compiler.analysis.queries.completion_position import context_at, position, prefix_at, tokens_before
from compiler.analysis.queries.completion_candidates import import_name_items, import_path_items, lexical_items, type_items, value_items
from compiler.analysis.queries.members import member_items
from compiler.analysis.queries.completion_model import CompletionResult, CONTEXT_IMPORT_NAME, CONTEXT_IMPORT_PATH, CONTEXT_MEMBER, CONTEXT_TYPE, CONTEXT_VALUE

def query_completion(
    context: QueryContext, path: Path, row: int, col: int, *,
    std_root: Path | None = None, navigator: Navigator | None = None,
) -> QueryResult[CompletionResult]:
    """Report availability from the dependencies of this position, never build success."""
    navigator = navigator or Navigator(context, std_root=std_root)
    state = context.documents.get(navigator.canonical_path(path))
    if state is not None and state.lexical_stop is not None and (row, col) >= (state.lexical_stop.row, state.lexical_stop.col):
        return QueryResult(reason=UnavailableReason.SYNTAX)
    _, span = prefix_at(path, navigator.text_of(path), row, col)
    at = span.start if span is not None else position(path, row, col)
    tokens = navigator.tokens_of(path)
    kind = context_at(tokens, at.row, at.col)
    if kind == CONTEXT_MEMBER and navigator.view.editor_recovery:
        before = tokens_before(tokens, at.row, at.col)
        receiver = before[-2] if len(before) > 1 else None
        if receiver is None or navigator.view.expression_ending_at(path, receiver.span.end.row, receiver.span.end.col) is None:
            return QueryResult(reason=UnavailableReason.TYPE)
    result = __complete(context, path, row, col, std_root=std_root, navigator=navigator)
    return QueryResult(value=result, completeness=result.completeness)


def complete(
    context: QueryContext, path: Path, row: int, col: int, *,
    std_root: Path | None = None, navigator: Navigator | None = None,
) -> CompletionResult:
    """Compatibility adapter: an unavailable query has an empty, partial result."""
    result = query_completion(context, path, row, col, std_root=std_root, navigator=navigator)
    if result.value is not None:
        return result.value
    _, span = prefix_at(path, context.result.sources.get(path, ""), row, col)
    return CompletionResult(span=span, completeness=Completeness.PARTIAL)


def __complete(
    context: QueryContext,
    path: Path,
    row: int,
    col: int,
    *,
    std_root: Path | None = None,
    navigator: Navigator | None = None,
) -> CompletionResult:
    """Candidates for the position ``(row, col)`` in *path*.

    *row* and *col* are 0-based; columns count code points.
    """
    navigator = navigator or Navigator(context, std_root=std_root)
    text = navigator.text_of(path)
    tokens = navigator.tokens_of(path)
    prefix, prefix_span = prefix_at(path, text, row, col)
    context_row, context_col = row, col
    if prefix_span is not None:
        context_row, context_col = prefix_span.start.row, prefix_span.start.col
    completion_context = context_at(tokens, context_row, context_col)
    if not navigator.view.has_symbols(path):
        if completion_context != CONTEXT_VALUE:
            return CompletionResult(span=prefix_span)
        return CompletionResult(items=lexical_items(tokens, prefix, prefix_span), span=prefix_span, completeness=Completeness.PARTIAL)
    if completion_context == CONTEXT_MEMBER:
        items = member_items(navigator, path, context_row, context_col)
    elif completion_context == CONTEXT_TYPE:
        items = type_items(navigator, path, row, col)
    elif completion_context == CONTEXT_IMPORT_PATH:
        items = import_path_items(navigator)
    elif completion_context == CONTEXT_IMPORT_NAME:
        items = import_name_items(navigator, path, row, col)
    else:
        items = value_items(navigator, path, row, col)
    if prefix:
        items = tuple(item for item in items if item.label.startswith(prefix))
    document = context.documents.get(navigator.canonical_path(path))
    environment_complete = document is not None and document.lexical_complete and document.syntax_complete
    return CompletionResult(items=items, span=prefix_span,
                            completeness=Completeness.COMPLETE if completion_context == CONTEXT_MEMBER or environment_complete else Completeness.PARTIAL)
