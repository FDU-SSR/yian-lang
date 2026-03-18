"""
This package contains definitions and utilities for handling the Intermediate Representation (IR) in the Yian compiler.

Brief Overview:
- GIR Definitions (gir.py): Contains definitions related to the GIR statements used in the compiler's intermediate representation.
- Operators (operator.py): Defines various operators used within the IR.
- Utilities (utils.py): Provides utility functions to manipulate and analyze the IR.
"""

from . import cgir, gir
from .def_point import DefPoint
from .meta import StmtMetadata
from .operator import Operator
from .symbol import CustomType, Function, Literal, Method, Symbol, TypeAlias, VariableSymbol
from .typed_value import (ArrayLiteral, BooleanLiteral, CharLiteral, FloatLiteral, IntegerLiteral, LiteralValue,
                          StringLiteral, TupleLiteral, TypedValue, Variable)
from .utils import map_stmts

__all__ = [
    # Operators
    "Operator",
    # Utilities
    "map_stmts",
    # Entities
    "TypedValue", "Variable",
    "LiteralValue", "IntegerLiteral", "FloatLiteral", "StringLiteral", "BooleanLiteral", "CharLiteral", "ArrayLiteral", "TupleLiteral",
    # GIR
    "gir",
    # CGIR
    "cgir",
    # Symbols
    "Symbol", "CustomType", "Function", "TypeAlias", "VariableSymbol", "Method", "Literal",
    # DefPoint
    "DefPoint",
    # Metadata
    "StmtMetadata",
]
