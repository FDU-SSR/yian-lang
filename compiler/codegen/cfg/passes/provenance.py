"""从 IR 重建下降期的指针出处（raw / 帧内 / 出处根 / 刚分配窗口）。

检查插入 pass 用它代替下降期的 `CheckState`：raw / 帧内 / 出处根按同一套登记规则
从 IR 重放（只增不清），刚分配窗口由 `LiveKnownBegin/End` 标记开合。
"""
from __future__ import annotations

from compiler.analysis.ty import ty as Type
from compiler.analysis.ty.context import TypeCtx
from compiler.codegen.cfg import ir as IR


class Provenance:
    """按语句顺序重放出处登记（与下降侧 `mark_*`/`inherit_*` 一一对应）。"""

    def __init__(self, type_ctx: TypeCtx, raw_pointers: bool) -> None:
        self.__type_ctx = type_ctx
        self.__raw_pointers = raw_pointers
        self.__raw: set[str] = set()
        self.__frame: set[str] = set()
        self.__root: dict[str, str] = {}
        self.__live: set[str] = set()

    # -- 查询（与下降侧 `CheckState` 同名，语义同源） --

    def is_raw(self, ptr: IR.Value) -> bool:
        return isinstance(ptr, IR.Reg) and ptr.name in self.__raw

    def is_fat_pointer(self, ptr: IR.Value) -> bool:
        """胖指针判定（与下降侧 `PtrPredicates.is_fat_pointer` 同一规则）。"""
        if self.__raw_pointers or self.is_raw(ptr):
            return False
        ty = self.__type_ctx[ptr.type_id]
        if not isinstance(ty, Type.PointerType):
            return False
        return not self.__type_ctx.is_zst(ty.pointee_type)

    def is_del_target(self, ptr: IR.Value) -> bool:
        """`del` 目标判定（与下降侧 `PtrPredicates.is_del_target` 同一规则）。"""
        if self.__raw_pointers or self.is_raw(ptr):
            return False
        if self.__type_ctx.is_zst(ptr.type_id):
            return False
        ty = self.__type_ctx[ptr.type_id]
        return isinstance(ty, (Type.PointerType, Type.SliceType, Type.RefType))

    def is_frame_locked(self, ptr: IR.Value) -> bool:
        return isinstance(ptr, IR.Reg) and ptr.name in self.__frame

    def root_of(self, ptr: IR.Value) -> str | None:
        return self.__root.get(ptr.name) if isinstance(ptr, IR.Reg) else None

    def is_live_known(self, ptr: IR.Value) -> bool:
        if not isinstance(ptr, IR.Reg):
            return False
        return self.__root.get(ptr.name, ptr.name) in self.__live

    # -- 登记 --

    def __mark_raw(self, reg: IR.Value) -> None:
        if isinstance(reg, IR.Reg):
            self.__raw.add(reg.name)

    def __mark_frame(self, reg: IR.Value) -> None:
        if isinstance(reg, IR.Reg):
            self.__frame.add(reg.name)

    def __mark_root(self, reg: IR.Value) -> None:
        if isinstance(reg, IR.Reg):
            self.__root[reg.name] = reg.name

    def __inherit(self, derived: IR.Value, source: IR.Value) -> None:
        if not isinstance(derived, IR.Reg):
            return
        if self.is_frame_locked(source):
            self.__frame.add(derived.name)
        root = self.root_of(source)
        if root is not None:
            self.__root[derived.name] = root

    def note(self, stmt: IR.Stmt) -> None:
        match stmt:
            case IR.VarPtr(result=reg, raw=True):
                self.__mark_raw(reg)
            case IR.VarPtr(result=reg):
                # 帧内取址合成胖指针：raw 模式下帧锁不实体化（帧字段为 None），
                # 下降侧仍照常登记帧内/出处，这里按同一条规则重建。
                self.__mark_frame(reg)
                self.__mark_root(reg)
            case IR.Alloca(result=reg, raw=True):
                self.__mark_raw(reg)
            case IR.Alloca(result=reg, frame_word=word) if word is not None:
                self.__mark_frame(reg)
                self.__mark_root(reg)
            case IR.Malloc(result=reg):
                if not self.__raw_pointers:
                    self.__mark_root(reg)
            case IR.Cast(result=reg, value=value, raw=True):
                self.__mark_raw(reg)
            case IR.Cast(result=reg, value=value):
                self.__inherit(reg, value)
            case IR.FieldPtr(result=reg, base=base):
                # 字段派生：非裸基址一律继承（与下降侧 `build_field_ptr` 同规则）
                if self.is_raw(base):
                    self.__mark_raw(reg)
                else:
                    self.__inherit(reg, base)
            case IR.ElementPtr(result=reg, base=base):
                # 元素派生：只有胖基址才继承（与下降侧 `build_element_ptr` 同规则）
                if self.is_raw(base):
                    self.__mark_raw(reg)
                elif self.is_fat_pointer(base):
                    self.__inherit(reg, base)
            case IR.LiveKnownBegin(root=root) if isinstance(root, IR.Reg):
                self.__live.add(root.name)
            case IR.LiveKnownEnd(root=root) if isinstance(root, IR.Reg):
                self.__live.discard(root.name)
            case _:
                pass
