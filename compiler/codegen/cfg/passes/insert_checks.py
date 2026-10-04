"""Materialize check requests and plan access checks from CFG pointer facts.

PointerFacts replays emitted statements in block order. Check placement also
tracks per-block deduplication and deferred derived-pointer obligations.
"""
from __future__ import annotations

from compiler.analysis.ty import ty as Type
from compiler.codegen.cfg import ir as IR
from compiler.codegen.cfg.module import CfgModule
from compiler.codegen.cfg.provenance import PointerFacts



def _materialize(request: IR.CheckRequest) -> IR.Stmt:
    """Materialize markers by kind; reject unknown kinds rather than silently dropping checks."""
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
    """Plan checks for one CFG function using block-local state and per-statement decisions."""

    def __init__(self, func: IR.Function, ctx: CfgModule) -> None:
        self.__func = func
        self.__ctx = ctx
        self.__prov = PointerFacts(ctx.type_ctx, ctx.raw_pointers)
        self.__checked: set[tuple[str, ...]] = set()
        self.__elem: dict[str, tuple[IR.Reg, IR.Value, IR.Value]] = {}
        self.__field: dict[str, str] = {}
        self.__out: list[IR.Stmt] = []

    # ------------------------------------------------------------------
    # Deduplicate checks within a block.
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
    # Derivation obligations (ElementPtr -> FieldPtr -> access).
    # ------------------------------------------------------------------

    def __pop_owed(self, base: IR.Value) -> IR.Value | None:
        """Pop base's pending bounds obligation; return the element to check, or None."""
        if not isinstance(base, IR.Reg):
            return None
        owed_name = self.__field.pop(base.name, None)
        if owed_name is None:
            return None
        elem, _base, _offset = self.__elem[owed_name]
        return elem

    def __merge_access(self, ptr: IR.Value, live: bool = True) -> bool:
        """Combine access checks when ptr is elem.field in an adjacent, matched derivation chain.
        One conjunction (ElementArith validity/no-wrap, InBounds, and SafeAccess's live term)
        discharges FieldPtr's pending bounds obligation and this access's SafeAccess check.
        Return True when combined, so the caller skips emitting CheckSafeAccess.
        """
        if not isinstance(ptr, IR.Reg):
            return False
        elem_name = self.__field.get(ptr.name)
        if elem_name is None:
            return False
        # The conjunction discharges InBounds and live; pop the obligation to avoid re-emitting at invalidation.
        self.__field.pop(ptr.name)
        # The conjunction covers SafeAccess(f)'s live term; in_bounds(f,1) is always true.
        # Record f's safe-check key so accesses through the same pointer share the check.
        self.__checked.add((ptr.name, "safe"))
        elem, base, offset = self.__elem[elem_name]
        if self.__dedup(self.__pair_key(base, offset, "eacc")):
            return True
        self.__out.append(IR.CheckElementAccess(base=base, offset=offset, ptr=elem, live=live))
        return True

    def __invalidate(self) -> None:
        """Invalidate: discharge pending combined obligations, then clear cached checks and combinations.

        Release, slot writes, arbitrary call effects, and block termination invalidate cached conclusions.
        Emit bounds obligations skipped by matched FieldPtr chains before those operations, so taking
        a field of a one-past-end elem fails before argument/return/block escape.
        """
        if self.__field:
            for elem_name in dict.fromkeys(self.__field.values()):
                elem, _base, _offset = self.__elem[elem_name]
                self.__out.append(IR.CheckInBounds(ptr=elem))
        self.__checked.clear()
        self.__elem.clear()
        self.__field.clear()

    # ------------------------------------------------------------------
    # Per-operation check decisions.
    # ------------------------------------------------------------------

    def __plan_access(self, ptr: IR.Value) -> None:
        """Load/Store access prechecks: T& checks live; fat pointers use safe_access or a conjunction."""
        type_ctx = self.__ctx.type_ctx
        type_id = ptr.type_id
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
        """Check ElementPtr arithmetic validity; discharge nested pending in_bounds(elem,1) first."""
        if not self.__prov.is_fat_pointer(base):
            return
        owed_elem = self.__pop_owed(base)
        if owed_elem is not None and not self.__dedup(self.__ptr_key(owed_elem, "ib")):
            self.__out.append(IR.CheckInBounds(ptr=owed_elem))
        if not self.__dedup(self.__pair_key(base, offset, "elarith")):
            self.__out.append(IR.CheckElementArith(base=base, offset=offset))

    def __plan_field_ptr(self, base: IR.Value) -> str | None:
        """FieldPtr re-anchoring preconditions; matched chains defer obligations to combined access checks.

        Return the pending elem name to attach to the FieldPtr result, or None.
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
                # Combined path: defer in_bounds(elem,1) to the access site's
                # CheckElementAccess conjunction; invalidation discharges it if access is not adjacent.
                merged_elem_name = entry[0].name
            elif not self.__dedup(self.__ptr_key(base, "ib")):
                self.__out.append(IR.CheckInBounds(ptr=base))
        return merged_elem_name

    def __plan_delete(self, ptr: IR.Value) -> None:
        """del preconditions (is_heap, live, is_raw); generation changes invalidate check state."""
        if self.__prov.is_del_target(ptr):
            self.__out.append(IR.CheckDelete(ptr=ptr))
            self.__invalidate()

    def __plan_marker(self, request: IR.CheckRequest) -> None:
        """Materialize semantic markers; receiver-conversion preconditions share block-local check keys."""
        if request.kind == IR.CHECK_REQUEST_RECEIVER_IN_BOUNDS:
            ptr = request.operands[0]
            if self.__prov.is_fat_pointer(ptr) and not self.__dedup(self.__ptr_key(ptr, "ib")):
                self.__out.append(IR.CheckInBounds(ptr=ptr))
            return
        self.__out.append(_materialize(request))

    # ------------------------------------------------------------------
    # Block traversal.
    # ------------------------------------------------------------------

    def __visit(self, stmt: IR.Stmt) -> None:
        match stmt:
            case IR.LiveKnownBegin() | IR.LiveKnownEnd():
                self.__prov.observe(stmt)
                return
            case IR.CheckRequest():
                self.__plan_marker(stmt)
                return
            case IR.Load(ptr=ptr) | IR.EnumIsVariant(address=ptr) | IR.Store(ptr=ptr):
                self.__plan_access(ptr)
            case IR.ElementPtr(result=reg, base=base, offset=offset):
                self.__plan_element_ptr(base, offset)
                self.__out.append(stmt)
                self.__prov.observe(stmt)
                if self.__prov.is_fat_pointer(base):
                    self.__elem[reg.name] = (reg, base, offset)
                return
            case IR.FieldPtr(result=reg, base=base) | IR.EnumPayloadFieldPtr(result=reg, address=base):
                merged_elem_name = self.__plan_field_ptr(base)
                self.__out.append(stmt)
                self.__prov.observe(stmt)
                if merged_elem_name is not None:
                    self.__field[reg.name] = merged_elem_name
                return
            case IR.Delete(ptr=ptr):
                self.__plan_delete(ptr)
            case IR.Realloc(ptr=ptr):
                self.__plan_delete(ptr)
            case IR.Call() | IR.Invoke() | IR.TraitObjectInvoke():
                self.__invalidate()
            case _:
                pass
        self.__out.append(stmt)
        self.__prov.observe(stmt)

    def run(self) -> None:
        for block in self.__func.blocks:
            # Check state is block-local; fresh-allocation windows span blocks through provenance.
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
    """Materialize checks for each function in a CFG module."""

    def __init__(self, ctx: CfgModule) -> None:
        self.__ctx = ctx

    def run(self) -> None:
        """Process functions in lowering order with independent per-function state.

        ctx.functions contains all functions in the module and supplies any cross-function information.
        """
        for record in self.__ctx.functions.values():
            _CheckPlanner(record.function, self.__ctx).run()
