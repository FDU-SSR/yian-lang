"""下降 pass 的入口：把一批 HIR 定义整体降成 CFG（管线的第 1 段）。

每个 `DefPoint` 交给 `lower.builder.CfgBuilder` 做单函数下降；同一个 builder 交出的
`CfgCtx`（span / 返回类型 / void 占位通道等）与函数一起收好，供后两段 pass 取用。
本段**只下降**，不跑后续 pass——三段按序由 `compiler/main.py` 编排。

用法::

    cfg_lower = CfgTranslator(type_ctx, raw_pointers=...)
    cfg_lower.run(def_points)
    functions = cfg_lower.export()          # dict[int, IR.Function]
    contexts = cfg_lower.pass_contexts()    # dict[int, CfgCtx]
"""
from __future__ import annotations

from compiler.analysis.ty import ty as Type
from compiler.analysis.ty.context import TypeCtx
from compiler.analysis.unit.def_point import DefPoint
from compiler.codegen.cfg import ir as IR
from compiler.codegen.cfg.lower.builder import CfgBuilder
from compiler.codegen.cfg.lower.cfg_ctx import CfgCtx


class CfgTranslator:
    """把一批 HIR 定义降成 CFG 函数（`run` → `export` / `pass_contexts`）。"""

    def __init__(self, type_ctx: TypeCtx, raw_pointers: bool = False) -> None:
        # session 级事实放在 `CfgCtx` 里；每个函数再从它派生一份（`spawn`）
        self.__session = CfgCtx(type_ctx, raw_pointers)
        self.__functions: dict[int, IR.Function] = {}
        self.__contexts: dict[int, CfgCtx] = {}

    def run(self, def_points: dict[int, DefPoint]) -> None:
        """逐个 `DefPoint` 下降成 CFG 函数，并收好各自的 `CfgCtx`。"""
        for dp in def_points.values():
            ty = self.__session.type_ctx[dp.type_id]
            if not isinstance(ty, (Type.FunctionType, Type.MethodType)):
                raise ValueError(f"Unsupported def type: {type(ty).__name__}")
            ctx = self.__session.spawn()
            func = CfgBuilder(ctx, dp).build()
            self.__functions[func.type_id] = func
            self.__contexts[func.type_id] = ctx

    def export(self) -> dict[int, IR.Function]:
        """Return the translated functions keyed by type_id."""
        return dict(self.__functions)

    def pass_contexts(self) -> dict[int, CfgCtx]:
        """Return the per-function shared facts the following passes need, keyed by type_id."""
        return dict(self.__contexts)
