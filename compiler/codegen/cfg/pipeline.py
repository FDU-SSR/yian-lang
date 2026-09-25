"""HIR-to-CFG lowering and CFG transformation sequence."""
from __future__ import annotations

from compiler.analysis.ty.context import TypeCtx
from compiler.analysis.unit.def_point import DefPoint
from compiler.codegen.cfg import ir as IR
from compiler.codegen.cfg.lower.translator import CfgTranslator
from compiler.codegen.cfg.passes.cleanup import Cleanup
from compiler.codegen.cfg.passes.insert_checks import InsertChecks


class CfgPipeline:
    def __init__(self, type_ctx: TypeCtx, raw_pointers: bool) -> None:
        self.__type_ctx = type_ctx
        self.__raw_pointers = raw_pointers

    def run(self, def_points: dict[int, DefPoint]) -> dict[int, IR.Function]:
        lowerer = CfgTranslator(self.__type_ctx, self.__raw_pointers)
        lowerer.run(def_points)
        module = lowerer.module
        InsertChecks(module).run()
        Cleanup(module).run()
        return module.export()
