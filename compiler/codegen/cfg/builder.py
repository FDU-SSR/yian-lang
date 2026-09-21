"""
CFG IR builder — lowers a single HIR function/method body into a CFG Function.
"""
from __future__ import annotations

from dataclasses import dataclass
from compiler.analysis.ty import ty as Type
from compiler.analysis.ty.context import TypeCtx
from compiler.analysis.ty.type_ops import default_literals
from compiler.analysis.unit import hir as HIR
from compiler.analysis.unit.def_point import DefPoint
from compiler.codegen.cfg import ir as IR
from compiler.codegen.cfg.passes import PassContext, run_pipeline
from compiler.codegen.cfg.passes.checks import CheckState
from compiler.codegen.cfg.passes.emitter import FunctionEmitter
from compiler.codegen.cfg.lower.stmts import StmtHost, StmtLowerer
from compiler.codegen.cfg.lower.calls import CallsHost, CallsLowerer
from compiler.codegen.cfg.lower.memory import MemoryHost, MemoryLowerer
from compiler.codegen.cfg.lower.sys import SysHost, SysLowerer
from compiler.codegen.cfg.lower.values import ValueHost, ValueLowerer
from compiler.codegen.error import CodegenError
from compiler.error import CompilerError
from compiler.frontend.parse.operator import BinaryOperator, UnaryOperator
from compiler.utils.log import CompilerLog


def ch_cfg():
    return CompilerLog.get("cfg.logical")


def ch_cfg_block():
    return CompilerLog.get("cfg.block")


@dataclass
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
        # 检查簇状态（出处/活跃度 + 去重/合并/失效）整体交给 CheckState；
        # 原来的 7 个字段（raw_ptrs/frame_locked/live_known/fat_root/checked/
        # elem_derived/field_derived）及其判定都在 passes/checks.py 里。
        self.__checks = CheckState(self.__emitter)
        # 惰值/取址 + 聚合/转换下降簇（含帧锁实体化状态），经 ValueHost 借用构建器能力
        self.__values = ValueLowerer(ValueHost(
            emitter=self.__emitter,
            checks=self.__checks,
            type_ctx=self.__type_ctx,
            raw_pointers=self.__raw_pointers,
            resolve_val=self.__resolve_val,
            # 内存簇在 values 之后构造，这三处用晚绑定 lambda 打破构造环
            build_element_ptr=lambda base, off, ty: self.__memory.build_element_ptr(base, off, ty),
            build_field_ptr=lambda base, idx, ty: self.__memory.build_field_ptr(base, idx, ty),
            build_func_ptr=self.__build_func_ptr,
            build_extract_value=lambda base, idx, ty: self.__memory.build_extract_value(base, idx, ty),
            is_fat_pointer=self.__is_fat_pointer,
        ))
        # 内存/指针原语簇（C8）与调用簇、系统内建簇（C7）
        self.__memory = MemoryLowerer(MemoryHost(
            emitter=self.__emitter,
            checks=self.__checks,
            type_ctx=self.__type_ctx,
            raw_pointers=self.__raw_pointers,
            is_fat_pointer=self.__is_fat_pointer,
            values=self.__values,
        ))
        self.__calls = CallsLowerer(CallsHost(
            emitter=self.__emitter,
            checks=self.__checks,
            type_ctx=self.__type_ctx,
            resolve_val=self.__resolve_val,
            set_terminator=self.__set_terminator,
            is_fat_pointer=self.__is_fat_pointer,
            values=self.__values,
        ))
        self.__sys = SysLowerer(SysHost(
            emitter=self.__emitter,
            checks=self.__checks,
            resolve_val=self.__resolve_val,
            is_fat_view=self.__is_fat_view,
        ))
        # 语句/控制流下降簇（循环栈 + defer 作用域栈），经 StmtHost 借用构建器能力
        self.__stmts = StmtLowerer(StmtHost(
            emitter=self.__emitter,
            checks=self.__checks,
            set_terminator=self.__set_terminator,
            switch_to=self.__switch_to,
            resolve_val=self.__resolve_val,
            is_del_target=self.__is_del_target,
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

        # ── CFG pass 管线: 去死块 / RPO 排序 / 终结保护(C1, 见 passes/) ──
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
        # 检查合并:块终结前补发挂起 InBounds 义务并清空去重/合并表(状态不跨块)
        self.__checks.invalidate()
        self.__emitter.terminate(term)

    def __switch_to(self, block: IR.Block) -> None:
        # 检查合并:块切换 → 去重/合并状态清空(义务已由 __set_terminator 补发;此处为保守兜底)
        self.__checks.clear_block()
        self.__emitter.position(block)

    # ------------------------------------------------------------------
    # block translation
    # ------------------------------------------------------------------

    def __resolve_val(self, expr: HIR.Expr) -> IR.Value:
        """Lower *expr* to a value."""
        match expr:
            # --- expression-oriented control flow ---
            case HIR.Block():
                return self.__stmts.translate_block(expr)
            case HIR.If():
                return self.__stmts.translate_if(expr)
            case HIR.ComptimeIf() | HIR.CompileConfig():
                raise CodegenError("compile-time conditional was not specialized before CFG lowering", expr.span)
            case HIR.Loop():
                return self.__stmts.translate_loop(expr)
            case HIR.Match():
                return self.__stmts.translate_match(expr)
            case HIR.Return():
                return self.__stmts.translate_return(expr)
            case HIR.Break():
                return self.__stmts.translate_break(expr)
            case HIR.Continue():
                return self.__stmts.translate_continue(expr)
            case HIR.Defer():
                return self.__stmts.translate_defer(expr)
            case HIR.Delete():
                return self.__stmts.translate_delete(expr)
            case HIR.Panic():
                return self.__stmts.translate_panic(expr)
            case HIR.RuntimeFail():
                return self.__stmts.translate_runtime_fail(expr)
            case HIR.ProcessExit():
                return self.__stmts.translate_process_exit(expr)
            case HIR.Semi():
                return self.__stmts.translate_semi(expr)
            case HIR.Let():
                return self.__stmts.translate_let(expr)
            # --- original expressions ---
            case HIR.Binary():
                return self.__resolve_binary(expr)
            case HIR.Unary():
                return self.__resolve_unary(expr)
            case HIR.Call():
                return self.__calls.resolve_call(expr)
            case HIR.StructConstruct():
                return self.__values.resolve_struct_construct(expr)
            case HIR.Invoke():
                return self.__calls.resolve_invoke(expr)
            case HIR.Cast():
                return self.__values.resolve_cast(expr)
            case HIR.MethodCall():
                return self.__calls.resolve_method_call(expr)
            case HIR.VariantConstruct():
                return self.__values.resolve_variant_construct(expr)
            case HIR.FieldAccess():
                return self.__resolve_field_access(expr)
            case HIR.TupleAccess():
                return self.__resolve_tuple_access(expr)
            case HIR.ArrayAccess():
                return self.__resolve_array_access(expr)
            case HIR.SliceAccess():
                return self.__resolve_slice_access(expr)
            case HIR.DynValue():
                return self.__resolve_dyn_value(expr)
            case HIR.DynBuffer():
                return self.__resolve_dyn_buffer(expr)
            case HIR.SizeOf():
                return self.__values.resolve_size_of(expr)
            case HIR.BitCast():
                return self.__values.resolve_bit_cast(expr)
            case HIR.Alloc():
                return self.__resolve_alloc(expr)
            case HIR.AssumeInit():
                return self.__resolve_val(expr.value)
            case HIR.SysRead():
                return self.__sys.resolve_sys_read(expr)
            case HIR.SysWrite():
                return self.__sys.resolve_sys_write(expr)
            case HIR.MemCopy():
                return self.__sys.resolve_mem_copy(expr)
            case HIR.Open():
                return self.__sys.resolve_open(expr)
            case HIR.Close():
                return self.__sys.resolve_close(expr)
            case HIR.Sqrt():
                return self.__sys.resolve_sqrt(expr)
            case HIR.ArgCount():
                return self.__sys.resolve_arg_count(expr)
            case HIR.ArgBytes():
                return self.__sys.resolve_arg_bytes(expr)
            case HIR.Tuple():
                return self.__values.resolve_tuple(expr)
            case HIR.Array():
                return self.__values.resolve_array(expr)
            case HIR.ArrayRepeat():
                return self.__values.resolve_array_repeat(expr)
            case HIR.Var():
                return self.__resolve_var(expr)
            case HIR.IntLiteral() | HIR.FloatLiteral() | HIR.CharLiteral() | HIR.BoolLiteral() | HIR.StrLiteral():
                return self.__resolve_literal(expr)
            case HIR.Ty():
                if self.__type_ctx.is_zst(expr.type_id):
                    return IR.Reg(name=self.__emitter.new_name(), type_id=expr.type_id)
                raise CodegenError(f"Cannot resolve type expression: {expr}", expr.span)
            case HIR.Closure():
                raise CompilerError(f"Closure lowering should have been completed before CFG building: {expr}")

    def __resolve_binary(self, expr: HIR.Binary) -> IR.Value:
        """
        Special cases:

        - logical operators should implement short-circuit evaluation
        - assignment => load address of lhs, load value from rhs, store value to lhs
        - compound assignments should be implemented as binary operation + store
        - `in` and `..` should not be handled here
        """
        if expr.op.is_logical():
            return self.__resolve_logical(expr)
        if expr.op == BinaryOperator.Assign:
            return self.__resolve_assign(expr)
        if expr.op.is_compound_assign():
            return self.__resolve_compound_assign(expr)
        if expr.op in (BinaryOperator.In, BinaryOperator.Range):
            raise CodegenError(f"Cannot resolve expression: {expr}", expr.span)

        # common case
        lhs = self.__resolve_val(expr.left)
        rhs = self.__resolve_val(expr.right)
        return self.__memory.build_binary(expr.op, lhs, rhs, expr.type_id)

    def __resolve_compound_assign(self, expr: HIR.Binary) -> IR.Value:
        op = expr.op.compound_assign_to_binary()

        lhs_addr = self.__values.resolve_addr(expr.left)
        lhs_val = self.__memory.build_load(lhs_addr)
        rhs_val = self.__resolve_val(expr.right)

        result = self.__memory.build_binary(op, lhs_val, rhs_val, expr.type_id)
        self.__memory.build_store(result, lhs_addr)
        return result

    def __resolve_assign(self, expr: HIR.Binary) -> IR.Value:
        lhs_addr = self.__values.resolve_addr(expr.left)
        rhs_val = self.__resolve_val(expr.right)
        if rhs_val.type_id != self.__type_ctx.never_id:
            self.__memory.build_store(rhs_val, lhs_addr)
        return rhs_val

    def __resolve_logical(self, expr: HIR.Binary) -> IR.Value:
        """Lower short-circuit logical operators (&&, ||).

        For ``a && b``::

            br %a, rhs, merge      ; if false → short-circuit to false
          rhs:
            %b = resolve(b)
            br merge
          merge:
            %r = phi [(entry, false), (rhs, %b)]

        For ``a || b``::

            br %a, merge, rhs      ; if true → short-circuit to true
          rhs:
            %b = resolve(b)
            br merge
          merge:
            %r = phi [(entry, true), (rhs, %b)]
        """
        ch_cfg().trace(lambda: f"logical {expr.op} at {expr.span}")
        cond_val = self.__resolve_val(expr.left)

        rhs_block = self.__emitter.new_block("logical.rhs")
        merge_block = self.__emitter.new_block("logical.merge")
        entry_block = self.__emitter.current_block

        if expr.op == BinaryOperator.LogicalAnd:
            # a && b: evaluate b only when a is true
            self.__set_terminator(IR.CondBr(cond_val, rhs_block, merge_block))
            short_circuit_value: IR.Value = IR.BoolLiteral(value=False, type_id=TypeCtx.bool_id)
        else:
            # a || b: evaluate b only when a is false
            self.__set_terminator(IR.CondBr(cond_val, merge_block, rhs_block))
            short_circuit_value = IR.BoolLiteral(value=True, type_id=TypeCtx.bool_id)

        # ── rhs block ──
        self.__switch_to(rhs_block)
        rhs_val = self.__resolve_val(expr.right)
        # __resolve_val may have switched current_block (nested logical).
        # The block that actually produced rhs_val is where we ended up.
        rhs_end_block = self.__emitter.current_block

        # Bridge rhs_end_block to merge if it doesn't already have a terminator.
        if rhs_end_block.terminator is None:
            self.__set_terminator(IR.Br(merge_block))

        # ── merge block ──
        self.__switch_to(merge_block)
        result = self.__emitter.emit_phi([
            (entry_block, short_circuit_value),
            (rhs_end_block, rhs_val),
        ])
        # The merge block needs a terminator so it is not left dangling.
        # Create a continuation block that callers can append to.
        cont_block = self.__emitter.new_block("logical.cont")
        self.__set_terminator(IR.Br(cont_block))
        self.__switch_to(cont_block)
        return result

    def __resolve_unary(self, expr: HIR.Unary) -> IR.Value:
        """
        Special cases:

        - dereference => resolve pointer value then load through it
        - addr of lvalue => get address
        """
        if expr.op == UnaryOperator.Deref:
            ptr_val = self.__resolve_val(expr.operand)
            return self.__memory.build_load(ptr_val)
        if expr.op == UnaryOperator.AddrOf:
            return self.__values.resolve_addr_fat(expr.operand)

        # common case
        val = self.__resolve_val(expr.operand)
        return self.__memory.build_unary(expr.op, val, expr.type_id)

    def __resolve_field_access(self, expr: HIR.FieldAccess) -> IR.Value:
        if expr.receiver.is_place:
            addr = self.__values.resolve_field_access_addr(expr)
            return self.__memory.build_load(addr)

        value = self.__resolve_val(expr.receiver)
        return self.__memory.build_extract_value(value, expr.field.index, expr.type_id)

    def __resolve_tuple_access(self, expr: HIR.TupleAccess) -> IR.Value:
        if expr.receiver.is_place:
            receiver_ty = self.__type_ctx[expr.receiver.type_id]
            if isinstance(receiver_ty, (Type.SliceType, Type.StrType)):
                # 分级指针表示:slice/str 字段须从值提取(fieldptr 只能取裸字段地址,
                # 无法携带锁元数据)。读整个值再 extract_value。
                value = self.__resolve_val(expr.receiver)
                return self.__memory.build_extract_value(value, expr.index, expr.type_id)
            addr = self.__values.resolve_tuple_access_addr(expr)
            return self.__memory.build_load(addr)

        value = self.__resolve_val(expr.receiver)
        return self.__memory.build_extract_value(value, expr.index, expr.type_id)

    def __resolve_dyn_value(self, expr: HIR.DynValue) -> IR.Value:
        """
        1. malloc a buffer on the heap
        2. write the value to the buffer
        """
        size = IR.IntLiteral(1, type_id=TypeCtx.u64_id)
        value = self.__resolve_val(expr.value)
        buffer = self.__memory.build_malloc(value.type_id, size)
        self.__memory.build_store(value, buffer)
        return buffer

    def __resolve_dyn_buffer(self, expr: HIR.DynBuffer) -> IR.Value:
        """``dyn[n] value``: allocate ``n`` elements and bit-copy ``value`` into each.

        The initializer is evaluated exactly once (it dominates the fill loop);
        ``element is None`` is the ZST-only bare form, which has nothing to
        initialize. Supports a runtime ``n``: the loop bound is a value.
        """
        count = self.__resolve_val(expr.length)
        buffer = self.__memory.build_malloc(expr.element_type, count)
        if expr.element is None:
            return buffer
        value = self.__resolve_val(expr.element)
        if self.__type_ctx.is_zst(expr.element_type):
            return buffer

        index_slot = self.__values.build_alloca(
            IR.IntLiteral(value=0, type_id=TypeCtx.u64_id), fat=False
        )
        header = self.__emitter.new_block("dyn.fill.head")
        body = self.__emitter.new_block("dyn.fill.body")
        exit_block = self.__emitter.new_block("dyn.fill.exit")
        self.__set_terminator(IR.Br(header))

        self.__switch_to(header)
        index = self.__memory.build_load(index_slot)
        filled = self.__emitter.emit(IR.Binary(
            result=IR.Reg(name=self.__emitter.new_name(), type_id=TypeCtx.bool_id),
            op=BinaryOperator.Neq,
            lhs=index,
            rhs=count,
        )).result
        self.__set_terminator(IR.CondBr(filled, body, exit_block))

        # 填充循环写在刚分配的缓冲上:这段封闭区域里不存在 del, live 恒真,
        # 因此登记出处、让元素 store 只保留 in_bounds 检查(同帧锁的 live=False 形态)。
        fill_root = buffer.name if isinstance(buffer, IR.Reg) else None
        if fill_root is not None:
            self.__checks.mark_live_known(fill_root)
        self.__switch_to(body)
        elem_ptr_type = self.__type_ctx.alloc_pointer(expr.element_type)
        elem_ptr = self.__memory.build_element_ptr(buffer, index, elem_ptr_type)
        self.__memory.build_store(value, elem_ptr)
        next_index = self.__emitter.emit(IR.Binary(
            result=IR.Reg(name=self.__emitter.new_name(), type_id=TypeCtx.u64_id),
            op=BinaryOperator.Add,
            lhs=index,
            rhs=IR.IntLiteral(value=1, type_id=TypeCtx.u64_id),
        )).result
        self.__memory.build_store(next_index, index_slot)
        self.__set_terminator(IR.Br(header))

        self.__switch_to(exit_block)
        if fill_root is not None:
            self.__checks.unmark_live_known(fill_root)
        return buffer

    def __resolve_alloc(self, expr: HIR.Alloc) -> IR.Value:
        """``@alloc<T>(n)``: trusted raw allocation, payload left uninitialized."""
        size = self.__resolve_val(expr.count)
        return self.__memory.build_malloc(expr.element_type, size)

    def __resolve_var(self, expr: HIR.Var) -> IR.Value:
        addr = self.__values.resolve_var_addr(expr)
        return self.__memory.build_load(addr)

    def __resolve_literal(self, expr: HIR.Literal) -> IR.Value:
        match expr:
            case HIR.IntLiteral():
                type_id = default_literals(self.__type_ctx, expr.type_id)
                return IR.IntLiteral(value=expr.value, type_id=type_id)
            case HIR.FloatLiteral():
                type_id = default_literals(self.__type_ctx, expr.type_id)
                return IR.FloatLiteral(value=expr.value, type_id=type_id)
            case HIR.CharLiteral():
                return IR.CharLiteral(value=expr.value, type_id=TypeCtx.char_id)
            case HIR.BoolLiteral():
                return IR.BoolLiteral(value=expr.value, type_id=TypeCtx.bool_id)
            case HIR.StrLiteral():
                return IR.StringLiteral(value=expr.value, type_id=TypeCtx.str_id)

    # ------------------------------------------------------------------
    # addr resolvors
    # ------------------------------------------------------------------

    def __resolve_array_access(self, expr: HIR.ArrayAccess) -> IR.Value:
        addr = self.__values.resolve_array_access_addr(expr)
        return self.__memory.build_load(addr)

    def __resolve_slice_access(self, expr: HIR.SliceAccess) -> IR.Value:
        addr = self.__values.resolve_slice_access_addr(expr)
        return self.__memory.build_load(addr)

    def __is_fat_pointer(self, ptr: IR.Value) -> bool:
        """胖指针判定:PointerType 且 pointee 非 ZST。

        指针-to-ZST 保持 ZST,走既有快路径、无检查;
        FunctionPointerType 非数据指针、不含 5 字段元数据,排除在外。
        诊断模式 raw_pointers 下恒 False:指针一律按裸 8B 处理,全部
        Check*/WriteLockSlot/Delete 检查与 PtrCmp 路由一并关闭。
        惰性左值路径:裸指针寄存器(未取址左值)同样恒 False——
        裸地址无胖元数据,不可承载检查。
        """
        if self.__raw_pointers:
            return False
        if self.__checks.is_raw(ptr):
            return False
        ty = self.__type_ctx[ptr.type_id]
        if not isinstance(ty, Type.PointerType):
            return False
        return not self.__type_ctx.is_zst(ty.pointee_type)

    def __is_fat_view(self, value: IR.Value) -> bool:
        """Whether *value* carries checked slice/str metadata."""
        if self.__raw_pointers:
            return False
        ty = self.__type_ctx[value.type_id]
        return isinstance(ty, (Type.SliceType, Type.StrType))

    def __is_del_target(self, ptr: IR.Value) -> bool:
        """del 专属目标判定:非 ZST 的 PointerType / SliceType / RefType。

        视图释放路径:T[]/T& 与 T* 同样支持整块释放——释放动作(WriteLockSlot
        与 delete())只提取 FAT_LOCK_PTR=1,三族布局 data/lock_ptr/key 前缀相同。
        两个 raw 守卫完整复刻 __is_fat_pointer(上方):诊断模式 raw_pointers 恒
        False；惰性左值路径中的裸指针寄存器也恒为 False，否则 raw 下对裸 8B 指针
        发射 WriteLockSlot 会写 data[0],内存破坏。指针-to-ZST 同 __is_fat_pointer
        保持非胖(ZST 擦除为空结构,无字段可写、无检查可插)。
        """
        if self.__raw_pointers:
            return False
        if self.__checks.is_raw(ptr):
            return False
        # ZST pointers/references are erased to `{}` in LLVM.  Keep the IR
        # Delete for the backend's no-op path, but do not inspect fat fields.
        if self.__type_ctx.is_zst(ptr.type_id):
            return False
        ty = self.__type_ctx[ptr.type_id]
        return isinstance(ty, (Type.PointerType, Type.SliceType, Type.RefType))

    def __build_func_ptr(self, func_type_id: int) -> IR.Value:
        func_ty = self.__type_ctx[func_type_id]
        assert isinstance(func_ty, Type.FunctionType)
        func_ptr_ty = func_ty.as_pointer(self.__type_ctx)
        result = IR.Reg(name=self.__emitter.new_name(), type_id=func_ptr_ty)
        return self.__emitter.emit(IR.FuncPtr(result=result, func_type_id=func_type_id)).result
