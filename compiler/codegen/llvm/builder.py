"""
LLBuilder — wraps llvmlite ``ir.IRBuilder`` to hide LLVM-level details.
"""

from __future__ import annotations

from enum import Enum, auto

from llvmlite import ir

from compiler.analysis.ty import ty as Type
from compiler.analysis.ty.context import TypeCtx
from compiler.codegen.cfg import ir as IR
from compiler.codegen.llvm.intrinsics import IntrinsicKind
from compiler.codegen.llvm.module import LLFunction, LLModule
from compiler.codegen.llvm.types import LLTypeCtx
from compiler.codegen.llvm.value import LLValue
from compiler.frontend.parse.operator import BinaryOperator, UnaryOperator


class BuilderPosition(Enum):
    End = auto()
    First = auto()
    Phi = auto()


class LLBuilder:
    """High-level builder that emits LLVM IR for a single function."""

    def __init__(self, func: LLFunction, module: LLModule, ll_type_ctx: LLTypeCtx, type_ctx: TypeCtx) -> None:
        self.__func = func
        self.__module = module
        self.__ll_type_ctx = ll_type_ctx
        self.__type_ctx = type_ctx
        self.__builder: ir.IRBuilder
        self.__check_seq = 0
        self.__continuations: dict[str, str] = {}
        self.__current_cfg_block = ""
        self.__frame_lock_slot_name: str | None = None  # 已实体化帧锁的 e_f 寄存器名(入口 alloca,规则 3.7.1)

    # ------------------------------------------------------------------
    # constants
    # ------------------------------------------------------------------

    def i32(self, v: int) -> LLValue:
        return LLValue(self.__type_ctx.u32_id, ir.Constant(ir.IntType(32), v))  # type: ignore

    def i64(self, v: int) -> LLValue:
        return LLValue(self.__type_ctx.u64_id, ir.Constant(ir.IntType(64), v))  # type: ignore

    def undef(self, type_id: int) -> LLValue:
        return LLValue(type_id, ir.Constant(self.__ll_type_ctx.get_ll_type(type_id).ir_type, ir.Undefined))  # type: ignore

    def sizeof_const(self, type_id: int, result: str) -> None:
        self.__func.set_reg(result, LLValue(self.__type_ctx.u64_id, ir.Constant(ir.IntType(64), self.__ll_type_ctx.get_type_size(type_id))))  # type: ignore

    # ------------------------------------------------------------------
    # fat pointer helpers (5 字段胖指针值层下降,t8)
    # ------------------------------------------------------------------

    def __is_fat(self, ll_val: LLValue) -> bool:
        """判定 LLVM 值是否已是 5 字段胖指针聚合(40B 结构值)。

        胖指针 = PointerType 且其 LLVM 值形态为 LiteralStructType;从 slice/str
        提取的裸 8B 指针(type_id 同为 PointerType)按 LLVM 值形态区分,不误判。
        """
        return (
            isinstance(self.__type_ctx[ll_val.type_id], Type.PointerType)
            and isinstance(ll_val.ir_val.type, ir.LiteralStructType)  # type: ignore
        )

    def __is_fat_type(self, type_id: int) -> bool:
        """类型层胖指针判定:PointerType 且 pointee 非 ZST(与 CFG 层 __is_fat_pointer 对应)。"""
        ty = self.__type_ctx[type_id]
        return isinstance(ty, Type.PointerType) and not self.__type_ctx.is_zst(ty.pointee_type)

    def __extract_fat_field(self, ll_val: LLValue, index: int) -> LLValue:
        """提取胖指针聚合字段;整数字段 u64、指针字段 u8*(与 FAT_* 下标约定一致)。"""
        ir_val = self.__builder.extract_value(ll_val.ir_val, index)  # type: ignore
        if index in (IR.FAT_DATA, IR.FAT_LOCK_PTR):
            field_type = self.__type_ctx.alloc_pointer(self.__type_ctx.u8_id)
        else:
            field_type = self.__type_ctx.u64_id
        return LLValue(field_type, ir_val)  # type: ignore

    def __build_fat(self, data: LLValue, lock_ptr: LLValue, key: LLValue, index: LLValue, size: LLValue, type_id: int) -> LLValue:
        """按 5 字段聚合 ⟨data, lock_ptr, key, index, size⟩ 构造胖指针值。"""
        val = self.undef(type_id)
        val = self.insert_value(val, data, IR.FAT_DATA)
        val = self.insert_value(val, lock_ptr, IR.FAT_LOCK_PTR)
        val = self.insert_value(val, key, IR.FAT_KEY)
        val = self.insert_value(val, index, IR.FAT_INDEX)
        val = self.insert_value(val, size, IR.FAT_SIZE)
        return val

    def __fat_data(self, ll_val: LLValue) -> LLValue:
        """取胖指针的 data 字段(裸 8B 地址);已是裸指针则直接返回。

        slice/str 构造边界:从 slice 提取的裸 8B 指针(未合成 fat)与 malloc/VarPtr
        合成的 fat 在此统一取数据地址。
        """
        if self.__is_fat(ll_val):
            return self.__extract_fat_field(ll_val, IR.FAT_DATA)
        return ll_val

    def __promote_fat(self, ll_val: LLValue) -> LLValue:
        """把裸 8B 指针值(如 Alloca 结果)提升为 5 字段胖指针 ⟨data, e_f, k_f, 0, 1⟩。

        值层统一:type_id 为 PointerType 的 LLVM 值须是 40B 聚合才能跨调用/返回/
        存储;Alloca 等产生裸指针的原语在此补全元数据(规则 3.5.1 帧锁)。
        """
        if self.__is_fat_type(ll_val.type_id) and not self.__is_fat(ll_val):
            lock_ir, key_ir = self.__lit_lock_pair()
            data = LLValue(self.__type_ctx.alloc_pointer(self.__type_ctx.u8_id),
                           self.__builder.bitcast(ll_val.ir_val, ir.PointerType(ir.IntType(8))))  # type: ignore
            lock = LLValue(self.__type_ctx.alloc_pointer(self.__type_ctx.u8_id), lock_ir)  # type: ignore
            key = LLValue(self.__type_ctx.u64_id, key_ir)  # type: ignore
            zero = LLValue(self.__type_ctx.u64_id, ir.Constant(ir.IntType(64), 0))  # type: ignore
            one = LLValue(self.__type_ctx.u64_id, ir.Constant(ir.IntType(64), 1))  # type: ignore
            return self.__build_fat(data, lock, key, zero, one, ll_val.type_id)
        return ll_val

    def __slice_len_value(self, slice_addr: ir.Value) -> LLValue:
        """从 slice 基址读 length 字段(偏移 1),供 16B 边界的 slice 字段指针标记。"""
        len_ptr = self.__builder.gep(slice_addr, [self.i32(0).ir_val, self.i32(1).ir_val], inbounds=True)  # type: ignore
        len_ir = self.__builder.load(len_ptr)  # type: ignore
        return LLValue(self.__type_ctx.u64_id, len_ir)  # type: ignore

    def __is_slice_field_marker(self, ll_val: LLValue) -> bool:
        """编译期判定:slice/str 字段指针标记(lock=null 常量 且 data 非 null)。

        fieldptr 对 16B slice/str 的 ptr 字段产生该标记;nullptr 全零编码的
        data 也为 null,以 data 是否为 null 常量区分。无副作用(不发射指令)。
        """
        if not self.__is_fat(ll_val):
            return False
        lock_ir = self.__insertvalue_field(ll_val.ir_val, IR.FAT_LOCK_PTR)
        data_ir = self.__insertvalue_field(ll_val.ir_val, IR.FAT_DATA)
        lock_is_null = isinstance(lock_ir, ir.Constant) and lock_ir.constant is None  # type: ignore
        data_is_null = isinstance(data_ir, ir.Constant) and data_ir.constant is None  # type: ignore
        return lock_is_null and not data_is_null

    @staticmethod
    def __insertvalue_field(ir_val: ir.Value, index: int) -> ir.Value | None:
        """沿 insertvalue 链取某字段的插入值;未知来源(load/phi)返回 None。"""
        if isinstance(ir_val, ir.instructions.InsertValue):  # type: ignore
            if ir_val.indices == [index]:  # type: ignore
                return ir_val.value  # type: ignore
            return LLBuilder.__insertvalue_field(ir_val.aggregate, index)  # type: ignore
        return None

    def __fat_addr(self, ll_val: LLValue, pointee_type_id: int) -> LLValue:
        """定义 17 地址折算:有效地址 = data + index·|T|,一次 GEP(检查与取数共用)。

        对 fat 值提取 data/index 字段,bitcast 到 T* 后按元素索引 GEP;对裸 8B
        指针直接使用(索引恒 0 语义)。O-1 无回摆由检查(well-formed)保障。
        """
        if self.__is_fat(ll_val):
            data = self.__extract_fat_field(ll_val, IR.FAT_DATA).ir_val
            index = self.__extract_fat_field(ll_val, IR.FAT_INDEX).ir_val
            pointee_ll = self.__ll_type_ctx.get_ll_type(pointee_type_id).ir_type
            typed = self.__builder.bitcast(data, pointee_ll.as_pointer())  # type: ignore
            addr = self.__builder.gep(typed, [index], inbounds=False)  # type: ignore
        else:
            addr = ll_val.ir_val
        return LLValue(self.__type_ctx.alloc_pointer(pointee_type_id), addr)  # type: ignore

    def __gen_key_value(self, is_heap: bool) -> LLValue:
        """k ← Gen()(定义 10):全局计数递增,堆键 or 最高位标志(MSB 1)/栈键原样。"""
        counter = self.__module.get_key_counter(is_heap)
        loaded = self.__builder.load(counter)  # type: ignore
        nxt = self.__builder.add(loaded, ir.Constant(ir.IntType(64), 1))  # type: ignore
        self.__builder.store(nxt, counter)  # type: ignore
        if is_heap:
            nxt = self.__builder.or_(nxt, ir.Constant(ir.IntType(64), 0x8000_0000_0000_0000))  # type: ignore
        return LLValue(self.__type_ctx.u64_id, nxt)  # type: ignore

    def __split_block_name(self, suffix: str, kind: str, seq: int) -> str:
        """分裂出的新块名:短 CFG 块标签作基名 + 单调序列保证唯一。

        基名用 ``__current_cfg_block``(CFG 块标签,position_at 维护,跨分裂
        不变)而非当前 LLVM 块名——后者随每次分裂拼接增长,直线序列(如 N 条
        连续 ``t[i] = v``)可把单个标签推过 1024 字符,parse_assembly 截断后
        各块共享同一前缀塌缩为一个块(缺陷 B)。超长回退短名作最后防线。
        """
        name = f"{self.__current_cfg_block}.{suffix}.{kind}.{seq}"
        if len(name) > 1000:
            name = f"b{seq}.{kind}"
        return name

    def __emit_check(self, cond: LLValue, suffix: str) -> None:
        """检查失败 → llvm.trap(SIGILL → Exit code -4);通过 → 继续于新 ok 块。

        检查是 CFG 块中间的语句,必须分裂基本块:原块以条件分支结束,ok 块
        承载后续语句与终止符,trap 块 call llvm.trap + unreachable。
        """
        seq = self.__check_seq
        self.__check_seq += 1
        ok_block = self.__func.new_block(self.__split_block_name(suffix, "ok", seq))
        trap_block = self.__func.new_block(self.__split_block_name(suffix, "trap", seq))
        self.__func.add_block(ok_block.name, ok_block)  # type: ignore
        self.__func.add_block(trap_block.name, trap_block)  # type: ignore
        self.__builder.cbranch(cond.ir_val, ok_block, trap_block)  # type: ignore
        trap_builder = ir.IRBuilder(trap_block)
        trap_builder.call(self.__module.get_trap_intrinsic(), [])  # type: ignore
        trap_builder.unreachable()  # type: ignore
        # 该 CFG 块的终止符改落在 ok 块——phi 的入边块标签须随之映射
        self.__continuations[self.__current_cfg_block] = ok_block.name
        self.__builder = ir.IRBuilder(ok_block)

    def __emit_guarded(self, guard: ir.Value, compute: object) -> ir.Value:
        """短路守卫:guard 真 → compute(由调用方提供闭包,在新块中),假 → false。

        用于 live 的 null 短路(定义 8:lock_ptr = 0 时短路为假,不读地址 0
        物理槽位,避免段错误退化)。
        """
        seq = self.__check_seq
        self.__check_seq += 1
        then_block = self.__func.new_block(self.__split_block_name("g", "then", seq))
        else_block = self.__func.new_block(self.__split_block_name("g", "else", seq))
        merge_block = self.__func.new_block(self.__split_block_name("g", "merge", seq))
        self.__builder.cbranch(guard, then_block, else_block)  # type: ignore
        then_builder = ir.IRBuilder(then_block)
        then_val = compute(then_builder)  # type: ignore[operator]
        then_builder.branch(merge_block)  # type: ignore
        else_builder = ir.IRBuilder(else_block)
        else_builder.branch(merge_block)  # type: ignore
        merge_builder = ir.IRBuilder(merge_block)
        phi = merge_builder.phi(ir.IntType(1))  # type: ignore
        phi.add_incoming(then_val, then_block)  # type: ignore
        phi.add_incoming(ir.Constant(ir.IntType(1), 0), else_block)  # type: ignore
        self.__builder = merge_builder
        return phi

    def __check_live(self, ll_val: LLValue) -> LLValue:
        """定义 8 live(p):锁槽键比较 μ⟨lock_ptr⟩ == key,含 null 短路。"""
        lock_ptr = self.__extract_fat_field(ll_val, IR.FAT_LOCK_PTR).ir_val
        key = self.__extract_fat_field(ll_val, IR.FAT_KEY).ir_val
        guard = self.__builder.icmp_signed("!=", lock_ptr, ir.Constant(lock_ptr.type, None))  # type: ignore

        def compute(builder: ir.IRBuilder) -> ir.Value:
            slot = builder.bitcast(lock_ptr, ir.PointerType(ir.IntType(64)))  # type: ignore
            slot_val = builder.load(slot)  # type: ignore
            return builder.icmp_signed("==", slot_val, key)  # type: ignore

        return LLValue(self.__type_ctx.bool_id, self.__emit_guarded(guard, compute))  # type: ignore

    def __check_in_bounds_cond(self, ll_val: LLValue) -> LLValue:
        """定义 12 in_bounds(p,1):0 ≤ index ∧ index+1 ≤ size,简化为 index < size(u64)。"""
        index = self.__extract_fat_field(ll_val, IR.FAT_INDEX).ir_val
        size = self.__extract_fat_field(ll_val, IR.FAT_SIZE).ir_val
        cond = self.__builder.icmp_unsigned("<", index, size)  # type: ignore
        return LLValue(self.__type_ctx.bool_id, cond)  # type: ignore

    def __check_live_and(self, ll_val: LLValue, other: ir.Value) -> ir.Value:
        """live(p) ∧ other(i1 值)。"""
        live_val = self.__check_live(ll_val)
        return self.__builder.and_(live_val.ir_val, other)  # type: ignore

    def string_literal(self, value: str, type_id: int) -> LLValue:
        """Create a ``{i8*, i64}`` struct value for a string literal."""
        encoded = value.encode("utf-8")
        global_var = self.__module.get_string_global(encoded)
        ptr = global_var.gep([ir.Constant(ir.IntType(32), 0), ir.Constant(ir.IntType(32), 0)])  # type: ignore
        length = ir.Constant(ir.IntType(64), len(encoded))  # type: ignore
        return LLValue(type_id, ir.Constant.literal_struct([ptr, length]))  # type: ignore

    # ------------------------------------------------------------------
    # builder position
    # ------------------------------------------------------------------

    def position_at(self, label: str, where: BuilderPosition = BuilderPosition.End) -> None:
        block = self.__func.block(label)
        self.__builder = ir.IRBuilder(block)
        self.__current_cfg_block = label
        if where == BuilderPosition.Phi:
            self.__builder.position_at_start(block)  # type: ignore
        elif where == BuilderPosition.First:
            instructions = list(block.instructions)  # type: ignore
            if instructions:
                self.__builder.position_before(instructions[0])  # type: ignore

    @property
    def current_block_label(self) -> str:
        """Return the label of the block the builder is currently positioned in."""
        return self.__builder.block.name  # type: ignore

    # ------------------------------------------------------------------
    # statements
    # ------------------------------------------------------------------

    def var_ptr(self, symbol_id: int, result: str, frame_lock_ptr: LLValue | None = None, frame_key: LLValue | None = None) -> LLValue:
        alloca_ptr = self.__func.get_var_ptr(symbol_id)
        if self.__is_fat_type(alloca_ptr.type_id):
            # 5 字段合成 ⟨a_x, e_f, k_f, 0, 1⟩(定义 15、规则 3.5.1)
            data = LLValue(self.__type_ctx.alloc_pointer(self.__type_ctx.u8_id),
                           self.__builder.bitcast(alloca_ptr.ir_val, ir.PointerType(ir.IntType(8))))  # type: ignore
            if frame_lock_ptr is None or frame_key is None:
                e_f_ir, k_f_ir = self.__lit_lock_pair()
            else:
                e_f_ir = frame_lock_ptr.ir_val
                k_f_ir = frame_key.ir_val
            lock = LLValue(self.__type_ctx.alloc_pointer(self.__type_ctx.u8_id),
                           self.__builder.bitcast(e_f_ir, ir.PointerType(ir.IntType(8))))  # type: ignore
            key = LLValue(self.__type_ctx.u64_id, k_f_ir)  # type: ignore
            result_val = self.__build_fat(
                data, lock, key,
                LLValue(self.__type_ctx.u64_id, ir.Constant(ir.IntType(64), 0)),  # type: ignore
                LLValue(self.__type_ctx.u64_id, ir.Constant(ir.IntType(64), 1)),  # type: ignore
                alloca_ptr.type_id,
            )
        else:
            result_val = alloca_ptr
        self.__func.set_reg(result, result_val)
        return result_val

    def alloca(self, type_id: int) -> LLValue:
        ptr_type_id = self.__type_ctx.alloc_pointer(type_id)
        return LLValue(ptr_type_id, self.__builder.alloca(self.__ll_type_ctx.get_ll_type(type_id).ir_type))  # type: ignore

    def alloca_store(self, value: LLValue, result: str) -> None:
        alloca_val = self.alloca(value.type_id)
        self.store(value, alloca_val)
        self.__func.set_reg(result, alloca_val)

    def malloc(self, type_id: int, size: LLValue, key: LLValue | None, result: str) -> LLValue:
        if self.__type_ctx.is_zst(type_id):
            ptr_type_id = self.__type_ctx.alloc_pointer(type_id)
            self.__func.set_reg(result, LLValue(ptr_type_id, ir.Constant(self.__ll_type_ctx.get_ll_type(ptr_type_id).ir_type, ir.Undefined)))  # type: ignore
            return LLValue(ptr_type_id, ir.Constant(self.__ll_type_ctx.get_ll_type(ptr_type_id).ir_type, ir.Undefined))  # type: ignore
        # Convert element count to byte count for C's malloc
        elem_size = self.__ll_type_ctx.get_type_size(type_id)
        if elem_size == 1:
            byte_size = size
        else:
            byte_size_ir = self.__builder.mul(size.ir_val, ir.Constant(ir.IntType(64), elem_size))  # type: ignore
            byte_size = LLValue(self.__type_ctx.u64_id, byte_size_ir)  # type: ignore
        # 规则 3.6.1:块 = 锁头(H=8)+ 负载;块头锁槽写键(锁槽 = 块首首字)
        total_ir = self.__builder.add(byte_size.ir_val, ir.Constant(ir.IntType(64), 8))  # type: ignore
        total = LLValue(self.__type_ctx.u64_id, total_ir)  # type: ignore
        raw = self.__call_intrinsic(IntrinsicKind.Malloc, [total])
        ptr_type_id = self.__type_ctx.alloc_pointer(type_id)
        block_base = LLValue(ptr_type_id, raw.ir_val)  # type: ignore
        if key is not None:
            slot_ptr = self.__builder.bitcast(block_base.ir_val, ir.PointerType(ir.IntType(64)))  # type: ignore
            self.__builder.store(key.ir_val, slot_ptr)  # type: ignore
            key_ir = key.ir_val
        else:
            key_ir = ir.Constant(ir.IntType(64), 0)  # type: ignore
        data_ir = self.__builder.gep(block_base.ir_val, [ir.Constant(ir.IntType(64), 8)], inbounds=False)  # type: ignore
        data = LLValue(self.__type_ctx.alloc_pointer(self.__type_ctx.u8_id), data_ir)  # type: ignore
        block_ptr = LLValue(self.__type_ctx.alloc_pointer(self.__type_ctx.u8_id), block_base.ir_val)  # type: ignore
        key_val = LLValue(self.__type_ctx.u64_id, key_ir)  # type: ignore
        zero = LLValue(self.__type_ctx.u64_id, ir.Constant(ir.IntType(64), 0))  # type: ignore
        size_val = LLValue(self.__type_ctx.u64_id, size.ir_val)  # type: ignore
        result_val = self.__build_fat(data, block_ptr, key_val, zero, size_val, ptr_type_id)
        self.__func.set_reg(result, result_val)
        return result_val

    def delete(self, ptr: LLValue) -> None:
        if self.__type_ctx.is_zst(ptr.type_id):
            return  # freeing a ZST pointer is a no-op
        # 规则 3.6.2 动作②:整块交还 —— 释放范围以 lock_ptr(块首)寻址
        if self.__is_fat(ptr):
            block_base = self.__extract_fat_field(ptr, IR.FAT_LOCK_PTR)
            self.__call_intrinsic(IntrinsicKind.Free, [block_base])
            return
        i8_ptr_type_id = self.__type_ctx.alloc_pointer(self.__type_ctx.u8_id)
        casted = self.__builder.bitcast(ptr.ir_val, ir.PointerType(ir.IntType(8)))  # type: ignore
        self.__call_intrinsic(IntrinsicKind.Free, [LLValue(i8_ptr_type_id, casted)])  # type: ignore

    # -- fat-pointer mechanism nodes (t8) --

    def gen_key(self, is_heap: bool, result: str) -> None:
        self.__func.set_reg(result, self.__gen_key_value(is_heap))

    def write_lock_slot(self, lock_ptr: LLValue, value: LLValue) -> None:
        """μ⟨lock_ptr⟩ := value(规则 3.6.2 动作① SENTINEL / 3.7.1 帧锁写键)。

        null 锁槽跳过:null delete 无块头可写,与 free(NULL) 语义一致。运行期
        值无法编译期判定,以分支守卫(代价仅帧进入/删除路径)。
        """
        raw = self.__fat_data(lock_ptr).ir_val
        if isinstance(raw, ir.Constant) and raw.constant is None:  # type: ignore
            return
        seq = self.__check_seq
        self.__check_seq += 1
        do_store = self.__func.new_block(self.__split_block_name("wls", "store", seq))
        skip_block = self.__func.new_block(self.__split_block_name("wls", "skip", seq))
        self.__func.add_block(do_store.name, do_store)  # type: ignore
        self.__func.add_block(skip_block.name, skip_block)  # type: ignore
        is_null = self.__builder.icmp_signed("==", raw, ir.Constant(raw.type, None))  # type: ignore
        self.__builder.cbranch(is_null, skip_block, do_store)  # type: ignore
        store_builder = ir.IRBuilder(do_store)
        slot_ptr = store_builder.bitcast(raw, ir.PointerType(ir.IntType(64)))  # type: ignore
        store_builder.store(value.ir_val, slot_ptr)  # type: ignore
        store_builder.branch(skip_block)  # type: ignore
        self.__continuations[self.__current_cfg_block] = skip_block.name
        self.__builder = ir.IRBuilder(skip_block)

    def nullptr_literal(self, type_id: int) -> LLValue:
        """nullptr 编码 ⟨0, 0, 0, 0, 0⟩(§7.1 NullptrLiteral):全零 5 字段结构。"""
        ll_type = self.__ll_type_ctx.get_ll_type(type_id).ir_type
        if self.__ll_type_ctx.is_zst(type_id):
            return self.undef(type_id)
        if isinstance(ll_type, ir.LiteralStructType):
            null_ptr = ir.Constant(ir.PointerType(ir.IntType(8)), None)  # type: ignore
            zero = ir.Constant(ir.IntType(64), 0)  # type: ignore
            ir_val = ir.Constant.literal_struct([null_ptr, null_ptr, zero, zero, zero])  # type: ignore
        else:
            ir_val = ir.Constant(ll_type, None)  # type: ignore
        return LLValue(type_id, ir_val)  # type: ignore

    def check_safe_access(self, ptr: LLValue) -> None:
        """safe_access(p,1) = live(p) ∧ in_bounds(p,1)(规则 3.2.1-3.2.2)。"""
        if not self.__is_fat(ptr) or self.__is_slice_field_marker(ptr):
            return
        in_bounds = self.__check_in_bounds_cond(ptr)
        cond = self.__check_live_and(ptr, in_bounds.ir_val)
        self.__emit_check(LLValue(self.__type_ctx.bool_id, cond), "safe")

    def check_in_bounds(self, ptr: LLValue) -> None:
        """in_bounds(p_s,1)(规则 3.5.2 重锚定前提)。"""
        if not self.__is_fat(ptr) or self.__is_slice_field_marker(ptr):
            return
        self.__emit_check(self.__check_in_bounds_cond(ptr), "ib")

    def check_element_arith(self, base: LLValue, offset: LLValue) -> None:
        """定义 13 良构检查:0 ≤ index+offset ≤ size;无回摆以 i128 计算(O-1)。"""
        if not self.__is_fat(base) or self.__is_slice_field_marker(base):
            return
        index = self.__extract_fat_field(base, IR.FAT_INDEX).ir_val
        size = self.__extract_fat_field(base, IR.FAT_SIZE).ir_val
        i128: ir.IntType = ir.IntType(128)  # type: ignore
        idx128 = self.__builder.zext(index, i128)  # type: ignore
        off64 = self.__builder.bitcast(offset.ir_val, ir.IntType(64))  # type: ignore
        off128 = self.__builder.sext(off64, i128)  # type: ignore
        size128 = self.__builder.zext(size, i128)  # type: ignore
        sum128 = self.__builder.add(idx128, off128)  # type: ignore
        ge0 = self.__builder.icmp_signed(">=", sum128, ir.Constant(i128, 0))  # type: ignore
        le_size = self.__builder.icmp_signed("<=", sum128, size128)  # type: ignore
        cond: ir.Value = self.__builder.and_(ge0, le_size)  # type: ignore
        self.__emit_check(LLValue(self.__type_ctx.bool_id, cond), "elarith")

    def check_ptrdiff(self, lhs: LLValue, rhs: LLValue) -> None:
        """规则 3.3.3 前提:data 相等 + 良构(双方 index ≤ size)+ 差可表示。"""
        if not (self.__is_fat(lhs) and self.__is_fat(rhs)):
            return
        data_l = self.__extract_fat_field(lhs, IR.FAT_DATA).ir_val
        data_r = self.__extract_fat_field(rhs, IR.FAT_DATA).ir_val
        data_eq = self.__builder.icmp_signed("==", data_l, data_r)  # type: ignore
        idx_l = self.__extract_fat_field(lhs, IR.FAT_INDEX).ir_val
        idx_r = self.__extract_fat_field(rhs, IR.FAT_INDEX).ir_val
        size_l = self.__extract_fat_field(lhs, IR.FAT_SIZE).ir_val
        size_r = self.__extract_fat_field(rhs, IR.FAT_SIZE).ir_val
        wf_l = self.__builder.icmp_unsigned("<=", idx_l, size_l)  # type: ignore
        wf_r = self.__builder.icmp_unsigned("<=", idx_r, size_r)  # type: ignore
        i128: ir.IntType = ir.IntType(128)  # type: ignore
        diff128 = self.__builder.sub(self.__builder.zext(idx_l, i128), self.__builder.zext(idx_r, i128))  # type: ignore
        diff64 = self.__builder.trunc(diff128, ir.IntType(64))  # type: ignore
        no_wrap = self.__builder.icmp_signed("==", self.__builder.sext(diff64, i128), diff128)  # type: ignore
        wf_cond = self.__builder.and_(wf_l, wf_r)  # type: ignore
        cond: ir.Value = self.__builder.and_(data_eq, self.__builder.and_(wf_cond, no_wrap))  # type: ignore
        self.__emit_check(LLValue(self.__type_ctx.bool_id, cond), "ptrdiff")

    def check_ptr_cmp(self, lhs: LLValue, rhs: LLValue) -> None:
        """规则 3.4.1 序比较前提:data 相等(跨对象序比较 trap,§7.6 风险 3)。"""
        if not (self.__is_fat(lhs) and self.__is_fat(rhs)):
            return
        data_l, _ = self.__cmp_fat_operands(lhs)
        data_r, _ = self.__cmp_fat_operands(rhs)
        cond = self.__builder.icmp_signed("==", data_l, data_r)  # type: ignore
        self.__emit_check(LLValue(self.__type_ctx.bool_id, cond), "ptrcmp")

    def check_delete(self, ptr: LLValue) -> None:
        """规则 3.6.2 四前提:is_heap(p) ∧ live(p) ∧ is_raw(p)。

        null 编码(⟨0,0,0,0,0⟩)放行——stdlib 空容器 Drop(RawVec<..>.new 后
        `del self.data`)依赖 free(NULL) 语义,与 C 一致;write_lock_slot 对
        null 锁槽同样跳过。
        """
        if not self.__is_fat(ptr) or self.__is_slice_field_marker(ptr):
            return
        data = self.__extract_fat_field(ptr, IR.FAT_DATA).ir_val
        is_null = self.__builder.icmp_signed("==", data, ir.Constant(data.type, None))  # type: ignore
        key = self.__extract_fat_field(ptr, IR.FAT_KEY).ir_val
        flag = self.__builder.and_(key, ir.Constant(ir.IntType(64), 0x8000_0000_0000_0000))  # type: ignore
        heap_ok = self.__builder.icmp_signed("!=", flag, ir.Constant(ir.IntType(64), 0))  # type: ignore
        lock = self.__extract_fat_field(ptr, IR.FAT_LOCK_PTR).ir_val
        index = self.__extract_fat_field(ptr, IR.FAT_INDEX).ir_val
        lock_int = self.__builder.ptrtoint(lock, ir.IntType(64))  # type: ignore
        data_int = self.__builder.ptrtoint(data, ir.IntType(64))  # type: ignore
        expected = self.__builder.add(lock_int, ir.Constant(ir.IntType(64), 8))  # type: ignore
        raw_data_ok = self.__builder.icmp_signed("==", data_int, expected)  # type: ignore
        raw_index_ok = self.__builder.icmp_signed("==", index, ir.Constant(ir.IntType(64), 0))  # type: ignore
        live_ok = self.__check_live(ptr)
        raw_cond = self.__builder.and_(raw_data_ok, raw_index_ok)  # type: ignore
        full = self.__builder.and_(heap_ok, self.__builder.and_(live_ok.ir_val, raw_cond))  # type: ignore
        cond: ir.Value = self.__builder.or_(is_null, full)  # type: ignore
        self.__emit_check(LLValue(self.__type_ctx.bool_id, cond), "del")

    # -- memory --

    def load(self, ptr: LLValue, result: str) -> LLValue:
        ptr_type = self.__type_ctx[ptr.type_id]
        assert isinstance(ptr_type, Type.PointerType)
        if self.__ll_type_ctx.is_zst(ptr_type.pointee_type):
            # Loading a zero-sized value yields nothing: emit no `load` and
            # bind no register (the result is never consumed).
            return self.undef(ptr_type.pointee_type)
        if self.__is_fat(ptr):
            if self.__is_slice_field_marker(ptr):
                # 16B slice/str 的 ptr 字段:读裸 8B 并合成 fat ⟨raw, &lit_lock, 1, 0, len⟩
                eff_addr = self.__fat_addr(ptr, ptr_type.pointee_type).ir_val
                raw_addr = self.__builder.bitcast(eff_addr, ir.PointerType(ir.PointerType(ir.IntType(8))))  # type: ignore
                raw = self.__builder.load(raw_addr)  # type: ignore
                size = self.__extract_fat_field(ptr, IR.FAT_SIZE)
                lock_ir, key_ir = self.__lit_lock_pair()
                data = LLValue(self.__type_ctx.alloc_pointer(self.__type_ctx.u8_id), raw)  # type: ignore
                lock = LLValue(self.__type_ctx.alloc_pointer(self.__type_ctx.u8_id), lock_ir)  # type: ignore
                key = LLValue(self.__type_ctx.u64_id, key_ir)  # type: ignore
                zero = LLValue(self.__type_ctx.u64_id, ir.Constant(ir.IntType(64), 0))  # type: ignore
                result_val = self.__build_fat(data, lock, key, zero, size, ptr_type.pointee_type)
            else:
                addr = self.__fat_addr(ptr, ptr_type.pointee_type)
                ir_val = self.__builder.load(addr.ir_val)  # type: ignore
                result_val = LLValue(ptr_type.pointee_type, ir_val)
        else:
            ir_val = self.__builder.load(ptr.ir_val)  # type: ignore
            result_val = LLValue(ptr_type.pointee_type, ir_val)
        self.__func.set_reg(result, result_val)
        return result_val

    def store(self, value: LLValue, ptr: LLValue) -> None:
        if self.__ll_type_ctx.is_zst(value.type_id):
            return  # storing a zero-sized value is a no-op
        if self.__is_fat(ptr):
            ptr_type = self.__type_ctx[ptr.type_id]
            assert isinstance(ptr_type, Type.PointerType)
            addr = self.__fat_addr(ptr, ptr_type.pointee_type)
            self.__builder.store(value.ir_val, addr.ir_val)  # type: ignore
        else:
            self.__builder.store(value.ir_val, ptr.ir_val)  # type: ignore

    def gep(self, base: LLValue, indices: list[int], result: str) -> LLValue:
        base_type = self.__type_ctx[base.type_id]
        if isinstance(base_type, Type.PointerType):
            pointee_type_id = base_type.pointee_type
        else:
            pointee_type_id = base.type_id

        # If we are already pointing at ZST, every offset is meaningless —
        # return undef before touching the (empty-struct) LLVM value.
        if self.__ll_type_ctx.is_zst(pointee_type_id):
            return self.undef(self.__type_ctx.alloc_pointer(pointee_type_id))

        # Walk sub-indices to find the final pointee.
        for idx in indices[1:]:
            ty = self.__type_ctx[pointee_type_id]
            if isinstance(ty, Type.StructType):
                fields = self.__type_ctx.get_struct_fields(ty.type_id)
                pointee_type_id = fields[idx].type_id
            elif isinstance(ty, Type.TupleType):
                pointee_type_id = ty.element_types[idx]
            elif isinstance(ty, Type.ArrayType):
                pointee_type_id = ty.element_type
            elif isinstance(ty, Type.SliceType):
                pointee_type_id = self.__type_ctx.alloc_pointer(ty.element_type) if idx == 0 else self.__type_ctx.u64_id
            elif isinstance(ty, Type.StrType):
                pointee_type_id = self.__type_ctx.alloc_pointer(self.__type_ctx.u8_id) if idx == 0 else self.__type_ctx.u64_id

        result_type_id = self.__type_ctx.alloc_pointer(pointee_type_id)
        # A sub-index may have led into a ZST field — skip GEP.
        if self.__ll_type_ctx.is_zst(pointee_type_id):
            return self.undef(result_type_id)
        if self.__is_fat(base):
            # 规则 3.5.2 重锚定:data' = addr_T(p_s,0) + δ,index'=0,size'=1,锁字段继承
            base_def = self.__type_ctx[base.type_id]
            addr = self.__fat_addr(base, base_def.pointee_type)  # type: ignore
            idx_vals = [self.i32(i).ir_val for i in indices]
            field_addr = self.__builder.gep(addr.ir_val, idx_vals, inbounds=True)  # type: ignore
            field_ptr = LLValue(self.__type_ctx.alloc_pointer(self.__type_ctx.u8_id),
                                self.__builder.bitcast(field_addr, ir.PointerType(ir.IntType(8))))  # type: ignore
            lock = self.__extract_fat_field(base, IR.FAT_LOCK_PTR)
            key = self.__extract_fat_field(base, IR.FAT_KEY)
            zero = LLValue(self.__type_ctx.u64_id, ir.Constant(ir.IntType(64), 0))  # type: ignore
            one = LLValue(self.__type_ctx.u64_id, ir.Constant(ir.IntType(64), 1))  # type: ignore
            if isinstance(base_def, Type.PointerType) and isinstance(self.__type_ctx[base_def.pointee_type], (Type.SliceType, Type.StrType)) and indices == [0, 0]:
                # 16B slice/str 边界:ptr 字段是裸 8B 指针。fieldptr 结果标记为
                # 「slice 字段指针」(lock=null,data=字段地址,size=切片长度),
                # load 依标记读 8B 并合成 fat;checks 对标记跳过。
                null_lock = LLValue(self.__type_ctx.alloc_pointer(self.__type_ctx.u8_id),
                                    ir.Constant(ir.PointerType(ir.IntType(8)), None))  # type: ignore
                slice_len = self.__slice_len_value(addr.ir_val)
                result_val = self.__build_fat(field_ptr, null_lock, zero, zero, slice_len, result_type_id)
            else:
                result_val = self.__build_fat(field_ptr, lock, key, zero, one, result_type_id)
        else:
            idx_vals = [self.i32(i).ir_val for i in indices]
            ir_val = self.__builder.gep(base.ir_val, idx_vals, inbounds=True)  # type: ignore
            result_val = LLValue(result_type_id, ir_val)
        self.__func.set_reg(result, result_val)
        return result_val

    # -- arithmetic / comparison --

    def binary(self, op: BinaryOperator, lhs: LLValue, rhs: LLValue, result: str) -> LLValue:
        if op.is_comparison():
            ir_val = self.__cmp_impl(op, lhs.ir_val, rhs.ir_val, lhs.type_id)
            result_val = LLValue(self.__type_ctx.bool_id, ir_val)
        else:
            ir_val = self.__arith_impl(op, lhs.ir_val, rhs.ir_val, lhs.type_id)
            result_val = LLValue(lhs.type_id, ir_val)
        self.__func.set_reg(result, result_val)
        return result_val

    def element_ptr(self, base: LLValue, offset: LLValue, result: str) -> LLValue:
        """Pointer arithmetic: ptr + offset → gep ptr, offset"""
        ptr_ty = self.__type_ctx[base.type_id]
        assert isinstance(ptr_ty, Type.PointerType)
        if self.__ll_type_ctx.is_zst(ptr_ty.pointee_type):
            return self.undef(base.type_id)
        if self.__is_fat(base):
            # 规则 3.3.1-3.3.2:算术仅更新 index' = index + n(检查已保证良构)
            data = self.__extract_fat_field(base, IR.FAT_DATA)
            lock = self.__extract_fat_field(base, IR.FAT_LOCK_PTR)
            key = self.__extract_fat_field(base, IR.FAT_KEY)
            index = self.__extract_fat_field(base, IR.FAT_INDEX)
            size = self.__extract_fat_field(base, IR.FAT_SIZE)
            new_index = LLValue(self.__type_ctx.u64_id,
                                self.__builder.add(index.ir_val, offset.ir_val))  # type: ignore
            result_val = self.__build_fat(data, lock, key, new_index, size, base.type_id)
        else:
            ir_val = self.__builder.gep(base.ir_val, [offset.ir_val], inbounds=False)  # type: ignore
            result_val = LLValue(base.type_id, ir_val)
        self.__func.set_reg(result, result_val)
        return result_val

    def ptr_diff(self, lhs: LLValue, rhs: LLValue, result: str) -> LLValue:
        """Pointer difference: ptr - ptr → i64 offset in elements(定型规则 8.1.9)。

        fat:有效地址 = data + index·|T| 以 i128 宽整数计算(O-1 无回摆),截断后
        sdiv |T| 得元素差(同对象内整除,检查已保证 data 相等 + 良构)。
        """
        lhs_ty = self.__type_ctx[lhs.type_id]
        assert isinstance(lhs_ty, Type.PointerType)
        elem_size = self.__ll_type_ctx.get_type_size(lhs_ty.pointee_type)
        if self.__is_fat(lhs):
            addr_l = self.__fat_addr_i128(lhs, lhs_ty.pointee_type)
            addr_r = self.__fat_addr_i128(rhs, lhs_ty.pointee_type)
            diff128 = self.__builder.sub(addr_l, addr_r)  # type: ignore
            diff64 = self.__builder.trunc(diff128, ir.IntType(64))  # type: ignore
            elem_size_val = self.__builder.sdiv(  # type: ignore
                diff64,
                ir.Constant(ir.IntType(64), elem_size)  # type: ignore
            )
            result_val = LLValue(self.__type_ctx.i64_id, elem_size_val)  # type: ignore
        else:
            lhs_int = self.__builder.ptrtoint(lhs.ir_val, ir.IntType(64))  # type: ignore
            rhs_int = self.__builder.ptrtoint(rhs.ir_val, ir.IntType(64))  # type: ignore
            byte_diff = self.__builder.sub(lhs_int, rhs_int)  # type: ignore
            elem_size_val = self.__builder.sdiv(  # type: ignore
                byte_diff,
                ir.Constant(ir.IntType(64), elem_size)  # type: ignore
            )
            result_val = LLValue(self.__type_ctx.i64_id, elem_size_val)  # type: ignore
        self.__func.set_reg(result, result_val)
        return result_val

    def __fat_addr_i128(self, ll_val: LLValue, pointee_type_id: int) -> ir.Value:
        """有效地址(data + index·|T|)以 i128 计算——宽整数天然无回摆(O-1)。"""
        data = self.__extract_fat_field(ll_val, IR.FAT_DATA).ir_val
        index = self.__extract_fat_field(ll_val, IR.FAT_INDEX).ir_val
        base128 = self.__builder.zext(self.__builder.ptrtoint(data, ir.IntType(64)), ir.IntType(128))  # type: ignore
        idx128 = self.__builder.zext(index, ir.IntType(128))  # type: ignore
        scaled = self.__builder.mul(idx128, ir.Constant(ir.IntType(128), self.__ll_type_ctx.get_type_size(pointee_type_id)))  # type: ignore
        return self.__builder.add(base128, scaled)  # type: ignore

    def __cmp_fat_operands(self, ll_val: LLValue) -> tuple[ir.Value, ir.Value]:
        """比较操作数的 (data, index) 字段对:fat 聚合提取字段;裸 8B 指针按
        (自身, 0)(索引恒 0 语义)。返回 i8* 与 u64 两个 LLVM 值。"""
        if self.__is_fat(ll_val):
            return (self.__extract_fat_field(ll_val, IR.FAT_DATA).ir_val,
                    self.__extract_fat_field(ll_val, IR.FAT_INDEX).ir_val)
        ty = self.__type_ctx[ll_val.type_id]
        if isinstance(ty, Type.PointerType):
            data = self.__builder.bitcast(ll_val.ir_val, ir.PointerType(ir.IntType(8)))  # type: ignore
            return (data, ir.Constant(ir.IntType(64), 0))  # type: ignore
        raise ValueError(f"Unsupported pointer comparison operand: {type(ty).__name__}")

    def ptr_cmp(self, op: BinaryOperator, lhs: LLValue, rhs: LLValue, result: str) -> LLValue:
        """指针比较(规则 3.4.1-3.4.2,§7.6 风险 3):相等按 (data, index) 二元组;
        序比较在 CheckPtrCmp 前提(规则 3.4.1)下按 index。LLVM 无聚合 icmp → 字段提取。"""
        lhs_ty = self.__type_ctx[lhs.type_id]
        assert isinstance(lhs_ty, Type.PointerType)
        if self.__ll_type_ctx.is_zst(lhs_ty.pointee_type):
            # 指针-to-ZST:全部值不可区分,Eq/Leq/Geq 恒真、其余恒假
            eq = op in (BinaryOperator.Eq, BinaryOperator.Leq, BinaryOperator.Geq)
            result_val = LLValue(self.__type_ctx.bool_id, ir.Constant(ir.IntType(1), 1 if eq else 0))  # type: ignore
            self.__func.set_reg(result, result_val)
            return result_val
        data_l, idx_l = self.__cmp_fat_operands(lhs)
        data_r, idx_r = self.__cmp_fat_operands(rhs)
        if op in (BinaryOperator.Eq, BinaryOperator.Neq):
            data_eq = self.__builder.icmp_signed("==", data_l, data_r)  # type: ignore
            idx_eq = self.__builder.icmp_unsigned("==", idx_l, idx_r)  # type: ignore
            both = self.__builder.and_(data_eq, idx_eq)  # type: ignore
            ir_val = self.__builder.not_(both) if op == BinaryOperator.Neq else both  # type: ignore
        else:
            predicate = {
                BinaryOperator.Lt: "<", BinaryOperator.Gt: ">",
                BinaryOperator.Leq: "<=", BinaryOperator.Geq: ">=",
            }[op]
            ir_val = self.__builder.icmp_unsigned(predicate, idx_l, idx_r)  # type: ignore
        result_val = LLValue(self.__type_ctx.bool_id, ir_val)  # type: ignore
        self.__func.set_reg(result, result_val)
        return result_val

    def unary(self, op: UnaryOperator, operand: LLValue, result: str) -> LLValue:
        if op == UnaryOperator.Neg:
            if isinstance(operand.ir_val.type, ir.types._BaseFloatType):  # type: ignore
                # llvmlite's neg() emits `sub` which is integer-only; use fsub for floats.
                ir_val = self.__builder.fsub(ir.Constant(operand.ir_val.type, 0.0), operand.ir_val)  # type: ignore
            else:
                ir_val = self.__builder.neg(operand.ir_val)  # type: ignore
        elif op == UnaryOperator.LogicalNot:
            ir_val = self.__builder.not_(operand.ir_val)  # type: ignore
        elif op == UnaryOperator.BitNot:
            ir_val = self.__builder.xor(operand.ir_val, ir.Constant(operand.ir_val.type, -1))  # type: ignore
        else:
            raise ValueError(f"Unsupported unary: {op}")
        result_val = LLValue(operand.type_id, ir_val)  # type: ignore
        self.__func.set_reg(result, result_val)
        return result_val

    # -- cast --

    def cast(self, value: LLValue, to_type: int, result: str) -> LLValue:
        src = self.__type_ctx[value.type_id]
        dst = self.__type_ctx[to_type]
        dest_ll_type = self.__ll_type_ctx.get_ll_type(to_type).ir_type

        if self.__ll_type_ctx.is_zst(value.type_id) or self.__ll_type_ctx.is_zst(to_type):
            # ZST 值已被擦除({} / undef),涉及 ZST 的转换一律 undef(指针-to-ZST 例外)
            ir_val = ir.Constant(dest_ll_type, ir.Undefined)  # type: ignore
        elif isinstance(src, (Type.IntType, Type.CharType, Type.BoolType)) and isinstance(
            dst, (Type.IntType, Type.CharType, Type.BoolType)
        ):
            src_width = self.__ll_type_ctx.get_ll_type(value.type_id).ir_type.width  # type: ignore
            dest_width = dest_ll_type.width  # type: ignore
            if dest_width > src_width:
                if isinstance(src, Type.CharType) or (isinstance(src, Type.IntType) and not src.signed):
                    ir_val = self.__builder.zext(value.ir_val, dest_ll_type)  # type: ignore
                else:
                    ir_val = self.__builder.sext(value.ir_val, dest_ll_type)  # type: ignore
            elif dest_width < src_width:
                ir_val = self.__builder.trunc(value.ir_val, dest_ll_type)  # type: ignore
            else:
                ir_val = value.ir_val
        elif isinstance(src, Type.IntType) and isinstance(dst, Type.FloatType):
            ir_val = self.__builder.sitofp(value.ir_val, dest_ll_type) if src.signed else self.__builder.uitofp(value.ir_val, dest_ll_type)  # type: ignore
        elif isinstance(src, Type.FloatType) and isinstance(dst, Type.IntType):
            if dst.signed:
                ir_val = self.__builder.fptosi(value.ir_val, dest_ll_type)  # type: ignore
            else:
                # Negative float → unsigned int should yield 0.
                zero_f = ir.Constant(value.ir_val.type, 0.0)  # type: ignore
                pos = self.__builder.fcmp_ordered(">=", value.ir_val, zero_f)  # type: ignore
                raw = self.__builder.fptoui(value.ir_val, dest_ll_type)  # type: ignore
                zero_i = ir.Constant(dest_ll_type, 0)  # type: ignore
                ir_val = self.__builder.select(pos, raw, zero_i)  # type: ignore
        elif isinstance(src, Type.FloatType) and isinstance(dst, Type.FloatType):
            ir_val = self.__builder.fpext(value.ir_val, dest_ll_type) if src.size < dst.size else self.__builder.fptrunc(value.ir_val, dest_ll_type)  # type: ignore
        elif isinstance(src, (Type.PointerType, Type.NullPtrType)) and isinstance(dst, Type.PointerType):
            if self.__ll_type_ctx.is_zst(dst.pointee_type):
                # both src and dst are ptr-to-ZST — no real cast, just undef
                ir_val = ir.Constant(dest_ll_type, ir.Undefined)  # type: ignore
            elif self.__is_fat(value):
                # 指针→指针:5 字段结构重贴;数组退化 T[m]*→T* 时重锚定 + size=m
                if isinstance(src, Type.PointerType) and isinstance(self.__type_ctx[src.pointee_type], Type.ArrayType):
                    arr_ty = self.__type_ctx[src.pointee_type]
                    assert isinstance(arr_ty, Type.ArrayType)
                    if arr_ty.element_type == dst.pointee_type:
                        eff = self.__fat_addr(value, src.pointee_type)
                        data = LLValue(self.__type_ctx.alloc_pointer(self.__type_ctx.u8_id),
                                       self.__builder.bitcast(eff.ir_val, ir.PointerType(ir.IntType(8))))  # type: ignore
                        lock = self.__extract_fat_field(value, IR.FAT_LOCK_PTR)
                        key = self.__extract_fat_field(value, IR.FAT_KEY)
                        zero = LLValue(self.__type_ctx.u64_id, ir.Constant(ir.IntType(64), 0))  # type: ignore
                        len_ty = self.__type_ctx[arr_ty.length]
                        assert isinstance(len_ty, Type.LiteralValueType)
                        size = LLValue(self.__type_ctx.u64_id, ir.Constant(ir.IntType(64), len_ty.value))  # type: ignore
                        ir_val = self.__build_fat(data, lock, key, zero, size, to_type).ir_val
                    else:
                        ir_val = value.ir_val
                else:
                    ir_val = value.ir_val
            else:
                ir_val = self.__builder.bitcast(value.ir_val, dest_ll_type)  # type: ignore
        else:
            raise ValueError(f"Unsupported cast: {type(src).__name__} → {type(dst).__name__}")
        result_val = LLValue(to_type, ir_val)  # type: ignore
        self.__func.set_reg(result, result_val)
        return result_val

    # -- aggregate --

    def extract_value(self, base: LLValue, index: int, result: str) -> LLValue:
        base_type = self.__type_ctx[base.type_id]
        if isinstance(base_type, Type.StructType):
            fields = self.__type_ctx.get_struct_fields(base.type_id)
            field_type = fields[index].type_id
        elif isinstance(base_type, Type.TupleType):
            field_type = base_type.element_types[index]
        elif isinstance(base_type, Type.EnumType):
            # Enum layout: { i32 discriminant, [pad x i8] payload }
            # Field 0 is always the i32 discriminant.
            field_type = self.__type_ctx.u32_id
        elif isinstance(base_type, Type.PointerType):
            # 5 字段胖指针 {data, lock_ptr, key, index, size}:data/lock 为 u8*,余为 u64
            field_type = self.__type_ctx.alloc_pointer(self.__type_ctx.u8_id) if index in (0, 1) else self.__type_ctx.u64_id
        elif isinstance(base_type, Type.SliceType):
            # slice layout: { T*, i64 } — field 0 为裸 8B 指针
            if index == 0:
                field_type = self.__type_ctx.alloc_pointer(base_type.element_type)
            elif index == 1:
                field_type = self.__type_ctx.u64_id
            else:
                raise ValueError(f"slice has no field {index}")
        elif isinstance(base_type, Type.StrType):
            # str layout: { u8*, i64 } — field 0 为裸 8B 指针
            if index == 0:
                field_type = self.__type_ctx.alloc_pointer(self.__type_ctx.u8_id)
            elif index == 1:
                field_type = self.__type_ctx.u64_id
            else:
                raise ValueError(f"str has no field {index}")
        else:
            field_type = base.type_id
        if self.__ll_type_ctx.is_zst(field_type):
            # Extracting a zero-sized field yields nothing (and the base may be
            # an erased `{}` with no indices to extract from).
            return self.undef(field_type)
        if isinstance(base_type, (Type.SliceType, Type.StrType)) and index == 0:
            # stdlib 边界 fat 合成:⟨data, e_f, k_f, 0, len⟩(t9 §1.6)——裸 8B 指针
            # 提升为胖指针,size = 切片长度;e_f/k_f 来自当前帧锁(惰性实体化)。
            result_val = self.__slice_ptr_fat(base, field_type)
            self.__func.set_reg(result, result_val)
            return result_val
        ir_val = self.__builder.extract_value(base.ir_val, index)  # type: ignore
        result_val = LLValue(field_type, ir_val)
        self.__func.set_reg(result, result_val)
        return result_val

    def __slice_ptr_fat(self, base: LLValue, ptr_type_id: int) -> LLValue:
        """把 slice/str 的裸 8B data 指针合成为 5 字段胖指针 ⟨data, e_f, k_f, 0, len⟩。

        切片的 ptr 字段在类型层是 T*(胖),而 LLVM slice 布局仍为 {i8*, i64} 16B
        (t6 决策,`sizeof(str) == 16` 断言约束)——取字段 0 时在值层提升为胖指针,
        size = 切片长度。锁用全局字面量锁槽(恒 live):合成点常在 `as_struct` 等
        薄包装内,帧锁会随返回失效并逃逸到调用者死栈帧,不得用帧锁。
        """
        raw = self.__builder.extract_value(base.ir_val, 0)  # type: ignore
        length = self.__builder.extract_value(base.ir_val, 1)  # type: ignore
        lock_ir, key_ir = self.__lit_lock_pair()
        data = LLValue(self.__type_ctx.alloc_pointer(self.__type_ctx.u8_id), raw)  # type: ignore
        lock = LLValue(self.__type_ctx.alloc_pointer(self.__type_ctx.u8_id), lock_ir)  # type: ignore
        key = LLValue(self.__type_ctx.u64_id, key_ir)  # type: ignore
        zero = LLValue(self.__type_ctx.u64_id, ir.Constant(ir.IntType(64), 0))  # type: ignore
        size = LLValue(self.__type_ctx.u64_id, length)  # type: ignore
        return self.__build_fat(data, lock, key, zero, size, ptr_type_id)

    def __lit_lock_pair(self) -> tuple[ir.Value, ir.Value]:
        """字面量锁槽 ⟨&__yian_lit_lock, 1⟩(i8*, u64)。"""
        lit_lock = self.__module.get_lit_lock()
        lock_ir = self.__builder.bitcast(lit_lock, ir.PointerType(ir.IntType(8)))  # type: ignore
        return lock_ir, ir.Constant(ir.IntType(64), 1)  # type: ignore

    def insert_value(self, agg: LLValue, value: LLValue, index: int) -> LLValue:
        ir_val = self.__builder.insert_value(agg.ir_val, value.ir_val, index)  # type: ignore
        return LLValue(agg.type_id, ir_val)

    # -- call --

    def call(self, callee: LLFunction, args: list[LLValue], result: str, return_type_id: int) -> LLValue:
        resolved = [self.__promote_fat(a).ir_val for a in args]
        ir_val = self.__builder.call(callee.ir_func, resolved)  # type: ignore
        if self.__ll_type_ctx.is_zst(return_type_id):
            return self.undef(return_type_id)  # `void` call: nothing to bind
        result_val = LLValue(return_type_id, ir_val)
        self.__func.set_reg(result, result_val)
        return result_val

    def call_func(self, callee_type: int, args: list[LLValue], result: str, return_type_id: int) -> LLValue:
        callee = self.__module.get_func(callee_type)
        assert callee is not None, f"Function callee_type={callee_type} not declared"
        return self.call(callee, args, result, return_type_id)

    def call_value(self, callee: LLValue, args: list[LLValue], result: str, return_type_id: int) -> LLValue:
        resolved_args = [self.__promote_fat(a).ir_val for a in args]
        ir_val = self.__builder.call(callee.ir_val, resolved_args)  # type: ignore
        if self.__ll_type_ctx.is_zst(return_type_id):
            return self.undef(return_type_id)  # `void` call: nothing to bind
        result_val = LLValue(return_type_id, ir_val)
        self.__func.set_reg(result, result_val)
        return result_val

    def func_ptr(self, func: LLFunction, func_ptr_type_id: int) -> LLValue:
        return LLValue(func_ptr_type_id, func.ir_func)  # type: ignore

    def func_ptr_by_type(self, func_type_id: int, func_ptr_type_id: int, result: str) -> None:
        callee = self.__module.get_func(func_type_id)
        assert callee is not None, f"FuncPtr func_type_id={func_type_id} not declared"
        self.__func.set_reg(result, self.func_ptr(callee, func_ptr_type_id))

    # -- phi --

    def phi(self, type_id: int, incoming: list[tuple[str, LLValue]], result: str) -> None:
        phi_node = self.__builder.phi(self.__ll_type_ctx.get_ll_type(type_id).ir_type)  # type: ignore
        self.__func.set_reg(result, LLValue(type_id, phi_node))
        for src_label, val in incoming:
            # 检查分裂后,源块终止符落在 ok 连续块——phi 入边用实际前驱块
            pred = self.__continuations.get(src_label, src_label)
            phi_node.add_incoming(val.ir_val, self.__func.block(pred))  # type: ignore

    # -- aggregate construct --

    def __synthesize_fat_value(self, value: LLValue, size: LLValue) -> LLValue:
        """把裸 8B 指针值合成 5 字段胖指针 ⟨data, e_f, k_f, 0, size⟩(t9 §1.6)。

        用于 slice/str 边界:从 16B slice 取出的 ptr 字段是裸 8B 指针,落入
        {T*, u64} 形态聚合(SliceStruct 等)时补全元数据。锁用全局字面量锁槽
        (恒 live)——合成点常在薄包装内,帧锁会随返回失效并逃逸到调用者死栈帧。
        """
        lock_ir, key_ir = self.__lit_lock_pair()
        data = LLValue(self.__type_ctx.alloc_pointer(self.__type_ctx.u8_id),
                       self.__builder.bitcast(value.ir_val, ir.PointerType(ir.IntType(8))))  # type: ignore
        lock = LLValue(self.__type_ctx.alloc_pointer(self.__type_ctx.u8_id), lock_ir)  # type: ignore
        key = LLValue(self.__type_ctx.u64_id, key_ir)  # type: ignore
        zero = LLValue(self.__type_ctx.u64_id, ir.Constant(ir.IntType(64), 0))  # type: ignore
        return self.__build_fat(data, lock, key, zero, size, value.type_id)

    def __build_aggregate(self, type_id: int, field_values: list[LLValue]) -> LLValue:
        """Build an aggregate value by inserting each field value at its index."""
        # An all-zero-sized aggregate erases to `{}` — there is nothing to
        # build, and inserting into `{}` would be an out-of-range index.
        if self.__ll_type_ctx.is_zst(type_id):
            return self.undef(type_id)
        type_def = self.__type_ctx[type_id]
        if isinstance(type_def, (Type.SliceType, Type.StrType)):
            # 边界收窄:slice/str 为 {T*, i64} 16B——胖指针字段取有效地址(data +
            # index·|T|)落槽(t9 §1.6 边界 fat 合成;SliceStruct 等显式结构保持 48B)
            elem_type = type_def.element_type if isinstance(type_def, Type.SliceType) else self.__type_ctx.u8_id
            field_ptr_ll = self.__ll_type_ctx.get_ll_type(elem_type).ir_type.as_pointer()  # type: ignore
            raw = self.__fat_addr(field_values[0], elem_type)
            raw_ir = self.__builder.bitcast(raw.ir_val, field_ptr_ll)  # type: ignore
            val = self.undef(type_id)
            val = self.insert_value(val, LLValue(self.__type_ctx.alloc_pointer(elem_type), raw_ir), 0)  # type: ignore
            val = self.insert_value(val, field_values[1], 1)
            return val
        val = self.undef(type_id)
        for i, fv in enumerate(field_values):
            if self.__ll_type_ctx.is_zst(fv.type_id):
                continue  # zero-sized field: its `{}` slot stays undef
            if self.__is_fat_type(fv.type_id) and not self.__is_fat(fv):
                # {T*, u64} 形态聚合:裸指针字段以 size = 相邻 u64 字段合成 fat
                size_val = self.__aggregate_slice_size(field_values, i)
                fv = self.__synthesize_fat_value(fv, size_val)
            val = self.insert_value(val, fv, i)
        return val

    def __aggregate_slice_size(self, field_values: list[LLValue], index: int) -> LLValue:
        """取 {T*, u64} 形态聚合的相邻 u64 字段作为 size(取 index 后首个 u64)。"""
        one = LLValue(self.__type_ctx.u64_id, ir.Constant(ir.IntType(64), 1))  # type: ignore
        for fv in field_values[index + 1:]:
            if fv.type_id == self.__type_ctx.u64_id:
                return fv
        return one

    def aggregate(self, type_id: int, fields: list[LLValue], result: str) -> None:
        self.__func.set_reg(result, self.__build_aggregate(type_id, fields))

    def array(self, type_id: int, elements: list[LLValue], result: str) -> None:
        self.__func.set_reg(result, self.__build_aggregate(type_id, elements))

    def construct_enum_variant(self, enum_type_id: int, discriminant: int, payload_type: int | None, payload_fields: list[LLValue] | None, result: str) -> None:
        """Construct an enum variant value via temporary alloca + store + load.

        Layout: { i32 discriminant, [pad x i8] payload }
        1. alloca the enum type
        2. store discriminant into field 0
        3. if payload: bitcast field 1 to the payload struct pointer, store payload fields
        4. load the complete enum value
        """
        tmp_ptr = self.__builder.alloca(self.__ll_type_ctx.get_ll_type(enum_type_id).ir_type)  # type: ignore

        # Store discriminant at field 0
        disc_ptr = self.__builder.gep(tmp_ptr, [self.i32(0).ir_val, self.i32(0).ir_val], inbounds=True)  # type: ignore
        self.__builder.store(self.i32(discriminant).ir_val, disc_ptr)  # type: ignore

        if payload_type is not None and not self.__type_ctx.is_zst(payload_type):
            assert payload_fields is not None
            payload_val = self.__build_aggregate(payload_type, payload_fields)
            # Bitcast the payload array pointer (field 1) to the payload struct pointer
            payload_arr_ptr = self.__builder.gep(tmp_ptr, [self.i32(0).ir_val, self.i32(1).ir_val], inbounds=True)  # type: ignore
            payload_ptr_ll = self.__ll_type_ctx.get_ll_type(payload_type).ir_type.as_pointer()  # type: ignore
            payload_typed_ptr = self.__builder.bitcast(payload_arr_ptr, payload_ptr_ll)  # type: ignore
            self.__builder.store(payload_val.ir_val, payload_typed_ptr)  # type: ignore

        ir_val = self.__builder.load(tmp_ptr)  # type: ignore
        self.__func.set_reg(result, LLValue(enum_type_id, ir_val))

    def unpack_enum_payload(
        self, matched: LLValue, block_label: str,
        payload_type_id: int, fields: list[tuple[int, int]],
    ) -> None:
        """Unpack enum variant payload fields into variable allocas.

        ``fields`` is ``(field_index, symbol_id)`` pairs.
        The caller is responsible for positioning the builder appropriately
        (typically at the start of the arm block).
        """
        payload_type_def = self.__type_ctx[payload_type_id]
        assert isinstance(payload_type_def, Type.StructType)
        if self.__type_ctx.is_zst(payload_type_id):
            return  # ZST payload: nothing to unpack
        payload_fields = self.__type_ctx.get_struct_fields(payload_type_id)

        matched_ty = self.__type_ctx[matched.type_id]
        enum_type_id = matched_ty.pointee_type if isinstance(matched_ty, Type.PointerType) else matched.type_id
        base_ptr = self.__fat_addr(matched, enum_type_id) if self.__is_fat(matched) else matched
        gep_val = self.__builder.gep(base_ptr.ir_val, [self.i32(0).ir_val, self.i32(1).ir_val], inbounds=True)  # type: ignore
        payload_ptr_ll_type = self.__ll_type_ctx.get_ll_type(payload_type_id).ir_type.as_pointer()  # type: ignore
        payload = self.__builder.bitcast(gep_val, payload_ptr_ll_type)  # type: ignore

        for field_index, symbol_id in fields:
            if field_index >= len(payload_fields):
                break
            field_ptr = self.__builder.gep(payload, [self.i32(0).ir_val, self.i32(field_index).ir_val], inbounds=True)  # type: ignore
            field_value = self.__builder.load(field_ptr)  # type: ignore
            alloca_ptr = self.__func.get_var_ptr(symbol_id)
            self.__builder.store(field_value, alloca_ptr.ir_val)  # type: ignore

    def unpack_enum_payload_ref(
        self, matched: LLValue, block_label: str,
        payload_type_id: int, fields: list[tuple[int, int]],
    ) -> None:
        """Unpack enum variant payload fields into reference variable allocas.

        ``matched`` is a pointer to the enum (type ``&E``, not a stack copy).
        ``fields`` is ``(field_index, symbol_id)`` pairs.

        Unlike ``unpack_enum_payload`` which loads field values and stores
        copies, this method GEPs to each field and stores the *pointer*
        directly into the variable's alloca. The resulting bindings are
        references (&T) pointing into the original enum value.
        """
        payload_type_def = self.__type_ctx[payload_type_id]
        assert isinstance(payload_type_def, Type.StructType)
        if self.__type_ctx.is_zst(payload_type_id):
            return  # ZST payload: no fields to unpack
        payload_fields = self.__type_ctx.get_struct_fields(payload_type_id)

        matched_ty = self.__type_ctx[matched.type_id]
        enum_type_id = matched_ty.pointee_type if isinstance(matched_ty, Type.PointerType) else matched.type_id
        base_ptr = self.__fat_addr(matched, enum_type_id) if self.__is_fat(matched) else matched
        gep_val = self.__builder.gep(base_ptr.ir_val, [self.i32(0).ir_val, self.i32(1).ir_val], inbounds=True)  # type: ignore
        payload_ptr_ll_type = self.__ll_type_ctx.get_ll_type(payload_type_id).ir_type.as_pointer()  # type: ignore
        payload = self.__builder.bitcast(gep_val, payload_ptr_ll_type)  # type: ignore

        for field_index, symbol_id in fields:
            if field_index >= len(payload_fields):
                break
            field_ptr = self.__builder.gep(payload, [self.i32(0).ir_val, self.i32(field_index).ir_val], inbounds=True)  # type: ignore
            alloca_ptr = self.__func.get_var_ptr(symbol_id)
            # &T 引用槽为 5 字段胖指针:裸字段地址补全元数据(规则 3.5.1)
            field_ll = LLValue(self.__type_ctx.alloc_pointer(payload_fields[field_index].type_id), field_ptr)  # type: ignore
            self.__builder.store(self.__promote_fat(field_ll).ir_val, alloca_ptr.ir_val)  # type: ignore

    # -- sys --

    def sys_write(self, fd: LLValue, buf: LLValue) -> None:
        self.__call_intrinsic(IntrinsicKind.Write, [
            fd, self.__extract_value_raw(buf, 0), self.__extract_value_raw(buf, 1),
        ])

    def mem_copy(self, dest: LLValue, src: LLValue, count: LLValue) -> None:
        """Byte-level copy: ``memcpy(dest, src, count)``.

        The YIAN ``__memcpy`` accepts any pointer pointee types — bitcast
        both to ``i8*`` for the C ``memcpy`` intrinsic.  Fat pointers are
        unwrapped to their ``data`` field first (t8).
        """
        i8_ptr_type = ir.PointerType(ir.IntType(8))
        dest_raw = LLValue(
            self.__type_ctx.alloc_pointer(self.__type_ctx.u8_id),
            self.__builder.bitcast(self.__fat_data(dest).ir_val, i8_ptr_type),  # type: ignore
        )
        src_raw = LLValue(
            self.__type_ctx.alloc_pointer(self.__type_ctx.u8_id),
            self.__builder.bitcast(self.__fat_data(src).ir_val, i8_ptr_type),  # type: ignore
        )
        self.__call_intrinsic(IntrinsicKind.MemCopy, [dest_raw, src_raw, count])

    def sys_read(self, fd: LLValue, buf: LLValue, result: str) -> None:
        buf_ptr = self.__extract_value_raw(buf, 0)
        buf_len = self.__extract_value_raw(buf, 1)
        bytes_read = self.__call_intrinsic(IntrinsicKind.Read, [
            fd, buf_ptr, buf_len,
        ])
        # construct str {i8*, i64} = {buf_ptr, bytes_read}
        str_ll_type = self.__ll_type_ctx.get_ll_type(self.__type_ctx.str_id).ir_type
        undef = ir.Constant(str_ll_type, ir.Undefined)  # type: ignore
        ir_val = self.__builder.insert_value(undef, buf_ptr.ir_val, 0)  # type: ignore
        ir_val = self.__builder.insert_value(ir_val, bytes_read.ir_val, 1)  # type: ignore
        self.__func.set_reg(result, LLValue(self.__type_ctx.str_id, ir_val))  # type: ignore

    def open(self, path: LLValue, flags: LLValue, result: str) -> None:
        # path is a str ({i8*, i64}) — extract the data pointer
        # mode is hardcoded to 0o644 = 420 (rw-r--r--)
        mode = self.i32(420)
        raw = self.__call_intrinsic(IntrinsicKind.Open, [
            self.__extract_value_raw(path, 0), flags, mode,
        ])
        self.__func.set_reg(result, raw)

    def close(self, fd: LLValue, result: str) -> None:
        raw = self.__call_intrinsic(IntrinsicKind.Close, [fd])
        self.__func.set_reg(result, raw)

    # -- yian CLI / env --

    def yian_argc(self, result: str) -> None:
        argc_global = self.__module.argc_global
        loaded = self.__builder.load(argc_global)  # type: ignore
        # zext i32 → u64
        extended = self.__builder.zext(loaded, ir.IntType(64))  # type: ignore
        self.__func.set_reg(result, LLValue(self.__type_ctx.u64_id, extended))  # type: ignore

    def yian_argv_ptr(self, index: LLValue, result: str) -> None:
        argv_global = self.__module.argv_global
        argv_val = self.__builder.load(argv_global)  # type: ignore
        gep = self.__builder.gep(argv_val, [index.ir_val], inbounds=True)  # type: ignore
        loaded = self.__builder.load(gep)  # type: ignore
        ptr_type_id = self.__type_ctx.alloc_pointer(self.__type_ctx.u8_id)
        # C argv 是裸 8B 指针:提升为胖指针(锁用全局字面量锁槽,恒 live)
        fat = self.__promote_fat(LLValue(ptr_type_id, loaded))  # type: ignore
        self.__func.set_reg(result, fat)

    def yian_cstrlen(self, ptr: LLValue, result: str) -> None:
        raw = self.__call_intrinsic(IntrinsicKind.StrLen, [self.__fat_data(ptr)])
        self.__func.set_reg(result, raw)

    def yian_exit(self, code: LLValue) -> None:
        self.__call_intrinsic(IntrinsicKind.Exit, [code])
        self.__builder.unreachable()

    # ------------------------------------------------------------------
    # terminators
    # ------------------------------------------------------------------

    def set_frame_lock(self, e_f: IR.Value) -> None:
        """标记函数已实体化帧锁(规则 3.7.1):全部返回路径 ret 前写 SENTINEL。"""
        assert isinstance(e_f, IR.Reg), "帧锁槽地址 e_f 必须为寄存器(入口 alloca)"
        self.__frame_lock_slot_name = e_f.name

    def __frame_exit_sentinel(self) -> None:
        """规则 3.7.2 动作①:帧退出把帧锁槽写成 SENTINEL(全部返回路径)。

        e_f 是入口 alloca 的锁槽地址(entry 块先于一切 ret 翻译,寄存器已就绪);
        SENTINEL = 全 1 字,栈悬垂访问经 live 键比较(match SENTINEL != k_f)trap。
        """
        if self.__frame_lock_slot_name is None:
            return
        slot = self.__func.reg(self.__frame_lock_slot_name)
        self.write_lock_slot(slot, self.i64(IR.SENTINEL))

    def ret(self, value: LLValue | None) -> None:
        self.__frame_exit_sentinel()
        if value is None:
            self.__builder.ret_void()
        else:
            self.__builder.ret(self.__promote_fat(value).ir_val)  # type: ignore

    def br(self, target_label: str) -> None:
        self.__builder.branch(self.__func.block(target_label))  # type: ignore

    def condbr(self, cond: LLValue, then_label: str, else_label: str) -> None:
        self.__builder.cbranch(  # type: ignore
            cond.ir_val,
            self.__func.block(then_label),
            self.__func.block(else_label),
        )

    def switch(self, value: LLValue, cases: list[tuple[LLValue, str]], default_label: str) -> None:
        """Emit a switch instruction. ``value`` must be integer/char type.
        ``cases`` are ``(case_value, target_label)`` pairs.
        """
        default_block = self.__func.block(default_label) if default_label else None
        if default_block is None:
            default_block = self.__func.new_block("match.unreach")
            ir.IRBuilder(default_block).unreachable()
        switch_instr = self.__builder.switch(value.ir_val, default_block)  # type: ignore
        for case_value, target_label in cases:
            switch_instr.add_case(case_value.ir_val, self.__func.block(target_label))  # type: ignore

    def unreachable(self) -> None:
        self.__builder.unreachable()

    def panic(self, msg: LLValue) -> None:
        self.__call_intrinsic(IntrinsicKind.Write, [
            self.i32(2), self.__extract_value_raw(msg, 0), self.__extract_value_raw(msg, 1),
        ])
        self.__call_intrinsic(IntrinsicKind.Exit, [self.i32(1)])
        self.__builder.unreachable()

    # ------------------------------------------------------------------
    # internal
    # ------------------------------------------------------------------

    def __extract_value_raw(self, base: LLValue, index: int) -> LLValue:
        ir_val = self.__builder.extract_value(base.ir_val, index)  # type: ignore
        return LLValue(base.type_id, ir_val)

    def __call_intrinsic(self, kind: IntrinsicKind, args: list[LLValue]) -> LLValue:
        callee = self.__module.intrinsics.get(kind)
        raw_args = [a.ir_val for a in args]
        result = self.__builder.call(callee, raw_args)  # type: ignore
        return LLValue(self.__intrinsic_return_type_id(kind), result)

    def __intrinsic_return_type_id(self, kind: IntrinsicKind) -> int:
        match kind:
            case IntrinsicKind.Malloc:
                return self.__type_ctx.alloc_pointer(self.__type_ctx.u8_id)
            case IntrinsicKind.Free | IntrinsicKind.Exit | IntrinsicKind.MemCopy:
                return self.__type_ctx.void_id
            case IntrinsicKind.Write | IntrinsicKind.Read:
                return self.__type_ctx.u64_id
            case IntrinsicKind.Open | IntrinsicKind.Close:
                return self.__type_ctx.i32_id
            case IntrinsicKind.SysRandom:
                return self.__type_ctx.u32_id
            case IntrinsicKind.StrLen:
                return self.__type_ctx.u64_id

    def __cmp_impl(self, op: BinaryOperator, lhs: ir.Value, rhs: ir.Value, type_id: int) -> ir.Value:
        if self.__type_ctx.is_zst(type_id):
            # All ZST values are indistinguishable — EQ is always true, NE always false
            is_eq = op in (BinaryOperator.Eq,)
            return ir.Constant(ir.IntType(1), 1 if is_eq else 0)  # type: ignore
        if isinstance(lhs.type, ir.LiteralStructType) or isinstance(rhs.type, ir.LiteralStructType):  # type: ignore
            # 胖指针聚合比较兜底(规则 3.4.1-3.4.2,§7.6 风险 3):LLVM 无聚合
            # icmp → 字段比较。正常路径由 CFG 路由至 PtrCmp;此分支兜底 CFG
            # 未路由的聚合操作数(如 nullptr 以未统一指针类型参与比较)。
            return self.__cmp_fat_values(op, lhs, rhs)
        predicate = {
            BinaryOperator.Eq: "==", BinaryOperator.Neq: "!=",
            BinaryOperator.Lt: "<", BinaryOperator.Gt: ">",
            BinaryOperator.Leq: "<=", BinaryOperator.Geq: ">=",
        }[op]
        if isinstance(lhs.type, (ir.IntType, ir.PointerType)):  # type: ignore
            ty = self.__type_ctx[type_id]
            if isinstance(ty, Type.IntType) and not ty.signed:
                return self.__builder.icmp_unsigned(predicate, lhs, rhs)  # type: ignore
            return self.__builder.icmp_signed(predicate, lhs, rhs)  # type: ignore
        return self.__builder.fcmp_ordered(predicate, lhs, rhs)  # type: ignore

    def __fat_value_pair(self, v: ir.Value) -> tuple[ir.Value, ir.Value]:
        """ir.Value 级别的 (data, index) 字段对:聚合提取字段;裸指针按 (自身, 0)。"""
        if isinstance(v.type, ir.LiteralStructType):  # type: ignore
            return (self.__builder.extract_value(v, IR.FAT_DATA),  # type: ignore
                    self.__builder.extract_value(v, IR.FAT_INDEX))  # type: ignore
        return (self.__builder.bitcast(v, ir.PointerType(ir.IntType(8))),  # type: ignore
                ir.Constant(ir.IntType(64), 0))  # type: ignore

    def __cmp_fat_values(self, op: BinaryOperator, lhs: ir.Value, rhs: ir.Value) -> ir.Value:
        """对 ir.Value 级胖指针聚合操作数做字段比较(规则 3.4.1-3.4.2)。

        相等按 (data, index) 二元组;序比较先插 data 相等前提检查(规则 3.4.1,
        跨对象 trap)——未由 CFG 路由至此分支,须现场插检。
        """
        data_l, idx_l = self.__fat_value_pair(lhs)
        data_r, idx_r = self.__fat_value_pair(rhs)
        if op in (BinaryOperator.Eq, BinaryOperator.Neq):
            data_eq = self.__builder.icmp_signed("==", data_l, data_r)  # type: ignore
            idx_eq = self.__builder.icmp_unsigned("==", idx_l, idx_r)  # type: ignore
            both = self.__builder.and_(data_eq, idx_eq)  # type: ignore
            return self.__builder.not_(both) if op == BinaryOperator.Neq else both  # type: ignore
        data_ok = self.__builder.icmp_signed("==", data_l, data_r)  # type: ignore
        self.__emit_check(LLValue(self.__type_ctx.bool_id, data_ok), "ptrcmp")
        predicate = {
            BinaryOperator.Lt: "<", BinaryOperator.Gt: ">",
            BinaryOperator.Leq: "<=", BinaryOperator.Geq: ">=",
        }[op]
        return self.__builder.icmp_unsigned(predicate, idx_l, idx_r)  # type: ignore

    ARITH_OPS = {
        BinaryOperator.Add: "add", BinaryOperator.Sub: "sub",
        BinaryOperator.Mul: "mul", BinaryOperator.Div: "sdiv",
        BinaryOperator.Mod: "srem", BinaryOperator.BitAnd: "and_",
        BinaryOperator.BitOr: "or_", BinaryOperator.BitXor: "xor",
        BinaryOperator.Shl: "shl", BinaryOperator.Shr: "ashr",
    }

    FLOAT_ARITH_OPS = {
        BinaryOperator.Add: "fadd", BinaryOperator.Sub: "fsub",
        BinaryOperator.Mul: "fmul", BinaryOperator.Div: "fdiv",
        BinaryOperator.Mod: "frem",
    }

    def __arith_impl(self, op: BinaryOperator, lhs: ir.Value, rhs: ir.Value, type_id: int) -> ir.Value:
        if isinstance(lhs.type, ir.types._BaseFloatType):  # type: ignore
            return getattr(self.__builder, self.FLOAT_ARITH_OPS[op])(lhs, rhs)
        ty = self.__type_ctx[type_id]
        if isinstance(ty, Type.IntType) and not ty.signed:
            if op == BinaryOperator.Div:
                return self.__builder.udiv(lhs, rhs)  # type: ignore
            if op == BinaryOperator.Mod:
                return self.__builder.urem(lhs, rhs)  # type: ignore
            if op == BinaryOperator.Shr:
                return self.__builder.lshr(lhs, rhs)  # type: ignore
        return getattr(self.__builder, self.ARITH_OPS[op])(lhs, rhs)
