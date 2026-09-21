"""检查插入 pass —— 物化语义标记 + 重放检查状态机。

下降侧只发"访问节点 + 语义标记"：`CheckRequest` 承载单看 IR 恢复不出的语义上下文
（`PtrDiff`/`PtrCmp` 前提、裸数组上界、视图访问、`T*→T&` 折算前提、切片构造源跨度），
`LiveKnownBegin/End` 承载"刚分配、尚未释放"的窗口。本 pass 按块顺序重放下降期那套
检查状态机——指针出处（`_Provenance` 从 IR 重放 raw / 帧内 / 出处根 / 刚分配窗口）、
块内去重、派生义务表（`ElementPtr`→`FieldPtr`→访问的检查合并）与失效冲刷——自行决定访问类检查
（`CheckRefAccess`/`CheckSafeAccess`/`CheckElementAccess`/`CheckInBounds`/
`CheckElementArith`/`CheckDelete`）的最终形态与位置。

下降侧的 `CheckState` 只保留出处三件，供它自己的 IR 形态判定使用（惰性左值路径的
`Cast.raw`、裸数组上界的 `is_raw` 门、视图 `live` 项）；判定本身已全部在本 pass 里。
"""
from __future__ import annotations

from compiler.analysis.ty import ty as Type
from compiler.analysis.ty.context import TypeCtx
from compiler.codegen.cfg import ir as IR
from compiler.codegen.cfg.lower.cfg_ctx import CfgCtx


# ---------------------------------------------------------------------------
# 出处重放（pass 侧的事实层）
# ---------------------------------------------------------------------------


class _Provenance:
    """按语句顺序重放下降期的指针出处（与下降侧 `mark_*`/`inherit_*` 一一对应）。

    raw / 帧内 / 出处根按同一套登记规则从 IR 重放（只增不清）；规则必须逐条复刻下降侧的
    **分支形状**（例如 `FieldPtr` 对非裸基址一律继承、`ElementPtr` 只在胖基址上继承），
    否则 pass 的判定会与下降期的 IR 形态分叉。刚分配窗口由 `LiveKnownBegin/End` 标记开合。
    """

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


def _materialize(request: IR.CheckRequest) -> IR.Stmt:
    """按 kind 物化标记（未知 kind 直接报错，避免静默丢检查）。"""
    match request.kind:
        case IR.CHECK_REQUEST_PTRDIFF:
            lhs, rhs = request.operands
            return IR.CheckPtrDiff(lhs=lhs, rhs=rhs)
        case IR.CHECK_REQUEST_PTRCMP:
            lhs, rhs = request.operands
            return IR.CheckPtrCmp(lhs=lhs, rhs=rhs)
        case IR.CHECK_REQUEST_RAW_BOUNDS:
            return IR.CheckRawBounds(index=request.operands[0], length=request.extra)
        case IR.CHECK_REQUEST_VIEW:
            return IR.CheckViewAccess(view=request.operands[0], live=request.live)
        case IR.CHECK_REQUEST_SLICE_NONEMPTY:
            return IR.CheckSliceNonEmpty(ptr=request.operands[0])
        case IR.CHECK_REQUEST_IN_BOUNDS:
            return IR.CheckInBounds(ptr=request.operands[0])
        case IR.CHECK_REQUEST_ELEMENT_ARITH:
            return IR.CheckElementArith(base=request.operands[0], offset=request.operands[1])
        case _:
            raise ValueError(f"unknown check request kind: {request.kind}")


class _CheckPlanner:
    """单个 CFG 函数的检查规划器：块内状态 + 逐语句决定检查节点。"""

    def __init__(self, func: IR.Function, ctx: CfgCtx) -> None:
        self.__func = func
        self.__ctx = ctx
        self.__prov = _Provenance(ctx.type_ctx, ctx.raw_pointers)
        self.__checked: set[tuple[str, ...]] = set()
        self.__elem: dict[str, tuple[IR.Reg, IR.Value, IR.Value]] = {}
        self.__field: dict[str, str] = {}
        self.__out: list[IR.Stmt] = []

    # ------------------------------------------------------------------
    # 去重键与去重（与下降期 `CheckState` 同规则）
    # ------------------------------------------------------------------

    def __value_key(self, v: IR.Value) -> str | None:
        if isinstance(v, IR.Reg):
            return v.name
        if isinstance(v, IR.IntLiteral):
            return f"lit:{v.value}"
        return None

    def __live_key(self, ptr: IR.Value) -> tuple[str, ...] | None:
        if not isinstance(ptr, IR.Reg):
            return None
        return (self.__prov.root_of(ptr) or ptr.name, "live")

    def __ptr_key(self, ptr: IR.Value, kind: str) -> tuple[str, ...] | None:
        name = self.__value_key(ptr)
        return None if name is None else (name, kind)

    def __pair_key(self, base: IR.Value, offset: IR.Value, kind: str) -> tuple[str, ...] | None:
        base_key = self.__value_key(base)
        off_key = self.__value_key(offset)
        if base_key is None or off_key is None:
            return None
        return (base_key, off_key, kind)

    def __dedup(self, key: tuple[str, ...] | None) -> bool:
        if key is None:
            return False
        if key in self.__checked:
            return True
        self.__checked.add(key)
        return False

    # ------------------------------------------------------------------
    # 派生义务表（ElementPtr→FieldPtr→访问）
    # ------------------------------------------------------------------

    def __pop_owed(self, base: IR.Value) -> IR.Value | None:
        """弹出 base 的挂起 in_bounds 义务，返回待检查的 elem（无义务返回 None）。"""
        if not isinstance(base, IR.Reg):
            return None
        owed_name = self.__field.pop(base.name, None)
        if owed_name is None:
            return None
        elem, _base, _offset = self.__elem[owed_name]
        return elem

    def __merge_access(self, ptr: IR.Value, live: bool = True) -> bool:
        """合并访问检查：`ptr` 是 elem.field（派生链可对且访问相邻）时，以单个合取
        检查（ElementArith 良构+无回绕 ∧ InBounds ∧ SafeAccess 的 live 项）替代
        FieldPtr 的 InBounds（挂起义务）与本访问的 SafeAccess。返回 True 表示已合并
        （调用方跳过 CheckSafeAccess 发射）。
        """
        if not isinstance(ptr, IR.Reg):
            return False
        elem_name = self.__field.get(ptr.name)
        if elem_name is None:
            return False
        # 义务已由合取检查承担（InBounds + live 并入）：弹出，避免失效点补发
        self.__field.pop(ptr.name)
        # SafeAccess(f) 的 live 项由合取检查承载（in_bounds(f,1) 恒真）：
        # 登记 f 的 safe 去重键，同指针后续访问共享
        self.__checked.add((ptr.name, "safe"))
        elem, base, offset = self.__elem[elem_name]
        if self.__dedup(self.__pair_key(base, offset, "eacc")):
            return True
        self.__out.append(IR.CheckElementAccess(base=base, offset=offset, ptr=elem, live=live))
        return True

    def __invalidate(self) -> None:
        """失效：先补发挂起合并义务（CheckInBounds），再清空去重/合并表。

        释放、锁槽写、任意函数副作用或块终结之前，已检查状态不再可靠；挂起义务
        （派生链可对的 FieldPtr 跳过的 in_bounds(elem,1)）在此补发，保证 one-past-end
        的 elem 取字段在任何逃逸（传参/返回/跨块）前报告安全错误。
        """
        if self.__field:
            for elem_name in dict.fromkeys(self.__field.values()):
                elem, _base, _offset = self.__elem[elem_name]
                self.__out.append(IR.CheckInBounds(ptr=elem))
        self.__checked.clear()
        self.__elem.clear()
        self.__field.clear()

    # ------------------------------------------------------------------
    # 各规则的决定（与下降期逐条等价）
    # ------------------------------------------------------------------

    def __plan_access(self, ptr: IR.Value, *, resolve_aliases: bool) -> None:
        """R4/R5：Load/Store 的访问前检（T& 只查 live；胖指针查 safe_access 或合取）。"""
        type_ctx = self.__ctx.type_ctx
        type_id = type_ctx.resolve_aliases(ptr.type_id) if resolve_aliases else ptr.type_id
        ptr_type = type_ctx[type_id]
        frame_locked = self.__prov.is_frame_locked(ptr) or self.__prov.is_live_known(ptr)
        live_covered = frame_locked or self.__dedup(self.__live_key(ptr))
        if isinstance(ptr_type, Type.RefType) and not self.__ctx.raw_pointers:
            if live_covered:
                return
            if self.__dedup(self.__ptr_key(ptr, "ref")):
                return
            self.__out.append(IR.CheckRefAccess(ptr=ptr))
        elif self.__prov.is_fat_pointer(ptr):
            if live_covered and self.__merge_access(ptr, live=False):
                return
            if not live_covered and self.__merge_access(ptr):
                return
            if self.__dedup(self.__ptr_key(ptr, "safe")):
                return
            self.__out.append(IR.CheckSafeAccess(ptr=ptr, live=not live_covered))

    def __plan_element_ptr(self, base: IR.Value, offset: IR.Value) -> None:
        """R11：ElementPtr 算术良构检查；嵌套派生链先补发挂起的 in_bounds(elem,1)。"""
        if not self.__prov.is_fat_pointer(base):
            return
        owed_elem = self.__pop_owed(base)
        if owed_elem is not None and not self.__dedup(self.__ptr_key(owed_elem, "ib")):
            self.__out.append(IR.CheckInBounds(ptr=owed_elem))
        if not self.__dedup(self.__pair_key(base, offset, "elarith")):
            self.__out.append(IR.CheckElementArith(base=base, offset=offset))

    def __plan_field_ptr(self, base: IR.Value) -> str | None:
        """R12：FieldPtr 重锚定前提；可对派生链挂起义务并入访问点合取检查。

        返回应登记到 FieldPtr 结果上的挂起 elem 名（无义务返回 None）。
        """
        merged_elem_name: str | None = None
        base_ty = self.__ctx.type_ctx[base.type_id]
        if isinstance(base_ty, Type.RefType) and not self.__ctx.raw_pointers:
            if self.__prov.is_frame_locked(base):
                return None
            if self.__dedup(self.__live_key(base)) or self.__dedup(self.__ptr_key(base, "ref")):
                return None
            self.__out.append(IR.CheckRefAccess(ptr=base))
            return None
        if self.__prov.is_fat_pointer(base):
            owed_elem = self.__pop_owed(base)
            if owed_elem is not None and not self.__dedup(self.__ptr_key(owed_elem, "ib")):
                self.__out.append(IR.CheckInBounds(ptr=owed_elem))
            entry = self.__elem.get(base.name) if isinstance(base, IR.Reg) else None
            if entry is not None:
                # 合并路径：in_bounds(elem,1) 挂起为义务，并入访问点的
                # CheckElementAccess 合取检查；若访问不相邻，失效点补发。
                merged_elem_name = entry[0].name
            elif not self.__dedup(self.__ptr_key(base, "ib")):
                self.__out.append(IR.CheckInBounds(ptr=base))
        return merged_elem_name

    def __plan_delete(self, ptr: IR.Value) -> None:
        """R14：del 四前提（is_heap ∧ live ∧ is_raw）——换代后检查状态失效。"""
        if self.__prov.is_del_target(ptr):
            self.__out.append(IR.CheckDelete(ptr=ptr))
            self.__invalidate()

    def __plan_marker(self, request: IR.CheckRequest) -> None:
        """物化语义标记；调用侧 receiver 折算前提与其它检查共享块内去重键。"""
        if request.kind == IR.CHECK_REQUEST_RECEIVER_IN_BOUNDS:
            ptr = request.operands[0]
            if self.__prov.is_fat_pointer(ptr) and not self.__dedup(self.__ptr_key(ptr, "ib")):
                self.__out.append(IR.CheckInBounds(ptr=ptr))
            return
        self.__out.append(_materialize(request))

    # ------------------------------------------------------------------
    # 块遍历
    # ------------------------------------------------------------------

    def __visit(self, stmt: IR.Stmt) -> None:
        match stmt:
            case IR.LiveKnownBegin() | IR.LiveKnownEnd():
                self.__prov.note(stmt)
                return
            case IR.CheckRequest():
                self.__plan_marker(stmt)
                return
            case IR.Load(ptr=ptr):
                self.__plan_access(ptr, resolve_aliases=True)
            case IR.Store(ptr=ptr):
                self.__plan_access(ptr, resolve_aliases=False)
            case IR.ElementPtr(result=reg, base=base, offset=offset):
                self.__plan_element_ptr(base, offset)
                self.__out.append(stmt)
                self.__prov.note(stmt)
                if self.__prov.is_fat_pointer(base):
                    self.__elem[reg.name] = (reg, base, offset)
                return
            case IR.FieldPtr(result=reg, base=base):
                merged_elem_name = self.__plan_field_ptr(base)
                self.__out.append(stmt)
                self.__prov.note(stmt)
                if merged_elem_name is not None:
                    self.__field[reg.name] = merged_elem_name
                return
            case IR.Delete(ptr=ptr):
                self.__plan_delete(ptr)
            case IR.Call() | IR.Invoke():
                self.__invalidate()
            case _:
                pass
        self.__out.append(stmt)
        self.__prov.note(stmt)

    def run(self) -> None:
        for block in self.__func.blocks:
            # 块内状态不跨块（下降期由 switch_to 清空）；刚分配窗口跨块，挂在 provenance 上。
            self.__checked.clear()
            self.__elem.clear()
            self.__field.clear()
            self.__out = []
            for stmt in block.stmts:
                self.__visit(stmt)
            if block.terminator is not None:
                self.__invalidate()
            block.stmts = self.__out


class InsertChecks:
    """检查插入 pass（管线第 2 段）：逐函数物化语义标记并决定访问类检查的形态。

    下降产物与它的 `CfgCtx` 在下降段结束时交出（见 `translator._CfgBuilder`），
    这里只按 `type_id` 取用；编排在 `main`。
    """

    def __init__(self, contexts: dict[int, CfgCtx]) -> None:
        self.__contexts = contexts

    def run(self) -> None:
        """函数间互不影响（状态都是单函数的），按下降序逐个跑。

        每个 ctx 自带它那一条 `function`，并共享本模块的完整函数表（`ctx.functions`）。
        """
        for ctx in self.__contexts.values():
            _CheckPlanner(ctx.function, ctx).run()
