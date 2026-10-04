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
        """Test whether an LLVM value is a fat aggregate: pointer/slice 24 B, reference 16 B.

        LiteralStructType values of these three families count as fat values.
        Raw mode has no metadata (bare pointer / {T*,u64} / bare reference) and returns False.
        A bare pointer extracted from slice/str is not mistaken for a fat PointerType value.
        """
        return self.__pointers.is_fat(ll_val)

    def is_fat_type(self, type_id: int) -> bool:
        """Classify fat values by type_id: non-ZST PointerType has four fields,
        SliceType/StrType three, and RefType two. All are aggregate values.
        Raw mode returns False and uses bare 8 B pointers. CFG's is_fat_pointer
        corresponds specifically to PointerType's full-check path.
        """
        return self.__pointers.is_fat_type(type_id)

    def extract_fat_field(self, ll_val: LLValue, index: int) -> LLValue:
        """Extract aggregate fields, zero-extending integers to u64 and returning u8* pointers.

        FAT_*/SLICE_*/REF_* define indices. PointerType index/size count elements in 32 bits,
        with allocation capacities bounded by MAX_VIEW_COUNT; zero-extension preserves u64
        comparison/arithmetic semantics. word retains all 64 bits encoding lock and key.
        """
        return self.__pointers.extract_fat_field(ll_val, index)

    def insert_field_value(self, agg: LLValue, value: LLValue, index: int) -> LLValue:
        """Insert a field at its actual width, truncating 32-bit view fields.

        Only index/size narrow: lengths come from Malloc-bounded capacities, and arithmetic
        uses CheckElementArith to enforce <= size <= MAX_VIEW_COUNT, so truncation preserves semantics.
        """
        return self.__pointers.insert_field_value(agg, value, index)

    def narrow_view_value(self, ir_val: ir.Value, agg: ir.Value, index: int) -> ir.Value:
        """Narrow a constant view value to its field width for direct insertion when rebuilding undef."""
        return self.__pointers.narrow_view_value(ir_val, agg, index)

    @staticmethod
    def check_static_view_count(count: int) -> None:
        """Reject array decay when its compile-time length exceeds the 32-bit view limit."""
        PointerRepresentation.check_static_view_count(count)

    def check_view_count(self, value: LLValue, suffix: str) -> None:
        """Check allocation count <= MAX_VIEW_COUNT to prevent 32-bit length truncation.

        Emit once per heap allocation. View lengths derive from allocation capacities, so this bound
        makes subslice lengths, T* -> T[] remaining lengths, and converted views representable.
        """
        limit: ir.Value = ir.Constant(ir.IntType(64), ABI.MAX_VIEW_COUNT)  # type: ignore
        ok = self.__builder.icmp_unsigned("<=", value.ir_val, limit)  # type: ignore
        self.__core.flow.emit_check(LLValue(self.__type_ctx.bool_id, ok), RuntimeErrorCode.R001, suffix)

    def build_fat(self, data: LLValue, word: LLValue, index: LLValue, size: LLValue, type_id: int) -> LLValue:
        """Construct fat aggregates: PointerType <data,word,index,size>,
        SliceType/StrType <data,word,size>, RefType <data,word>.

        word carries the lock index and key, matching the slot (see lockmech).
        """
        return self.__pointers.build_fat(data, word, index, size, type_id)

    def lock_of(self, builder: ir.IRBuilder, word: ir.Value, data: ir.Value) -> ir.Value:
        """Resolve the lock-entry address from word's index, independent of data or slot kind.

        Address = @__secl_lock_table + lock*LOCK_ENTRY_BYTES. Index ranges encode kind without
        hot-path dispatch. The data argument retains a uniform call-site interface.
        """
        i32: ir.IntType = ir.IntType(32)  # type: ignore
        i64: ir.IntType = ir.IntType(64)  # type: ignore
        table = self.__module.get_lock_table()
        index = builder.zext(builder.trunc(word, i32), i64)  # type: ignore
        return builder.gep(table, [ir.Constant(i64, 0), index], inbounds=True)  # type: ignore

    def literal_word(self) -> ir.Value:
        """Word carried by literal pointers (literal slot key=0, always live)."""
        return self.__pointers.literal_word()

    def env_word(self) -> ir.Value:
        """Word carried by environment pointers (environment slot key=0, always live)."""
        return self.__pointers.env_word()

    def fat_data(self, ll_val: LLValue) -> LLValue:
        """Extract a fat pointer's bare 8 B data address, or return an already raw pointer.

        At slice/str construction boundaries this handles both raw pointers extracted from slices
        and fat values synthesized by malloc/VarPtr.
        """
        return self.__pointers.fat_data(ll_val)

    def promote_fat(self, ll_val: LLValue) -> LLValue:
        """Promote a raw 8 B pointer (such as Alloca) to <data,word,0,1>.

        PointerType LLVM values must be aggregates to cross calls/returns/stores. Complete metadata
        for raw-pointer primitives here using the always-live literal slot.
        """
        return self.__pointers.promote_fat(ll_val)

    def fat_addr(self, ll_val: LLValue, pointee_type_id: int) -> LLValue:
        """Resolve effective address = data + index*|T| with one GEP shared by checks and access.

        Extract fat data/index, bitcast to T*, then GEP by element; raw pointers use their address
        with index zero. T& has no index and always addresses one element, so its address is data.
        Validity checks enforce no wrap.
        """
        return self.__pointers.fat_addr(ll_val, pointee_type_id)

    def gen_key_value(self) -> LLValue:
        """Re-key on frame entry with a monotonic 32-bit key; exhaustion reports R003.

        Key width matches the entry's 32-bit key. Reaching FRAME_KEY_LIMIT (0xFFFFFFFE, with
        0xFFFFFFFF reserved for SENTINEL) terminates without wrapping, since reuse would match
        old pointers. Heap identities use per-entry generations instead.
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
        # When count and element size are compile-time constants, choose the allocator by size-class index.
        # Use the generic entry for requests above the largest class or nonconstant element counts.
        if class_index is None:
            block_ir = self.__builder.call(self.__module.get_pool_alloc(), [payload.ir_val])  # type: ignore
        else:
            block_ir = self.__builder.call(
                self.__module.get_pool_alloc_class(),
                [ir.Constant(ir.IntType(32), class_index)],  # type: ignore
            )  # type: ignore
        block_base = LLValue(ptr_type_id, block_ir)  # type: ignore
        # Header {extent:u32 @0,pad:u32 @4} is 8 B; identity resides in the lock entry.
        # Emitted IR handles slot allocation: pop a free index or call bump when empty,
        # making key writes visible to LLVM so live checks on fresh allocations can fold to true.
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
            source_etype=ir.IntType(8),  # Index the block header by byte offset.
        )
        self.__builder.store(self.__builder.trunc(size.ir_val, i32), extent_ptr)  # type: ignore

        key_ir = word_ir
        data_ir = self.__builder.gep(
            block_base.ir_val,
            [ir.Constant(ir.IntType(64), ABI.BlockHeader.BYTES)],  # type: ignore
            inbounds=False,
            source_etype=ir.IntType(8),  # Index the payload by byte offset.
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
        """Push a frame-lock slot on the fixed-address shadow stack and write its word.

        word=<key:32|lock:32>; key comes from the global monotonic counter and lock is the depth.
        The node returns word; __frame_lock_slot_name records the address for return-path
        SENTINEL writes. The hot path has one depth check, one GEP, and two stores:
        write the slot before publishing depth, all before user statements.
        """
        if key is None:
            # Frame-lock lowering can precede its entry-block GenKey; obtain the key here if needed.
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
        # word=<key:32|lock:32>, where lock is shadow-stack depth (lock-table index).
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
        # Record the slot address in a function register for SENTINEL writes; the node result is word, not the slot.
        self.__func.set_reg(
            "frame.slot",
            LLValue(self.__type_ctx.alloc_pointer(TypeCtx.u64_id), slot),
        )
        self.__frame_lock_slot_name = "frame.slot"
        return LLValue(self.__type_ctx.u64_id, frame_word)

    def write_lock_slot(self, lock_ptr: LLValue, value: LLValue) -> None:
        """Write ``value`` to the lock slot addressed by ``lock_ptr``.

        The pointer is nonnull and the slot writable (empty containers own real heap blocks)。
        """
        raw = self.fat_data(lock_ptr).ir_val
        self.__builder.store(value.ir_val, raw)  # type: ignore

    def set_frame_lock(self, e_f: IR.Value) -> None:
        """Record that the function acquired a frame lock: every return writes SENTINEL and pops.

        acquire_frame_lock records the address in __frame_lock_slot_name.
        This method records acquisition state; its argument is word, not the address.
        """
        assert isinstance(e_f, IR.Reg), "帧锁必须为入口寄存器"
        self.__frame_lock_acquired = True

    def release_frame_lock(self) -> None:
        """Write SENTINEL on exit, then pop the stable shadow stack.

        Slots remain mapped and never hold user data; push writes their key before user statements.
        Compiler-generated function entry/exit strictly follows LIFO order,
        so exit decrements depth without a free list or dynamic slot lookup.
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
        """Compute effective address (data+index*|T|) as i128 to avoid address wrap."""
        return self.__pointers.fat_addr_i128(ll_val, pointee_type_id)

    def cmp_fat_operands(self, ll_val: LLValue) -> tuple[ir.Value, ir.Value]:
        """Return comparison fields (data,index): extract fat fields or use (raw pointer,0).
        Return two LLVM values, i8* and u64."""
        return self.__pointers.cmp_fat_operands(ll_val)

    def slice_ptr_fat(self, base: LLValue, ptr_type_id: int) -> LLValue:
        """Convert three-field slice/str data to <data,word,0,size>.

        Inherit the view's actual word directly (size is field 2), preserving
        lock identity when deriving T* from T[].
        """
        return self.__pointers.slice_ptr_fat(base, ptr_type_id)

    def synthesize_fat_value(self, value: LLValue, size: LLValue) -> LLValue:
        """Synthesize <data,word,0,size> from a bare 8 B pointer.

        At slice/str boundaries, complete metadata for raw ptr fields entering {T*,u64}
        wrappers such as SliceStruct. Use the always-live global literal slot: a frame lock
        in a thin wrapper would become invalid on return and escape into its caller.
        """
        data = LLValue(self.__type_ctx.alloc_pointer(self.__type_ctx.u8_id), value.ir_val)  # type: ignore
        word = LLValue(self.__type_ctx.u64_id, self.literal_word())  # type: ignore
        zero = LLValue(self.__type_ctx.u64_id, ir.Constant(ir.IntType(64), 0))  # type: ignore
        if not self.__raw_pointers:
            self.check_view_count(size, "vcap")
        return self.build_fat(data, word, zero, size, value.type_id)

    def __fat_value_pair(self, v: ir.Value) -> tuple[ir.Value, ir.Value]:
        """Return (data,second) at ir.Value level according to pointer representation.

        second is index for four-field pointers, size for three-field slices, word for
        two-field references, size for raw slices, and zero for bare pointers.
        """
        if isinstance(v.type, ir.LiteralStructType):  # type: ignore
            field_count = len(v.type.elements)  # type: ignore
            if field_count >= 4:
                second_idx = ABI.FAT_INDEX      # Fat pointer: compare (data,index).
            elif field_count == 3:
                second_idx = ABI.SLICE_SIZE     # Slice: compare (data,size).
            else:
                second_idx = ABI.REF_WORD       # Reference: compare (data,word).
            second = self.__builder.extract_value(v, second_idx)  # type: ignore
            if isinstance(second.type, ir.IntType) and second.type.width < 64:  # type: ignore
                second = self.__builder.zext(second, ir.IntType(64))  # type: ignore
            return (self.__builder.extract_value(v, ABI.FAT_DATA), second)  # type: ignore
        return (v, ir.Constant(ir.IntType(64), 0))  # type: ignore

    def cmp_fat_values(self, op: BinaryOperator, lhs: ir.Value, rhs: ir.Value) -> ir.Value:
        """Compare fields of fat ir.Value aggregates.

        Equality uses (data,index); ordering first checks equal data and rejects different objects.
        CFG did not route these values here, so insert the precondition locally.
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
        """Extract word's 32-bit key, matching the lock-entry key width."""
        i64: ir.IntType = ir.IntType(64)  # type: ignore
        return self.__builder.and_(  # type: ignore
            self.__builder.lshr(word, ir.Constant(i64, ABI.WORD_KEY_SHIFT)),  # type: ignore
            ir.Constant(i64, ABI.KEY_MASK),  # type: ignore
        )

    def __extract_check_fields(self, ll_val: LLValue) -> tuple[ir.Value, ir.Value, ir.Value, ir.Value]:
        """Extract the check bundle (data,word,index,size) once for four-field PointerType.

        check_safe_access uses the bundle for live (data/word reconstruct the slot) and
        in_bounds (index/size), avoiding repeated extraction.
        Two-field RefType has no index/size and must not use this path.
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
        """live(p): compare the entry key and require word != 0, without dispatch or windows.

        word's lock index alone addresses the slot, whose key has the same 32-bit width,
        so comparison uses one 32-bit load and comparison. Null (word=0) yields false
        through an and operation without reading entry zero or adding a basic block.
        """
        i32: ir.IntType = ir.IntType(32)  # type: ignore
        lock = self.lock_of(self.__builder, word, data)
        slot_val = self.__builder.load(lock, typ=i32)  # type: ignore
        key = self.__builder.trunc(self.__word_key(word), i32)  # type: ignore
        matched = self.__builder.icmp_unsigned("==", slot_val, key)  # type: ignore
        nonnull = self.__builder.icmp_unsigned("!=", word, ir.Constant(ir.IntType(64), 0))  # type: ignore
        return LLValue(self.__type_ctx.bool_id, self.__builder.and_(nonnull, matched))  # type: ignore

    def __check_in_bounds_cond(self, index: ir.Value, size: ir.Value) -> LLValue:
        """in_bounds(p,1): 0 <= index and index+1 <= size, simplified to unsigned index < size."""
        cond = self.__builder.icmp_unsigned("<", index, size)  # type: ignore
        return LLValue(self.__type_ctx.bool_id, cond)  # type: ignore

    def __check_live_and(self, word: ir.Value, data: ir.Value, other: ir.Value) -> ir.Value:
        """Combine live(p) with other, an i1 value."""
        live_val = self.__check_live(word, data)
        return self.__builder.and_(live_val.ir_val, other)  # type: ignore

    def check_safe_access(self, ptr: LLValue, live: bool = True) -> None:
        """safe_access(p,1) = live(p) and in_bounds(p,1).

        live=False means the call site knows the slot matches the pointer's key (current-frame address),
        so emit only bounds checks, preserving error codes and remaining failure conditions.
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
            # Current-frame views have matching slot keys and known-true live (see check_safe_access).
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
        # Index ranges encode slot kind (heap >= HEAP_LOCK_BASE); reconstruct the payload anchor from
        # anchor_lo32 and data's high bits, then block base=anchor-BYTES (the allocator guarantees one 4 GiB window).
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
        # Header extent counts elements: convert to bytes with the same element size before comparison.
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
        """in_bounds(p_s,1), the re-anchoring precondition."""
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
        """T& access precheck: live only, without bounds.

        Two-field <data,word> references have no index/size or bounds metadata.
        Compare slot keys, with null yielding false; live failure reports S003.
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
        """Check 0 <= index+offset <= size using u64 no-wrap and upper-bound comparisons.

        Pointer arithmetic does not access memory, so, like PtrDiff/PtrCmp, it omits
        allocation liveness. Dereference and external I/O check live at their access boundaries.

        For unsigned indices, nonnegativity is automatic. Decompose the condition into:
        (a) no_wrap = icmp uge sum,index (sum=index+offset; unsigned wrap means sum<index),
        (b) in_range = icmp ule sum,size, combining them with and.
        For positive offsets below 2^63 and index+offset below 2^64, no_wrap is true,
        leaving the upper bound, equivalent to the i128 formulation.
        On overflow sum<index rejects the operation, equivalent to i128 sum>=2^64>size.
        Offsets >=2^63 are very large unsigned indices and report a safety error;
        sign-extension would instead treat them as negative and could admit a subslice
        when base.index >= |offset|. The u64 form lets ConstraintElimination relate
        loop/branch constraints and remove redundant checks,
        while no-wrap remains enforced without wide integers. LLVM emits the predicate.
        """
        if not self.is_fat(base):
            return
        cond = self.__element_arith_cond(base, offset)
        self.__core.flow.emit_check(LLValue(self.__type_ctx.bool_id, cond), RuntimeErrorCode.S004, "elarith")

    def check_element_access(self, base: LLValue, offset: LLValue, ptr: LLValue, live: bool = True) -> None:
        """Combine ElementArith, InBounds, and SafeAccess into one predicate.

        For elem=base+offset, f=elem.field, and access of f, require arithmetic validity
        (u64 no-wrap and upper bound), in_bounds(elem,1)
        (one-past-end field access is invalid), and live(elem).
        A re-anchored field pointer has in_bounds(f,1) true and inherits its lock,
        so live(f)=live(elem). Neither no-wrap nor live may be dropped.
        LLVM emits the check.
        """
        if not (self.is_fat(base) and self.is_fat(ptr)):
            return
        elarith_cond = self.__element_arith_cond(base, offset)
        # InBounds term: elem.index < elem.size.
        e_index = self.extract_fat_field(ptr, ABI.FAT_INDEX).ir_val
        e_size = self.extract_fat_field(ptr, ABI.FAT_SIZE).ir_val
        ib_cond = self.__builder.icmp_unsigned("<", e_index, e_size)  # type: ignore
        # live term: compare slot keys, with null false; omit it when current-frame elem is known live.
        if live:
            ev_data = self.extract_fat_field(ptr, ABI.FAT_DATA).ir_val
            ev_word = self.extract_fat_field(ptr, ABI.FAT_WORD).ir_val
            live_ok = self.__check_live(ev_word, ev_data)
            cond: ir.Value = self.__builder.and_(self.__builder.and_(elarith_cond, ib_cond), live_ok.ir_val)  # type: ignore
        else:
            cond = self.__builder.and_(elarith_cond, ib_cond)  # type: ignore
        self.__core.flow.emit_check(LLValue(self.__type_ctx.bool_id, cond), RuntimeErrorCode.S002, "eacc")

    def check_raw_bounds(self, index: LLValue, length: int) -> None:
        """Lazy lvalue raw-array bounds check: 0 <= index < compile-time length.

        Unaddressed arrays have no fat metadata. One unsigned comparison gives the combined
        CheckElementArith/CheckSafeAccess bounds semantics; index is already coerced to u64.
        """
        cond = self.__builder.icmp_unsigned("<", index.ir_val, ir.Constant(ir.IntType(64), length))  # type: ignore
        self.__core.flow.emit_check(LLValue(self.__type_ctx.bool_id, cond), RuntimeErrorCode.S001, "rb")

    def check_ptrdiff(self, lhs: LLValue, rhs: LLValue) -> None:
        """Preconditions: equal data, both indices <= size, and a representable difference.

        PtrDiff does not access memory and, like pointer arithmetic, omits allocation liveness.
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
        """Ordering precondition: equal data; cross-object ordering reports S005.

        PtrCmp does not access memory and, like pointer arithmetic, omits allocation liveness.
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
        # Anchor check: anchor_lo32 must equal data's low 32 bits, immediately rejecting re-anchored pointers.
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
