"""C3：检查簇的状态与算法（出处/活跃度 + 去重/合并/失效）。

从 `CfgBuilder` 整体搬出的状态与判定：构建器不再自己持有这七个集合/映射，
而是持有一个 `CheckState` 实例并调用它的公开方法。搬移保持逐条等价
（见 `docs/plan/cfg-builder-pass-split-plan.md` §4 的规则表）。

本阶段只做"状态搬家"：检查的**插入点**仍在下降侧（P8 才升级为独立 pass）。
补发挂起义务需要写 IR，因此这里持有 `FunctionEmitter`。
"""
from __future__ import annotations

from compiler.codegen.cfg import ir as IR
from compiler.codegen.cfg.passes.emitter import FunctionEmitter
from compiler.utils.log import CompilerLog


def _ch_block():
    return CompilerLog.get("cfg.block")


class CheckState:
    """单个 CFG 函数的检查状态。

    字段（原来的 `CfgBuilder.__*`）：

    - `raw_ptrs`：裸指针寄存器名（跳检查）；
    - `frame_locked`：锁字段取自本函数帧锁槽（live 恒真）；
    - `live_known`：刚分配、尚未释放的锁字段出处（live 恒真）；
    - `fat_root`：锁字段出处（去重键的基础）；
    - `checked` / `elem_derived` / `field_derived`：块内已发检查、派生链与挂起义务。
    """

    def __init__(self, emitter: FunctionEmitter) -> None:
        self.__emit = emitter
        self.__raw_ptrs: set[str] = set()
        self.__frame_locked: set[str] = set()
        self.__live_known: set[str] = set()
        self.__fat_root: dict[str, str] = {}
        self.__checked: set[tuple[str, ...]] = set()
        self.__elem_derived: dict[str, tuple[IR.Value, IR.Value, IR.Value]] = {}
        self.__field_derived: dict[str, str] = {}

    # ------------------------------------------------------------------
    # 出处 / 活跃度
    # ------------------------------------------------------------------

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

    def is_live_known(self, ptr: IR.Value) -> bool:
        """指针的锁字段来自一次刚发生、尚未释放的分配(live 恒真)。

        只由填充循环这类"分配后立即写、循环体内不可能 del"的封闭区域登记;
        登记窗口内不存在 Delete, 因此不需要失效跟踪。与帧锁一样, 只跳过 live
        项, in_bounds 等空间检查照常发射。
        """
        if not isinstance(ptr, IR.Reg):
            return False
        return self.__fat_root.get(ptr.name, ptr.name) in self.__live_known

    def mark_live_known(self, root: str) -> None:
        self.__live_known.add(root)

    def unmark_live_known(self, root: str) -> None:
        self.__live_known.discard(root)

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

    # ------------------------------------------------------------------
    # 去重键与去重
    # ------------------------------------------------------------------

    def live_key(self, ptr: IR.Value) -> tuple[str, ...] | None:
        """live 项的去重键:按锁字段出处;出处未知时退化为指针自身。"""
        if not isinstance(ptr, IR.Reg):
            return None
        return (self.__fat_root.get(ptr.name, ptr.name), "live")

    def value_key(self, v: IR.Value) -> str | None:
        """检查合并:SSA 值去重键——寄存器用名,整数字面量用值;其余返回 None(不参与去重)。"""
        if isinstance(v, IR.Reg):
            return v.name
        if isinstance(v, IR.IntLiteral):
            return f"lit:{v.value}"
        return None

    def ptr_key(self, ptr: IR.Value, kind: str) -> tuple[str, ...] | None:
        """检查合并:单指针检查(CheckSafeAccess/CheckInBounds/CheckRefAccess)去重键。"""
        name = self.value_key(ptr)
        if name is None:
            return None
        return (name, kind)

    def pair_key(self, base: IR.Value, offset: IR.Value, kind: str) -> tuple[str, ...] | None:
        """检查合并:(base, offset) 二元检查(CheckElementArith / CheckElementAccess)去重键。"""
        base_key = self.value_key(base)
        off_key = self.value_key(offset)
        if base_key is None or off_key is None:
            return None
        return (base_key, off_key, kind)

    def dedup(self, key: tuple[str, ...] | None) -> bool:
        """保守去重:键命中(同块同 SSA 值、中间无失效已检查)→ True 跳过发射;
        未命中 → 登记并返回 False。键为 None(非 SSA 值)→ 不参与去重。
        """
        if key is None:
            return False
        if key in self.__checked:
            return True
        self.__checked.add(key)
        return False

    # ------------------------------------------------------------------
    # 派生链与挂起义务
    # ------------------------------------------------------------------

    def note_element_derived(self, elem_ptr: IR.Value, base: IR.Value, offset: IR.Value) -> None:
        """登记 ElementPtr 派生链 (elem_ptr, base, offset)，供 FieldPtr→Load/Store 合并。"""
        if isinstance(elem_ptr, IR.Reg):
            self.__elem_derived[elem_ptr.name] = (elem_ptr, base, offset)

    def note_field_derived(self, field_ptr: IR.Value, elem_name: str) -> None:
        if isinstance(field_ptr, IR.Reg):
            self.__field_derived[field_ptr.name] = elem_name

    def propagate_field_derived(self, dst: IR.Value, src: IR.Value) -> None:
        """cast 等 identity 派生：把 src 的挂起义务沿 dst 传播。"""
        if isinstance(dst, IR.Reg) and isinstance(src, IR.Reg) and src.name in self.__field_derived:
            self.__field_derived[dst.name] = self.__field_derived.pop(src.name)

    def has_owed_in_bounds(self, base: IR.Value) -> bool:
        return isinstance(base, IR.Reg) and base.name in self.__field_derived

    def pop_owed_in_bounds(self, base: IR.Value) -> IR.Value | None:
        """弹出 base 的挂起 in_bounds 义务，返回待检查的 elem（无义务返回 None）。"""
        if not isinstance(base, IR.Reg):
            return None
        owed_name = self.__field_derived.pop(base.name, None)
        if owed_name is None:
            return None
        elem, _base, _offset = self.__elem_derived[owed_name]
        return elem

    def elem_entry(self, base: IR.Value) -> IR.Value | None:
        """base 是 ElementPtr 结果时返回其 elem（合并路径用），否则 None。"""
        if not isinstance(base, IR.Reg):
            return None
        entry = self.__elem_derived.get(base.name)
        if entry is None:
            return None
        return entry[0]

    def merge_access(self, ptr: IR.Value, live: bool = True) -> bool:
        """合并访问检查:ptr 是 elem.field(派生链可对且访问相邻)时,以单个
        合取检查(ElementArith 良构+无回绕 ∧ InBounds ∧ SafeAccess 的 live 项)
        替代 FieldPtr 的 InBounds(挂起义务)与本访问的 SafeAccess。SafeAccess
        的 in_bounds(f,1) 对重锚定字段指针(index=0,size=1)恒真、live(f)=
        live(elem)(锁字段继承),合取谓词 = 原三者,禁止丢 no-wrap/live 任一
        子项。返回 True 表示已合并(调用方跳过 CheckSafeAccess 发射)。
        """
        if not isinstance(ptr, IR.Reg):
            return False
        elem_name = self.__field_derived.get(ptr.name)
        if elem_name is None:
            return False
        # 义务已由合取检查承担(InBounds + live 并入):弹出,避免失效点补发
        self.__field_derived.pop(ptr.name)
        # SafeAccess(f) 的 live 项由合取检查承载(in_bounds(f,1) 恒真):
        # 登记 f 的 safe 去重键,同指针后续访问共享
        self.__checked.add((ptr.name, "safe"))
        elem, base, offset = self.__elem_derived[elem_name]
        key = self.pair_key(base, offset, "eacc")
        if self.dedup(key):
            return True
        self.__emit.emit(IR.CheckElementAccess(base=base, offset=offset, ptr=elem, live=live))
        return True

    # ------------------------------------------------------------------
    # 失效
    # ------------------------------------------------------------------

    def invalidate(self) -> None:
        """检查合并 失效:先补发挂起合并义务(CheckInBounds),再清空去重/合并表。

        于 Delete / WriteLockSlot / 调用 / 终止符之前调用——中间有失效操作
        (释放、锁槽写、任意函数副作用)时,已检查状态不再可靠,逐访问前提
        必须重新建立。挂起义务(派生链可对的 FieldPtr 跳过的 in_bounds(elem,1))
        在此补发,保证 one-past-end 的 elem 取字段在任何逃逸(传参 / 返回 / 跨块)前
        报告安全错误(前提不丢)。
        """
        if self.__field_derived:
            for elem_name in dict.fromkeys(self.__field_derived.values()):
                elem, _base, _offset = self.__elem_derived[elem_name]
                self.__emit.emit(IR.CheckInBounds(ptr=elem))
                _ch_block().debug(lambda: "check merge FieldPtr→invalidate: 补发 in_bounds(elem,1) (合并检查义务)")
        self.clear_block()

    def clear_block(self) -> None:
        """块内状态清空（块切换兜底；义务已由 invalidate/终结符补发）。"""
        self.__checked.clear()
        self.__elem_derived.clear()
        self.__field_derived.clear()
