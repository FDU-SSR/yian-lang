"""CFG 发射句柄（当前函数 / 当前块 / 命名计数器 + 最小发射原语）。

下降各簇与后续 pass 共用这一个句柄，而不是各自持有 `CfgBuilder` 的私有状态。
这里只放"往 IR 里写东西"的原语；块终结与块切换的**检查状态副作用**留在构建器
（`CfgBuilder.__set_terminator` / `__switch_to`），句柄本身无副作用。
"""
from __future__ import annotations

from compiler.analysis.ty.context import TypeCtx
from compiler.codegen.cfg import ir as IR
from compiler.utils.log import CompilerLog


def _ch_block():
    """cfg.block 日志通道（类体内引用，按约定用单下划线）。"""
    return CompilerLog.get("cfg.block")


class FunctionEmitter:
    """单个 CFG 函数的发射状态与最小原语。

    - `func` / `current_block` / `counter` 是唯一的可变状态；
    - `new_name()` / `new_block()` 共用 `counter`，保证 SSA 名与块名全局唯一；
    - `terminate()` / `position()` 只做赋值与日志，不做检查状态维护。
    """

    def __init__(self) -> None:
        self.func: IR.Function = IR.Function(
            name="", type_id=0, blocks=[], entry=IR.Block("")
        )  # placeholder; bind() 后替换
        self.current_block: IR.Block = self.func.entry
        self.counter: int = 0

    def bind(self, func: IR.Function, entry: IR.Block) -> None:
        """绑定到本次构建的函数与入口块（构建器在 `build()` 开头调用一次）。"""
        self.func = func
        self.current_block = entry

    def new_name(self) -> str:
        name = str(self.counter)
        self.counter += 1
        return name

    def emit[StmtType: IR.Stmt](self, stmt: StmtType) -> StmtType:
        """Append *stmt* to the current block and return it."""
        self.current_block.stmts.append(stmt)
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
        """写入当前块的终结符（调用方负责检查状态副作用）。"""
        _ch_block().trace(lambda: f"{self.current_block.label} <- {type(term).__name__}")
        self.current_block.terminator = term

    def position(self, block: IR.Block) -> None:
        """切换当前块（调用方负责检查状态副作用）。"""
        self.current_block = block
