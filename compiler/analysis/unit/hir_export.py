from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING

from compiler.analysis.unit import hir as HIR
from compiler.frontend.lex.position import SrcSpan
from compiler.utils.tree_format import TreeFormatter

if TYPE_CHECKING:
    from compiler.analysis.symbol.context import SymbolCtx
    from compiler.analysis.ty.context import TypeCtx
    from compiler.analysis.unit.def_point import DefPoint
    from compiler.analysis.unit.unit_data import UnitData


class HirTreeFormatter(TreeFormatter):
    """Format HIR bodies and their surrounding symbol and type tables."""

    def __init__(self, type_ctx: TypeCtx | None) -> None:
        self.__type_ctx = type_ctx

    def __format_type(self, type_id: int) -> str:
        if self.__type_ctx is None:
            return f"type_id={type_id}"
        return self.__type_ctx.get_name(type_id)

    def __format_span(self, span: SrcSpan) -> str:
        return str(span)

    def __export_expr_child(self, label: str, child: HIR.Expr, guides: list[bool], parent_is_last: bool, is_last: bool) -> str:
        return self.render_child(label, child, guides, parent_is_last, is_last, self.__export_expr)

    def __export_block_child(self, label: str, child: HIR.Block, guides: list[bool], parent_is_last: bool, is_last: bool) -> str:
        return self.render_child(
            label, child, guides, parent_is_last, is_last,
            lambda block, child_guides, _: self.__export_block(block, child_guides),
        )

    def __export_expr_items(self, label: str, items: list[HIR.Expr], guides: list[bool], parent_is_last: bool, is_last: bool) -> str:
        child_guides = guides + [not parent_is_last]
        res = self.render_line(child_guides, is_last, f"{label}:")
        res += self.render_items(items, child_guides + [not is_last], lambda item, g, last: self.__export_expr(item, g, last))
        return res

    def __export_stmt_list(self, stmts: list[HIR.Expr], guides: list[bool]) -> str:
        return self.render_items(stmts, guides, lambda stmt, g, last: self.__export_expr(stmt, g, last))

    def __export_block(self, block: HIR.Block, guides: list[bool]) -> str:
        return self.__export_block_node(block, guides, True)

    def __export_block_node(self, block: HIR.Block, guides: list[bool], is_last: bool) -> str:
        res = self.render_line(guides, is_last, f"Block: span={self.__format_span(block.span)} type_id={block.type_id}")
        if block.stmts:
            res += self.__export_stmt_list(block.stmts, guides + [False])
        else:
            res += self.render_line(guides, True, "Stmts: []")
        return res

    def __export_expr(self, expr: HIR.Expr, guides: list[bool], is_last: bool) -> str:
        match expr:
            # --- control flow / statement-like ---
            case HIR.Block():
                return self.__export_block_node(expr, guides, is_last)
            case HIR.Return():
                return self.__export_return(expr, guides, is_last)
            case HIR.If():
                return self.__export_if(expr, guides, is_last)
            case HIR.Loop():
                return self.__export_loop(expr, guides, is_last)
            case HIR.Break():
                val_str = f" value={repr(expr.value)}" if expr.value is not None else ""
                return self.render_line(guides, is_last, f"Break: span={self.__format_span(expr.span)} type_id={expr.type_id}{val_str}")
            case HIR.Continue():
                return self.render_line(guides, is_last, f"Continue: span={self.__format_span(expr.span)} type_id={expr.type_id}")
            case HIR.Defer():
                res = self.render_line(guides, is_last, f"Defer: span={self.__format_span(expr.span)} type_id={expr.type_id}")
                res += self.__export_expr_child("Action", expr.action, guides, is_last, True)
                return res
            case HIR.Builtin():
                return self.__export_builtin(expr, guides, is_last)
            case HIR.Delete():
                res = self.render_line(guides, is_last, f"Delete: span={self.__format_span(expr.span)}")
                res += self.__export_expr_child("Target", expr.target, guides, is_last, True)
                return res
            case HIR.Match():
                return self.__export_match(expr, guides, is_last)
            case HIR.Semi():
                res = self.render_line(guides, is_last, f"Semi: span={self.__format_span(expr.span)} type_id={expr.type_id}")
                res += self.__export_expr_child("Expr", expr.expr, guides, is_last, True)
                return res
            case HIR.Let():
                res = self.render_line(guides, is_last, f"Let: span={self.__format_span(expr.span)} type_id={expr.type_id}")
                if expr.init is not None:
                    res += self.__export_expr_child("Init", expr.init, guides, is_last, True)
                return res
            case HIR.PatternLet():
                res = self.render_line(guides, is_last, f"PatternLet: pattern={expr.pattern!r} span={self.__format_span(expr.span)}")
                res += self.__export_expr_child("Value", expr.value, guides, is_last, expr.else_branch is None)
                if expr.else_branch is not None:
                    res += self.__export_block_child("Else", expr.else_branch, guides, is_last, True)
                return res
            # --- pure expressions ---
            case HIR.Binary():
                return self.__export_binary(expr, guides, is_last)
            case HIR.Unary():
                return self.__export_unary(expr, guides, is_last)
            case HIR.Call():
                return self.__export_call(expr, guides, is_last)
            case HIR.StructConstruct():
                return self.__export_struct_construct(expr, guides, is_last)
            case HIR.Invoke():
                return self.__export_invoke(expr, guides, is_last)
            case HIR.BitCast():
                res = self.render_line(guides, is_last, f"BitCast: target_type={self.__format_type(expr.target_type)} type={self.__format_type(expr.type_id)} place={expr.is_place} span={self.__format_span(expr.span)}")
                res += self.__export_expr_child("Value", expr.value, guides, is_last, True)
                return res
            case HIR.TraitObjectCoerce():
                res = self.render_line(guides, is_last, f"TraitObjectCoerce: concrete={self.__format_type(expr.concrete_type_id)} trait={self.__format_type(expr.trait_type_id)} type={self.__format_type(expr.type_id)} span={self.__format_span(expr.span)}")
                res += self.__export_expr_child("Value", expr.value, guides, is_last, True)
                return res
            case HIR.Cast():
                return self.__export_cast(expr, guides, is_last)
            case HIR.MethodCall():
                return self.__export_method_call(expr, guides, is_last)
            case HIR.TraitObjectMethodCall():
                res = self.render_line(guides, is_last, f"TraitObjectMethodCall: method_id={expr.method_id} slot={expr.slot_index} trait={self.__format_type(expr.trait_type_id)} type={self.__format_type(expr.type_id)} span={self.__format_span(expr.span)}")
                res += self.__export_expr_child("Receiver", expr.receiver, guides, is_last, False)
                res += self.__export_expr_items("Args", expr.args, guides, is_last, True)
                return res
            case HIR.VariantConstruct():
                return self.__export_variant_construct(expr, guides, is_last)
            case HIR.FieldAccess():
                return self.__export_field_access(expr, guides, is_last)
            case HIR.TupleAccess():
                return self.__export_tuple_access(expr, guides, is_last)
            case HIR.ArrayAccess():
                return self.__export_array_access(expr, guides, is_last)
            case HIR.SliceAccess():
                return self.__export_slice_access(expr, guides, is_last)
            case HIR.DynValue():
                return self.__export_dyn_value(expr, guides, is_last)
            case HIR.DynBuffer():
                return self.__export_dyn_buffer(expr, guides, is_last)
            case HIR.Closure():
                res = self.render_line(guides, is_last, f"Closure: type={self.__format_type(expr.type_id)} captures={list(expr.captures.keys())} span={self.__format_span(expr.span)}")
                return res
            case HIR.Tuple():
                return self.__export_tuple(expr, guides, is_last)
            case HIR.Array():
                return self.__export_array(expr, guides, is_last)
            case HIR.ArrayRepeat():
                return self.__export_array_repeat(expr, guides, is_last)
            case HIR.Var():
                return self.render_line(guides, is_last, f"Var: symbol_id={expr.symbol_id} type={self.__format_type(expr.type_id)} place={expr.is_place} span={self.__format_span(expr.span)}")
            case HIR.IntLiteral():
                return self.render_line(guides, is_last, f"IntLiteral: {expr.value} type={self.__format_type(expr.type_id)} place={expr.is_place} span={self.__format_span(expr.span)}")
            case HIR.FloatLiteral():
                return self.render_line(guides, is_last, f"FloatLiteral: {expr.value} type={self.__format_type(expr.type_id)} place={expr.is_place} span={self.__format_span(expr.span)}")
            case HIR.CharLiteral():
                return self.render_line(guides, is_last, f"CharLiteral: {expr.value!r} type={self.__format_type(expr.type_id)} place={expr.is_place} span={self.__format_span(expr.span)}")
            case HIR.StrLiteral():
                return self.render_line(guides, is_last, f"StrLiteral: {expr.value!r} type={self.__format_type(expr.type_id)} place={expr.is_place} span={self.__format_span(expr.span)}")
            case HIR.BoolLiteral():
                return self.render_line(guides, is_last, f"BoolLiteral: {expr.value} type={self.__format_type(expr.type_id)} place={expr.is_place} span={self.__format_span(expr.span)}")
            case HIR.Ty():
                return self.render_line(guides, is_last, f"Ty: {self.__format_type(expr.type_id)} place={expr.is_place} span={self.__format_span(expr.span)}")

    def __export_return(self, stmt: HIR.Return, guides: list[bool], is_last: bool) -> str:
        res = self.render_line(guides, is_last, f"Return: span={self.__format_span(stmt.span)}")
        if stmt.value is not None:
            res += self.__export_expr_child("Value", stmt.value, guides, is_last, True)
        return res

    def __export_if(self, stmt: HIR.If, guides: list[bool], is_last: bool) -> str:
        res = self.render_line(guides, is_last, f"If: span={self.__format_span(stmt.span)}")
        res += self.__export_expr_child("Condition", stmt.cond, guides, is_last, False)
        has_else = stmt.else_branch is not None
        res += self.__export_block_child("Then", stmt.then_branch, guides, is_last, not has_else)
        if stmt.else_branch is not None:
            res += self.__export_block_child("Else", stmt.else_branch, guides, is_last, True)
        return res

    def __export_loop(self, stmt: HIR.Loop, guides: list[bool], is_last: bool) -> str:
        res = self.render_line(guides, is_last, f"Loop: span={self.__format_span(stmt.span)}")
        res += self.__export_block_child("Body", stmt.body, guides, is_last, True)
        return res

    def __export_match_arm(self, arm: HIR.MatchArm, guides: list[bool], is_last: bool) -> str:
        res = self.render_line(guides, is_last, f"MatchArm: pattern={arm.pattern!r}")
        if arm.guard is not None:
            res += self.__export_expr_child("Guard", arm.guard, guides, is_last, False)
        res += self.__export_block_child("Body", arm.body, guides, is_last, True)
        return res

    def __export_match(self, stmt: HIR.Match, guides: list[bool], is_last: bool) -> str:
        res = self.render_line(guides, is_last, f"NewMatch: span={self.__format_span(stmt.span)}")
        res += self.__export_expr_child("Value", stmt.value, guides, is_last, False)
        child_guides = guides + [not is_last]
        res += self.render_line(child_guides, True, "Arms:")
        res += self.render_items(stmt.arms, child_guides + [False], lambda arm, g, last: self.__export_match_arm(arm, g, last))
        return res

    def __export_binary(self, expr: HIR.Binary, guides: list[bool], is_last: bool) -> str:
        res = self.render_line(guides, is_last, f"Binary: op={expr.op} type={self.__format_type(expr.type_id)} place={expr.is_place} span={self.__format_span(expr.span)}")
        res += self.__export_expr_child("Left", expr.left, guides, is_last, False)
        res += self.__export_expr_child("Right", expr.right, guides, is_last, True)
        return res

    def __export_unary(self, expr: HIR.Unary, guides: list[bool], is_last: bool) -> str:
        res = self.render_line(guides, is_last, f"Unary: op={expr.op} type={self.__format_type(expr.type_id)} place={expr.is_place} span={self.__format_span(expr.span)}")
        res += self.__export_expr_child("Operand", expr.operand, guides, is_last, True)
        return res

    def __export_call(self, expr: HIR.Call, guides: list[bool], is_last: bool) -> str:
        res = self.render_line(guides, is_last, f"Call: func={expr.func} type={self.__format_type(expr.type_id)} place={expr.is_place} span={self.__format_span(expr.span)}")
        res += self.__export_expr_items("Args", expr.args, guides, is_last, True)
        return res

    def __export_struct_construct(self, expr: HIR.StructConstruct, guides: list[bool], is_last: bool) -> str:
        res = self.render_line(guides, is_last, f"StructConstruct: struct_id={expr.struct_id} type={self.__format_type(expr.type_id)} place={expr.is_place} span={self.__format_span(expr.span)}")
        if expr.field_values:
            child_guides = guides + [not is_last]
            res += self.render_line(child_guides, True, "Fields:")
            items = list(expr.field_values.items())
            res += self.render_items(
                items,
                child_guides + [False],
                lambda item, g, last: self.render_line(g, last, f"{item[0]}:") + self.__export_expr(item[1], g + [not last], True),
            )
        else:
            res += self.render_line(guides, True, "Fields: []")
        return res

    def __export_invoke(self, expr: HIR.Invoke, guides: list[bool], is_last: bool) -> str:
        res = self.render_line(guides, is_last, f"Invoke: type={self.__format_type(expr.type_id)} place={expr.is_place} span={self.__format_span(expr.span)}")
        res += self.__export_expr_child("Callable", expr.callable, guides, is_last, False)
        res += self.__export_expr_items("Args", expr.args, guides, is_last, True)
        return res

    def __export_cast(self, expr: HIR.Cast, guides: list[bool], is_last: bool) -> str:
        res = self.render_line(guides, is_last, f"Cast: target_type={self.__format_type(expr.target_type)} type={self.__format_type(expr.type_id)} place={expr.is_place} span={self.__format_span(expr.span)}")
        res += self.__export_expr_child("Value", expr.value, guides, is_last, True)
        return res

    def __export_method_call(self, expr: HIR.MethodCall, guides: list[bool], is_last: bool) -> str:
        res = self.render_line(guides, is_last, f"MethodCall: method_id={expr.method_id} type={self.__format_type(expr.type_id)} place={expr.is_place} span={self.__format_span(expr.span)}")
        res += self.__export_expr_child("Receiver", expr.receiver, guides, is_last, False)
        res += self.__export_expr_items("Args", expr.args, guides, is_last, True)
        return res

    def __export_variant_construct(self, expr: HIR.VariantConstruct, guides: list[bool], is_last: bool) -> str:
        res = self.render_line(guides, is_last, f"VariantConstruct: variant={expr.variant.name} type={self.__format_type(expr.type_id)} place={expr.is_place} span={self.__format_span(expr.span)}")
        if expr.args:
            child_guides = guides + [not is_last]
            res += self.render_line(child_guides, True, "Args:")
            items = list(expr.args.items())
            res += self.render_items(
                items,
                child_guides + [False],
                lambda item, g, last: self.render_line(g, last, f"{item[0]}:") + self.__export_expr(item[1], g + [not last], True),
            )
        else:
            res += self.render_line(guides, True, "Args: []")
        return res

    def __export_field_access(self, expr: HIR.FieldAccess, guides: list[bool], is_last: bool) -> str:
        res = self.render_line(guides, is_last, f"FieldAccess: field={expr.field.name} type={self.__format_type(expr.type_id)} place={expr.is_place} span={self.__format_span(expr.span)}")
        res += self.__export_expr_child("Receiver", expr.receiver, guides, is_last, True)
        return res

    def __export_tuple_access(self, expr: HIR.TupleAccess, guides: list[bool], is_last: bool) -> str:
        res = self.render_line(guides, is_last, f"TupleAccess: index={expr.index} type={self.__format_type(expr.type_id)} place={expr.is_place} span={self.__format_span(expr.span)}")
        res += self.__export_expr_child("Receiver", expr.receiver, guides, is_last, True)
        return res

    def __export_array_access(self, expr: HIR.ArrayAccess, guides: list[bool], is_last: bool) -> str:
        res = self.render_line(guides, is_last, f"ArrayAccess: length={expr.length} element_type={self.__format_type(expr.element_type)} place={expr.is_place} span={self.__format_span(expr.span)}")
        res += self.__export_expr_child("Array", expr.array, guides, is_last, False)
        res += self.__export_expr_child("Index", expr.index, guides, is_last, True)
        return res

    def __export_slice_access(self, expr: HIR.SliceAccess, guides: list[bool], is_last: bool) -> str:
        res = self.render_line(guides, is_last, f"SliceAccess: element_type={self.__format_type(expr.element_type)} place={expr.is_place} span={self.__format_span(expr.span)}")
        res += self.__export_expr_child("Slice", expr.slice, guides, is_last, False)
        res += self.__export_expr_child("Index", expr.index, guides, is_last, True)
        return res

    def __export_dyn_value(self, expr: HIR.DynValue, guides: list[bool], is_last: bool) -> str:
        res = self.render_line(guides, is_last, f"DynValue: type={self.__format_type(expr.type_id)} place={expr.is_place} span={self.__format_span(expr.span)}")
        res += self.__export_expr_child("Value", expr.value, guides, is_last, True)
        return res

    def __export_dyn_buffer(self, expr: HIR.DynBuffer, guides: list[bool], is_last: bool) -> str:
        res = self.render_line(guides, is_last, f"DynBuffer: element_type={self.__format_type(expr.element_type)} type={self.__format_type(expr.type_id)} place={expr.is_place} span={self.__format_span(expr.span)}")
        if expr.element is None:
            res += self.__export_expr_child("Length", expr.length, guides, is_last, True)
            return res
        res += self.__export_expr_child("Length", expr.length, guides, False, False)
        res += self.__export_expr_child("Element", expr.element, guides, is_last, True)
        return res

    def __export_builtin(self, expr: HIR.Builtin, guides: list[bool], is_last: bool) -> str:
        type_args = ", ".join(self.__format_type(type_id) for type_id in expr.type_args)
        type_suffix = f"<{type_args}>" if expr.type_args else ""
        res = self.render_line(
            guides,
            is_last,
            f"Builtin: {expr.kind.spelling}{type_suffix} type={self.__format_type(expr.type_id)} "
            f"place={expr.is_place} span={self.__format_span(expr.span)}",
        )
        for index, arg in enumerate(expr.args):
            res += self.__export_expr_child(f"Arg {index}", arg, guides, is_last, index == len(expr.args) - 1)
        return res

    def __export_tuple(self, expr: HIR.Tuple, guides: list[bool], is_last: bool) -> str:
        res = self.render_line(guides, is_last, f"Tuple: type={self.__format_type(expr.type_id)} place={expr.is_place} span={self.__format_span(expr.span)}")
        if expr.field_values:
            res += self.__export_expr_items("Elements", expr.field_values, guides, is_last, True)
        else:
            res += self.render_line(guides, True, "Elements: []")
        return res

    def __export_array(self, expr: HIR.Array, guides: list[bool], is_last: bool) -> str:
        res = self.render_line(guides, is_last, f"Array: element_type={self.__format_type(expr.element_type)} type={self.__format_type(expr.type_id)} place={expr.is_place} span={self.__format_span(expr.span)}")
        if expr.elements:
            res += self.__export_expr_items("Elements", expr.elements, guides, is_last, True)
        else:
            res += self.render_line(guides, True, "Elements: []")
        return res

    def __export_array_repeat(self, expr: HIR.ArrayRepeat, guides: list[bool], is_last: bool) -> str:
        res = self.render_line(guides, is_last, f"ArrayRepeat: element_type={self.__format_type(expr.element_type)} type={self.__format_type(expr.type_id)} place={expr.is_place} span={self.__format_span(expr.span)}")
        res += self.__export_expr_items("Element", [expr.element], guides, is_last, True)
        return res

    def export_block(self, block: HIR.Block) -> str:
        return self.__export_block_node(block, [], True)

    def export_def_point(self, def_point: DefPoint) -> str:
        type_id = getattr(def_point, "type_id", -1)
        unit_id = getattr(def_point, "unit_id", -1)
        locals_list = getattr(def_point, "locals", [])
        params_list = getattr(def_point, "params", [])
        body = getattr(def_point, "body", None)

        res = "DefPoint\n"
        res += self.render_line([], True, f"Header: type={self.__format_type(type_id)} unit_id={unit_id} locals={locals_list} params={params_list}")
        if body is not None:
            res += self.render_line([], True, "Body:")
            res += self.__export_block_node(body, [False], True)
        else:
            res += self.render_line([], True, "Body: None")
        return res

    def __export_type_space(self) -> str:
        if self.__type_ctx is None:
            raise TypeError("the HIR bundle requires a type context")
        res = "TypeSpace\n"
        type_items = sorted(self.__type_ctx.items(), key=lambda item: item[0])
        for idx, (type_id, ty) in enumerate(type_items):
            type_name = self.__type_ctx.get_name(type_id)
            is_last = idx == len(type_items) - 1
            res += self.render_line([], is_last, f"Type: id={type_id} name={type_name} raw={ty}")
        return res

    def __export_symbol_ctx(self, symbol_ctx: SymbolCtx, title: str) -> str:
        symbol_items = sorted(symbol_ctx.items(), key=lambda item: item[0])
        res = f"{title}\n"
        if not symbol_items:
            res += self.render_line([], True, "Symbols: []")
            return res

        for idx, (symbol_id, symbol) in enumerate(symbol_items):
            is_last = idx == len(symbol_items) - 1
            attrs = "[" + ", ".join(attr.value for attr in sorted(symbol.attributes, key=lambda attr: attr.value)) + "]"
            res += self.render_line([], is_last, f"Symbol: id={symbol_id} name={symbol.name} kind={symbol.kind.value} type={symbol.type_id} attrs={attrs}")
        return res

    def export_hir_bundle(self, unit_datas: Mapping[int, UnitData], def_points: Mapping[int, DefPoint]) -> str:
        sections: list[str] = []

        sections.append(self.__export_type_space().rstrip())

        symbol_sections: list[str] = []
        for _, unit in sorted(unit_datas.items(), key=lambda item: item[0]):
            symbol_sections.append(self.__export_symbol_ctx(unit.symbol_ctx, f"Symbols for {unit.path}").rstrip())
        if symbol_sections:
            sections.append("\n".join(symbol_sections))

        def_point_sections: list[str] = []
        for def_point in sorted(def_points.values(), key=lambda item: (getattr(item, "unit_id", -1), getattr(item, "type_id", -1))):
            src_file = unit_datas[getattr(def_point, "unit_id", -1)].path
            def_point_sections.append(f"HIR for {src_file}:\n{self.export_def_point(def_point).rstrip()}")
        if def_point_sections:
            sections.append("\n\n".join(def_point_sections))

        return "\n\n".join(sections) + ("\n" if sections else "")


def export_block(block: HIR.Block, type_ctx: TypeCtx | None = None) -> str:
    return HirTreeFormatter(type_ctx).export_block(block)


def export_def_point(def_point: DefPoint, type_ctx: TypeCtx | None = None) -> str:
    return HirTreeFormatter(type_ctx).export_def_point(def_point)


def export_hir_bundle(
    unit_datas: Mapping[int, UnitData],
    def_points: Mapping[int, DefPoint],
    type_ctx: TypeCtx,
) -> str:
    return HirTreeFormatter(type_ctx).export_hir_bundle(unit_datas, def_points)
