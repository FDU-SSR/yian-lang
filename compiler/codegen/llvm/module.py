"""
LLVM Module manager — owns ir.Module, TypeMapper, IntrinsicManager.
"""

from __future__ import annotations

from llvmlite import ir

from compiler.codegen.cfg import ir as IR
from compiler.codegen.llvm.intrinsics import IntrinsicManager
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
        string_type = ir.ArrayType(ir.IntType(8), len(value))  # type: ignore
        global_var = ir.GlobalVariable(self.__module, string_type, name=f"str.{self.__string_counter}")
        self.__string_counter += 1
        global_var.linkage = "private"
        global_var.global_constant = True
        global_var.unnamed_addr = True
        global_var.initializer = ir.Constant(string_type, bytearray(value))  # type: ignore[arg-type]
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

    # -- fat-pointer mechanism globals (t8) --

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

    def emit_wrapper_main(self) -> None:
        """Emit the C-compatible ``@main`` wrapper that calls ``__yian_main``."""
        assert self.__yian_main_type_id is not None

        wrapper_type = ir.FunctionType(ir.IntType(32), [ir.IntType(32), ir.PointerType(ir.PointerType(ir.IntType(8)))])  # type: ignore
        wrapper = ir.Function(self.__module, wrapper_type, name="main")  # type: ignore
        entry = wrapper.append_basic_block("entry")
        builder = ir.IRBuilder(entry)

        yian_main_func = self.__functions[self.__yian_main_type_id]
        builder.call(yian_main_func.ir_func, [])  # type: ignore
        builder.ret(ir.Constant(ir.IntType(32), 0))  # type: ignore
