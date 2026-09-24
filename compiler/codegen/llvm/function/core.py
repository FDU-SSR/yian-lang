# pyright: reportUnknownMemberType=false
"""Function-scoped LLVM emission state and low-level operations."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum, auto

from llvmlite import ir

from compiler.analysis.ty.context import TypeCtx
from compiler.codegen.llvm.base.intrinsics import IntrinsicKind
from compiler.codegen.llvm.base.module import LLFunction, LLModule
from compiler.codegen.llvm.base.types import LLTypeCtx
from compiler.codegen.llvm.base.value import LLValue
from compiler.runtime_error import RuntimeErrorCode


class BuilderPosition(Enum):
    End = auto()
    First = auto()
    Phi = auto()


@dataclass(frozen=True)
class FunctionContext:
    """Read-only function and module references shared by emitters."""

    func: LLFunction
    module: LLModule
    type_ctx: TypeCtx
    ll_type_ctx: LLTypeCtx
    raw_pointers: bool


class FunctionFlow:
    """Own the active insertion point and control-flow blocks for one function."""

    def __init__(self, context: FunctionContext) -> None:
        self.__context = context
        self.__builder: ir.IRBuilder | None = None
        self.__check_seq = 0
        self.__current_cfg_block = ""
        self.__continuations: dict[str, str] = {}

    @property
    def builder(self) -> ir.IRBuilder:
        assert self.__builder is not None, "LLVM insertion point has not been initialized"
        return self.__builder

    @property
    def current_block_label(self) -> str:
        return self.builder.block.name  # type: ignore

    def alloca_in_entry(self, allocated_type: ir.Type) -> ir.Value:
        """Insert a static stack allocation in the entry block without moving flow state."""
        entry = self.__context.func.entry_block
        entry_builder = ir.IRBuilder(entry)
        if entry.is_terminated:
            entry_builder.position_before(entry.instructions[-1])  # type: ignore
        else:
            entry_builder.position_at_end(entry)  # type: ignore
        return entry_builder.alloca(allocated_type)  # type: ignore

    def position_at(self, label: str, where: BuilderPosition = BuilderPosition.End) -> None:
        block = self.__context.func.block(label)
        self.__builder = ir.IRBuilder(block)
        self.__current_cfg_block = label
        if where == BuilderPosition.Phi:
            self.builder.position_at_start(block)  # type: ignore
        elif where == BuilderPosition.First:
            instructions = list(block.instructions)  # type: ignore
            if instructions:
                self.builder.position_before(instructions[0])  # type: ignore

    def emit_check(self, cond: LLValue, error_code: RuntimeErrorCode, suffix: str) -> None:
        """Split the current block into a cold failure edge and a hot continuation."""
        seq = self.__next_sequence()
        ok_block = self.__context.func.new_block(self.__split_block_name(suffix, "ok", seq))
        fail_block = self.__context.func.new_block(self.__split_block_name(suffix, "fail", seq))
        self.__context.func.add_block(ok_block.name, ok_block)  # type: ignore
        self.__context.func.add_block(fail_block.name, fail_block)  # type: ignore
        branch = self.builder.cbranch(cond.ir_val, ok_block, fail_block)  # type: ignore
        branch.set_weights([2000, 1])  # type: ignore
        fail_builder = ir.IRBuilder(fail_block)
        self.__context.module.emit_runtime_fail(fail_builder, error_code)
        fail_builder.unreachable()  # type: ignore
        self.__continuations[self.__current_cfg_block] = ok_block.name
        self.__builder = ir.IRBuilder(ok_block)

    def emit_guarded(
        self,
        guard: ir.Value,
        compute: Callable[[ir.IRBuilder], ir.Value],
        result_type: ir.Type | None = None,
    ) -> ir.Value:
        """Emit a short-circuit computation and merge its value with zero."""
        seq = self.__next_sequence()
        then_block = self.__context.func.new_block(self.__split_block_name("g", "then", seq))
        else_block = self.__context.func.new_block(self.__split_block_name("g", "else", seq))
        merge_block = self.__context.func.new_block(self.__split_block_name("g", "merge", seq))
        self.__context.func.add_block(then_block.name, then_block)  # type: ignore
        self.__context.func.add_block(else_block.name, else_block)  # type: ignore
        self.__context.func.add_block(merge_block.name, merge_block)  # type: ignore
        self.builder.cbranch(guard, then_block, else_block)  # type: ignore
        then_builder = ir.IRBuilder(then_block)
        then_val = compute(then_builder)
        then_builder.branch(merge_block)  # type: ignore
        else_builder = ir.IRBuilder(else_block)
        else_builder.branch(merge_block)  # type: ignore
        merge_builder = ir.IRBuilder(merge_block)
        phi_type: ir.Type = result_type if result_type is not None else ir.IntType(1)  # type: ignore
        phi = merge_builder.phi(phi_type)  # type: ignore
        phi.add_incoming(then_val, then_block)  # type: ignore
        phi.add_incoming(ir.Constant(phi_type, 0), else_block)  # type: ignore
        self.__builder = merge_builder
        self.__continuations[self.__current_cfg_block] = merge_block.name
        return phi

    def emit_either(
        self,
        cond: ir.Value,
        then_fn: Callable[[ir.IRBuilder], ir.Value],
        else_fn: Callable[[ir.IRBuilder], ir.Value],
        result_type: ir.Type,
    ) -> ir.Value:
        """Emit two lazy branches and merge their results with a phi."""
        seq = self.__next_sequence()
        then_block = self.__context.func.new_block(self.__split_block_name("g", "then", seq))
        else_block = self.__context.func.new_block(self.__split_block_name("g", "else", seq))
        merge_block = self.__context.func.new_block(self.__split_block_name("g", "merge", seq))
        self.__context.func.add_block(then_block.name, then_block)  # type: ignore
        self.__context.func.add_block(else_block.name, else_block)  # type: ignore
        self.__context.func.add_block(merge_block.name, merge_block)  # type: ignore
        self.builder.cbranch(cond, then_block, else_block)  # type: ignore
        then_builder = ir.IRBuilder(then_block)
        then_val = then_fn(then_builder)
        then_builder.branch(merge_block)  # type: ignore
        else_builder = ir.IRBuilder(else_block)
        else_val = else_fn(else_builder)
        else_builder.branch(merge_block)  # type: ignore
        merge_builder = ir.IRBuilder(merge_block)
        phi = merge_builder.phi(result_type)  # type: ignore
        phi.add_incoming(then_val, then_block)  # type: ignore
        phi.add_incoming(else_val, else_block)  # type: ignore
        self.__builder = merge_builder
        self.__continuations[self.__current_cfg_block] = merge_block.name
        return phi

    def phi(self, type_id: int, incoming: list[tuple[str, ir.Value]]) -> ir.Value:
        ll_type = self.__context.ll_type_ctx.get_ll_type(type_id).ir_type
        node = self.builder.phi(ll_type)  # type: ignore
        for source_label, value in incoming:
            predecessor = self.continuation_for(source_label)
            node.add_incoming(value, self.__context.func.block(predecessor))  # type: ignore
        return node  # type: ignore

    def ret(self, value: ir.Value | None) -> None:
        if value is None:
            self.builder.ret_void()  # type: ignore
        else:
            self.builder.ret(value)  # type: ignore

    def branch(self, target_label: str) -> None:
        self.builder.branch(self.__context.func.block(target_label))  # type: ignore

    def cond_branch(self, condition: ir.Value, true_label: str, false_label: str) -> None:
        self.builder.cbranch(
            condition,
            self.__context.func.block(true_label),
            self.__context.func.block(false_label),
        )  # type: ignore

    def cond_branch_optional(
        self,
        condition: ir.Value,
        true_label: str,
        false_label: str,
        true_missing: str,
        false_missing: str,
    ) -> None:
        true_block = self.__block_or_unreachable(true_label, true_missing)
        false_block = self.__block_or_unreachable(false_label, false_missing)
        self.builder.cbranch(condition, true_block, false_block)  # type: ignore

    def switch(
        self, value: ir.Value, cases: list[tuple[ir.Value, str]], default_label: str,
    ) -> None:
        default = self.__block_or_unreachable(default_label, "match.unreach")
        instruction = self.builder.switch(value, default)  # type: ignore
        for case_value, target in cases:
            instruction.add_case(case_value, self.__context.func.block(target))  # type: ignore

    def unreachable(self) -> None:
        self.builder.unreachable()  # type: ignore

    def runtime_fail(self, error_code: RuntimeErrorCode) -> None:
        self.__context.module.emit_runtime_fail(self.builder, error_code)
        self.builder.unreachable()  # type: ignore

    def continuation_for(self, cfg_label: str) -> str:
        """Return the LLVM block that currently terminates a CFG block."""
        return self.__continuations.get(cfg_label, cfg_label)

    def __next_sequence(self) -> int:
        seq = self.__check_seq
        self.__check_seq += 1
        return seq

    def __split_block_name(self, suffix: str, kind: str, seq: int) -> str:
        name = f"{self.__current_cfg_block}.{suffix}.{kind}.{seq}"
        if len(name) > 1000:
            return f"b{seq}.{kind}"
        return name

    def __block_or_unreachable(self, label: str, missing_name: str) -> ir.Block:
        if label:
            return self.__context.func.block(label)
        block = self.__context.func.new_block(missing_name)
        ir.IRBuilder(block).unreachable()
        return block


class PrimitiveIR:
    """Typed LLVM primitives with no YIAN pointer-safety policy."""

    def __init__(self, context: FunctionContext, flow: FunctionFlow) -> None:
        self.__context = context
        self.__flow = flow

    def undef(self, type_id: int) -> LLValue:
        ll_type = self.__context.ll_type_ctx.get_ll_type(type_id).ir_type
        return LLValue(type_id, ir.Constant(ll_type, ir.Undefined))  # type: ignore

    def bitcast(self, value: ir.Value, typ: ir.Type, name: str = "") -> ir.Value:
        """Force a pointer re-type when llvmlite would otherwise return its operand."""
        source_type: ir.Type = value.type  # type: ignore
        if isinstance(source_type, ir.PointerType) and isinstance(typ, ir.PointerType) \
                and typ.is_opaque and not source_type.is_opaque:
            instr = ir.instructions.CastInstr(self.__flow.builder.block, "bitcast", value, typ, name)  # type: ignore
            self.__flow.builder._insert(instr)  # type: ignore
            return instr
        return self.__flow.builder.bitcast(value, typ, name)  # type: ignore

    def insert_value(self, agg: LLValue, value: LLValue, index: int) -> LLValue:
        ir_value = self.__flow.builder.insert_value(agg.ir_val, value.ir_val, index)  # type: ignore
        return LLValue(agg.type_id, ir_value)

    def call_intrinsic(self, kind: IntrinsicKind, args: list[LLValue]) -> LLValue:
        intrinsics = self.__context.module.intrinsics
        callee = intrinsics.get(kind)
        raw_args = intrinsics.prepare_call_args(kind, [arg.ir_val for arg in args])
        result = self.__flow.builder.call(callee, raw_args)  # type: ignore
        type_id = intrinsics.return_type_id(kind)
        if type_id is None:
            type_id = self.__context.type_ctx.alloc_pointer(self.__context.type_ctx.u8_id)
        return LLValue(type_id, result)

    def copy_bytes(self, dest: ir.Value, src: ir.Value, count: ir.Value) -> None:
        """Copy between already-resolved raw addresses."""
        self.call_intrinsic(
            IntrinsicKind.MemCopy,
            [
                LLValue(self.__context.type_ctx.alloc_pointer(self.__context.type_ctx.u8_id), dest),
                LLValue(self.__context.type_ctx.alloc_pointer(self.__context.type_ctx.u8_id), src),
                LLValue(self.__context.type_ctx.u64_id, count),
            ],
        )


class FunctionCore:
    """Compose the shared function context, flow state, and primitive IR API."""

    def __init__(
        self,
        func: LLFunction,
        module: LLModule,
        ll_type_ctx: LLTypeCtx,
        type_ctx: TypeCtx,
        raw_pointers: bool,
    ) -> None:
        context = FunctionContext(func, module, type_ctx, ll_type_ctx, raw_pointers)
        self.__context = context
        self.__flow = FunctionFlow(context)
        self.__ir = PrimitiveIR(context, self.__flow)

    @property
    def context(self) -> FunctionContext:
        return self.__context

    @property
    def flow(self) -> FunctionFlow:
        return self.__flow

    @property
    def ir(self) -> PrimitiveIR:
        return self.__ir
