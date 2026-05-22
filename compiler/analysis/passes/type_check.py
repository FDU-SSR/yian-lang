from compiler.analysis.error import AnalysisError
from compiler.analysis.passes.expr_check import ExprCheck
from compiler.analysis.symbol.context import SymbolCtx
from compiler.analysis.symbol.symbol import SymbolKind
from compiler.analysis.ty import ty as Type
from compiler.analysis.ty.context import TypeCtx
from compiler.analysis.unit import hir as HIR
from compiler.analysis.unit.def_point import DefPoint
from compiler.analysis.unit.unit_data import UnitData
from compiler.frontend.parse import ast as AST
from compiler.frontend.parse.operator import BinaryOperator, UnaryOperator
from compiler.utils.IR.position import SrcSpan


class TypeCheck:
    def __init__(self, units: dict[int, UnitData], type_ctx: TypeCtx):
        self.__units = units
        self.__type_ctx = type_ctx

        self.__worklist: list[DefPoint] = []
        self.__def_points: dict[int, DefPoint] = {}  # type_id -> DefPoint

        self.__current_type_id: int = -1
        self.__current_locals: list[int] = []

        self.__expr_checker = ExprCheck(self)

    def run(self) -> None:
        self.__find_main()

        processed_def: set[int] = set()
        while self.__worklist:
            def_point = self.__worklist.pop()
            if def_point.type_id in processed_def:
                continue
            processed_def.add(def_point.type_id)

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
                    main_def_point = DefPoint(type_id=symbol.type_id, unit_id=unit.unit_id, ast_body=item.body, symbol_ctx=unit.symbol_ctx.clone())
                    self.__worklist.append(main_def_point)
                    self.__def_points[symbol.type_id] = main_def_point
        if not self.__worklist:
            raise AnalysisError("No 'main' function found", SrcSpan.empty())

    def __type_check_def(self, def_point: DefPoint) -> None:
        self.__current_type_id = def_point.type_id
        self.__current_locals = []

        ty = self.__type_ctx[self.__current_type_id]
        if isinstance(ty, Type.FunctionType):
            def_point.body = self.__check_function(def_point)
        elif isinstance(ty, Type.MethodType):
            def_point.body = self.__check_method(def_point)
        else:
            raise AnalysisError(f"Type with id {def_point.type_id} is not a function or method type", SrcSpan.empty())

        def_point.locals = self.__current_locals

    def __check_function(self, def_point: DefPoint) -> HIR.Block:
        func_ty = self.__type_ctx[self.__current_type_id]
        assert isinstance(func_ty, Type.FunctionType)

        def_point.symbol_ctx.enter_scope()

        # add generics to symbol context
        for generic, generic_arg in zip(func_ty.custom_def.generics, func_ty.generic_args):
            generic_ty = self.__type_ctx[generic]
            assert isinstance(generic_ty, Type.GenericType)
            def_point.symbol_ctx.add_symbol(generic_ty.name, SymbolKind.Type, generic_arg)

        # add parameters to symbol context and create local variables for them
        for param in func_ty.parameters(self.__type_ctx):
            symbol_id = def_point.symbol_ctx.add_symbol(param.name, SymbolKind.Variable, param.type_id)
            assert symbol_id is not None
            self.__current_locals.append(symbol_id)

        # type check function body
        body = self.__check_block(def_point.ast_body, def_point.symbol_ctx)

        def_point.symbol_ctx.exit_scope()

        return body

    def __check_method(self, def_point: DefPoint) -> HIR.Block:
        method_ty = self.__type_ctx[self.__current_type_id]
        assert isinstance(method_ty, Type.MethodType)

        def_point.symbol_ctx.enter_scope()

        # add generics to symbol context
        for generic, generic_arg in zip(method_ty.custom_def.generics, method_ty.generic_args):
            generic_ty = self.__type_ctx[generic]
            assert isinstance(generic_ty, Type.GenericType)
            def_point.symbol_ctx.add_symbol(generic_ty.name, SymbolKind.Type, generic_arg)

        # add Self type to symbol context
        Self_type_id = method_ty.receiver_type(self.__type_ctx)
        def_point.symbol_ctx.add_symbol("Self", SymbolKind.Type, Self_type_id)

        # add receiver to symbol context and create a local variable for it if the method is not static
        if not method_ty.custom_def.is_static:
            self_type_id = self.__type_ctx.alloc_pointer(Self_type_id)
            symbol_id = def_point.symbol_ctx.add_symbol("self", SymbolKind.Variable, self_type_id)
            assert symbol_id is not None
            self.__current_locals.append(symbol_id)

        # add parameters to symbol context and create local variables for them
        for param in method_ty.parameters(self.__type_ctx):
            symbol_id = def_point.symbol_ctx.add_symbol(param.name, SymbolKind.Variable, param.type_id)
            assert symbol_id is not None
            self.__current_locals.append(symbol_id)

        # type check method body
        body = self.__check_block(def_point.ast_body, def_point.symbol_ctx)

        def_point.symbol_ctx.exit_scope()

        return body

    def __check_block(self, ast_block: AST.Block, symbol_ctx: SymbolCtx) -> HIR.Block:
        symbol_ctx.enter_scope()
        stmts: list[HIR.Stmt] = []
        for stmt in ast_block.stmts:
            self.__check_stmt(stmt, stmts, symbol_ctx)
        symbol_ctx.exit_scope()

        return HIR.Block(span=ast_block.span, stmts=stmts)

    def __check_stmt(self, stmt: AST.Stmt, out: list[HIR.Stmt], symbol_ctx: SymbolCtx) -> None:
        match stmt:
            case AST.Block():
                out.append(self.__check_block(stmt, symbol_ctx))
            case AST.VarDecl():
                self.__check_var_decl(stmt, out, symbol_ctx)
            case AST.If():
                self.__check_if(stmt, out, symbol_ctx)
            case AST.For():
                self.__check_for(stmt, out, symbol_ctx)
            case AST.While():
                self.__check_while(stmt, out, symbol_ctx)
            case AST.Loop():
                self.__check_loop(stmt, out, symbol_ctx)
            case AST.Match():
                self.__check_match(stmt, out, symbol_ctx)
            case AST.Return():
                self.__check_return(stmt, out, symbol_ctx)
            case AST.Break():
                self.__check_break(stmt, out, symbol_ctx)
            case AST.Continue():
                self.__check_continue(stmt, out, symbol_ctx)
            case AST.Assert():
                self.__check_assert(stmt, out, symbol_ctx)
            case AST.Delete():
                self.__check_delete(stmt, out, symbol_ctx)
            case _:
                out.append(self.__expr_eval(stmt, symbol_ctx))

    def __check_var_decl(self, stmt: AST.VarDecl, out: list[HIR.Stmt], symbol_ctx: SymbolCtx) -> None:
        # add variable to symbol context and create a local variable for it
        var_type_id = self.__type_ctx.resolve_type(stmt.var_type, symbol_ctx)
        symbol_id = symbol_ctx.add_symbol(stmt.name.name, SymbolKind.Variable, var_type_id)
        if symbol_id is None:
            raise AnalysisError(f"Variable '{stmt.name.name}' is already defined in the current scope", stmt.span)
        self.__current_locals.append(symbol_id)

        # type check initializer if it exists
        if stmt.init_expr is not None:
            init_expr = self.__expr_value(stmt.init_expr, symbol_ctx, var_type_id)
            var = HIR.Var(span=stmt.name.span, symbol_id=symbol_id, type_id=var_type_id, is_place=True)
            out.append(HIR.Binary(
                span=stmt.span,
                op=BinaryOperator.Assign,
                left=var,
                right=init_expr,
                type_id=var_type_id,
                is_place=False
            ))

    def __check_if(self, stmt: AST.If, out: list[HIR.Stmt], symbol_ctx: SymbolCtx) -> None:
        # type check condition
        cond_expr = self.__expr_value(stmt.condition, symbol_ctx, expected=TypeCtx.bool_id)

        # type check then branch
        then_block = self.__check_block(stmt.then_branch, symbol_ctx)

        # type check elif branches
        elif_blocks: list[tuple[HIR.Expr, HIR.Block]] = []
        for elif_branch in stmt.elif_branches:
            elif_cond_expr = self.__expr_value(elif_branch[0], symbol_ctx, expected=TypeCtx.bool_id)
            elif_block = self.__check_block(elif_branch[1], symbol_ctx)
            elif_blocks.append((elif_cond_expr, elif_block))

        # type check else branch
        else_block = self.__check_block(stmt.else_branch, symbol_ctx) if stmt.else_branch is not None else None

        # convert elif blocks to nested if-else
        current_else_block = else_block
        for elif_cond_expr, elif_block in reversed(elif_blocks):
            current_else_block = HIR.Block(
                span=elif_block.span,
                stmts=[HIR.If(
                    span=elif_cond_expr.span,
                    cond=elif_cond_expr,
                    then_branch=elif_block,
                    else_branch=current_else_block
                )]
            )

        out.append(HIR.If(span=stmt.span, cond=cond_expr, then_branch=then_block, else_branch=current_else_block))

    def __check_for(self, stmt: AST.For, out: list[HIR.Stmt], symbol_ctx: SymbolCtx) -> None:
        raise NotImplementedError("For statement is not implemented yet")

    def __check_while(self, stmt: AST.While, out: list[HIR.Stmt], symbol_ctx: SymbolCtx) -> None:
        # type check condition
        cond_expr = self.__expr_value(stmt.condition, symbol_ctx, expected=TypeCtx.bool_id)

        # type check body
        body_block = self.__check_block(stmt.body, symbol_ctx)

        # loop { if not condition { break } body }
        not_cond_expr = HIR.Unary(
            span=cond_expr.span,
            op=UnaryOperator.LogicalNot,
            operand=cond_expr,
            type_id=TypeCtx.bool_id,
            is_place=False
        )
        break_stmt = HIR.Break(span=stmt.span)
        if_stmt = HIR.If(
            span=cond_expr.span,
            cond=not_cond_expr,
            then_branch=HIR.Block(span=stmt.span, stmts=[break_stmt]),
            else_branch=None
        )
        loop_block = HIR.Block(span=stmt.span, stmts=[if_stmt, body_block])
        out.append(HIR.Loop(span=stmt.span, body=loop_block))

    def __check_loop(self, stmt: AST.Loop, out: list[HIR.Stmt], symbol_ctx: SymbolCtx) -> None:
        # type check body
        body_block = self.__check_block(stmt.body, symbol_ctx)

        out.append(HIR.Loop(span=stmt.span, body=body_block))

    def __check_match(self, stmt: AST.Match, out: list[HIR.Stmt], symbol_ctx: SymbolCtx) -> None:
        raise NotImplementedError("Match statement is not implemented yet")

    def __check_return(self, stmt: AST.Return, out: list[HIR.Stmt], symbol_ctx: SymbolCtx) -> None:
        raise NotImplementedError("Return statement is not implemented yet")

    def __check_break(self, stmt: AST.Break, out: list[HIR.Stmt], symbol_ctx: SymbolCtx) -> None:
        raise NotImplementedError("Break statement is not implemented yet")

    def __check_continue(self, stmt: AST.Continue, out: list[HIR.Stmt], symbol_ctx: SymbolCtx) -> None:
        raise NotImplementedError("Continue statement is not implemented yet")

    def __check_assert(self, stmt: AST.Assert, out: list[HIR.Stmt], symbol_ctx: SymbolCtx) -> None:
        raise NotImplementedError("Assert statement is not implemented yet")

    def __check_delete(self, stmt: AST.Delete, out: list[HIR.Stmt], symbol_ctx: SymbolCtx) -> None:
        raise NotImplementedError("Delete statement is not implemented yet")

    def __expr_eval(self, expr: AST.Expr, symbol_ctx: SymbolCtx) -> HIR.Expr:
        return self.__expr_checker.eval(expr, symbol_ctx)

    def __expr_value(self, expr: AST.Expr, symbol_ctx: SymbolCtx, expected: int | None) -> HIR.Expr:
        return self.__expr_checker.value(expr, symbol_ctx, expected)
