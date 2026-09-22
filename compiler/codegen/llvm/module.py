# pyright: reportUnknownMemberType=false, reportUnknownArgumentType=false
"""
LLVM Module manager — owns ir.Module, TypeMapper, IntrinsicManager.
"""

from __future__ import annotations

from llvmlite import ir

from compiler.codegen.cfg import ir as IR
from compiler.codegen.llvm.intrinsics import IntrinsicManager
from compiler.codegen.llvm.types import LLTypeCtx
from compiler.codegen.llvm.value import LLValue
from compiler.runtime_error import RuntimeErrorCode, runtime_error_message

# 目标平台: 三元组与 data layout 必须成对出现在模块上。
#
# 只写 triple、留空 data layout 时, 模块内声明的布局与目标机布局不一致: 中端
# pass 按"无 ABI 对齐约束"的空布局折叠字段偏移, 后端按目标机布局落子, 同一个
# 字段的读写偏移就会不同 (例如 4 字节 tag + 8 字节指针的 enum 之后跟 u64:
# 空布局给 12, 目标机给 16), 表现为静默取错值, 且只在 -O1 及以上出现
# (见 docs/llvm_note.md「目标三元组与 data layout」)。
TARGET_TRIPLE = "x86_64-unknown-linux-gnu"
TARGET_DATA_LAYOUT = "e-m:e-p270:32:32-p271:32:32-p272:64:64-i64:64-i128:128-f80:128-n8:16:32:64-S128"

# llvmlite 的 IR 层默认把指针打印成有型的 `T*` (兼容旧 IR)。本项目的指针类型已统一
# 为 opaque pointer (见 types.py 与 builder.py), 因此关掉这套打印, 使发射的 IR 与
# LLVM 的指针表示一致。该开关只影响类型打印: llvmlite 侧的指针判定与生成的机器码
# 都不变。
ir.types.ir_layer_typed_pointers_enabled = False  # type: ignore


def apply_target(module: ir.Module) -> None:
    """把目标三元组与配套的 data layout 写到 ``module`` 上。

    LLVM 类型的具体布局(TargetData)由模块的 data layout 决定, 编译期布局查询与
    发射阶段的优化/后端必须看到同一份, 故两者只能在同一个地方定义、一起落盘。
    """
    module.triple = TARGET_TRIPLE
    module.data_layout = TARGET_DATA_LAYOUT


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

    def __init__(self, module: ir.Module, type_ctx: LLTypeCtx, entry_type_id: int | None = None) -> None:
        self.__module = module
        self.__type_ctx = type_ctx
        self.__entry_type_id = entry_type_id
        self.__intrinsics = IntrinsicManager(module)
        self.__functions: dict[int, LLFunction] = {}  # type_id → LLFunction
        self.__string_counter = 0
        self.__strings: dict[bytes, ir.GlobalVariable] = {}
        self.__argc_global: ir.GlobalVariable | None = None
        self.__argv_global: ir.GlobalVariable | None = None
        self.__env_lock_global: ir.GlobalVariable | None = None
        self.__key_stack_global: ir.GlobalVariable | None = None
        self.__runtime_fail_func: ir.Function | None = None
        self.__panic_func: ir.Function | None = None
        self.__lit_lock_global: ir.GlobalVariable | None = None
        self.__pool_head_global: ir.GlobalVariable | None = None
        self.__pool_alloc_func: ir.Function | None = None
        self.__pool_alloc_class_func: ir.Function | None = None
        self.__pool_release_func: ir.Function | None = None
        self.__frame_lock_arena_global: ir.GlobalVariable | None = None
        self.__frame_lock_depth_global: ir.GlobalVariable | None = None
        self.__lock_bump_global: ir.GlobalVariable | None = None
        self.__lock_free_head_global: ir.GlobalVariable | None = None
        self.__lock_new_func: ir.Function | None = None
        self.__lock_release_func: ir.Function | None = None

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
        # The program entry is identified by its type id, not by its name: a
        # dependency may define its own `main`, which is an ordinary function
        # here (renamed to `main.<type_id>`).
        if cfg_func.type_id == self.__entry_type_id:
            llvm_name = "__yian_main"
        else:
            llvm_name = f"{cfg_func.name}.{cfg_func.type_id}"
        ir_func = ir.Function(self.__module, func_ir_type, name=llvm_name)
        if llvm_name != "__yian_main":
            # 事实登记: 整个程序编译进同一个 module、交付物是单个可执行文件, YIAN 函数
            # 不构成对外 ABI —— 除入口 (C 运行时的 main 包装要调它) 外都不需要对外可见。
            # 副作用是 LLVM 拿得到全部定义 (IPSCCP/GlobalOpt/内联, 以及内联器对
            # "内部符号且只有一个调用点"的加成), 方向随基准而变 (见提交信息);
            # 将来若支持"多个 YIAN 目标文件互链", 这里要改成按导出面区分。
            ir_func.linkage = "internal"
        if llvm_name.startswith("index."):
            # 索引/检查辅助强制内联: 变体 B 的胖指针让它变胖后掉出内联阈值,
            # 每个元素会多一次跨函数调用(实测过的根因)。
            ir_func.attributes.add("alwaysinline")
        func = LLFunction(ir_func)
        self.__functions[cfg_func.type_id] = func
        return func

    def get_func(self, type_id: int) -> LLFunction:
        return self.__functions[type_id]

    @property
    def argc_global(self) -> ir.GlobalVariable:
        if self.__argc_global is None:
            global_var = ir.GlobalVariable(self.__module, ir.IntType(32), name="__yian_argc")
            global_var.linkage = "external"
            self.__argc_global = global_var
        return self.__argc_global

    @property
    def argv_global(self) -> ir.GlobalVariable:
        if self.__argv_global is None:
            argv_type = ir.PointerType()
            global_var = ir.GlobalVariable(self.__module, argv_type, name="__yian_argv")
            global_var.linkage = "external"
            self.__argv_global = global_var
        return self.__argv_global

    def get_env_lock(self) -> ir.GlobalVariable:
        """Return the process-lifetime lock slot used by argv byte slices."""
        if self.__env_lock_global is None:
            global_var = ir.GlobalVariable(self.__module, ir.IntType(64), name="__yian_env_lock")
            global_var.linkage = "external"
            self.__env_lock_global = global_var
        return self.__env_lock_global

    # -- fat-pointer mechanism globals (LLVM 层) --

    def get_key_counter(self) -> ir.GlobalVariable:
        """帧键单调计数器全局（``__yian_key_stack``，由 runtime 定义）。

        ``k_f ← Gen()`` 的 LLVM 发射：load 全局计数 → 上限检查 → add 1 → store 回。
        全局变量保证跨函数单调（同一槽位复用也不会给出相同的键）。堆对象的身份不走
        计数器，而是由锁表项按块换代。
        """
        if self.__key_stack_global is None:
            self.__key_stack_global = self.__new_key_counter("__yian_key_stack")
        return self.__key_stack_global

    def __new_key_counter(self, name: str) -> ir.GlobalVariable:
        global_var = ir.GlobalVariable(self.__module, ir.IntType(64), name=name)  # type: ignore
        global_var.linkage = "external"
        return global_var

    def get_runtime_fail(self) -> ir.Function:
        """Return the runtime fail-stop helper declaration used by safety checks."""
        if self.__runtime_fail_func is not None:
            return self.__runtime_fail_func

        i8_ptr = ir.PointerType()  # type: ignore
        i64 = ir.IntType(64)  # type: ignore
        fn = ir.Function(
            self.__module,
            ir.FunctionType(ir.VoidType(), [i8_ptr, i64]),
            name="__yian_runtime_fail",
        )
        fn.attributes.add("cold")
        fn.attributes.add("noreturn")
        fn.attributes.add("nounwind")
        message, length = fn.args
        message.name = "message"
        length.name = "length"
        self.__runtime_fail_func = fn
        return fn

    def get_panic(self) -> ir.Function:
        """Return the runtime panic helper declaration."""
        if self.__panic_func is not None:
            return self.__panic_func

        i8_ptr = ir.PointerType()  # type: ignore
        i64 = ir.IntType(64)  # type: ignore
        fn = ir.Function(
            self.__module,
            ir.FunctionType(ir.VoidType(), [i8_ptr, i64]),
            name="__yian_panic",
        )
        fn.attributes.add("cold")
        fn.attributes.add("noreturn")
        fn.attributes.add("nounwind")
        message, length = fn.args
        message.name = "message"
        length.name = "length"
        self.__panic_func = fn
        return fn

    def emit_runtime_fail(self, builder: ir.IRBuilder, code: RuntimeErrorCode) -> None:
        """Emit a call with a private, canonical diagnostic string."""
        message = runtime_error_message(code)
        global_var = self.get_string_global(message)
        ptr = builder.gep(
            global_var,
            [ir.Constant(ir.IntType(32), 0), ir.Constant(ir.IntType(32), 0)],
        )
        builder.call(
            self.get_runtime_fail(),
            [ptr, ir.Constant(ir.IntType(64), len(message))],
        )

    def get_lit_lock(self) -> ir.GlobalVariable:
        """字符串字面量锁槽:全局 i64,初值 = LITERAL_KEY(1),永不写。

        字面量数据是全局只读区、生命周期为整个程序——live(p) 读该锁槽恒等于
        键,字面量派生的指针恒 live;键 1 与任何真实帧键只在各自锁槽内比较,
        无跨槽串扰(以锁槽地址寻址)。初始化与所有权在运行时库。
        """
        if self.__lit_lock_global is None:
            global_var = ir.GlobalVariable(self.__module, ir.IntType(64), name="__yian_lit_lock")  # type: ignore
            global_var.linkage = "external"
            self.__lit_lock_global = global_var
        return self.__lit_lock_global

    # -- single-threaded stable frame-lock shadow stack --

    def get_lock_table(self) -> ir.GlobalVariable:
        """Return the fixed-address lock-table declaration (变体 B)。

        表项 8 B = {key:u32, anchor_lo32:u32}; 下标区间自带 kind:
        [0, FRAME_LOCK_SLOTS) 帧影子栈槽, 接着两个字面量/环境常量槽, 其余由分配器发放。
        表在运行时库里、位于 BSS: 只有被触到的页常驻。
        """
        if self.__frame_lock_arena_global is None:
            arena_type = ir.ArrayType(
                ir.IntType(64), IR.LOCK_TABLE_SLOTS  # type: ignore
            )
            global_var = ir.GlobalVariable(
                self.__module, arena_type, name="__secl_lock_table"
            )
            global_var.linkage = "external"
            global_var.align = IR.LockEntry.BYTES  # type: ignore
            self.__frame_lock_arena_global = global_var
        return self.__frame_lock_arena_global

    def get_lock_bump(self) -> ir.GlobalVariable:
        """Return the lock-table bump cursor declaration (堆段下一个未用下标)."""
        if self.__lock_bump_global is None:
            global_var = ir.GlobalVariable(
                self.__module, ir.IntType(64), name="__secl_lock_bump"
            )
            global_var.linkage = "external"
            global_var.align = 8  # type: ignore
            self.__lock_bump_global = global_var
        return self.__lock_bump_global

    def get_lock_free_head(self) -> ir.GlobalVariable:
        """Return the lock-table free-list head declaration (0 = 空, 否则 = 下标 + 1)."""
        if self.__lock_free_head_global is None:
            global_var = ir.GlobalVariable(
                self.__module, ir.IntType(64), name="__secl_lock_free_head"
            )
            global_var.linkage = "external"
            global_var.align = 8  # type: ignore
            self.__lock_free_head_global = global_var
        return self.__lock_free_head_global

    def get_lock_bump_take(self) -> ir.Function:
        """Return the lock-table bump declaration (自由链为空时取新下标; 耗尽 R003)."""
        if self.__lock_new_func is None:
            fn = ir.Function(
                self.__module,
                ir.FunctionType(ir.IntType(64), []),
                name="__secl_lock_bump_take",
            )
            # 事实性标注: 失败路径经 noreturn 的 __yian_runtime_fail 终止, 不 unwind。
            fn.attributes.add("nounwind")
            self.__lock_new_func = fn
        return self.__lock_new_func

    def get_lock_release(self) -> ir.Function:
        """Return the lock-table release declaration (按 word 释放表项)."""
        if self.__lock_release_func is None:
            fn = ir.Function(
                self.__module,
                ir.FunctionType(ir.VoidType(), [ir.IntType(64)]),
                name="__secl_lock_release",
            )
            fn.args[0].name = "word"
            # 事实性标注: 失败路径经 noreturn 的 __yian_runtime_fail 终止, 不 unwind。
            fn.attributes.add("nounwind")
            self.__lock_release_func = fn
        return self.__lock_release_func

    def get_frame_lock_depth(self) -> ir.GlobalVariable:
        """Return the active-depth cursor declaration for the frame-lock segment."""
        if self.__frame_lock_depth_global is None:
            i64 = ir.IntType(64)  # type: ignore
            global_var = ir.GlobalVariable(
                self.__module, i64, name="__secl_frame_lock_depth"
            )
            global_var.linkage = "external"
            global_var.align = IR.FrameLockArena.SLOT_BYTES  # type: ignore
            self.__frame_lock_depth_global = global_var
        return self.__frame_lock_depth_global

    # -- single-threaded stable-header heap pool --

    def get_pool_alloc(self) -> ir.Function:
        """Return the runtime allocator declaration for stable-header blocks.

        分配器在运行时库里 (尺寸类 arena), 返回块基址 (锁槽地址); 编译器随后写锁槽与
        active_size, 负载 = 基址 + `BlockHeader.BYTES`。
        """
        if self.__pool_alloc_func is None:
            fn = ir.Function(
                self.__module,
                ir.FunctionType(ir.PointerType(), [ir.IntType(64)]),
                name="__secl_pool_alloc",
            )
            fn.args[0].name = "requested"
            # 事实性标注: 失败路径经 noreturn 的 __yian_runtime_fail 终止, 不 unwind。
            fn.attributes.add("nounwind")
            self.__pool_alloc_func = fn
        return self.__pool_alloc_func

    def get_pool_alloc_class(self) -> ir.Function:
        """Return the size-class allocator declaration (类号由编译器算好).

        尺寸在编译期已知的分配点直接传类号, 运行时省掉按字节数查表; 类号取值见
        `runtime_lib.CLASS_BYTES`。
        """
        if self.__pool_alloc_class_func is None:
            fn = ir.Function(
                self.__module,
                ir.FunctionType(ir.PointerType(), [ir.IntType(32)]),
                name="__secl_pool_alloc_class",
            )
            fn.args[0].name = "class_index"
            # 事实性标注: 失败路径经 noreturn 的 __yian_runtime_fail 终止, 不 unwind。
            fn.attributes.add("nounwind")
            self.__pool_alloc_class_func = fn
        return self.__pool_alloc_class_func

    def get_pool_release(self) -> ir.Function:
        """Return the runtime release declaration; 归还整块 (物理页由运行时回收)."""
        if self.__pool_release_func is None:
            fn = ir.Function(
                self.__module,
                ir.FunctionType(ir.VoidType(), [ir.PointerType()]),
                name="__secl_pool_release",
            )
            fn.args[0].name = "block"
            # 事实性标注: 失败路径经 noreturn 的 __yian_runtime_fail 终止, 不 unwind。
            fn.attributes.add("nounwind")
            self.__pool_release_func = fn
        return self.__pool_release_func
