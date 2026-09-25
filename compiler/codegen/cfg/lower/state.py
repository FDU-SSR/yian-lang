"""Per-function state shared by HIR-to-CFG lowering operations."""
from __future__ import annotations

from dataclasses import dataclass

from compiler.analysis.ty import ty as Type
from compiler.codegen.cfg import ir as IR
from compiler.codegen.cfg.lower.emitter import FunctionEmitter
from compiler.codegen.cfg.lower.session import LoweringSession
from compiler.codegen.cfg.provenance import PointerFacts


@dataclass(frozen=True)
class FunctionState:
    session: LoweringSession
    emitter: FunctionEmitter
    pointers: PointerFacts
    closure_receiver_id: int | None
    closure_receiver_ref_type_id: int | None
    closure_struct_type_id: int | None
    closure_capture_fields: dict[int, Type.StructField]

    def build_func_ptr(self, func_type_id: int) -> IR.Value:
        func_ty = self.session.type_ctx[func_type_id]
        assert isinstance(func_ty, Type.FunctionType)
        func_ptr_ty = func_ty.as_pointer(self.session.type_ctx)
        result = IR.Reg(name=self.emitter.new_name(), type_id=func_ptr_ty)
        return self.emitter.emit(IR.FuncPtr(result=result, func_type_id=func_type_id)).result
