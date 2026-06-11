from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Callable, TypeVar

from compiler.analysis.unit import hir as HIR

if TYPE_CHECKING:
    from compiler.analysis.symbol.context import SymbolCtx
    from compiler.analysis.ty.context import TypeCtx
    from compiler.analysis.unit.unit_data import UnitData

ItemType = TypeVar("ItemType")


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


def __export_items_with_handler(items: list[ItemType], guides: list[bool], handler: Callable[[ItemType, list[bool], bool], str]) -> str:
    res = ""
    for idx, item in enumerate(items):
        res += handler(item, guides, idx == len(items) - 1)
    return res


def __format_type(type_ctx: TypeCtx | None, type_id: int) -> str:
    if type_ctx is None:
        return f"type_id={type_id}"
    return type_ctx.get_name(type_id)


def __format_span(span: object) -> str:
    return str(span)


def __export_expr_child(label: str, child: HIR.Expr, guides: list[bool], parent_is_last: bool, is_last: bool, type_ctx: TypeCtx | None) -> str:
    child_guides = guides + [not parent_is_last]
    res = __line(child_guides, is_last, f"{label}:")
    res += __export_expr(child, child_guides + [not is_last], True, type_ctx)
    return res


def __export_block_child(label: str, child: HIR.Block, guides: list[bool], parent_is_last: bool, is_last: bool, type_ctx: TypeCtx | None) -> str:
    child_guides = guides + [not parent_is_last]
    res = __line(child_guides, is_last, f"{label}:")
    res += __export_block(child, child_guides + [not is_last], type_ctx)
    return res


def __export_expr_items(label: str, items: list[HIR.Expr], guides: list[bool], parent_is_last: bool, is_last: bool, type_ctx: TypeCtx | None) -> str:
    child_guides = guides + [not parent_is_last]
    res = __line(child_guides, is_last, f"{label}:")
    res += __export_items_with_handler(items, child_guides + [not is_last], lambda item, g, last: __export_expr(item, g, last, type_ctx))
    return res


def __export_stmt_list(stmts: list[HIR.Stmt], guides: list[bool], type_ctx: TypeCtx | None) -> str:
    return __export_items_with_handler(stmts, guides, lambda stmt, g, last: __export_stmt(stmt, g, last, type_ctx))


def __export_block(block: HIR.Block, guides: list[bool], type_ctx: TypeCtx | None) -> str:
    return __export_block_node(block, guides, True, type_ctx)


def __export_block_node(block: HIR.Block, guides: list[bool], is_last: bool, type_ctx: TypeCtx | None) -> str:
    res = __line(guides, is_last, f"Block: span={__format_span(block.span)}")
    if block.stmts:
        res += __export_stmt_list(block.stmts, guides + [False], type_ctx)
    else:
        res += __line(guides, True, "Stmts: []")
    return res


def __export_stmt(stmt: HIR.Stmt, guides: list[bool], is_last: bool, type_ctx: TypeCtx | None) -> str:
    match stmt:
        case HIR.Block():
            return __export_block_node(stmt, guides, is_last, type_ctx)
        case HIR.Return():
            return __export_return(stmt, guides, is_last, type_ctx)
        case HIR.If():
            return __export_if(stmt, guides, is_last, type_ctx)
        case HIR.Loop():
            return __export_loop(stmt, guides, is_last, type_ctx)
        case HIR.Break():
            return __line(guides, is_last, f"Break: span={__format_span(stmt.span)}")
        case HIR.Continue():
            return __line(guides, is_last, f"Continue: span={__format_span(stmt.span)}")
        case HIR.Panic():
            res = __line(guides, is_last, f"Panic: span={__format_span(stmt.span)}")
            res += __export_expr_child("Message", stmt.message, guides, is_last, True, type_ctx)
            return res
        case HIR.Delete():
            res = __line(guides, is_last, f"Delete: span={__format_span(stmt.span)}")
            res += __export_expr_child("Target", stmt.target, guides, is_last, True, type_ctx)
            return res
        case HIR.Match():
            return __export_match(stmt, guides, is_last, type_ctx)
        case HIR.SysWrite():
            return __export_sys_write(stmt, guides, is_last, type_ctx)
        case _:
            return __export_expr_stmt(stmt, guides, is_last, type_ctx)


def __export_return(stmt: HIR.Return, guides: list[bool], is_last: bool, type_ctx: TypeCtx | None) -> str:
    res = __line(guides, is_last, f"Return: span={__format_span(stmt.span)}")
    if stmt.value is not None:
        res += __export_expr_child("Value", stmt.value, guides, is_last, True, type_ctx)
    return res


def __export_if(stmt: HIR.If, guides: list[bool], is_last: bool, type_ctx: TypeCtx | None) -> str:
    res = __line(guides, is_last, f"If: span={__format_span(stmt.span)}")
    res += __export_expr_child("Condition", stmt.cond, guides, is_last, False, type_ctx)
    has_else = stmt.else_branch is not None
    res += __export_block_child("Then", stmt.then_branch, guides, is_last, not has_else, type_ctx)
    if stmt.else_branch is not None:
        res += __export_block_child("Else", stmt.else_branch, guides, is_last, True, type_ctx)
    return res


def __export_loop(stmt: HIR.Loop, guides: list[bool], is_last: bool, type_ctx: TypeCtx | None) -> str:
    res = __line(guides, is_last, f"Loop: span={__format_span(stmt.span)}")
    res += __export_block_child("Body", stmt.body, guides, is_last, True, type_ctx)
    return res


def __export_match_arm(arm: HIR.MatchArm, guides: list[bool], is_last: bool, type_ctx: TypeCtx | None) -> str:
    if arm.pattern is None:
        header = "NewMatchArm: _"
    elif isinstance(arm.pattern, HIR.IntPattern):
        header = f"NewMatchArm: int={arm.pattern.value} type_id={arm.pattern.type_id}"
    elif isinstance(arm.pattern, HIR.CharPattern):
        header = f"NewMatchArm: char={arm.pattern.value!r}"
    else:
        unpacked = arm.pattern.unpack_fields if arm.pattern.unpack_fields is not None else []
        header = f"NewMatchArm: variant={arm.pattern.variant.name} unpack_fields={unpacked}"
    res = __line(guides, is_last, header)
    res += __export_block_child("Body", arm.body, guides, is_last, True, type_ctx)
    return res


def __export_match(stmt: HIR.Match, guides: list[bool], is_last: bool, type_ctx: TypeCtx | None) -> str:
    res = __line(guides, is_last, f"NewMatch: span={__format_span(stmt.span)}")
    res += __export_expr_child("Value", stmt.value, guides, is_last, False, type_ctx)
    child_guides = guides + [not is_last]
    res += __line(child_guides, True, "Arms:")
    res += __export_items_with_handler(stmt.arms, child_guides + [False], lambda arm, g, last: __export_match_arm(arm, g, last, type_ctx))
    return res


def __export_expr_stmt(stmt: HIR.Expr, guides: list[bool], is_last: bool, type_ctx: TypeCtx | None) -> str:
    res = __line(guides, is_last, f"ExprStmt: span={__format_span(stmt.span)}")
    res += __export_expr(stmt, guides + [not is_last], True, type_ctx)
    return res


def __export_expr(expr: HIR.Expr, guides: list[bool], is_last: bool, type_ctx: TypeCtx | None) -> str:
    match expr:
        case HIR.Binary():
            return __export_binary(expr, guides, is_last, type_ctx)
        case HIR.Unary():
            return __export_unary(expr, guides, is_last, type_ctx)
        case HIR.Call():
            return __export_call(expr, guides, is_last, type_ctx)
        case HIR.StructConstruct():
            return __export_struct_construct(expr, guides, is_last, type_ctx)
        case HIR.Invoke():
            return __export_invoke(expr, guides, is_last, type_ctx)
        case HIR.Cast():
            return __export_cast(expr, guides, is_last, type_ctx)
        case HIR.MethodCall():
            return __export_method_call(expr, guides, is_last, type_ctx)
        case HIR.VariantConstruct():
            return __export_variant_construct(expr, guides, is_last, type_ctx)
        case HIR.FieldAccess():
            return __export_field_access(expr, guides, is_last, type_ctx)
        case HIR.TupleAccess():
            return __export_tuple_access(expr, guides, is_last, type_ctx)
        case HIR.DynValue():
            return __export_dyn_value(expr, guides, is_last, type_ctx)
        case HIR.DynBuffer():
            return __export_dyn_buffer(expr, guides, is_last, type_ctx)
        case HIR.SizeOf():
            return __export_size_of(expr, guides, is_last, type_ctx)
        case HIR.BitCast():
            return __export_bit_cast(expr, guides, is_last, type_ctx)
        case HIR.SysRead():
            return __export_sys_read(expr, guides, is_last, type_ctx)
        case HIR.Tuple():
            return __export_tuple(expr, guides, is_last, type_ctx)
        case HIR.Array():
            return __export_array(expr, guides, is_last, type_ctx)
        case HIR.Var():
            return __line(guides, is_last, f"Var: symbol_id={expr.symbol_id} type={__format_type(type_ctx, expr.type_id)} place={expr.is_place} span={__format_span(expr.span)}")
        case HIR.IntLiteral():
            return __line(guides, is_last, f"IntLiteral: {expr.value} type={__format_type(type_ctx, expr.type_id)} place={expr.is_place} span={__format_span(expr.span)}")
        case HIR.FloatLiteral():
            return __line(guides, is_last, f"FloatLiteral: {expr.value} type={__format_type(type_ctx, expr.type_id)} place={expr.is_place} span={__format_span(expr.span)}")
        case HIR.CharLiteral():
            return __line(guides, is_last, f"CharLiteral: {expr.value!r} type={__format_type(type_ctx, expr.type_id)} place={expr.is_place} span={__format_span(expr.span)}")
        case HIR.StrLiteral():
            return __line(guides, is_last, f"StrLiteral: {expr.value!r} type={__format_type(type_ctx, expr.type_id)} place={expr.is_place} span={__format_span(expr.span)}")
        case HIR.BoolLiteral():
            return __line(guides, is_last, f"BoolLiteral: {expr.value} type={__format_type(type_ctx, expr.type_id)} place={expr.is_place} span={__format_span(expr.span)}")
        case HIR.Ty():
            return __line(guides, is_last, f"Ty: {__format_type(type_ctx, expr.type_id)} place={expr.is_place} span={__format_span(expr.span)}")


def __export_binary(expr: HIR.Binary, guides: list[bool], is_last: bool, type_ctx: TypeCtx | None) -> str:
    res = __line(guides, is_last, f"Binary: op={expr.op} type={__format_type(type_ctx, expr.type_id)} place={expr.is_place} span={__format_span(expr.span)}")
    res += __export_expr_child("Left", expr.left, guides, is_last, False, type_ctx)
    res += __export_expr_child("Right", expr.right, guides, is_last, True, type_ctx)
    return res


def __export_unary(expr: HIR.Unary, guides: list[bool], is_last: bool, type_ctx: TypeCtx | None) -> str:
    res = __line(guides, is_last, f"Unary: op={expr.op} type={__format_type(type_ctx, expr.type_id)} place={expr.is_place} span={__format_span(expr.span)}")
    res += __export_expr_child("Operand", expr.operand, guides, is_last, True, type_ctx)
    return res


def __export_call(expr: HIR.Call, guides: list[bool], is_last: bool, type_ctx: TypeCtx | None) -> str:
    res = __line(guides, is_last, f"Call: func={expr.func} type={__format_type(type_ctx, expr.type_id)} place={expr.is_place} span={__format_span(expr.span)}")
    res += __export_expr_items("Args", expr.args, guides, is_last, True, type_ctx)
    return res


def __export_struct_construct(expr: HIR.StructConstruct, guides: list[bool], is_last: bool, type_ctx: TypeCtx | None) -> str:
    res = __line(guides, is_last, f"StructConstruct: struct_id={expr.struct_id} type={__format_type(type_ctx, expr.type_id)} place={expr.is_place} span={__format_span(expr.span)}")
    if expr.field_values:
        child_guides = guides + [not is_last]
        res += __line(child_guides, True, "Fields:")
        items = list(expr.field_values.items())
        res += __export_items_with_handler(
            items,
            child_guides + [False],
            lambda item, g, last: __line(g, last, f"{item[0]}:") + __export_expr(item[1], g + [not last], True, type_ctx),
        )
    else:
        res += __line(guides, True, "Fields: []")
    return res


def __export_invoke(expr: HIR.Invoke, guides: list[bool], is_last: bool, type_ctx: TypeCtx | None) -> str:
    res = __line(guides, is_last, f"Invoke: type={__format_type(type_ctx, expr.type_id)} place={expr.is_place} span={__format_span(expr.span)}")
    res += __export_expr_child("Callable", expr.callable, guides, is_last, False, type_ctx)
    res += __export_expr_items("Args", expr.args, guides, is_last, True, type_ctx)
    return res


def __export_cast(expr: HIR.Cast, guides: list[bool], is_last: bool, type_ctx: TypeCtx | None) -> str:
    res = __line(guides, is_last, f"Cast: target_type={__format_type(type_ctx, expr.target_type)} type={__format_type(type_ctx, expr.type_id)} place={expr.is_place} span={__format_span(expr.span)}")
    res += __export_expr_child("Value", expr.value, guides, is_last, True, type_ctx)
    return res


def __export_method_call(expr: HIR.MethodCall, guides: list[bool], is_last: bool, type_ctx: TypeCtx | None) -> str:
    res = __line(guides, is_last, f"MethodCall: method_id={expr.method_id} type={__format_type(type_ctx, expr.type_id)} place={expr.is_place} span={__format_span(expr.span)}")
    res += __export_expr_child("Receiver", expr.receiver, guides, is_last, False, type_ctx)
    res += __export_expr_items("Args", expr.args, guides, is_last, True, type_ctx)
    return res


def __export_variant_construct(expr: HIR.VariantConstruct, guides: list[bool], is_last: bool, type_ctx: TypeCtx | None) -> str:
    res = __line(guides, is_last, f"VariantConstruct: variant={expr.variant.name} type={__format_type(type_ctx, expr.type_id)} place={expr.is_place} span={__format_span(expr.span)}")
    if expr.args:
        child_guides = guides + [not is_last]
        res += __line(child_guides, True, "Args:")
        items = list(expr.args.items())
        res += __export_items_with_handler(
            items,
            child_guides + [False],
            lambda item, g, last: __line(g, last, f"{item[0]}:") + __export_expr(item[1], g + [not last], True, type_ctx),
        )
    else:
        res += __line(guides, True, "Args: []")
    return res


def __export_field_access(expr: HIR.FieldAccess, guides: list[bool], is_last: bool, type_ctx: TypeCtx | None) -> str:
    res = __line(guides, is_last, f"FieldAccess: field={expr.field.name} type={__format_type(type_ctx, expr.type_id)} place={expr.is_place} span={__format_span(expr.span)}")
    res += __export_expr_child("Receiver", expr.receiver, guides, is_last, True, type_ctx)
    return res


def __export_tuple_access(expr: HIR.TupleAccess, guides: list[bool], is_last: bool, type_ctx: TypeCtx | None) -> str:
    res = __line(guides, is_last, f"TupleAccess: index={expr.index} type={__format_type(type_ctx, expr.type_id)} place={expr.is_place} span={__format_span(expr.span)}")
    res += __export_expr_child("Receiver", expr.receiver, guides, is_last, True, type_ctx)
    return res


def __export_dyn_value(expr: HIR.DynValue, guides: list[bool], is_last: bool, type_ctx: TypeCtx | None) -> str:
    res = __line(guides, is_last, f"DynValue: type={__format_type(type_ctx, expr.type_id)} place={expr.is_place} span={__format_span(expr.span)}")
    res += __export_expr_child("Value", expr.value, guides, is_last, True, type_ctx)
    return res


def __export_dyn_buffer(expr: HIR.DynBuffer, guides: list[bool], is_last: bool, type_ctx: TypeCtx | None) -> str:
    res = __line(guides, is_last, f"DynBuffer: element_type={__format_type(type_ctx, expr.element_type)} type={__format_type(type_ctx, expr.type_id)} place={expr.is_place} span={__format_span(expr.span)}")
    res += __export_expr_child("Length", expr.length, guides, is_last, True, type_ctx)
    return res


def __export_size_of(expr: HIR.SizeOf, guides: list[bool], is_last: bool, type_ctx: TypeCtx | None) -> str:
    return __line(guides, is_last, f"SizeOf: target_type={__format_type(type_ctx, expr.target_type)} type={__format_type(type_ctx, expr.type_id)} place={expr.is_place} span={__format_span(expr.span)}")


def __export_bit_cast(expr: HIR.BitCast, guides: list[bool], is_last: bool, type_ctx: TypeCtx | None) -> str:
    res = __line(guides, is_last, f"BitCast: target_type={__format_type(type_ctx, expr.target_type)} type={__format_type(type_ctx, expr.type_id)} place={expr.is_place} span={__format_span(expr.span)}")
    res += __export_expr_child("Value", expr.value, guides, is_last, True, type_ctx)
    return res


def __export_sys_read(expr: HIR.SysRead, guides: list[bool], is_last: bool, type_ctx: TypeCtx | None) -> str:
    res = __line(guides, is_last, f"SysRead: type={__format_type(type_ctx, expr.type_id)} place={expr.is_place} span={__format_span(expr.span)}")
    res += __export_expr_child("Fd", expr.fd, guides, is_last, False, type_ctx)
    res += __export_expr_child("Buf", expr.buf, guides, is_last, True, type_ctx)
    return res


def __export_sys_write(expr: HIR.SysWrite, guides: list[bool], is_last: bool, type_ctx: TypeCtx | None) -> str:
    res = __line(guides, is_last, f"SysWrite: type={__format_type(type_ctx, expr.type_id)} place={expr.is_place} span={__format_span(expr.span)}")
    res += __export_expr_child("Fd", expr.fd, guides, is_last, False, type_ctx)
    res += __export_expr_child("Buf", expr.buf, guides, is_last, True, type_ctx)
    return res


def __export_tuple(expr: HIR.Tuple, guides: list[bool], is_last: bool, type_ctx: TypeCtx | None) -> str:
    res = __line(guides, is_last, f"Tuple: type={__format_type(type_ctx, expr.type_id)} place={expr.is_place} span={__format_span(expr.span)}")
    if expr.field_values:
        res += __export_expr_items("Elements", expr.field_values, guides, is_last, True, type_ctx)
    else:
        res += __line(guides, True, "Elements: []")
    return res


def __export_array(expr: HIR.Array, guides: list[bool], is_last: bool, type_ctx: TypeCtx | None) -> str:
    res = __line(guides, is_last, f"Array: element_type={__format_type(type_ctx, expr.element_type)} type={__format_type(type_ctx, expr.type_id)} place={expr.is_place} span={__format_span(expr.span)}")
    if expr.elements:
        res += __export_expr_items("Elements", expr.elements, guides, is_last, True, type_ctx)
    else:
        res += __line(guides, True, "Elements: []")
    return res


def export_block(block: HIR.Block, type_ctx: TypeCtx | None = None) -> str:
    return __export_block_node(block, [], True, type_ctx)


def export_def_point(def_point: object, type_ctx: TypeCtx | None = None) -> str:
    type_id = getattr(def_point, "type_id", -1)
    unit_id = getattr(def_point, "unit_id", -1)
    locals_list = getattr(def_point, "locals", [])
    params_list = getattr(def_point, "params", [])
    body = getattr(def_point, "body", None)

    res = "DefPoint\n"
    res += __line([], True, f"Header: type={__format_type(type_ctx, type_id)} unit_id={unit_id} locals={locals_list} params={params_list}")
    if body is not None:
        res += __line([], True, "Body:")
        res += __export_block_node(body, [False], True, type_ctx)
    else:
        res += __line([], True, "Body: None")
    return res


def __export_type_space(type_ctx: TypeCtx) -> str:
    res = "TypeSpace\n"
    type_items = sorted(type_ctx.items(), key=lambda item: item[0])
    for idx, (type_id, ty) in enumerate(type_items):
        type_name = type_ctx.get_name(type_id)
        is_last = idx == len(type_items) - 1
        res += __line([], is_last, f"Type: id={type_id} name={type_name} raw={ty}")
    return res


def __export_symbol_ctx(symbol_ctx: SymbolCtx, title: str) -> str:
    symbol_items = sorted(symbol_ctx.items(), key=lambda item: item[0])
    res = f"{title}\n"
    if not symbol_items:
        res += __line([], True, "Symbols: []")
        return res

    for idx, (symbol_id, symbol) in enumerate(symbol_items):
        is_last = idx == len(symbol_items) - 1
        attrs = "[" + ", ".join(attr.value for attr in sorted(symbol.attributes, key=lambda attr: attr.value)) + "]"
        res += __line([], is_last, f"Symbol: id={symbol_id} name={symbol.name} kind={symbol.kind.value} type={symbol.type_id} attrs={attrs}")
    return res


def export_hir_bundle(unit_datas: Mapping[int, UnitData], def_points: Mapping[int, object], type_ctx: TypeCtx) -> str:
    sections: list[str] = []

    sections.append(__export_type_space(type_ctx).rstrip())

    symbol_sections: list[str] = []
    for _, unit in sorted(unit_datas.items(), key=lambda item: item[0]):
        symbol_sections.append(__export_symbol_ctx(unit.symbol_ctx, f"Symbols for {unit.path}").rstrip())
    if symbol_sections:
        sections.append("\n".join(symbol_sections))

    def_point_sections: list[str] = []
    for def_point in sorted(def_points.values(), key=lambda item: (getattr(item, "unit_id", -1), getattr(item, "type_id", -1))):
        src_file = unit_datas[getattr(def_point, "unit_id", -1)].path
        def_point_sections.append(f"HIR for {src_file}:\n{export_def_point(def_point, type_ctx).rstrip()}")
    if def_point_sections:
        sections.append("\n\n".join(def_point_sections))

    return "\n\n".join(sections) + ("\n" if sections else "")
