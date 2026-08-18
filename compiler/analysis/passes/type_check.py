"""Worklist-driven type checking — lowers AST function/method bodies to HIR."""

from __future__ import annotations

from compiler.analysis.error import AnalysisError
from compiler.analysis.lowering.expr_checker import ExprChecker
from compiler.analysis.lowering.sem_ctx import DefKind, SemCtx
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


class TypeCheck:
    def __init__(self, units: dict[int, UnitData], type_ctx: TypeCtx):
        self.__units = units
        self.__type_ctx = type_ctx

        self.__worklist: list[DefPoint] = []
        self.__def_points: dict[int, DefPoint] = {}  # type_id -> DefPoint

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

    def run(self) -> None:
        self.__find_main()

        processed_def: set[int] = set()
        while self.__worklist:
            def_point = self.__worklist.pop()
            if def_point.type_id in processed_def:
                continue
            processed_def.add(def_point.type_id)
            ch_tc().trace(lambda: f"checking {self.__type_ctx.get_name(def_point.type_id)}")
            self.__type_check_def(def_point)

    def export(self) -> dict[int, DefPoint]:
        return self.__def_points

    def __find_main(self) -> None:
        for unit in self.__units.values():
            for item in unit.items():
                if isinstance(item, AST.FuncDef) and item.name.name == "main":
                    if item.generics:
                        raise AnalysisError("The 'main' function cannot have generics", item.span)
                    if self.__worklist:
                        raise AnalysisError("Multiple 'main' functions found", item.span)
                    symbol = unit.symbol_ctx.lookup("main")
                    assert symbol is not None

                    main_ty = self.__type_ctx[symbol.type_id]
                    assert isinstance(main_ty, Type.FunctionType)
                    ret_ty = main_ty.return_type(self.__type_ctx)
                    if ret_ty != self.__type_ctx.void_id:
                        raise AnalysisError(
                            f"main must return void, not {self.__type_ctx.get_name(ret_ty)}",
                            item.span,
                        )

                    main_def_point = DefPoint(type_id=symbol.type_id, unit_id=unit.unit_id, ast_body=item.body, symbol_ctx=unit.symbol_ctx)
                    self.__worklist.append(main_def_point)
                    self.__def_points[symbol.type_id] = main_def_point
        if not self.__worklist:
            raise CompilerError("No 'main' function found")

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
            symbol_id = self.__sem_ctx.symbol_ctx.add_symbol(param.name, SymbolKind.Variable, param.type_id)
            assert symbol_id is not None
            self.__sem_ctx.push_local(symbol_id)

        self.__current_params = list(self.__sem_ctx.locals)

        body = self.__expr_helper.check_block(def_point.ast_body)
        return_type_id = func_ty.return_type(self.__type_ctx)
        # Coerce unresolved literal types in expression-bodied functions
        # (e.g. fn f() -> u64 { 2 }) to the declared return type.
        if body.stmts:
            last = body.stmts[-1]
            last_ty = self.__type_ctx[last.type_id]
            if isinstance(last_ty, (Type.IntLiteralType, Type.FloatLiteralType)):
                body.stmts[-1] = self.__expr_helper.coerce(last, return_type_id)
                body.type_id = return_type_id
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
            symbol_id = self.__sem_ctx.symbol_ctx.add_symbol(param.name, SymbolKind.Variable, param.type_id)
            assert symbol_id is not None
            self.__sem_ctx.push_local(symbol_id)

        self.__current_params = list(self.__sem_ctx.locals)

        body = self.__expr_helper.check_block(def_point.ast_body)
        return_type_id = method_ty.return_type(self.__type_ctx)
        if body.stmts:
            last = body.stmts[-1]
            last_ty = self.__type_ctx[last.type_id]
            if isinstance(last_ty, (Type.IntLiteralType, Type.FloatLiteralType)):
                body.stmts[-1] = self.__expr_helper.coerce(last, return_type_id)
                body.type_id = return_type_id
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
            sid = self.__sem_ctx.symbol_ctx.add_symbol(param.name, SymbolKind.Variable, param.type_id)
            if sid is not None:
                self.__sem_ctx.push_local(sid)

        self.__current_params = list(self.__sem_ctx.locals)

        body = self.__expr_helper.check_block(def_point.ast_body)
        return_type_id = closure_ty.return_type
        if body.stmts:
            last = body.stmts[-1]
            last_ty = self.__type_ctx[last.type_id]
            if isinstance(last_ty, (Type.IntLiteralType, Type.FloatLiteralType)):
                body.stmts[-1] = self.__expr_helper.coerce(last, return_type_id)
                body.type_id = return_type_id
        return body
