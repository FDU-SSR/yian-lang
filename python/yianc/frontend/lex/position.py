from __future__ import annotations

from pathlib import Path


class SrcPosition:
    """A zero-based source position measured in Unicode code points."""

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
        return SrcSpan(SrcPosition(-1, -1, Path("")), SrcPosition(-1, -1, Path("")))

    def is_synthetic(self) -> bool:
        """Whether this span has no position in source text."""
        return self.start.row < 0 or self.start.col < 0

    def __iadd__(self, other: SrcSpan) -> SrcSpan:
        """Extend this span to cover *other* as well (mutates in place)."""
        if other.is_synthetic():
            return self
        if self.is_synthetic():
            self.start = other.start.clone()
            self.end = other.end.clone()
            self.path = other.path
            return self
        if (other.start.row, other.start.col) < (self.start.row, self.start.col):
            self.start = other.start.clone()
        if (other.end.row, other.end.col) > (self.end.row, self.end.col):
            self.end = other.end.clone()
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
