"""下降 pass（管线的第 1 段）：把一批 HIR 定义整体降成 CFG。

入口 `CfgTranslator`：为每个 `DefPoint` 派生一份共享上下文 `CfgCtx`，交给私有 helper
`_CfgBuilder` 做单函数下降，再把函数与 ctx 一起收好供后两段 pass 取用。本段**只下降**，
不跑后续 pass——三段按序由 `compiler/main.py` 编排。

下降机器（各下降簇与它们共用的句柄）在兄弟目录 `cfg/lower/`；这里只放 pass 入口与它
自己的逐函数装配。

用法::

    cfg_lower = CfgTranslator(type_ctx, raw_pointers=...)
    cfg_lower.run(def_points)
    InsertChecks(cfg_lower.ctx).run()
    Cleanup(cfg_lower.ctx).run()
    functions = cfg_lower.export()          # dict[int, IR.Function]
"""
from __future__ import annotations

from compiler.analysis.ty import ty as Type
from compiler.analysis.ty.context import TypeCtx
from compiler.analysis.unit.def_point import DefPoint
from compiler.codegen.cfg import ir as IR
from compiler.codegen.cfg.lower.calls import CallsHost, CallsLowerer
from compiler.codegen.cfg.lower.cfg_ctx import CfgCtx
from compiler.codegen.cfg.lower.checks import CheckState
from compiler.codegen.cfg.lower.emitter import FunctionEmitter
from compiler.codegen.cfg.lower.exprs import ExprHost, ExprLowerer
from compiler.codegen.cfg.lower.memory import MemoryHost, MemoryLowerer
from compiler.codegen.cfg.lower.predicates import PtrPredicates
from compiler.codegen.cfg.lower.stmts import StmtHost, StmtLowerer
from compiler.codegen.cfg.lower.sys import SysHost, SysLowerer
from compiler.codegen.cfg.lower.values import ValueHost, ValueLowerer


class CfgTranslator:
    """把一批 HIR 定义降成 CFG 函数（`run` → `export`；共享上下文经 `ctx` 交出）。"""

    def __init__(self, type_ctx: TypeCtx, raw_pointers: bool = False) -> None:
        self.__ctx = CfgCtx(type_ctx, raw_pointers)

    @property
    def ctx(self) -> CfgCtx:
        """本模块的共享上下文——后两段 pass 直接拿它（函数表 + 每函数事实都在里面）。"""
        return self.__ctx

    def run(self, def_points: dict[int, DefPoint]) -> None:
        """逐个 `DefPoint` 下降成 CFG 函数，登记进 ctx 的函数表。"""
        for dp in def_points.values():
            ty = self.__ctx.type_ctx[dp.type_id]
            if not isinstance(ty, (Type.FunctionType, Type.MethodType, Type.ClosureType)):
                raise ValueError(f"Unsupported def type: {type(ty).__name__}")
            function = _CfgBuilder(self.__ctx, dp).build()
            self.__ctx.normalize_function(function)
            self.__ctx.declare_function(function)

    def export(self) -> dict[int, IR.Function]:
        """Return the translated functions keyed by type_id（函数表由 ctx 持有）。"""
        return dict(self.__ctx.functions)


# ---------------------------------------------------------------------------
# 逐函数装配（本 pass 的私有 helper）
# ---------------------------------------------------------------------------


class _CfgBuilder:
    """把一个 HIR 函数体降成一条 CFG（`IR.Function`）。

    构造时接收共享上下文 `CfgCtx` 与函数定义（`DefPoint`）：session 级资源从 ctx 读，
    函数级事实由这里 `begin_def()` 写回同一个 ctx。各下降簇与它们共用的句柄也都注入
    这个 ctx，因此下降期读到的就是后两段 pass 将要读的同一份事实。
    """

    def __init__(self, ctx: CfgCtx, dp: DefPoint) -> None:
        self.__ctx = ctx
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
                symbol = dp.symbol_ctx.lookup(captured.name)
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
        self.__emitter = FunctionEmitter()
        # 本函数的事实存进共享上下文并留一份自用（后两段 pass 从 ctx.facts 取同一份）
        self.__facts = ctx.begin_def(
            type_id=self.__function_type_id,
            symbol_ctx=dp.symbol_ctx,
            func_name=func_type.custom_def.name,
            span=dp.ast_body.span,
            return_type=ctx.cfg_type_id(func_type.return_type(ctx.type_ctx)),
            new_void_value=self.__emitter.void_reg,
        )
        # 下降侧指针出处（裸/帧内/出处根）；检查决定已归检查插入 pass。
        self.__checks = CheckState()
        # 指针族判定（无状态）
        self.__preds = PtrPredicates(ctx=ctx, checks=self.__checks)
        # 惰值/取址 + 聚合/转换下降簇（含帧锁实体化状态），经 ValueHost 借用构建器能力
        self.__values = ValueLowerer(ValueHost(
            ctx=ctx,
            emitter=self.__emitter,
            checks=self.__checks,
            resolve_val=lambda expr: self.__exprs.resolve_val(expr),
            # 内存簇在 values 之后构造，这三处用晚绑定 lambda 打破构造环
            build_element_ptr=lambda base, off, ty: self.__memory.build_element_ptr(base, off, ty),
            build_field_ptr=lambda base, idx, ty: self.__memory.build_field_ptr(base, idx, ty),
            build_func_ptr=self.__build_func_ptr,
            build_extract_value=lambda base, idx, ty: self.__memory.build_extract_value(base, idx, ty),
            is_fat_pointer=self.__preds.is_fat_pointer,
            closure_receiver_id=self.__closure_receiver_id,
            closure_receiver_ref_type_id=self.__closure_receiver_ref_type_id,
            closure_struct_type_id=self.__closure_struct_type_id,
            closure_capture_fields=self.__capture_fields,
        ))
        # 内存/指针原语簇与调用簇、系统内建簇
        self.__memory = MemoryLowerer(MemoryHost(
            ctx=ctx,
            emitter=self.__emitter,
            checks=self.__checks,
            is_fat_pointer=self.__preds.is_fat_pointer,
            values=self.__values,
        ))
        self.__calls = CallsLowerer(CallsHost(
            ctx=ctx,
            emitter=self.__emitter,
            checks=self.__checks,
            resolve_val=lambda expr: self.__exprs.resolve_val(expr),
            set_terminator=self.__set_terminator,
            values=self.__values,
        ))
        self.__sys = SysLowerer(SysHost(
            emitter=self.__emitter,
            checks=self.__checks,
            resolve_val=lambda expr: self.__exprs.resolve_val(expr),
            is_fat_view=self.__preds.is_fat_view,
        ))
        # 语句/控制流下降簇（循环栈 + defer 作用域栈），经 StmtHost 借用构建器能力
        self.__stmts = StmtLowerer(StmtHost(
            emitter=self.__emitter,
            checks=self.__checks,
            set_terminator=self.__set_terminator,
            switch_to=self.__switch_to,
            resolve_val=lambda expr: self.__exprs.resolve_val(expr),
        ))
        # 表达式下降簇（含分派器）：位于各簇之上，最后装配
        self.__exprs = ExprLowerer(ExprHost(
            ctx=ctx,
            emitter=self.__emitter,
            checks=self.__checks,
            stmts=self.__stmts,
            values=self.__values,
            memory=self.__memory,
            calls=self.__calls,
            sys=self.__sys,
            set_terminator=self.__set_terminator,
            switch_to=self.__switch_to,
        ))

    def build(self) -> IR.Function:
        facts = self.__facts
        dp = self.__dp
        entry_block = IR.Block("entry")
        self.__emitter.bind(
            IR.Function(name=facts.func_name, type_id=facts.type_id, blocks=[entry_block], entry=entry_block),
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
            symbol = facts.symbol_ctx.get(local_id)
            self.__emitter.func.local_vars[local_id] = IR.VarRef(symbol.name, local_id, symbol.type_id)

        # ── translate the body ──
        assert dp.body is not None
        body_val = self.__stmts.translate_block(dp.body)

        if self.__emitter.current_block.terminator is None and facts.return_type != TypeCtx.void_id:
            self.__set_terminator(IR.Ret(body_val))

        # ── 帧锁实体化标记 ──
        # 函数若实体化了帧锁,LLVM 层须在全部返回路径 ret 前
        # 写 SENTINEL 并从稳定影子栈弹出槽位,
        # 使栈悬垂访问经 live 键比较确定性失败。标记随函数传给 LLTranslator。
        self.__emitter.func.frame_lock = self.__values.frame_lock

        return self.__emitter.func

    def __set_terminator(self, term: IR.Terminator) -> None:
        # 块终结前的挂起义务补发与状态清空由检查插入 pass 在终结符处完成
        self.__emitter.terminate(term)

    def __switch_to(self, block: IR.Block) -> None:
        # 块切换 → 检查插入 pass 的块内状态不跨块（pass 自己按块清空）
        self.__emitter.position(block)

    def __build_func_ptr(self, func_type_id: int) -> IR.Value:
        func_ty = self.__ctx.type_ctx[func_type_id]
        assert isinstance(func_ty, Type.FunctionType)
        func_ptr_ty = func_ty.as_pointer(self.__ctx.type_ctx)
        result = IR.Reg(name=self.__emitter.new_name(), type_id=func_ptr_ty)
        return self.__emitter.emit(IR.FuncPtr(result=result, func_type_id=func_type_id)).result
