"""Documents the analysis session works on: in-memory text plus a version.

The editor holds unsaved buffers, so analysis must not read files from disk for
the documents it was handed.  :class:`DocumentStore` keeps the overlay and falls
back to disk for everything else (the standard library, dependencies the editor
has not opened).  A document's identity is its path; the version is the editor's
document version and is part of the snapshot key (plan §5.12).
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Document:
    """One document: a path, its current text, and the editor's version."""

    path: Path
    text: str
    version: int | None = None


class DocumentStore:
    """Overlay of in-memory documents over the file system.

    ``text()`` prefers the overlay and only touches disk when the document was
    not provided, which is what lets an analysis session run entirely on unsaved
    text.  ``versions()`` exposes the version numbers for snapshot keys.
    """

    def __init__(self, documents: Iterable[Document] = ()) -> None:
        self.__documents: dict[Path, Document] = {}
        for document in documents:
            self.add(document)

    def add(self, document: Document) -> None:
        """Add or replace one document."""
        self.__documents[document.path.resolve()] = Document(
            path=document.path.resolve(), text=document.text, version=document.version
        )

    def remove(self, path: Path) -> None:
        """Drop a document; later reads fall back to disk."""
        self.__documents.pop(path.resolve(), None)

    def __contains__(self, path: Path) -> bool:
        return path.resolve() in self.__documents

    def __iter__(self) -> Iterator[Document]:
        return iter(self.__documents.values())

    def __len__(self) -> int:
        return len(self.__documents)

    def text(self, path: Path) -> str:
        """Return the document's text: the overlay if present, else the file."""
        document = self.__documents.get(path.resolve())
        if document is not None:
            return document.text
        return path.read_text()

    def version(self, path: Path) -> int | None:
        """Return the editor's version for *path*, or ``None`` when unknown."""
        document = self.__documents.get(path.resolve())
        return document.version if document is not None else None

    def versions(self) -> Mapping[Path, int | None]:
        """Return every overlay document's version, for snapshot keys."""
        return {path: document.version for path, document in self.__documents.items()}


__all__ = ["Document", "DocumentStore"]
