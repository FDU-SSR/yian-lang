"""
This file defines exception classes used for error handling in the Yian compiler.
"""


from typing import TYPE_CHECKING

from compiler.config.defs import TypeFormatter

if TYPE_CHECKING:
    from compiler.utils.IR import TypedValue


class YianError(Exception):
    """
    Base class for all Yian compiler errors.
    """

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message

    @property
    def error_type(self) -> str:
        return self.__class__.__name__

    def __repr__(self) -> str:
        return f"[{self.error_type}] {self.message}"


class CompilerError(YianError):
    """
    If this error is raised, it indicates a bug in the Yian compiler itself, not in the user's code.
    """
    pass


class SourceCodeError(YianError):
    """
    Base class for errors related to the user's Yian source code.

    Raised when there is a syntax error in the Yian source code.
    """
    pass


class ImportResolutionError(SourceCodeError):
    """
    Raised when an import statement fails to resolve the specified module or file.
    """
    pass


class NameResolutionError(SourceCodeError):
    """
    Raised when a name (variable, function, type, etc.) cannot be resolved in the current scope.
    """
    pass


class YianTypeError(SourceCodeError):
    """
    Raised when there is a type mismatch or invalid type usage in the source code.
    """
    @staticmethod
    def mismatch(expected: int, actual: "TypedValue | int", fmt: TypeFormatter) -> 'YianTypeError':
        expected_name = fmt(expected)
        if isinstance(actual, int):
            actual_name = fmt(actual)
        else:
            actual_name = fmt(actual.type_id)
        message = f"Type mismatch: expected '{expected_name}', got '{actual_name}'."
        return YianTypeError(message)

    @staticmethod
    def member_not_found(type_id: int, member_name: str, fmt: TypeFormatter) -> 'YianTypeError':
        type_name = fmt(type_id)
        message = f"Member '{member_name}' not found in type '{type_name}'."
        return YianTypeError(message)


class YianSyntaxError(SourceCodeError):
    """
    Raised when there is a syntax error in the Yian source code.
    """
    pass


class SemanticError(SourceCodeError):
    """
    Raised when there is a semantic error in the Yian source code.
    """
    pass


class CodegenError(YianError):
    """
    Raised when there is an error during the code generation phase.
    """
    pass
