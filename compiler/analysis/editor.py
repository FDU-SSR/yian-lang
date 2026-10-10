"""Frozen editor inputs and one-shot analysis snapshots."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING

from compiler.analysis.documents import Document, DocumentStore
from compiler.analysis.queries.context import QueryContext
from compiler.analysis.queries.index import Declaration, DeclarationKind
from compiler.analysis.queries.completion import (
    CompletionResult,
    SignatureInfo,
    complete,
    signature_help,
)
from compiler.analysis.queries.navigation import Navigator, Resolution
from compiler.analysis.queries.refactor import ImportRemoval, find_import_removals
from compiler.analysis.queries.semantic import SemanticToken, classify as classify_semantic_tokens
from compiler.analysis.queries.refactor import (
    PrepareRenameResult,
    ReferenceResult,
    RenameResult,
    find_references,
    prepare_rename,
    rename,
)
from compiler.analysis.session import AnalysisResult, AnalysisSession, collect_an_files
from compiler.format.formatter import format_text

if TYPE_CHECKING:
    from collections.abc import Sequence


class EditorMode(Enum):
    """The amount of compiler work requested for a frozen input set."""

    SYNTAX = "syntax"
    FULL = "full"


@dataclass(frozen=True)
class FrozenInputs:
    """Ordered, owned source text and version metadata for one editor revision."""

    paths: tuple[Path, ...]
    documents: tuple[Document, ...]
    revision: int
    topology_revision: int
    configuration_identity: str

    @classmethod
    def freeze(
        cls,
        paths: Sequence[Path],
        *,
        documents: DocumentStore | None = None,
        revision: int,
        topology_revision: int,
        configuration_identity: str,
    ) -> FrozenInputs:
        """Capture overlays and disk files in compiler input order."""
        overlay = documents if documents is not None else DocumentStore()
        ordered_paths = tuple(collect_an_files(paths, overlay=overlay))
        frozen_documents = tuple(
            Document(
                path=path.resolve(),
                text=overlay.text(path),
                version=overlay.version(path),
            )
            for path in ordered_paths
        )
        return cls(
            paths=ordered_paths,
            documents=frozen_documents,
            revision=revision,
            topology_revision=topology_revision,
            configuration_identity=configuration_identity,
        )

    def document_store(self) -> DocumentStore:
        """Create an independent overlay for a single analysis call."""
        return DocumentStore(self.documents)


@dataclass
class EditorSnapshot:
    """One result tied to the exact frozen input identity that produced it."""

    inputs: FrozenInputs
    mode: EditorMode
    result: AnalysisResult
    queries: QueryContext | None
    _navigator: Navigator | None = field(default=None, init=False, repr=False)
    _navigator_std_root: Path | None = field(default=None, init=False, repr=False)

    @property
    def identity(self) -> tuple[int, int, EditorMode, str]:
        return (
            self.inputs.revision,
            self.inputs.topology_revision,
            self.mode,
            self.inputs.configuration_identity,
        )

    @property
    def semantic_complete(self) -> bool:
        # Editor queries consume semantic facts and do not run a backend HIR audit.
        return (
            self.mode is EditorMode.FULL
            and self.result.type_ctx is not None
            and self.result.failed_stage is None
            and self.result.ok()
        )

    def format_document(self, path: Path) -> str | None:
        """Format the frozen document text, returning None when it does not parse."""
        canonical = path.resolve()
        for document in self.inputs.documents:
            if document.path.resolve() == canonical:
                return format_text(document.text, path=canonical)
        return None

    def resolve_at(
        self, path: Path, row: int, column: int, *, std_root: Path | None = None
    ) -> Resolution | None:
        """Resolve a cursor using only semantic facts from this snapshot."""
        navigator = self.__navigator_for(std_root)
        if navigator is None:
            return None
        return navigator.resolve(path, row, column)

    def __navigator_for(self, std_root: Path | None) -> Navigator | None:
        """Reuse the snapshot's path, declaration, and reference indexes."""
        if self.queries is None:
            return None

        canonical_std_root = None if std_root is None else std_root.resolve()
        if self._navigator is None or canonical_std_root != self._navigator_std_root:
            self._navigator = Navigator(self.queries, std_root=canonical_std_root)
            self._navigator.prepare_query_indexes()
            self._navigator_std_root = canonical_std_root

        return self._navigator

    def hover_at(
        self, path: Path, row: int, column: int, *, std_root: Path | None = None
    ) -> Resolution | None:
        """Return the symbol target and expression type used to render hover."""
        return self.resolve_at(path, row, column, std_root=std_root)

    def references_at(
        self,
        path: Path,
        row: int,
        column: int,
        *,
        include_declaration: bool = True,
        std_root: Path | None = None,
    ) -> ReferenceResult | None:
        """Return references for the resolved declaration identity at a cursor."""
        navigator = self.__navigator_for(std_root)
        if self.queries is None or navigator is None:
            return None
        return find_references(
            self.queries,
            path,
            row,
            column,
            include_declaration=include_declaration,
            std_root=std_root,
            navigator=navigator,
        )

    def rename_at(
        self,
        path: Path,
        row: int,
        column: int,
        new_name: str,
        *,
        std_root: Path | None = None,
    ) -> RenameResult:
        """Rename only when this snapshot has a complete semantic result."""
        navigator = self.__navigator_for(std_root)
        if not self.semantic_complete or self.queries is None or navigator is None:
            return RenameResult(refusal="semantic analysis is incomplete; rename is unavailable")
        return rename(
            self.queries,
            path,
            row,
            column,
            new_name,
            std_root=std_root,
            navigator=navigator,
        )

    def prepare_rename_at(
        self, path: Path, row: int, column: int, *, std_root: Path | None = None
    ) -> PrepareRenameResult:
        """Check rename safety and return the target's source-name range."""
        navigator = self.__navigator_for(std_root)
        if not self.semantic_complete or self.queries is None or navigator is None:
            return PrepareRenameResult(refusal="semantic analysis is incomplete; rename is unavailable")
        return prepare_rename(
            self.queries,
            path,
            row,
            column,
            std_root=std_root,
            navigator=navigator,
        )

    def complete_at(
        self, path: Path, row: int, column: int, *, std_root: Path | None = None
    ) -> CompletionResult:
        """Compute completion candidates from the snapshot's parsed and semantic facts."""
        navigator = self.__navigator_for(std_root)
        if self.queries is None or navigator is None:
            return CompletionResult()
        return complete(self.queries, path, row, column, std_root=std_root, navigator=navigator)

    def signature_help_at(
        self, path: Path, row: int, column: int, *, std_root: Path | None = None
    ) -> SignatureInfo | None:
        """Return the callable signature active at the cursor, when known."""
        navigator = self.__navigator_for(std_root)
        if self.queries is None or navigator is None:
            return None
        return signature_help(self.queries, path, row, column, std_root=std_root, navigator=navigator)

    def import_removals_at(self, path: Path) -> tuple[ImportRemoval, ...]:
        """Return protocol-neutral import edits tied to this analysis snapshot."""
        navigator = self.__navigator_for(None)
        if navigator is None:
            return ()
        return find_import_removals(self.result, navigator.view, path)

    def document_symbols_at(self, path: Path) -> tuple[Declaration, ...]:
        """Return outline declarations with source-level container identities."""
        navigator = self.__navigator_for(None)
        if navigator is None:
            return ()
        outline_kinds = {
            DeclarationKind.STRUCT,
            DeclarationKind.ENUM,
            DeclarationKind.TRAIT,
            DeclarationKind.ALIAS,
            DeclarationKind.FUNCTION,
            DeclarationKind.METHOD,
            DeclarationKind.FIELD,
            DeclarationKind.VARIANT,
        }
        return tuple(
            declaration
            for declaration in navigator.declarations_in(path)
            if declaration.kind in outline_kinds
        )

    def semantic_tokens_at(self, path: Path) -> tuple[SemanticToken, ...]:
        """Classify only identifiers resolved by this complete snapshot."""
        if not self.semantic_complete:
            return ()
        navigator = self.__navigator_for(None)
        if navigator is None:
            return ()
        return classify_semantic_tokens(navigator, path)


class EditorEngine:
    """Run one fresh compiler analysis against immutable editor inputs."""

    def __init__(self, session: AnalysisSession) -> None:
        self.__session = session

    def analyze(self, inputs: FrozenInputs, mode: EditorMode) -> EditorSnapshot:
        """Build a fresh result; compiler passes never mutate a previous snapshot."""
        result = self.__session.analyze(
            inputs.paths,
            documents=inputs.document_store(),
            syntax_only=mode is EditorMode.SYNTAX,
            recover=True,
        )
        queries = None if mode is EditorMode.SYNTAX else QueryContext(result)
        return EditorSnapshot(inputs, mode, result, queries)


__all__ = ["EditorEngine", "EditorMode", "EditorSnapshot", "FrozenInputs"]
