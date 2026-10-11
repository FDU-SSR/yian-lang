"""Availability of current-snapshot facts, independent of build success."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from compiler.frontend.lex.position import SrcSpan, SrcPosition


class Completeness(Enum):
    COMPLETE = "complete"
    PARTIAL = "partial"


class UnavailableReason(Enum):
    SYNTAX = "unavailable syntax"
    DECLARATION = "unresolved declaration"
    TYPE = "undetermined expression type"
    CONFLICT = "conflicting instantiation facts"


@dataclass(frozen=True)
class QueryResult[T]:
    """An owned result or an explicit lack of reliable semantic information."""

    value: T | None = None
    completeness: Completeness = Completeness.PARTIAL
    reason: UnavailableReason | None = None


@dataclass(frozen=True)
class DocumentState:
    """Stage reliability describes a document without discarding its reliable prefix."""

    lexical_complete: bool
    syntax_complete: bool
    declarations_available: bool
    recovery_regions: tuple[SrcSpan, ...] = ()
    lexical_stop: SrcPosition | None = None


class BodyState(Enum):
    COMPLETE = "complete"
    FAILED = "failed"
    UNREACHED = "unreached"
    UNINSTANTIATED = "uninstantiated"


@dataclass(frozen=True)
class DefinitionState:
    signature_available: bool
    body: BodyState
