"""Post-TypeCheck pass: lower closure DefPoints to method DefPoints.

Rewrites capture variable references (Var → FieldAccess(self, field))
and converts the DefPoint from Closure to Method.
"""

from __future__ import annotations

from compiler.analysis.symbol.context import SymbolCtx
from compiler.analysis.symbol.symbol import SymbolKind
from compiler.analysis.ty import ty as Type
from compiler.analysis.ty.context import TypeCtx
from compiler.analysis.ty.ty import AccessMode, StructField
from compiler.analysis.unit import hir as HIR
from compiler.analysis.unit.def_point import DefPoint


class ClosureLowering:
    """Post-TypeCheck pass that lowers closure DefPoints to method DefPoints.

    For each DefPoint whose type is ClosureType:
    1. Rewrites capture-variable references (Var → FieldAccess(self, field))
    2. Injects ``self`` as the first parameter
    3. Changes the DefPoint type_id from closure_type_id to call_method_type_id
    """

    def __init__(self, def_points: dict[int, DefPoint], type_ctx: TypeCtx):
        self.__def_points = def_points
        self.__type_ctx = type_ctx
        # Per-closure state for the HIR rewrite
        self.__sid_to_field: dict[int, tuple[str, int]] = {}
        self.__self_sid: int = 0
        self.__struct_type_id: int = 0

    def run(self) -> None:
        for dp in list(self.__def_points.values()):
            if dp.body is None:
                continue

            ty = self.__type_ctx[dp.type_id]
            if not isinstance(ty, Type.ClosureType):
                continue

            self.__lower_one(dp, ty)

    # ------------------------------------------------------------------
    # per-closure lowering
    # ------------------------------------------------------------------

    def __lower_one(self, dp: DefPoint, closure_ty: Type.ClosureType) -> None:
        struct_type_id = closure_ty.struct_type_id
        assert struct_type_id != -1

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
        self_sid = call_sym_ctx.add_symbol("self", SymbolKind.Variable, struct_type_id)
        assert self_sid is not None

        # --- collect param sids ---
        param_sids: list[int] = []
        for param in closure_ty.parameters:
            sym = call_sym_ctx.lookup(param.name)
            if sym is not None:
                param_sids.append(sym.symbol_id)

        # --- rewrite body ---
        self.__setup_rewrite(capture_sids, closure_ty.captured_vars, struct_type_id, self_sid)
        assert dp.body is not None
        rewritten_body = self.__rewrite_block(dp.body)

        # --- convert DefPoint from Closure to Method ---
        dp.body = rewritten_body
        dp.symbol_ctx = call_sym_ctx
        dp.params = [self_sid] + param_sids
        dp.locals = dp.params.copy()
        dp.type_id = closure_ty.call_method_type_id

    # ------------------------------------------------------------------
    # HIR rewriting (Var(capture_sid) → FieldAccess(self, field))
    # ------------------------------------------------------------------

    def __setup_rewrite(self, capture_sids: dict[str, int], captured_vars: list[Type.CapturedVar], struct_type_id: int, self_sid: int) -> None:
        self.__sid_to_field.clear()
        for cv in captured_vars:
            sid = capture_sids.get(cv.name)
            if sid is not None:
                field = self.__type_ctx.get_struct_field_by_name(struct_type_id, cv.name)
                if field is not None:
                    self.__sid_to_field[sid] = (cv.name, field.type_id)
        self.__self_sid = self_sid
        self.__struct_type_id = struct_type_id

    def __rewrite_block(self, block: HIR.Block) -> HIR.Block:
        new_stmts = [self.__rewrite(s) for s in block.stmts]
        return HIR.Block(span=block.span, stmts=new_stmts,
                         type_id=block.type_id, is_place=block.is_place)

    def __rewrite(self, expr: HIR.Expr) -> HIR.Expr:
        if isinstance(expr, HIR.Var) and expr.symbol_id in self.__sid_to_field:
            field_name, field_type_id = self.__sid_to_field[expr.symbol_id]
            field = StructField(name=field_name, type_id=field_type_id,
                                access_mode=AccessMode.Private, index=0)
            return HIR.FieldAccess(
                span=expr.span,
                receiver=HIR.Var(span=expr.span, symbol_id=self.__self_sid,
                                 type_id=self.__struct_type_id, is_place=True),
                field=field,
                type_id=field_type_id,
                is_place=expr.is_place,
            )
        match expr:
            case HIR.Block():
                expr.stmts = [self.__rewrite(s) for s in expr.stmts]
            case HIR.Binary():
                expr.left = self.__rewrite(expr.left)
                expr.right = self.__rewrite(expr.right)
            case HIR.Unary():
                expr.operand = self.__rewrite(expr.operand)
            case HIR.Call():
                expr.args = [self.__rewrite(a) for a in expr.args]
            case HIR.Invoke():
                expr.callable = self.__rewrite(expr.callable)
                expr.args = [self.__rewrite(a) for a in expr.args]
            case HIR.If():
                expr.cond = self.__rewrite(expr.cond)
                expr.then_branch = self.__rewrite_block(expr.then_branch)
                if expr.else_branch is not None:
                    expr.else_branch = self.__rewrite_block(expr.else_branch)
            case HIR.Loop():
                expr.body = self.__rewrite_block(expr.body)
            case HIR.Match():
                expr.value = self.__rewrite(expr.value)
                for arm in expr.arms:
                    arm.body = self.__rewrite_block(arm.body)
            case HIR.Return() if expr.value is not None:
                expr.value = self.__rewrite(expr.value)
            case HIR.Semi():
                expr.expr = self.__rewrite(expr.expr)
            case HIR.Let() if expr.init is not None:
                expr.init = self.__rewrite(expr.init)
            case HIR.StructConstruct():
                expr.field_values = {k: self.__rewrite(v) for k, v in expr.field_values.items()}
            case HIR.MethodCall():
                expr.receiver = self.__rewrite(expr.receiver)
                expr.args = [self.__rewrite(a) for a in expr.args]
            case HIR.FieldAccess():
                expr.receiver = self.__rewrite(expr.receiver)
            case HIR.Tuple():
                expr.field_values = [self.__rewrite(f) for f in expr.field_values]
            case HIR.Array():
                expr.elements = [self.__rewrite(e) for e in expr.elements]
            case HIR.ArrayRepeat():
                expr.element = self.__rewrite(expr.element)
            case HIR.Cast():
                expr.value = self.__rewrite(expr.value)
            case HIR.VariantConstruct() if expr.args is not None:
                expr.args = {k: self.__rewrite(v) for k, v in expr.args.items()}
            case HIR.Panic():
                expr.message = self.__rewrite(expr.message)
            case HIR.Delete():
                expr.target = self.__rewrite(expr.target)
            case HIR.DynValue():
                expr.value = self.__rewrite(expr.value)
            case HIR.DynBuffer():
                expr.length = self.__rewrite(expr.length)
            case HIR.BitCast():
                expr.value = self.__rewrite(expr.value)
            case HIR.AssumeInit():
                expr.value = self.__rewrite(expr.value)
            case HIR.Closure():
                expr.captures = {k: self.__rewrite(v) for k, v in expr.captures.items()}
            case _:
                pass
        return expr
