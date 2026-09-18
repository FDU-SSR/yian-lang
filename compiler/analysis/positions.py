"""Conversion between compiler positions and editor (LSP) positions.

This is the **only** module allowed to know both coordinate systems (plan §5.1).
The compiler keeps its own representation — ``SrcPosition(row, col, path)`` with a
0-based row, a 1-based column counted in code points, and a ``Path`` — and it is
not modified.  Editors speak LSP: 0-based lines, 0-based characters counted in
UTF-16 code units, and ``file://`` URIs.

Everything that crosses into an editor goes through :func:`to_lsp_range` and
:func:`path_to_uri`; everything coming back goes through :func:`uri_to_path`.
"""

from __future__ import annotations

from pathlib import Path
from urllib.parse import unquote, urlparse

from compiler.frontend.lex.position import SrcSpan


def utf16_length(text: str) -> int:
    """Length of *text* in UTF-16 code units (LSP's character unit)."""
    return sum(2 if ord(character) > 0xFFFF else 1 for character in text)


def to_lsp_position(row: int, line_text: str, col: int) -> dict[str, int]:
    """Convert a compiler position to a 0-based line with a UTF-16 character.

    *row* is already 0-based and is passed through; *col* is 1-based and counted
    in code points, so *line_text* is needed to convert it.
    """
    code_point_index = max(0, col - 1)
    character = utf16_length(line_text[:code_point_index])
    return {"line": row, "character": character}


def to_lsp_range(span: SrcSpan, text: str) -> dict[str, dict[str, int]]:
    """Convert *span* to an LSP range, using *text* to resolve columns.

    *text* is the document's current text, so ranges stay correct for unsaved
    buffers where the file on disk may be older (or missing).
    """
    lines = text.splitlines()
    start_line = lines[span.start.row] if 0 <= span.start.row < len(lines) else ""
    end_line = lines[span.end.row] if 0 <= span.end.row < len(lines) else ""

    return {
        "start": to_lsp_position(span.start.row, start_line, span.start.col),
        "end": to_lsp_position(span.end.row, end_line, span.end.col),
    }


def path_to_uri(path: Path) -> str:
    """Convert a compiler path to a ``file://`` URI."""
    return path.resolve().as_uri()


def uri_to_path(uri: str) -> Path:
    """Convert a ``file://`` URI back to a path.

    Percent-encoded characters are decoded, so paths with spaces round-trip.
    """
    parsed = urlparse(uri)
    if parsed.scheme != "file":
        raise ValueError(f"not a file URI: {uri}")
    return Path(unquote(parsed.path))


__all__ = [
    "path_to_uri",
    "to_lsp_position",
    "to_lsp_range",
    "uri_to_path",
    "utf16_length",
]
