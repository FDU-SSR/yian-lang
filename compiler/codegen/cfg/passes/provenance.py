"""P8 §5.6 第 2(a) 步：从 IR 重建下降期的指针出处（raw / 帧内 / 出处根 / 刚分配窗口）。

下降侧目前仍自己做这些登记（判定还没搬）；本模块是**给 pass 用的重建**，并提供一个
对照校验：`verify` 在 pass 运行期把重建结果与下降期 `CheckState` 的单调集合逐点比对
（raw/frame_locked/fat_root 都是只增不清的集合，因此事后比对是可靠的；`live_known`
是窗口式的，改由 `LiveKnownBegin/End` 标记在 pass 内自行跟踪，不参与比对）。
"""
from __future__ import annotations

from compiler.analysis.ty import ty as Type
from compiler.analysis.ty.context import TypeCtx
from compiler.codegen.cfg import ir as IR
from compiler.codegen.cfg.passes.checks import CheckState


class Provenance:
    """按语句顺序重放出处登记（与下降侧 `mark_*`/`inherit_*` 一一对应）。"""

    def __init__(self, type_ctx: TypeCtx, raw_pointers: bool) -> None:
        self.__type_ctx = type_ctx
        self.__raw_pointers = raw_pointers
        self.__raw: set[str] = set()
        self.__frame: set[str] = set()
        self.__root: dict[str, str] = {}
        self.__live: set[str] = set()

    # -- 查询（与 CheckState 同名，便于对照） --

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

    def run(self, func: IR.Function) -> None:
        for block in func.blocks:
            for stmt in block.stmts:
                self.note(stmt)

    def note(self, stmt: IR.Stmt) -> None:
        match stmt:
            case IR.VarPtr(result=reg, raw=True):
                self.__mark_raw(reg)
            case IR.VarPtr(result=reg):
                # 帧内取址合成胖指针:raw 模式下帧锁不实体化(帧字段为 None),但下降侧
                # 仍照常登记帧内/出处,这里按同一条规则重建。
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
                # 字段派生:非裸基址一律继承(与下降侧 `build_field_ptr` 同规则)
                if self.is_raw(base):
                    self.__mark_raw(reg)
                else:
                    self.__inherit(reg, base)
            case IR.ElementPtr(result=reg, base=base):
                # 元素派生:只有胖基址才继承(与下降侧 `build_element_ptr` 同规则)
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


def verify(func: IR.Function, state: CheckState, prov: Provenance) -> None:
    """对照校验：重建的 raw/帧内/出处根必须与下降侧单调集合逐点一致（只读，不改行为）。"""
    def ptrs(stmt: IR.Stmt) -> list[IR.Value]:
        match stmt:
            case IR.Load(ptr=ptr) | IR.Store(ptr=ptr) | IR.Delete(ptr=ptr):
                return [ptr]
            case IR.FieldPtr(base=base) | IR.ElementPtr(base=base):
                return [base]
            case IR.Cast(value=value):
                return [value]
            case _:
                return []

    for block in func.blocks:
        for stmt in block.stmts:
            for ptr in ptrs(stmt):
                if not isinstance(ptr, IR.Reg):
                    continue
                assert prov.is_raw(ptr) == state.is_raw(ptr), \
                    f"provenance(raw) mismatch at {type(stmt).__name__}({ptr.name})"
                assert prov.is_frame_locked(ptr) == state.is_frame_locked(ptr), \
                    f"provenance(frame) mismatch at {type(stmt).__name__}({ptr.name})"
                assert prov.root_of(ptr) == state.root_of(ptr), \
                    f"provenance(root) mismatch at {type(stmt).__name__}({ptr.name})"
