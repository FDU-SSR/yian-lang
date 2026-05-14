from __future__ import annotations

from copy import deepcopy


class SrcPosition:
    def __init__(self, row: int, col: int):
        self.row = row
        self.col = col

    def clone(self) -> SrcPosition:
        return deepcopy(self)

    def into_span(self) -> SrcSpan:
        """
        Converts this position into a span.
        """
        return SrcSpan(self, self)


class SrcSpan:
    def __init__(self, start: SrcPosition, end: SrcPosition):
        self.start = start.clone()
        self.end = end.clone()

    @staticmethod
    def empty() -> SrcSpan:
        return SrcSpan(SrcPosition(0, 0), SrcPosition(0, 0))
