"""Worklist-driven type checking — lowers AST function/method bodies to HIR.

With ``recover=True`` the pass also collects errors instead of stopping at the
first one: the granularity is the **top-level definition** (layer
three).  A definition whose body fails is recorded as a diagnostic and skipped;
the rest of the program is still checked, which is what turns "this file has one
error" into "this file has five errors" in the Problems panel.  Recovery is for
the analysis session (``--analyze`` and the language server); a build keeps the
strict first-error behaviour, because code generation cannot proceed past a
definition it could not type.
"""

from __future__ import annotations

from compiler.analysis.diagnostics import Diagnostic, Stage, diagnostic_from_error
from compiler.analysis.error import AnalysisError
from compiler.analysis.lowering.expr_checker import ExprChecker
from compiler.analysis.lowering.sem_ctx import DefKind, SemCtx
from compiler.analysis.package_map import PackageMap
from compiler.analysis.symbol.symbol import SymbolKind
from compiler.analysis.ty import ty as Type
from compiler.analysis.ty.context import TypeCtx
from compiler.analysis.unit import hir as HIR
from compiler.analysis.unit.def_point import DefPoint
from compiler.analysis.unit.unit_data import UnitData
from compiler.error import CompilerError
from compiler.frontend.parse import ast as AST
from compiler.utils.log import CompilerLog


def ch_tc():
    return CompilerLog.get("type_check")


#: The errors a definition body may raise and still be recoverable from.
#: Anything else (an assertion, a key error) is a compiler bug and must not be
#: swallowed by recovery.
RECOVERABLE_ERRORS = (AnalysisError, CompilerError)


class TypeCheck:
    def __init__(self, units: dict[int, UnitData], type_ctx: TypeCtx, packages: PackageMap | None = None,
                 require_entry: bool = True, entry_optional: bool = False, recover: bool = False):
        self.__units = units
        self.__type_ctx = type_ctx
        self.__packages = packages
        # A library root has no program entry; `-t none` still checks its
        # definitions, but a codegen run without an entry is a user error.
        self.__require_entry = require_entry
        # ``entry_optional`` is for callers that analyze *text* rather than build
        # a program (the analysis session behind `--analyze` and the language
        # server): a file set with no `main` at all, or whose `main` is being
        # edited, is not an error there.  The command line keeps its stricter
        # behaviour because a build needs exactly one program entry.
        self.__entry_optional = entry_optional
        self.__recover = recover
        self.__errors: list[Diagnostic] = []
        # One condition at one position is one diagnostic: a generic body that
        # fails is instantiated once per use, and the editor must not show the
        # same squiggle five times.
        self.__reported: set[tuple[str, str, int, int]] = set()

        self.__worklist: list[DefPoint] = []
        self.__def_points: dict[int, DefPoint] = {}  # type_id -> DefPoint
        # Two sets (G23): everything that gets type-checked, and the subset that
        # is reachable from the program entry and therefore code-generated.
        self.__generating = True
        self.__generated: set[int] = set()
        self.__entry_type_id: int | None = None

        self.__current_type_id: int = -1
        self.__current_locals: list[int] = []
        self.__current_params: list[int] = []

        self.__sem_ctx = SemCtx(type_ctx)
        self.__expr_helper = ExprChecker(self.__sem_ctx)

        # wire sem_ctx reachable-def reporter to our enqueue function
        self.__sem_ctx.set_def_reporter(self.__report_def_point)

    def __report_def_point(self, type_id: int) -> None:
        """Idempotently register a reachable `type_id` as a DefPoint and enqueue it.

        This mirrors the old worklist behaviour: whenever lowering discovers a
        concrete instantiated function/method type id, we record and schedule it
        for later checking.
        """
        if type_id in self.__def_points:
            return
        body, unit_id = self.__type_ctx.get_procedure(type_id)

        unit = self.__units[unit_id]
        dp = DefPoint(type_id=type_id, unit_id=unit_id, ast_body=body, symbol_ctx=unit.symbol_ctx)
        self.__def_points[type_id] = dp
        self.__worklist.append(dp)
        if self.__generating:
            self.__generated.add(type_id)

    def run(self) -> None:
        self.__generating = True
        self.__find_main()
        self.__drain()

        self.__check_root_definitions()
        self.__drain()

    def __drain(self) -> None:
        processed_def: set[int] = set()
        while self.__worklist:
            def_point = self.__worklist.pop()
            if def_point.type_id in processed_def:
                continue
            processed_def.add(def_point.type_id)
            ch_tc().trace(lambda: f"checking {self.__type_ctx.get_name(def_point.type_id)}")
            try:
                self.__type_check_def(def_point)
            except RECOVERABLE_ERRORS as error:
                if not self.__recover:
                    raise
                self.__record(error)

    def __record(self, error: Exception) -> None:
        """Record one recovered error, ignoring a repeat of the same condition."""
        diagnostic = diagnostic_from_error(error, stage=Stage.TYPE_CHECK)
        span = diagnostic.span
        key = (diagnostic.code, str(span.path), span.start.row, span.start.col)
        if key in self.__reported:
            return
        self.__reported.add(key)
        self.__errors.append(diagnostic)

    def __check_root_definitions(self) -> None:
        """Seed every top-level definition of the root package (G18/G21).

        Dependencies (including the standard library) stay on demand: each
        package is checked by its own ``anx check``.  Definitions seeded here are
        checked but never marked as generated, so they never reach CFG/LLVM
        lowering.  Uninstantiated generics are skipped — the language has no
        parameter constraints, so checking their bodies would produce false
        positives (for example ``a + b``).
        """
        self.__generating = False
        seeded = 0
        skipped_generic = 0
        for type_id, _body, unit_id in self.__type_ctx.iter_procedures():
            if type_id in self.__def_points:
                continue
            if not self.__is_root_unit(unit_id):
                continue
            if self.__type_ctx.contains_generic(type_id):
                skipped_generic += 1
                continue
            self.__report_def_point(type_id)
            seeded += 1
        ch_tc().debug(f"check-roots: seeded {seeded} extra definition(s), skipped {skipped_generic} generic")

    def __is_root_unit(self, unit_id: int) -> bool:
        """True when *unit_id* belongs to the code being checked."""
        unit = self.__units[unit_id]
        if self.__packages is not None and self.__packages.root is not None:
            owner = self.__packages.package_of(unit.path)
            # A file that belongs to no package is not part of a dependency: it
            # is an extra file on the command line, or an editor buffer outside
            # every source root, and the caller asked for it to be checked.
            return owner == self.__packages.root or owner is None
        return not unit.is_stdlib

    def export(self) -> dict[int, DefPoint]:
        return self.__def_points

    def export_diagnostics(self) -> tuple[Diagnostic, ...]:
        """Errors collected while recovering (empty unless ``recover=True``).

        Ordered by position so a client that renders them in order sees them top
        to bottom, the way they appear in the file.
        """
        return tuple(
            sorted(
                self.__errors,
                key=lambda diagnostic: (
                    str(diagnostic.span.path),
                    diagnostic.span.start.row,
                    diagnostic.span.start.col,
                ),
            )
        )

    def export_generated(self) -> dict[int, DefPoint]:
        """Return only the definitions reachable from the program entry.

        These are the ones handed to CFG/LLVM lowering; the extra check-only
        definitions stay out of the generated artifact.
        """
        return {type_id: dp for type_id, dp in self.__def_points.items() if type_id in self.__generated}

    @property
    def entry_type_id(self) -> int | None:
        """The type id of the program entry, or ``None`` for an entry-less root."""
        return self.__entry_type_id

    def __find_main(self) -> None:
        """Locate the program entry and register it as the first definition.

        With a ``--packages`` root package the entry is exactly
        ``packages[root].entry``: a dependency may define its own
        ``main`` without colliding with the program (G22). Without one, the
        entry is the single ``main`` of the non-stdlib units.
        """
        if self.__packages is not None and self.__packages.root is not None:
            self.__find_package_main()
            return

        candidates: list[tuple[UnitData, AST.FuncDef]] = []
        for unit in self.__units.values():
            if unit.is_stdlib:
                continue
            item = self.__main_def(unit)
            if item is not None:
                candidates.append((unit, item))
        for _unit, item in candidates:
            if item.generics:
                raise AnalysisError("The 'main' function cannot have generics", item.span)
        if not candidates:
            if self.__entry_optional:
                return
            raise CompilerError("No 'main' function found")
        if len(candidates) > 1:
            if self.__entry_optional:
                return
            raise AnalysisError("Multiple 'main' functions found", candidates[1][1].span)
        self.__register_main(candidates[0][0], candidates[0][1])

    def __find_package_main(self) -> None:
        assert self.__packages is not None
        root = self.__packages.root
        assert root is not None
        spec = self.__packages.packages.get(root)
        if spec is None:
            raise CompilerError(f"the --packages file has no entry for root package '{root}'")
        if spec.entry is None:
            if self.__require_entry:
                raise CompilerError(f"Package '{root}' has no program entry (kind '{spec.kind}')")
            return

        entry = spec.entry.resolve()
        unit = next((u for u in self.__units.values() if u.path.resolve() == entry), None)
        if unit is None:
            raise CompilerError(f"Program entry {entry} was not passed to the compiler")
        item = self.__main_def(unit)
        if item is None:
            # The entry file is present but has no `main`: with an editor that is
            # a file being written, not a program that must build.
            if self.__entry_optional:
                return
            raise CompilerError(f"No 'main' function found in the program entry {entry}")
        self.__register_main(unit, item)

    @staticmethod
    def __main_def(unit: UnitData) -> AST.FuncDef | None:
        for item in unit.items():
            if isinstance(item, AST.FuncDef) and item.name.name == "main":
                return item
        return None

    def __register_main(self, unit: UnitData, item: AST.FuncDef) -> None:
        if item.generics:
            raise AnalysisError("The 'main' function cannot have generics", item.span)
        symbol = unit.symbol_ctx.lookup("main")
        assert symbol is not None

        main_ty = self.__type_ctx[symbol.type_id]
        assert isinstance(main_ty, Type.FunctionType)
        ret_ty = main_ty.return_type(self.__type_ctx)
        if ret_ty != self.__type_ctx.void_id:
            raise AnalysisError(
                f"main must return void, not {self.__type_ctx.get_name(ret_ty)}; "
                "use std.core.env.exit(code) for non-zero exit",
                item.span,
            )

        main_def_point = DefPoint(type_id=symbol.type_id, unit_id=unit.unit_id, ast_body=item.body, symbol_ctx=unit.symbol_ctx)
        self.__worklist.append(main_def_point)
        self.__def_points[symbol.type_id] = main_def_point
        self.__generated.add(symbol.type_id)
        self.__entry_type_id = symbol.type_id

    def __type_check_def(self, def_point: DefPoint) -> None:
        self.__current_type_id = def_point.type_id
        self.__current_locals = []

        ty = self.__type_ctx[self.__current_type_id]
        if isinstance(ty, Type.ClosureType):
            def_point.body = self.__check_closure(def_point)
        elif isinstance(ty, Type.FunctionType):
            def_point.body = self.__check_function(def_point)
        elif isinstance(ty, Type.MethodType):
            def_point.body = self.__check_method(def_point)
        else:
            raise CompilerError(f"Type with id {def_point.type_id} is not a function, method, or closure type")

        def_point.locals = self.__current_locals
        def_point.params = self.__current_params
        assert self.__sem_ctx.symbol_ctx is not None
        def_point.symbol_ctx = self.__sem_ctx.symbol_ctx

    def __check_function(self, def_point: DefPoint) -> HIR.Block:
        func_ty = self.__type_ctx[self.__current_type_id]
        assert isinstance(func_ty, Type.FunctionType)

        self.__sem_ctx.begin_def(
            unit_id=def_point.unit_id,
            def_type_id=def_point.type_id,
            def_kind=DefKind.Function,
            ast_body=def_point.ast_body,
            return_type_id=func_ty.return_type(self.__type_ctx),
            receiver_type_id=None,
            is_static=False,
            symbol_ctx=def_point.symbol_ctx.clone(),
        )
        self.__current_locals = self.__sem_ctx.locals

        assert self.__sem_ctx.symbol_ctx is not None

        for generic_id, generic_arg_id in zip(func_ty.custom_def.generics, func_ty.generic_args):
            generic_ty = self.__type_ctx[generic_id]
            if isinstance(generic_ty, Type.GenericType):
                self.__sem_ctx.symbol_ctx.add_symbol(generic_ty.name, SymbolKind.Type, generic_arg_id)
            elif isinstance(generic_ty, Type.ConstGenericType):
                self.__sem_ctx.symbol_ctx.add_symbol(generic_ty.name, SymbolKind.ConstGeneric, generic_arg_id)

        for param in func_ty.parameters(self.__type_ctx):
            symbol_id = self.__sem_ctx.symbol_ctx.add_symbol(param.name, SymbolKind.Variable, param.type_id, span=param.span)
            assert symbol_id is not None
            self.__sem_ctx.push_local(symbol_id)

        self.__current_params = list(self.__sem_ctx.locals)

        body = self.__expr_helper.check_block(def_point.ast_body)
        return_type_id = func_ty.return_type(self.__type_ctx)
        self.__coerce_expression_body_tail(body, return_type_id)
        return body

    def __check_method(self, def_point: DefPoint) -> HIR.Block:
        method_ty = self.__type_ctx[self.__current_type_id]
        assert isinstance(method_ty, Type.MethodType)

        self.__sem_ctx.begin_def(
            unit_id=def_point.unit_id,
            def_type_id=def_point.type_id,
            def_kind=DefKind.Method,
            ast_body=def_point.ast_body,
            return_type_id=method_ty.return_type(self.__type_ctx),
            receiver_type_id=method_ty.receiver_type(self.__type_ctx),
            is_static=method_ty.custom_def.is_static,
            symbol_ctx=def_point.symbol_ctx.clone(),
        )
        self.__current_locals = self.__sem_ctx.locals

        assert self.__sem_ctx.symbol_ctx is not None

        for generic_id, generic_arg_id in zip(method_ty.custom_def.generics, method_ty.generic_args):
            generic_ty = self.__type_ctx[generic_id]
            if isinstance(generic_ty, Type.GenericType):
                self.__sem_ctx.symbol_ctx.add_symbol(generic_ty.name, SymbolKind.Type, generic_arg_id)
            elif isinstance(generic_ty, Type.ConstGenericType):
                self.__sem_ctx.symbol_ctx.add_symbol(generic_ty.name, SymbolKind.ConstGeneric, generic_arg_id)

        self_type_id = method_ty.receiver_type(self.__type_ctx)
        self.__sem_ctx.symbol_ctx.add_symbol("Self", SymbolKind.Type, self_type_id)

        if not method_ty.custom_def.is_static:
            ref_type_id = self.__type_ctx.alloc_ref(self_type_id)
            symbol_id = self.__sem_ctx.symbol_ctx.add_symbol("self", SymbolKind.Variable, ref_type_id)
            assert symbol_id is not None
            self.__sem_ctx.push_local(symbol_id)

        for param in method_ty.parameters(self.__type_ctx):
            symbol_id = self.__sem_ctx.symbol_ctx.add_symbol(param.name, SymbolKind.Variable, param.type_id, span=param.span)
            assert symbol_id is not None
            self.__sem_ctx.push_local(symbol_id)

        self.__current_params = list(self.__sem_ctx.locals)

        body = self.__expr_helper.check_block(def_point.ast_body)
        return_type_id = method_ty.return_type(self.__type_ctx)
        self.__coerce_expression_body_tail(body, return_type_id)
        return body

    def __check_closure(self, def_point: DefPoint) -> HIR.Block:
        """Type-check a closure body. Captures are injected as local variables.
        The lowering pass later rewrites them to FieldAccess(self, field)."""
        closure_ty = self.__type_ctx[def_point.type_id]
        assert isinstance(closure_ty, Type.ClosureType)

        self.__sem_ctx.begin_def(
            unit_id=def_point.unit_id,
            def_type_id=def_point.type_id,
            def_kind=DefKind.Closure,
            ast_body=def_point.ast_body,
            return_type_id=closure_ty.return_type,
            receiver_type_id=None,
            is_static=False,
            symbol_ctx=def_point.symbol_ctx.clone(),
        )
        self.__current_locals = self.__sem_ctx.locals

        assert self.__sem_ctx.symbol_ctx is not None

        # Inject capture variables as locals
        for cv in closure_ty.captured_vars:
            sid = self.__sem_ctx.symbol_ctx.add_symbol(cv.name, SymbolKind.Variable, cv.type_id)
            if sid is not None:
                self.__sem_ctx.push_local(sid)

        # Inject parameters as locals
        for param in closure_ty.parameters:
            sid = self.__sem_ctx.symbol_ctx.add_symbol(param.name, SymbolKind.Variable, param.type_id, span=param.span)
            if sid is not None:
                self.__sem_ctx.push_local(sid)

        self.__current_params = list(self.__sem_ctx.locals)

        body = self.__expr_helper.check_block(def_point.ast_body)
        return_type_id = closure_ty.return_type
        self.__coerce_expression_body_tail(body, return_type_id)
        return body

    def __coerce_expression_body_tail(self, body: HIR.Block, return_type_id: int) -> None:
        """Align an implicit expression return with the declared return type.

        Explicit ``return`` expressions already go through
        :meth:`ExprChecker.lower_return`.  A block's final expression is
        otherwise emitted directly as the function result, so it needs the
        same pointer/reference (and literal) coercion before CFG lowering.
        Diverging and statement-valued tails do not produce a return value.
        """
        if not body.stmts or return_type_id == TypeCtx.void_id:
            return
        last = body.stmts[-1]
        if last.type_id in (TypeCtx.void_id, TypeCtx.never_id):
            return
        if last.type_id != return_type_id:
            body.stmts[-1] = self.__expr_helper.coerce(last, return_type_id)
        body.type_id = return_type_id
