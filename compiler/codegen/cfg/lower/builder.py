"""单函数下降：把一个 HIR 函数体降成一条 CFG（`IR.Function`）。

构造时接收共享上下文 `CfgCtx` 与函数定义（`DefPoint`）：session 级资源从 ctx 读，
函数级事实由这里 `begin_def()` 写回同一个 ctx。各下降簇与它们共用的句柄也都注入
这个 ctx，因此下降期读到的就是后两段 pass 将要读的同一份事实。本类不跑 pass——
`passes/translator.py` 拿着同一个 ctx 往后传，编排在 `main`。
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


class CfgBuilder:
    """Per-function builder that lowers HIR statements/expressions into CFG IR."""

    def __init__(self, ctx: CfgCtx, dp: DefPoint) -> None:
        self.__ctx = ctx
        self.__dp = dp
        # 发射句柄：当前函数 / 当前块 / 命名计数器
        self.__emitter = FunctionEmitter()
        # 函数级事实写进共享上下文（返回类型即后处理 pass 的终结保护要用的事实）
        func_type = ctx.type_ctx[dp.type_id]
        assert isinstance(func_type, (Type.FunctionType, Type.MethodType))
        ctx.begin_def(
            symbol_ctx=dp.symbol_ctx,
            func_name=func_type.custom_def.name,
            span=dp.ast_body.span,
            return_type=func_type.return_type(ctx.type_ctx),
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

    # ------------------------------------------------------------------
    # public entry point
    # ------------------------------------------------------------------

    def build(self) -> IR.Function:
        ctx = self.__ctx
        dp = self.__dp
        entry_block = IR.Block("entry")
        self.__emitter.bind(
            IR.Function(name=ctx.func_name, type_id=dp.type_id, blocks=[entry_block], entry=entry_block),
            entry_block,
        )

        # ── register parameters ──
        self.__emitter.func.params = dp.params.copy()

        # ── register body local variables ──
        for local_id in dp.locals:
            symbol = ctx.symbol_ctx.get(local_id)
            self.__emitter.func.local_vars[local_id] = IR.VarRef(symbol.name, local_id, symbol.type_id)

        # ── translate the body ──
        assert dp.body is not None
        body_val = self.__stmts.translate_block(dp.body)

        if self.__emitter.current_block.terminator is None and ctx.return_type != TypeCtx.void_id:
            self.__set_terminator(IR.Ret(body_val))

        # ── 帧锁实体化标记 ──
        # 函数若实体化了帧锁,LLVM 层须在全部返回路径 ret 前
        # 写 SENTINEL 并从稳定影子栈弹出槽位,
        # 使栈悬垂访问经 live 键比较确定性失败。标记随函数传给 LLTranslator。
        self.__emitter.func.frame_lock = self.__values.frame_lock

        return self.__emitter.func

    # ------------------------------------------------------------------
    # SSA names & block helpers
    # ------------------------------------------------------------------

    def __set_terminator(self, term: IR.Terminator) -> None:
        # 块终结前的挂起义务补发与状态清空由检查插入 pass 在终结符处完成
        self.__emitter.terminate(term)

    def __switch_to(self, block: IR.Block) -> None:
        # 块切换 → 检查插入 pass 的块内状态不跨块（pass 自己按块清空）
        self.__emitter.position(block)

    # ------------------------------------------------------------------
    # block translation
    # ------------------------------------------------------------------

    def __build_func_ptr(self, func_type_id: int) -> IR.Value:
        func_ty = self.__ctx.type_ctx[func_type_id]
        assert isinstance(func_ty, Type.FunctionType)
        func_ptr_ty = func_ty.as_pointer(self.__ctx.type_ctx)
        result = IR.Reg(name=self.__emitter.new_name(), type_id=func_ptr_ty)
        return self.__emitter.emit(IR.FuncPtr(result=result, func_type_id=func_type_id)).result
