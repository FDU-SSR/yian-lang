"""
HIR → CFG lowering.

Translates typed HIR function / method definitions into CFG Function objects.
"""

from __future__ import annotations

from compiler.analysis.ty import ty as Type
from compiler.analysis.ty.context import TypeCtx
from compiler.analysis.unit.def_point import DefPoint
from compiler.codegen.cfg import ir as IR


class CfgTranslator:
    """Translate a set of HIR DefPoints into CFG Functions.

    Usage::

        translator = CfgTranslator(type_ctx)
        translator.run(def_points)
        functions = translator.export()  # dict[str, Function]
    """

    def __init__(self, type_ctx: TypeCtx) -> None:
        self.__type_ctx = type_ctx
        self.__functions: dict[str, IR.Function] = {}

    def run(self, def_points: dict[int, DefPoint]) -> None:
        """Lower every DefPoint in *def_points* to a CFG Function.

        Currently only function signatures (params + return type) are
        translated; the body is a single empty entry block.
        """
        for dp in def_points.values():
            func = self.__translate(dp)
            self.__functions[func.name] = func

    def export(self) -> dict[str, IR.Function]:
        """Return the translated functions keyed by name."""
        return dict(self.__functions)

    # ------------------------------------------------------------------
    # internal translation
    # ------------------------------------------------------------------

    def __translate(self, dp: DefPoint) -> IR.Function:
        ty = self.__type_ctx[dp.type_id]

        if isinstance(ty, Type.FunctionType):
            return self.__translate_function(ty)
        if isinstance(ty, Type.MethodType):
            return self.__translate_method(ty)

        raise ValueError(f"Unsupported def type: {type(ty).__name__}")

    def __translate_function(self, ty: Type.FunctionType) -> IR.Function:
        params = self.__build_params(ty)
        return_type_id = ty.return_type(self.__type_ctx)
        entry = IR.Block("entry")

        return IR.Function(
            name=ty.custom_def.name,
            params=params,
            return_type=return_type_id,
            blocks=[entry],
            entry="entry",
        )

    def __translate_method(self, ty: Type.MethodType) -> IR.Function:
        receiver_type_id = ty.receiver_type(self.__type_ctx)
        receiver_param = IR.Param(name="self", type_id=receiver_type_id)

        params = [receiver_param] + self.__build_params(ty)
        return_type_id = ty.return_type(self.__type_ctx)
        entry = IR.Block("entry")

        return IR.Function(
            name=ty.custom_def.name,
            params=params,
            return_type=return_type_id,
            blocks=[entry],
            entry="entry",
        )

    def __build_params(self, ty: Type.FunctionType | Type.MethodType) -> list[IR.Param]:
        return [
            IR.Param(name=p.name, type_id=p.type_id)
            for p in ty.parameters(self.__type_ctx)
        ]
