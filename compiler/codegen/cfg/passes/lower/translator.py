"""下降 pass 的模块级入口：把一批 HIR 定义整体降成 CFG。

每个 `DefPoint` 交给 `builder.CfgBuilder`（单函数下降 + 随后调用 `run_pipeline`），
结果按 `type_id` 收在 `export()` 里交给下一段（CFG → LLVM）。

用法::

    translator = CfgTranslator(type_ctx, raw_pointers=...)
    translator.run(def_points)
    functions = translator.export()  # dict[int, IR.Function]
"""
from __future__ import annotations

from compiler.analysis.ty import ty as Type
from compiler.analysis.ty.context import TypeCtx
from compiler.analysis.unit.def_point import DefPoint
from compiler.codegen.cfg import ir as IR
from compiler.codegen.cfg.passes.lower.builder import CfgBuilder


class CfgTranslator:
    """把一批 HIR 定义降成 CFG 函数（`run` → `export`）。"""

    def __init__(self, type_ctx: TypeCtx, raw_pointers: bool = False) -> None:
        self.__type_ctx = type_ctx
        self.__raw_pointers = raw_pointers
        self.__functions: dict[int, IR.Function] = {}

    def run(self, def_points: dict[int, DefPoint]) -> None:
        """Lower every DefPoint in *def_points* to a CFG Function."""
        for dp in def_points.values():
            func = self.__translate(dp)
            self.__functions[func.type_id] = func

    def export(self) -> dict[int, IR.Function]:
        """Return the translated functions keyed by type_id."""
        return dict(self.__functions)

    # ------------------------------------------------------------------
    # internal translation
    # ------------------------------------------------------------------

    def __translate(self, dp: DefPoint) -> IR.Function:
        ty = self.__type_ctx[dp.type_id]
        if not isinstance(ty, (Type.FunctionType, Type.MethodType)):
            raise ValueError(f"Unsupported def type: {type(ty).__name__}")
        return CfgBuilder(
            self.__type_ctx, dp, ty.custom_def.name, raw_pointers=self.__raw_pointers
        ).build()
