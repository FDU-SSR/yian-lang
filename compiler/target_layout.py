"""Target layout queries shared by semantic analysis and code generation."""

from __future__ import annotations

from collections.abc import Callable, Mapping

from llvmlite import ir

from compiler.analysis.ty.context import TypeCtx
from compiler.codegen.llvm.base.module import apply_target
from compiler.codegen.llvm.base.types import LLTypeCtx


def type_size_provider(
    type_ctx: TypeCtx, unit_names: Mapping[int, str], raw_pointers: bool
) -> Callable[[int], int]:
    """Use the emitted module's exact ABI layout for compile-time sizes."""
    module = ir.Module(name="yian.comptime.layout")
    apply_target(module)
    return LLTypeCtx(type_ctx, module, dict(unit_names), raw_pointers).get_type_size
