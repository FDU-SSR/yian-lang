"""Query-local projections of a completed analysis result."""

from __future__ import annotations

from compiler.analysis.queries.index import DeclarationIndex, LazyIndex
from compiler.analysis.session import AnalysisResult
from compiler.analysis.diagnostics import Stage
from compiler.analysis.queries.availability import DocumentState, DefinitionState, BodyState
from pathlib import Path


class QueryContext:
    """Own the optional declaration index for one completed analysis result."""

    def __init__(self, result: AnalysisResult) -> None:
        self.result = result
        self.documents: dict[Path, DocumentState] = {}
        for path in result.sources:
            problems = tuple(diagnostic for diagnostic in result.diagnostics
                             if diagnostic.span.path == path and diagnostic.stage in (Stage.LEX, Stage.PARSE))
            self.documents[path] = DocumentState(
                lexical_complete=not any(diagnostic.stage is Stage.LEX for diagnostic in problems),
                syntax_complete=not problems,
                declarations_available=any(unit.path == path for unit in result.units.values()),
                recovery_regions=tuple(diagnostic.span for diagnostic in problems),
                lexical_stop=next((diagnostic.span.start for diagnostic in problems if diagnostic.stage is Stage.LEX), None),
            )
        self.definitions: dict[int, DefinitionState] = {}
        for type_id, _, _ in result.procedures:
            available = result.type_ctx is not None and result.type_ctx.editor_type_available(type_id)
            point = result.def_points.get(type_id)
            body = BodyState.COMPLETE if point is not None and point.body is not None else BodyState.FAILED if point is not None else BodyState.UNREACHED
            if point is None and available and result.type_ctx is not None and result.type_ctx.contains_generic(type_id):
                body = BodyState.UNINSTANTIATED
            self.definitions[type_id] = DefinitionState(available, body)
        self.index: DeclarationIndex | None = None
        if result.import_edges is not None:
            self.index = LazyIndex(
                units=result.units,
                type_ctx=result.type_ctx,
                import_edges=result.import_edges,
                packages=result.packages,
                def_points=result.def_points,
            )


__all__ = ["QueryContext"]
