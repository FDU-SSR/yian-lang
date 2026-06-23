"""
Definite assignment analysis for the YIAN compiler.

This pass walks the typed HIR after type checking and determines whether
every variable use is reachable from a preceding assignment on all paths.

The analysis result is stored on each :class:`DefPoint` as ``validity`` and
is consumed by downstream passes (e.g. drop cleanup) as well as by error
reporting for uses of potentially-uninitialised variables.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum, auto
from typing import TYPE_CHECKING

from compiler.analysis.error import AnalysisError
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


@dataclass
class FuncAnalysis:
    """Per-function result of definite assignment analysis."""

    uncertain_vars: set[int] = field(default_factory=set[int])
    """Symbol ids of variables that become UNCERTAIN at any reachable point."""

    exit_state: dict[int, VarState] = field(default_factory=dict[int, VarState])
    """Per-variable state at the function's normal (block-end) exit."""


class DefiniteAssignment:
    """Definite assignment analysis pass.

    Walks the HIR body of every :class:`DefPoint` and checks that every
    non-assignment variable use is guarded by a preceding assignment on all
    reachable control-flow paths.

    Usage::

        da = DefiniteAssignment(def_points)
        da.run()
        errors = da.export_errors()
        # raise / print errors ...
        for type_id, dp in def_points.items():
            dp.validity = da.export_analysis(type_id)
    """

    def __init__(self, def_points: dict[int, DefPoint]) -> None:
        self.__def_points = def_points
        self.__errors: list[AnalysisError] = []
        self.__analyses: dict[int, FuncAnalysis] = {}

        # Per-definition transient state (reset for each DefPoint)
        self.__symbol_ctx = None
        self.__uncertain_vars: set[int] = set()
        self.__exit_state: dict[int, VarState] | None = None

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
        """Return the :class:`FuncAnalysis` for the given *type_id*, or ``None``."""
        return self.__analyses.get(type_id)

    # ------------------------------------------------------------------
    # per-definition entry point
    # ------------------------------------------------------------------

    def __analyze_def_point(self, dp: DefPoint) -> None:
        assert dp.body is not None

        self.__symbol_ctx = dp.symbol_ctx
        self.__uncertain_vars = set()
        self.__exit_state = None

        # Initial state: params are VALID, other locals are INVALID
        state: dict[int, VarState] = {}
        for loc in dp.locals:
            state[loc] = VarState.VALID if loc in dp.params else VarState.INVALID

        final_state = self.__check_expr(dp.body, state)

        # Merge block-end state with recorded divergent-exit states
        if self.__exit_state is not None:
            final_state = self.__merge_states(final_state, self.__exit_state)

        self.__analyses[dp.type_id] = FuncAnalysis(
            uncertain_vars=self.__uncertain_vars.copy(),
            exit_state=final_state,
        )

    # ==================================================================
    # core walker — returns the state *after* the expression
    # ==================================================================

    def __check_expr(self, expr: HIR.Expr, state: dict[int, VarState]) -> dict[int, VarState]:
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

        # -- access -------------------------------------------------------
        if isinstance(expr, HIR.FieldAccess):
            return self.__check_expr(expr.receiver, state)

        if isinstance(expr, HIR.TupleAccess):
            return self.__check_expr(expr.receiver, state)

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

        # -- leaf nodes (no children to walk) -----------------------------
        return state

    # ==================================================================
    # control-flow helpers
    # ==================================================================

    def __check_if(self, expr: HIR.If, state: dict[int, VarState]) -> dict[int, VarState]:
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

        # No else branch — implicit else preserves pre-if state.
        if then_diverges:
            return then_state
        return self.__merge_states(then_state, dict(state))

    def __check_loop(self, expr: HIR.Loop, state: dict[int, VarState]) -> dict[int, VarState]:
        """Walk the loop body once; variables changed inside become UNCERTAIN."""
        pre_state = dict(state)
        body_state = self.__check_expr(expr.body, dict(pre_state))
        merged: dict[int, VarState] = {}
        all_keys = set(pre_state.keys()) | set(body_state.keys())
        for k in all_keys:
            pre_val = pre_state.get(k, VarState.INVALID)
            body_val = body_state.get(k, VarState.INVALID)
            if pre_val == VarState.VALID:
                # Already valid before loop — stays valid.
                merged[k] = VarState.VALID
            elif pre_val == body_val:
                merged[k] = pre_val
            else:
                merged[k] = VarState.UNCERTAIN
        return merged

    def __check_match(self, expr: HIR.Match, state: dict[int, VarState]) -> dict[int, VarState]:
        state = self.__check_expr(expr.value, state)

        arm_states: list[dict[int, VarState]] = []
        for arm in expr.arms:
            arm_state = dict(state)
            # Enum pattern bindings are VALID inside the arm
            if (arm.pattern is not None
                    and isinstance(arm.pattern, HIR.EnumPattern)
                    and arm.pattern.unpack_fields is not None):
                for sym_id in arm.pattern.unpack_fields:
                    arm_state[sym_id] = VarState.VALID
            arm_state = self.__check_expr(arm.body, arm_state)
            # Arms that always diverge (panic / return / break) do not
            # contribute to the post-match state — their code is unreachable.
            if arm.body.type_id != TypeCtx.never_id:
                arm_states.append(arm_state)

        if not arm_states:
            # All arms diverge — no state flows past the match.
            return state

        # Merge across surviving arms only (not with pre-match state).
        merged = arm_states[0]
        for arm_state in arm_states[1:]:
            merged = self.__merge_states(merged, arm_state)
        return merged

    # ==================================================================
    # expression-specific handlers
    # ==================================================================

    def __check_let(self, expr: HIR.Let, state: dict[int, VarState]) -> dict[int, VarState]:
        sym_id = expr.symbol_id
        if expr.init is not None:
            # init is Binary(op=Assign, left=Var, right=…)
            state = self.__check_expr(expr.init, state)
            if sym_id is not None:
                state = {**state, sym_id: VarState.VALID}
        elif sym_id is not None:
            state = {**state, sym_id: VarState.INVALID}
        return state

    def __check_binary(self, expr: HIR.Binary, state: dict[int, VarState]) -> dict[int, VarState]:
        op = expr.op

        if op == BinaryOperator.Assign:
            state = self.__check_expr(expr.right, state)
            return self.__walk_assign_target(expr.left, state)

        if op.is_compound_assign():
            # Read-modify-write: left must already be VALID
            state = self.__check_expr(expr.left, state)
            state = self.__check_expr(expr.right, state)
            return state

        # All other binary ops
        state = self.__check_expr(expr.left, state)
        state = self.__check_expr(expr.right, state)
        return state

    def __walk_assign_target(self, target: HIR.Expr,
                             state: dict[int, VarState]) -> dict[int, VarState]:
        """Walk *target* as the left-hand side of an assignment.

        Only a direct variable or a field/tuple-element access marks the
        root variable as VALID.  Writing through an index expression
        (``arr[i] = v``) or a dereference (``*ptr = v``) does **not** make
        the container / pointer variable valid — element-wise writes do not
        constitute a whole-value assignment.
        """
        if isinstance(target, HIR.Var):
            # a = b — direct whole-variable assignment.
            return {**state, target.symbol_id: VarState.VALID}

        if isinstance(target, (HIR.FieldAccess, HIR.TupleAccess)):
            # a.field = b  or  a.0 = b — field/element write on a
            # struct/tuple IS a write to the composite.
            return self.__walk_assign_target(target.receiver, state)

        if isinstance(target, HIR.Unary) and target.op == UnaryOperator.Deref:
            # *ptr = 3 — ptr is a pointer variable whose stored address we
            # must read; it must be VALID.  The write does NOT make ptr valid.
            if isinstance(target.operand, HIR.Var):
                return self.__check_expr(target.operand, state)
            # arr[i] = 3 or other complex expression under the deref.
            # Walk sub-expressions for nested state changes but do NOT flag
            # Var nodes as uses and do NOT mark any Var as VALID.
            return self.__walk_neutral(target.operand, state)

        # For anything else (method calls, calls, invokes, …) walk
        # sub-expressions neutrally — no Var validity checks, no marks.
        return self.__walk_neutral(target, state)

    def __walk_neutral(self, expr: HIR.Expr,
                       state: dict[int, VarState]) -> dict[int, VarState]:
        """Walk *expr* tracking nested assignments / declarations but
        treating Var nodes as transparent — they are neither checked for
        validity nor marked VALID.

        This is used for sub-expressions inside an assignment target that
        are not themselves the ultimate target (e.g. the index operand of
        ``arr[i] = v``, or a function call that returns a pointer).
        """
        # -- transparent (neither check nor mark) --------------------------
        if isinstance(expr, HIR.Var):
            return state

        # -- state-changing (process fully) --------------------------------
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
            # Loop body may contain assignments — process them, but mark
            # changed vars UNCERTAIN just as in __check_loop.
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

        # -- calls (recurse into args) -------------------------------------
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

        # -- leaf nodes ----------------------------------------------------
        return state

    # ==================================================================
    # state helpers
    # ==================================================================

    def __check_var_use(self, sym_id: int, state: dict[int, VarState],
                        span: SrcSpan) -> None:
        """Report an error if *sym_id* is not definitely VALID at a use site."""
        cur = state.get(sym_id, VarState.INVALID)
        name = self.__var_name(sym_id)

        if cur == VarState.INVALID:
            self.__errors.append(AnalysisError(
                f"variable '{name}' is used before it is definitely assigned", span,
            ))
        elif cur == VarState.UNCERTAIN:
            self.__uncertain_vars.add(sym_id)
            self.__errors.append(AnalysisError(
                f"variable '{name}' may not be assigned on all code paths before this use",
                span,
            ))

    def __var_name(self, sym_id: int) -> str:
        """Best-effort variable name for error messages."""
        if self.__symbol_ctx is not None:
            sym = self.__symbol_ctx.get(sym_id)
            return sym.name
        return f"<{sym_id}>"

    def __merge_states(self, s1: dict[int, VarState],
                       s2: dict[int, VarState]) -> dict[int, VarState]:
        """Merge two states at a control-flow join point."""
        all_keys = set(s1.keys()) | set(s2.keys())
        merged: dict[int, VarState] = {}
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

    def __record_exit_state(self, state: dict[int, VarState]) -> None:
        """Merge *state* into the cumulative exit-state for the current function."""
        if self.__exit_state is None:
            self.__exit_state = dict(state)
        else:
            self.__exit_state = self.__merge_states(self.__exit_state, state)
