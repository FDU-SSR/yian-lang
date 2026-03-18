"""
This package contains definitions and utilities for handling errors in the Yian compiler.

Brief Overview:
- Error Reporter (error_reporter.py): Manages the reporting of errors encountered during compilation.
- Yian Errors (yian_error.py): Defines various error types specific to the Yian language, such as syntax errors, semantic errors, and type errors.
"""

from .error_reporter import ErrorReporter
from .yian_error import (CodegenError, CompilerError, ImportResolutionError, NameResolutionError, SemanticError,
                         YianSyntaxError, YianTypeError)

__all__ = [
    # Error Reporter
    "ErrorReporter",
    # Yian Errors
    "CompilerError",
    "ImportResolutionError",
    "NameResolutionError",
    "YianTypeError",
    "YianSyntaxError",
    "SemanticError",
    "CodegenError",
]
