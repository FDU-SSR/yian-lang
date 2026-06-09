"""
LLVM Module manager — owns ir.Module, TypeMapper, IntrinsicManager.
"""

from __future__ import annotations

from llvmlite import ir  # type: ignore[import-untyped]

from compiler.analysis.ty.context import TypeCtx
from compiler.analysis.unit.unit_data import UnitData
from compiler.codegen.cfg import ir as IR
from compiler.codegen.llvm.intrinsics import IntrinsicManager
from compiler.codegen.llvm.types import LLTypeCtx


class LLFunction:
    """Wraps an ``ir.Function`` with codegen context."""

    def __init__(self, ir_func: ir.Function) -> None:
        self.__ir = ir_func
        self.__var_allocas: dict[int, ir.AllocaInstr] = {}
        self.__entry_block: ir.Block | None = None
        self.__blocks: dict[str, ir.Block] = {}
        self.__regs: dict[str, ir.Value] = {}

    # -- entry block --

    def add_entry_block(self) -> None:
        self.__entry_block = self.__ir.append_basic_block(".entry")

    @property
    def entry_block(self) -> ir.Block:
        assert self.__entry_block is not None
        return self.__entry_block

    # -- var allocas --

    def set_alloca(self, symbol_id: int, alloca: ir.AllocaInstr) -> None:
        self.__var_allocas[symbol_id] = alloca

    def alloca(self, symbol_id: int) -> ir.AllocaInstr | None:
        return self.__var_allocas.get(symbol_id)

    # -- block management --

    def add_block(self, label: str, block: ir.Block) -> None:
        self.__blocks[label] = block

    def block(self, label: str) -> ir.Block:
        return self.__blocks[label]

    # -- register management --

    def set_reg(self, name: str, value: ir.Value) -> None:
        self.__regs[name] = value

    def reg(self, name: str) -> ir.Value:
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

    # -- constants --

    def constant(self, typ: ir.Type, value: int | float | None) -> ir.Constant:
        return ir.Constant(typ, value)  # type: ignore[arg-type]

    def const_literal_struct(self, values: list[ir.Value]) -> ir.Constant:
        return ir.Constant.literal_struct(values)

    def undefined(self, typ: ir.Type) -> ir.Constant:
        return ir.Constant(typ, ir.Undefined)

    # -- string literals --

    def get_string_global(self, value: bytes) -> ir.Constant:
        if value in self.__strings:
            gv = self.__strings[value]
        else:
            s_type = ir.ArrayType(ir.IntType(8), len(value))
            gv = ir.GlobalVariable(self.__module, s_type, name=f"str.{self.__string_counter}")
            self.__string_counter += 1
            gv.linkage = "private"
            gv.global_constant = True
            gv.unnamed_addr = True
            gv.initializer = ir.Constant(s_type, bytearray(value))  # type: ignore[arg-type]
            self.__strings[value] = gv
        return gv.gep([ir.Constant(ir.IntType(32), 0), ir.Constant(ir.IntType(32), 0)])

    def str_literal_val(self, value: bytes) -> ir.Constant:
        """Return {i8*, i64} struct for a string literal."""
        ptr = self.get_string_global(value)
        length = ir.Constant(ir.IntType(64), len(value))
        return ir.Constant.literal_struct([ptr, length])

    # -- function declaration --

    def declare(self, cfg_func: IR.Function) -> LLFunction:
        func_type = self.__type_ctx.get_ll_type(cfg_func.type_id)
        assert isinstance(func_type, ir.FunctionType)
        ir_func = ir.Function(self.__module, func_type, name=cfg_func.name)
        func = LLFunction(ir_func)
        self.__functions[cfg_func.type_id] = func
        return func

    def get_func(self, type_id: int) -> LLFunction:
        return self.__functions[type_id]
