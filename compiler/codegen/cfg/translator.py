"""
HIR → CFG lowering.

Translates typed HIR function / method definitions into CFG Function objects.
"""
from __future__ import annotations

from __future__ import annotations

from compiler.analysis.ty import ty as Type
from compiler.analysis.ty.context import TypeCtx
from compiler.analysis.unit.def_point import DefPoint
from compiler.codegen.cfg import ir as IR
from compiler.codegen.cfg.builder import CfgBuilder


class CfgTranslator:
    """Translate a set of HIR DefPoints into CFG Functions.

    Usage::

        translator = CfgTranslator(type_ctx)
        translator.run(def_points)
        functions = translator.export()  # dict[int, Function]
    """

    def __init__(self, type_ctx: TypeCtx) -> None:
        self.__type_ctx = type_ctx
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

        if isinstance(ty, Type.FunctionType):
            return self.__build_function(dp, ty)
        if isinstance(ty, Type.MethodType):
            return self.__build_method(dp, ty)

        raise ValueError(f"Unsupported def type: {type(ty).__name__}")

    def __build_function(self, dp: DefPoint, ty: Type.FunctionType) -> IR.Function:
        builder = CfgBuilder(self.__type_ctx, dp, ty.custom_def.name)
        return builder.build()

    def __build_method(self, dp: DefPoint, ty: Type.MethodType) -> IR.Function:
        builder = CfgBuilder(self.__type_ctx, dp, ty.custom_def.name)
        return builder.build()
