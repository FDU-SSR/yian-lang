"""下降侧指针出处状态：裸指针 / 帧内锁 / 出处根。

检查的**判定**（去重、派生义务表、刚分配窗口、失效冲刷）已全部搬进检查插入 pass
（`passes/insert_checks.py`，出处由 `passes/provenance.py` 按同一套登记规则从 IR 重放）；
这里只保留下降期自己还要用的出处事实——惰性左值路径的裸性（决定 `Cast.raw`、
`ElementPtr` 派生与裸数组上界门）、帧锁出处与出处根（视图访问的 `live` 项）。
"""
from __future__ import annotations

from compiler.codegen.cfg import ir as IR


class CheckState:
    """单个 CFG 函数的指针出处状态（只增集合，不参与检查决定）。"""

    def __init__(self) -> None:
        self.__raw_ptrs: set[str] = set()
        self.__frame_locked: set[str] = set()
        self.__fat_root: dict[str, str] = {}

    def is_raw(self, ptr: IR.Value) -> bool:
        """惰性左值路径:裸指针判定——寄存器名在裸集合中(未取址左值及其裸派生)。"""
        return isinstance(ptr, IR.Reg) and ptr.name in self.__raw_ptrs

    def mark_raw(self, ptr: IR.Value) -> None:
        if isinstance(ptr, IR.Reg):
            self.__raw_ptrs.add(ptr.name)

    def mark_frame_locked(self, ptr: IR.Value) -> None:
        if isinstance(ptr, IR.Reg):
            self.__frame_locked.add(ptr.name)

    def mark_root(self, ptr: IR.Value) -> None:
        """把自己登记为锁字段出处（VarPtr / Malloc / Alloca 的锚点）。"""
        if isinstance(ptr, IR.Reg):
            self.__fat_root[ptr.name] = ptr.name

    def root_of(self, ptr: IR.Value) -> str | None:
        """胖指针锁字段的出处(SSA 名);未知出处返回 None。"""
        if not isinstance(ptr, IR.Reg):
            return None
        return self.__fat_root.get(ptr.name)

    def is_frame_locked(self, ptr: IR.Value) -> bool:
        """指针的锁字段是否取自当前函数帧锁槽(live 恒真)。"""
        return isinstance(ptr, IR.Reg) and ptr.name in self.__frame_locked

    def inherit_frame_lock(self, derived: IR.Value, source: IR.Value) -> None:
        """派生指针继承源指针的锁字段:源是帧内指针时,派生结果也是。"""
        if isinstance(derived, IR.Reg) and self.is_frame_locked(source):
            self.__frame_locked.add(derived.name)

    def inherit_root(self, derived: IR.Value, source: IR.Value) -> None:
        """派生指针继承源指针的锁字段出处(ElementPtr/FieldPtr/Cast 不改 lock/key)。"""
        if not isinstance(derived, IR.Reg):
            return
        root = self.root_of(source)
        if root is not None:
            self.__fat_root[derived.name] = root
