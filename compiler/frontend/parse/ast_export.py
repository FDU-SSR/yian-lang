from __future__ import annotations

from collections.abc import Sequence
from typing import Callable

from compiler.frontend.parse import ast as AST
from compiler.frontend.parse.ast_type import (ASTType, ConstExpr,
                                              GenericConstExpr,
                                              LiteralConstExpr)


def __gen_prefix(guides: list[bool], is_last: bool) -> str:
    res = ""
    for guide in guides:
        if guide:
            res += "│   "
        else:
            res += "    "
    if is_last:
        res += "└── "
    else:
        res += "├── "
    return res


def __line(guides: list[bool], is_last: bool, text: str) -> str:
    return __gen_prefix(guides, is_last) + text + "\n"


def __format_attrs(attrs: list[AST.Attr]) -> str:
    return f"{' '.join(str(attr) for attr in attrs)} " if attrs else ""


def __format_generics(generics: list[AST.GenericParam]) -> str:
    if not generics:
        return ""
    parts: list[str] = []
    for gen in generics:
        if isinstance(gen, AST.TypeGenericParam):
            parts.append(gen.name.name)
        else:
            parts.append(f"const {gen.name.name}: {gen.value_type}")
    return f"<{', '.join(parts)}>"


def __export_items_with_handler[ItemType](items: list[ItemType], guides: list[bool], handler: Callable[[ItemType, list[bool], bool], str]) -> str:
    res = ""
    for idx, item in enumerate(items):
        res += handler(item, guides, idx == len(items) - 1)
    return res


def __export_type_list(types: Sequence[ASTType | ConstExpr], guides: list[bool]) -> str:
    res = ""
    for idx, value in enumerate(types):
        match value:
            case LiteralConstExpr() | GenericConstExpr():
                res += __line(guides, idx == len(types) - 1, f"ConstExpr: {value}")
            case _:
                res += __line(guides, idx == len(types) - 1, f"Type: {value}")
    return res


def __export_expr_child(label: str, child: AST.Expr, guides: list[bool], parent_is_last: bool, is_last: bool) -> str:
    child_guides = guides + [not parent_is_last]
    res = __line(child_guides, is_last, f"{label}:")
    res += __export_expr(child, child_guides + [not is_last], True)
    return res


def __export_block_child(label: str, child: AST.Block, guides: list[bool], parent_is_last: bool, is_last: bool) -> str:
    child_guides = guides + [not parent_is_last]
    res = __line(child_guides, is_last, f"{label}:")
    res += __export_block(child, child_guides + [not is_last])
    return res


def __export_program_item(item: AST.ProgramItem, guides: list[bool], is_last: bool) -> str:
    match item:
        case AST.Import():
            return __export_import(item, guides, is_last)
        case AST.Alias():
            return __export_alias(item, guides, is_last)
        case AST.FuncDef():
            return __export_func_def(item, guides, is_last)
        case AST.StructDef():
            return __export_struct_def(item, guides, is_last)
        case AST.EnumDef():
            return __export_enum_def(item, guides, is_last)
        case AST.TraitDef():
            return __export_trait_def(item, guides, is_last)
        case AST.Impl():
            return __export_impl(item, guides, is_last)


def __export_import(item: AST.Import, guides: list[bool], is_last: bool) -> str:
    return __line(guides, is_last, f"Import: {item}")


def __export_alias(item: AST.Alias, guides: list[bool], is_last: bool) -> str:
    return __line(guides, is_last, f"Alias: {item}")


def __export_func_def(item: AST.FuncDef, guides: list[bool], is_last: bool) -> str:
    header = (
        f"FuncDef: {__format_attrs(item.attrs)}fn {item.name.name}"
        f"{__format_generics(item.generics)}"
        f"({', '.join(str(param) for param in item.params)})"
        f"{f' -> {item.ret_type}' if item.ret_type else ''}"
    )
    res = __line(guides, is_last, header)
    res += __export_block_child("Body", item.body, guides, is_last, True)
    return res


def __export_struct_def(item: AST.StructDef, guides: list[bool], is_last: bool) -> str:
    res = __line(guides, is_last, f"StructDef: {__format_attrs(item.attrs)}struct {item.name.name}{__format_generics(item.generics)}")
    if item.fields:
        child_guides = guides + [not is_last]
        res += __line(child_guides, True, "Fields:")
        res += __export_items_with_handler(item.fields, child_guides + [False], __export_field_info)
    else:
        res += __line(guides, True, "Fields: []")
    return res


def __export_enum_def(item: AST.EnumDef, guides: list[bool], is_last: bool) -> str:
    res = __line(guides, is_last, f"EnumDef: {__format_attrs(item.attrs)}enum {item.name.name}{__format_generics(item.generics)}")
    if item.variants:
        child_guides = guides + [not is_last]
        res += __line(child_guides, True, "Variants:")
        res += __export_items_with_handler(item.variants, child_guides + [False], __export_variant_info)
    else:
        res += __line(guides, True, "Variants: []")
    return res


def __export_trait_def(item: AST.TraitDef, guides: list[bool], is_last: bool) -> str:
    res = __line(guides, is_last, f"TraitDef: {__format_attrs(item.attrs)}trait {item.name.name}{__format_generics(item.generics)}")
    if item.items:
        child_guides = guides + [not is_last]
        res += __line(child_guides, True, "Items:")
        res += __export_trait_items(item.items, child_guides + [False])
    else:
        res += __line(guides, True, "Items: []")
    return res


def __export_impl(item: AST.Impl, guides: list[bool], is_last: bool) -> str:
    if item.trait is not None:
        header = f"Impl: impl{__format_generics(item.generics)} {item.trait} for {item.target}"
    else:
        header = f"Impl: impl{__format_generics(item.generics)} {item.target}"
    res = __line(guides, is_last, header)
    if item.items:
        child_guides = guides + [not is_last]
        res += __line(child_guides, True, "Items:")
        res += __export_items_with_handler(item.items, child_guides + [False], __export_method_def)
    else:
        res += __line(guides, True, "Items: []")
    return res


def __export_field_info(field_info: AST.FieldInfo, guides: list[bool], is_last: bool) -> str:
    return __line(guides, is_last, f"{field_info}")


def __export_variant_info(variant_info: AST.VariantInfo, guides: list[bool], is_last: bool) -> str:
    return __line(guides, is_last, f"VariantInfo: {variant_info}")


def __export_method_decl(method_decl: AST.MethodDecl, guides: list[bool], is_last: bool) -> str:
    return __line(guides, is_last, f"MethodDecl: {method_decl}")


def __export_method_def(method_def: AST.MethodDef, guides: list[bool], is_last: bool) -> str:
    res = __line(guides, is_last, f"MethodDef: {method_def.decl}")
    res += __export_block_child("Body", method_def.body, guides, is_last, True)
    return res


def __export_stmt(stmt: AST.Expr, guides: list[bool], is_last: bool) -> str:
    match stmt:
        case AST.Block():
            return __export_stmt_block(stmt, guides, is_last)
        case AST.VarDecl():
            return __export_var_decl(stmt, guides, is_last)
        case AST.Return():
            return __export_return(stmt, guides, is_last)
        case AST.If():
            return __export_if(stmt, guides, is_last)
        case AST.For():
            return __export_for(stmt, guides, is_last)
        case AST.While():
            return __export_while(stmt, guides, is_last)
        case AST.Loop():
            return __export_loop(stmt, guides, is_last)
        case AST.Match():
            return __export_match(stmt, guides, is_last)
        case AST.Break():
            return __export_break(stmt, guides, is_last)
        case AST.Continue():
            return __export_continue(stmt, guides, is_last)
        case AST.Assert():
            return __export_assert(stmt, guides, is_last)
        case AST.Delete():
            return __export_delete(stmt, guides, is_last)
        case AST.Semi():
            # Unwrap Semi — re-dispatch the inner expression as a statement
            return __export_stmt(stmt.expr, guides, is_last)
        case AST.Binary() | AST.Unary() | AST.Call() | AST.MethodCall() | AST.FieldAccess() | AST.DynValue() | AST.DynBuffer() | AST.TypeItem() | AST.Identifier() | AST.Literal() | AST.Tuple() | AST.Array():
            return __export_stmt_expr(stmt, guides, is_last)


def __export_stmt_block(stmt: AST.Block, guides: list[bool], is_last: bool) -> str:
    res = __line(guides, is_last, "Block:")
    guides.append(not is_last)
    res += __export_block(stmt, guides)
    guides.pop()
    return res


def __export_var_decl(stmt: AST.VarDecl, guides: list[bool], is_last: bool) -> str:
    res = __line(guides, is_last, f"VarDecl: {stmt.var_type} {stmt.name.name}")
    if stmt.init_expr is not None:
        res += __export_expr_child("Init", stmt.init_expr, guides, is_last, True)
    return res


def __export_return(stmt: AST.Return, guides: list[bool], is_last: bool) -> str:
    res = __line(guides, is_last, "Return")
    if stmt.expr is not None:
        res += __export_expr_child("Value", stmt.expr, guides, is_last, True)
    return res


def __export_if(stmt: AST.If, guides: list[bool], is_last: bool) -> str:
    res = __line(guides, is_last, "If")
    res += __export_expr_child("Condition", stmt.condition, guides, is_last, False)
    has_else = stmt.else_branch is not None
    has_elif = len(stmt.elif_branches) > 0
    res += __export_block_child("Then", stmt.then_branch, guides, is_last, not has_elif and not has_else)
    for idx, (elif_condition, elif_block) in enumerate(stmt.elif_branches):
        is_last_elif = idx == len(stmt.elif_branches) - 1 and not has_else
        res += __export_stmt_if_elif(elif_condition, elif_block, guides, is_last_elif)
    if stmt.else_branch is not None:
        res += __export_block_child("Else", stmt.else_branch, guides, is_last, True)
    return res


def __export_stmt_if_elif(condition: AST.Expr, body: AST.Block, guides: list[bool], is_last: bool) -> str:
    res = __line(guides, is_last, "Elif:")
    res += __export_expr_child("Condition", condition, guides, is_last, False)
    res += __export_block_child("Body", body, guides, is_last, True)
    return res


def __export_for(stmt: AST.For, guides: list[bool], is_last: bool) -> str:
    res = __line(guides, is_last, f"For: {stmt.var_name.name}")
    res += __export_expr_child("Iterable", stmt.iterable, guides, is_last, False)
    res += __export_block_child("Body", stmt.body, guides, is_last, True)
    return res


def __export_while(stmt: AST.While, guides: list[bool], is_last: bool) -> str:
    res = __line(guides, is_last, "While")
    res += __export_expr_child("Condition", stmt.condition, guides, is_last, False)
    res += __export_block_child("Body", stmt.body, guides, is_last, True)
    return res


def __export_loop(stmt: AST.Loop, guides: list[bool], is_last: bool) -> str:
    res = __line(guides, is_last, "Loop")
    res += __export_block_child("Body", stmt.body, guides, is_last, True)
    return res


def __export_match(stmt: AST.Match, guides: list[bool], is_last: bool) -> str:
    res = __line(guides, is_last, "Match")
    res += __export_expr_child("Value", stmt.expr, guides, is_last, False)
    child_guides = guides + [not is_last]
    res += __line(child_guides, True, "Arms:")
    res += __export_items_with_handler(stmt.arms, child_guides + [False], lambda arm, g, last: __export_stmt_match_arm(arm[0], arm[1], g, last))
    return res


def __export_stmt_match_arm(pattern: AST.Pattern, arm_block: AST.Block, guides: list[bool], is_last: bool) -> str:
    res = __line(guides, is_last, f"Arm: {pattern}")
    guides.append(not is_last)
    res += __export_block(arm_block, guides)
    guides.pop()
    return res


def __export_break(_: AST.Break, guides: list[bool], is_last: bool) -> str:
    return __line(guides, is_last, "Break")


def __export_continue(_: AST.Continue, guides: list[bool], is_last: bool) -> str:
    return __line(guides, is_last, "Continue")


def __export_assert(stmt: AST.Assert, guides: list[bool], is_last: bool) -> str:
    res = __line(guides, is_last, "Assert")
    res += __export_expr_child("Condition", stmt.condition, guides, is_last, stmt.message is None)
    if stmt.message is not None:
        res += __export_expr_child("Message", stmt.message, guides, is_last, True)
    return res


def __export_delete(stmt: AST.Delete, guides: list[bool], is_last: bool) -> str:
    res = __line(guides, is_last, "Delete")
    res += __export_expr_child("Target", stmt.target, guides, is_last, True)
    return res


def __export_stmt_expr(stmt: AST.Expr, guides: list[bool], is_last: bool) -> str:
    res = __line(guides, is_last, "ExprStmt:")
    guides.append(not is_last)
    res += __export_expr(stmt, guides, True)
    guides.pop()
    return res


def __export_block(block: AST.Block, guides: list[bool]) -> str:
    return __export_items_with_handler(block.stmts, guides, __export_stmt)


def __export_expr(expr: AST.Expr, guides: list[bool], is_last: bool) -> str:
    match expr:
        case AST.Binary():
            return __export_binary(expr, guides, is_last)
        case AST.Unary():
            return __export_unary(expr, guides, is_last)
        case AST.Call():
            return __export_call(expr, guides, is_last)
        case AST.MethodCall():
            return __export_method_call(expr, guides, is_last)
        case AST.FieldAccess():
            return __export_field_access(expr, guides, is_last)
        case AST.DynValue():
            return __export_dyn_value(expr, guides, is_last)
        case AST.DynBuffer():
            return __export_dyn_buffer(expr, guides, is_last)
        case AST.TypeItem():
            return __export_type_item(expr, guides, is_last)
        case AST.Identifier():
            return __export_identifier(expr, guides, is_last)
        case AST.Literal():
            return __export_literal(expr, guides, is_last)
        case AST.Tuple():
            return __export_tuple(expr, guides, is_last)
        case AST.Array():
            return __export_array(expr, guides, is_last)
        case AST.Block():
            return __export_stmt_block(expr, guides, is_last)
        case AST.If():
            return __export_if(expr, guides, is_last)
        case AST.Loop():
            return __export_loop(expr, guides, is_last)
        case AST.Match():
            return __export_match(expr, guides, is_last)
        case AST.VarDecl():
            return __export_var_decl(expr, guides, is_last)
        case AST.Return():
            return __export_return(expr, guides, is_last)
        case AST.Break():
            return __export_break(expr, guides, is_last)
        case AST.Continue():
            return __export_continue(expr, guides, is_last)
        case AST.Semi():
            return __export_stmt(expr.expr, guides, is_last)
        case _:
            raise TypeError(f"Unsupported expression: {type(expr)!r}")


def __export_binary(expr: AST.Binary, guides: list[bool], is_last: bool) -> str:
    res = __line(guides, is_last, f"BinaryOp: {expr.op}")
    res += __export_expr_child("Left", expr.left, guides, is_last, False)
    res += __export_expr_child("Right", expr.right, guides, is_last, True)
    return res


def __export_unary(expr: AST.Unary, guides: list[bool], is_last: bool) -> str:
    res = __line(guides, is_last, f"UnaryOp: {expr.op}")
    res += __export_expr_child("Operand", expr.operand, guides, is_last, True)
    return res


def __export_call(expr: AST.Call, guides: list[bool], is_last: bool) -> str:
    res = __line(guides, is_last, "Call")
    res += __export_expr_child("Callee", expr.callee, guides, is_last, False)
    if expr.args:
        child_guides = guides + [not is_last]
        res += __line(child_guides, True, "Args:")
        res += __export_items_with_handler(expr.args, child_guides + [False], __export_arg)
    else:
        child_guides = guides + [not is_last]
        res += __line(child_guides, True, "Args: []")
    return res


def __export_method_call(expr: AST.MethodCall, guides: list[bool], is_last: bool) -> str:
    res = __line(guides, is_last, f"MethodCall: {expr.method_name.name}")
    res += __export_expr_child("Receiver", expr.receiver, guides, is_last, False)
    if expr.generics:
        child_guides = guides + [not is_last]
        res += __line(child_guides, False if expr.args else True, "Generics:")
        res += __export_type_list(expr.generics, child_guides + [expr.args != []])
    if expr.args:
        child_guides = guides + [not is_last]
        res += __line(child_guides, True, "Args:")
        res += __export_items_with_handler(expr.args, child_guides + [False], __export_arg)
    elif not expr.generics:
        child_guides = guides + [not is_last]
        res += __line(child_guides, True, "Args: []")
    return res


def __export_field_access(expr: AST.FieldAccess, guides: list[bool], is_last: bool) -> str:
    res = __line(guides, is_last, f"FieldAccess: {expr.field_name.name}")
    res += __export_expr_child("Receiver", expr.receiver, guides, is_last, True)
    return res


def __export_dyn_value(expr: AST.DynValue, guides: list[bool], is_last: bool) -> str:
    res = __line(guides, is_last, "DynValue")
    res += __export_expr_child("Value", expr.value, guides, is_last, True)
    return res


def __export_dyn_buffer(expr: AST.DynBuffer, guides: list[bool], is_last: bool) -> str:
    res = __line(guides, is_last, "DynBuffer")
    res += __line(guides, False, f"TargetType: {expr.target_type}")
    res += __export_expr_child("Size", expr.size, guides, is_last, True)
    return res


def __export_type_item(expr: AST.TypeItem, guides: list[bool], is_last: bool) -> str:
    return __line(guides, is_last, f"TypeItem: {expr}")


def __export_identifier(expr: AST.Identifier, guides: list[bool], is_last: bool) -> str:
    return __line(guides, is_last, f"Identifier: {expr.name}")


def __export_literal(expr: AST.Literal, guides: list[bool], is_last: bool) -> str:
    return __line(guides, is_last, f"Literal: {expr.literal}")


def __export_tuple(expr: AST.Tuple, guides: list[bool], is_last: bool) -> str:
    res = __line(guides, is_last, "Tuple")
    if expr.elements:
        res += __line(guides, True, "Elements:")
        guides.append(False)
        res += __export_items_with_handler(expr.elements, guides, __export_expr)
        guides.pop()
    else:
        res += __line(guides, True, "Elements: []")
    return res


def __export_array(expr: AST.Array, guides: list[bool], is_last: bool) -> str:
    res = __line(guides, is_last, "Array")
    guides.append(not is_last)
    if expr.elements:
        res += __line(guides, True, "Elements:")
        guides.append(False)
        res += __export_items_with_handler(expr.elements, guides, __export_expr)
        guides.pop()
    else:
        res += __line(guides, True, "Elements: []")
    guides.pop()
    return res


def __export_arg(arg: AST.Arg, guides: list[bool], is_last: bool) -> str:
    return __line(guides, is_last, f"{arg}")


def __export_trait_items(items: list[AST.TraitItem], guides: list[bool]) -> str:
    res = ""
    for idx, item in enumerate(items):
        if isinstance(item, AST.MethodDef):
            res += __export_method_def(item, guides, idx == len(items) - 1)
        else:
            res += __export_method_decl(item, guides, idx == len(items) - 1)
    return res


def __export_program(program: AST.Program) -> str:
    return "Program\n" + __export_items_with_handler(program.items, [], __export_program_item)


def export_program(program: AST.Program) -> str:
    return __export_program(program)
