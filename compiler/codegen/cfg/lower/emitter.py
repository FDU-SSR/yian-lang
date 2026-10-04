"""Per-function CFG emitter with shared pointer-fact observation."""
from __future__ import annotations

from compiler.analysis.ty.context import TypeCtx
from compiler.codegen.cfg import ir as IR
from compiler.codegen.cfg.provenance import PointerFacts
from compiler.utils.log import CompilerLog


def _ch_block():
    """cfg.block logging channel (single underscore permits references from class bodies)."""
    return CompilerLog.get("cfg.block")


class FunctionEmitter:
    """Emission state and minimal primitives for one CFG function.

    - func/current_block/counter are the only mutable state.
    - new_name()/new_block() share counter for unique SSA and block names.
    - emit() updates pointer facts from each appended statement.
    - terminate()/position() update control-flow position only.
    """

    def __init__(self, pointers: PointerFacts) -> None:
        self.func: IR.Function = IR.Function(
            name="", type_id=0, blocks=[], entry=IR.Block("")
        )  # Placeholder replaced by bind().
        self.current_block: IR.Block = self.func.entry
        self.counter: int = 0
        self.__pointers = pointers

    def bind(self, func: IR.Function, entry: IR.Block) -> None:
        """Bind the function and entry block once at the start of the builder's build()."""
        self.func = func
        self.current_block = entry

    def new_name(self) -> str:
        name = str(self.counter)
        self.counter += 1
        return name

    def emit[StmtType: IR.Stmt](self, stmt: StmtType) -> StmtType:
        """Append *stmt*, observe its pointer facts, and return it."""
        self.current_block.stmts.append(stmt)
        self.__pointers.observe(stmt)
        return stmt

    def new_block(self, label: str) -> IR.Block:
        block = IR.Block(f"{label}.{self.counter}")
        self.counter += 1
        self.func.blocks.append(block)
        _ch_block().trace(lambda: f"new block {block.label}")
        return block

    def void_reg(self) -> IR.Value:
        return IR.Reg(name=self.new_name(), type_id=TypeCtx.void_id)

    def never_reg(self) -> IR.Value:
        return IR.Reg(name=self.new_name(), type_id=TypeCtx.never_id)

    def emit_phi(self, incoming: list[tuple[IR.Block, IR.Value]]) -> IR.Value:
        """Emit a phi node into the current block's dedicated phi list."""
        result = IR.Reg(name=self.new_name(), type_id=incoming[0][1].type_id)
        stmt = IR.Phi(result=result, incoming=incoming)
        self.current_block.phis.append(stmt)
        return stmt.result

    def terminate(self, term: IR.Terminator) -> None:
        """Set the current block's terminator; the caller handles check-state effects."""
        _ch_block().trace(lambda: f"{self.current_block.label} <- {type(term).__name__}")
        self.current_block.terminator = term

    def position(self, block: IR.Block) -> None:
        """Switch the current block; the caller handles check-state effects."""
        self.current_block = block
