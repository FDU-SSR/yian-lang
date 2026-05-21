from __future__ import annotations

from copy import deepcopy
from pathlib import Path


class SrcPosition:
    def __init__(self, row: int, col: int, path: Path):
        self.row = row
        self.col = col
        self.path = path

    def clone(self) -> SrcPosition:
        return deepcopy(self)

    def into_span(self) -> SrcSpan:
        """
        Converts this position into a span.
        """
        return SrcSpan(self, self)


class SrcSpan:
    def __init__(self, start: SrcPosition, end: SrcPosition):
        if start.path != end.path:
            raise ValueError("Start and end positions must be in the same file")
        self.start = start.clone()
        self.end = end.clone()
        self.path = start.path

    def __repr__(self) -> str:
        return f"{self.path}({self.start.row}:{self.start.col} - {self.end.row}:{self.end.col})"

    @staticmethod
    def empty() -> SrcSpan:
        return SrcSpan(SrcPosition(0, 0, Path("")), SrcPosition(0, 0, Path("")))

    def __iadd__(self, other: SrcSpan) -> SrcSpan:
        self.start.row = min(self.start.row, other.start.row)
        self.start.col = min(self.start.col, other.start.col)
        self.end.row = max(self.end.row, other.end.row)
        self.end.col = max(self.end.col, other.end.col)
        return self

    def __add__(self, other: SrcSpan) -> SrcSpan:
        new_span = deepcopy(self)
        new_span += other
        return new_span

    @staticmethod
    def combine_all(spans: list[SrcSpan]) -> SrcSpan:
        combined_span = SrcSpan.empty()
        for span in spans:
            combined_span += span
        return combined_span
