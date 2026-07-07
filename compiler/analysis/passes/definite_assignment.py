"""
Definite assignment analysis for the YIAN compiler.

This pass walks the typed HIR after type checking and determines whether
every variable use is reachable from a preceding assignment on all paths.

Per-field tracking
------------------
For structs and tuples the analysis tracks each field independently.
Assigning ``s.field = v`` marks only *field* as VALID, not the whole
variable.  A whole-variable read succeeds when either the whole variable
is explicitly VALID or every field is VALID (recursively).

The state dictionary uses :class:`StateKey` keys::

    StateKey(sym_id)                  whole variable
    StateKey(sym_id, ("name",))       struct field
    StateKey(sym_id, (0,))            tuple element
    StateKey(sym_id, ("a", 0))        nested: field ``a``, then tuple element 0
"""
from __future__ import annotations

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum, auto
from typing import TYPE_CHECKING

from compiler.analysis.error import AnalysisError
from compiler.analysis.ty import ty as Type
from compiler.analysis.ty.context import TypeCtx
from compiler.analysis.unit import hir as HIR
from compiler.frontend.lex.position import SrcSpan
from compiler.frontend.parse.operator import BinaryOperator, UnaryOperator

if TYPE_CHECKING:
    from compiler.analysis.unit.def_point import DefPoint


class VarState(Enum):
    """Validity state of a single local variable at a given program point."""

    INVALID = auto()    # not assigned on any path
    VALID = auto()      # assigned on every path
    UNCERTAIN = auto()  # assigned on some paths but not all


# ------------------------------------------------------------------
# state key
# ------------------------------------------------------------------


@dataclass(frozen=True)
class StateKey:
    """Key into the per-variable / per-field validity state dictionary.

    *path* is ``()`` for the whole variable, otherwise a sequence of
    field names (:class:`str`) and tuple indices (:class:`int`).
    """

    sym_id: int
    path: tuple[int | str, ...] = ()


def __whole(sym_id: int) -> StateKey:
    return StateKey(sym_id)


# ------------------------------------------------------------------


@dataclass
class FuncAnalysis:
    """Per-function result of definite assignment analysis."""

    uncertain_vars: set[int] = field(default_factory=set[int])
    """Symbol ids whose whole variable or any field is UNCERTAIN."""

    exit_state: dict[StateKey, VarState] = field(default_factory=dict[StateKey, VarState])
    """Per-variable / per-field state at the function's block-end exit."""


class DefiniteAssignment:
    """Definite assignment analysis pass.

    Usage::

        da = DefiniteAssignment(def_points, type_ctx)
        da.run()
        errors = da.export_errors()
        for type_id, dp in def_points.items():
            dp.validity = da.export_analysis(type_id)
    """

    def __init__(self, def_points: dict[int, DefPoint],
                 type_ctx: TypeCtx) -> None:
        self.__def_points = def_points
        self.__type_ctx = type_ctx
        self.__errors: list[AnalysisError] = []
        self.__analyses: dict[int, FuncAnalysis] = {}

        # Per-definition transient state (reset for each DefPoint)
        self.__symbol_ctx = None
        self.__uncertain_vars: set[int] = set()
        self.__exit_state: dict[StateKey, VarState] | None = None

    # ------------------------------------------------------------------
    # public API
    # ------------------------------------------------------------------

    def run(self) -> None:
        """Run the analysis over all DefPoints."""
        for dp in self.__def_points.values():
            if dp.body is None:
                continue
            self.__analyze_def_point(dp)

    def export_errors(self) -> list[AnalysisError]:
        """Return every error collected during the run."""
        return self.__errors

    def export_analysis(self, type_id: int) -> FuncAnalysis | None:
        """Return the :class:`FuncAnalysis` for the given *type_id*."""
        return self.__analyses.get(type_id)

    # ------------------------------------------------------------------
    # per-definition entry point
    # ------------------------------------------------------------------

    def __analyze_def_point(self, dp: DefPoint) -> None:
        assert dp.body is not None

        self.__symbol_ctx = dp.symbol_ctx
        self.__uncertain_vars = set()
        self.__exit_state = None

        # Initial state: params are VALID (whole), other locals INVALID.
        state: dict[StateKey, VarState] = {}
        for loc in dp.locals:
            state[__whole(loc)] = (VarState.VALID if loc in dp.params else VarState.INVALID)

        final_state = self.__check_expr(dp.body, state)

        # Merge block-end state with recorded divergent-exit states
        if self.__exit_state is not None:
            final_state = self.__merge_states(final_state, self.__exit_state)

        self.__analyses[dp.type_id] = FuncAnalysis(
            uncertain_vars=self.__uncertain_vars.copy(),
            exit_state=final_state,
        )

    # ==================================================================
    # core walker
    # ==================================================================

    def __check_expr(self, expr: HIR.Expr, state: dict[StateKey, VarState]) -> dict[StateKey, VarState]:
        """Walk *expr* and return the variable state after it."""

        # -- control flow -------------------------------------------------
        if isinstance(expr, HIR.Block):
            for stmt in expr.stmts:
                state = self.__check_expr(stmt, state)
            return state

        if isinstance(expr, HIR.Semi):
            return self.__check_expr(expr.expr, state)

        if isinstance(expr, HIR.If):
            return self.__check_if(expr, state)

        if isinstance(expr, HIR.Loop):
            return self.__check_loop(expr, state)

        if isinstance(expr, HIR.Match):
            return self.__check_match(expr, state)

        # -- divergent terminators ----------------------------------------
        if isinstance(expr, HIR.Return):
            if expr.value is not None:
                state = self.__check_expr(expr.value, state)
            self.__record_exit_state(state)
            return state

        if isinstance(expr, HIR.Break):
            if expr.value is not None:
                state = self.__check_expr(expr.value, state)
            self.__record_exit_state(state)
            return state

        if isinstance(expr, HIR.Continue):
            self.__record_exit_state(state)
            return state

        if isinstance(expr, HIR.Panic):
            state = self.__check_expr(expr.message, state)
            self.__record_exit_state(state)
            return state

        # -- declarations -------------------------------------------------
        if isinstance(expr, HIR.Let):
            return self.__check_let(expr, state)

        # -- operators ----------------------------------------------------
        if isinstance(expr, HIR.Binary):
            return self.__check_binary(expr, state)

        if isinstance(expr, HIR.Unary):
            return self.__check_expr(expr.operand, state)

        # -- variable use -------------------------------------------------
        if isinstance(expr, HIR.Var):
            self.__check_var_use(expr.symbol_id, state, expr.span)
            return state

        # -- calls --------------------------------------------------------
        if isinstance(expr, HIR.Call):
            for arg in expr.args:
                state = self.__check_expr(arg, state)
            return state

        if isinstance(expr, HIR.MethodCall):
            state = self.__check_expr(expr.receiver, state)
            for arg in expr.args:
                state = self.__check_expr(arg, state)
            return state

        if isinstance(expr, HIR.Invoke):
            state = self.__check_expr(expr.callable, state)
            for arg in expr.args:
                state = self.__check_expr(arg, state)
            return state

        # -- construction -------------------------------------------------
        if isinstance(expr, HIR.StructConstruct):
            for v in expr.field_values.values():
                state = self.__check_expr(v, state)
            return state

        if isinstance(expr, HIR.VariantConstruct):
            if expr.args is not None:
                for v in expr.args.values():
                    state = self.__check_expr(v, state)
            return state

        if isinstance(expr, HIR.Tuple):
            for f in expr.field_values:
                state = self.__check_expr(f, state)
            return state

        if isinstance(expr, HIR.Array):
            for e in expr.elements:
                state = self.__check_expr(e, state)
            return state

        if isinstance(expr, HIR.ArrayRepeat):
            return self.__check_expr(expr.element, state)

        # -- access -------------------------------------------------------
        if isinstance(expr, HIR.FieldAccess):
            state = self.__check_expr(expr.receiver, state)
            self.__check_field_read(expr, state)
            return state

        if isinstance(expr, HIR.TupleAccess):
            state = self.__check_expr(expr.receiver, state)
            self.__check_tuple_read(expr, state)
            return state

        if isinstance(expr, HIR.DynValue):
            return self.__check_expr(expr.value, state)

        if isinstance(expr, HIR.DynBuffer):
            return self.__check_expr(expr.length, state)

        # -- cast / bitcast -----------------------------------------------
        if isinstance(expr, HIR.Cast):
            return self.__check_expr(expr.value, state)

        if isinstance(expr, HIR.BitCast):
            return self.__check_expr(expr.value, state)

        # -- sys calls ----------------------------------------------------
        if isinstance(expr, HIR.SysRead):
            state = self.__check_expr(expr.fd, state)
            state = self.__check_expr(expr.buf, state)
            return state

        if isinstance(expr, HIR.SysWrite):
            state = self.__check_expr(expr.fd, state)
            state = self.__check_expr(expr.buf, state)
            return state

        if isinstance(expr, HIR.Delete):
            return self.__check_expr(expr.target, state)

        if isinstance(expr, HIR.AssumeInit):
            # Mark the variable VALID *before* checking, so the Var use
            # inside assume_init itself does not trigger a DA error.
            if isinstance(expr.value, HIR.Var):
                sym_id = expr.value.symbol_id
                state = {**state, __whole(sym_id): VarState.VALID}
                state = {k: v for k, v in state.items() if k.sym_id != sym_id or not k.path}
            state = self.__check_expr(expr.value, state)
            return state

        # -- leaf nodes ---------------------------------------------------
        return state

    # ==================================================================
    # control-flow helpers
    # ==================================================================

    def __check_if(self, expr: HIR.If, state: dict[StateKey, VarState]) -> dict[StateKey, VarState]:
        state = self.__check_expr(expr.cond, state)
        then_state = self.__check_expr(expr.then_branch, dict(state))
        then_diverges = expr.then_branch.type_id == TypeCtx.never_id

        if expr.else_branch is not None:
            else_state = self.__check_expr(expr.else_branch, dict(state))
            else_diverges = expr.else_branch.type_id == TypeCtx.never_id
            if then_diverges and else_diverges:
                return then_state
            if then_diverges:
                return else_state
            if else_diverges:
                return then_state
            return self.__merge_states(then_state, else_state)

        if then_diverges:
            return then_state
        return self.__merge_states(then_state, dict(state))

    def __check_loop(self, expr: HIR.Loop, state: dict[StateKey, VarState]) -> dict[StateKey, VarState]:
        pre_state = dict(state)
        body_state = self.__check_expr(expr.body, dict(pre_state))
        merged: dict[StateKey, VarState] = {}
        all_keys = set(pre_state.keys()) | set(body_state.keys())
        for k in all_keys:
            pre_val = pre_state.get(k, VarState.INVALID)
            body_val = body_state.get(k, VarState.INVALID)
            if pre_val == VarState.VALID:
                merged[k] = VarState.VALID
            elif pre_val == body_val:
                merged[k] = pre_val
            else:
                merged[k] = VarState.UNCERTAIN
        return merged

    def __check_match(self, expr: HIR.Match, state: dict[StateKey, VarState]) -> dict[StateKey, VarState]:
        state = self.__check_expr(expr.value, state)

        arm_states: list[dict[StateKey, VarState]] = []
        for arm in expr.arms:
            arm_state = dict(state)
            if (arm.pattern is not None
                    and isinstance(arm.pattern, HIR.EnumPattern)
                    and arm.pattern.unpack_fields is not None):
                for sym_id in arm.pattern.unpack_fields:
                    arm_state[__whole(sym_id)] = VarState.VALID
            arm_state = self.__check_expr(arm.body, arm_state)
            if arm.body.type_id != TypeCtx.never_id:
                arm_states.append(arm_state)

        if not arm_states:
            return state

        merged = arm_states[0]
        for arm_state in arm_states[1:]:
            merged = self.__merge_states(merged, arm_state)
        return merged

    # ==================================================================
    # expression-specific handlers
    # ==================================================================

    def __check_let(self, expr: HIR.Let, state: dict[StateKey, VarState]) -> dict[StateKey, VarState]:
        sym_id = expr.symbol_id
        if expr.init is not None:
            state = self.__check_expr(expr.init, state)
            if sym_id is not None:
                state = {**state, __whole(sym_id): VarState.VALID}
        elif sym_id is not None:
            state = {**state, __whole(sym_id): VarState.INVALID}
        return state

    def __check_binary(self, expr: HIR.Binary, state: dict[StateKey, VarState]) -> dict[StateKey, VarState]:
        op = expr.op

        if op == BinaryOperator.Assign:
            state = self.__check_expr(expr.right, state)
            return self.__walk_assign_target(expr.left, state)

        if op.is_compound_assign():
            state = self.__check_expr(expr.left, state)
            state = self.__check_expr(expr.right, state)
            return state

        state = self.__check_expr(expr.left, state)
        state = self.__check_expr(expr.right, state)
        return state

    # ==================================================================
    # assignment target walking
    # ==================================================================

    def __walk_assign_target(self, target: HIR.Expr, state: dict[StateKey, VarState], path: tuple[int | str, ...] = ()) -> dict[StateKey, VarState]:
        """Walk *target* as the left-hand side of an assignment.

        *path* accumulates field/element names as we recurse through
        ``FieldAccess`` / ``TupleAccess``.  When we reach the root
        ``Var`` the accumulated path determines the state key.
        """
        if isinstance(target, HIR.Var):
            sym_id = target.symbol_id
            if not path:
                # Whole-variable assignment  s = …
                key = __whole(sym_id)
                state = {**state, key: VarState.VALID}
                # Remove stale per-field entries — whole VALID subsumes them.
                state = {k: v for k, v in state.items() if not (k.sym_id == sym_id and k.path)}
            else:
                # Field / element assignment  s.field = …  or  s.0 = …
                key = StateKey(sym_id, path)
                state = {**state, key: VarState.VALID}
            return state

        if isinstance(target, HIR.FieldAccess):
            return self.__walk_assign_target(
                target.receiver, state, (*path, target.field.name))

        if isinstance(target, HIR.TupleAccess):
            return self.__walk_assign_target(target.receiver, state, (*path, target.index))

        if isinstance(target, HIR.Unary) and target.op == UnaryOperator.Deref:
            if isinstance(target.operand, HIR.Var):
                return self.__check_expr(target.operand, state)
            return self.__walk_neutral(target.operand, state)

        return self.__walk_neutral(target, state)

    # ==================================================================
    # neutral walker (no Var checks, no Var marks)
    # ==================================================================

    def __walk_neutral(self, expr: HIR.Expr, state: dict[StateKey, VarState]) -> dict[StateKey, VarState]:
        """Walk *expr* tracking nested state changes but treating
        ``Var`` nodes as transparent."""

        # -- transparent ---------------------------------------------------
        if isinstance(expr, HIR.Var):
            return state

        # -- state-changing ------------------------------------------------
        if isinstance(expr, HIR.Let):
            return self.__check_let(expr, state)

        if isinstance(expr, HIR.Binary):
            if expr.op == BinaryOperator.Assign:
                state = self.__check_expr(expr.right, state)
                return self.__walk_assign_target(expr.left, state)
            if expr.op.is_compound_assign():
                state = self.__walk_neutral(expr.left, state)
                state = self.__walk_neutral(expr.right, state)
                return state
            state = self.__walk_neutral(expr.left, state)
            state = self.__walk_neutral(expr.right, state)
            return state

        # -- control flow --------------------------------------------------
        if isinstance(expr, HIR.Block):
            for stmt in expr.stmts:
                state = self.__walk_neutral(stmt, state)
            return state

        if isinstance(expr, HIR.Semi):
            return self.__walk_neutral(expr.expr, state)

        if isinstance(expr, HIR.If):
            state = self.__walk_neutral(expr.cond, state)
            then_state = self.__walk_neutral(expr.then_branch, dict(state))
            then_diverges = expr.then_branch.type_id == TypeCtx.never_id
            if expr.else_branch is not None:
                else_state = self.__walk_neutral(expr.else_branch, dict(state))
                else_diverges = expr.else_branch.type_id == TypeCtx.never_id
                if then_diverges and else_diverges:
                    return state
                if then_diverges:
                    return else_state
                if else_diverges:
                    return then_state
                return self.__merge_states(then_state, else_state)
            if then_diverges:
                return state
            return self.__merge_states(then_state, dict(state))

        if isinstance(expr, HIR.Loop):
            return self.__check_loop(expr, state)

        if isinstance(expr, HIR.Match):
            return self.__check_match(expr, state)

        # -- divergent terminators -----------------------------------------
        if isinstance(expr, HIR.Return):
            if expr.value is not None:
                state = self.__walk_neutral(expr.value, state)
            self.__record_exit_state(state)
            return state

        if isinstance(expr, HIR.Break):
            if expr.value is not None:
                state = self.__walk_neutral(expr.value, state)
            self.__record_exit_state(state)
            return state

        if isinstance(expr, HIR.Continue):
            self.__record_exit_state(state)
            return state

        if isinstance(expr, HIR.Panic):
            state = self.__walk_neutral(expr.message, state)
            self.__record_exit_state(state)
            return state

        # -- calls ---------------------------------------------------------
        if isinstance(expr, HIR.Call):
            for arg in expr.args:
                state = self.__walk_neutral(arg, state)
            return state

        if isinstance(expr, HIR.MethodCall):
            state = self.__walk_neutral(expr.receiver, state)
            for arg in expr.args:
                state = self.__walk_neutral(arg, state)
            return state

        if isinstance(expr, HIR.Invoke):
            state = self.__walk_neutral(expr.callable, state)
            for arg in expr.args:
                state = self.__walk_neutral(arg, state)
            return state

        # -- unary ---------------------------------------------------------
        if isinstance(expr, HIR.Unary):
            return self.__walk_neutral(expr.operand, state)

        # -- construction / access -----------------------------------------
        if isinstance(expr, HIR.StructConstruct):
            for v in expr.field_values.values():
                state = self.__walk_neutral(v, state)
            return state

        if isinstance(expr, HIR.VariantConstruct):
            if expr.args is not None:
                for v in expr.args.values():
                    state = self.__walk_neutral(v, state)
            return state

        if isinstance(expr, HIR.Tuple):
            for f in expr.field_values:
                state = self.__walk_neutral(f, state)
            return state

        if isinstance(expr, HIR.Array):
            for e in expr.elements:
                state = self.__walk_neutral(e, state)
            return state

        if isinstance(expr, HIR.ArrayRepeat):
            return self.__walk_neutral(expr.element, state)

        if isinstance(expr, HIR.FieldAccess):
            return self.__walk_neutral(expr.receiver, state)

        if isinstance(expr, HIR.TupleAccess):
            return self.__walk_neutral(expr.receiver, state)

        if isinstance(expr, HIR.DynValue):
            return self.__walk_neutral(expr.value, state)

        if isinstance(expr, HIR.DynBuffer):
            return self.__walk_neutral(expr.length, state)

        if isinstance(expr, HIR.Cast):
            return self.__walk_neutral(expr.value, state)

        if isinstance(expr, HIR.BitCast):
            return self.__walk_neutral(expr.value, state)

        if isinstance(expr, HIR.SysRead):
            state = self.__walk_neutral(expr.fd, state)
            state = self.__walk_neutral(expr.buf, state)
            return state

        if isinstance(expr, HIR.SysWrite):
            state = self.__walk_neutral(expr.fd, state)
            state = self.__walk_neutral(expr.buf, state)
            return state

        if isinstance(expr, HIR.Delete):
            return self.__walk_neutral(expr.target, state)

        if isinstance(expr, HIR.AssumeInit):
            # Mark the variable VALID before walking, matching __check_expr.
            if isinstance(expr.value, HIR.Var):
                sym_id = expr.value.symbol_id
                state = {**state, __whole(sym_id): VarState.VALID}
                state = {k: v for k, v in state.items() if k.sym_id != sym_id or not k.path}
            state = self.__walk_neutral(expr.value, state)
            return state

        return state

    # ==================================================================
    # field-level read checks
    # ==================================================================

    def __check_field_read(self, expr: HIR.FieldAccess, state: dict[StateKey, VarState]) -> None:
        """Check that reading *expr* (a struct field) is valid."""
        path: list[int | str] = [expr.field.name]
        receiver = expr.receiver
        while isinstance(receiver, (HIR.FieldAccess, HIR.TupleAccess)):
            if isinstance(receiver, HIR.FieldAccess):
                path.append(receiver.field.name)
            else:
                path.append(receiver.index)
            receiver = receiver.receiver

        if isinstance(receiver, HIR.Var):
            sym_id = receiver.symbol_id
            path.reverse()
            key = StateKey(sym_id, tuple(path))
            self.__check_key_valid(key, sym_id, state, expr.span, receiver.type_id)

    def __check_tuple_read(self, expr: HIR.TupleAccess, state: dict[StateKey, VarState]) -> None:
        """Check that reading *expr* (a tuple element) is valid."""
        path: list[int | str] = [expr.index]
        receiver = expr.receiver
        while isinstance(receiver, (HIR.FieldAccess, HIR.TupleAccess)):
            if isinstance(receiver, HIR.FieldAccess):
                path.append(receiver.field.name)
            else:
                path.append(receiver.index)
            receiver = receiver.receiver

        if isinstance(receiver, HIR.Var):
            sym_id = receiver.symbol_id
            path.reverse()
            key = StateKey(sym_id, tuple(path))
            self.__check_key_valid(key, sym_id, state, expr.span,
                                   receiver.type_id)

    def __check_key_valid(self, key: StateKey, sym_id: int, state: dict[StateKey, VarState], span: SrcSpan, type_id: int) -> None:
        """Report an error unless *key* (or its whole-variable ancestor)
        is definitely VALID."""
        # 1. Whole variable VALID → all fields implicitly VALID.
        whole = __whole(sym_id)
        if state.get(whole) is VarState.VALID:
            return

        # 2. Exact field path VALID.
        cur = state.get(key, VarState.INVALID)
        if cur == VarState.VALID:
            return

        # 3. Recursive inference: are all sub-fields VALID?
        if self.__all_fields_valid(sym_id, type_id, key.path, state):
            return

        name = self.__var_name(sym_id)
        if cur == VarState.INVALID:
            self.__errors.append(AnalysisError(
                f"variable '{name}' (field) is used before it is "
                f"definitely assigned", span,
            ))
        else:
            self.__uncertain_vars.add(sym_id)
            self.__errors.append(AnalysisError(
                f"variable '{name}' (field) may not be assigned on all "
                f"code paths before this use", span,
            ))

    # ==================================================================
    # recursive all-fields-valid inference
    # ==================================================================

    def __all_fields_valid(self, sym_id: int, type_id: int, prefix: tuple[int | str, ...], state: dict[StateKey, VarState]) -> bool:
        """Return True when every field / element of the type at *type_id*
        is provably VALID under the given *prefix*."""
        ty = self.__type_ctx[type_id]
        if isinstance(ty, Type.StructType):
            fields = self.__type_ctx.get_struct_fields(type_id)
            return all(
                self.__is_field_or_whole_valid(sym_id, (*prefix, f.name), f.type_id, state)
                for f in fields
            )
        if isinstance(ty, Type.TupleType):
            return all(
                self.__is_field_or_whole_valid(sym_id, (*prefix, i), elem_type, state)
                for i, elem_type in enumerate(ty.element_types)
            )
        return False

    def __is_field_or_whole_valid(self, sym_id: int, key_suffix: tuple[int | str, ...], type_id: int, state: dict[StateKey, VarState]) -> bool:
        key = StateKey(sym_id, key_suffix)
        if state.get(key) is VarState.VALID:
            return True
        if state.get(__whole(sym_id)) is VarState.VALID:
            return True
        # Recurse into nested struct / tuple
        return self.__all_fields_valid(sym_id, type_id, key_suffix, state)

    # ==================================================================
    # state helpers
    # ==================================================================

    def __check_var_use(self, sym_id: int, state: dict[StateKey, VarState], span: SrcSpan) -> None:
        """Report an error if the whole variable *sym_id* is not
        definitely VALID at a use site."""
        whole = __whole(sym_id)
        cur = state.get(whole, VarState.INVALID)
        name = self.__var_name(sym_id)

        if cur == VarState.VALID:
            return

        # Try recursive inference
        assert self.__symbol_ctx is not None
        sym = self.__symbol_ctx.get(sym_id)
        if self.__all_fields_valid(sym_id, sym.type_id, (), state):
            return

        if cur == VarState.INVALID:
            self.__errors.append(AnalysisError(
                f"variable '{name}' is used before it is definitely "
                f"assigned", span,
            ))
        else:
            self.__uncertain_vars.add(sym_id)
            self.__errors.append(AnalysisError(
                f"variable '{name}' may not be assigned on all code "
                f"paths before this use", span,
            ))

    def __var_name(self, sym_id: int) -> str:
        """Best-effort variable name for error messages."""
        if self.__symbol_ctx is not None:
            sym = self.__symbol_ctx.get(sym_id)
            return sym.name
        return f"<{sym_id}>"

    def __merge_states(self, s1: dict[StateKey, VarState], s2: dict[StateKey, VarState]) -> dict[StateKey, VarState]:
        """Merge two states at a control-flow join point."""
        all_keys = set(s1.keys()) | set(s2.keys())
        merged: dict[StateKey, VarState] = {}
        for k in all_keys:
            v1 = s1.get(k, VarState.INVALID)
            v2 = s2.get(k, VarState.INVALID)
            if v1 == VarState.VALID and v2 == VarState.VALID:
                merged[k] = VarState.VALID
            elif v1 == VarState.INVALID and v2 == VarState.INVALID:
                merged[k] = VarState.INVALID
            else:
                merged[k] = VarState.UNCERTAIN
        return merged

    def __record_exit_state(self, state: dict[StateKey, VarState]) -> None:
        if self.__exit_state is None:
            self.__exit_state = dict(state)
        else:
            self.__exit_state = self.__merge_states(self.__exit_state, state)
