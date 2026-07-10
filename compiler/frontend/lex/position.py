from __future__ import annotations

from pathlib import Path


class SrcPosition:
    def __init__(self, row: int, col: int, path: Path):
        self.row = row
        self.col = col
        self.path = path

    def clone(self) -> SrcPosition:
        """Create a new position with the same coordinates."""
        return SrcPosition(self.row, self.col, self.path)

    def into_span(self) -> SrcSpan:
        """Convert this position into a zero-width span."""
        return SrcSpan(self.clone(), self.clone())


class SrcSpan:
    def __init__(self, start: SrcPosition, end: SrcPosition):
        if start.path != end.path:
            raise ValueError("Start and end positions must be in the same file")
        self.start = start
        self.end = end
        self.path = start.path

    def __repr__(self) -> str:
        return f"{self.path}({self.start.row}:{self.start.col} - {self.end.row}:{self.end.col})"

    @staticmethod
    def empty() -> SrcSpan:
        return SrcSpan(SrcPosition(0, 0, Path("")), SrcPosition(0, 0, Path("")))

    def __iadd__(self, other: SrcSpan) -> SrcSpan:
        """Extend this span to cover *other* as well (mutates in place)."""
        self.start = SrcPosition(
            min(self.start.row, other.start.row),
            min(self.start.col, other.start.col),
            self.start.path,
        )
        self.end = SrcPosition(
            max(self.end.row, other.end.row),
            max(self.end.col, other.end.col),
            self.end.path,
        )
        return self

    def __add__(self, other: SrcSpan) -> SrcSpan:
        """Return a new span that covers both *self* and *other*."""
        new_span = SrcSpan(
            SrcPosition(self.start.row, self.start.col, self.path),
            SrcPosition(self.end.row, self.end.col, self.path),
        )
        new_span += other
        return new_span

    @staticmethod
    def combine_all(spans: list[SrcSpan]) -> SrcSpan:
        combined_span = SrcSpan.empty()
        for span in spans:
            combined_span += span
        return combined_span
