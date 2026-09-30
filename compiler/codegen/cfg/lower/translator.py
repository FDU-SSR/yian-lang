"""Build CFG functions from type-checked HIR definitions."""
from __future__ import annotations

from compiler.analysis.ty import ty as Type
from compiler.analysis.ty.context import TypeCtx
from compiler.analysis.unit.def_point import DefPoint
from compiler.codegen.cfg import ir as IR
from compiler.codegen.cfg.module import CfgFunction, CfgModule
from compiler.codegen.cfg.lower.session import LoweringSession
from compiler.codegen.cfg.lower.emitter import FunctionEmitter
from compiler.codegen.cfg.lower.exprs import ExprLowerer
from compiler.codegen.cfg.lower.state import FunctionState
from compiler.codegen.cfg.provenance import PointerFacts


class CfgTranslator:
    """Lower a collection of definitions into a CFG module."""

    def __init__(self, type_ctx: TypeCtx, raw_pointers: bool = False) -> None:
        self.__session = LoweringSession(type_ctx, raw_pointers)
        self.__module = CfgModule(type_ctx, raw_pointers)

    @property
    def module(self) -> CfgModule:
        return self.__module

    def run(self, def_points: dict[int, DefPoint]) -> None:
        """逐个 `DefPoint` 下降成 CFG 函数，登记进 ctx 的函数表。"""
        for dp in def_points.values():
            ty = self.__session.type_ctx[dp.type_id]
            if not isinstance(ty, (Type.FunctionType, Type.MethodType, Type.ClosureType)):
                raise ValueError(f"Unsupported def type: {type(ty).__name__}")
            record = FunctionBuilder(self.__session, dp).build()
            self.__session.normalize_function(record.function)
            self.__module.add(record)


# ---------------------------------------------------------------------------
# 逐函数装配
# ---------------------------------------------------------------------------


class FunctionBuilder:
    """Assemble one lowered function and its pass-visible metadata."""

    def __init__(self, ctx: LoweringSession, dp: DefPoint) -> None:
        self.__dp = dp
        semantic_type = ctx.type_ctx[dp.type_id]
        self.__closure_type = semantic_type if isinstance(semantic_type, Type.ClosureType) else None
        self.__function_type_id = ctx.cfg_function_type_id(dp.type_id)
        func_type = ctx.type_ctx[self.__function_type_id]
        assert isinstance(func_type, (Type.FunctionType, Type.MethodType))
        self.__capture_fields: dict[int, Type.StructField] = {}
        self.__closure_receiver_id: int | None = None
        self.__closure_receiver_ref_type_id: int | None = None
        self.__closure_struct_type_id: int | None = None
        self.__closure_parameter_ids: list[int] = []
        self.__captured_symbol_ids: set[int] = set()
        if self.__closure_type is not None:
            self.__closure_receiver_id = -1
            self.__closure_struct_type_id = self.__closure_type.struct_type_id
            self.__closure_receiver_ref_type_id = ctx.type_ctx.alloc_ref(self.__closure_struct_type_id)
            for captured in self.__closure_type.captured_vars:
                symbol = dp.symbol_ctx.lookup_typed(captured.name)
                field = ctx.type_ctx.get_struct_field_by_name(self.__closure_struct_type_id, captured.name)
                if symbol is None or field is None:
                    raise ValueError(f"Missing closure capture '{captured.name}' in CFG function")
                self.__captured_symbol_ids.add(symbol.symbol_id)
                self.__capture_fields[symbol.symbol_id] = field
            self.__closure_parameter_ids = [
                symbol_id for symbol_id in dp.params
                if symbol_id not in self.__captured_symbol_ids
            ]
            if len(self.__closure_parameter_ids) != len(self.__closure_type.parameters):
                raise ValueError("Closure CFG parameters do not match the checked closure signature")
        # 发射句柄：当前函数 / 当前块 / 命名计数器
        self.__pointers = PointerFacts(ctx.type_ctx, ctx.raw_pointers)
        self.__emitter = FunctionEmitter(self.__pointers)
        self.__func_name = func_type.custom_def.name
        self.__return_type = ctx.cfg_type_id(func_type.return_type(ctx.type_ctx))
        state = FunctionState(
            session=ctx,
            emitter=self.__emitter,
            pointers=self.__pointers,
            closure_receiver_id=self.__closure_receiver_id,
            closure_receiver_ref_type_id=self.__closure_receiver_ref_type_id,
            closure_struct_type_id=self.__closure_struct_type_id,
            closure_capture_fields=self.__capture_fields,
        )
        self.__exprs = ExprLowerer(state)

    def build(self) -> CfgFunction:
        dp = self.__dp
        entry_block = IR.Block("entry")
        self.__emitter.bind(
            IR.Function(name=self.__func_name, type_id=self.__function_type_id, blocks=[entry_block], entry=entry_block),
            entry_block,
        )

        # ── register parameters ──
        if self.__closure_type is None:
            self.__emitter.func.params = dp.params.copy()
        else:
            assert self.__closure_receiver_id is not None
            assert self.__closure_receiver_ref_type_id is not None
            self.__emitter.func.params = [self.__closure_receiver_id] + self.__closure_parameter_ids
            self.__emitter.func.local_vars[self.__closure_receiver_id] = IR.VarRef(
                "self", self.__closure_receiver_id, self.__closure_receiver_ref_type_id
            )

        # ── register body local variables ──
        for local_id in dp.locals:
            if local_id in self.__captured_symbol_ids:
                continue
            symbol = dp.symbol_ctx.get_typed(local_id)
            self.__emitter.func.local_vars[local_id] = IR.VarRef(symbol.name, local_id, symbol.type_id)

        # ── translate the body ──
        assert dp.body is not None
        body_val = self.__exprs.stmts.translate_block(dp.body)

        if self.__emitter.current_block.terminator is None and self.__return_type != TypeCtx.void_id:
            self.__emitter.terminate(IR.Ret(body_val))

        # ── 帧锁实体化标记 ──
        # 函数若实体化了帧锁,LLVM 层须在全部返回路径 ret 前
        # 写 SENTINEL 并从稳定影子栈弹出槽位,
        # 使栈悬垂访问经 live 键比较确定性失败。标记随函数传给 LLTranslator。
        self.__emitter.func.frame_lock = self.__exprs.values.frame_lock

        return CfgFunction(
            function=self.__emitter.func,
            span=dp.ast_body.span,
            return_type=self.__return_type,
            next_name=self.__emitter.counter,
        )
