"""Query-local projections of a completed analysis result."""

from __future__ import annotations

from compiler.analysis.queries.index import DeclarationIndex, LazyIndex
from compiler.analysis.session import AnalysisResult


class QueryContext:
    """Own the optional declaration index for one completed analysis result."""

    def __init__(self, result: AnalysisResult) -> None:
        self.result = result
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
