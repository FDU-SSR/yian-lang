from __future__ import annotations

from collections.abc import Sequence

from compiler.frontend.parse import ast as AST
from compiler.frontend.parse.ast_type import (ASTType, ConstExpr,
                                              GenericConstExpr,
                                              LiteralConstExpr)
from compiler.utils.tree_format import TreeFormatter


class AstTreeFormatter(TreeFormatter):
    """Format the AST dump without changing the syntax tree."""

    def __format_attrs(self, attrs: list[AST.Attr]) -> str:
        return f"{' '.join(str(attr) for attr in attrs)} " if attrs else ""

    def __format_generics(self, generics: list[AST.GenericParam]) -> str:
        if not generics:
            return ""
        parts: list[str] = []
        for gen in generics:
            if isinstance(gen, AST.TypeGenericParam):
                parts.append(gen.name.name)
            else:
                parts.append(f"const {gen.name.name}: {gen.value_type}")
        return f"<{', '.join(parts)}>"

    def __export_type_list(self, types: Sequence[ASTType | ConstExpr], guides: list[bool]) -> str:
        res = ""
        for idx, value in enumerate(types):
            match value:
                case LiteralConstExpr() | GenericConstExpr():
                    res += self.render_line(guides, idx == len(types) - 1, f"ConstExpr: {value}")
                case _:
                    res += self.render_line(guides, idx == len(types) - 1, f"Type: {value}")
        return res

    def __export_expr_child(self, label: str, child: AST.Expr, guides: list[bool], parent_is_last: bool, is_last: bool) -> str:
        return self.render_child(label, child, guides, parent_is_last, is_last, self.__export_expr)

    def __export_block_child(self, label: str, child: AST.Block, guides: list[bool], parent_is_last: bool, is_last: bool) -> str:
        return self.render_child(
            label, child, guides, parent_is_last, is_last,
            lambda block, child_guides, _: self.__export_block(block, child_guides),
        )

    def __export_program_item(self, item: AST.ProgramItem, guides: list[bool], is_last: bool) -> str:
        match item:
            case AST.Import():
                return self.__export_import(item, guides, is_last)
            case AST.Alias():
                return self.__export_alias(item, guides, is_last)
            case AST.FuncDef():
                return self.__export_func_def(item, guides, is_last)
            case AST.StructDef():
                return self.__export_struct_def(item, guides, is_last)
            case AST.EnumDef():
                return self.__export_enum_def(item, guides, is_last)
            case AST.TraitDef():
                return self.__export_trait_def(item, guides, is_last)
            case AST.Impl():
                return self.__export_impl(item, guides, is_last)

    def __export_import(self, item: AST.Import, guides: list[bool], is_last: bool) -> str:
        return self.render_line(guides, is_last, f"Import: {item}")

    def __export_alias(self, item: AST.Alias, guides: list[bool], is_last: bool) -> str:
        return self.render_line(guides, is_last, f"Alias: {item}")

    def __export_func_def(self, item: AST.FuncDef, guides: list[bool], is_last: bool) -> str:
        header = (
            f"FuncDef: {self.__format_attrs(item.attrs)}fn {item.name.name}"
            f"{self.__format_generics(item.generics)}"
            f"({', '.join(str(param) for param in item.params)})"
            f"{f' -> {item.ret_type}' if item.ret_type else ''}"
        )
        res = self.render_line(guides, is_last, header)
        res += self.__export_block_child("Body", item.body, guides, is_last, True)
        return res

    def __export_struct_def(self, item: AST.StructDef, guides: list[bool], is_last: bool) -> str:
        res = self.render_line(guides, is_last, f"StructDef: {self.__format_attrs(item.attrs)}struct {item.name.name}{self.__format_generics(item.generics)}")
        if item.fields:
            child_guides = guides + [not is_last]
            res += self.render_line(child_guides, True, "Fields:")
            res += self.render_items(item.fields, child_guides + [False], self.__export_field_info)
        else:
            res += self.render_line(guides, True, "Fields: []")
        return res

    def __export_enum_def(self, item: AST.EnumDef, guides: list[bool], is_last: bool) -> str:
        res = self.render_line(guides, is_last, f"EnumDef: {self.__format_attrs(item.attrs)}enum {item.name.name}{self.__format_generics(item.generics)}")
        if item.variants:
            child_guides = guides + [not is_last]
            res += self.render_line(child_guides, True, "Variants:")
            res += self.render_items(item.variants, child_guides + [False], self.__export_variant_info)
        else:
            res += self.render_line(guides, True, "Variants: []")
        return res

    def __export_trait_def(self, item: AST.TraitDef, guides: list[bool], is_last: bool) -> str:
        res = self.render_line(guides, is_last, f"TraitDef: {self.__format_attrs(item.attrs)}trait {item.name.name}{self.__format_generics(item.generics)}")
        if item.items:
            child_guides = guides + [not is_last]
            res += self.render_line(child_guides, True, "Items:")
            res += self.__export_trait_items(item.items, child_guides + [False])
        else:
            res += self.render_line(guides, True, "Items: []")
        return res

    def __export_impl(self, item: AST.Impl, guides: list[bool], is_last: bool) -> str:
        if item.trait is not None:
            header = f"Impl: impl{self.__format_generics(item.generics)} {item.trait} for {item.target}"
        else:
            header = f"Impl: impl{self.__format_generics(item.generics)} {item.target}"
        res = self.render_line(guides, is_last, header)
        if item.items:
            child_guides = guides + [not is_last]
            res += self.render_line(child_guides, True, "Items:")
            res += self.render_items(item.items, child_guides + [False], self.__export_method_def)
        else:
            res += self.render_line(guides, True, "Items: []")
        return res

    def __export_field_info(self, field_info: AST.FieldInfo, guides: list[bool], is_last: bool) -> str:
        return self.render_line(guides, is_last, f"{field_info}")

    def __export_variant_info(self, variant_info: AST.VariantInfo, guides: list[bool], is_last: bool) -> str:
        return self.render_line(guides, is_last, f"VariantInfo: {variant_info}")

    def __export_method_decl(self, method_decl: AST.MethodDecl, guides: list[bool], is_last: bool) -> str:
        return self.render_line(guides, is_last, f"MethodDecl: {method_decl}")

    def __export_method_def(self, method_def: AST.MethodDef, guides: list[bool], is_last: bool) -> str:
        res = self.render_line(guides, is_last, f"MethodDef: {method_def.decl}")
        res += self.__export_block_child("Body", method_def.body, guides, is_last, True)
        return res

    def __export_stmt_block(self, stmt: AST.Block, guides: list[bool], is_last: bool) -> str:
        res = self.render_line(guides, is_last, "Block:")
        guides.append(not is_last)
        res += self.__export_block(stmt, guides)
        guides.pop()
        return res

    def __export_var_decl(self, stmt: AST.VarDecl, guides: list[bool], is_last: bool) -> str:
        res = self.render_line(guides, is_last, f"VarDecl: {stmt.var_type} {stmt.name.name}")
        if stmt.init_expr is not None:
            res += self.__export_expr_child("Init", stmt.init_expr, guides, is_last, True)
        return res

    def __export_return(self, stmt: AST.Return, guides: list[bool], is_last: bool) -> str:
        res = self.render_line(guides, is_last, "Return")
        if stmt.expr is not None:
            res += self.__export_expr_child("Value", stmt.expr, guides, is_last, True)
        return res

    def __export_if(self, stmt: AST.If, guides: list[bool], is_last: bool) -> str:
        res = self.render_line(guides, is_last, "If")
        res += self.__export_expr_child("Condition", stmt.condition, guides, is_last, False)
        has_else = stmt.else_branch is not None
        has_elif = len(stmt.elif_branches) > 0
        res += self.__export_block_child("Then", stmt.then_branch, guides, is_last, not has_elif and not has_else)
        for idx, (elif_condition, elif_block) in enumerate(stmt.elif_branches):
            is_last_elif = idx == len(stmt.elif_branches) - 1 and not has_else
            res += self.__export_stmt_if_elif(elif_condition, elif_block, guides, is_last_elif)
        if stmt.else_branch is not None:
            res += self.__export_block_child("Else", stmt.else_branch, guides, is_last, True)
        return res

    def __export_comptime_if(self, stmt: AST.ComptimeIf, guides: list[bool], is_last: bool) -> str:
        res = self.render_line(guides, is_last, "ComptimeIf")
        res += self.__export_expr_child("Condition", stmt.condition, guides, is_last, False)
        res += self.__export_block_child("Then", stmt.then_branch, guides, is_last, False)
        res += self.__export_block_child("Else", stmt.else_branch, guides, is_last, True)
        return res

    def __export_compile_config(self, expr: AST.CompileConfig, guides: list[bool], is_last: bool) -> str:
        return self.render_line(guides, is_last, f"CompileConfig: {expr.name}")

    def __export_stmt_if_elif(self, condition: AST.Expr, body: AST.Block, guides: list[bool], is_last: bool) -> str:
        res = self.render_line(guides, is_last, "Elif:")
        res += self.__export_expr_child("Condition", condition, guides, is_last, False)
        res += self.__export_block_child("Body", body, guides, is_last, True)
        return res

    def __export_array_repeat(self, stmt: AST.ArrayRepeat, guides: list[bool], is_last: bool) -> str:
        res = self.render_line(guides, is_last, "ArrayRepeat")
        res += self.__export_expr_child("Element", stmt.element, guides, is_last, False)
        res += self.__export_expr_child("Count", stmt.count, guides, is_last, True)
        return res

    def __export_for(self, stmt: AST.For, guides: list[bool], is_last: bool) -> str:
        res = self.render_line(guides, is_last, f"For: {stmt.var_name.name}")
        res += self.__export_expr_child("Iterable", stmt.iterable, guides, is_last, False)
        res += self.__export_block_child("Body", stmt.body, guides, is_last, True)
        return res

    def __export_while(self, stmt: AST.While, guides: list[bool], is_last: bool) -> str:
        res = self.render_line(guides, is_last, "While")
        res += self.__export_expr_child("Condition", stmt.condition, guides, is_last, False)
        res += self.__export_block_child("Body", stmt.body, guides, is_last, True)
        return res

    def __export_loop(self, stmt: AST.Loop, guides: list[bool], is_last: bool) -> str:
        res = self.render_line(guides, is_last, "Loop")
        res += self.__export_block_child("Body", stmt.body, guides, is_last, True)
        return res

    def __export_match(self, stmt: AST.Match, guides: list[bool], is_last: bool) -> str:
        res = self.render_line(guides, is_last, "Match")
        res += self.__export_expr_child("Value", stmt.expr, guides, is_last, False)
        child_guides = guides + [not is_last]
        res += self.render_line(child_guides, True, "Arms:")
        res += self.render_items(stmt.arms, child_guides + [False], lambda arm, g, last: self.__export_stmt_match_arm(arm, g, last))
        return res

    def __export_stmt_match_arm(self, arm: AST.MatchArm, guides: list[bool], is_last: bool) -> str:
        res = self.render_line(guides, is_last, f"Arm: {arm.pattern}")
        guides.append(not is_last)
        if arm.guard is not None:
            res += self.__export_expr_child("Guard", arm.guard, guides, is_last, False)
        res += self.__export_block(arm.body, guides)
        guides.pop()
        return res

    def __export_break(self, _: AST.Break, guides: list[bool], is_last: bool) -> str:
        return self.render_line(guides, is_last, "Break")

    def __export_continue(self, _: AST.Continue, guides: list[bool], is_last: bool) -> str:
        return self.render_line(guides, is_last, "Continue")

    def __export_defer(self, stmt: AST.Defer, guides: list[bool], is_last: bool) -> str:
        res = self.render_line(guides, is_last, "Defer")
        res += self.__export_expr_child("Action", stmt.action, guides, is_last, True)
        return res

    def __export_assert(self, stmt: AST.Assert, guides: list[bool], is_last: bool) -> str:
        res = self.render_line(guides, is_last, "Assert")
        res += self.__export_expr_child("Condition", stmt.condition, guides, is_last, stmt.message is None)
        if stmt.message is not None:
            res += self.__export_expr_child("Message", stmt.message, guides, is_last, True)
        return res

    def __export_delete(self, stmt: AST.Delete, guides: list[bool], is_last: bool) -> str:
        res = self.render_line(guides, is_last, "Delete")
        res += self.__export_expr_child("Target", stmt.target, guides, is_last, True)
        return res

    def __export_stmt_expr(self, stmt: AST.Expr, guides: list[bool], is_last: bool) -> str:
        res = self.render_line(guides, is_last, "ExprStmt:")
        guides.append(not is_last)
        res += self.__export_expr(stmt, guides, True)
        guides.pop()
        return res

    def __export_block(self, block: AST.Block, guides: list[bool]) -> str:
        return self.render_items(block.stmts, guides, self.__export_stmt_expr)

    def __export_expr(self, expr: AST.Expr, guides: list[bool], is_last: bool) -> str:
        match expr:
            case AST.Binary():
                return self.__export_binary(expr, guides, is_last)
            case AST.Unary():
                return self.__export_unary(expr, guides, is_last)
            case AST.Call():
                return self.__export_call(expr, guides, is_last)
            case AST.Builtin():
                return self.__export_builtin(expr, guides, is_last)
            case AST.MethodCall():
                return self.__export_method_call(expr, guides, is_last)
            case AST.FieldAccess():
                return self.__export_field_access(expr, guides, is_last)
            case AST.DynValue():
                return self.__export_dyn_value(expr, guides, is_last)
            case AST.DynBuffer():
                return self.__export_dyn_buffer(expr, guides, is_last)
            case AST.TypeItem():
                return self.__export_type_item(expr, guides, is_last)
            case AST.Identifier():
                return self.__export_identifier(expr, guides, is_last)
            case AST.Literal():
                return self.__export_literal(expr, guides, is_last)
            case AST.Tuple():
                return self.__export_tuple(expr, guides, is_last)
            case AST.Array():
                return self.__export_array(expr, guides, is_last)
            case AST.Block():
                return self.__export_stmt_block(expr, guides, is_last)
            case AST.If():
                return self.__export_if(expr, guides, is_last)
            case AST.ComptimeIf():
                return self.__export_comptime_if(expr, guides, is_last)
            case AST.Loop():
                return self.__export_loop(expr, guides, is_last)
            case AST.Match():
                return self.__export_match(expr, guides, is_last)
            case AST.VarDecl():
                return self.__export_var_decl(expr, guides, is_last)
            case AST.Return():
                return self.__export_return(expr, guides, is_last)
            case AST.Break():
                return self.__export_break(expr, guides, is_last)
            case AST.Continue():
                return self.__export_continue(expr, guides, is_last)
            case AST.Defer():
                return self.__export_defer(expr, guides, is_last)
            case AST.Semi():
                return self.__export_stmt_expr(expr.expr, guides, is_last)
            case AST.ArrayRepeat():
                return self.__export_array_repeat(expr, guides, is_last)
            case AST.For():
                return self.__export_for(expr, guides, is_last)
            case AST.While():
                return self.__export_while(expr, guides, is_last)
            case AST.Assert():
                return self.__export_assert(expr, guides, is_last)
            case AST.Delete():
                return self.__export_delete(expr, guides, is_last)
            case AST.ClosureExpr():
                return self.__export_closure_expr(expr, guides, is_last)
            case AST.CompileConfig():
                return self.__export_compile_config(expr, guides, is_last)

    def __export_closure_expr(self, expr: AST.ClosureExpr, guides: list[bool], is_last: bool) -> str:
        captures_str = ", ".join(repr(c) for c in expr.captures)
        params_str = ", ".join(repr(p) for p in expr.params)
        ret_str = f" -> {expr.return_type}" if expr.return_type is not None else ""
        res = self.render_line(guides, is_last, f"ClosureExpr: |{captures_str}| ({params_str}){ret_str}")
        for cap in expr.captures:
            res += self.__export_expr_child(f"Capture({cap.name.name})", cap.expr, guides, is_last, False)
        res += self.__export_block_child("Body", expr.body, guides, is_last, True)
        return res

    def __export_binary(self, expr: AST.Binary, guides: list[bool], is_last: bool) -> str:
        res = self.render_line(guides, is_last, f"BinaryOp: {expr.op}")
        res += self.__export_expr_child("Left", expr.left, guides, is_last, False)
        res += self.__export_expr_child("Right", expr.right, guides, is_last, True)
        return res

    def __export_unary(self, expr: AST.Unary, guides: list[bool], is_last: bool) -> str:
        res = self.render_line(guides, is_last, f"UnaryOp: {expr.op}")
        res += self.__export_expr_child("Operand", expr.operand, guides, is_last, True)
        return res

    def __export_call(self, expr: AST.Call, guides: list[bool], is_last: bool) -> str:
        res = self.render_line(guides, is_last, "Call")
        res += self.__export_expr_child("Callee", expr.callee, guides, is_last, False)
        if expr.args:
            child_guides = guides + [not is_last]
            res += self.render_line(child_guides, True, "Args:")
            res += self.render_items(expr.args, child_guides + [False], self.__export_arg)
        else:
            child_guides = guides + [not is_last]
            res += self.render_line(child_guides, True, "Args: []")
        return res

    def __export_builtin(self, expr: AST.Builtin, guides: list[bool], is_last: bool) -> str:
        res = self.render_line(guides, is_last, f"Builtin: {expr.kind.spelling}")
        if expr.type_args:
            child_guides = guides + [not is_last]
            res += self.render_line(child_guides, False if expr.args else True, "TypeArgs:")
            res += self.__export_type_list(expr.type_args, child_guides + [expr.args != []])
        if expr.args:
            child_guides = guides + [not is_last]
            res += self.render_line(child_guides, True, "Args:")
            res += self.render_items(expr.args, child_guides + [False], self.__export_arg)
        else:
            child_guides = guides + [not is_last]
            res += self.render_line(child_guides, True, "Args: []")
        return res

    def __export_method_call(self, expr: AST.MethodCall, guides: list[bool], is_last: bool) -> str:
        res = self.render_line(guides, is_last, f"MethodCall: {expr.method_name.name}")
        res += self.__export_expr_child("Receiver", expr.receiver, guides, is_last, False)
        if expr.generics:
            child_guides = guides + [not is_last]
            res += self.render_line(child_guides, False if expr.args else True, "Generics:")
            res += self.__export_type_list(expr.generics, child_guides + [expr.args != []])
        if expr.args:
            child_guides = guides + [not is_last]
            res += self.render_line(child_guides, True, "Args:")
            res += self.render_items(expr.args, child_guides + [False], self.__export_arg)
        elif not expr.generics:
            child_guides = guides + [not is_last]
            res += self.render_line(child_guides, True, "Args: []")
        return res

    def __export_field_access(self, expr: AST.FieldAccess, guides: list[bool], is_last: bool) -> str:
        res = self.render_line(guides, is_last, f"FieldAccess: {expr.field_name.name}")
        res += self.__export_expr_child("Receiver", expr.receiver, guides, is_last, True)
        return res

    def __export_dyn_value(self, expr: AST.DynValue, guides: list[bool], is_last: bool) -> str:
        res = self.render_line(guides, is_last, "DynValue")
        res += self.__export_expr_child("Value", expr.value, guides, is_last, True)
        return res

    def __export_dyn_buffer(self, expr: AST.DynBuffer, guides: list[bool], is_last: bool) -> str:
        res = self.render_line(guides, is_last, "DynBuffer")
        res += self.__export_expr_child("Size", expr.size, guides, False, False)
        res += self.__export_expr_child("Element", expr.element, guides, is_last, True)
        return res

    def __export_type_item(self, expr: AST.TypeItem, guides: list[bool], is_last: bool) -> str:
        return self.render_line(guides, is_last, f"TypeItem: {expr}")

    def __export_identifier(self, expr: AST.Identifier, guides: list[bool], is_last: bool) -> str:
        return self.render_line(guides, is_last, f"Identifier: {expr.name}")

    def __export_literal(self, expr: AST.Literal, guides: list[bool], is_last: bool) -> str:
        return self.render_line(guides, is_last, f"Literal: {expr.literal}")

    def __export_tuple(self, expr: AST.Tuple, guides: list[bool], is_last: bool) -> str:
        res = self.render_line(guides, is_last, "Tuple")
        if expr.elements:
            res += self.render_line(guides, True, "Elements:")
            guides.append(False)
            res += self.render_items(expr.elements, guides, self.__export_expr)
            guides.pop()
        else:
            res += self.render_line(guides, True, "Elements: []")
        return res

    def __export_array(self, expr: AST.Array, guides: list[bool], is_last: bool) -> str:
        res = self.render_line(guides, is_last, "Array")
        guides.append(not is_last)
        if expr.elements:
            res += self.render_line(guides, True, "Elements:")
            guides.append(False)
            res += self.render_items(expr.elements, guides, self.__export_expr)
            guides.pop()
        else:
            res += self.render_line(guides, True, "Elements: []")
        guides.pop()
        return res

    def __export_arg(self, arg: AST.Arg, guides: list[bool], is_last: bool) -> str:
        return self.render_line(guides, is_last, f"{arg}")

    def __export_trait_items(self, items: list[AST.TraitItem], guides: list[bool]) -> str:
        res = ""
        for idx, item in enumerate(items):
            if isinstance(item, AST.MethodDef):
                res += self.__export_method_def(item, guides, idx == len(items) - 1)
            else:
                res += self.__export_method_decl(item, guides, idx == len(items) - 1)
        return res

    def __export_program(self, program: AST.Program) -> str:
        return "Program\n" + self.render_items(program.items, [], self.__export_program_item)

    def format_program(self, program: AST.Program) -> str:
        return self.__export_program(program)


def export_program(program: AST.Program) -> str:
    return AstTreeFormatter().format_program(program)
