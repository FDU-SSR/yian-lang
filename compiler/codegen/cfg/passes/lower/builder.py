"""下降 pass 的入口：把一个 HIR 函数体降成一条 CFG（`IR.Function`）。

构造时接收类型上下文与函数定义（`DefPoint`），把各下降簇与它们共用的句柄装配好；
`build()` 按 HIR 语句/表达式产生 CFG，随后调用 `run_pipeline` 交给检查插入与后处理。
"""
from __future__ import annotations

from compiler.analysis.ty import ty as Type
from compiler.analysis.ty.context import TypeCtx
from compiler.analysis.unit.def_point import DefPoint
from compiler.codegen.cfg import ir as IR
from compiler.codegen.cfg.passes import PassContext, run_pipeline
from compiler.codegen.cfg.passes.lower.checks import CheckState
from compiler.codegen.cfg.passes.lower.emitter import FunctionEmitter
from compiler.codegen.cfg.passes.lower.predicates import PtrPredicates
from compiler.codegen.cfg.passes.lower.stmts import StmtHost, StmtLowerer
from compiler.codegen.cfg.passes.lower.calls import CallsHost, CallsLowerer
from compiler.codegen.cfg.passes.lower.exprs import ExprHost, ExprLowerer
from compiler.codegen.cfg.passes.lower.memory import MemoryHost, MemoryLowerer
from compiler.codegen.cfg.passes.lower.sys import SysHost, SysLowerer
from compiler.codegen.cfg.passes.lower.values import ValueHost, ValueLowerer


class CfgBuilder:
    """Per-function builder that lowers HIR statements/expressions into CFG IR."""

    def __init__(self, type_ctx: TypeCtx, dp: DefPoint, func_name: str, raw_pointers: bool = False) -> None:
        self.__type_ctx = type_ctx
        self.__symbol_ctx = dp.symbol_ctx
        self.__dp = dp
        self.__func_name = func_name
        # 诊断模式开关：开启时指针一律按裸 8B 处理，不生成检查、锁槽或帧锁。
        # 生产环境不应使用。
        self.__raw_pointers = raw_pointers
        # 发射句柄：当前函数/当前块/命名计数器（原 self.__func / __current_block / __counter）
        self.__emitter = FunctionEmitter()
        # 下降侧指针出处（裸/帧内/出处根）；检查决定已归检查插入 pass。
        self.__checks = CheckState()
        # 指针族判定（无状态）
        self.__preds = PtrPredicates(
            type_ctx=self.__type_ctx,
            raw_pointers=self.__raw_pointers,
            checks=self.__checks,
        )
        # 惰值/取址 + 聚合/转换下降簇（含帧锁实体化状态），经 ValueHost 借用构建器能力
        self.__values = ValueLowerer(ValueHost(
            emitter=self.__emitter,
            checks=self.__checks,
            type_ctx=self.__type_ctx,
            raw_pointers=self.__raw_pointers,
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
            emitter=self.__emitter,
            checks=self.__checks,
            type_ctx=self.__type_ctx,
            raw_pointers=self.__raw_pointers,
            is_fat_pointer=self.__preds.is_fat_pointer,
            values=self.__values,
        ))
        self.__calls = CallsLowerer(CallsHost(
            emitter=self.__emitter,
            checks=self.__checks,
            type_ctx=self.__type_ctx,
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
            emitter=self.__emitter,
            checks=self.__checks,
            type_ctx=self.__type_ctx,
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
        dp = self.__dp
        entry_block = IR.Block("entry")
        self.__emitter.bind(
            IR.Function(name=self.__func_name, type_id=dp.type_id, blocks=[entry_block], entry=entry_block),
            entry_block,
        )

        # ── register parameters ──
        self.__emitter.func.params = dp.params.copy()

        # ── register body local variables ──
        for local_id in dp.locals:
            symbol = self.__symbol_ctx.get(local_id)
            self.__emitter.func.local_vars[local_id] = IR.VarRef(symbol.name, local_id, symbol.type_id)

        # ── translate the body ──
        assert dp.body is not None
        body_val = self.__stmts.translate_block(dp.body)

        func_type = self.__type_ctx[dp.type_id]
        assert isinstance(func_type, (Type.FunctionType, Type.MethodType))
        ret_ty = func_type.return_type(self.__type_ctx)
        if self.__emitter.current_block.terminator is None and ret_ty != TypeCtx.void_id:
            self.__set_terminator(IR.Ret(body_val))

        # ── CFG pass 管线: 检查插入 → 去死块 / RPO 排序 / 终结保护(见 passes/) ──
        run_pipeline(self.__emitter.func, PassContext(
            type_ctx=self.__type_ctx,
            symbol_ctx=self.__symbol_ctx,
            raw_pointers=self.__raw_pointers,
            func_name=self.__func_name,
            span=dp.ast_body.span,
            return_type=ret_ty,
            new_void_value=self.__emitter.void_reg,
        ))

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
        func_ty = self.__type_ctx[func_type_id]
        assert isinstance(func_ty, Type.FunctionType)
        func_ptr_ty = func_ty.as_pointer(self.__type_ctx)
        result = IR.Reg(name=self.__emitter.new_name(), type_id=func_ptr_ty)
        return self.__emitter.emit(IR.FuncPtr(result=result, func_type_id=func_type_id)).result
