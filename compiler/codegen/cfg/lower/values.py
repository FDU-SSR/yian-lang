"""C5+C6：惰值/取址与聚合/转换下降（HIR → CFG IR）。

从 `CfgBuilder` 整体搬出的两个簇：

- C5 惰值/取址：`resolve_addr`/`resolve_addr_fat` 与各 `resolve_*_addr`、
  `build_var_ptr_fat/raw`、`build_alloca`、`emit_frame_lock`、`build_gen_key`；
- C6 聚合/构造/转换：`resolve_cast`/`build_cast`/`resolve_bit_cast`、`build_size_of`、
  `build_{aggregate,array,variant}_construct`、`resolve_{tuple,array,array_repeat,
  struct_construct,variant_construct,size_of}`。

两个簇原先按计划分两个模块，实际它们**双向引用**（`resolve_*_addr` 要 `build_cast`，
`build_cast` 要 `is_fat_pointer`/`checks`），且共用同一份 host 面，因此合成一个模块、
一个 `ValueLowerer`：避免两个 host 互相持有。帧锁实体化状态（`frame_lock`）也随之搬入，
构建器只读它（`build()` 里写进 `IR.Function.frame_lock`）。

搬移保持逐条等价（见 `docs/plan/cfg-builder-pass-split-plan.md`）。
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from compiler.analysis.ty import ty as Type
from compiler.analysis.ty.context import TypeCtx
from compiler.analysis.unit import hir as HIR
from compiler.codegen.cfg import ir as IR
from compiler.codegen.cfg.passes.checks import CheckState
from compiler.codegen.cfg.passes.emitter import FunctionEmitter
from compiler.codegen.error import CodegenError
from compiler.frontend.parse.operator import UnaryOperator
from compiler.utils.log import CompilerLog


def _ch_block():
    """cfg.block 日志通道（类体内引用，按约定用单下划线）。"""
    return CompilerLog.get("cfg.block")


@dataclass(frozen=True)
class ValueHost:
    """惰值/聚合下降需要从构建器借用的能力（只读句柄 + 回调）。"""

    emitter: FunctionEmitter
    checks: CheckState
    type_ctx: TypeCtx
    raw_pointers: bool
    resolve_val: Callable[[HIR.Expr], IR.Value]
    build_element_ptr: Callable[[IR.Value, IR.Value, int], IR.Value]
    build_field_ptr: Callable[[IR.Value, int, int], IR.Value]
    build_func_ptr: Callable[[int], IR.Value]
    build_extract_value: Callable[[IR.Value, int, int], IR.Value]
    is_fat_pointer: Callable[[IR.Value], bool]


class ValueLowerer:
    """惰值/取址 + 聚合/构造/转换下降器（持有帧锁实体化状态）。"""

    def __init__(self, host: ValueHost) -> None:
        self.__host = host
        self.__frame_lock: tuple[IR.Value, IR.Value] | None = None  # ⟨e_f, k_f⟩:函数入口帧锁实体化

    @property
    def frame_lock(self) -> tuple[IR.Value, IR.Value] | None:
        """帧锁寄存器对（构建器在 build() 末尾写入 IR.Function.frame_lock）。"""
        return self.__frame_lock

    def resolve_addr(self, expr: HIR.Expr) -> IR.Value:
        """Lower *expr* to an address.

        - for lvalue expressions, this is the address of the lvalue
        - for rvalue expressions, allocates a temporary and copies the value to it
        """
        if isinstance(expr, HIR.ArrayAccess):
            return self.resolve_array_access_addr(expr)
        if isinstance(expr, HIR.SliceAccess):
            return self.resolve_slice_access_addr(expr)

        if not expr.is_place:
            val = self.__host.resolve_val(expr)
            return self.build_alloca(val, fat=False)

        # resolve expr that produces an address
        match expr:
            case HIR.Unary() if expr.op == UnaryOperator.Deref:
                return self.resolve_deref_addr(expr)
            case HIR.FieldAccess():
                return self.resolve_field_access_addr(expr)
            case HIR.TupleAccess():
                return self.resolve_tuple_access_addr(expr)
            case HIR.Var():
                return self.resolve_var_addr(expr)
            case HIR.Ty():
                return self.__host.build_func_ptr(expr.type_id)
            case _:
                raise CodegenError(f"Cannot resolve address of expression: {expr}", expr.span)

    def resolve_addr_fat(self, expr: HIR.Expr) -> IR.Value:
        """惰性左值路径:恒胖地址解析——显式 &x(AddrOf)与方法 self receiver。

        与 __resolve_addr(自然形态,裸变量→裸地址)不同:变量的地址一律合成
        5 字段胖指针(首次取址惰性实体化帧锁),派生地址(field/array/deref)
        沿胖基址传播,后续 Load/Store/FieldPtr/ElementPtr 检查全保留。
        """
        # SliceAccess is value-shaped in HIR because ordinary indexing loads an
        # element.  When it is the operand of AddrOf, however, preserve the
        # element address instead of materializing a temporary and taking that
        # temporary's address.
        if isinstance(expr, HIR.SliceAccess):
            return self.resolve_slice_access_addr(expr, fat=True)
        if not expr.is_place:
            val = self.__host.resolve_val(expr)
            return self.build_alloca(val, fat=True)

        match expr:
            case HIR.Unary() if expr.op == UnaryOperator.Deref:
                return self.resolve_deref_addr(expr)
            case HIR.FieldAccess():
                return self.resolve_field_access_addr(expr, fat=True)
            case HIR.TupleAccess():
                return self.resolve_tuple_access_addr(expr, fat=True)
            case HIR.ArrayAccess():
                return self.resolve_array_access_addr(expr, fat=True)
            case HIR.Var():
                return self.resolve_var_addr(expr, fat=True)
            case HIR.Ty():
                return self.__host.build_func_ptr(expr.type_id)
            case _:
                raise CodegenError(f"Cannot resolve address of expression: {expr}", expr.span)

    # ------------------------------------------------------------------
    # value resolvors
    # ------------------------------------------------------------------

    def resolve_deref_addr(self, expr: HIR.Unary) -> IR.Value:
        return self.__host.resolve_val(expr.operand)

    def resolve_field_access_addr(self, expr: HIR.FieldAccess, *, fat: bool = False) -> IR.Value:
        # 惰性左值路径:fat=True(显式 & / 方法 receiver)时沿胖基址传播;
        # 自然形态下基址裸/胖随其形态——裸变量 → 裸字段地址,胖指针(Deref 后)→ 胖。
        base_addr = self.resolve_addr_fat(expr.receiver) if fat else self.resolve_addr(expr.receiver)
        return self.__host.build_field_ptr(base_addr, expr.field.index, expr.type_id)

    def resolve_tuple_access_addr(self, expr: HIR.TupleAccess, *, fat: bool = False) -> IR.Value:
        base_addr = self.resolve_addr_fat(expr.receiver) if fat else self.resolve_addr(expr.receiver)
        return self.__host.build_field_ptr(base_addr, expr.index, expr.type_id)

    def resolve_array_access_addr(self, expr: HIR.ArrayAccess, *, fat: bool = False) -> IR.Value:
        # T[N] 元素地址:数组指针退化为 T* 后按元素索引(LLVM cast 数组退化
        # 重锚定 data + size=N,等价旧 trait 路径的 bitcast<T*> + p + *index)。
        # 惰性左值路径:裸数组基址(普通数组变量)走裸位转换 + 编译期
        # 越界检查;胖基址(Deref 后)走原退化 + ElementPtr 良构检查。
        base_addr = self.resolve_addr_fat(expr.array) if fat else self.resolve_addr(expr.array)
        elem_ptr_type = self.__host.type_ctx.alloc_pointer(expr.element_type)
        elem_base = self.build_cast(base_addr, elem_ptr_type)
        index_val = self.__host.resolve_val(expr.index)
        if not fat and self.__host.checks.is_raw(base_addr):
            self.__host.emitter.emit(IR.CheckRawBounds(index=index_val, length=expr.length))
            _ch_block().debug(lambda: "check insert ArrayAccess(raw): index < length (裸数组越界)")
        return self.__host.build_element_ptr(elem_base, index_val, elem_ptr_type)

    def resolve_slice_access_addr(self, expr: HIR.SliceAccess, *, fat: bool = False) -> IR.Value:
        """T[] 元素地址内建解析（切片索引内联优化）。

        镜像 slice.an as_struct+ptr+index 链的净效应,但内联在调用点,消除
        emit_object 无优化时逐次全栈调用开销。实现:
        1. 解析切片值(fat 4 字段 {data,lock,key,size} / raw 2 字段 {data,size});
        2. 提取 data 字段(fat 下经 __slice_ptr_fat 合成 5 字段胖指针,携带切片
           真锁,锁继承语义与 as_struct 一致;raw 下为裸指针);
        3. __build_element_ptr → CheckElementArith + ElementPtr 得元素地址。
        调用方后续 fieldptr/load/store 照常触发 CheckInBounds/CheckSafeAccess。
        """
        slice_val = self.__host.resolve_val(expr.slice)
        index_val = self.__host.resolve_val(expr.index)
        elem_ptr_type = self.__host.type_ctx.alloc_pointer(expr.element_type)
        data = self.__host.build_extract_value(slice_val, 0, elem_ptr_type)
        return self.__host.build_element_ptr(data, index_val, elem_ptr_type)

    def resolve_var_addr(self, expr: HIR.Var, *, fat: bool = False) -> IR.Value:
        if expr.symbol_id not in self.__host.emitter.func.local_vars:
            raise CodegenError(f"Undefined variable: {expr.symbol_id}", expr.span)
        var_ref = self.__host.emitter.func.local_vars[expr.symbol_id]
        if fat:
            return self.build_var_ptr_fat(var_ref)
        return self.build_var_ptr_raw(var_ref)

    # ------------------------------------------------------------------
    # ir building helpers
    # ------------------------------------------------------------------

    def build_var_ptr_fat(self, var_ref: IR.VarRef) -> IR.Value:
        """取局部变量槽地址并合成 5 字段胖指针 ⟨a_x, e_f, k_f, 0, 1⟩。

        data = 槽地址 a_x;lock_ptr/key = 当前帧锁 ⟨e_f, k_f⟩(首次取址时惰性
        实体化于函数入口);index = 0;size = 1(取址总是指向单个元素,含数组取址)。
        LLVM 下降由 LLVM 层完成。惰性左值路径:显式 &x 与方法 receiver 专用。
        """
        e_f, k_f = self.emit_frame_lock()
        result = IR.Reg(name=self.__host.emitter.new_name(), type_id=self.__host.type_ctx.alloc_pointer(var_ref.type_id))
        fat = self.__host.emitter.emit(IR.VarPtr(
            result=result, var_ref=var_ref, frame_word=e_f, frame_key=k_f,
        )).result
        self.__host.checks.mark_frame_locked(fat)
        self.__host.checks.mark_root(fat)
        return fat

    def build_var_ptr_raw(self, var_ref: IR.VarRef) -> IR.Value:
        """惰性左值路径:裸取址——未取址左值(赋值/读取/字段派生基址)
        仅返回栈槽地址,不合成 5 字段、不触发帧锁实体化(帧锁延迟到真正需要
        胖指针的 AddrOf/方法 receiver 首次取址)。裸指针无胖元数据,检查跳过。
        """
        result = IR.Reg(name=self.__host.emitter.new_name(), type_id=self.__host.type_ctx.alloc_pointer(var_ref.type_id))
        raw_ptr = self.__host.emitter.emit(IR.VarPtr(
            result=result, var_ref=var_ref, frame_word=None, frame_key=None, raw=True,
        )).result
        self.__host.checks.mark_raw(raw_ptr)
        return raw_ptr

    def build_alloca(self, value: IR.Value, *, fat: bool) -> IR.Value:
        result = IR.Reg(name=self.__host.emitter.new_name(), type_id=self.__host.type_ctx.alloc_pointer(value.type_id))
        if fat and not self.__host.raw_pointers:
            frame_word, frame_key = self.emit_frame_lock()
            addr = self.__host.emitter.emit(IR.Alloca(
                result=result,
                value=value,
                frame_word=frame_word,
                frame_key=frame_key,
            )).result
            self.__host.checks.mark_frame_locked(addr)
            self.__host.checks.mark_root(addr)
        else:
            addr = self.__host.emitter.emit(IR.Alloca(result=result, value=value, raw=True)).result
            self.__host.checks.mark_raw(addr)
        return addr

    def emit_frame_lock(self) -> tuple[IR.Value | None, IR.Value | None]:
        """帧锁实体化:k_f ← Gen()(栈键 MSB 0),从独立
        稳定影子栈 push 一个 u64 锁槽并写入 k_f。仅在首次
        取址(VarPtr)时惰性触发,实体化语句插入入口块语句最前——先于正文与
        终止符;无取址的函数不含帧锁节点。VarPtr 的 frame_key 已是
        GenKey 结果。帧退出写 SENTINEL 并 pop(全部返回路径)
        的发射由 LLVM 层完成。
        raw 模式:无帧锁——直接返回 None 帧字段,不实体化
        GenKey/AcquireFrameLock。"""
        if self.__host.raw_pointers:
            return (None, None)
        if self.__frame_lock is not None:
            return self.__frame_lock
        saved_block = self.__host.emitter.current_block
        self.__host.emitter.current_block = self.__host.emitter.func.entry
        k_f = self.build_gen_key(is_heap=False)
        e_f_result = IR.Reg(
            name=self.__host.emitter.new_name(),
            type_id=TypeCtx.u64_id,  # 帧 word(整字)
        )
        e_f = self.__host.emitter.emit(IR.AcquireFrameLock(result=e_f_result, key=k_f)).result
        self.__host.emitter.current_block = saved_block
        entry = self.__host.emitter.func.entry
        frame_stmts = entry.stmts[-2:]
        del entry.stmts[-2:]
        entry.stmts[0:0] = frame_stmts
        self.__frame_lock = (e_f, k_f)
        return (e_f, k_f)

    def build_gen_key(self, is_heap: bool) -> IR.Value:
        """k ← Gen():堆键 MSB 1 / 栈键 MSB 0。"""
        result = IR.Reg(name=self.__host.emitter.new_name(), type_id=TypeCtx.u64_id)
        return self.__host.emitter.emit(IR.GenKey(result=result, is_heap=is_heap)).result

    def resolve_cast(self, expr: HIR.Cast) -> IR.Value:
        value = self.__host.resolve_val(expr.value)
        return self.build_cast(value, expr.target_type)

    def build_cast(self, value: IR.Value, to_type: int) -> IR.Value:
        # CFG 层:Cast 指针→指针语义 ——ptr-to-T ↔ ptr-to-U(均非 ZST)= identity
        #   (5 字段结构重贴,LLVM 类型同为 {i8*,i8*,i64,i64,i64});涉及 ptr-to-ZST
        #   = undef 例外(消除 LLVM size 不匹配风险)。CFG 层定义语义,发射由 LLVM 层完成。
        # 惰性左值路径:裸源强转标 raw——LLVM 层位转换(不合成胖值);
        # 裸性沿转换传播(裸数组退化基址的派生保持裸)。
        to_resolved = self.__host.type_ctx.resolve_aliases(to_type)
        if isinstance(self.__host.type_ctx[to_resolved], Type.PointerType):
            _ch_block().debug(lambda: "cast ptr→ptr: identity (5 字段重贴) / ptr-to-ZST 例外 = undef")
        raw = self.__host.checks.is_raw(value)
        result = IR.Reg(name=self.__host.emitter.new_name(), type_id=to_type)
        cast = self.__host.emitter.emit(IR.Cast(result=result, value=value, to_type=to_type, raw=raw)).result
        if raw:
            self.__host.checks.mark_raw(cast)
        else:
            self.__host.checks.inherit_frame_lock(cast, value)
            self.__host.checks.inherit_root(cast, value)
        # 嵌套派生链(安全修复 复核):ptr→ptr cast = identity(5 字段重贴),
        # 挂起义务沿 cast 传播——(ptr+k).a[j] 的 elementptr base 是 cast
        # 结果时,义务仍可被访问点合并或提前补发。
        if self.__host.is_fat_pointer(cast):
            self.__host.checks.propagate_field_derived(cast, value)
        return cast

    def resolve_bit_cast(self, expr: HIR.BitCast) -> IR.Value:
        value = self.__host.resolve_val(expr.value)
        source_type = self.__host.type_ctx[self.__host.type_ctx.resolve_aliases(value.type_id)]
        target_type = self.__host.type_ctx[self.__host.type_ctx.resolve_aliases(expr.type_id)]
        if not self.__host.raw_pointers:
            if isinstance(source_type, Type.PointerType) and isinstance(target_type, Type.RefType):
                # T& drops index/size, so the source must denote a real element
                # rather than the legal one-past pointer value.
                self.__host.emitter.emit(IR.CheckInBounds(ptr=value))
            elif isinstance(source_type, Type.SliceType) and isinstance(target_type, Type.RefType):
                # An empty slice has no element from which a reference can be
                # formed.  Establish this before dropping the size field.
                self.__host.emitter.emit(IR.CheckSliceNonEmpty(ptr=value))
        return self.build_cast(value, expr.type_id)

    def build_size_of(self, type_id: int) -> IR.Value:
        result = IR.Reg(name=self.__host.emitter.new_name(), type_id=TypeCtx.u64_id)
        return self.__host.emitter.emit(IR.SizeOf(result=result, type_id=type_id)).result

    def build_aggregate_construct(self, type_id: int, fields: list[IR.Value]) -> IR.Value:
        result = IR.Reg(name=self.__host.emitter.new_name(), type_id=type_id)
        return self.__host.emitter.emit(IR.AggregateConstruct(result=result, type_id=type_id, fields=fields)).result

    def build_array_construct(self, type_id: int, elements: list[IR.Value]) -> IR.Value:
        result = IR.Reg(name=self.__host.emitter.new_name(), type_id=type_id)
        return self.__host.emitter.emit(IR.ArrayConstruct(result=result, type_id=type_id, elements=elements)).result

    def build_variant_construct(self, enum_type: int, variant: Type.EnumVariant, payload_fields: list[IR.Value] | None, result_type: int) -> IR.Value:
        result = IR.Reg(name=self.__host.emitter.new_name(), type_id=result_type)
        return self.__host.emitter.emit(IR.VariantConstruct(result=result, enum_type=enum_type, variant=variant, payload_fields=payload_fields)).result

    def resolve_tuple(self, expr: HIR.Tuple) -> IR.Value:
        field_vals = [self.__host.resolve_val(field) for field in expr.field_values]
        tuple_type = self.__host.type_ctx[expr.type_id]
        if (
            isinstance(tuple_type, (Type.SliceType, Type.StrType))
            and len(field_vals) == 2
            and self.__host.is_fat_pointer(field_vals[0])
        ):
            # ``@slice_from_parts``/``@str_from_parts`` are trusted metadata
            # constructors.  Keep the requested view within the source
            # pointer's remaining extent before publishing its size field.
            self.__host.emitter.emit(IR.CheckElementArith(base=field_vals[0], offset=field_vals[1]))
            _ch_block().debug(lambda: "check insert slice construction: source extent + requested length")
        return self.build_aggregate_construct(expr.type_id, field_vals)

    def resolve_array(self, expr: HIR.Array) -> IR.Value:
        elements = [self.__host.resolve_val(element) for element in expr.elements]
        return self.build_array_construct(expr.type_id, elements)

    def resolve_array_repeat(self, expr: HIR.ArrayRepeat) -> IR.Value:
        elem_val = self.__host.resolve_val(expr.element)
        array_ty = self.__host.type_ctx[expr.type_id]
        assert isinstance(array_ty, Type.ArrayType)
        length_ty = self.__host.type_ctx[array_ty.length]
        assert isinstance(length_ty, Type.LiteralValueType), (
            f"array repeat count must be concrete at codegen, got {type(length_ty).__name__}"
        )
        elements = [elem_val] * length_ty.value
        return self.build_array_construct(expr.type_id, elements)

    def resolve_struct_construct(self, expr: HIR.StructConstruct) -> IR.Value:
        struct_type = self.__host.type_ctx[expr.struct_id]
        assert isinstance(struct_type, Type.StructType)
        fields = self.__host.type_ctx.get_struct_fields(expr.struct_id)
        field_vals = [self.__host.resolve_val(expr.field_values[field.name]) for field in fields]
        return self.build_aggregate_construct(expr.struct_id, field_vals)

    def resolve_variant_construct(self, expr: HIR.VariantConstruct) -> IR.Value:
        if expr.args is None:
            payload_fields = None
        else:
            assert expr.variant.payload_type is not None
            payload_type = self.__host.type_ctx[expr.variant.payload_type]
            assert isinstance(payload_type, Type.StructType)
            fields = self.__host.type_ctx.get_struct_fields(expr.variant.payload_type)
            payload_fields = [self.__host.resolve_val(expr.args[field.name]) for field in fields]

        return self.build_variant_construct(expr.enum_id, expr.variant, payload_fields, expr.type_id)

    def resolve_size_of(self, expr: HIR.SizeOf) -> IR.Value:
        return self.build_size_of(expr.target_type)
