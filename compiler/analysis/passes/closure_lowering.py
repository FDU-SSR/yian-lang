"""Post-TypeCheck pass: remove all ClosureType references from HIR.

Phase 1 — lower closure DefPoints to method DefPoints:
  Rewrites capture-variable references (Var → FieldAccess(self, field)),
  injects ``self``, changes DefPoint type_id to call_method_type_id.

Phase 2 — erase ClosureType from all HIR bodies:
  Walks every DefPoint and replaces any remaining ClosureType reference
  with the corresponding struct/method type so CFG/LLVM never see ClosureType.
"""

from __future__ import annotations

from typing import cast

from compiler.analysis.symbol.context import SymbolCtx
from compiler.analysis.symbol.symbol import SymbolKind
from compiler.analysis.ty import ty as Type
from compiler.analysis.ty.context import TypeCtx
from compiler.analysis.ty.ty import AccessMode, StructField
from compiler.analysis.unit import hir as HIR
from compiler.analysis.unit.def_point import DefPoint
from compiler.frontend.parse.operator import UnaryOperator


class ClosureLowering:
    """Post-TypeCheck pass that eliminates ClosureType from the HIR."""

    def __init__(self, def_points: dict[int, DefPoint], type_ctx: TypeCtx):
        self.__def_points = def_points
        self.__type_ctx = type_ctx
        # Per-closure state for the HIR rewrite
        self.__sid_to_field: dict[int, tuple[str, int]] = {}
        self.__self_sid: int = 0
        self.__struct_type_id: int = 0
        self.__self_ptr_type_id: int = 0

    def run(self) -> None:
        # --- Phase 1: lower closure DefPoints ---
        for dp in list(self.__def_points.values()):
            if dp.body is None:
                continue
            ty = self.__type_ctx[dp.type_id]
            if not isinstance(ty, Type.ClosureType):
                continue
            self.__lower_one(dp, ty)

        # --- Phase 2: erase ClosureType from all HIR bodies and symbols ---
        for dp in self.__def_points.values():
            if dp.body is None:
                continue
            dp.body = cast(HIR.Block, self.__rewrite_type_ids(dp.body))
            self.__rewrite_symbol_types(dp.symbol_ctx)

    # ------------------------------------------------------------------
    # Phase 1 — per-closure lowering
    # ------------------------------------------------------------------

    def __lower_one(self, dp: DefPoint, closure_ty: Type.ClosureType) -> None:
        struct_type_id = closure_ty.struct_type_id
        call_method_type_id = closure_ty.call_method_type_id

        # --- build capture-sid → field mapping ---
        capture_sids: dict[str, int] = {}
        for cv in closure_ty.captured_vars:
            sym = dp.symbol_ctx.lookup(cv.name)
            if sym is not None:
                capture_sids[cv.name] = sym.symbol_id

        # --- create new symbol_ctx with self ---
        call_sym_ctx = SymbolCtx()
        capture_sid_set = set(capture_sids.values())
        for sid, sym in dp.symbol_ctx.items():
            if sid in capture_sid_set:
                continue
            call_sym_ctx.add_symbol_with_id(sid, sym.name, sym.kind, sym.type_id)
        # self is a pointer (like in __check_method)
        self_ptr_type_id = self.__type_ctx.alloc_pointer(struct_type_id)
        self_sid = call_sym_ctx.add_symbol("self", SymbolKind.Variable, self_ptr_type_id)
        assert self_sid is not None

        # --- collect param sids ---
        param_sids: list[int] = []
        for param in closure_ty.parameters:
            sym = call_sym_ctx.lookup(param.name)
            if sym is not None:
                param_sids.append(sym.symbol_id)

        # --- rewrite call method body ---
        self.__setup_rewrite(capture_sids, closure_ty.captured_vars, struct_type_id, self_ptr_type_id, self_sid)
        assert dp.body is not None
        rewritten_body = self.__rewrite_capture_refs(dp.body)

        # --- convert DefPoint from Closure to Method ---
        dp.body = rewritten_body
        dp.symbol_ctx = call_sym_ctx
        dp.params = [self_sid] + param_sids
        dp.locals = dp.params.copy()
        dp.type_id = call_method_type_id

    # ------------------------------------------------------------------
    # Phase 2 — erase ClosureType from all HIR bodies
    # ------------------------------------------------------------------

    def __rewrite_type_ids(self, expr: HIR.Expr) -> HIR.Expr:
        """Recursively replace ClosureType references with struct/method types."""
        match expr:
            case HIR.Var(type_id=tid) if self.__is_closure_type(tid):
                expr.type_id = self.__struct_id(tid)

            case HIR.Closure(type_id=tid, captures=captures):
                return HIR.StructConstruct(
                    span=expr.span,
                    struct_id=self.__struct_id(tid),
                    field_values={k: self.__rewrite_type_ids(v) for k, v in captures.items()},
                    type_id=self.__struct_id(tid),
                    is_place=expr.is_place,
                )

            case HIR.Invoke(callable=callee, args=args, type_id=ret_tid) \
                    if self.__is_closure_type(callee.type_id):
                closure_tid = callee.type_id  # save before rewrite mutates it
                return HIR.MethodCall(
                    span=expr.span,
                    receiver=self.__rewrite_type_ids(callee),
                    method_id=self.__method_id(closure_tid),
                    args=[self.__rewrite_type_ids(a) for a in args],
                    type_id=ret_tid,
                    is_place=expr.is_place,
                )

            case HIR.Block():
                expr.stmts = [self.__rewrite_type_ids(s) for s in expr.stmts]
            case HIR.Binary():
                expr.left = self.__rewrite_type_ids(expr.left)
                expr.right = self.__rewrite_type_ids(expr.right)
            case HIR.Unary():
                expr.operand = self.__rewrite_type_ids(expr.operand)
            case HIR.Call():
                expr.args = [self.__rewrite_type_ids(a) for a in expr.args]
            case HIR.Invoke():
                expr.callable = self.__rewrite_type_ids(expr.callable)
                expr.args = [self.__rewrite_type_ids(a) for a in expr.args]
            case HIR.If():
                expr.cond = self.__rewrite_type_ids(expr.cond)
                expr.then_branch = cast(HIR.Block, self.__rewrite_type_ids(expr.then_branch))
                if expr.else_branch is not None:
                    expr.else_branch = cast(HIR.Block, self.__rewrite_type_ids(expr.else_branch))
            case HIR.Loop():
                expr.body = cast(HIR.Block, self.__rewrite_type_ids(expr.body))
            case HIR.Match():
                expr.value = self.__rewrite_type_ids(expr.value)
                for arm in expr.arms:
                    arm.body = cast(HIR.Block, self.__rewrite_type_ids(arm.body))
            case HIR.Return() if expr.value is not None:
                expr.value = self.__rewrite_type_ids(expr.value)
            case HIR.Semi():
                expr.expr = self.__rewrite_type_ids(expr.expr)
            case HIR.Let() if expr.init is not None:
                expr.init = self.__rewrite_type_ids(expr.init)
            case HIR.StructConstruct():
                expr.field_values = {k: self.__rewrite_type_ids(v) for k, v in expr.field_values.items()}
            case HIR.MethodCall():
                expr.receiver = self.__rewrite_type_ids(expr.receiver)
                expr.args = [self.__rewrite_type_ids(a) for a in expr.args]
            case HIR.FieldAccess():
                expr.receiver = self.__rewrite_type_ids(expr.receiver)
            case HIR.Tuple():
                expr.field_values = [self.__rewrite_type_ids(f) for f in expr.field_values]
            case HIR.Array():
                expr.elements = [self.__rewrite_type_ids(e) for e in expr.elements]
            case HIR.ArrayRepeat():
                expr.element = self.__rewrite_type_ids(expr.element)
            case HIR.Cast():
                expr.value = self.__rewrite_type_ids(expr.value)
            case HIR.VariantConstruct() if expr.args is not None:
                expr.args = {k: self.__rewrite_type_ids(v) for k, v in expr.args.items()}
            case HIR.Panic():
                expr.message = self.__rewrite_type_ids(expr.message)
            case HIR.Delete():
                expr.target = self.__rewrite_type_ids(expr.target)
            case HIR.DynValue():
                expr.value = self.__rewrite_type_ids(expr.value)
            case HIR.DynBuffer():
                expr.length = self.__rewrite_type_ids(expr.length)
            case HIR.BitCast():
                expr.value = self.__rewrite_type_ids(expr.value)
            case HIR.AssumeInit():
                expr.value = self.__rewrite_type_ids(expr.value)
            case _:
                pass
        return expr

    def __rewrite_symbol_types(self, sym_ctx: SymbolCtx) -> None:
        """Update any symbol whose type is a ClosureType to use struct_type_id."""
        for _sid, sym in list(sym_ctx.items()):
            if self.__is_closure_type(sym.type_id):
                sym.type_id = self.__struct_id(sym.type_id)

    def __is_closure_type(self, type_id: int) -> bool:
        ty = self.__type_ctx[type_id]
        return isinstance(ty, Type.ClosureType)

    def __struct_id(self, closure_type_id: int) -> int:
        ty = self.__type_ctx[closure_type_id]
        assert isinstance(ty, Type.ClosureType)
        return ty.struct_type_id

    def __method_id(self, closure_type_id: int) -> int:
        ty = self.__type_ctx[closure_type_id]
        assert isinstance(ty, Type.ClosureType)
        return ty.call_method_type_id

    # ------------------------------------------------------------------
    # Capture reference rewriting (Phase 1)
    # ------------------------------------------------------------------

    def __setup_rewrite(self, capture_sids: dict[str, int], captured_vars: list[Type.CapturedVar], struct_type_id: int, self_ptr_type_id: int, self_sid: int) -> None:
        self.__sid_to_field.clear()
        for cv in captured_vars:
            sid = capture_sids.get(cv.name)
            if sid is not None:
                field = self.__type_ctx.get_struct_field_by_name(struct_type_id, cv.name)
                if field is not None:
                    self.__sid_to_field[sid] = (cv.name, field.type_id)
        self.__self_sid = self_sid
        self.__struct_type_id = struct_type_id
        self.__self_ptr_type_id = self_ptr_type_id

    def __rewrite_capture_refs(self, block: HIR.Block) -> HIR.Block:
        new_stmts = [self.__rewrite_one_capture(s) for s in block.stmts]
        return HIR.Block(span=block.span, stmts=new_stmts,
                         type_id=block.type_id, is_place=block.is_place)

    def __rewrite_one_capture(self, expr: HIR.Expr) -> HIR.Expr:
        if isinstance(expr, HIR.Var) and expr.symbol_id in self.__sid_to_field:
            field_name, field_type_id = self.__sid_to_field[expr.symbol_id]
            field = StructField(name=field_name, type_id=field_type_id,
                                access_mode=AccessMode.Private, index=0)
            # self is PointerType(struct); deref to get the struct value
            self_val = HIR.Var(
                span=expr.span,
                symbol_id=self.__self_sid,
                type_id=self.__self_ptr_type_id,
                is_place=True
            )
            deref_self = HIR.Unary(
                span=expr.span,
                op=UnaryOperator.Deref,
                operand=self_val,
                type_id=self.__struct_type_id,
                is_place=True
            )
            return HIR.FieldAccess(
                span=expr.span,
                receiver=deref_self,
                field=field,
                type_id=field_type_id,
                is_place=expr.is_place,
            )
        match expr:
            case HIR.Block():
                expr.stmts = [self.__rewrite_one_capture(s) for s in expr.stmts]
            case HIR.Binary():
                expr.left = self.__rewrite_one_capture(expr.left)
                expr.right = self.__rewrite_one_capture(expr.right)
            case HIR.Unary():
                expr.operand = self.__rewrite_one_capture(expr.operand)
            case HIR.Call():
                expr.args = [self.__rewrite_one_capture(a) for a in expr.args]
            case HIR.Invoke():
                expr.callable = self.__rewrite_one_capture(expr.callable)
                expr.args = [self.__rewrite_one_capture(a) for a in expr.args]
            case HIR.If():
                expr.cond = self.__rewrite_one_capture(expr.cond)
                expr.then_branch = self.__rewrite_capture_refs(expr.then_branch)
                if expr.else_branch is not None:
                    expr.else_branch = self.__rewrite_capture_refs(expr.else_branch)
            case HIR.Loop():
                expr.body = self.__rewrite_capture_refs(expr.body)
            case HIR.Match():
                expr.value = self.__rewrite_one_capture(expr.value)
                for arm in expr.arms:
                    arm.body = self.__rewrite_capture_refs(arm.body)
            case HIR.Return() if expr.value is not None:
                expr.value = self.__rewrite_one_capture(expr.value)
            case HIR.Semi():
                expr.expr = self.__rewrite_one_capture(expr.expr)
            case HIR.Let() if expr.init is not None:
                expr.init = self.__rewrite_one_capture(expr.init)
            case HIR.StructConstruct():
                expr.field_values = {k: self.__rewrite_one_capture(v) for k, v in expr.field_values.items()}
            case HIR.MethodCall():
                expr.receiver = self.__rewrite_one_capture(expr.receiver)
                expr.args = [self.__rewrite_one_capture(a) for a in expr.args]
            case HIR.FieldAccess():
                expr.receiver = self.__rewrite_one_capture(expr.receiver)
            case HIR.Tuple():
                expr.field_values = [self.__rewrite_one_capture(f) for f in expr.field_values]
            case HIR.Array():
                expr.elements = [self.__rewrite_one_capture(e) for e in expr.elements]
            case HIR.ArrayRepeat():
                expr.element = self.__rewrite_one_capture(expr.element)
            case HIR.Cast():
                expr.value = self.__rewrite_one_capture(expr.value)
            case HIR.VariantConstruct() if expr.args is not None:
                expr.args = {k: self.__rewrite_one_capture(v) for k, v in expr.args.items()}
            case HIR.Panic():
                expr.message = self.__rewrite_one_capture(expr.message)
            case HIR.Delete():
                expr.target = self.__rewrite_one_capture(expr.target)
            case HIR.DynValue():
                expr.value = self.__rewrite_one_capture(expr.value)
            case HIR.DynBuffer():
                expr.length = self.__rewrite_one_capture(expr.length)
            case HIR.BitCast():
                expr.value = self.__rewrite_one_capture(expr.value)
            case HIR.AssumeInit():
                expr.value = self.__rewrite_one_capture(expr.value)
            case HIR.Closure():
                expr.captures = {k: self.__rewrite_one_capture(v) for k, v in expr.captures.items()}
            case _:
                pass
        return expr
