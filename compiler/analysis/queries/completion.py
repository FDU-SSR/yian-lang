"""Compatibility facade for read-only completion and signature queries."""

from compiler.analysis.queries.completion_model import (
    Completion, CompletionKind, CompletionResult, Signature, SignatureInfo,
)
from compiler.analysis.queries.completion_query import complete, query_completion
from compiler.analysis.queries.signatures import signature_help, query_signature_help

__all__ = [
    "Completion", "CompletionKind", "CompletionResult", "Signature", "SignatureInfo",
    "complete", "query_completion", "signature_help", "query_signature_help",
]
