# pyright: reportUnknownMemberType=false
"""
LLBuilder — wraps llvmlite ``ir.IRBuilder`` to hide LLVM-level details.

llvmlite 0.44 leaves the operand types of several dynamic IRBuilder methods
unspecified; suppress that dependency-boundary noise while keeping every other
strict Pyright diagnostic enabled for this module.
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

    def __init__(self, func: LLFunction, module: LLModule, ll_type_ctx: LLTypeCtx, type_ctx: TypeCtx, raw_pointers: bool = False) -> None:
        self.__func = func
        self.__module = module
        self.__ll_type_ctx = ll_type_ctx
        self.__type_ctx = type_ctx
        self.__raw_pointers = raw_pointers
        self.__builder: ir.IRBuilder
        self.__check_seq = 0
        self.__continuations: dict[str, str] = {}
        self.__current_cfg_block = ""
        self.__frame_lock_slot_name: str | None = None  # 已从稳定影子栈取得的 e_f 寄存器名(规则 3.7.1)

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
        """判定 LLVM 值是否已是胖结构聚合(PointerType 40B / SliceType 32B / RefType 24B)。

        t2 三结构统一:三个指针族类型的 LLVM 值形态为 LiteralStructType 即视为胖值;
        raw 模式无元数据(裸 T* / {T*, u64} / 裸 T*),恒 False。从 slice/str 提取的
        裸 8B 指针(type_id 为 PointerType)按 LLVM 值形态(指针而非结构)不误判。
        """
        if self.__raw_pointers:
            return False
        ty = self.__type_ctx[ll_val.type_id]
        if isinstance(ty, (Type.PointerType, Type.SliceType, Type.StrType, Type.RefType)):
            # 胖值须为多字段结构(40B/32B/24B);空结构 `{}`(ZST 擦除,ref/ptr-to-ZST
            # 零运行时信息)按非胖处理——对它的任意字段操作均无意义(t6 修)。
            return isinstance(ll_val.ir_val.type, ir.LiteralStructType) and len(ll_val.ir_val.type.elements) > 0  # type: ignore
        return False

    def __is_fat_type(self, type_id: int) -> bool:
        """类型层胖指针判定(t3 按 type_id 分派):PointerType(pointee 非 ZST)
        5 字段 / SliceType·StrType 4 字段 / RefType 3 字段,均为胖结构值;
        raw 模式(t1)恒 False,指针一律按裸 8B 处理。与 CFG 层 __is_fat_pointer
        对应(后者专指 PointerType 的全检查路径)。
        """
        if self.__raw_pointers:
            return False
        ty = self.__type_ctx[type_id]
        if isinstance(ty, Type.PointerType):
            return not self.__type_ctx.is_zst(ty.pointee_type)
        return isinstance(ty, (Type.SliceType, Type.StrType, Type.RefType))

    def __extract_fat_field(self, ll_val: LLValue, index: int) -> LLValue:
        """提取胖指针聚合字段;整数字段 u64、指针字段 u8*(下标约定见 FAT_*/SLICE_*/REF_*)。"""
        ir_val = self.__builder.extract_value(ll_val.ir_val, index)  # type: ignore
        if index in (IR.FAT_DATA, IR.FAT_LOCK_PTR):
            field_type = self.__type_ctx.alloc_pointer(self.__type_ctx.u8_id)
        else:
            field_type = self.__type_ctx.u64_id
        return LLValue(field_type, ir_val)  # type: ignore

    def __build_fat(self, data: LLValue, lock_ptr: LLValue, key: LLValue, index: LLValue, size: LLValue, type_id: int) -> LLValue:
        """按结构构造胖值(t2 三结构):PointerType 5 字段 ⟨data,lock,key,index,size⟩ /
        SliceType 4 字段 ⟨data,lock,key,size⟩(删 index)/ RefType 3 字段 ⟨data,lock,key⟩。
        """
        ty = self.__type_ctx[type_id]
        val = self.undef(type_id)
        val = self.insert_value(val, data, IR.FAT_DATA)
        val = self.insert_value(val, lock_ptr, IR.FAT_LOCK_PTR)
        val = self.insert_value(val, key, IR.FAT_KEY)
        if isinstance(ty, Type.PointerType):
            val = self.insert_value(val, index, IR.FAT_INDEX)
            val = self.insert_value(val, size, IR.FAT_SIZE)
        elif isinstance(ty, (Type.SliceType, Type.StrType)):
            val = self.insert_value(val, size, IR.SLICE_SIZE)
        elif isinstance(ty, Type.RefType):
            pass
        else:
            raise ValueError(f"not a pointer-family type: {type(ty).__name__}")
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

    def __fat_addr(self, ll_val: LLValue, pointee_type_id: int) -> LLValue:
        """定义 17 地址折算:有效地址 = data + index·|T|,一次 GEP(检查与取数共用)。

        对 fat 值提取 data/index 字段,bitcast 到 T* 后按元素索引 GEP;对裸 8B
        指针直接使用(索引恒 0 语义)。T&(t3)无 index 字段,恒指单个元素——
        有效地址即 data。O-1 无回摆由检查(well-formed)保障。
        """
        if self.__is_fat(ll_val):
            data = self.__extract_fat_field(ll_val, IR.FAT_DATA).ir_val
            pointee_ll = self.__ll_type_ctx.get_ll_type(pointee_type_id).ir_type
            ty = self.__type_ctx[ll_val.type_id]
            if isinstance(ty, Type.RefType):
                # T& 3 字段 {data, lock_ptr, key}:无 index,恒指单个元素——
                # 有效地址 = data(bitcast 到 T* 供调用方按字段 GEP)
                addr = self.__builder.bitcast(data, pointee_ll.as_pointer())  # type: ignore
            else:
                index = self.__extract_fat_field(ll_val, IR.FAT_INDEX).ir_val
                typed = self.__builder.bitcast(data, pointee_ll.as_pointer())  # type: ignore
                addr = self.__builder.gep(typed, [index], inbounds=False)  # type: ignore
        else:
            addr = ll_val.ir_val
        return LLValue(self.__type_ctx.alloc_pointer(pointee_type_id), addr)  # type: ignore

    def __gen_key_value(self, is_heap: bool) -> LLValue:
        """Emit a non-wrapping monotonic key, trapping on exhaustion.

        Heap body ``BODY_MASK`` is reserved because adding the heap flag would
        produce the all-ones ``SENTINEL``.  Stack keys may use that body value
        because their most-significant bit remains zero.
        """
        counter = self.__module.get_key_counter(is_heap)
        loaded = self.__builder.load(counter)  # type: ignore
        limit = IR.MAX_HEAP_BODY if is_heap else IR.MAX_STACK_BODY
        available = self.__builder.icmp_unsigned(
            "<", loaded, ir.Constant(ir.IntType(64), limit)  # type: ignore
        )
        self.__emit_check(LLValue(self.__type_ctx.bool_id, available), "keyex")
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

    def __extract_check_fields(self, ll_val: LLValue) -> tuple[ir.Value, ir.Value, ir.Value, ir.Value]:
        """一次提取 check 字段 bundle (lock, key, index, size)——仅 PointerType(5 字段)。

        check_safe_access 用 bundle 一次取齐 live(lock/key)与 in_bounds(index/size)
        两谓词所需字段,避免各谓词重复 extract;RefType(3 字段,无 index/size)不得经此。
        """
        ty = self.__type_ctx[ll_val.type_id]
        if not isinstance(ty, Type.PointerType):
            raise ValueError(f"check field bundle requires PointerType, got {type(ty).__name__}")
        lock = self.__extract_fat_field(ll_val, IR.FAT_LOCK_PTR).ir_val
        key = self.__extract_fat_field(ll_val, IR.FAT_KEY).ir_val
        index = self.__extract_fat_field(ll_val, IR.FAT_INDEX).ir_val
        size = self.__extract_fat_field(ll_val, IR.FAT_SIZE).ir_val
        return lock, key, index, size

    def __check_live(self, lock_ptr: ir.Value, key: ir.Value) -> LLValue:
        """定义 8 live(p):锁槽键比较 μ⟨lock_ptr⟩ == key,含 null 短路。

        lock_ptr/key 由调用方预提取传入(check_delete/check_safe_access 复用提取,
        避免重复 extract);锁槽 load 留在 __emit_guarded 守卫内——null 短路为假,
        不读地址 0 物理槽位,避免段错误退化。
        """
        guard = self.__builder.icmp_signed("!=", lock_ptr, ir.Constant(lock_ptr.type, None))  # type: ignore

        def compute(builder: ir.IRBuilder) -> ir.Value:
            slot = builder.bitcast(lock_ptr, ir.PointerType(ir.IntType(64)))  # type: ignore
            slot_val = builder.load(slot)  # type: ignore
            return builder.icmp_signed("==", slot_val, key)  # type: ignore

        return LLValue(self.__type_ctx.bool_id, self.__emit_guarded(guard, compute))  # type: ignore

    def __check_in_bounds_cond(self, index: ir.Value, size: ir.Value) -> LLValue:
        """定义 12 in_bounds(p,1):0 ≤ index ∧ index+1 ≤ size,简化为 index < size(u64)。"""
        cond = self.__builder.icmp_unsigned("<", index, size)  # type: ignore
        return LLValue(self.__type_ctx.bool_id, cond)  # type: ignore

    def __check_live_and(self, lock_ptr: ir.Value, key: ir.Value, other: ir.Value) -> ir.Value:
        """live(p) ∧ other(i1 值)。"""
        live_val = self.__check_live(lock_ptr, key)
        return self.__builder.and_(live_val.ir_val, other)  # type: ignore

    def string_literal(self, value: str, type_id: int) -> LLValue:
        """Create a str value for a string literal:4 字段 {data, lock_ptr, key, size}(raw 2 字段)。

        字面量数据是全局只读区、生命周期为整个程序——锁用全局字面量锁槽(恒 live)。
        """
        encoded = value.encode("utf-8")
        global_var = self.__module.get_string_global(encoded)
        ptr = global_var.gep([ir.Constant(ir.IntType(32), 0), ir.Constant(ir.IntType(32), 0)])  # type: ignore
        length = ir.Constant(ir.IntType(64), len(encoded))  # type: ignore
        if self.__raw_pointers:
            return LLValue(type_id, ir.Constant.literal_struct([ptr, length]))  # type: ignore
        lock_ir = self.__module.get_lit_lock().bitcast(ir.PointerType(ir.IntType(8)))  # type: ignore
        key_ir = ir.Constant(ir.IntType(64), 1)  # type: ignore
        return LLValue(type_id, ir.Constant.literal_struct([ptr, lock_ir, key_ir, length]))  # type: ignore

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

    def var_ptr(self, symbol_id: int, result: str, frame_lock_ptr: LLValue | None = None, frame_key: LLValue | None = None, raw: bool = False) -> LLValue:
        alloca_ptr = self.__func.get_var_ptr(symbol_id)
        if raw or not self.__is_fat_type(alloca_ptr.type_id):
            # lazy-lvalue-fat(todo1):裸取址(未取址左值)仅返回栈地址;raw 模式下
            # __is_fat_type 恒 False(既有行为),此处同样裸返回。
            result_val = alloca_ptr
        else:
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
        self.__func.set_reg(result, result_val)
        return result_val

    def alloca(self, type_id: int) -> LLValue:
        ptr_type_id = self.__type_ctx.alloc_pointer(type_id)
        ll_type = self.__ll_type_ctx.get_ll_type(type_id).ir_type
        entry_block = self.__func.entry_block
        if self.__builder.block is not entry_block:  # type: ignore
            # 循环体/分支内隐式取址(如字面量实参 &0)与枚举构造把 alloca 发到
            # 非 entry 块 → llvmlite SelectionDAG 按动态栈分配下降(rsp 单调
            # -32B/调用),大迭代循环栈泄漏 SIGSEGV。统一提升到 entry 块末尾
            # (终止符之前)发射,保持静态栈分配语义;不扰动当前插入位置。
            entry_builder = ir.IRBuilder(entry_block)
            if entry_block.is_terminated:
                entry_builder.position_before(entry_block.instructions[-1])  # type: ignore
            else:
                entry_builder.position_at_end(entry_block)  # type: ignore
            return LLValue(ptr_type_id, entry_builder.alloca(ll_type))  # type: ignore
        return LLValue(ptr_type_id, self.__builder.alloca(ll_type))  # type: ignore

    def alloca_store(self, value: LLValue, result: str) -> None:
        alloca_val = self.alloca(value.type_id)
        self.store(value, alloca_val)
        self.__func.set_reg(result, alloca_val)

    def malloc(self, type_id: int, size: LLValue, key: LLValue | None, result: str) -> LLValue:
        if self.__type_ctx.is_zst(type_id):
            ptr_type_id = self.__type_ctx.alloc_pointer(type_id)
            self.__func.set_reg(result, LLValue(ptr_type_id, ir.Constant(self.__ll_type_ctx.get_ll_type(ptr_type_id).ir_type, ir.Undefined)))  # type: ignore
            return LLValue(ptr_type_id, ir.Constant(self.__ll_type_ctx.get_ll_type(ptr_type_id).ir_type, ir.Undefined))  # type: ignore
        # Convert element count to byte count for allocation.
        # O-1 无回绕:元素数 n 与元素大小 |T| 的乘积、以及固定池块头,一律在 i128
        # 宽算中完成,再检测 total ≥ 2^64(分配请求超限)→ trap。否则纯 64 位乘法
        # 回绕(如 n=2^62+1,|T|=8 → 2^65 → 小值)会令物理分配过小,而胖指针
        # size 字段 = n(元素数,无回绕),in_bounds 全部通过 → 越界访问逃过检查。
        elem_size = self.__ll_type_ctx.get_type_size(type_id)
        i128: ir.IntType = ir.IntType(128)  # type: ignore
        size128 = self.__builder.zext(size.ir_val, i128)  # type: ignore
        payload128 = self.__builder.mul(size128, ir.Constant(i128, elem_size))  # type: ignore
        total128 = payload128
        if not self.__raw_pointers:
            # 规则 3.6.1:块 = 稳定池块头(H=32)+负载;块头首字为锁槽。
            # raw 模式无锁头(块 = 负载,data = 块基址)。
            total128 = self.__builder.add(
                total128, ir.Constant(i128, IR.BlockHeader.BYTES)  # type: ignore
            )
        # O-1 溢出检查(raw 模式保留:防御性,决策点已定)
        fits = self.__builder.icmp_unsigned("<", total128, ir.Constant(i128, 1 << 64))  # type: ignore
        self.__emit_check(LLValue(self.__type_ctx.bool_id, fits), "mof")
        payload_ir = self.__builder.trunc(payload128, ir.IntType(64))  # type: ignore
        payload = LLValue(self.__type_ctx.u64_id, payload_ir)  # type: ignore
        ptr_type_id = self.__type_ctx.alloc_pointer(type_id)
        if self.__raw_pointers:
            raw = self.__call_intrinsic(IntrinsicKind.Malloc, [payload])
            # raw 模式:data = 块基址,直接返回裸指针(无锁头偏移、无 5 字段聚合)。
            # malloc intrinsic 返回 i8*,须 bitcast 到有型 T*(raw 指针为 T*)。
            typed = self.__builder.bitcast(raw.ir_val, self.__ll_type_ctx.get_ll_type(ptr_type_id).ir_type)  # type: ignore
            result_val = LLValue(ptr_type_id, typed)  # type: ignore
            self.__func.set_reg(result, result_val)
            return result_val
        block_ir = self.__builder.call(self.__module.get_pool_alloc(), [payload.ir_val])  # type: ignore
        block_base = LLValue(ptr_type_id, block_ir)  # type: ignore
        if key is not None:
            slot_ptr = self.__builder.bitcast(block_base.ir_val, ir.PointerType(ir.IntType(64)))  # type: ignore
            self.__builder.store(key.ir_val, slot_ptr)  # type: ignore
            key_ir = key.ir_val
        else:
            key_ir = ir.Constant(ir.IntType(64), 0)  # type: ignore
        data_ir = self.__builder.gep(
            block_base.ir_val,
            [ir.Constant(ir.IntType(64), IR.BlockHeader.BYTES)],  # type: ignore
            inbounds=False,
        )
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
        # 规则 3.6.2 动作②:块进入稳定头空闲池,不向 libc 归还。
        if self.__is_fat(ptr):
            block_base = self.__extract_fat_field(ptr, IR.FAT_LOCK_PTR)
            self.__builder.call(self.__module.get_pool_release(), [block_base.ir_val])  # type: ignore
            return
        i8_ptr_type_id = self.__type_ctx.alloc_pointer(self.__type_ctx.u8_id)
        if isinstance(self.__type_ctx[ptr.type_id], Type.SliceType):
            # raw 模式(del-view)slice 为 {T*, u64} 聚合:整块释放须取 data
            # 字段(0)——直接 bitcast 聚合为指针是非法 IR。T*/T& 在 raw 模式
            # 为裸 8B 指针,不受影响。
            data = self.__builder.extract_value(ptr.ir_val, 0)  # type: ignore
            casted = self.__builder.bitcast(data, ir.PointerType(ir.IntType(8)))  # type: ignore
            self.__call_intrinsic(IntrinsicKind.Free, [LLValue(i8_ptr_type_id, casted)])  # type: ignore
            return
        casted = self.__builder.bitcast(ptr.ir_val, ir.PointerType(ir.IntType(8)))  # type: ignore
        self.__call_intrinsic(IntrinsicKind.Free, [LLValue(i8_ptr_type_id, casted)])  # type: ignore

    # -- fat-pointer mechanism nodes (t8) --

    def gen_key(self, is_heap: bool, result: str) -> None:
        self.__func.set_reg(result, self.__gen_key_value(is_heap))

    def acquire_frame_lock(self, key: LLValue, result: str) -> None:
        """规则 3.7.1:在固定地址的独立影子栈上 push 一个帧锁槽。

        热路径只执行一次深度检查、一次 GEP 和两次 store;无动态
        分配或空闲链指针追踪。槽位先写新键,再发布新深度,且在任何
        用户语句之前完成。
        """
        depth_ptr = self.__module.get_frame_lock_depth()
        depth = self.__builder.load(depth_ptr, name="frame.depth")  # type: ignore
        available = self.__builder.icmp_unsigned(
            "<",
            depth,
            ir.Constant(ir.IntType(64), IR.FrameLockArena.SLOTS),  # type: ignore
        )
        self.__emit_check(
            LLValue(self.__type_ctx.bool_id, available), "framecap"
        )
        arena = self.__module.get_frame_lock_arena()
        slot = self.__builder.gep(  # type: ignore
            arena,
            [ir.Constant(ir.IntType(64), 0), depth],  # type: ignore
            inbounds=True,
            name="frame.lock",
        )
        self.__builder.store(key.ir_val, slot)  # type: ignore
        next_depth = self.__builder.add(  # type: ignore
            depth, ir.Constant(ir.IntType(64), 1), name="frame.depth.next"  # type: ignore
        )
        self.__builder.store(next_depth, depth_ptr)  # type: ignore
        self.__func.set_reg(
            result,
            LLValue(self.__type_ctx.alloc_pointer(TypeCtx.u64_id), slot),
        )

    def write_lock_slot(self, lock_ptr: LLValue, value: LLValue) -> None:
        """μ⟨lock_ptr⟩ := value(规则 3.6.2 动作① SENTINEL / 3.7.1 帧锁写键)。

        指针恒非空,锁槽恒可写(空容器持真实堆块)。
        """
        raw = self.__fat_data(lock_ptr).ir_val
        slot_ptr = self.__builder.bitcast(raw, ir.PointerType(ir.IntType(64)))  # type: ignore
        self.__builder.store(value.ir_val, slot_ptr)  # type: ignore

    def check_safe_access(self, ptr: LLValue) -> None:
        """safe_access(p,1) = live(p) ∧ in_bounds(p,1)(规则 3.2.1-3.2.2)。"""
        if not self.__is_fat(ptr):
            return
        lock, key, index, size = self.__extract_check_fields(ptr)
        in_bounds = self.__check_in_bounds_cond(index, size)
        cond = self.__check_live_and(lock, key, in_bounds.ir_val)
        self.__emit_check(LLValue(self.__type_ctx.bool_id, cond), "safe")

    def check_in_bounds(self, ptr: LLValue) -> None:
        """in_bounds(p_s,1)(规则 3.5.2 重锚定前提)。"""
        if not self.__is_fat(ptr):
            return
        index = self.__extract_fat_field(ptr, IR.FAT_INDEX).ir_val
        size = self.__extract_fat_field(ptr, IR.FAT_SIZE).ir_val
        self.__emit_check(self.__check_in_bounds_cond(index, size), "ib")

    def check_slice_nonempty(self, ptr: LLValue) -> None:
        """Establish the one-element origin invariant for ``T[] -> T&``."""
        if not self.__is_fat(ptr):
            return
        size = self.__extract_fat_field(ptr, IR.SLICE_SIZE).ir_val
        nonempty = self.__builder.icmp_unsigned(
            ">", size, ir.Constant(ir.IntType(64), 0)  # type: ignore
        )
        self.__emit_check(LLValue(self.__type_ctx.bool_id, nonempty), "sref")

    def check_ref_access(self, ptr: LLValue) -> None:
        """T& 引用访问前检:仅 live(免 in_bounds,tiered-pointers t3)。

        引用无 index/size(3 字段 ⟨data,lock_ptr,key⟩),无越界概念;
        live = 锁槽键比较(定义 8,含 null 短路)。live 失败 → llvm.trap。
        """
        if not self.__is_fat(ptr):
            return
        lock_ptr = self.__extract_fat_field(ptr, IR.FAT_LOCK_PTR).ir_val
        key = self.__extract_fat_field(ptr, IR.FAT_KEY).ir_val
        cond = self.__check_live(lock_ptr, key)
        self.__emit_check(cond, "ref")

    def assume(self, cond: LLValue) -> None:
        """llvm.assume(cond):优化器提示 cond 恒真(perf P1,CFG Assume 节点)。

        只承载编译期可证纯数据谓词(如 range 循环契约 0 ≤ i < n,u64 同型);
        llvm.assume 无运行期代码,仅供 ConstraintElimination 等优化器消除
        可证冗余检查。绝不承载动态事实(assume 假 → UB → 删检查 → 安全失效)。
        """
        self.__builder.assume(cond.ir_val)  # type: ignore[reportUnknownMemberType]  # llvmlite assume 签名未标注

    def check_element_arith(self, base: LLValue, offset: LLValue) -> None:
        """定义 13 良构检查:0 ≤ index+offset ≤ size;u64 同型化(回绕检测 + 上界比较)。

        对 u64 索引(非负恒真)把 0 ≤ index+offset ≤ size 分解为 u64 计算:
        (a) 回绕检测 no_wrap = icmp uge sum, index(sum = index+offset,u64 回绕
        ⟺ sum < index);(b) 上界比较 in_range = icmp ule sum, size;条件 =
        and(no_wrap, in_range)。与 i128 语义论证:正偏移(offset < 2^63 且
        index+offset < 2^64)无回绕时 sum ≥ index 恒真,只剩上界比较,与 i128
        完全等价;回绕(index+offset ≥ 2^64)时 sum < index → no_wrap 失败 →
        trap(等价 i128 的 sum ≥ 2^64 > size 必 trap);下溢偏移(offset ≥ 2^63,
        语义 = 极大无符号下标)u64 形式 trap(收紧,对齐 u64 索引语义——sext
        形式把其误解为负数,子切片 base.index ≥ |o| 时放行)。u64 同型使
        ConstraintElimination 可关联循环/分支约束消除本检查(O-1 由回绕检测
        落地,无需宽整数)。t8 发射。
        """
        if not self.__is_fat(base):
            return
        index = self.__extract_fat_field(base, IR.FAT_INDEX).ir_val
        size = self.__extract_fat_field(base, IR.FAT_SIZE).ir_val
        off64 = self.__builder.bitcast(offset.ir_val, ir.IntType(64))  # type: ignore
        sum = self.__builder.add(index, off64)  # type: ignore
        no_wrap = self.__builder.icmp_unsigned(">=", sum, index)  # type: ignore
        in_range = self.__builder.icmp_unsigned("<=", sum, size)  # type: ignore
        cond: ir.Value = self.__builder.and_(no_wrap, in_range)  # type: ignore
        self.__emit_check(LLValue(self.__type_ctx.bool_id, cond), "elarith")

    def check_element_access(self, base: LLValue, offset: LLValue, ptr: LLValue) -> None:
        """C3 合并检查:ElementArith→InBounds→SafeAccess 合取谓词(perf-optimization todo 3)。

        派生链 elem = base + offset → f = elem.field → 访问 f 的三重检查合并:
        良构(elem)(定义 13,u64 同型化:回绕检测 + 上界比较)∧ in_bounds(elem,1)
        (规则 3.5.2,one-past-end 的 elem 取字段 trap)∧ live(elem)(定义 8,
        SafeAccess 的 live 项;in_bounds(f,1) 对重锚定字段指针恒真、
        live(f)=live(elem) 由锁字段继承)。禁止丢 no-wrap/live 任一子项。
        t8 发射。
        """
        if not (self.__is_fat(base) and self.__is_fat(ptr)):
            return
        # ElementArith 部分(定义 13,同 check_element_arith 的 u64 同型化:
        # 回绕检测 no_wrap = icmp uge sum, index + 上界比较 in_range =
        # icmp ule sum, size;语义等价性论证见 check_element_arith 注释)
        index = self.__extract_fat_field(base, IR.FAT_INDEX).ir_val
        size = self.__extract_fat_field(base, IR.FAT_SIZE).ir_val
        off64 = self.__builder.bitcast(offset.ir_val, ir.IntType(64))  # type: ignore
        sum = self.__builder.add(index, off64)  # type: ignore
        no_wrap = self.__builder.icmp_unsigned(">=", sum, index)  # type: ignore
        in_range = self.__builder.icmp_unsigned("<=", sum, size)  # type: ignore
        elarith_cond: ir.Value = self.__builder.and_(no_wrap, in_range)  # type: ignore
        # InBounds 部分(规则 3.5.2):elem.index < elem.size
        e_index = self.__extract_fat_field(ptr, IR.FAT_INDEX).ir_val
        e_size = self.__extract_fat_field(ptr, IR.FAT_SIZE).ir_val
        ib_cond = self.__builder.icmp_unsigned("<", e_index, e_size)  # type: ignore
        # live 部分(定义 8):锁槽键比较,含 null 短路
        lock = self.__extract_fat_field(ptr, IR.FAT_LOCK_PTR).ir_val
        key = self.__extract_fat_field(ptr, IR.FAT_KEY).ir_val
        live_ok = self.__check_live(lock, key)
        cond: ir.Value = self.__builder.and_(self.__builder.and_(elarith_cond, ib_cond), live_ok.ir_val)  # type: ignore
        self.__emit_check(LLValue(self.__type_ctx.bool_id, cond), "eacc")

    def check_raw_bounds(self, index: LLValue, length: int) -> None:
        """lazy-lvalue-fat(todo1)裸数组越界检查:0 ≤ index < length(编译期长度)。

        未取址裸数组元素访问无胖元数据,单 unsigned 比较即达 fat 路径
        CheckElementArith + CheckSafeAccess 的组合越界语义(索引已 coerce u64)。
        """
        cond = self.__builder.icmp_unsigned("<", index.ir_val, ir.Constant(ir.IntType(64), length))  # type: ignore
        self.__emit_check(LLValue(self.__type_ctx.bool_id, cond), "rb")

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

        del-view(todo1):is_raw 的 index 分量按类型分派——PointerType 5 字段
        (FAT_INDEX=3 为 index)保留 index==0 检查;SliceType/StrType 4 字段
        (下标 3 为 size)、RefType 3 字段均无 index 字段,分量恒真跳过,仅查
        data==lock_ptr+H。
        """
        if not self.__is_fat(ptr):
            return
        data = self.__extract_fat_field(ptr, IR.FAT_DATA).ir_val
        key = self.__extract_fat_field(ptr, IR.FAT_KEY).ir_val
        flag = self.__builder.and_(key, ir.Constant(ir.IntType(64), 0x8000_0000_0000_0000))  # type: ignore
        heap_ok = self.__builder.icmp_signed("!=", flag, ir.Constant(ir.IntType(64), 0))  # type: ignore
        lock = self.__extract_fat_field(ptr, IR.FAT_LOCK_PTR).ir_val
        lock_int = self.__builder.ptrtoint(lock, ir.IntType(64))  # type: ignore
        data_int = self.__builder.ptrtoint(data, ir.IntType(64))  # type: ignore
        expected = self.__builder.add(
            lock_int, ir.Constant(ir.IntType(64), IR.BlockHeader.BYTES)  # type: ignore
        )
        raw_data_ok = self.__builder.icmp_signed("==", data_int, expected)  # type: ignore
        raw_cond: ir.Value = raw_data_ok
        if isinstance(self.__type_ctx[ptr.type_id], Type.PointerType):
            index = self.__extract_fat_field(ptr, IR.FAT_INDEX).ir_val
            raw_index_ok = self.__builder.icmp_signed("==", index, ir.Constant(ir.IntType(64), 0))  # type: ignore
            raw_cond = self.__builder.and_(raw_data_ok, raw_index_ok)  # type: ignore
        live_ok = self.__check_live(lock, key)
        cond: ir.Value = self.__builder.and_(heap_ok, self.__builder.and_(live_ok.ir_val, raw_cond))  # type: ignore
        self.__emit_check(LLValue(self.__type_ctx.bool_id, cond), "del")

    # -- memory --

    def load(self, ptr: LLValue, result: str) -> LLValue:
        ptr_type = self.__type_ctx[ptr.type_id]
        assert isinstance(ptr_type, (Type.PointerType, Type.RefType))
        if self.__ll_type_ctx.is_zst(ptr_type.pointee_type):
            # Loading a zero-sized value yields nothing: emit no `load` and
            # bind no register (the result is never consumed).
            return self.undef(ptr_type.pointee_type)
        if self.__is_fat(ptr):
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
            assert isinstance(ptr_type, (Type.PointerType, Type.RefType))
            addr = self.__fat_addr(ptr, ptr_type.pointee_type)
            self.__builder.store(value.ir_val, addr.ir_val)  # type: ignore
        else:
            self.__builder.store(value.ir_val, ptr.ir_val)  # type: ignore

    def gep(self, base: LLValue, indices: list[int], result: str) -> LLValue:
        base_type = self.__type_ctx[base.type_id]
        if isinstance(base_type, (Type.PointerType, Type.RefType)):
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
                if idx == 0:
                    pointee_type_id = self.__type_ctx.alloc_pointer(ty.element_type)
                elif idx == 1:
                    pointee_type_id = self.__type_ctx.alloc_pointer(self.__type_ctx.u8_id)
                else:
                    pointee_type_id = self.__type_ctx.u64_id
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
            if isinstance(base_def, Type.PointerType):
                # PointerType base:锁字段继承、完全不提取;直插 data=field_addr、
                # index=0、size=1(重锚定常量,非 base 原值,规则 3.5.2)
                zero = LLValue(self.__type_ctx.u64_id, ir.Constant(ir.IntType(64), 0))  # type: ignore
                one = LLValue(self.__type_ctx.u64_id, ir.Constant(ir.IntType(64), 1))  # type: ignore
                ir_val = self.__builder.insert_value(base.ir_val, field_ptr.ir_val, IR.FAT_DATA)  # type: ignore
                ir_val = self.__builder.insert_value(ir_val, zero.ir_val, IR.FAT_INDEX)  # type: ignore
                ir_val = self.__builder.insert_value(ir_val, one.ir_val, IR.FAT_SIZE)  # type: ignore
                result_val = LLValue(result_type_id, ir_val)
            else:
                # RefType(3 字段)/SliceType(4 字段):FAT_INDEX/FAT_SIZE 越界或语义错——
                # 保留 undef 重建路径(字段数由 __build_fat 类型分派)
                lock = self.__extract_fat_field(base, IR.FAT_LOCK_PTR)
                key = self.__extract_fat_field(base, IR.FAT_KEY)
                zero = LLValue(self.__type_ctx.u64_id, ir.Constant(ir.IntType(64), 0))  # type: ignore
                one = LLValue(self.__type_ctx.u64_id, ir.Constant(ir.IntType(64), 1))  # type: ignore
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
        # 常量 0 偏移短路:index' = index + 0 ≡ index(裸指针 gep [0] ≡ 自身)——免
        # extract+add+insert / gep,结果与 base 逐位相同,直接复用。
        if isinstance(offset.ir_val, ir.Constant) and offset.ir_val.constant == 0:  # type: ignore
            self.__func.set_reg(result, base)
            return base
        if self.__is_fat(base):
            # 规则 3.3.1-3.3.2:算术仅更新 index' = index + n(检查已保证良构)。
            # 单字段直插保留 data/lock/key/size 原值,无需 5 提取 + undef 重建。
            index = self.__extract_fat_field(base, IR.FAT_INDEX)
            new_index = LLValue(self.__type_ctx.u64_id,
                                self.__builder.add(index.ir_val, offset.ir_val))  # type: ignore
            result_val = self.insert_value(base, new_index, IR.FAT_INDEX)
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

    def cast(self, value: LLValue, to_type: int, result: str, raw: bool = False) -> LLValue:
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
        elif isinstance(src, Type.PointerType) and isinstance(dst, Type.PointerType):
            if self.__ll_type_ctx.is_zst(dst.pointee_type):
                # both src and dst are ptr-to-ZST — no real cast, just undef
                ir_val = ir.Constant(dest_ll_type, ir.Undefined)  # type: ignore
            elif raw:
                # lazy-lvalue-fat(todo1)裸强转:位转换到裸目标指针。normal 模式下
                # *T 的 LLVM 型是 5 字段聚合,须手动取 pointee 的裸指针型
                # (裸数组退化 T[N]*→T* 的纯地址重贴)。
                pointee_ll = self.__ll_type_ctx.get_ll_type(dst.pointee_type).ir_type
                ir_val = self.__builder.bitcast(value.ir_val, pointee_ll.as_pointer())  # type: ignore
            elif self.__is_fat(value):
                # 指针→指针:5 字段结构重贴;数组退化 T[m]*→T* 时重锚定 + size=m
                if isinstance(self.__type_ctx[src.pointee_type], Type.ArrayType):
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
            elif (
                not self.__raw_pointers
                and not raw
                and isinstance(self.__type_ctx[src.pointee_type], Type.ArrayType)
            ):
                # 裸指针源(如 rvalue 数组临时量的 Alloca 结果):T[m]* → T* 退化同样
                # 合成胖值(裸指针 data 即基址 = 首元素地址,锁用字面量锁槽,同 __promote_fat)。
                # lazy-lvalue-fat(todo1):raw 强转(未取址裸数组退化)位转换,不合成胖值。
                arr_ty = self.__type_ctx[src.pointee_type]
                assert isinstance(arr_ty, Type.ArrayType)
                if arr_ty.element_type == dst.pointee_type:
                    lock_ir, key_ir = self.__lit_lock_pair()
                    data = LLValue(self.__type_ctx.alloc_pointer(self.__type_ctx.u8_id),
                                   self.__builder.bitcast(value.ir_val, ir.PointerType(ir.IntType(8))))  # type: ignore
                    lock = LLValue(self.__type_ctx.alloc_pointer(self.__type_ctx.u8_id), lock_ir)  # type: ignore
                    key = LLValue(self.__type_ctx.u64_id, key_ir)  # type: ignore
                    zero = LLValue(self.__type_ctx.u64_id, ir.Constant(ir.IntType(64), 0))  # type: ignore
                    len_ty = self.__type_ctx[arr_ty.length]
                    assert isinstance(len_ty, Type.LiteralValueType)
                    size = LLValue(self.__type_ctx.u64_id, ir.Constant(ir.IntType(64), len_ty.value))  # type: ignore
                    ir_val = self.__build_fat(data, lock, key, zero, size, to_type).ir_val
                else:
                    ir_val = self.__builder.bitcast(value.ir_val, dest_ll_type)  # type: ignore
            else:
                ir_val = self.__builder.bitcast(value.ir_val, dest_ll_type)  # type: ignore
        elif isinstance(src, Type.RefType) and isinstance(dst, Type.RefType):
            # T& → T&: 同为 3 字段布局(t2),identity 重贴。
            ir_val = value.ir_val
        elif isinstance(src, Type.PointerType) and isinstance(dst, Type.RefType):
            # T* → T&: 有效地址折入 index·|T|(ref 无 index 字段),取 ⟨data', lock, key⟩。
            if self.__raw_pointers:
                # raw 模式:裸指针。数组退化 T[N]*→T& 时位转换为元素指针
                # (数组基址 = 首元素地址,与 fat 分支 __fat_addr 重锚定等价);
                # 标量/同型引用类型一致,恒 identity。
                src_pointee = self.__type_ctx[src.pointee_type]
                if isinstance(src_pointee, Type.ArrayType) and src_pointee.element_type == dst.pointee_type:
                    ir_val = self.__builder.bitcast(value.ir_val, dest_ll_type)  # type: ignore
                else:
                    ir_val = value.ir_val
            elif self.__is_fat(value):
                eff = self.__fat_addr(value, src.pointee_type)
                data = LLValue(self.__type_ctx.alloc_pointer(self.__type_ctx.u8_id),
                               self.__builder.bitcast(eff.ir_val, ir.PointerType(ir.IntType(8))))  # type: ignore
                lock = self.__extract_fat_field(value, IR.FAT_LOCK_PTR)
                key = self.__extract_fat_field(value, IR.FAT_KEY)
                zero = LLValue(self.__type_ctx.u64_id, ir.Constant(ir.IntType(64), 0))  # type: ignore
                ir_val = self.__build_fat(data, lock, key, zero, zero, to_type).ir_val
            else:
                ir_val = value.ir_val
        elif isinstance(src, Type.RefType) and isinstance(dst, Type.PointerType):
            # T& → T*: 3 字段补 index=0;数组退化 T[N]&→T* 时重锚定 size=N,
            # 否则 size=1(引用恒指向单个元素)。
            if self.__raw_pointers:
                # raw 模式:裸指针。数组退化 T[N]&→T* 时位转换为元素指针
                # (数组基址 = 首元素地址,与 fat 分支 data 重锚定等价);
                # 标量/同型引用类型一致,恒 identity。
                src_pointee = self.__type_ctx[src.pointee_type]
                if isinstance(src_pointee, Type.ArrayType) and src_pointee.element_type == dst.pointee_type:
                    ir_val = self.__builder.bitcast(value.ir_val, dest_ll_type)  # type: ignore
                else:
                    ir_val = value.ir_val
            elif self.__is_fat(value):
                data = self.__extract_fat_field(value, IR.FAT_DATA)
                lock = self.__extract_fat_field(value, IR.FAT_LOCK_PTR)
                key = self.__extract_fat_field(value, IR.FAT_KEY)
                zero = LLValue(self.__type_ctx.u64_id, ir.Constant(ir.IntType(64), 0))  # type: ignore
                src_pointee = self.__type_ctx[src.pointee_type]
                if isinstance(src_pointee, Type.ArrayType) and src_pointee.element_type == dst.pointee_type:
                    len_ty = self.__type_ctx[src_pointee.length]
                    assert isinstance(len_ty, Type.LiteralValueType)
                    size = LLValue(self.__type_ctx.u64_id, ir.Constant(ir.IntType(64), len_ty.value))  # type: ignore
                else:
                    size = LLValue(self.__type_ctx.u64_id, ir.Constant(ir.IntType(64), 1))  # type: ignore
                ir_val = self.__build_fat(data, lock, key, zero, size, to_type).ir_val
            else:
                ir_val = value.ir_val
        elif isinstance(src, Type.PointerType) and isinstance(dst, Type.SliceType):
            # T* → T[]: 4 字段 {data_eff, lock, key, remaining};index 折叠进
            # data 有效地址,切片长度缩为 size-index,锁字段继承。
            if self.__raw_pointers:
                # raw 模式 slice 2 字段 {data, size}:size 无源,置 0。
                data = self.__fat_addr(value, src.pointee_type)
                slice_val = self.undef(to_type)
                slice_val = self.insert_value(slice_val, data, 0)
                slice_val = self.insert_value(slice_val, self.i64(0), 1)
                ir_val = slice_val.ir_val
            else:
                data = self.__fat_addr(value, src.pointee_type)
                lock = self.__extract_fat_field(value, IR.FAT_LOCK_PTR)
                key = self.__extract_fat_field(value, IR.FAT_KEY)
                size = self.__extract_fat_field(value, IR.FAT_SIZE)
                index = self.__extract_fat_field(value, IR.FAT_INDEX)
                remaining = LLValue(
                    self.__type_ctx.u64_id,
                    self.__builder.sub(size.ir_val, index.ir_val),  # type: ignore
                )
                zero = LLValue(self.__type_ctx.u64_id, ir.Constant(ir.IntType(64), 0))  # type: ignore
                ir_val = self.__build_fat(data, lock, key, zero, remaining, to_type).ir_val
        elif isinstance(src, Type.SliceType) and isinstance(dst, Type.RefType):
            # T[] → T&: 取 4 字段切片 data/lock_ptr/key 合成 3 字段引用(真锁,非 lit lock)。
            if self.__raw_pointers:
                # raw 模式 slice {data, size}:引用 = data 地址(字段 0)。
                ir_val = self.__builder.extract_value(value.ir_val, 0)  # type: ignore
            else:
                data = LLValue(self.__type_ctx.alloc_pointer(self.__type_ctx.u8_id),
                               self.__builder.bitcast(self.__builder.extract_value(value.ir_val, IR.FAT_DATA), ir.PointerType(ir.IntType(8))))  # type: ignore
                lock = LLValue(self.__type_ctx.alloc_pointer(self.__type_ctx.u8_id),
                               self.__builder.extract_value(value.ir_val, IR.FAT_LOCK_PTR))  # type: ignore
                key = LLValue(self.__type_ctx.u64_id,
                              self.__builder.extract_value(value.ir_val, IR.FAT_KEY))  # type: ignore
                zero = LLValue(self.__type_ctx.u64_id, ir.Constant(ir.IntType(64), 0))  # type: ignore
                ir_val = self.__build_fat(data, lock, key, zero, zero, to_type).ir_val
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
        elif isinstance(base_type, Type.RefType):
            # 3 字段引用 {data, lock_ptr, key}:data/lock 为 u8*,key 为 u64
            field_type = self.__type_ctx.alloc_pointer(self.__type_ctx.u8_id) if index in (0, 1) else self.__type_ctx.u64_id
        elif isinstance(base_type, Type.SliceType):
            # slice layout (4 字段):{data: T*, lock_ptr: i8*, key: u64, size: u64};
            # raw 模式 2 字段 {T*, u64}。字段 0 = data 裸地址。
            if index == 0:
                field_type = self.__type_ctx.alloc_pointer(base_type.element_type)
            elif index == 1:
                field_type = self.__type_ctx.alloc_pointer(self.__type_ctx.u8_id)
            elif index in (2, 3):
                field_type = self.__type_ctx.u64_id
            else:
                raise ValueError(f"slice has no field {index}")
        elif isinstance(base_type, Type.StrType):
            # str layout (4 字段):{data: u8*, lock_ptr: i8*, key: u64, size: u64}
            if index == 0:
                field_type = self.__type_ctx.alloc_pointer(self.__type_ctx.u8_id)
            elif index == 1:
                field_type = self.__type_ctx.alloc_pointer(self.__type_ctx.u8_id)
            elif index in (2, 3):
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
            if self.__raw_pointers:
                # raw 模式:slice/str 字段 0 即裸 8B 指针,直接提取;不合成 5 字段胖值
                # (否则拿 8B 做 __build_fat 于 i8* insert_value → TypeError)
                result_val = LLValue(field_type, self.__builder.extract_value(base.ir_val, 0))  # type: ignore
                self.__func.set_reg(result, result_val)
                return result_val
            # T[]→T* 派生的锁继承:slice/str 4 字段的 data 提升为胖指针(真锁继承)。
            result_val = self.__slice_ptr_fat(base, field_type)
            self.__func.set_reg(result, result_val)
            return result_val
        if isinstance(base_type, (Type.SliceType, Type.StrType)) and index == 3 and self.__raw_pointers:
            # raw 模式 2 字段 {data, size}:语义下标 3(size)映射到 LLVM 字段 1。
            result_val = LLValue(field_type, self.__builder.extract_value(base.ir_val, 1))  # type: ignore
            self.__func.set_reg(result, result_val)
            return result_val
        ir_val = self.__builder.extract_value(base.ir_val, index)  # type: ignore
        result_val = LLValue(field_type, ir_val)
        self.__func.set_reg(result, result_val)
        return result_val

    def __slice_ptr_fat(self, base: LLValue, ptr_type_id: int) -> LLValue:
        """把 4 字段 slice/str 的 data 合成为 5 字段胖指针 ⟨data, lock_ptr, key, 0, size⟩。

        t2 三结构:slice/str 自身携带真实 lock_ptr/key(size 下标 3),直接继承
        (锁继承)——这是 T[]→T* 派生的锁继承来源。
        """
        data = LLValue(self.__type_ctx.alloc_pointer(self.__type_ctx.u8_id),
                       self.__builder.bitcast(self.__builder.extract_value(base.ir_val, IR.FAT_DATA), ir.PointerType(ir.IntType(8))))  # type: ignore
        lock = LLValue(self.__type_ctx.alloc_pointer(self.__type_ctx.u8_id),
                       self.__builder.extract_value(base.ir_val, IR.FAT_LOCK_PTR))  # type: ignore
        key = LLValue(self.__type_ctx.u64_id,
                      self.__builder.extract_value(base.ir_val, IR.FAT_KEY))  # type: ignore
        size = LLValue(self.__type_ctx.u64_id,
                       self.__builder.extract_value(base.ir_val, IR.SLICE_SIZE))  # type: ignore
        zero = LLValue(self.__type_ctx.u64_id, ir.Constant(ir.IntType(64), 0))  # type: ignore
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
            # t2 三结构:slice/str 4 字段 {data, lock_ptr, key, size}(raw 2 字段)。
            # HIR 供给 {ptr, len}:正常模式 data = ptr 有效地址,lock/key 继承自
            # ptr 的胖元数据(T* coerce 构造,锁继承),size = 显式 len。
            elem_type = type_def.element_type if isinstance(type_def, Type.SliceType) else self.__type_ctx.u8_id
            field_ptr_ll = self.__ll_type_ctx.get_ll_type(elem_type).ir_type.as_pointer()  # type: ignore
            raw = self.__fat_addr(field_values[0], elem_type)
            raw_ir = self.__builder.bitcast(raw.ir_val, field_ptr_ll)  # type: ignore
            if self.__raw_pointers:
                val = self.undef(type_id)
                val = self.insert_value(val, LLValue(self.__type_ctx.alloc_pointer(elem_type), raw_ir), 0)  # type: ignore
                val = self.insert_value(val, field_values[1], 1)
                return val
            data = LLValue(self.__type_ctx.alloc_pointer(elem_type), raw_ir)  # type: ignore
            lock = self.__extract_fat_field(field_values[0], IR.FAT_LOCK_PTR)
            key = self.__extract_fat_field(field_values[0], IR.FAT_KEY)
            return self.__build_fat(data, lock, key, self.i64(0), field_values[1], type_id)
        # 全常量聚合 → 单一定值(ir.Constant),免 undef + N×insertvalue 链。
        # 条件:① 无 ZST 字段(其 {} 槽位常量需逐槽构造,保持原路径);② 无
        # {T*,u64} 形态合成(合成点是运行时位型重构,恒非常量)。
        if (not any(self.__ll_type_ctx.is_zst(fv.type_id) for fv in field_values)
                and not any(self.__is_fat_type(fv.type_id) and not self.__is_fat(fv) for fv in field_values)
                and all(isinstance(fv.ir_val, ir.Constant) for fv in field_values)):  # type: ignore
            ll_type = self.__ll_type_ctx.get_ll_type(type_id).ir_type
            return LLValue(type_id, ir.Constant(ll_type, [fv.ir_val for fv in field_values]))  # type: ignore
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

        niche enum(省略 tag,布局 = payload 类型):unit 变体 → 全零聚合;
        payload 变体 → payload 直值(无 tag store)。
        """
        if self.__ll_type_ctx.is_niche_enum(enum_type_id):
            if payload_type is None:
                # unit 变体(None)→ 全零编码:指针字段 null、整数字段 0
                ll_type = self.__ll_type_ctx.get_ll_type(enum_type_id).ir_type
                ir_val = self.__zero_const(ll_type)
            else:
                # payload 变体(Some)→ payload 直值(匿名单字段 struct 仅 1 字段)
                assert payload_fields is not None and len(payload_fields) == 1
                ir_val = self.__promote_fat(payload_fields[0]).ir_val
            self.__func.set_reg(result, LLValue(enum_type_id, ir_val))
            return

        if payload_type is None or self.__type_ctx.is_zst(payload_type):
            # unit 变体(无 payload / ZST payload):纯 insertvalue 构造——免
            # alloca+gep+store+load;payload 槽位保持 undef(与 alloca 未初始化一致)。
            ir_val = self.undef(enum_type_id).ir_val
            ir_val = self.__builder.insert_value(ir_val, self.i32(discriminant).ir_val, 0)  # type: ignore
            self.__func.set_reg(result, LLValue(enum_type_id, ir_val))
            return

        # payload 变体:enum 布局 {i32, [pad x i8]} 的 payload 槽是字节数组——结构体
        # 值无法 insert_value(型别不匹配),须经 alloca+bitcast+store+load(表示级
        # 重构范围外,保持既有路径)。
        tmp_ptr = self.alloca(enum_type_id).ir_val

        # Store discriminant at field 0
        disc_ptr = self.__builder.gep(tmp_ptr, [self.i32(0).ir_val, self.i32(0).ir_val], inbounds=True)  # type: ignore
        self.__builder.store(self.i32(discriminant).ir_val, disc_ptr)  # type: ignore

        if not self.__type_ctx.is_zst(payload_type):
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

        if self.__ll_type_ctx.is_niche_enum(enum_type_id):
            # niche enum:payload 即整个值(匿名单字段 struct 字段 0 = 值本身)
            for field_index, symbol_id in fields:
                if field_index >= len(payload_fields):
                    break
                field_value = self.__builder.load(base_ptr.ir_val)  # type: ignore
                alloca_ptr = self.__func.get_var_ptr(symbol_id)
                self.__builder.store(field_value, alloca_ptr.ir_val)  # type: ignore
            return

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
        enum_type_id = matched_ty.pointee_type if isinstance(matched_ty, (Type.PointerType, Type.RefType)) else matched.type_id
        base_ptr = self.__fat_addr(matched, enum_type_id) if self.__is_fat(matched) else matched

        if self.__ll_type_ctx.is_niche_enum(enum_type_id):
            # niche enum:字段地址 = 值地址(base_ptr 即 payload 起始)
            for field_index, symbol_id in fields:
                if field_index >= len(payload_fields):
                    break
                alloca_ptr = self.__func.get_var_ptr(symbol_id)
                field_ll = LLValue(self.__type_ctx.alloc_pointer(payload_fields[field_index].type_id), base_ptr.ir_val)  # type: ignore
                self.__builder.store(self.__promote_fat(field_ll).ir_val, alloca_ptr.ir_val)  # type: ignore
            return

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
            fd, self.__extract_value_raw(buf, 0), self.__slice_len_field(buf),
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
        buf_len = self.__slice_len_field(buf)
        bytes_read = self.__call_intrinsic(IntrinsicKind.Read, [
            fd, buf_ptr, buf_len,
        ])
        # construct str:4 字段 {data, lock_ptr, key, size}(raw 2 字段 {data, size});
        # 正常模式 lock/key 继承自输入缓冲区 buf(t2 锁继承)。
        str_ll_type = self.__ll_type_ctx.get_ll_type(self.__type_ctx.str_id).ir_type
        undef = ir.Constant(str_ll_type, ir.Undefined)  # type: ignore
        ir_val = self.__builder.insert_value(undef, buf_ptr.ir_val, 0)  # type: ignore
        if self.__raw_pointers:
            ir_val = self.__builder.insert_value(ir_val, bytes_read.ir_val, 1)  # type: ignore
        else:
            ir_val = self.__builder.insert_value(ir_val, self.__extract_value_raw(buf, 1).ir_val, 1)  # type: ignore
            ir_val = self.__builder.insert_value(ir_val, self.__extract_value_raw(buf, 2).ir_val, 2)  # type: ignore
            ir_val = self.__builder.insert_value(ir_val, bytes_read.ir_val, 3)  # type: ignore
        self.__func.set_reg(result, LLValue(self.__type_ctx.str_id, ir_val))  # type: ignore

    def open(self, path: LLValue, flags: LLValue, result: str) -> None:
        # path is a str — field 0 is the data pointer (4 字段布局同样适用)
        # mode is hardcoded to 0o644 = 420 (rw-r--r--)
        mode = self.i32(420)
        raw = self.__call_intrinsic(IntrinsicKind.Open, [
            self.__extract_value_raw(path, 0), flags, mode,
        ])
        self.__func.set_reg(result, raw)

    def close(self, fd: LLValue, result: str) -> None:
        raw = self.__call_intrinsic(IntrinsicKind.Close, [fd])
        self.__func.set_reg(result, raw)

    # ------------------------------------------------------------------
    # terminators
    # ------------------------------------------------------------------

    def set_frame_lock(self, e_f: IR.Value) -> None:
        """标记函数已取得帧锁:全部返回路径写 SENTINEL 并 pop。"""
        assert isinstance(e_f, IR.Reg), "帧锁槽地址 e_f 必须为入口寄存器"
        self.__frame_lock_slot_name = e_f.name

    def __release_frame_lock(self) -> None:
        """规则 3.7.2:帧退出写 SENTINEL,再从稳定影子栈 pop。

        槽位保持映射且永不成为用户数据;后续 push 复用该地址时会在
        任何用户步之前写入新键。编译器生成的函数进退严格 LIFO,
        因此退出仅需递减深度,无需空闲链或动态槽位检索。
        """
        if self.__frame_lock_slot_name is None:
            return
        slot = self.__func.reg(self.__frame_lock_slot_name)
        self.write_lock_slot(slot, self.i64(IR.SENTINEL))
        depth_ptr = self.__module.get_frame_lock_depth()
        depth = self.__builder.load(depth_ptr, name="frame.depth.exit")  # type: ignore
        previous = self.__builder.sub(  # type: ignore
            depth, ir.Constant(ir.IntType(64), 1), name="frame.depth.prev"  # type: ignore
        )
        self.__builder.store(previous, depth_ptr)  # type: ignore

    def ret(self, value: LLValue | None) -> None:
        self.__release_frame_lock()
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

    def niche_branch(self, value: LLValue, zero_label: str, nonzero_label: str) -> None:
        """niche enum match:全零检测(逐字段 icmp+and)后分支。

        ``value`` 是 niche enum 的值(LLVM 形态即 payload);全零 → zero_label
        (unit 变体 arm),非全零 → nonzero_label(payload 变体 arm)。空标签
        落到 unreachable 块(穷举 arm 时未覆盖的一侧不会执行)。
        """
        zero_cond = self.__is_all_zero(value)
        zero_block = self.__func.block(zero_label) if zero_label else None
        nonzero_block = self.__func.block(nonzero_label) if nonzero_label else None
        if zero_block is None:
            zero_block = self.__func.new_block("match.niche.zero")
            ir.IRBuilder(zero_block).unreachable()
        if nonzero_block is None:
            nonzero_block = self.__func.new_block("match.niche.nonzero")
            ir.IRBuilder(nonzero_block).unreachable()
        self.__builder.cbranch(zero_cond.ir_val, zero_block, nonzero_block)  # type: ignore

    def unreachable(self) -> None:
        self.__builder.unreachable()

    def panic(self, msg: LLValue) -> None:
        self.__call_intrinsic(IntrinsicKind.Write, [
            self.i32(2), self.__extract_value_raw(msg, 0), self.__slice_len_field(msg),
        ])
        self.__call_intrinsic(IntrinsicKind.Exit, [self.i32(1)])
        self.__builder.unreachable()

    # ------------------------------------------------------------------
    # internal
    # ------------------------------------------------------------------

    def __slice_len_field(self, base: LLValue) -> LLValue:
        """slice/str 的 size 字段:4 字段布局下标 3;raw 模式 2 字段布局下标 1。"""
        index = IR.SLICE_SIZE if not self.__raw_pointers else 1
        return self.__extract_value_raw(base, index)

    def __extract_value_raw(self, base: LLValue, index: int) -> LLValue:
        ir_val = self.__builder.extract_value(base.ir_val, index)  # type: ignore
        return LLValue(base.type_id, ir_val)

    def __zero_const(self, ll_type: ir.Type) -> ir.Constant:
        """全零常量(niche None 编码):指针 null、整数 0、结构逐字段归零。"""
        if isinstance(ll_type, ir.LiteralStructType):
            return ir.Constant.literal_struct([self.__zero_const(f) for f in ll_type.elements])  # type: ignore
        if isinstance(ll_type, ir.PointerType):
            return ir.Constant(ll_type, None)  # type: ignore
        if isinstance(ll_type, ir.IntType):
            return ir.Constant(ll_type, 0)  # type: ignore
        if isinstance(ll_type, ir.types._BaseFloatType):  # type: ignore
            return ir.Constant(ll_type, 0.0)  # type: ignore
        raise ValueError(f"cannot build zero constant for {ll_type}")

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

    def __is_all_zero(self, value: LLValue) -> LLValue:
        """niche 全零检测:LLVM 无聚合 icmp → 逐字段 icmp + and 归约。"""
        ll_type = value.ir_val.type  # type: ignore
        if isinstance(ll_type, ir.LiteralStructType):  # type: ignore
            acc: ir.Value | None = None
            for index in range(len(ll_type.elements)):  # type: ignore
                field = self.__builder.extract_value(value.ir_val, index)  # type: ignore
                is_zero = self.__is_field_zero(field)
                acc = is_zero if acc is None else self.__builder.and_(acc, is_zero)  # type: ignore
            assert acc is not None
            return LLValue(self.__type_ctx.bool_id, acc)
        return LLValue(self.__type_ctx.bool_id, self.__is_field_zero(value.ir_val))

    def __is_field_zero(self, val: ir.Value) -> ir.Value:
        if isinstance(val.type, ir.PointerType):  # type: ignore
            return self.__builder.icmp_signed("==", val, ir.Constant(val.type, None))  # type: ignore
        if isinstance(val.type, ir.IntType):  # type: ignore
            return self.__builder.icmp_signed("==", val, ir.Constant(val.type, 0))  # type: ignore
        if isinstance(val.type, ir.types._BaseFloatType):  # type: ignore
            return self.__builder.fcmp_ordered("==", val, ir.Constant(val.type, 0.0))  # type: ignore
        raise ValueError(f"unsupported zero-check field type: {val.type}")  # type: ignore

    def __cmp_impl(self, op: BinaryOperator, lhs: ir.Value, rhs: ir.Value, type_id: int) -> ir.Value:
        if self.__type_ctx.is_zst(type_id):
            # All ZST values are indistinguishable — EQ is always true, NE always false
            is_eq = op in (BinaryOperator.Eq,)
            return ir.Constant(ir.IntType(1), 1 if is_eq else 0)  # type: ignore
        if isinstance(lhs.type, ir.LiteralStructType) or isinstance(rhs.type, ir.LiteralStructType):  # type: ignore
            # 胖指针聚合比较兜底(规则 3.4.1-3.4.2,§7.6 风险 3):LLVM 无聚合
            # icmp → 字段比较。正常路径由 CFG 路由至 PtrCmp;此分支兜底 CFG
            # 未路由的聚合操作数。
            return self.__cmp_fat_values(op, lhs, rhs)
        predicate = {
            BinaryOperator.Eq: "==", BinaryOperator.Neq: "!=",
            BinaryOperator.Lt: "<", BinaryOperator.Gt: ">",
            BinaryOperator.Leq: "<=", BinaryOperator.Geq: ">=",
        }[op]
        if isinstance(lhs.type, (ir.IntType, ir.PointerType)):  # type: ignore
            if isinstance(lhs.type, ir.PointerType) and lhs.type != rhs.type:  # type: ignore
                # raw 模式:有型指针比较时不同 pointee 的 T* 不能直接 icmp → 统一 bitcast i8*
                i8_ptr = ir.PointerType(ir.IntType(8))  # type: ignore
                lhs = self.__builder.bitcast(lhs, i8_ptr)  # type: ignore
                rhs = self.__builder.bitcast(rhs, i8_ptr)  # type: ignore
            ty = self.__type_ctx[type_id]
            if isinstance(ty, Type.IntType) and not ty.signed:
                return self.__builder.icmp_unsigned(predicate, lhs, rhs)  # type: ignore
            return self.__builder.icmp_signed(predicate, lhs, rhs)  # type: ignore
        return self.__builder.fcmp_ordered(predicate, lhs, rhs)  # type: ignore

    def __fat_value_pair(self, v: ir.Value) -> tuple[ir.Value, ir.Value]:
        """ir.Value 级别的 (data, second) 字段对(t2 三结构分派)。

        第二字段按结构:5 字段指针取 index、4 字段 slice 取 size、3 字段 ref 取 key、
        raw 2 字段 slice 取 size;裸指针按 (自身, 0)。
        """
        if isinstance(v.type, ir.LiteralStructType):  # type: ignore
            field_count = len(v.type.elements)  # type: ignore
            if field_count >= 4:
                second_idx = IR.FAT_INDEX
            elif field_count == 3:
                second_idx = IR.REF_KEY
            else:
                second_idx = 1
            return (self.__builder.extract_value(v, IR.FAT_DATA),  # type: ignore
                    self.__builder.extract_value(v, second_idx))  # type: ignore
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
