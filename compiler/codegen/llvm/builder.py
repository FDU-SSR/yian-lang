# pyright: reportUnknownMemberType=false
"""
LLBuilder — wraps llvmlite ``ir.IRBuilder`` to hide LLVM-level details.

llvmlite 0.44 leaves the operand types of several dynamic IRBuilder methods
unspecified; suppress that dependency-boundary noise while keeping every other
strict Pyright diagnostic enabled for this module.
"""

from __future__ import annotations

from enum import Enum, auto
from typing import cast

from llvmlite import ir

from compiler.analysis.ty import ty as Type
from compiler.analysis.ty.context import TypeCtx
from compiler.codegen.cfg import ir as IR
from compiler.codegen.llvm.intrinsics import IntrinsicKind
from compiler.codegen.llvm.module import LLFunction, LLModule
from compiler.codegen.llvm.types import LLTypeCtx
from compiler.codegen.llvm.value import LLValue
from compiler.frontend.parse.operator import BinaryOperator, UnaryOperator
from compiler.runtime_error import RuntimeErrorCode
from compiler.runtime_lib import class_index_for_payload


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
        self.__frame_lock_slot_name: str | None = None  # 帧锁槽寄存器名(acquire 时记录)
        self.__frame_lock_acquired: bool = False         # 是否已取得帧锁(返回路径写 SENTINEL)

    # ------------------------------------------------------------------
    # constants
    # ------------------------------------------------------------------

    def i32(self, v: int) -> LLValue:
        return LLValue(self.__type_ctx.u32_id, ir.Constant(ir.IntType(32), v))  # type: ignore

    def i64(self, v: int) -> LLValue:
        return LLValue(self.__type_ctx.u64_id, ir.Constant(ir.IntType(64), v))  # type: ignore

    def undef(self, type_id: int) -> LLValue:
        return LLValue(type_id, ir.Constant(self.__ll_type_ctx.get_ll_type(type_id).ir_type, ir.Undefined))  # type: ignore

    def __bitcast(self, value: ir.Value, typ: ir.Type, name: str = "") -> ir.Value:
        """Bitcast that really re-types its operand.

        ``IRBuilder.bitcast`` returns the operand unchanged whenever the two
        IR-layer types compare equal, and a typed pointer compares equal to an
        opaque one.  Data pointers are opaque since the opaque-pointer
        migration, so a cast onto one of them would silently keep the operand's
        static pointee type; a later GEP would then scale its indices by that
        stale type.  Build the instruction directly so the value carries
        ``typ``; every other case keeps llvmlite's behaviour, including the
        intentional no-op when nothing has to change.
        """
        source_type: ir.Type = value.type  # type: ignore
        if isinstance(source_type, ir.PointerType) and isinstance(typ, ir.PointerType) \
                and typ.is_opaque and not source_type.is_opaque:
            instr = ir.instructions.CastInstr(self.__builder.block, "bitcast", value, typ, name)  # type: ignore
            self.__builder._insert(instr)  # type: ignore
            return instr
        return self.__builder.bitcast(value, typ, name)  # type: ignore

    def sizeof_const(self, type_id: int, result: str) -> None:
        self.__func.set_reg(result, LLValue(self.__type_ctx.u64_id, ir.Constant(ir.IntType(64), self.__ll_type_ctx.get_type_size(type_id))))  # type: ignore

    # ------------------------------------------------------------------
    # fat pointer helpers (5 字段胖指针值层下降,LLVM 层)
    # ------------------------------------------------------------------

    def __is_fat(self, ll_val: LLValue) -> bool:
        """判定 LLVM 值是否已是胖结构聚合(PointerType 40B / SliceType 32B / RefType 24B)。

        分级指针表示统一:三个指针族类型的 LLVM 值形态为 LiteralStructType 即视为胖值;
        raw 模式无元数据(裸 T* / {T*, u64} / 裸 T*),恒 False。从 slice/str 提取的
        裸 8B 指针(type_id 为 PointerType)按 LLVM 值形态(指针而非结构)不误判。
        """
        if self.__raw_pointers:
            return False
        ty = self.__type_ctx[self.__type_ctx.resolve_aliases(ll_val.type_id)]
        if isinstance(ty, (Type.PointerType, Type.SliceType, Type.StrType, Type.RefType)):
            # 胖值须为多字段结构(40B/32B/24B);空结构 `{}`(ZST 擦除,ref/ptr-to-ZST
            # 零运行时信息)按非胖处理——对它的任意字段操作均无意义(ZST 特例)。
            return isinstance(ll_val.ir_val.type, ir.LiteralStructType) and len(ll_val.ir_val.type.elements) > 0  # type: ignore
        return False

    def __is_fat_type(self, type_id: int) -> bool:
        """类型层胖指针判定(分级指针 按 type_id 分派):PointerType(pointee 非 ZST)
        5 字段 / SliceType·StrType 4 字段 / RefType 3 字段,均为胖结构值;
        raw 模式恒 False,指针一律按裸 8B 处理。与 CFG 层 __is_fat_pointer
        对应(后者专指 PointerType 的全检查路径)。
        """
        if self.__raw_pointers:
            return False
        ty = self.__type_ctx[self.__type_ctx.resolve_aliases(type_id)]
        if isinstance(ty, Type.PointerType):
            return not self.__type_ctx.is_zst(ty.pointee_type)
        return isinstance(ty, (Type.SliceType, Type.StrType, Type.RefType))

    def __extract_fat_field(self, ll_val: LLValue, index: int) -> LLValue:
        """提取胖指针聚合字段;整数字段一律零扩展到 u64、指针字段 u8*(下标见 FAT_*/SLICE_*/REF_*)。

        PointerType 的 index/size 是 32 位元素数, 值层统一按 u64 处理(长度来自已被
        校验 ≤ MAX_VIEW_COUNT 的分配容量, 提取端零扩展; 比较/算术语义与 u64 版一致);
        word 是完整的 64 位整字(锁址 + 键)。
        """
        ir_val = self.__builder.extract_value(ll_val.ir_val, index)  # type: ignore
        if index == IR.FAT_DATA:  # 只有 data 是裸指针; word 是整字, index/size 是 32 位
            field_type = self.__type_ctx.alloc_pointer(self.__type_ctx.u8_id)
        else:
            field_type = self.__type_ctx.u64_id
            if isinstance(ir_val.type, ir.IntType) and ir_val.type.width < 64:  # type: ignore
                ir_val = self.__builder.zext(ir_val, ir.IntType(64))  # type: ignore
        return LLValue(field_type, ir_val)  # type: ignore

    def __insert_field_value(self, agg: LLValue, value: LLValue, index: int) -> LLValue:
        """按目标字段实际位宽插入聚合字段(收窄到 32 位视图字段时截断)。

        收窄只发生在 index/size 上: 构造写入的长度来自受 Malloc 上限约束的分配容量,
        算术写入由 CheckElementArith 保证 ≤ size ≤ MAX_VIEW_COUNT, 因此截断不改变语义。
        """
        dest: ir.Type = agg.ir_val.type.elements[index]  # type: ignore
        ir_val: ir.Value = value.ir_val
        if isinstance(dest, ir.IntType) and isinstance(ir_val.type, ir.IntType) and ir_val.type.width > dest.width:  # type: ignore
            ir_val = self.__builder.trunc(ir_val, dest)  # type: ignore
        return LLValue(agg.type_id, self.__builder.insert_value(agg.ir_val, ir_val, index))  # type: ignore

    def __narrow_view_value(self, ir_val: ir.Value, agg: ir.Value, index: int) -> ir.Value:
        """把常量视图值收窄到目标聚合字段位宽(供 undef 重建路径直插用)。"""
        dest: ir.Type = agg.type.elements[index]  # type: ignore
        if isinstance(dest, ir.IntType) and isinstance(ir_val.type, ir.IntType) and ir_val.type.width > dest.width:  # type: ignore
            return self.__builder.trunc(ir_val, dest)  # type: ignore
        return ir_val

    @staticmethod
    def __check_static_view_count(count: int) -> None:
        """编译期数组退化的长度上限: 超过 32 位视图上限的类型无法表示, 直接报错。"""
        if count > IR.MAX_VIEW_COUNT:
            raise ValueError(
                f"array length {count} exceeds the 32-bit view element limit {IR.MAX_VIEW_COUNT}"
            )

    def __check_view_count(self, value: LLValue, suffix: str) -> None:
        """分配元素数上限: 必须 ≤ MAX_VIEW_COUNT(否则 32 位长度字段会截断)。

        只在堆分配处发射一次: 视图长度都来自某个分配的容量, 分配受这条上限约束后,
        由视图派生出的长度(子切片、T*→T[] 的剩余长度、视图转换)必然可表示。
        """
        limit: ir.Value = ir.Constant(ir.IntType(64), IR.MAX_VIEW_COUNT)  # type: ignore
        ok = self.__builder.icmp_unsigned("<=", value.ir_val, limit)  # type: ignore
        self.__emit_check(LLValue(self.__type_ctx.bool_id, ok), RuntimeErrorCode.R001, suffix)

    def __build_fat(self, data: LLValue, word: LLValue, index: LLValue, size: LLValue, type_id: int) -> LLValue:
        """按结构构造胖值:PointerType 4 字段 ⟨data,word,index,size⟩ /
        SliceType·StrType 3 字段 ⟨data,word,size⟩ / RefType 2 字段 ⟨data,word⟩。

        word 同时携带锁址与键(见 lockmech), 槽里存同一份。
        """
        ty = self.__type_ctx[self.__type_ctx.resolve_aliases(type_id)]
        val = self.undef(type_id)
        val = self.insert_value(val, data, IR.FAT_DATA)
        val = self.insert_value(val, word, IR.FAT_WORD)
        if isinstance(ty, Type.PointerType):
            val = self.__insert_field_value(val, index, IR.FAT_INDEX)
            val = self.__insert_field_value(val, size, IR.FAT_SIZE)
        elif isinstance(ty, (Type.SliceType, Type.StrType)):
            val = self.insert_value(val, size, IR.SLICE_SIZE)
        elif isinstance(ty, Type.RefType):
            pass
        else:
            raise ValueError(f"not a pointer-family type: {type(ty).__name__}")
        return val

    def __lock_of(self, builder: ir.IRBuilder, word: ir.Value, data: ir.Value) -> ir.Value:
        """从 word 取锁表项地址(变体 B: 只有一条公式, 与 data/kind 无关)。

        lock 字段就是锁表下标, 地址 = @__secl_lock_table + lock*LOCK_ENTRY_BYTES;
        下标区间自带 kind, 热路径没有分派。data 参数保留是为了与调用点同构。
        """
        i32: ir.IntType = ir.IntType(32)  # type: ignore
        i64: ir.IntType = ir.IntType(64)  # type: ignore
        table = self.__module.get_lock_table()
        index = builder.zext(builder.trunc(word, i32), i64)  # type: ignore
        return builder.gep(table, [ir.Constant(i64, 0), index], inbounds=True)  # type: ignore

    def __word_key(self, word: ir.Value) -> ir.Value:
        """取 word 里的 32 位 key(与锁表项里的 key 同宽)。"""
        i64: ir.IntType = ir.IntType(64)  # type: ignore
        return self.__builder.and_(  # type: ignore
            self.__builder.lshr(word, ir.Constant(i64, IR.WORD_KEY_SHIFT)),  # type: ignore
            ir.Constant(i64, IR.KEY_MASK),  # type: ignore
        )

    def __literal_word(self) -> ir.Value:
        """字面量指针携带的 word(锁表字面量槽 key = 0, 恒 live)。"""
        return ir.Constant(ir.IntType(64), IR.LITERAL_WORD)  # type: ignore

    def __env_word(self) -> ir.Value:
        """环境指针携带的 word(锁表环境槽 key = 0, 恒 live)。"""
        return ir.Constant(ir.IntType(64), IR.ENV_WORD)  # type: ignore
    def __fat_data(self, ll_val: LLValue) -> LLValue:
        """取胖指针的 data 字段(裸 8B 地址);已是裸指针则直接返回。

        slice/str 构造边界:从 slice 提取的裸 8B 指针(未合成 fat)与 malloc/VarPtr
        合成的 fat 在此统一取数据地址。
        """
        if self.__is_fat(ll_val):
            return self.__extract_fat_field(ll_val, IR.FAT_DATA)
        return ll_val

    def __promote_fat(self, ll_val: LLValue) -> LLValue:
        """把裸 8B 指针值(如 Alloca 结果)提升为胖指针 ⟨data, word, 0, 1⟩。

        值层统一: type_id 为 PointerType 的 LLVM 值须是聚合才能跨调用/返回/存储;
        Alloca 等产生裸指针的原语在此补全元数据(字面量锁槽, 恒 live)。
        """
        if self.__is_fat_type(ll_val.type_id) and not self.__is_fat(ll_val):
            data = LLValue(self.__type_ctx.alloc_pointer(self.__type_ctx.u8_id), ll_val.ir_val)  # type: ignore
            word = LLValue(self.__type_ctx.u64_id, self.__literal_word())  # type: ignore
            zero = LLValue(self.__type_ctx.u64_id, ir.Constant(ir.IntType(64), 0))  # type: ignore
            one = LLValue(self.__type_ctx.u64_id, ir.Constant(ir.IntType(64), 1))  # type: ignore
            return self.__build_fat(data, word, zero, one, ll_val.type_id)
        return ll_val

    def __fat_addr(self, ll_val: LLValue, pointee_type_id: int) -> LLValue:
        """地址折算:有效地址 = data + index·|T|,一次 GEP(检查与取数共用)。

        对 fat 值提取 data/index 字段,bitcast 到 T* 后按元素索引 GEP;对裸 8B
        指针直接使用(索引恒 0 语义)。T&无 index 字段,恒指单个元素——
        有效地址即 data。O-1 无回摆由检查(well-formed)保障。
        """
        if self.__is_fat(ll_val):
            data = self.__extract_fat_field(ll_val, IR.FAT_DATA).ir_val
            pointee_ll = self.__ll_type_ctx.get_ll_type(pointee_type_id).ir_type
            ty = self.__type_ctx[self.__type_ctx.resolve_aliases(ll_val.type_id)]
            if isinstance(ty, Type.RefType):
                # T& 3 字段 {data, lock_ptr, key}:无 index,恒指单个元素——
                # 有效地址 = data (opaque 指针无需 bitcast)
                addr = data
            else:
                index = self.__extract_fat_field(ll_val, IR.FAT_INDEX).ir_val
                addr = self.__builder.gep(data, [index], inbounds=False, source_etype=pointee_ll)  # type: ignore
        else:
            addr = ll_val.ir_val
        return LLValue(self.__type_ctx.alloc_pointer(pointee_type_id), addr)  # type: ignore

    def __gen_key_value(self) -> LLValue:
        """帧进入 re-key:发射不回绕的 32 位单调键体, 耗尽即失败 (R003)。

        key 与锁表项的 32 位槽严格同宽: 计数到 FRAME_KEY_LIMIT(0xFFFFFFFE,
        0xFFFFFFFF 留给帧退出的 SENTINEL)即确定性终止, 绝不回绕——回绕会让旧指针
        重新匹配。堆对象的身份不走这里(锁表项按块换代)。
        """
        counter = self.__module.get_key_counter()
        loaded = self.__builder.load(counter)  # type: ignore
        available = self.__builder.icmp_unsigned(
            "<", loaded, ir.Constant(ir.IntType(64), IR.FRAME_KEY_LIMIT)  # type: ignore
        )
        self.__emit_check(LLValue(self.__type_ctx.bool_id, available), RuntimeErrorCode.R003, "keyex")
        nxt = self.__builder.add(loaded, ir.Constant(ir.IntType(64), 1))  # type: ignore
        self.__builder.store(nxt, counter)  # type: ignore
        return LLValue(self.__type_ctx.u64_id, self.__builder.and_(nxt, ir.Constant(ir.IntType(64), IR.KEY_MASK)))  # type: ignore

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

    def __emit_check(self, cond: LLValue, error_code: RuntimeErrorCode, suffix: str) -> None:
        """检查失败 → 报告规范错误并退出;通过 → 继续于新 ok 块。

        检查是 CFG 块中间的语句,必须分裂基本块:原块以条件分支结束,ok 块
        承载后续语句与终止符,失败块调用不可返回的运行时错误入口。
        """
        seq = self.__check_seq
        self.__check_seq += 1
        ok_block = self.__func.new_block(self.__split_block_name(suffix, "ok", seq))
        fail_block = self.__func.new_block(self.__split_block_name(suffix, "fail", seq))
        self.__func.add_block(ok_block.name, ok_block)  # type: ignore
        self.__func.add_block(fail_block.name, fail_block)  # type: ignore
        branch = self.__builder.cbranch(cond.ir_val, ok_block, fail_block)  # type: ignore
        # 冷路径:检查失败几乎不发生,给出分支权重让后端把 fail 块移出热路径直线
        branch.set_weights([2000, 1])  # type: ignore
        fail_builder = ir.IRBuilder(fail_block)
        self.__module.emit_runtime_fail(fail_builder, error_code)
        fail_builder.unreachable()  # type: ignore
        # 该 CFG 块的终止符改落在 ok 块——phi 的入边块标签须随之映射
        self.__continuations[self.__current_cfg_block] = ok_block.name
        self.__builder = ir.IRBuilder(ok_block)

    def __emit_guarded(
        self,
        guard: ir.Value,
        compute: object,
        result_type: ir.Type | None = None,
    ) -> ir.Value:
        """短路守卫:guard 真 → compute(由调用方提供闭包,在新块中),假 → false。

        用于 live 的 null 短路(lock_ptr = 0 时短路为假,不读地址 0
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
        phi_type: ir.Type = result_type if result_type is not None else ir.IntType(1)  # type: ignore
        phi = merge_builder.phi(phi_type)  # type: ignore
        phi.add_incoming(then_val, then_block)  # type: ignore
        phi.add_incoming(ir.Constant(phi_type, 0), else_block)  # type: ignore
        self.__builder = merge_builder
        return phi

    def __emit_either(
        self,
        cond: ir.Value,
        then_fn: object,
        else_fn: object,
        result_type: ir.Type,
    ) -> ir.Value:
        """二选一求值:cond 真走 then_fn(新块), 假走 else_fn(新块), phi 合并。

        与 __emit_guarded 的区别是 else 分支也能产出值(锁表发放: 自由链非空时内联
        弹出, 为空时才调用运行时的 bump 入口)——两条路径都必须按需执行, 不能都求值。
        """
        seq = self.__check_seq
        self.__check_seq += 1
        then_block = self.__func.new_block(self.__split_block_name("g", "then", seq))
        else_block = self.__func.new_block(self.__split_block_name("g", "else", seq))
        merge_block = self.__func.new_block(self.__split_block_name("g", "merge", seq))
        self.__builder.cbranch(cond, then_block, else_block)  # type: ignore
        then_builder = ir.IRBuilder(then_block)
        then_val = then_fn(then_builder)  # type: ignore[operator]
        then_builder.branch(merge_block)  # type: ignore
        else_builder = ir.IRBuilder(else_block)
        else_val = else_fn(else_builder)  # type: ignore[operator]
        else_builder.branch(merge_block)  # type: ignore
        merge_builder = ir.IRBuilder(merge_block)
        phi = merge_builder.phi(result_type)  # type: ignore
        phi.add_incoming(then_val, then_block)  # type: ignore
        phi.add_incoming(else_val, else_block)  # type: ignore
        self.__builder = merge_builder
        return phi


    def __extract_check_fields(self, ll_val: LLValue) -> tuple[ir.Value, ir.Value, ir.Value, ir.Value]:
        """一次提取 check 字段 bundle (data, word, index, size)——仅 PointerType(4 字段)。

        check_safe_access 用 bundle 一次取齐 live(data/word 重建锁槽)与
        in_bounds(index/size)两谓词所需字段, 避免各谓词重复 extract;
        RefType(2 字段, 无 index/size)不得经此。
        """
        ty = self.__type_ctx[ll_val.type_id]
        if not isinstance(ty, Type.PointerType):
            raise ValueError(f"check field bundle requires PointerType, got {type(ty).__name__}")
        data = self.__extract_fat_field(ll_val, IR.FAT_DATA).ir_val
        word = self.__extract_fat_field(ll_val, IR.FAT_WORD).ir_val
        index = self.__extract_fat_field(ll_val, IR.FAT_INDEX).ir_val
        size = self.__extract_fat_field(ll_val, IR.FAT_SIZE).ir_val
        return data, word, index, size

    def __check_live(self, word: ir.Value, data: ir.Value) -> LLValue:
        """live(p):锁表项 key 比较 ∧ word ≠ 0, 无分派、无窗口。

        锁槽地址只由 word 的 lock 下标给出; 表项里存的是同一个 32 位 key, 所以比较
        恒为"一次 32 位 load + 一次 32 位比较"。null(word = 0)用一次与运算短路为假,
        不读表项 0, 也不多一个基本块。
        """
        i32: ir.IntType = ir.IntType(32)  # type: ignore
        lock = self.__lock_of(self.__builder, word, data)
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
        return LLValue(type_id, ir.Constant.literal_struct([ptr, self.__literal_word(), length]))  # type: ignore

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

    def var_ptr(self, symbol_id: int, result: str, frame_word: LLValue | None = None, frame_key: LLValue | None = None, raw: bool = False) -> LLValue:
        alloca_ptr = self.__func.get_var_ptr(symbol_id)
        if raw or not self.__is_fat_type(alloca_ptr.type_id):
            # 惰性左值路径:裸取址(未取址左值)仅返回栈地址;raw 模式下
            # __is_fat_type 恒 False(既有行为),此处同样裸返回。
            result_val = alloca_ptr
        else:
            # 合成 ⟨a_x, frame word, 0, 1⟩(取址恒指向单个元素)
            data = LLValue(self.__type_ctx.alloc_pointer(self.__type_ctx.u8_id), alloca_ptr.ir_val)  # type: ignore
            if frame_word is None:
                word = LLValue(self.__type_ctx.u64_id, self.__literal_word())  # type: ignore
            else:
                word = LLValue(self.__type_ctx.u64_id, frame_word.ir_val)  # type: ignore
            result_val = self.__build_fat(
                data, word,
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

    def alloca_store(
        self,
        value: LLValue,
        result: str,
        frame_word: LLValue | None = None,
        frame_key: LLValue | None = None,
        raw: bool = False,
    ) -> None:
        alloca_val = self.alloca(value.type_id)
        self.store(value, alloca_val)
        if raw or not self.__is_fat_type(alloca_val.type_id):
            result_val = alloca_val
        else:
            data = LLValue(self.__type_ctx.alloc_pointer(self.__type_ctx.u8_id), alloca_val.ir_val)  # type: ignore
            if frame_word is None:
                raise ValueError("fat temporary alloca missing current frame lock")
            word = LLValue(self.__type_ctx.u64_id, frame_word.ir_val)  # type: ignore
            result_val = self.__build_fat(
                data,
                word,
                self.i64(0),
                self.i64(1),
                alloca_val.type_id,
            )
        self.__func.set_reg(result, result_val)

    def __constant_class_index(self, count_ir: ir.Value, elem_size: int) -> int | None:
        """元素数为编译期常量时返回尺寸类号; 否则返回 ``None`` (走通用分配入口)。"""
        if not isinstance(count_ir, ir.Constant):  # type: ignore
            return None
        count = count_ir.constant  # type: ignore
        if not isinstance(count, int) or count < 0:
            return None
        payload = count * elem_size
        if payload == 0:
            payload = 1  # 与运行时的空请求归一化一致
        return class_index_for_payload(payload)

    def malloc(self, type_id: int, size: LLValue, key: LLValue | None, result: str) -> LLValue:
        if self.__type_ctx.is_zst(type_id):
            ptr_type_id = self.__type_ctx.alloc_pointer(type_id)
            self.__func.set_reg(result, LLValue(ptr_type_id, ir.Constant(self.__ll_type_ctx.get_ll_type(ptr_type_id).ir_type, ir.Undefined)))  # type: ignore
            return LLValue(ptr_type_id, ir.Constant(self.__ll_type_ctx.get_ll_type(ptr_type_id).ir_type, ir.Undefined))  # type: ignore
        # Convert element count to byte count for allocation.
        # O-1 无回绕:元素数 n 与元素大小 |T| 的乘积不能回绕。胖态先卡 n ≤ 2^32-1 (R001),
        # 之后 n·|T| 必然落在 u64 内; 只有 raw 模式或元素大小 ≥ 2^32 的类型仍走 i128
        # 宽算 + total < 2^64 检查——否则纯 64 位乘法回绕(如 n=2^62+1、|T|=8 → 2^65 →
        # 小值)会令物理分配过小, 而 in_bounds 全部通过 → 越界访问逃过检查。
        elem_size = self.__ll_type_ctx.get_type_size(type_id)
        i128: ir.IntType = ir.IntType(128)  # type: ignore
        if not self.__raw_pointers and elem_size < (1 << 32):
            # 胖指针的 size 字段是 32 位元素数: 先卡元素数上限(超限报 R001),
            # 之后 元素数 × 元素大小 ≤ (2^32-1)^2 < 2^64 必然落在 u64 内 —— 这条上限
            # 检查蕴含原来的 i128 溢出检查, 因此热路径上不再需要宽整数乘法。
            self.__check_view_count(size, "vcap")
            payload_ir = self.__builder.mul(size.ir_val, ir.Constant(ir.IntType(64), elem_size))  # type: ignore
        else:
            # raw 模式无 32 位 size 字段; 元素大小 ≥ 2^32 的类型极罕见:
            # 两种情况都保留 O-1 的 i128 溢出检查(raw 下是防御性检查)。
            if not self.__raw_pointers:
                self.__check_view_count(size, "vcap")
            size128 = self.__builder.zext(size.ir_val, i128)  # type: ignore
            payload128 = self.__builder.mul(size128, ir.Constant(i128, elem_size))  # type: ignore
            total128 = payload128
            if not self.__raw_pointers:
                # 块 = 堆块头 + 负载;块头首字为锁槽。
                # raw 模式无锁头(块 = 负载,data = 块基址)。
                total128 = self.__builder.add(
                    total128, ir.Constant(i128, IR.BlockHeader.BYTES)  # type: ignore
                )
            fits = self.__builder.icmp_unsigned("<", total128, ir.Constant(i128, 1 << 64))  # type: ignore
            self.__emit_check(LLValue(self.__type_ctx.bool_id, fits), RuntimeErrorCode.R001, "mof")
            payload_ir = self.__builder.trunc(payload128, ir.IntType(64))  # type: ignore
        zero = ir.Constant(ir.IntType(64), 0)  # type: ignore
        one = ir.Constant(ir.IntType(64), 1)  # type: ignore
        has_size = self.__builder.icmp_unsigned("!=", payload_ir, zero)  # type: ignore
        normalized_size = self.__builder.select(has_size, payload_ir, one)  # type: ignore
        payload = LLValue(self.__type_ctx.u64_id, normalized_size)  # type: ignore
        ptr_type_id = self.__type_ctx.alloc_pointer(type_id)
        if self.__raw_pointers:
            raw = self.__call_intrinsic(IntrinsicKind.Malloc, [payload])
            nonnull = self.__builder.icmp_signed("!=", raw.ir_val, ir.Constant(raw.ir_val.type, None))  # type: ignore
            self.__emit_check(LLValue(self.__type_ctx.bool_id, nonnull), RuntimeErrorCode.R002, "malloc-null")
            # raw 模式:data = 块基址,直接返回裸指针(无锁头偏移、无 5 字段聚合)。
            # malloc intrinsic 返回的 i8* 重新定型成 raw 指针(opaque), 值类型随后由
            # 使用点的 typ=/source_etype= 给出。
            typed = self.__bitcast(raw.ir_val, self.__ll_type_ctx.get_ll_type(ptr_type_id).ir_type)  # type: ignore
            result_val = LLValue(ptr_type_id, typed)  # type: ignore
            self.__func.set_reg(result, result_val)
            return result_val
        # 元素数与元素大小都是编译期常量时, 直接传尺寸类号: 运行时不再按字节数查表.
        # 运行时取不到类号 (请求大于最大类) 或元素数非常量时走通用入口.
        constant_class = self.__constant_class_index(size.ir_val, elem_size)
        if constant_class is None:
            block_ir = self.__builder.call(self.__module.get_pool_alloc(), [payload.ir_val])  # type: ignore
        else:
            block_ir = self.__builder.call(
                self.__module.get_pool_alloc_class(),
                [ir.Constant(ir.IntType(32), constant_class)],  # type: ignore
            )  # type: ignore
        block_base = LLValue(ptr_type_id, block_ir)  # type: ignore
        # 块头 {extent:u32 @0, pad:u32 @4} = 8 B; 身份在锁表项里(变体 B)。
        # 发放全部在发射的 IR 里完成(自由链非空 → 弹出下标; 为空 → 调 bump 入口),
        # 因此表项的 key 写对 LLVM 可见: 刚分配内存上的 live 检查能折叠成真。
        i32: ir.IntType = ir.IntType(32)  # type: ignore
        i64: ir.IntType = ir.IntType(64)  # type: ignore
        anchor_lo32 = self.__builder.trunc(  # type: ignore
            self.__builder.add(  # type: ignore
                self.__builder.ptrtoint(block_base.ir_val, i64),  # type: ignore
                ir.Constant(i64, IR.BlockHeader.BYTES),  # type: ignore
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

        lock_index = self.__emit_either(has_free, take_fast, take_slow, i64)  # type: ignore
        entry = self.__builder.gep(table, [ir.Constant(i64, 0), lock_index], inbounds=True)  # type: ignore
        stored = self.__builder.load(entry, typ=i64)  # type: ignore
        next_key = self.__builder.and_(  # type: ignore
            self.__builder.add(  # type: ignore
                self.__builder.and_(stored, ir.Constant(i64, IR.KEY_MASK)),  # type: ignore
                ir.Constant(i64, 1),  # type: ignore
            ),
            ir.Constant(i64, IR.KEY_MASK),  # type: ignore
        )
        next_entry = self.__builder.or_(  # type: ignore
            next_key,
            self.__builder.shl(  # type: ignore
                self.__builder.zext(anchor_lo32, i64),  # type: ignore
                ir.Constant(i64, IR.WORD_KEY_SHIFT),  # type: ignore
            ),
        )
        self.__builder.store(next_entry, entry)  # type: ignore
        word_ir = self.__builder.or_(  # type: ignore
            lock_index,
            self.__builder.shl(next_key, ir.Constant(i64, IR.WORD_KEY_SHIFT)),  # type: ignore
        )
        extent_ptr = self.__builder.gep(
            block_base.ir_val,
            [ir.Constant(i64, IR.BlockHeader.EXTENT_OFFSET)],  # type: ignore
            inbounds=False,
            source_etype=ir.IntType(8),  # 块头按字节偏移索引
        )
        self.__builder.store(self.__builder.trunc(size.ir_val, i32), extent_ptr)  # type: ignore

        key_ir = word_ir
        data_ir = self.__builder.gep(
            block_base.ir_val,
            [ir.Constant(ir.IntType(64), IR.BlockHeader.BYTES)],  # type: ignore
            inbounds=False,
            source_etype=ir.IntType(8),  # 载荷区按字节偏移索引
        )
        data = LLValue(self.__type_ctx.alloc_pointer(self.__type_ctx.u8_id), data_ir)  # type: ignore
        word_val = LLValue(self.__type_ctx.u64_id, key_ir)  # type: ignore
        zero = LLValue(self.__type_ctx.u64_id, ir.Constant(ir.IntType(64), 0))  # type: ignore
        size_val = LLValue(self.__type_ctx.u64_id, size.ir_val)  # type: ignore
        result_val = self.__build_fat(data, word_val, zero, size_val, ptr_type_id)
        self.__func.set_reg(result, result_val)
        return result_val

    def delete(self, ptr: LLValue) -> None:
        if self.__type_ctx.is_zst(ptr.type_id):
            return  # freeing a ZST pointer is a no-op
        # 块进入稳定头空闲池,不向 libc 归还。
        if self.__is_fat(ptr):
            # 释放: 表项里的 anchor_lo32 + data 高 32 位还原负载锚, -BYTES 得块首
            # (分配器保证同 4 GiB 窗口); 表项 key +1 使悬垂指针立刻失配, 下标回自由链。
            i32: ir.IntType = ir.IntType(32)  # type: ignore
            i64: ir.IntType = ir.IntType(64)  # type: ignore
            data = self.__extract_fat_field(ptr, IR.FAT_DATA)
            word = self.__extract_fat_field(ptr, IR.FAT_WORD).ir_val
            entry = self.__lock_of(self.__builder, word, data.ir_val)
            anchor_ptr = self.__builder.gep(  # type: ignore
                entry,
                [ir.Constant(i64, IR.LockEntry.ANCHOR_OFFSET)],  # type: ignore
                inbounds=False,
                source_etype=ir.IntType(8),
            )
            anchor = self.__builder.load(anchor_ptr, typ=i32)  # type: ignore
            payload_int = self.__builder.or_(  # type: ignore
                self.__builder.and_(  # type: ignore
                    self.__builder.ptrtoint(data.ir_val, i64),  # type: ignore
                    ir.Constant(i64, IR.WINDOW_MASK),  # type: ignore
                ),
                self.__builder.zext(anchor, i64),  # type: ignore
            )
            block = self.__builder.inttoptr(  # type: ignore
                self.__builder.sub(payload_int, ir.Constant(i64, IR.BlockHeader.BYTES)),  # type: ignore
                self.__ll_type_ctx.ptr_type,
            )
            self.__builder.call(self.__module.get_lock_release(), [word])  # type: ignore
            self.__builder.call(self.__module.get_pool_release(), [block])  # type: ignore
            return
        i8_ptr_type_id = self.__type_ctx.alloc_pointer(self.__type_ctx.u8_id)
        if isinstance(self.__type_ctx[ptr.type_id], Type.SliceType):
            # raw 模式(del-view)slice 为 {T*, u64} 聚合:整块释放须取 data
            # 字段(0)——直接 bitcast 聚合为指针是非法 IR。T*/T& 在 raw 模式
            # 为裸 8B 指针,不受影响。
            data = self.__builder.extract_value(ptr.ir_val, 0)  # type: ignore
            self.__call_intrinsic(IntrinsicKind.Free, [LLValue(i8_ptr_type_id, data)])  # type: ignore
            return
        self.__call_intrinsic(IntrinsicKind.Free, [LLValue(i8_ptr_type_id, ptr.ir_val)])  # type: ignore

    # -- fat-pointer mechanism nodes (LLVM 层) --

    def gen_key(self, result: str) -> None:
        self.__func.set_reg(result, self.__gen_key_value())

    def acquire_frame_lock(self, key: LLValue | None, result: str) -> None:
        """在固定地址的独立影子栈上 push 一个帧锁槽, 槽里写帧 word。

        帧 word = ⟨KIND_FRAME | id:42 | depth:20⟩: id 来自帧键(全局单调计数器),
        depth 是槽位下标。节点结果是该 word(指针携带的就是它); 槽地址记在
        __frame_lock_slot_name 里供返回路径写 SENTINEL。热路径只有一次深度检查、
        一次 GEP 与两次 store; 槽位先写 word, 再发布新深度, 且在任何用户语句之前完成。
        """
        if key is None:
            # 帧锁节点可能先于它的 GenKey 被翻译(两者都插在入口), 这里就地取键。
            key = self.__gen_key_value()
        depth_ptr = self.__module.get_frame_lock_depth()
        depth = self.__builder.load(depth_ptr, name="frame.depth")  # type: ignore
        available = self.__builder.icmp_unsigned(
            "<",
            depth,
            ir.Constant(ir.IntType(64), IR.FrameLockArena.SLOTS),  # type: ignore
        )
        self.__emit_check(
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
        frame_key = self.__builder.and_(key.ir_val, ir.Constant(i64, IR.KEY_MASK))  # type: ignore
        # word = ⟨key:32 | lock:32⟩, lock = 影子栈深度(锁表下标)
        frame_word: ir.Value = self.__builder.or_(  # type: ignore
            self.__builder.shl(frame_key, ir.Constant(i64, IR.WORD_KEY_SHIFT)),  # type: ignore
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
        self.__func.set_reg(result, LLValue(self.__type_ctx.u64_id, frame_word))

    def write_lock_slot(self, lock_ptr: LLValue, value: LLValue) -> None:
        """μ⟨lock_ptr⟩ := value(SENTINEL / 3.7.1 帧锁写键)。

        指针恒非空,锁槽恒可写(空容器持真实堆块)。
        """
        raw = self.__fat_data(lock_ptr).ir_val
        self.__builder.store(value.ir_val, raw)  # type: ignore

    def check_safe_access(self, ptr: LLValue, live: bool = True) -> None:
        """safe_access(p,1) = live(p) ∧ in_bounds(p,1)。

        ``live=False``:调用点已确认该指针的锁槽恒等于其键(函数帧内取址),
        时序项恒真,只发射空间项;错误码与失败条件(排除恒真项后)不变。
        """
        if not self.__is_fat(ptr):
            return
        if not live:
            index = self.__extract_fat_field(ptr, IR.FAT_INDEX).ir_val
            size = self.__extract_fat_field(ptr, IR.FAT_SIZE).ir_val
            self.__emit_check(self.__check_in_bounds_cond(index, size), RuntimeErrorCode.S002, "safe")
            return
        data, word, index, size = self.__extract_check_fields(ptr)
        in_bounds = self.__check_in_bounds_cond(index, size)
        cond = self.__check_live_and(word, data, in_bounds.ir_val)
        self.__emit_check(LLValue(self.__type_ctx.bool_id, cond), RuntimeErrorCode.S002, "safe")

    def check_view_access(self, view: LLValue, live: bool = True) -> None:
        """Validate a slice/str before passing its span to a syscall."""
        if not self.__is_fat(view):
            return
        view_type = self.__type_ctx[view.type_id]
        if not isinstance(view_type, (Type.SliceType, Type.StrType)):
            return

        data = self.__extract_fat_field(view, IR.SLICE_DATA).ir_val
        word = self.__extract_fat_field(view, IR.SLICE_WORD).ir_val
        size = self.__extract_fat_field(view, IR.SLICE_SIZE).ir_val
        lock = self.__lock_of(self.__builder, word, data)
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
        # 变体 B: kind 由锁表下标区间给出(堆区间 ≥ HEAP_LOCK_BASE); 负载锚由表项
        # anchor_lo32 + data 高 32 位还原, 块首 = 锚 - BYTES(分配器保证同 4 GiB 窗口)。
        index = self.__builder.and_(word, ir.Constant(ir.IntType(64), IR.LOCK_MASK))  # type: ignore
        heap = self.__builder.icmp_unsigned(  # type: ignore
            ">=", index, ir.Constant(ir.IntType(64), IR.HEAP_LOCK_BASE)  # type: ignore
        )
        header_guard: ir.Value = self.__builder.and_(heap, live_ok.ir_val)  # type: ignore
        anchor = self.__load_anchor(lock, header_guard)
        payload_int = self.__builder.or_(  # type: ignore
            self.__builder.and_(  # type: ignore
                self.__builder.ptrtoint(data, ir.IntType(64)),  # type: ignore
                ir.Constant(ir.IntType(64), IR.WINDOW_MASK),  # type: ignore
            ),
            anchor,
        )
        block_int: ir.Value = self.__builder.inttoptr(  # type: ignore
            self.__builder.sub(payload_int, ir.Constant(ir.IntType(64), IR.BlockHeader.BYTES)),  # type: ignore
            self.__ll_type_ctx.ptr_type,
        )
        active_size = self.__load_active_size(block_int, header_guard)

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
        self.__emit_check(LLValue(self.__type_ctx.bool_id, cond), RuntimeErrorCode.S002, "view")

    def check_in_bounds(self, ptr: LLValue) -> None:
        """in_bounds(p_s,1)(重锚定前提)。"""
        if not self.__is_fat(ptr):
            return
        index = self.__extract_fat_field(ptr, IR.FAT_INDEX).ir_val
        size = self.__extract_fat_field(ptr, IR.FAT_SIZE).ir_val
        self.__emit_check(self.__check_in_bounds_cond(index, size), RuntimeErrorCode.S001, "ib")

    def check_slice_nonempty(self, ptr: LLValue) -> None:
        """Establish the one-element origin invariant for ``T[] -> T&``."""
        if not self.__is_fat(ptr):
            return
        size = self.__extract_fat_field(ptr, IR.SLICE_SIZE).ir_val
        nonempty = self.__builder.icmp_unsigned(
            ">", size, ir.Constant(ir.IntType(64), 0)  # type: ignore
        )
        self.__emit_check(LLValue(self.__type_ctx.bool_id, nonempty), RuntimeErrorCode.S007, "sref")

    def check_ref_access(self, ptr: LLValue) -> None:
        """T& 引用访问前检:仅 live(免 in_bounds,tiered-pointers)。

        引用无 index/size(3 字段 ⟨data,lock_ptr,key⟩),无越界概念;
        live = 锁槽键比较(含 null 短路)。live 失败 → 报告 S003。
        """
        if not self.__is_fat(ptr):
            return
        data = self.__extract_fat_field(ptr, IR.FAT_DATA).ir_val
        word = self.__extract_fat_field(ptr, IR.FAT_WORD).ir_val
        cond = self.__check_live(word, data)
        self.__emit_check(cond, RuntimeErrorCode.S003, "ref")

    def check_element_arith(self, base: LLValue, offset: LLValue) -> None:
        """良构检查:0 ≤ index+offset ≤ size;u64 同型化(回绕检测 + 上界比较)。

        指针算术本身不访问内存，因此这里与 PtrDiff/PtrCmp 一样不检查
        allocation live 状态；后续解引用或外部 I/O 在各自访问边界检查 live。

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
        if not self.__is_fat(base):
            return
        index = self.__extract_fat_field(base, IR.FAT_INDEX).ir_val
        size = self.__extract_fat_field(base, IR.FAT_SIZE).ir_val
        off64 = self.__bitcast(offset.ir_val, ir.IntType(64))  # type: ignore
        sum = self.__builder.add(index, off64)  # type: ignore
        no_wrap = self.__builder.icmp_unsigned(">=", sum, index)  # type: ignore
        in_range = self.__builder.icmp_unsigned("<=", sum, size)  # type: ignore
        cond: ir.Value = self.__builder.and_(no_wrap, in_range)  # type: ignore
        self.__emit_check(LLValue(self.__type_ctx.bool_id, cond), RuntimeErrorCode.S004, "elarith")

    def check_element_access(self, base: LLValue, offset: LLValue, ptr: LLValue, live: bool = True) -> None:
        """合并检查:ElementArith→InBounds→SafeAccess 合取谓词(检查合并优化)。

        派生链 elem = base + offset → f = elem.field → 访问 f 的三重检查合并:
        良构(elem)(u64 同型化:回绕检测 + 上界比较)∧ in_bounds(elem,1)
        (one-past-end 的 elem 取字段时报告安全错误)∧ live(elem)(
        SafeAccess 的 live 项;in_bounds(f,1) 对重锚定字段指针恒真、
        live(f)=live(elem) 由锁字段继承)。禁止丢 no-wrap/live 任一子项。
        LLVM 层 发射。
        """
        if not (self.__is_fat(base) and self.__is_fat(ptr)):
            return
        # ElementArith 部分(同 check_element_arith 的 u64 同型化:
        # 回绕检测 no_wrap = icmp uge sum, index + 上界比较 in_range =
        # icmp ule sum, size;语义等价性论证见 check_element_arith 注释)
        index = self.__extract_fat_field(base, IR.FAT_INDEX).ir_val
        size = self.__extract_fat_field(base, IR.FAT_SIZE).ir_val
        off64 = self.__bitcast(offset.ir_val, ir.IntType(64))  # type: ignore
        sum = self.__builder.add(index, off64)  # type: ignore
        no_wrap = self.__builder.icmp_unsigned(">=", sum, index)  # type: ignore
        in_range = self.__builder.icmp_unsigned("<=", sum, size)  # type: ignore
        elarith_cond: ir.Value = self.__builder.and_(no_wrap, in_range)  # type: ignore
        # InBounds 部分:elem.index < elem.size
        e_index = self.__extract_fat_field(ptr, IR.FAT_INDEX).ir_val
        e_size = self.__extract_fat_field(ptr, IR.FAT_SIZE).ir_val
        ib_cond = self.__builder.icmp_unsigned("<", e_index, e_size)  # type: ignore
        # live 部分:锁槽键比较,含 null 短路(帧内 elem 恒真时不再发射)
        if live:
            ev_data = self.__extract_fat_field(ptr, IR.FAT_DATA).ir_val
            ev_word = self.__extract_fat_field(ptr, IR.FAT_WORD).ir_val
            live_ok = self.__check_live(ev_word, ev_data)
            cond: ir.Value = self.__builder.and_(self.__builder.and_(elarith_cond, ib_cond), live_ok.ir_val)  # type: ignore
        else:
            cond = self.__builder.and_(elarith_cond, ib_cond)  # type: ignore
        self.__emit_check(LLValue(self.__type_ctx.bool_id, cond), RuntimeErrorCode.S002, "eacc")

    def check_raw_bounds(self, index: LLValue, length: int) -> None:
        """惰性左值路径裸数组越界检查:0 ≤ index < length(编译期长度)。

        未取址裸数组元素访问无胖元数据,单 unsigned 比较即达 fat 路径
        CheckElementArith + CheckSafeAccess 的组合越界语义(索引已 coerce u64)。
        """
        cond = self.__builder.icmp_unsigned("<", index.ir_val, ir.Constant(ir.IntType(64), length))  # type: ignore
        self.__emit_check(LLValue(self.__type_ctx.bool_id, cond), RuntimeErrorCode.S001, "rb")

    def check_ptrdiff(self, lhs: LLValue, rhs: LLValue) -> None:
        """前提:data 相等 + 良构(双方 index ≤ size)+ 差可表示。

        PtrDiff 不访问内存，故沿用指针算术策略，不检查 allocation live。
        """
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
        self.__emit_check(LLValue(self.__type_ctx.bool_id, cond), RuntimeErrorCode.S005, "ptrdiff")

    def check_ptr_cmp(self, lhs: LLValue, rhs: LLValue) -> None:
        """序比较前提:data 相等(跨对象序比较报告 S005)。

        PtrCmp 不访问内存，故沿用指针算术策略，不检查 allocation live。
        """
        if not (self.__is_fat(lhs) and self.__is_fat(rhs)):
            return
        data_l, _ = self.__cmp_fat_operands(lhs)
        data_r, _ = self.__cmp_fat_operands(rhs)
        cond = self.__builder.icmp_signed("==", data_l, data_r)  # type: ignore
        self.__emit_check(LLValue(self.__type_ctx.bool_id, cond), RuntimeErrorCode.S005, "ptrcmp")

    def check_delete(self, ptr: LLValue) -> None:
        """Validate and release a complete, live heap view.

        In addition to the original heap/live/base checks, compare the view's
        logical byte extent with the active allocation extent recorded in the
        block header.  A pool block may have a larger reusable capacity, so
        capacity alone cannot establish that a slice is the complete view.
        """
        if not self.__is_fat(ptr):
            return
        ptr_type = self.__type_ctx[ptr.type_id]
        if not isinstance(ptr_type, (Type.PointerType, Type.SliceType, Type.StrType, Type.RefType)):
            return
        data = self.__extract_fat_field(ptr, IR.FAT_DATA).ir_val
        word = self.__extract_fat_field(ptr, IR.FAT_WORD).ir_val
        index = self.__builder.and_(word, ir.Constant(ir.IntType(64), IR.LOCK_MASK))  # type: ignore
        heap_ok = self.__builder.icmp_unsigned(  # type: ignore
            ">=", index, ir.Constant(ir.IntType(64), IR.HEAP_LOCK_BASE)  # type: ignore
        )
        lock = self.__lock_of(self.__builder, word, data)
        live_ok = self.__check_live(word, data)
        header_guard: ir.Value = self.__builder.and_(heap_ok, live_ok.ir_val)  # type: ignore
        # 锚点判定: 表项 anchor_lo32 必须等于 data 低 32 位(重锚定指针立刻被拒)。
        anchor = self.__load_anchor(lock, header_guard)
        data_int = self.__builder.ptrtoint(data, ir.IntType(64))  # type: ignore
        raw_data_ok = self.__builder.icmp_unsigned(  # type: ignore
            "==",
            self.__builder.and_(data_int, ir.Constant(ir.IntType(64), IR.LOCK_MASK)),  # type: ignore
            anchor,
        )
        raw_cond: ir.Value = cast(ir.Value, raw_data_ok)
        if isinstance(ptr_type, Type.PointerType):
            ptr_index = self.__extract_fat_field(ptr, IR.FAT_INDEX).ir_val
            raw_index_ok = self.__builder.icmp_signed("==", ptr_index, ir.Constant(ir.IntType(64), 0))  # type: ignore
            raw_cond = self.__builder.and_(raw_data_ok, raw_index_ok)  # type: ignore
        payload_int = self.__builder.or_(  # type: ignore
            self.__builder.and_(data_int, ir.Constant(ir.IntType(64), IR.WINDOW_MASK)),  # type: ignore
            anchor,
        )
        block_int: ir.Value = self.__builder.inttoptr(  # type: ignore
            self.__builder.sub(payload_int, ir.Constant(ir.IntType(64), IR.BlockHeader.BYTES)),  # type: ignore
            self.__ll_type_ctx.ptr_type,
        )
        active_size = self.__load_active_size(block_int, header_guard)
        extent_ok = self.__delete_extent_ok(ptr, ptr_type, active_size)
        cond: ir.Value = self.__builder.and_(
            header_guard,
            self.__builder.and_(raw_cond, extent_ok),  # type: ignore
        )  # type: ignore
        self.__emit_check(LLValue(self.__type_ctx.bool_id, cond), RuntimeErrorCode.S006, "del")

    def __load_active_size(self, block: ir.Value, guard: ir.Value) -> ir.Value:
        """Load the current logical payload extent (元素数) from a guarded block header.

        块头里存元素数(u32); 调用方按指针元素大小折算字节再与视图跨度比较。
        """
        i64: ir.IntType = ir.IntType(64)  # type: ignore
        i32: ir.IntType = ir.IntType(32)  # type: ignore

        def load_header(builder: ir.IRBuilder) -> ir.Value:
            field = builder.gep(  # type: ignore
                block,
                [ir.Constant(i64, IR.BlockHeader.EXTENT_OFFSET)],  # type: ignore
                inbounds=False,
                source_etype=ir.IntType(8),  # 块头按字节偏移索引
            )
            return builder.zext(builder.load(field, typ=i32), i64)  # type: ignore

        return self.__emit_guarded(guard, load_header, ir.IntType(64))  # type: ignore

    def __load_anchor(self, entry: ir.Value, guard: ir.Value) -> ir.Value:
        """Load the payload-anchor low 32 bits from a guarded lock-table entry."""
        i64: ir.IntType = ir.IntType(64)  # type: ignore
        i32: ir.IntType = ir.IntType(32)  # type: ignore

        def load_anchor(builder: ir.IRBuilder) -> ir.Value:
            field = builder.gep(  # type: ignore
                entry,
                [ir.Constant(i64, IR.LockEntry.ANCHOR_OFFSET)],  # type: ignore
                inbounds=False,
                source_etype=ir.IntType(8),
            )
            return builder.zext(builder.load(field, typ=i32), i64)  # type: ignore

        return self.__emit_guarded(guard, load_anchor, ir.IntType(64))  # type: ignore

    def __delete_extent_ok(
        self,
        ptr: LLValue,
        ptr_type: Type.PointerType | Type.SliceType | Type.StrType | Type.RefType,
        active_size: ir.Value,
    ) -> ir.Value:
        """Check that *ptr* denotes exactly the active heap payload."""
        i128: ir.IntType = ir.IntType(128)  # type: ignore
        if isinstance(ptr_type, Type.PointerType):
            count = self.__extract_fat_field(ptr, IR.FAT_SIZE).ir_val
            element_type = ptr_type.pointee_type
        elif isinstance(ptr_type, (Type.SliceType, Type.StrType)):
            count = self.__extract_fat_field(ptr, IR.SLICE_SIZE).ir_val
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

    # -- memory --

    def load(self, ptr: LLValue, result: str) -> LLValue:
        ptr_type = self.__type_ctx[self.__type_ctx.resolve_aliases(ptr.type_id)]
        assert isinstance(ptr_type, (Type.PointerType, Type.RefType))
        pointee_type_id = self.__type_ctx.resolve_aliases(ptr_type.pointee_type)
        if self.__ll_type_ctx.is_zst(pointee_type_id):
            # Loading a zero-sized value yields nothing: emit no `load` and
            # bind no register (the result is never consumed).
            return self.undef(pointee_type_id)
        if self.__is_fat(ptr):
            addr = self.__fat_addr(ptr, pointee_type_id)
            ir_val = self.__builder.load(addr.ir_val, typ=self.__ll_type_ctx.get_ll_type(pointee_type_id).ir_type)  # type: ignore
            result_val = LLValue(pointee_type_id, ir_val)
        else:
            ir_val = self.__builder.load(ptr.ir_val, typ=self.__ll_type_ctx.get_ll_type(pointee_type_id).ir_type)  # type: ignore
            result_val = LLValue(pointee_type_id, ir_val)
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
        base_type = self.__type_ctx[self.__type_ctx.resolve_aliases(base.type_id)]
        if isinstance(base_type, (Type.PointerType, Type.RefType)):
            # 别名 (typedef) 必须解析到目标类型: 否则下面的字段游走认不出 StructType,
            # 结果类型会停在别名上 (load/store 会按错误的类型取值)。
            pointee_type_id = self.__type_ctx.resolve_aliases(base_type.pointee_type)
        else:
            pointee_type_id = self.__type_ctx.resolve_aliases(base.type_id)

        # If we are already pointing at ZST, every offset is meaningless —
        # return undef before touching the (empty-struct) LLVM value.
        if self.__ll_type_ctx.is_zst(pointee_type_id):
            return self.undef(self.__type_ctx.alloc_pointer(pointee_type_id))

        # opaque 指针的 GEP 必须显式给出 source element type; 有型指针 (alloca/全局)
        # 不能给: llvmlite 会把结果类型设成 base 类型而不走索引, 后续 store/load 就
        # 会类型不符。规则: base 是 opaque 才传 source_etype。
        source_ll = self.__ll_type_ctx.get_ll_type(pointee_type_id).ir_type

        # Walk sub-indices to find the final pointee.
        for idx in indices[1:]:
            ty = self.__type_ctx[self.__type_ctx.resolve_aliases(pointee_type_id)]
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
            # 重锚定:data' = addr_T(p_s,0) + δ,index'=0,size'=1,锁字段继承
            base_def = self.__type_ctx[base.type_id]
            element_ll = self.__ll_type_ctx.get_ll_type(base_def.pointee_type).ir_type  # type: ignore[union-attr]
            addr = self.__fat_addr(base, base_def.pointee_type)  # type: ignore
            idx_vals = [self.i32(i).ir_val for i in indices]
            addr_source = element_ll if addr.ir_val.type.is_opaque else None  # type: ignore
            field_addr = self.__builder.gep(addr.ir_val, idx_vals, inbounds=True, source_etype=addr_source)  # type: ignore
            field_ptr = LLValue(self.__type_ctx.alloc_pointer(self.__type_ctx.u8_id), field_addr)  # type: ignore
            if isinstance(base_def, Type.PointerType):
                # PointerType base:锁字段继承、完全不提取;直插 data=field_addr、
                # index=0、size=1(重锚定常量,非 base 原值)
                zero = LLValue(self.__type_ctx.u64_id, ir.Constant(ir.IntType(64), 0))  # type: ignore
                one = LLValue(self.__type_ctx.u64_id, ir.Constant(ir.IntType(64), 1))  # type: ignore
                ir_val = self.__builder.insert_value(base.ir_val, field_ptr.ir_val, IR.FAT_DATA)  # type: ignore
                ir_val = self.__builder.insert_value(ir_val, self.__narrow_view_value(zero.ir_val, base.ir_val, IR.FAT_INDEX), IR.FAT_INDEX)  # type: ignore
                ir_val = self.__builder.insert_value(ir_val, self.__narrow_view_value(one.ir_val, base.ir_val, IR.FAT_SIZE), IR.FAT_SIZE)  # type: ignore
                result_val = LLValue(result_type_id, ir_val)
            else:
                # RefType(3 字段)/SliceType(4 字段):FAT_INDEX/FAT_SIZE 越界或语义错——
                # 保留 undef 重建路径(字段数由 __build_fat 类型分派)
                word = self.__extract_fat_field(base, IR.FAT_WORD)
                zero = LLValue(self.__type_ctx.u64_id, ir.Constant(ir.IntType(64), 0))  # type: ignore
                one = LLValue(self.__type_ctx.u64_id, ir.Constant(ir.IntType(64), 1))  # type: ignore
                result_val = self.__build_fat(field_ptr, word, zero, one, result_type_id)
        else:
            idx_vals = [self.i32(i).ir_val for i in indices]
            base_source = source_ll if base.ir_val.type.is_opaque else None  # type: ignore
            ir_val = self.__builder.gep(base.ir_val, idx_vals, inbounds=True, source_etype=base_source)  # type: ignore
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
            # 算术仅更新 index' = index + n(检查已保证良构)。
            # 单字段直插保留 data/lock/key/size 原值,无需 5 提取 + undef 重建。
            index = self.__extract_fat_field(base, IR.FAT_INDEX)
            new_index = LLValue(self.__type_ctx.u64_id,
                                self.__builder.add(index.ir_val, offset.ir_val))  # type: ignore
            # 良构检查 (CheckElementArith) 保证 index+n ≤ size ≤ MAX_VIEW_COUNT, 截断安全
            result_val = self.__insert_field_value(base, new_index, IR.FAT_INDEX)
        else:
            base_def = self.__type_ctx[base.type_id]
            element_type_id = base_def.pointee_type if isinstance(base_def, (Type.PointerType, Type.RefType)) else base.type_id
            element_ll = self.__ll_type_ctx.get_ll_type(element_type_id).ir_type
            element_source = element_ll if base.ir_val.type.is_opaque else None  # type: ignore
            ir_val = self.__builder.gep(base.ir_val, [offset.ir_val], inbounds=False, source_etype=element_source)  # type: ignore
            result_val = LLValue(base.type_id, ir_val)
        self.__func.set_reg(result, result_val)
        return result_val

    def ptr_diff(self, lhs: LLValue, rhs: LLValue, result: str) -> LLValue:
        """Pointer difference: ptr - ptr → i64 offset in elements。

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
            data = ll_val.ir_val
            return (data, ir.Constant(ir.IntType(64), 0))  # type: ignore
        raise ValueError(f"Unsupported pointer comparison operand: {type(ty).__name__}")

    def ptr_cmp(self, op: BinaryOperator, lhs: LLValue, rhs: LLValue, result: str) -> LLValue:
        """指针比较:相等按 (data, index) 二元组;
        序比较在 CheckPtrCmp 前提 下按 index。LLVM 无聚合 icmp → 字段提取。"""
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
        # Casts work on the types themselves, so an alias is read as what it
        # stands for: a `typedef Meter = u64` value casts like a `u64`.
        value_type_id = self.__type_ctx.resolve_aliases(value.type_id)
        to_type = self.__type_ctx.resolve_aliases(to_type)
        src = self.__type_ctx[value_type_id]
        dst = self.__type_ctx[to_type]
        dest_ll_type = self.__ll_type_ctx.get_ll_type(to_type).ir_type

        if self.__ll_type_ctx.is_zst(value_type_id) or self.__ll_type_ctx.is_zst(to_type):
            # A zero-length array still has a meaningful empty-slice view.  Its
            # value is erased to `{}`, so the ordinary bitcast would manufacture
            # an undef pointer; at O0 that can fail arithmetic checks, while raw
            # niche matching can mistake it for `None`.  Use the immortal literal
            # lock address as a non-dereferenceable data sentinel and carry a
            # zero-sized fat range.  Any attempted access is rejected by the
            # resulting size=0 bounds check; raw mode remains unchecked by contract.
            # A zero-length array receiver is commonly materialized as a
            # ``T&`` (or ``T*``) to the array before ``as_slice`` casts it to
            # ``T*``.  The type-level ZST erasure means the LLVM operand is
            # only ``undef {}``, so inspect the pointer-family pointee here as
            # well as a direct array value.
            empty_array_type: Type.ArrayType | None = None
            if isinstance(src, Type.ArrayType):
                empty_array_type = src
            elif isinstance(src, (Type.PointerType, Type.RefType)):
                pointee = self.__type_ctx[src.pointee_type]
                if isinstance(pointee, Type.ArrayType):
                    empty_array_type = pointee
            empty_length = False
            if empty_array_type is not None:
                length_ty = self.__type_ctx[empty_array_type.length]
                empty_length = isinstance(length_ty, Type.LiteralValueType) and length_ty.value == 0
            if empty_array_type is not None and empty_length and isinstance(dst, Type.PointerType) \
                    and not self.__type_ctx.is_zst(dst.pointee_type):
                lit_slot = self.__builder.gep(  # type: ignore
                    self.__module.get_lock_table(),
                    [ir.Constant(ir.IntType(64), 0), ir.Constant(ir.IntType(64), IR.LITERAL_LOCK_INDEX)],  # type: ignore
                    inbounds=True,
                )
                if self.__raw_pointers or raw:
                    lit_slot_ir = self.__builder.bitcast(lit_slot, ir.PointerType(ir.IntType(8)))  # type: ignore
                    ir_val = self.__bitcast(lit_slot_ir, dest_ll_type)  # type: ignore
                else:
                    data = LLValue(self.__type_ctx.alloc_pointer(self.__type_ctx.u8_id), lit_slot)  # type: ignore
                    word = LLValue(self.__type_ctx.u64_id, self.__literal_word())  # type: ignore
                    zero = LLValue(self.__type_ctx.u64_id, ir.Constant(ir.IntType(64), 0))  # type: ignore
                    ir_val = self.__build_fat(data, word, zero, zero, to_type).ir_val
            else:
                # Other ZST conversions remain erased; there is no addressable
                # object or metadata to preserve for them.
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
                # 惰性左值路径裸强转:位转换到裸目标指针。normal 模式下
                # *T 的 LLVM 型是 5 字段聚合,须手动取 pointee 的裸指针型
                # (裸数组退化 T[N]*→T* 的纯地址重贴)。
                pointee_ll = self.__ll_type_ctx.get_ll_type(dst.pointee_type).ir_type
                ir_val = self.__bitcast(value.ir_val, pointee_ll.as_pointer())  # type: ignore
            elif self.__is_fat(value):
                # 指针→指针:5 字段结构重贴;数组退化 T[m]*→T* 时重锚定 + size=m
                if isinstance(self.__type_ctx[src.pointee_type], Type.ArrayType):
                    arr_ty = self.__type_ctx[src.pointee_type]
                    assert isinstance(arr_ty, Type.ArrayType)
                    if arr_ty.element_type == dst.pointee_type:
                        eff = self.__fat_addr(value, src.pointee_type)
                        data = LLValue(self.__type_ctx.alloc_pointer(self.__type_ctx.u8_id), eff.ir_val)  # type: ignore
                        word = self.__extract_fat_field(value, IR.FAT_WORD)
                        zero = LLValue(self.__type_ctx.u64_id, ir.Constant(ir.IntType(64), 0))  # type: ignore
                        len_ty = self.__type_ctx[arr_ty.length]
                        assert isinstance(len_ty, Type.LiteralValueType)
                        self.__check_static_view_count(len_ty.value)
                        size = LLValue(self.__type_ctx.u64_id, ir.Constant(ir.IntType(64), len_ty.value))  # type: ignore
                        ir_val = self.__build_fat(data, word, zero, size, to_type).ir_val
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
                # 惰性左值路径:raw 强转(未取址裸数组退化)位转换,不合成胖值。
                arr_ty = self.__type_ctx[src.pointee_type]
                assert isinstance(arr_ty, Type.ArrayType)
                if arr_ty.element_type == dst.pointee_type:
                    data = LLValue(self.__type_ctx.alloc_pointer(self.__type_ctx.u8_id), value.ir_val)  # type: ignore
                    word = LLValue(self.__type_ctx.u64_id, self.__literal_word())  # type: ignore
                    zero = LLValue(self.__type_ctx.u64_id, ir.Constant(ir.IntType(64), 0))  # type: ignore
                    len_ty = self.__type_ctx[arr_ty.length]
                    assert isinstance(len_ty, Type.LiteralValueType)
                    size = LLValue(self.__type_ctx.u64_id, ir.Constant(ir.IntType(64), len_ty.value))  # type: ignore
                    ir_val = self.__build_fat(data, word, zero, size, to_type).ir_val
                else:
                    ir_val = self.__bitcast(value.ir_val, dest_ll_type)  # type: ignore
            else:
                ir_val = self.__bitcast(value.ir_val, dest_ll_type)  # type: ignore
        elif isinstance(src, Type.RefType) and isinstance(dst, Type.RefType):
            # T& → T&: 同为 3 字段布局(表示层),identity 重贴。
            ir_val = value.ir_val
        elif isinstance(src, Type.PointerType) and isinstance(dst, Type.RefType):
            # T* → T&: 有效地址折入 index·|T|(ref 无 index 字段),取 ⟨data', lock, key⟩。
            if self.__raw_pointers:
                # raw 模式:裸指针。数组退化 T[N]*→T& 时位转换为元素指针
                # (数组基址 = 首元素地址,与 fat 分支 __fat_addr 重锚定等价);
                # 标量/同型引用类型一致,恒 identity。
                src_pointee = self.__type_ctx[src.pointee_type]
                if isinstance(src_pointee, Type.ArrayType) and src_pointee.element_type == dst.pointee_type:
                    ir_val = self.__bitcast(value.ir_val, dest_ll_type)  # type: ignore
                else:
                    ir_val = value.ir_val
            elif self.__is_fat(value):
                eff = self.__fat_addr(value, src.pointee_type)
                data = LLValue(self.__type_ctx.alloc_pointer(self.__type_ctx.u8_id), eff.ir_val)  # type: ignore
                word = self.__extract_fat_field(value, IR.FAT_WORD)
                zero = LLValue(self.__type_ctx.u64_id, ir.Constant(ir.IntType(64), 0))  # type: ignore
                ir_val = self.__build_fat(data, word, zero, zero, to_type).ir_val
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
                    ir_val = self.__bitcast(value.ir_val, dest_ll_type)  # type: ignore
                else:
                    ir_val = value.ir_val
            elif self.__is_fat(value):
                data = self.__extract_fat_field(value, IR.FAT_DATA)
                word = self.__extract_fat_field(value, IR.FAT_WORD)
                zero = LLValue(self.__type_ctx.u64_id, ir.Constant(ir.IntType(64), 0))  # type: ignore
                src_pointee = self.__type_ctx[src.pointee_type]
                if isinstance(src_pointee, Type.ArrayType) and src_pointee.element_type == dst.pointee_type:
                    len_ty = self.__type_ctx[src_pointee.length]
                    assert isinstance(len_ty, Type.LiteralValueType)
                    self.__check_static_view_count(len_ty.value)
                    size = LLValue(self.__type_ctx.u64_id, ir.Constant(ir.IntType(64), len_ty.value))  # type: ignore
                else:
                    size = LLValue(self.__type_ctx.u64_id, ir.Constant(ir.IntType(64), 1))  # type: ignore
                ir_val = self.__build_fat(data, word, zero, size, to_type).ir_val
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
                word = self.__extract_fat_field(value, IR.FAT_WORD)
                size = self.__extract_fat_field(value, IR.FAT_SIZE)
                index = self.__extract_fat_field(value, IR.FAT_INDEX)
                remaining = LLValue(
                    self.__type_ctx.u64_id,
                    self.__builder.sub(size.ir_val, index.ir_val),  # type: ignore
                )
                zero = LLValue(self.__type_ctx.u64_id, ir.Constant(ir.IntType(64), 0))  # type: ignore
                ir_val = self.__build_fat(data, word, zero, remaining, to_type).ir_val
        elif isinstance(src, Type.SliceType) and isinstance(dst, Type.RefType):
            # T[] → T&: 取 4 字段切片 data/lock_ptr/key 合成 3 字段引用(真锁,非 lit lock)。
            if self.__raw_pointers:
                # raw 模式 slice {data, size}:引用 = data 地址(字段 0)。
                ir_val = self.__builder.extract_value(value.ir_val, 0)  # type: ignore
            else:
                data = LLValue(self.__type_ctx.alloc_pointer(self.__type_ctx.u8_id),
                               self.__builder.extract_value(value.ir_val, IR.FAT_DATA))  # type: ignore
                word = LLValue(self.__type_ctx.u64_id,
                               self.__builder.extract_value(value.ir_val, IR.FAT_WORD))  # type: ignore
                zero = LLValue(self.__type_ctx.u64_id, ir.Constant(ir.IntType(64), 0))  # type: ignore
                ir_val = self.__build_fat(data, word, zero, zero, to_type).ir_val
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
        if isinstance(base_type, (Type.SliceType, Type.StrType)) and index == 3:
            # 语义下标 3 = size:胖模式 3 字段 {data, word, size} → LLVM 字段 2,
            # raw 模式 2 字段 {data, size} → LLVM 字段 1。
            mapped = 1 if self.__raw_pointers else IR.SLICE_SIZE
            result_val = LLValue(field_type, self.__builder.extract_value(base.ir_val, mapped))  # type: ignore
            self.__func.set_reg(result, result_val)
            return result_val
        ir_val = self.__builder.extract_value(base.ir_val, index)  # type: ignore
        result_val = LLValue(field_type, ir_val)
        self.__func.set_reg(result, result_val)
        return result_val

    def __slice_ptr_fat(self, base: LLValue, ptr_type_id: int) -> LLValue:
        """把 4 字段 slice/str 的 data 合成为 5 字段胖指针 ⟨data, lock_ptr, key, 0, size⟩。

        分级指针表示:slice/str 自身携带真实 lock_ptr/key(size 下标 3),直接继承
        (锁继承)——这是 T[]→T* 派生的锁继承来源。
        """
        data = LLValue(self.__type_ctx.alloc_pointer(self.__type_ctx.u8_id),
                       self.__builder.extract_value(base.ir_val, IR.SLICE_DATA))  # type: ignore
        word = LLValue(self.__type_ctx.u64_id,
                       self.__builder.extract_value(base.ir_val, IR.SLICE_WORD))  # type: ignore
        size = LLValue(self.__type_ctx.u64_id,
                       self.__builder.extract_value(base.ir_val, IR.SLICE_SIZE))  # type: ignore
        zero = LLValue(self.__type_ctx.u64_id, ir.Constant(ir.IntType(64), 0))  # type: ignore
        # 长度进入 32 位 size 字段, 但不在这里检查: 视图长度都来自某个分配的容量,
        # 而分配元素数已在 malloc 处校验 ≤ MAX_VIEW_COUNT, 因此这里必然可表示。
        return self.__build_fat(data, word, zero, size, ptr_type_id)


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
        """把裸 8B 指针值合成 5 字段胖指针 ⟨data, e_f, k_f, 0, size⟩。

        用于 slice/str 边界:从 16B slice 取出的 ptr 字段是裸 8B 指针,落入
        {T*, u64} 形态聚合(SliceStruct 等)时补全元数据。锁用全局字面量锁槽
        (恒 live)——合成点常在薄包装内,帧锁会随返回失效并逃逸到调用者死栈帧。
        """
        data = LLValue(self.__type_ctx.alloc_pointer(self.__type_ctx.u8_id), value.ir_val)  # type: ignore
        word = LLValue(self.__type_ctx.u64_id, self.__literal_word())  # type: ignore
        zero = LLValue(self.__type_ctx.u64_id, ir.Constant(ir.IntType(64), 0))  # type: ignore
        if not self.__raw_pointers:
            self.__check_view_count(size, "vcap")
        return self.__build_fat(data, word, zero, size, value.type_id)

    def __build_aggregate(self, type_id: int, field_values: list[LLValue]) -> LLValue:
        """Build an aggregate value by inserting each field value at its index."""
        # An all-zero-sized aggregate erases to `{}` — there is nothing to
        # build, and inserting into `{}` would be an out-of-range index.
        if self.__ll_type_ctx.is_zst(type_id):
            return self.undef(type_id)
        type_def = self.__type_ctx[type_id]
        if isinstance(type_def, (Type.SliceType, Type.StrType)):
            # 分级指针表示:slice/str 4 字段 {data, lock_ptr, key, size}(raw 2 字段)。
            # HIR 供给 {ptr, len}:正常模式 data = ptr 有效地址,lock/key 继承自
            # ptr 的胖元数据(T* coerce 构造,锁继承),size = 显式 len。
            elem_type = type_def.element_type if isinstance(type_def, Type.SliceType) else self.__type_ctx.u8_id
            raw = self.__fat_addr(field_values[0], elem_type)
            raw_ir = raw.ir_val
            if self.__raw_pointers:
                val = self.undef(type_id)
                val = self.insert_value(val, LLValue(self.__type_ctx.alloc_pointer(elem_type), raw_ir), 0)  # type: ignore
                val = self.insert_value(val, field_values[1], 1)
                return val
            data = LLValue(self.__type_ctx.alloc_pointer(elem_type), raw_ir)  # type: ignore
            word = self.__extract_fat_field(field_values[0], IR.FAT_WORD)
            # 切片 size 是 64 位, 不截断; 长度都来自分配容量, 而分配元素数已在
            # malloc 处校验 ≤ MAX_VIEW_COUNT, 因此进入 32 位指针字段时必然可表示。
            return self.__build_fat(data, word, self.i64(0), field_values[1], type_id)
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
            payload_typed_ptr = self.__bitcast(payload_arr_ptr, payload_ptr_ll)  # type: ignore
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
        matched_ty = self.__type_ctx[matched.type_id]
        enum_type_id = matched_ty.pointee_type if isinstance(matched_ty, Type.PointerType) else matched.type_id
        base_ptr = self.__fat_addr(matched, enum_type_id) if self.__is_fat(matched) else matched

        enum_ll = self.__ll_type_ctx.get_ll_type(enum_type_id).ir_type
        if self.__ll_type_ctx.is_niche_enum(enum_type_id):
            for field_index, symbol_id in fields:
                if field_index != 0:
                    continue
                field_ll = self.__ll_type_ctx.get_ll_type(
                    self.__type_ctx.get_struct_fields(payload_type_id)[field_index].type_id
                ).ir_type
                field_value = self.__builder.load(base_ptr.ir_val, typ=field_ll)  # type: ignore
                alloca_ptr = self.__func.get_var_ptr(symbol_id)
                self.__builder.store(field_value, alloca_ptr.ir_val)  # type: ignore
            return

        if self.__type_ctx.is_zst(payload_type_id):
            return  # ZST payload: nothing to unpack
        payload_fields = self.__type_ctx.get_struct_fields(payload_type_id)

        payload_ll = self.__ll_type_ctx.get_ll_type(payload_type_id).ir_type
        payload = self.__builder.gep(base_ptr.ir_val, [self.i32(0).ir_val, self.i32(1).ir_val], inbounds=True, source_etype=enum_ll)  # type: ignore
        for field_index, symbol_id in fields:
            if field_index >= len(payload_fields):
                break
            field_ll = self.__ll_type_ctx.get_ll_type(payload_fields[field_index].type_id).ir_type
            # payload 槽是字节数组: 步进必须按 payload 结构体算, 故显式给 source_etype
            # (llvmlite 对指针的 bitcast 是 no-op, 拿不到有型指针; 值类型由 typ= 给出)。
            field_ptr = self.__builder.gep(payload, [self.i32(0).ir_val, self.i32(field_index).ir_val], inbounds=True, source_etype=payload_ll)  # type: ignore
            field_value = self.__builder.load(field_ptr, typ=field_ll)  # type: ignore
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
        matched_ty = self.__type_ctx[matched.type_id]
        enum_type_id = matched_ty.pointee_type if isinstance(matched_ty, (Type.PointerType, Type.RefType)) else matched.type_id
        base_ptr = self.__fat_addr(matched, enum_type_id) if self.__is_fat(matched) else matched
        payload_fields = self.__type_ctx.get_struct_fields(payload_type_id)
        enum_ll = self.__ll_type_ctx.get_ll_type(enum_type_id).ir_type

        if self.__ll_type_ctx.is_niche_enum(enum_type_id):
            for field_index, symbol_id in fields:
                if field_index != 0:
                    continue
                alloca_ptr = self.__func.get_var_ptr(symbol_id)
                field_ll = LLValue(self.__type_ctx.alloc_pointer(payload_fields[field_index].type_id), base_ptr.ir_val)  # type: ignore
                self.__builder.store(self.__promote_fat(field_ll).ir_val, alloca_ptr.ir_val)  # type: ignore
            return

        if self.__type_ctx.is_zst(payload_type_id):
            return  # ZST payload: no fields to unpack

        payload_ll = self.__ll_type_ctx.get_ll_type(payload_type_id).ir_type
        payload = self.__builder.gep(base_ptr.ir_val, [self.i32(0).ir_val, self.i32(1).ir_val], inbounds=True, source_etype=enum_ll)  # type: ignore

        for field_index, symbol_id in fields:
            if field_index >= len(payload_fields):
                break
            field_ptr = self.__builder.gep(payload, [self.i32(0).ir_val, self.i32(field_index).ir_val], inbounds=True, source_etype=payload_ll)  # type: ignore
            alloca_ptr = self.__func.get_var_ptr(symbol_id)
            # &T 引用槽为 5 字段胖指针:裸字段地址补全元数据
            field_ll = LLValue(self.__type_ctx.alloc_pointer(payload_fields[field_index].type_id), field_ptr)  # type: ignore
            self.__builder.store(self.__promote_fat(field_ll).ir_val, alloca_ptr.ir_val)  # type: ignore

    # -- sys --

    def sys_write(self, fd: LLValue, buf: LLValue) -> None:
        self.__call_intrinsic(IntrinsicKind.Write, [
            fd, self.__extract_value_raw(buf, 0), self.__slice_len_field(buf),
        ])

    def mem_copy(self, dest: LLValue, src: LLValue, count: LLValue) -> None:
        """Byte-level copy: ``memcpy(dest, src, count)``.

        The YIAN ``@memcpy`` accepts any pointer pointee types — bitcast
        both to ``i8*`` for the C ``memcpy`` intrinsic.  Fat pointers are
        unwrapped to their ``data`` field first (LLVM 层).
        """
        dest_raw = LLValue(self.__type_ctx.alloc_pointer(self.__type_ctx.u8_id), self.__fat_data(dest).ir_val)  # type: ignore
        src_raw = LLValue(self.__type_ctx.alloc_pointer(self.__type_ctx.u8_id), self.__fat_data(src).ir_val)  # type: ignore
        self.__call_intrinsic(IntrinsicKind.MemCopy, [dest_raw, src_raw, count])

    def sys_read(self, fd: LLValue, buf: LLValue, result: str) -> None:
        buf_ptr = self.__extract_value_raw(buf, 0)
        buf_len = self.__slice_len_field(buf)
        bytes_read = self.__call_intrinsic(IntrinsicKind.Read, [
            fd, buf_ptr, buf_len,
        ])
        # construct str:胖 3 字段 {data, word, size}(raw 2 字段 {data, size});
        # 正常模式锁元数据(整字)继承自输入缓冲区 buf。
        str_ll_type = self.__ll_type_ctx.get_ll_type(self.__type_ctx.str_id).ir_type
        undef = ir.Constant(str_ll_type, ir.Undefined)  # type: ignore
        ir_val = self.__builder.insert_value(undef, buf_ptr.ir_val, 0)  # type: ignore
        if self.__raw_pointers:
            ir_val = self.__builder.insert_value(ir_val, bytes_read.ir_val, 1)  # type: ignore
        else:
            ir_val = self.__builder.insert_value(ir_val, self.__extract_value_raw(buf, 1).ir_val, 1)  # type: ignore
            ir_val = self.__builder.insert_value(ir_val, bytes_read.ir_val, 2)  # type: ignore
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

    def sqrt(self, value: LLValue, result: str) -> None:
        """``f64`` square root (hardware square root, same primitive as C ``sqrt``)."""
        raw = self.__call_intrinsic(IntrinsicKind.Sqrt, [value])
        self.__func.set_reg(result, raw)

    # -- process arguments / exit --

    def arg_count(self, result: str) -> None:
        """Load the validated C ``argc`` and expose it as ``u64``."""
        loaded = self.__builder.load(self.__module.argc_global)  # type: ignore
        extended = self.__builder.zext(loaded, ir.IntType(64))  # type: ignore
        self.__func.set_reg(result, LLValue(self.__type_ctx.u64_id, extended))  # type: ignore

    def arg_bytes(self, index: LLValue, result: str) -> None:
        """Return one NUL-terminated C argument as a borrowed byte slice.

        The bounds and null checks are deliberately emitted here, independently
        of the normal fat-pointer checking switch: the wrapper's process ABI is
        trusted only after these two conditions have been established.
        """
        argc = self.__builder.load(self.__module.argc_global)  # type: ignore
        argc_u64 = self.__builder.zext(argc, ir.IntType(64))  # type: ignore
        in_range = self.__builder.icmp_unsigned("<", index.ir_val, argc_u64)  # type: ignore
        self.__emit_check(
            LLValue(self.__type_ctx.bool_id, in_range),
            RuntimeErrorCode.S001,
            "argv-index",
        )

        argv = self.__builder.load(self.__module.argv_global, typ=self.__ll_type_ctx.ptr_type)  # type: ignore
        slot = self.__builder.gep(argv, [index.ir_val], inbounds=False, source_etype=self.__ll_type_ctx.ptr_type)  # type: ignore
        ptr_ir = self.__builder.load(slot, typ=self.__ll_type_ctx.ptr_type)  # type: ignore
        ptr_type_id = self.__type_ctx.alloc_pointer(self.__type_ctx.u8_id)
        ptr = LLValue(ptr_type_id, ptr_ir)
        nonnull = self.__builder.icmp_unsigned(
            "!=", ptr_ir, ir.Constant(ptr_ir.type, None)  # type: ignore
        )
        self.__emit_check(
            LLValue(self.__type_ctx.bool_id, nonnull),
            RuntimeErrorCode.S002,
            "argv-null",
        )

        length = self.__call_intrinsic(IntrinsicKind.StrLen, [ptr])
        slice_type_id = self.__type_ctx.alloc_slice(self.__type_ctx.u8_id)
        if self.__raw_pointers:
            value = self.undef(slice_type_id)
            value = self.insert_value(value, ptr, 0)
            value = self.insert_value(value, length, 1)
        else:
            value = self.__build_fat(
                ptr,
                LLValue(self.__type_ctx.u64_id, self.__env_word()),  # type: ignore
                self.i64(0),
                length,
                slice_type_id,
            )
        self.__func.set_reg(result, value)

    def process_exit(self, code: LLValue) -> None:
        """Terminate immediately with the caller-selected process status."""
        self.__call_intrinsic(IntrinsicKind.ImmediateExit, [code])
        self.__builder.unreachable()

    # ------------------------------------------------------------------
    # terminators
    # ------------------------------------------------------------------

    def set_frame_lock(self, e_f: IR.Value) -> None:
        """标记函数已取得帧锁:全部返回路径写 SENTINEL 并 pop。

        帧 word 由 acquire_frame_lock 记在 __frame_lock_slot_name(槽地址)里;
        这里只确认"已取得", 参数是帧 word(不是槽地址), 因此不再记录它。
        """
        assert isinstance(e_f, IR.Reg), "帧锁必须为入口寄存器"
        self.__frame_lock_acquired = True

    def __release_frame_lock(self) -> None:
        """帧退出写 SENTINEL,再从稳定影子栈 pop。

        槽位保持映射且永不成为用户数据;后续 push 复用该地址时会在
        任何用户步之前写入新键。编译器生成的函数进退严格 LIFO,
        因此退出仅需递减深度,无需空闲链或动态槽位检索。
        """
        if not self.__frame_lock_acquired or self.__frame_lock_slot_name is None:
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

    def runtime_fail(self, code: RuntimeErrorCode) -> None:
        """Emit a canonical runtime diagnostic and terminate the block."""
        self.__module.emit_runtime_fail(self.__builder, code)
        self.__builder.unreachable()

    def panic(self, msg: LLValue) -> None:
        self.__builder.call(
            self.__module.get_panic(),
            [self.__extract_value_raw(msg, 0).ir_val, self.__slice_len_field(msg).ir_val],
        )
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
            case IntrinsicKind.Free | IntrinsicKind.ImmediateExit | IntrinsicKind.MemCopy:
                return self.__type_ctx.void_id
            case IntrinsicKind.Write | IntrinsicKind.Read:
                return self.__type_ctx.u64_id
            case IntrinsicKind.Open | IntrinsicKind.Close:
                return self.__type_ctx.i32_id
            case IntrinsicKind.Sqrt:
                return self.__type_ctx.f64_id
            case IntrinsicKind.StrLen:
                return self.__type_ctx.u64_id
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
            # 胖指针聚合比较兜底:LLVM 无聚合
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
                # raw 模式:两侧都是有型指针且 pointee 不同时统一 bitcast i8*
                # (icmp 要求同型操作数;opaque 指针彼此相等,不会走到这里)。
                i8_ptr = ir.PointerType(ir.IntType(8))  # type: ignore
                lhs = self.__bitcast(lhs, i8_ptr)  # type: ignore
                rhs = self.__bitcast(rhs, i8_ptr)  # type: ignore
            ty = self.__type_ctx[type_id]
            if isinstance(ty, Type.IntType) and not ty.signed:
                return self.__builder.icmp_unsigned(predicate, lhs, rhs)  # type: ignore
            return self.__builder.icmp_signed(predicate, lhs, rhs)  # type: ignore
        return self.__builder.fcmp_ordered(predicate, lhs, rhs)  # type: ignore

    def __fat_value_pair(self, v: ir.Value) -> tuple[ir.Value, ir.Value]:
        """ir.Value 级别的 (data, second) 字段对(分级指针表示分派)。

        第二字段按结构:5 字段指针取 index、4 字段 slice 取 size、3 字段 ref 取 key、
        raw 2 字段 slice 取 size;裸指针按 (自身, 0)。
        """
        if isinstance(v.type, ir.LiteralStructType):  # type: ignore
            field_count = len(v.type.elements)  # type: ignore
            if field_count >= 4:
                second_idx = IR.FAT_INDEX      # 胖指针: 比较 (data, index)
            elif field_count == 3:
                second_idx = IR.SLICE_SIZE     # 切片: 比较 (data, size)
            else:
                second_idx = IR.REF_WORD       # 引用: 比较 (data, word)
            second = self.__builder.extract_value(v, second_idx)  # type: ignore
            if isinstance(second.type, ir.IntType) and second.type.width < 64:  # type: ignore
                second = self.__builder.zext(second, ir.IntType(64))  # type: ignore
            return (self.__builder.extract_value(v, IR.FAT_DATA), second)  # type: ignore
        return (v, ir.Constant(ir.IntType(64), 0))  # type: ignore

    def __cmp_fat_values(self, op: BinaryOperator, lhs: ir.Value, rhs: ir.Value) -> ir.Value:
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
        self.__emit_check(LLValue(self.__type_ctx.bool_id, data_ok), RuntimeErrorCode.S005, "ptrcmp")
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
