"""Structured compiler diagnostics.

Source-level diagnostics carry a code, a severity, a message and a source span.
A code is ``E`` plus one group digit and two digits: ``E1xx`` lexical, ``E2xx``
syntax, ``E3xx`` names/imports/visibility, ``E4xx`` types, ``E5xx`` other
compile-time checks.  One code stands for one diagnostic *condition* (not one
message text), and a shipped code is never renumbered or reused.

Every compiler error path funnels through :func:`diagnostic_from_error`, so the
CLI and any in-process analysis session report the same structure.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from compiler.analysis.error import AnalysisError
from compiler.frontend.lex.lexer import LexError
from compiler.frontend.lex.position import SrcSpan
from compiler.frontend.parse.error import ParseError


class Severity(Enum):
    """Diagnostic severity, mirroring the LSP severity names."""

    ERROR = "error"
    WARNING = "warning"
    INFORMATION = "information"
    HINT = "hint"


class Stage(Enum):
    """The pipeline stage that produced an error.

    The stage is what :func:`diagnostic_from_error` uses to pick the code group;
    the exception classes alone cannot tell resolution problems from type
    problems, because both raise :class:`AnalysisError`.
    """

    LEX = "lex"
    PARSE = "parse"
    DESUGAR = "desugar"
    PRELUDE = "prelude"
    RESTRICTED_OPS = "restricted_ops"
    RESOLVE = "resolve"
    FINALIZE = "finalize"
    TYPE_CHECK = "type_check"
    COMPTIME = "comptime"
    DEFINITE_ASSIGNMENT = "definite_assignment"
    CODEGEN = "codegen"


# ── lexical (E1xx) ────────────────────────────────────────────────────────────

E101_UNTERMINATED_BLOCK_COMMENT = "E101"
E102_FSTRING_BRACE = "E102"
E103_UNTERMINATED_ESCAPE = "E103"
E104_INVALID_LITERAL = "E104"
E105_UNTERMINATED_LITERAL = "E105"
E199_LEXICAL = "E199"

# ── syntax (E2xx) ─────────────────────────────────────────────────────────────

E201_UNEXPECTED_EOF = "E201"
E202_EXPECTED_TOKEN = "E202"
E203_UNEXPECTED_TOKEN = "E203"
E204_RESERVED_KEYWORD = "E204"
E299_SYNTAX = "E299"

# ── names, imports, visibility (E3xx) ─────────────────────────────────────────

E301_UNKNOWN_IDENTIFIER = "E301"
E302_UNKNOWN_TYPE = "E302"
E303_DUPLICATE_DEFINITION = "E303"
E304_IMPORT_NOT_FOUND = "E304"
E305_NOT_PUBLIC = "E305"
E306_NOT_A_TYPE = "E306"
E307_IMPORT_ENTRY_MODULE = "E307"
E308_UNKNOWN_PACKAGE = "E308"
E309_NOT_A_DEPENDENCY = "E309"
E310_NOT_A_MODULE = "E310"
E399_RESOLUTION = "E399"

# ── types (E4xx) ──────────────────────────────────────────────────────────────

E401_TYPE_MISMATCH = "E401"
E402_INFER_FAILED = "E402"
E403_NO_SUCH_FIELD = "E403"
E404_NO_SUCH_METHOD = "E404"
E405_ARITY_MISMATCH = "E405"
E406_UNSUPPORTED_OPERATOR = "E406"
E407_CIRCULAR_ALIAS = "E407"
E499_TYPE = "E499"

# ── other compile-time checks (E5xx) ──────────────────────────────────────────

E501_RESTRICTED_OPERATION = "E501"
E502_DEFINITE_ASSIGNMENT = "E502"
E503_COMPTIME_CONDITION = "E503"
E599_INTERNAL = "E599"


@dataclass(frozen=True)
class Diagnostic:
    """One compiler diagnostic, ready to be rendered or sent to an editor."""

    code: str
    severity: Severity
    message: str
    span: SrcSpan
    #: True when the message reports a recovery rather than a hard failure; the
    #: editor renders these as warnings (plan P0.3).
    recovered: bool = False


#: ``AX`` codes the *compiler* reuses for source-level import failures.  ``AX``
#: belongs to project/package-level diagnostics (``anx``), so a source-level
#: diagnostic reports the equivalent ``E3xx`` code instead, and the message stops
#: repeating the code (plan P0.3).
__AX_TO_SOURCE_CODE: dict[str, str] = {
    "AX009": E309_NOT_A_DEPENDENCY,
    "AX010": E310_NOT_A_MODULE,
    "AX012": E308_UNKNOWN_PACKAGE,
    "AX014": E307_IMPORT_ENTRY_MODULE,
}


def __split_embedded_code(message: str) -> tuple[str | None, str]:
    """Split a leading ``error[AXnnn]: `` prefix off *message*.

    Returns the source-level code it maps to (``None`` when there is no prefix)
    and the message without the prefix.
    """
    if not message.startswith("error[") or "]: " not in message:
        return None, message
    prefix, rest = message.split("]: ", 1)
    embedded = prefix[len("error[") :]
    code = __AX_TO_SOURCE_CODE.get(embedded)
    if code == E310_NOT_A_MODULE and "does not resolve" in rest:
        code = E304_IMPORT_NOT_FOUND
    return code, rest


def diagnostic_from_error(
    error: Exception, *, stage: Stage | None = None, recovered: bool = False
) -> Diagnostic:
    """Convert a compiler exception into a structured diagnostic.

    ``error`` is expected to be one of ``LexError`` / ``ParseError`` /
    ``AnalysisError`` / ``CompilerError``; anything else becomes an internal
    diagnostic (`E599`) rather than escaping as a traceback.
    """
    span = error.span if isinstance(error, (LexError, ParseError, AnalysisError)) else SrcSpan.empty()

    embedded_code, message = __split_embedded_code(str(error))
    if isinstance(error, LexError):
        code = __lexical_code(message)
    elif isinstance(error, ParseError):
        code = __syntax_code(message)
    elif isinstance(error, AnalysisError):
        code = __analysis_code(message, stage)
    else:
        code = E599_INTERNAL
        if not message:
            message = f"internal compiler error: {type(error).__name__}"

    if embedded_code is not None:
        code = embedded_code

    return Diagnostic(
        code=code,
        severity=Severity.ERROR,
        message=message,
        span=span,
        recovered=recovered,
    )


def __lexical_code(message: str) -> str:
    if "Unterminated block comment" in message:
        return E101_UNTERMINATED_BLOCK_COMMENT
    if "Unterminated string literal" in message or "Unterminated character literal" in message or "Unterminated byte literal" in message:
        return E105_UNTERMINATED_LITERAL
    if "f-string" in message:
        return E102_FSTRING_BRACE
    if "unterminated" in message and "escape" in message:
        return E103_UNTERMINATED_ESCAPE
    return E199_LEXICAL


def __syntax_code(message: str) -> str:
    if message.startswith("End of token stream reached") or "end of file" in message or "<eof>" in message:
        return E201_UNEXPECTED_EOF
    if message.startswith("Expected"):
        return E202_EXPECTED_TOKEN
    if message.startswith("Unexpected"):
        return E203_UNEXPECTED_TOKEN
    if "reserved" in message:
        return E204_RESERVED_KEYWORD
    return E299_SYNTAX


def __analysis_code(message: str, stage: Stage | None) -> str:
    """Classify an analysis error by its message, then by the stage that raised it.

    The message is what distinguishes conditions, so this is where the code for a
    given condition is decided; new conditions get a new code next to their
    siblings.  Conditions that are not classified yet fall back to the group code
    (``E399`` / ``E499`` / …) rather than guessing a specific one.
    """
    if "Circular type alias" in message or "has no resolved body" in message:
        return E407_CIRCULAR_ALIAS

    if message.startswith("Unknown identifier") or message.startswith("Unknown enum variant"):
        return E301_UNKNOWN_IDENTIFIER
    if message.startswith("Undefined type") or message.startswith("Undefined const generic"):
        return E302_UNKNOWN_TYPE
    if message.startswith("Duplicate symbol name") or message.startswith("Duplicate method name"):
        return E303_DUPLICATE_DEFINITION
    if "is not found in the imported unit" in message or message.startswith("Cannot resolve import path"):
        return E304_IMPORT_NOT_FOUND
    if "is not declared 'pub'" in message:
        return E305_NOT_PUBLIC
    if message.endswith("is not a type"):
        return E306_NOT_A_TYPE
    if message.startswith("Cannot import variable"):
        return E307_IMPORT_ENTRY_MODULE

    if message.startswith("Expected type") or message.startswith("Expected tuple type"):
        return E401_TYPE_MISMATCH
    if message.startswith("cannot infer generic arguments"):
        return E402_INFER_FAILED
    if "has no field named" in message:
        return E403_NO_SUCH_FIELD
    if message.startswith("Unknown method call") or message.startswith("Unknown static method call") or message.startswith("Unknown function call"):
        return E404_NO_SUCH_METHOD
    if "expects" in message and "arguments, got" in message:
        return E405_ARITY_MISMATCH
    if message.startswith("operator") and "is not supported between" in message:
        return E406_UNSUPPORTED_OPERATOR

    if stage is Stage.RESTRICTED_OPS:
        return E501_RESTRICTED_OPERATION
    if stage is Stage.DEFINITE_ASSIGNMENT:
        return E502_DEFINITE_ASSIGNMENT
    if stage is Stage.COMPTIME:
        return E503_COMPTIME_CONDITION
    if stage is Stage.CODEGEN:
        return E599_INTERNAL
    if stage is Stage.FINALIZE:
        return E499_TYPE
    if stage is Stage.TYPE_CHECK:
        return E499_TYPE
    if stage is Stage.RESOLVE:
        return E399_RESOLUTION
    return E599_INTERNAL


def format_source_error(diagnostic: Diagnostic, text: str) -> str:
    """Render *diagnostic* as a human-readable block pointing at the source line.

    *text* is the document's current text — callers pass the in-memory text, so
    rendering works for unsaved buffers and never re-reads the file.
    """
    span = diagnostic.span
    start_row = span.start.row
    start_col = span.start.col
    end_row = span.end.row
    end_col = span.end.col

    lines = [
        f"{diagnostic.severity.value}[{diagnostic.code}]: {diagnostic.message}",
        f"--> {span.path}:{start_row + 1}:{start_col}",
    ]

    source_lines = text.splitlines()
    if 0 <= start_row < len(source_lines):
        source_line = source_lines[start_row]
        lines.append(f"    {source_line}")
        if start_row == end_row:
            marker_width = max(1, end_col - start_col)
        else:
            marker_width = max(1, len(source_line) - start_col + 1)
        lines.append("    " + " " * (start_col - 1) + "^" * marker_width)
    return "\n".join(lines)


def diagnostic_payload(diagnostic: Diagnostic, *, range_payload: dict[str, object]) -> dict[str, object]:
    """Build the JSON payload for one diagnostic.

    ``range_payload`` comes from :func:`compiler.analysis.positions.to_lsp_range`;
    keeping the conversion outside keeps this module free of position maths.
    """
    return {
        "code": diagnostic.code,
        "severity": diagnostic.severity.value,
        "message": diagnostic.message,
        "range": range_payload,
        "recovered": diagnostic.recovered,
    }


__all__ = [
    "Diagnostic",
    "Severity",
    "Stage",
    "diagnostic_from_error",
    "diagnostic_payload",
    "format_source_error",
]
