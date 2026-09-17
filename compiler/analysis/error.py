from __future__ import annotations

from compiler.error import CompilerError
from compiler.frontend.lex.position import SrcSpan


class AnalysisError(ValueError):
    def __init__(self, message: str, span: SrcSpan) -> None:
        super().__init__(message)
        self.span = span


class UnfilledAliasError(CompilerError):
    """Raised when an alias chain reaches an alias whose body is not resolved.

    ``GlobalResolve`` fills alias bodies in a retry loop, so it catches this
    while the alias it depends on is still pending.  Anywhere else it means the
    aliases form a cycle, or an alias names something that was never defined.
    """
