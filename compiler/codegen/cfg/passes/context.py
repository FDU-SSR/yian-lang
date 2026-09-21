"""CFG pass 的共享上下文。

`PassContext` 只承载跨 pass 的**只读事实**与由构建器提供的**命名/诊断通道**；
检查状态、循环栈这类"只有某个 pass 写"的可变状态留在各自的 pass 里。
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from compiler.analysis.symbol.context import SymbolCtx
from compiler.analysis.ty.context import TypeCtx
from compiler.codegen.cfg import ir as IR
from compiler.codegen.cfg.passes.checks import CheckState
from compiler.frontend.lex.position import SrcSpan


@dataclass
class PassContext:
    """CFG pass 运行所需的共享事实。

    - `type_ctx` / `symbol_ctx`：类型与符号查询（只读）；
    - `raw_pointers`：诊断模式开关（pass 入口整体短路）；
    - `func_name` / `span` / `return_type`：诊断、错误定位与返回类型（构建器已算好）；
    - `new_void_value`：构造 void 型占位寄存器（命名计数器由构建器持有）。
    """

    type_ctx: TypeCtx
    symbol_ctx: SymbolCtx
    raw_pointers: bool
    func_name: str
    span: SrcSpan
    return_type: int
    new_void_value: Callable[[], IR.Value]
    checks: CheckState
