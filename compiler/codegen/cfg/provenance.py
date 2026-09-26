"""Pointer provenance facts shared by CFG lowering and check insertion."""
from __future__ import annotations

from compiler.analysis.ty import ty as Type
from compiler.analysis.ty.context import TypeCtx
from compiler.codegen.cfg import ir as IR


class PointerFacts:
    """Observe emitted CFG statements and answer pointer-layout questions."""

    def __init__(self, type_ctx: TypeCtx, raw_pointers: bool) -> None:
        self.__type_ctx = type_ctx
        self.__raw_pointers = raw_pointers
        self.__raw: set[str] = set()
        self.__frame: set[str] = set()
        self.__root: dict[str, str] = {}
        self.__live: set[str] = set()

    def is_raw(self, ptr: IR.Value) -> bool:
        return isinstance(ptr, IR.Reg) and ptr.name in self.__raw

    def is_fat_pointer(self, ptr: IR.Value) -> bool:
        if self.__raw_pointers or self.is_raw(ptr):
            return False
        ty = self.__type_ctx[ptr.type_id]
        return isinstance(ty, Type.PointerType) and not self.__type_ctx.is_zst(ty.pointee_type)

    def is_fat_view(self, value: IR.Value) -> bool:
        if self.__raw_pointers:
            return False
        ty = self.__type_ctx[value.type_id]
        return isinstance(ty, (Type.SliceType, Type.StrType))

    def is_del_target(self, ptr: IR.Value) -> bool:
        if self.__raw_pointers or self.is_raw(ptr) or self.__type_ctx.is_zst(ptr.type_id):
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

    def observe(self, stmt: IR.Stmt) -> None:
        """Apply the same transfer rule during emission and pass replay."""
        match stmt:
            case IR.VarPtr(result=reg, raw=True):
                self.__mark_raw(reg)
            case IR.VarPtr(result=reg):
                self.__mark_frame(reg)
                self.__mark_root(reg)
            case IR.Alloca(result=reg, raw=True):
                self.__mark_raw(reg)
            case IR.Alloca(result=reg, frame_word=word) if word is not None:
                self.__mark_frame(reg)
                self.__mark_root(reg)
            case IR.Malloc(result=reg) | IR.Realloc(result=reg):
                if not self.__raw_pointers:
                    self.__mark_root(reg)
            case IR.Cast(result=reg, raw=True):
                self.__mark_raw(reg)
            case IR.Cast(result=reg, value=value):
                self.__inherit(reg, value)
            case IR.FieldPtr(result=reg, base=base) | IR.EnumPayloadFieldPtr(result=reg, address=base):
                if self.is_raw(base):
                    self.__mark_raw(reg)
                else:
                    self.__inherit(reg, base)
            case IR.ElementPtr(result=reg, base=base):
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
