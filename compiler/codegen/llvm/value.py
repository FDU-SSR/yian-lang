"""LLValue — binds a Yian type ID to an LLVM ``ir.Value``."""

from __future__ import annotations

from dataclasses import dataclass

from llvmlite import ir  # type: ignore[import-untyped]


@dataclass
class LLType:
    """A Yian-typed LLVM IR type."""

    type_id: int
    ir_type: ir.Type


@dataclass
class LLValue:
    """A Yian-typed LLVM IR value."""

    type_id: int
    ir_val: ir.Value
