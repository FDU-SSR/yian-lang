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
        # Keep "main" as-is for the linker; suffix type_id for generic instantiations that share a name.
        llvm_name = cfg_func.name if cfg_func.name == "main" else f"{cfg_func.name}.{cfg_func.type_id}"
        ir_func = ir.Function(self.__module, func_ir_type, name=llvm_name)
        func = LLFunction(ir_func)
        self.__functions[cfg_func.type_id] = func
        return func

    def get_func(self, type_id: int) -> LLFunction:
        return self.__functions[type_id]
