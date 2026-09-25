# pyright: reportUnknownMemberType=false
"""LLVM fat-pointer metadata, allocation, lifetime, and access safety."""

from __future__ import annotations

from typing import cast

from llvmlite import ir

from compiler.analysis.ty import ty as Type
from compiler.analysis.ty.context import TypeCtx
from compiler.codegen.abi import lockmech as ABI
from compiler.codegen.cfg import ir as IR
from compiler.codegen.llvm.function.core import FunctionCore
from compiler.codegen.llvm.base.module import LLFunction, LLModule
from compiler.codegen.llvm.pointer.representation import PointerRepresentation
from compiler.codegen.llvm.base.types import LLTypeCtx
from compiler.codegen.llvm.base.value import LLValue
from compiler.frontend.parse.operator import BinaryOperator
from compiler.runtime_error import RuntimeErrorCode


class FatSafety:
    """Emit fat-pointer metadata, lifetime, allocation, and access operations."""

    def __init__(self, core: FunctionCore, pointers: PointerRepresentation) -> None:
        self.__core = core
        self.__pointers = pointers
        self.__frame_lock_slot_name: str | None = None
        self.__frame_lock_acquired = False

    @property
    def __func(self) -> LLFunction:
        return self.__core.context.func

    @property
    def __module(self) -> LLModule:
        return self.__core.context.module

    @property
    def __type_ctx(self) -> TypeCtx:
        return self.__core.context.type_ctx

    @property
    def __ll_type_ctx(self) -> LLTypeCtx:
        return self.__core.context.ll_type_ctx

    @property
    def __raw_pointers(self) -> bool:
        return self.__core.context.raw_pointers

    @property
    def __builder(self) -> ir.IRBuilder:
        return self.__core.flow.builder

    # -- Pointer metadata --

    def is_fat(self, ll_val: LLValue) -> bool:
        """判定 LLVM 值是否已是胖结构聚合(PointerType 24B / SliceType 24B / RefType 16B)。

        分级指针表示统一:三个指针族类型的 LLVM 值形态为 LiteralStructType 即视为胖值;
        raw 模式无元数据(裸 T* / {T*, u64} / 裸 T*),恒 False。从 slice/str 提取的
        裸 8B 指针(type_id 为 PointerType)按 LLVM 值形态(指针而非结构)不误判。
        """
        return self.__pointers.is_fat(ll_val)

    def is_fat_type(self, type_id: int) -> bool:
        """类型层胖指针判定(分级指针 按 type_id 分派):PointerType(pointee 非 ZST)
        4 字段 / SliceType·StrType 3 字段 / RefType 2 字段,均为胖结构值;
        raw 模式恒 False,指针一律按裸 8B 处理。与 CFG 层 is_fat_pointer
        对应(后者专指 PointerType 的全检查路径)。
        """
        return self.__pointers.is_fat_type(type_id)

    def extract_fat_field(self, ll_val: LLValue, index: int) -> LLValue:
        """提取胖指针聚合字段;整数字段一律零扩展到 u64、指针字段 u8*(下标见 FAT_*/SLICE_*/REF_*)。

        PointerType 的 index/size 是 32 位元素数, 值层统一按 u64 处理(长度来自已被
        校验 ≤ MAX_VIEW_COUNT 的分配容量, 提取端零扩展; 比较/算术语义与 u64 版一致);
        word 是完整的 64 位整字(锁址 + 键)。
        """
        return self.__pointers.extract_fat_field(ll_val, index)

    def insert_field_value(self, agg: LLValue, value: LLValue, index: int) -> LLValue:
        """按目标字段实际位宽插入聚合字段(收窄到 32 位视图字段时截断)。

        收窄只发生在 index/size 上: 构造写入的长度来自受 Malloc 上限约束的分配容量,
        算术写入由 CheckElementArith 保证 ≤ size ≤ MAX_VIEW_COUNT, 因此截断不改变语义。
        """
        return self.__pointers.insert_field_value(agg, value, index)

    def narrow_view_value(self, ir_val: ir.Value, agg: ir.Value, index: int) -> ir.Value:
        """把常量视图值收窄到目标聚合字段位宽(供 undef 重建路径直插用)。"""
        return self.__pointers.narrow_view_value(ir_val, agg, index)

    @staticmethod
    def check_static_view_count(count: int) -> None:
        """编译期数组退化的长度上限: 超过 32 位视图上限的类型无法表示, 直接报错。"""
        PointerRepresentation.check_static_view_count(count)

    def check_view_count(self, value: LLValue, suffix: str) -> None:
        """分配元素数上限: 必须 ≤ MAX_VIEW_COUNT(否则 32 位长度字段会截断)。

        只在堆分配处发射一次: 视图长度都来自某个分配的容量, 分配受这条上限约束后,
        由视图派生出的长度(子切片、T*→T[] 的剩余长度、视图转换)必然可表示。
        """
        limit: ir.Value = ir.Constant(ir.IntType(64), ABI.MAX_VIEW_COUNT)  # type: ignore
        ok = self.__builder.icmp_unsigned("<=", value.ir_val, limit)  # type: ignore
        self.__core.flow.emit_check(LLValue(self.__type_ctx.bool_id, ok), RuntimeErrorCode.R001, suffix)

    def build_fat(self, data: LLValue, word: LLValue, index: LLValue, size: LLValue, type_id: int) -> LLValue:
        """按结构构造胖值:PointerType 4 字段 ⟨data,word,index,size⟩ /
        SliceType·StrType 3 字段 ⟨data,word,size⟩ / RefType 2 字段 ⟨data,word⟩。

        word 同时携带锁址与键(见 lockmech), 槽里存同一份。
        """
        return self.__pointers.build_fat(data, word, index, size, type_id)

    def lock_of(self, builder: ir.IRBuilder, word: ir.Value, data: ir.Value) -> ir.Value:
        """从 word 的锁表下标取得表项地址, 与 data 和槽种类无关。

        lock 字段就是锁表下标, 地址 = @__secl_lock_table + lock*LOCK_ENTRY_BYTES;
        下标区间自带 kind, 热路径没有分派。data 参数保留是为了与调用点同构。
        """
        i32: ir.IntType = ir.IntType(32)  # type: ignore
        i64: ir.IntType = ir.IntType(64)  # type: ignore
        table = self.__module.get_lock_table()
        index = builder.zext(builder.trunc(word, i32), i64)  # type: ignore
        return builder.gep(table, [ir.Constant(i64, 0), index], inbounds=True)  # type: ignore

    def literal_word(self) -> ir.Value:
        """字面量指针携带的 word(锁表字面量槽 key = 0, 恒 live)。"""
        return self.__pointers.literal_word()

    def env_word(self) -> ir.Value:
        """环境指针携带的 word(锁表环境槽 key = 0, 恒 live)。"""
        return self.__pointers.env_word()

    def fat_data(self, ll_val: LLValue) -> LLValue:
        """取胖指针的 data 字段(裸 8B 地址);已是裸指针则直接返回。

        slice/str 构造边界:从 slice 提取的裸 8B 指针(未合成 fat)与 malloc/VarPtr
        合成的 fat 在此统一取数据地址。
        """
        return self.__pointers.fat_data(ll_val)

    def promote_fat(self, ll_val: LLValue) -> LLValue:
        """把裸 8B 指针值(如 Alloca 结果)提升为胖指针 ⟨data, word, 0, 1⟩。

        值层统一: type_id 为 PointerType 的 LLVM 值须是聚合才能跨调用/返回/存储;
        Alloca 等产生裸指针的原语在此补全元数据(字面量锁槽, 恒 live)。
        """
        return self.__pointers.promote_fat(ll_val)

    def fat_addr(self, ll_val: LLValue, pointee_type_id: int) -> LLValue:
        """地址折算:有效地址 = data + index·|T|,一次 GEP(检查与取数共用)。

        对 fat 值提取 data/index 字段,bitcast 到 T* 后按元素索引 GEP;对裸 8B
        指针直接使用(索引恒 0 语义)。T&无 index 字段,恒指单个元素——
        有效地址即 data。O-1 无回摆由检查(well-formed)保障。
        """
        return self.__pointers.fat_addr(ll_val, pointee_type_id)

    def gen_key_value(self) -> LLValue:
        """帧进入 re-key:发射不回绕的 32 位单调键体, 耗尽即失败 (R003)。

        key 与锁表项的 32 位槽严格同宽: 计数到 FRAME_KEY_LIMIT(0xFFFFFFFE,
        0xFFFFFFFF 留给帧退出的 SENTINEL)即确定性终止, 绝不回绕——回绕会让旧指针
        重新匹配。堆对象的身份不走这里(锁表项按块换代)。
        """
        counter = self.__module.get_key_counter()
        loaded = self.__builder.load(counter)  # type: ignore
        available = self.__builder.icmp_unsigned(
            "<", loaded, ir.Constant(ir.IntType(64), ABI.FRAME_KEY_LIMIT)  # type: ignore
        )
        self.__core.flow.emit_check(LLValue(self.__type_ctx.bool_id, available), RuntimeErrorCode.R003, "keyex")
        nxt = self.__builder.add(loaded, ir.Constant(ir.IntType(64), 1))  # type: ignore
        self.__builder.store(nxt, counter)  # type: ignore
        return LLValue(self.__type_ctx.u64_id, self.__builder.and_(nxt, ir.Constant(ir.IntType(64), ABI.KEY_MASK)))  # type: ignore

    # -- Fat heap-pool allocation and release --

    def allocate_fat(
        self,
        type_id: int,
        size: LLValue,
        payload: LLValue,
        class_index: int | None,
    ) -> LLValue:
        """Allocate a fat-mode pool block and attach a fresh lock-table identity."""
        ptr_type_id = self.__type_ctx.alloc_pointer(type_id)
        # 元素数与元素大小都是编译期常量时, 直接传尺寸类号: 运行时不再按字节数查表.
        # 运行时取不到类号 (请求大于最大类) 或元素数非常量时走通用入口.
        if class_index is None:
            block_ir = self.__builder.call(self.__module.get_pool_alloc(), [payload.ir_val])  # type: ignore
        else:
            block_ir = self.__builder.call(
                self.__module.get_pool_alloc_class(),
                [ir.Constant(ir.IntType(32), class_index)],  # type: ignore
            )  # type: ignore
        block_base = LLValue(ptr_type_id, block_ir)  # type: ignore
        # 块头 {extent:u32 @0, pad:u32 @4} = 8 B; 身份在锁表项里。
        # 发放全部在发射的 IR 里完成(自由链非空 → 弹出下标; 为空 → 调 bump 入口),
        # 因此表项的 key 写对 LLVM 可见: 刚分配内存上的 live 检查能折叠成真。
        i32: ir.IntType = ir.IntType(32)  # type: ignore
        i64: ir.IntType = ir.IntType(64)  # type: ignore
        anchor_lo32 = self.__builder.trunc(  # type: ignore
            self.__builder.add(  # type: ignore
                self.__builder.ptrtoint(block_base.ir_val, i64),  # type: ignore
                ir.Constant(i64, ABI.BlockHeader.BYTES),  # type: ignore
            ),
            i32,
        )
        table = self.__module.get_lock_table()
        free_head_g = self.__module.get_lock_free_head()
        head = self.__builder.load(free_head_g)  # type: ignore
        has_free = self.__builder.icmp_unsigned("!=", head, ir.Constant(i64, 0))  # type: ignore

        def take_fast(builder: ir.IRBuilder) -> ir.Value:
            popped = builder.sub(head, ir.Constant(i64, 1))  # type: ignore
            entry_ptr = builder.gep(table, [ir.Constant(i64, 0), popped], inbounds=True)  # type: ignore
            next_head = builder.lshr(builder.load(entry_ptr, typ=i64), ir.Constant(i64, 32))  # type: ignore
            builder.store(next_head, free_head_g)  # type: ignore
            return popped  # type: ignore

        def take_slow(builder: ir.IRBuilder) -> ir.Value:
            return builder.call(self.__module.get_lock_bump_take(), [])  # type: ignore

        lock_index = self.__core.flow.emit_either(has_free, take_fast, take_slow, i64)  # type: ignore
        entry = self.__builder.gep(table, [ir.Constant(i64, 0), lock_index], inbounds=True)  # type: ignore
        stored = self.__builder.load(entry, typ=i64)  # type: ignore
        next_key = self.__builder.and_(  # type: ignore
            self.__builder.add(  # type: ignore
                self.__builder.and_(stored, ir.Constant(i64, ABI.KEY_MASK)),  # type: ignore
                ir.Constant(i64, 1),  # type: ignore
            ),
            ir.Constant(i64, ABI.KEY_MASK),  # type: ignore
        )
        next_entry = self.__builder.or_(  # type: ignore
            next_key,
            self.__builder.shl(  # type: ignore
                self.__builder.zext(anchor_lo32, i64),  # type: ignore
                ir.Constant(i64, ABI.WORD_KEY_SHIFT),  # type: ignore
            ),
        )
        self.__builder.store(next_entry, entry)  # type: ignore
        word_ir = self.__builder.or_(  # type: ignore
            lock_index,
            self.__builder.shl(next_key, ir.Constant(i64, ABI.WORD_KEY_SHIFT)),  # type: ignore
        )
        extent_ptr = self.__builder.gep(
            block_base.ir_val,
            [ir.Constant(i64, ABI.BlockHeader.EXTENT_OFFSET)],  # type: ignore
            inbounds=False,
            source_etype=ir.IntType(8),  # 块头按字节偏移索引
        )
        self.__builder.store(self.__builder.trunc(size.ir_val, i32), extent_ptr)  # type: ignore

        key_ir = word_ir
        data_ir = self.__builder.gep(
            block_base.ir_val,
            [ir.Constant(ir.IntType(64), ABI.BlockHeader.BYTES)],  # type: ignore
            inbounds=False,
            source_etype=ir.IntType(8),  # 载荷区按字节偏移索引
        )
        data = LLValue(self.__type_ctx.alloc_pointer(self.__type_ctx.u8_id), data_ir)  # type: ignore
        word_val = LLValue(self.__type_ctx.u64_id, key_ir)  # type: ignore
        zero = LLValue(self.__type_ctx.u64_id, ir.Constant(ir.IntType(64), 0))  # type: ignore
        size_val = LLValue(self.__type_ctx.u64_id, size.ir_val)  # type: ignore
        return self.build_fat(data, word_val, zero, size_val, ptr_type_id)

    def release_fat(self, ptr: LLValue) -> None:
        """Release a fat pool allocation and invalidate its lock-table identity."""
        if not self.is_fat(ptr):
            raise ValueError("fat pool release requires a fat pointer")
        i32: ir.IntType = ir.IntType(32)  # type: ignore
        i64: ir.IntType = ir.IntType(64)  # type: ignore
        data = self.extract_fat_field(ptr, ABI.FAT_DATA)
        word = self.extract_fat_field(ptr, ABI.FAT_WORD).ir_val
        entry = self.lock_of(self.__builder, word, data.ir_val)
        anchor_ptr = self.__builder.gep(  # type: ignore
            entry,
            [ir.Constant(i64, ABI.LockEntry.ANCHOR_OFFSET)],  # type: ignore
            inbounds=False,
            source_etype=ir.IntType(8),
        )
        anchor = self.__builder.load(anchor_ptr, typ=i32)  # type: ignore
        data_int: ir.Value = self.__builder.ptrtoint(data.ir_val, i64)  # type: ignore
        anchor64: ir.Value = self.__builder.zext(anchor, i64)  # type: ignore
        _, block = self.__heap_block(data_int, anchor64)
        self.__builder.call(self.__module.get_lock_release(), [word])  # type: ignore
        self.__builder.call(self.__module.get_pool_release(), [block])  # type: ignore

    # -- Frame locks --

    def gen_key(self) -> LLValue:
        return self.gen_key_value()

    def acquire_frame_lock(self, key: LLValue | None) -> LLValue:
        """在固定地址的独立影子栈上 push 一个帧锁槽, 槽里写帧 word。

        帧 word = ⟨key:32 | lock:32⟩: key 来自全局单调计数器,
        lock 是影子栈槽位下标。节点结果是该 word; 槽地址记在
        __frame_lock_slot_name 里供返回路径写 SENTINEL。热路径只有一次深度检查、
        一次 GEP 与两次 store; 槽位先写 word, 再发布新深度, 且在任何用户语句之前完成。
        """
        if key is None:
            # 帧锁节点可能先于它的 GenKey 被翻译(两者都插在入口), 这里就地取键。
            key = self.gen_key_value()
        depth_ptr = self.__module.get_frame_lock_depth()
        depth = self.__builder.load(depth_ptr, name="frame.depth")  # type: ignore
        available = self.__builder.icmp_unsigned(
            "<",
            depth,
            ir.Constant(ir.IntType(64), ABI.FrameLockArena.SLOTS),  # type: ignore
        )
        self.__core.flow.emit_check(
            LLValue(self.__type_ctx.bool_id, available), RuntimeErrorCode.R003, "framecap"
        )
        arena = self.__module.get_lock_table()
        slot = self.__builder.gep(  # type: ignore
            arena,
            [ir.Constant(ir.IntType(64), 0), depth],  # type: ignore
            inbounds=True,
            name="frame.lock",
        )
        i64: ir.IntType = ir.IntType(64)  # type: ignore
        frame_key = self.__builder.and_(key.ir_val, ir.Constant(i64, ABI.KEY_MASK))  # type: ignore
        # word = ⟨key:32 | lock:32⟩, lock = 影子栈深度(锁表下标)
        frame_word: ir.Value = self.__builder.or_(  # type: ignore
            self.__builder.shl(frame_key, ir.Constant(i64, ABI.WORD_KEY_SHIFT)),  # type: ignore
            depth,
        )
        frame_key64 = frame_key
        self.__builder.store(frame_key64, slot)  # type: ignore
        next_depth = self.__builder.add(  # type: ignore
            depth, ir.Constant(ir.IntType(64), 1), name="frame.depth.next"  # type: ignore
        )
        self.__builder.store(next_depth, depth_ptr)  # type: ignore
        # 槽地址登记为函数级寄存器: 返回路径用它写 SENTINEL(节点结果是帧 word, 不是槽)。
        self.__func.set_reg(
            "frame.slot",
            LLValue(self.__type_ctx.alloc_pointer(TypeCtx.u64_id), slot),
        )
        self.__frame_lock_slot_name = "frame.slot"
        return LLValue(self.__type_ctx.u64_id, frame_word)

    def write_lock_slot(self, lock_ptr: LLValue, value: LLValue) -> None:
        """μ⟨lock_ptr⟩ := value(SENTINEL / 3.7.1 帧锁写键)。

        指针恒非空,锁槽恒可写(空容器持真实堆块)。
        """
        raw = self.fat_data(lock_ptr).ir_val
        self.__builder.store(value.ir_val, raw)  # type: ignore

    def set_frame_lock(self, e_f: IR.Value) -> None:
        """标记函数已取得帧锁:全部返回路径写 SENTINEL 并 pop。

        帧 word 由 acquire_frame_lock 记在 __frame_lock_slot_name(槽地址)里;
        这里只确认"已取得", 参数是帧 word(不是槽地址), 因此不再记录它。
        """
        assert isinstance(e_f, IR.Reg), "帧锁必须为入口寄存器"
        self.__frame_lock_acquired = True

    def release_frame_lock(self) -> None:
        """帧退出写 SENTINEL,再从稳定影子栈 pop。

        槽位保持映射且永不成为用户数据;每次 push 均在用户语句前写入键。
        编译器生成的函数进退严格 LIFO,
        因此退出仅需递减深度,无需空闲链或动态槽位检索。
        """
        if not self.__frame_lock_acquired or self.__frame_lock_slot_name is None:
            return
        slot = self.__func.reg(self.__frame_lock_slot_name)
        sentinel = LLValue(self.__type_ctx.u64_id, ir.Constant(ir.IntType(64), ABI.SENTINEL))  # type: ignore
        self.write_lock_slot(slot, sentinel)
        depth_ptr = self.__module.get_frame_lock_depth()
        depth = self.__builder.load(depth_ptr, name="frame.depth.exit")  # type: ignore
        previous = self.__builder.sub(  # type: ignore
            depth, ir.Constant(ir.IntType(64), 1), name="frame.depth.prev"  # type: ignore
        )
        self.__builder.store(previous, depth_ptr)  # type: ignore

    # -- Fat-pointer derivation and comparison --

    def fat_addr_i128(self, ll_val: LLValue, pointee_type_id: int) -> ir.Value:
        """有效地址(data + index·|T|)以 i128 计算——宽整数天然无回摆(O-1)。"""
        return self.__pointers.fat_addr_i128(ll_val, pointee_type_id)

    def cmp_fat_operands(self, ll_val: LLValue) -> tuple[ir.Value, ir.Value]:
        """比较操作数的 (data, index) 字段对:fat 聚合提取字段;裸 8B 指针按
        (自身, 0)(索引恒 0 语义)。返回 i8* 与 u64 两个 LLVM 值。"""
        return self.__pointers.cmp_fat_operands(ll_val)

    def slice_ptr_fat(self, base: LLValue, ptr_type_id: int) -> LLValue:
        """把 3 字段 slice/str 的 data 合成为 4 字段胖指针 ⟨data, word, 0, size⟩。

        分级指针表示:slice/str 自身携带真实 word(size 下标 2),直接继承
        (锁继承)——这是 T[]→T* 派生的锁继承来源。
        """
        return self.__pointers.slice_ptr_fat(base, ptr_type_id)

    def synthesize_fat_value(self, value: LLValue, size: LLValue) -> LLValue:
        """把裸 8B 指针值合成 4 字段胖指针 ⟨data, word, 0, size⟩。

        用于 slice/str 边界:从 16B slice 取出的 ptr 字段是裸 8B 指针,落入
        {T*, u64} 形态聚合(SliceStruct 等)时补全元数据。锁用全局字面量锁槽
        (恒 live)——合成点常在薄包装内,帧锁会随返回失效并逃逸到调用者死栈帧。
        """
        data = LLValue(self.__type_ctx.alloc_pointer(self.__type_ctx.u8_id), value.ir_val)  # type: ignore
        word = LLValue(self.__type_ctx.u64_id, self.literal_word())  # type: ignore
        zero = LLValue(self.__type_ctx.u64_id, ir.Constant(ir.IntType(64), 0))  # type: ignore
        if not self.__raw_pointers:
            self.check_view_count(size, "vcap")
        return self.build_fat(data, word, zero, size, value.type_id)

    def __fat_value_pair(self, v: ir.Value) -> tuple[ir.Value, ir.Value]:
        """ir.Value 级别的 (data, second) 字段对(分级指针表示分派)。

        第二字段按结构:4 字段指针取 index、3 字段 slice 取 size、2 字段 ref 取 word、
        raw 2 字段 slice 取 size;裸指针按 (自身, 0)。
        """
        if isinstance(v.type, ir.LiteralStructType):  # type: ignore
            field_count = len(v.type.elements)  # type: ignore
            if field_count >= 4:
                second_idx = ABI.FAT_INDEX      # 胖指针: 比较 (data, index)
            elif field_count == 3:
                second_idx = ABI.SLICE_SIZE     # 切片: 比较 (data, size)
            else:
                second_idx = ABI.REF_WORD       # 引用: 比较 (data, word)
            second = self.__builder.extract_value(v, second_idx)  # type: ignore
            if isinstance(second.type, ir.IntType) and second.type.width < 64:  # type: ignore
                second = self.__builder.zext(second, ir.IntType(64))  # type: ignore
            return (self.__builder.extract_value(v, ABI.FAT_DATA), second)  # type: ignore
        return (v, ir.Constant(ir.IntType(64), 0))  # type: ignore

    def cmp_fat_values(self, op: BinaryOperator, lhs: ir.Value, rhs: ir.Value) -> ir.Value:
        """对 ir.Value 级胖指针聚合操作数做字段比较。

        相等按 (data, index) 二元组;序比较先插 data 相等前提检查(
        跨对象失败)——未由 CFG 路由至此分支,须现场插检。
        """
        data_l, idx_l = self.__fat_value_pair(lhs)
        data_r, idx_r = self.__fat_value_pair(rhs)
        if op in (BinaryOperator.Eq, BinaryOperator.Neq):
            data_eq = self.__builder.icmp_signed("==", data_l, data_r)  # type: ignore
            idx_eq = self.__builder.icmp_unsigned("==", idx_l, idx_r)  # type: ignore
            both = self.__builder.and_(data_eq, idx_eq)  # type: ignore
            return self.__builder.not_(both) if op == BinaryOperator.Neq else both  # type: ignore
        data_ok = self.__builder.icmp_signed("==", data_l, data_r)  # type: ignore
        self.__core.flow.emit_check(LLValue(self.__type_ctx.bool_id, data_ok), RuntimeErrorCode.S005, "ptrcmp")
        predicate = {
            BinaryOperator.Lt: "<", BinaryOperator.Gt: ">",
            BinaryOperator.Leq: "<=", BinaryOperator.Geq: ">=",
        }[op]
        return self.__builder.icmp_unsigned(predicate, idx_l, idx_r)  # type: ignore

    # -- Access checks --

    def __word_key(self, word: ir.Value) -> ir.Value:
        """取 word 里的 32 位 key(与锁表项里的 key 同宽)。"""
        i64: ir.IntType = ir.IntType(64)  # type: ignore
        return self.__builder.and_(  # type: ignore
            self.__builder.lshr(word, ir.Constant(i64, ABI.WORD_KEY_SHIFT)),  # type: ignore
            ir.Constant(i64, ABI.KEY_MASK),  # type: ignore
        )

    def __extract_check_fields(self, ll_val: LLValue) -> tuple[ir.Value, ir.Value, ir.Value, ir.Value]:
        """一次提取 check 字段 bundle (data, word, index, size)——仅 PointerType(4 字段)。

        check_safe_access 用 bundle 一次取齐 live(data/word 重建锁槽)与
        in_bounds(index/size)两谓词所需字段, 避免各谓词重复 extract;
        RefType(2 字段, 无 index/size)不得经此。
        """
        ty = self.__type_ctx[ll_val.type_id]
        if not isinstance(ty, Type.PointerType):
            raise ValueError(f"check field bundle requires PointerType, got {type(ty).__name__}")
        data = self.extract_fat_field(ll_val, ABI.FAT_DATA).ir_val
        word = self.extract_fat_field(ll_val, ABI.FAT_WORD).ir_val
        index = self.extract_fat_field(ll_val, ABI.FAT_INDEX).ir_val
        size = self.extract_fat_field(ll_val, ABI.FAT_SIZE).ir_val
        return data, word, index, size

    def __check_live(self, word: ir.Value, data: ir.Value) -> LLValue:
        """live(p):锁表项 key 比较 ∧ word ≠ 0, 无分派、无窗口。

        锁槽地址只由 word 的 lock 下标给出; 表项里存的是同一个 32 位 key, 所以比较
        恒为"一次 32 位 load + 一次 32 位比较"。null(word = 0)用一次与运算短路为假,
        不读表项 0, 也不多一个基本块。
        """
        i32: ir.IntType = ir.IntType(32)  # type: ignore
        lock = self.lock_of(self.__builder, word, data)
        slot_val = self.__builder.load(lock, typ=i32)  # type: ignore
        key = self.__builder.trunc(self.__word_key(word), i32)  # type: ignore
        matched = self.__builder.icmp_unsigned("==", slot_val, key)  # type: ignore
        nonnull = self.__builder.icmp_unsigned("!=", word, ir.Constant(ir.IntType(64), 0))  # type: ignore
        return LLValue(self.__type_ctx.bool_id, self.__builder.and_(nonnull, matched))  # type: ignore

    def __check_in_bounds_cond(self, index: ir.Value, size: ir.Value) -> LLValue:
        """in_bounds(p,1):0 ≤ index ∧ index+1 ≤ size,简化为 index < size(u64)。"""
        cond = self.__builder.icmp_unsigned("<", index, size)  # type: ignore
        return LLValue(self.__type_ctx.bool_id, cond)  # type: ignore

    def __check_live_and(self, word: ir.Value, data: ir.Value, other: ir.Value) -> ir.Value:
        """live(p) ∧ other(i1 值)。"""
        live_val = self.__check_live(word, data)
        return self.__builder.and_(live_val.ir_val, other)  # type: ignore

    def check_safe_access(self, ptr: LLValue, live: bool = True) -> None:
        """safe_access(p,1) = live(p) ∧ in_bounds(p,1)。

        ``live=False``:调用点已确认该指针的锁槽恒等于其键(函数帧内取址),
        时序项恒真,只发射空间项;错误码与失败条件(排除恒真项后)不变。
        """
        if not self.is_fat(ptr):
            return
        if not live:
            index = self.extract_fat_field(ptr, ABI.FAT_INDEX).ir_val
            size = self.extract_fat_field(ptr, ABI.FAT_SIZE).ir_val
            self.__core.flow.emit_check(self.__check_in_bounds_cond(index, size), RuntimeErrorCode.S002, "safe")
            return
        data, word, index, size = self.__extract_check_fields(ptr)
        in_bounds = self.__check_in_bounds_cond(index, size)
        cond = self.__check_live_and(word, data, in_bounds.ir_val)
        self.__core.flow.emit_check(LLValue(self.__type_ctx.bool_id, cond), RuntimeErrorCode.S002, "safe")

    def check_view_access(self, view: LLValue, live: bool = True) -> None:
        """Validate a slice/str before passing its span to a syscall."""
        if not self.is_fat(view):
            return
        view_type = self.__type_ctx[view.type_id]
        if not isinstance(view_type, (Type.SliceType, Type.StrType)):
            return

        data = self.extract_fat_field(view, ABI.SLICE_DATA).ir_val
        word = self.extract_fat_field(view, ABI.SLICE_WORD).ir_val
        size = self.extract_fat_field(view, ABI.SLICE_SIZE).ir_val
        lock = self.lock_of(self.__builder, word, data)
        if live:
            live_ok = self.__check_live(word, data)
        else:
            # 帧内视图:锁槽恒等于其键,live 项恒真(见 check_safe_access)。
            live_ok = LLValue(self.__type_ctx.bool_id, ir.Constant(ir.IntType(1), 1))  # type: ignore

        zero = ir.Constant(ir.IntType(64), 0)  # type: ignore
        nonempty = self.__builder.icmp_unsigned("!=", size, zero)  # type: ignore
        nonnull = self.__builder.icmp_unsigned(
            "!=", data, ir.Constant(data.type, None)  # type: ignore
        )  # type: ignore
        data_ok = self.__builder.or_(  # type: ignore
            self.__builder.not_(nonempty), nonnull  # type: ignore
        )

        # Heap views can be checked against the allocation header. Stack and
        # literal views have no allocation header; their constructors establish
        # the source range and the live lock protects their lifetime.
        # 槽种类由锁表下标区间给出(堆区间 ≥ HEAP_LOCK_BASE); 负载锚由表项
        # anchor_lo32 + data 高 32 位还原, 块首 = 锚 - BYTES(分配器保证同 4 GiB 窗口)。
        index = self.__builder.and_(word, ir.Constant(ir.IntType(64), ABI.LOCK_MASK))  # type: ignore
        heap = self.__builder.icmp_unsigned(  # type: ignore
            ">=", index, ir.Constant(ir.IntType(64), ABI.HEAP_LOCK_BASE)  # type: ignore
        )
        header_guard: ir.Value = self.__builder.and_(heap, live_ok.ir_val)  # type: ignore
        anchor = self.__load_guarded_u32(lock, ABI.LockEntry.ANCHOR_OFFSET, header_guard)
        data_int: ir.Value = self.__builder.ptrtoint(data, ir.IntType(64))  # type: ignore
        payload_int, block_int = self.__heap_block(data_int, anchor)
        active_size = self.__load_guarded_u32(block_int, ABI.BlockHeader.EXTENT_OFFSET, header_guard)

        element_type = view_type.element_type if isinstance(view_type, Type.SliceType) else self.__type_ctx.u8_id
        element_size = self.__ll_type_ctx.get_type_size(element_type)
        i128: ir.IntType = ir.IntType(128)  # type: ignore
        span_bytes, span_no_wrap = self.__mul_u64_i128(size, element_size)
        # 块头 extent 是元素数: 按同一个元素大小折算成字节再比较。
        active_size_bytes = self.__builder.mul(  # type: ignore
            active_size, ir.Constant(ir.IntType(64), element_size)  # type: ignore
        )
        data_addr = cast(
            ir.Value,
            self.__builder.zext(  # type: ignore
                self.__builder.ptrtoint(data, ir.IntType(64)), i128  # type: ignore
            ),
        )
        base_addr, base_no_wrap = self.__add_i128_no_wrap(
            cast(ir.Value, self.__builder.zext(payload_int, i128)),  # type: ignore
            ir.Constant(i128, 0),  # type: ignore
        )
        end_addr, end_no_wrap = self.__add_i128_no_wrap(data_addr, span_bytes)
        allocation_end, allocation_end_no_wrap = self.__add_i128_no_wrap(
            base_addr,
            cast(ir.Value, self.__builder.zext(active_size_bytes, i128)),  # type: ignore
        )
        heap_span_ok = self.__builder.and_(  # type: ignore
            self.__builder.and_(  # type: ignore
                self.__builder.icmp_unsigned(">=", data_addr, base_addr),  # type: ignore
                self.__builder.icmp_unsigned("<=", end_addr, allocation_end),  # type: ignore
            ),
            self.__builder.and_(  # type: ignore
                span_no_wrap,
                self.__builder.and_(base_no_wrap, self.__builder.and_(end_no_wrap, allocation_end_no_wrap)),  # type: ignore
            ),
        )
        span_ok = self.__builder.or_(  # type: ignore
            self.__builder.not_(heap), heap_span_ok  # type: ignore
        )
        cond: ir.Value = self.__builder.and_(  # type: ignore
            live_ok.ir_val, self.__builder.and_(data_ok, span_ok)  # type: ignore
        )  # type: ignore
        self.__core.flow.emit_check(LLValue(self.__type_ctx.bool_id, cond), RuntimeErrorCode.S002, "view")

    def check_in_bounds(self, ptr: LLValue) -> None:
        """in_bounds(p_s,1)(重锚定前提)。"""
        if not self.is_fat(ptr):
            return
        index = self.extract_fat_field(ptr, ABI.FAT_INDEX).ir_val
        size = self.extract_fat_field(ptr, ABI.FAT_SIZE).ir_val
        self.__core.flow.emit_check(self.__check_in_bounds_cond(index, size), RuntimeErrorCode.S001, "ib")

    def check_slice_nonempty(self, ptr: LLValue) -> None:
        """Establish the one-element origin invariant for ``T[] -> T&``."""
        if not self.is_fat(ptr):
            return
        size = self.extract_fat_field(ptr, ABI.SLICE_SIZE).ir_val
        nonempty = self.__builder.icmp_unsigned(
            ">", size, ir.Constant(ir.IntType(64), 0)  # type: ignore
        )
        self.__core.flow.emit_check(LLValue(self.__type_ctx.bool_id, nonempty), RuntimeErrorCode.S007, "sref")

    def check_ref_access(self, ptr: LLValue) -> None:
        """T& 引用访问前检:仅 live(免 in_bounds,tiered-pointers)。

        引用无 index/size(2 字段 ⟨data,word⟩),无越界概念;
        live = 锁槽键比较(含 null 短路)。live 失败 → 报告 S003。
        """
        if not self.is_fat(ptr):
            return
        data = self.extract_fat_field(ptr, ABI.FAT_DATA).ir_val
        word = self.extract_fat_field(ptr, ABI.FAT_WORD).ir_val
        cond = self.__check_live(word, data)
        self.__core.flow.emit_check(cond, RuntimeErrorCode.S003, "ref")

    def __element_arith_cond(self, base: LLValue, offset: LLValue) -> ir.Value:
        index = self.extract_fat_field(base, ABI.FAT_INDEX).ir_val
        size = self.extract_fat_field(base, ABI.FAT_SIZE).ir_val
        off64 = self.__builder.bitcast(offset.ir_val, ir.IntType(64))  # type: ignore
        total = self.__builder.add(index, off64)  # type: ignore
        no_wrap = self.__builder.icmp_unsigned(">=", total, index)  # type: ignore
        in_range = self.__builder.icmp_unsigned("<=", total, size)  # type: ignore
        return self.__builder.and_(no_wrap, in_range)  # type: ignore

    def check_element_arith(self, base: LLValue, offset: LLValue) -> None:
        """良构检查:0 ≤ index+offset ≤ size;u64 同型化(回绕检测 + 上界比较)。

        指针算术本身不访问内存，因此这里与 PtrDiff/PtrCmp 一样不检查
        allocation live 状态；解引用与外部 I/O 在各自访问边界检查 live。

        对 u64 索引(非负恒真)把 0 ≤ index+offset ≤ size 分解为 u64 计算:
        (a) 回绕检测 no_wrap = icmp uge sum, index(sum = index+offset,u64 回绕
        ⟺ sum < index);(b) 上界比较 in_range = icmp ule sum, size;条件 =
        and(no_wrap, in_range)。与 i128 语义论证:正偏移(offset < 2^63 且
        index+offset < 2^64)无回绕时 sum ≥ index 恒真,只剩上界比较,与 i128
        完全等价;回绕(index+offset ≥ 2^64)时 sum < index → no_wrap 失败 →
        失败(等价 i128 的 sum ≥ 2^64 > size);下溢偏移(offset ≥ 2^63,
        语义 = 极大无符号下标)u64 形式报告安全错误(收紧,对齐 u64 索引语义——sext
        形式把其误解为负数,子切片 base.index ≥ |o| 时放行)。u64 同型使
        ConstraintElimination 可关联循环/分支约束消除本检查(O-1 由回绕检测
        落地,无需宽整数)。LLVM 层 发射。
        """
        if not self.is_fat(base):
            return
        cond = self.__element_arith_cond(base, offset)
        self.__core.flow.emit_check(LLValue(self.__type_ctx.bool_id, cond), RuntimeErrorCode.S004, "elarith")

    def check_element_access(self, base: LLValue, offset: LLValue, ptr: LLValue, live: bool = True) -> None:
        """合并检查:ElementArith→InBounds→SafeAccess 合取谓词(检查合并优化)。

        派生链 elem = base + offset → f = elem.field → 访问 f 的三重检查合并:
        良构(elem)(u64 同型化:回绕检测 + 上界比较)∧ in_bounds(elem,1)
        (one-past-end 的 elem 取字段时报告安全错误)∧ live(elem)(
        SafeAccess 的 live 项;in_bounds(f,1) 对重锚定字段指针恒真、
        live(f)=live(elem) 由锁字段继承)。禁止丢 no-wrap/live 任一子项。
        LLVM 层 发射。
        """
        if not (self.is_fat(base) and self.is_fat(ptr)):
            return
        elarith_cond = self.__element_arith_cond(base, offset)
        # InBounds 部分:elem.index < elem.size
        e_index = self.extract_fat_field(ptr, ABI.FAT_INDEX).ir_val
        e_size = self.extract_fat_field(ptr, ABI.FAT_SIZE).ir_val
        ib_cond = self.__builder.icmp_unsigned("<", e_index, e_size)  # type: ignore
        # live 部分:锁槽键比较,含 null 短路(帧内 elem 恒真时不再发射)
        if live:
            ev_data = self.extract_fat_field(ptr, ABI.FAT_DATA).ir_val
            ev_word = self.extract_fat_field(ptr, ABI.FAT_WORD).ir_val
            live_ok = self.__check_live(ev_word, ev_data)
            cond: ir.Value = self.__builder.and_(self.__builder.and_(elarith_cond, ib_cond), live_ok.ir_val)  # type: ignore
        else:
            cond = self.__builder.and_(elarith_cond, ib_cond)  # type: ignore
        self.__core.flow.emit_check(LLValue(self.__type_ctx.bool_id, cond), RuntimeErrorCode.S002, "eacc")

    def check_raw_bounds(self, index: LLValue, length: int) -> None:
        """惰性左值路径裸数组越界检查:0 ≤ index < length(编译期长度)。

        未取址裸数组元素访问无胖元数据,单 unsigned 比较即达 fat 路径
        CheckElementArith + CheckSafeAccess 的组合越界语义(索引已 coerce u64)。
        """
        cond = self.__builder.icmp_unsigned("<", index.ir_val, ir.Constant(ir.IntType(64), length))  # type: ignore
        self.__core.flow.emit_check(LLValue(self.__type_ctx.bool_id, cond), RuntimeErrorCode.S001, "rb")

    def check_ptrdiff(self, lhs: LLValue, rhs: LLValue) -> None:
        """前提:data 相等 + 良构(双方 index ≤ size)+ 差可表示。

        PtrDiff 不访问内存，故沿用指针算术策略，不检查 allocation live。
        """
        if not (self.is_fat(lhs) and self.is_fat(rhs)):
            return
        data_l = self.extract_fat_field(lhs, ABI.FAT_DATA).ir_val
        data_r = self.extract_fat_field(rhs, ABI.FAT_DATA).ir_val
        data_eq = self.__builder.icmp_signed("==", data_l, data_r)  # type: ignore
        idx_l = self.extract_fat_field(lhs, ABI.FAT_INDEX).ir_val
        idx_r = self.extract_fat_field(rhs, ABI.FAT_INDEX).ir_val
        size_l = self.extract_fat_field(lhs, ABI.FAT_SIZE).ir_val
        size_r = self.extract_fat_field(rhs, ABI.FAT_SIZE).ir_val
        wf_l = self.__builder.icmp_unsigned("<=", idx_l, size_l)  # type: ignore
        wf_r = self.__builder.icmp_unsigned("<=", idx_r, size_r)  # type: ignore
        i128: ir.IntType = ir.IntType(128)  # type: ignore
        diff128 = self.__builder.sub(self.__builder.zext(idx_l, i128), self.__builder.zext(idx_r, i128))  # type: ignore
        diff64 = self.__builder.trunc(diff128, ir.IntType(64))  # type: ignore
        no_wrap = self.__builder.icmp_signed("==", self.__builder.sext(diff64, i128), diff128)  # type: ignore
        wf_cond = self.__builder.and_(wf_l, wf_r)  # type: ignore
        cond: ir.Value = self.__builder.and_(data_eq, self.__builder.and_(wf_cond, no_wrap))  # type: ignore
        self.__core.flow.emit_check(LLValue(self.__type_ctx.bool_id, cond), RuntimeErrorCode.S005, "ptrdiff")

    def check_ptr_cmp(self, lhs: LLValue, rhs: LLValue) -> None:
        """序比较前提:data 相等(跨对象序比较报告 S005)。

        PtrCmp 不访问内存，故沿用指针算术策略，不检查 allocation live。
        """
        if not (self.is_fat(lhs) and self.is_fat(rhs)):
            return
        data_l = self.extract_fat_field(lhs, ABI.FAT_DATA).ir_val
        data_r = self.extract_fat_field(rhs, ABI.FAT_DATA).ir_val
        cond = self.__builder.icmp_signed("==", data_l, data_r)  # type: ignore
        self.__core.flow.emit_check(LLValue(self.__type_ctx.bool_id, cond), RuntimeErrorCode.S005, "ptrcmp")

    def check_delete(self, ptr: LLValue) -> None:
        """Validate and release a complete, live heap view.

        The view must be live, start at the heap allocation base, and cover
        exactly the active payload extent recorded in the block header.  Pool
        capacity can exceed that logical extent.
        """
        if not self.is_fat(ptr):
            return
        ptr_type = self.__type_ctx[ptr.type_id]
        if not isinstance(ptr_type, (Type.PointerType, Type.SliceType, Type.StrType, Type.RefType)):
            return
        data = self.extract_fat_field(ptr, ABI.FAT_DATA).ir_val
        word = self.extract_fat_field(ptr, ABI.FAT_WORD).ir_val
        index = self.__builder.and_(word, ir.Constant(ir.IntType(64), ABI.LOCK_MASK))  # type: ignore
        heap_ok = self.__builder.icmp_unsigned(  # type: ignore
            ">=", index, ir.Constant(ir.IntType(64), ABI.HEAP_LOCK_BASE)  # type: ignore
        )
        lock = self.lock_of(self.__builder, word, data)
        live_ok = self.__check_live(word, data)
        header_guard: ir.Value = self.__builder.and_(heap_ok, live_ok.ir_val)  # type: ignore
        # 锚点判定: 表项 anchor_lo32 必须等于 data 低 32 位(重锚定指针立刻被拒)。
        anchor = self.__load_guarded_u32(lock, ABI.LockEntry.ANCHOR_OFFSET, header_guard)
        data_int: ir.Value = self.__builder.ptrtoint(data, ir.IntType(64))  # type: ignore
        raw_data_ok = self.__builder.icmp_unsigned(  # type: ignore
            "==",
            self.__builder.and_(data_int, ir.Constant(ir.IntType(64), ABI.LOCK_MASK)),  # type: ignore
            anchor,
        )
        raw_cond: ir.Value = cast(ir.Value, raw_data_ok)
        if isinstance(ptr_type, Type.PointerType):
            ptr_index = self.extract_fat_field(ptr, ABI.FAT_INDEX).ir_val
            raw_index_ok = self.__builder.icmp_signed("==", ptr_index, ir.Constant(ir.IntType(64), 0))  # type: ignore
            raw_cond = self.__builder.and_(raw_data_ok, raw_index_ok)  # type: ignore
        _, block_int = self.__heap_block(data_int, anchor)
        active_size = self.__load_guarded_u32(block_int, ABI.BlockHeader.EXTENT_OFFSET, header_guard)
        extent_ok = self.__delete_extent_ok(ptr, ptr_type, active_size)
        cond: ir.Value = self.__builder.and_(
            header_guard,
            self.__builder.and_(raw_cond, extent_ok),  # type: ignore
        )  # type: ignore
        self.__core.flow.emit_check(LLValue(self.__type_ctx.bool_id, cond), RuntimeErrorCode.S006, "del")

    def __heap_block(self, data_int: ir.Value, anchor: ir.Value) -> tuple[ir.Value, ir.Value]:
        """Reconstruct a heap payload and block base from its low-32-bit anchor."""
        i64: ir.IntType = ir.IntType(64)  # type: ignore
        payload_int: ir.Value = self.__builder.or_(  # type: ignore
            self.__builder.and_(data_int, ir.Constant(i64, ABI.WINDOW_MASK)),  # type: ignore
            anchor,
        )
        block: ir.Value = self.__builder.inttoptr(  # type: ignore
            self.__builder.sub(payload_int, ir.Constant(i64, ABI.BlockHeader.BYTES)),  # type: ignore
            self.__ll_type_ctx.ptr_type,
        )
        return payload_int, block

    def __load_guarded_u32(self, base: ir.Value, offset: int, guard: ir.Value) -> ir.Value:
        """Load a guarded 32-bit header or lock-table field as u64."""
        i64: ir.IntType = ir.IntType(64)  # type: ignore
        i32: ir.IntType = ir.IntType(32)  # type: ignore

        def load_field(builder: ir.IRBuilder) -> ir.Value:
            field = builder.gep(  # type: ignore
                base,
                [ir.Constant(i64, offset)],  # type: ignore
                inbounds=False,
                source_etype=ir.IntType(8),  # Header fields use byte offsets.
            )
            return builder.zext(builder.load(field, typ=i32), i64)  # type: ignore

        return self.__core.flow.emit_guarded(guard, load_field, ir.IntType(64))  # type: ignore

    def __delete_extent_ok(
        self,
        ptr: LLValue,
        ptr_type: Type.PointerType | Type.SliceType | Type.StrType | Type.RefType,
        active_size: ir.Value,
    ) -> ir.Value:
        """Check that *ptr* denotes exactly the active heap payload."""
        i128: ir.IntType = ir.IntType(128)  # type: ignore
        if isinstance(ptr_type, Type.PointerType):
            count = self.extract_fat_field(ptr, ABI.FAT_SIZE).ir_val
            element_type = ptr_type.pointee_type
        elif isinstance(ptr_type, (Type.SliceType, Type.StrType)):
            count = self.extract_fat_field(ptr, ABI.SLICE_SIZE).ir_val
            element_type = ptr_type.element_type if isinstance(ptr_type, Type.SliceType) else self.__type_ctx.u8_id
        else:
            count = ir.Constant(ir.IntType(64), 1)  # type: ignore
            element_type = ptr_type.pointee_type

        element_size = self.__ll_type_ctx.get_type_size(element_type)
        logical_bytes, logical_no_wrap = self.__mul_u64_i128(count, element_size)
        active_bytes = self.__builder.zext(  # type: ignore
            self.__builder.mul(active_size, ir.Constant(ir.IntType(64), element_size)),  # type: ignore
            i128,
        )
        return cast(
            ir.Value,
            self.__builder.and_(  # type: ignore
                logical_no_wrap,
                self.__builder.icmp_unsigned("==", logical_bytes, active_bytes),  # type: ignore
            ),
        )

    def __mul_u64_i128(self, value: ir.Value, factor: int) -> tuple[ir.Value, ir.Value]:
        """Multiply a u64 value in i128 and return (product, no_wrap)."""
        i128: ir.IntType = ir.IntType(128)  # type: ignore
        wide = cast(ir.Value, self.__builder.zext(value, i128))  # type: ignore
        product = cast(ir.Value, self.__builder.mul(wide, ir.Constant(i128, factor)))  # type: ignore
        max_i128 = (1 << 128) - 1
        limit = max_i128 // factor if factor > 0 else max_i128
        no_wrap = cast(
            ir.Value,
            self.__builder.icmp_unsigned(  # type: ignore
                "<=", wide, ir.Constant(i128, limit)  # type: ignore
            ),
        )
        return product, no_wrap

    def __add_i128_no_wrap(self, left: ir.Value, right: ir.Value) -> tuple[ir.Value, ir.Value]:
        """Add i128 values and return (sum, unsigned no-wrap predicate)."""
        total = cast(ir.Value, self.__builder.add(left, right))  # type: ignore
        no_wrap = cast(ir.Value, self.__builder.icmp_unsigned(">=", total, left))  # type: ignore
        return total, no_wrap
