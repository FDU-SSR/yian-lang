"""
This package contains data structures and utilities for handling types in the Yian compiler.

Brief Overview:
- Impl Definitions (impl.py): Contains definitions related to implementations of traits and methods for types.
- Method Registry (method_registry.py): Manages a registry of methods associated with various types.
- TypeSpace (space.py): A storage system for managing and retrieving types during compilation.
- Utilities (utils.py): Provides helper functions for type manipulation and analysis.
- YianType and Subclasses (yian_types.py): Defines various types used in the Yian language, such as basic types, derived types, and custom types.
"""

from .impl import Impl
from .method_registry import LookupResult, MethodRegistry
from .space import TypeSpace
from .utils import is_same_template, type_unification
from .yian_types import (ArrayType, BasicType, BoolType, CharType, CustomDef, EnumDef, EnumType, EnumVariant, FloatType,
                         FunctionDef, FunctionPointerType, FunctionType, GenericType, InstantiatedType, IntType,
                         MethodDef, MethodType, Parameter, PointerType, SliceType, StrType, StructDef, StructField,
                         StructType, TraitDef, TraitType, TupleType, VoidType, YianType)

__all__ = [
    # Impl definitions
    "Impl",
    # Implementation registry
    "MethodRegistry", "LookupResult",
    # Type space
    "TypeSpace",
    # Utilities
    "type_unification", "is_same_template",
    # Type classes
    "YianType",
    "BasicType", "CustomDef", "GenericType", "InstantiatedType",
    "IntType", "FloatType", "BoolType", "CharType", "StrType", "VoidType",
    "ArrayType", "TupleType", "PointerType", "SliceType",
    "FunctionType", "MethodType", "FunctionPointerType",
    "StructType", "EnumType", "TraitType",
    "StructDef", "EnumDef", "TraitDef", "FunctionDef", "MethodDef",
    "StructField", "Parameter", "EnumVariant",
]
