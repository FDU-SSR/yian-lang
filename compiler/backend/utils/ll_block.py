from dataclasses import dataclass

from compiler.utils.errors import CompilerError
from llvmlite import ir


@dataclass
class LoopCtx:
    continue_bb: ir.Block
    end_bb: ir.Block | None = None


@dataclass
class BlockCtx:
    end_bb: ir.Block | None = None


@dataclass
class SwitchCtx:
    default_bb: ir.Block
    switch_inst: ir.SwitchInstr
    end_bb: ir.Block | None = None


ControlFlowCtx = BlockCtx | LoopCtx | SwitchCtx


class LowLevelBlockManager:
    def __init__(self) -> None:
        self.__bb_id = 0
        self.__ctx_stack: list[ControlFlowCtx] = []

    @property
    def __current_ctx(self) -> ControlFlowCtx:
        if len(self.__ctx_stack) == 0:
            raise CompilerError("no current context")
        return self.__ctx_stack[-1]

    @property
    def __loop_ctx(self) -> LoopCtx:
        for x in reversed(self.__ctx_stack):
            if isinstance(x, LoopCtx):
                return x
        raise CompilerError("no loop context")

    def generate_bb(self, builder: ir.IRBuilder) -> ir.Block:
        bb = builder.append_basic_block(f".bb{self.__bb_id}")
        self.__bb_id += 1
        return bb

    def enter_loop(self, continue_bb: ir.Block):
        self.__ctx_stack.append(LoopCtx(continue_bb))

    def enter_block(self):
        self.__ctx_stack.append(BlockCtx())

    def enter_switch(self, builder: ir.IRBuilder, cond_value: ir.Value):
        default_bb = self.generate_bb(builder)
        switch_inst = builder.switch(cond_value, default_bb)
        self.__ctx_stack.append(SwitchCtx(default_bb, switch_inst))

    def __ctx_end_bb(self, ctx: ControlFlowCtx, builder: ir.IRBuilder) -> ir.Block:
        if ctx.end_bb is None:
            ctx.end_bb = self.generate_bb(builder)
        return ctx.end_bb

    def end_bb(self, builder: ir.IRBuilder) -> ir.Block:
        return self.__ctx_end_bb(self.__current_ctx, builder)

    def branch_end_bb(self, builder: ir.IRBuilder):
        assert builder.block is not None
        if builder.block.is_terminated:
            return

        end_bb = self.end_bb(builder)
        builder.branch(end_bb)

    def do_break(self, builder: ir.IRBuilder):
        assert builder.block is not None
        if builder.block.is_terminated:
            return

        end_bb = self.__ctx_end_bb(self.__loop_ctx, builder)
        builder.branch(end_bb)

    def do_continue(self, builder: ir.IRBuilder):
        if builder.block is None:
            raise CompilerError("cannot continue out side block")
        if builder.block.is_terminated:
            return

        builder.branch(self.__loop_ctx.continue_bb)

    def add_case(self, case_value: ir.Constant, case_bb: ir.Block):
        if not isinstance(self.__current_ctx, SwitchCtx):
            raise CompilerError("not in switch context")
        self.__current_ctx.switch_inst.add_case(case_value, case_bb)

    def set_default_bb(self, builder: ir.IRBuilder):
        if not isinstance(self.__current_ctx, SwitchCtx):
            raise CompilerError("not in switch context")
        builder.position_at_end(self.__current_ctx.default_bb)

    def exit_ctx(self, builder: ir.IRBuilder):
        if self.__current_ctx.end_bb is not None:
            builder.position_at_end(self.__current_ctx.end_bb)
        self.__ctx_stack.pop()
