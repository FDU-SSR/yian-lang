# pyright: reportUnknownMemberType=false, reportUnknownArgumentType=false
"""
LLVM Module manager — owns ir.Module, TypeMapper, IntrinsicManager.
"""

from __future__ import annotations

from llvmlite import ir

from compiler.codegen.cfg import ir as IR
from compiler.codegen.llvm.intrinsics import IntrinsicKind, IntrinsicManager
from compiler.codegen.llvm.types import LLTypeCtx
from compiler.codegen.llvm.value import LLValue


class LLFunction:
    """Wraps an ``ir.Function`` with codegen context."""

    def __init__(self, ir_func: ir.Function) -> None:
        self.__ir = ir_func
        self.__var_allocas: dict[int, LLValue] = {}
        self.__entry_block: ir.Block | None = None
        self.__blocks: dict[str, ir.Block] = {}
        self.__regs: dict[str, LLValue] = {}

    @property
    def ir_func(self) -> ir.Function:
        """The underlying llvmlite ``ir.Function``."""
        return self.__ir

    # -- entry block --

    def add_entry_block(self) -> None:
        self.__entry_block = self.new_block("entry")

    @property
    def entry_block(self) -> ir.Block:
        assert self.__entry_block is not None
        return self.__entry_block

    # -- var allocas --

    def set_alloca(self, symbol_id: int, alloca: LLValue) -> None:
        self.__var_allocas[symbol_id] = alloca

    def get_var_ptr(self, symbol_id: int) -> LLValue:
        if symbol_id not in self.__var_allocas:
            raise KeyError(f"Variable with symbol id {symbol_id} not found in function {self.__ir.name}")
        return self.__var_allocas[symbol_id]

    # -- block management --

    def new_block(self, label: str) -> ir.Block:
        """Append a basic block to the function."""
        return self.__ir.append_basic_block(label)

    def add_block(self, label: str, block: ir.Block) -> None:
        self.__blocks[label] = block

    def block(self, label: str) -> ir.Block:
        return self.__blocks[label]

    # -- register management --

    def set_reg(self, name: str, value: LLValue) -> None:
        self.__regs[name] = value

    def reg(self, name: str) -> LLValue:
        return self.__regs[name]


class LLModule:
    """Manages a single LLVM module."""

    def __init__(self, module: ir.Module, type_ctx: LLTypeCtx) -> None:
        self.__module = module
        self.__type_ctx = type_ctx
        self.__intrinsics = IntrinsicManager(module)
        self.__functions: dict[int, LLFunction] = {}  # type_id → LLFunction
        self.__string_counter = 0
        self.__strings: dict[bytes, ir.GlobalVariable] = {}
        self.__yian_main_type_id: int | None = None
        self.__key_heap_global: ir.GlobalVariable | None = None
        self.__key_stack_global: ir.GlobalVariable | None = None
        self.__trap_intrinsic: ir.Function | None = None
        self.__lit_lock_global: ir.GlobalVariable | None = None
        self.__pool_head_global: ir.GlobalVariable | None = None
        self.__pool_alloc_func: ir.Function | None = None
        self.__pool_release_func: ir.Function | None = None
        self.__frame_lock_arena_global: ir.GlobalVariable | None = None
        self.__frame_lock_depth_global: ir.GlobalVariable | None = None

    # -- properties --

    @property
    def intrinsics(self) -> IntrinsicManager:
        return self.__intrinsics

    @property
    def functions(self) -> dict[int, LLFunction]:
        return self.__functions

    @property
    def triple(self) -> str:
        return self.__module.triple

    def __str__(self) -> str:
        return str(self.__module)

    # -- string literals --

    def get_string_global(self, value: bytes) -> ir.GlobalVariable:
        """Return a global constant for the given string bytes."""
        if value in self.__strings:
            return self.__strings[value]
        storage = value if value else b"\0"
        string_type = ir.ArrayType(ir.IntType(8), len(storage))  # type: ignore
        global_var = ir.GlobalVariable(self.__module, string_type, name=f"str.{self.__string_counter}")
        self.__string_counter += 1
        global_var.linkage = "private"
        global_var.global_constant = True
        global_var.unnamed_addr = True
        global_var.initializer = ir.Constant(string_type, bytearray(storage))  # type: ignore[arg-type]
        self.__strings[value] = global_var
        return global_var

    # -- function declaration --

    def declare(self, cfg_func: IR.Function) -> LLFunction:
        func_ir_type = self.__type_ctx.get_ll_func_type(cfg_func.type_id)
        # Rename Yian "main" → "__yian_main"; the real "main" is emitted later as a wrapper.
        llvm_name = cfg_func.name if cfg_func.name != "main" else "__yian_main"
        if cfg_func.name != "main":
            llvm_name = f"{llvm_name}.{cfg_func.type_id}"
        else:
            self.__yian_main_type_id = cfg_func.type_id
        ir_func = ir.Function(self.__module, func_ir_type, name=llvm_name)
        func = LLFunction(ir_func)
        self.__functions[cfg_func.type_id] = func
        return func

    def get_func(self, type_id: int) -> LLFunction:
        return self.__functions[type_id]

    @property
    def has_yian_main(self) -> bool:
        return self.__yian_main_type_id is not None

    @property
    def yian_main_type_id(self) -> int:
        assert self.__yian_main_type_id is not None
        return self.__yian_main_type_id

    # -- fat-pointer mechanism globals (LLVM 层) --

    def get_key_counter(self, is_heap: bool) -> ir.GlobalVariable:
        """Gen 单调计数器全局(定义 10):堆/栈各一个 63 位计数,键 = 最高位标志拼接计数。

        ``k ← Gen()`` 的 LLVM 发射:load 全局计数 → add 1 → store 回 → 堆键 or MSB
        标志位。全局变量保证跨函数单调(同类内任意两次调用输出不同,定义 10)。
        """
        if is_heap:
            if self.__key_heap_global is None:
                self.__key_heap_global = self.__new_key_counter("__yian_key_heap")
            return self.__key_heap_global
        if self.__key_stack_global is None:
            self.__key_stack_global = self.__new_key_counter("__yian_key_stack")
        return self.__key_stack_global

    def __new_key_counter(self, name: str) -> ir.GlobalVariable:
        global_var = ir.GlobalVariable(self.__module, ir.IntType(64), name=name)  # type: ignore
        global_var.linkage = "internal"
        global_var.initializer = ir.Constant(ir.IntType(64), 0)  # type: ignore
        return global_var

    def get_trap_intrinsic(self) -> ir.Function:
        """``declare void @llvm.trap()`` — 运行期检查失败 → SIGILL → Exit code -4。"""
        if self.__trap_intrinsic is None:
            self.__trap_intrinsic = ir.Function(
                self.__module, ir.FunctionType(ir.VoidType(), []), name="llvm.trap"
            )
        return self.__trap_intrinsic

    def get_lit_lock(self) -> ir.GlobalVariable:
        """字符串字面量锁槽:全局 i64,初值 = LITERAL_KEY(1),永不写。

        字面量数据是全局只读区、生命周期为整个程序——live(p) 读该锁槽恒等于
        键,字面量派生的指针恒 live;键 1 与任何真实帧键只在各自锁槽内比较,
        无跨槽串扰(定义 8 以锁槽地址寻址)。
        """
        if self.__lit_lock_global is None:
            self.__lit_lock_global = ir.GlobalVariable(self.__module, ir.IntType(64), name="__yian_lit_lock")  # type: ignore
            self.__lit_lock_global.linkage = "internal"
            self.__lit_lock_global.initializer = ir.Constant(ir.IntType(64), 1)  # type: ignore
        return self.__lit_lock_global

    # -- single-threaded stable frame-lock shadow stack --

    def get_frame_lock_arena(self) -> ir.GlobalVariable:
        """Return the fixed-address frame-lock arena.

        The zero-initialized array occupies BSS-backed virtual address space;
        only pages reached by the peak active address-taking depth become
        resident.  Its address never aliases ordinary stack or heap payloads.
        """
        if self.__frame_lock_arena_global is None:
            arena_type = ir.ArrayType(
                ir.IntType(64), IR.FrameLockArena.SLOTS  # type: ignore
            )
            global_var = ir.GlobalVariable(
                self.__module, arena_type, name="__secl_frame_locks"
            )
            global_var.linkage = "internal"
            global_var.initializer = ir.Constant(arena_type, None)  # type: ignore
            global_var.align = IR.FrameLockArena.SLOT_BYTES  # type: ignore
            self.__frame_lock_arena_global = global_var
        return self.__frame_lock_arena_global

    def get_frame_lock_depth(self) -> ir.GlobalVariable:
        """Return the active-depth cursor for the frame-lock arena."""
        if self.__frame_lock_depth_global is None:
            i64 = ir.IntType(64)  # type: ignore
            global_var = ir.GlobalVariable(
                self.__module, i64, name="__secl_frame_lock_depth"
            )
            global_var.linkage = "internal"
            global_var.initializer = ir.Constant(i64, 0)  # type: ignore
            global_var.align = IR.FrameLockArena.SLOT_BYTES  # type: ignore
            self.__frame_lock_depth_global = global_var
        return self.__frame_lock_depth_global

    # -- single-threaded stable-header heap pool --

    def __pool_head(self) -> ir.GlobalVariable:
        if self.__pool_head_global is None:
            i8_ptr = ir.PointerType(ir.IntType(8))  # type: ignore
            self.__pool_head_global = ir.GlobalVariable(
                self.__module, i8_ptr, name="__secl_pool_head"
            )
            self.__pool_head_global.linkage = "internal"
            self.__pool_head_global.initializer = ir.Constant(i8_ptr, None)  # type: ignore
        return self.__pool_head_global

    @staticmethod
    def __pool_field_ptr(
        builder: ir.IRBuilder, block: ir.Value, offset: int, field_type: ir.Type
    ) -> ir.Value:
        byte_ptr = builder.gep(
            block, [ir.Constant(ir.IntType(64), offset)], inbounds=False  # type: ignore
        )
        return builder.bitcast(byte_ptr, ir.PointerType(field_type))  # type: ignore

    def get_pool_alloc(self) -> ir.Function:
        """Return the internal first-fit allocator for stable-header blocks."""
        if self.__pool_alloc_func is not None:
            return self.__pool_alloc_func

        i8 = ir.IntType(8)  # type: ignore
        i8_ptr = ir.PointerType(i8)  # type: ignore
        i8_ptr_ptr = ir.PointerType(i8_ptr)  # type: ignore
        i64 = ir.IntType(64)  # type: ignore
        fn = ir.Function(
            self.__module,
            ir.FunctionType(i8_ptr, [i64]),
            name="__secl_pool_alloc",
        )
        fn.linkage = "internal"
        requested = fn.args[0]
        requested.name = "requested"

        entry = fn.append_basic_block("entry")
        scan = fn.append_basic_block("scan")
        inspect = fn.append_basic_block("inspect")
        advance = fn.append_basic_block("advance")
        reuse = fn.append_basic_block("reuse")
        fresh = fn.append_basic_block("fresh")

        entry_builder = ir.IRBuilder(entry)
        entry_builder.branch(scan)

        scan_builder = ir.IRBuilder(scan)
        link = scan_builder.phi(i8_ptr_ptr, name="link")
        link.add_incoming(self.__pool_head(), entry)
        current = scan_builder.load(link, name="current")
        is_null = scan_builder.icmp_unsigned("==", current, ir.Constant(i8_ptr, None))
        scan_builder.cbranch(is_null, fresh, inspect)

        inspect_builder = ir.IRBuilder(inspect)
        capacity_ptr = self.__pool_field_ptr(
            inspect_builder, current, IR.BlockHeader.CAPACITY_OFFSET, i64
        )
        capacity = inspect_builder.load(capacity_ptr, name="capacity")
        fits = inspect_builder.icmp_unsigned(">=", capacity, requested)
        inspect_builder.cbranch(fits, reuse, advance)

        advance_builder = ir.IRBuilder(advance)
        next_link = self.__pool_field_ptr(
            advance_builder, current, IR.BlockHeader.NEXT_OFFSET, i8_ptr
        )
        advance_builder.branch(scan)
        link.add_incoming(next_link, advance)

        reuse_builder = ir.IRBuilder(reuse)
        reuse_next_ptr = self.__pool_field_ptr(
            reuse_builder, current, IR.BlockHeader.NEXT_OFFSET, i8_ptr
        )
        reuse_next = reuse_builder.load(reuse_next_ptr, name="next")
        reuse_builder.store(reuse_next, link)
        reuse_builder.store(ir.Constant(i8_ptr, None), reuse_next_ptr)
        reuse_builder.ret(current)

        fresh_builder = ir.IRBuilder(fresh)
        total = fresh_builder.add(
            requested, ir.Constant(i64, IR.BlockHeader.BYTES), name="total"
        )
        block = fresh_builder.call(
            self.__intrinsics.get(IntrinsicKind.Malloc),
            [total],
            name="block",
        )
        fresh_capacity_ptr = self.__pool_field_ptr(
            fresh_builder, block, IR.BlockHeader.CAPACITY_OFFSET, i64
        )
        fresh_next_ptr = self.__pool_field_ptr(
            fresh_builder, block, IR.BlockHeader.NEXT_OFFSET, i8_ptr
        )
        reserved_ptr = self.__pool_field_ptr(
            fresh_builder, block, IR.BlockHeader.RESERVED_OFFSET, i64
        )
        fresh_builder.store(requested, fresh_capacity_ptr)
        fresh_builder.store(ir.Constant(i8_ptr, None), fresh_next_ptr)
        fresh_builder.store(ir.Constant(i64, 0), reserved_ptr)
        fresh_builder.ret(block)

        self.__pool_alloc_func = fn
        return fn

    def get_pool_release(self) -> ir.Function:
        """Return the internal release helper; blocks remain mapped in the pool."""
        if self.__pool_release_func is not None:
            return self.__pool_release_func

        i8_ptr = ir.PointerType(ir.IntType(8))  # type: ignore
        fn = ir.Function(
            self.__module,
            ir.FunctionType(ir.VoidType(), [i8_ptr]),
            name="__secl_pool_release",
        )
        fn.linkage = "internal"
        block = fn.args[0]
        block.name = "block"
        entry = fn.append_basic_block("entry")
        builder = ir.IRBuilder(entry)
        head = builder.load(self.__pool_head(), name="head")
        next_ptr = self.__pool_field_ptr(
            builder, block, IR.BlockHeader.NEXT_OFFSET, i8_ptr
        )
        builder.store(head, next_ptr)
        builder.store(block, self.__pool_head())
        builder.ret_void()

        self.__pool_release_func = fn
        return fn

    def emit_wrapper_main(self) -> None:
        """Emit the C-compatible ``@main`` wrapper that calls ``__yian_main``."""
        assert self.__yian_main_type_id is not None

        wrapper_type = ir.FunctionType(ir.IntType(32), [ir.IntType(32), ir.PointerType(ir.PointerType(ir.IntType(8)))])
        wrapper = ir.Function(self.__module, wrapper_type, name="main")
        entry = wrapper.append_basic_block("entry")
        builder = ir.IRBuilder(entry)

        yian_main_func = self.__functions[self.__yian_main_type_id]
        builder.call(yian_main_func.ir_func, [])
        builder.ret(ir.Constant(ir.IntType(32), 0))
