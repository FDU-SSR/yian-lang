"""Ordered formatted-output lowering shared by templates and formatting builtins.

The recovery compiler expands a template into ordinary HIR: method calls on the
standard library formatter and nested ``match`` expressions over each write
result.  Literal and interpolation evaluation therefore follows the template
order, and a failed write reaches only its own error arm.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING

from yianc.analysis.error import AnalysisError
from yianc.analysis.lowering.state import DefinitionState
from yianc.analysis.symbol.context import SymbolCtx
from yianc.analysis.symbol.symbol import AliasSymbol, Symbol
from yianc.analysis.ty import ty as Type
from yianc.analysis.ty.context import TypeCtx
from yianc.analysis.ty.intrinsics import IntrinsicIds
from yianc.analysis.unit import hir as HIR
from yianc.analysis.unit.unit_data import UnitData
from yianc.builtins import BuiltinKind
from yianc.error import CompilerError
from yianc.frontend.lex import token as Tok
from yianc.frontend.lex.position import SrcSpan
from yianc.frontend.parse import ast as AST
from yianc.frontend.parse.operator import BinaryOperator, UnaryOperator

if TYPE_CHECKING:
    from yianc.analysis.lowering.expr_checker import ExprChecker


class FormatDestination(Enum):
    """Outer owner of one formatted template."""

    OWNED_STRING = "owned-string"
    APPEND = "append"
    WRITER = "writer"
    STDOUT = "stdout"
    STDERR = "stderr"


class FormatFailure(Enum):
    """Where a write failure goes."""

    PROPAGATE = "propagate"
    PANIC = "panic"


@dataclass(frozen=True)
class _FormatLibrary:
    formatter_type: int
    format_trait: int
    string_type: int
    io_error_type: int
    unit_type: int
    result_type: int
    formatter_for: int
    string_writer: int
    stdout_printer: int
    stderr_printer: int


class FormatLowering:
    """Build formatted output HIR for one recovery type-checking session."""

    def __init__(self, ctx: DefinitionState, expr: ExprChecker) -> None:
        self.__ctx = ctx
        self.__expr = expr
        self.__library_value: _FormatLibrary | None = None
        self.__serial = 0

    @property
    def __library(self) -> _FormatLibrary:
        if self.__library_value is None:
            self.__library_value = self.__load_library()
        return self.__library_value

    # ------------------------------------------------------------------
    # standard library identity
    # ------------------------------------------------------------------

    def __stdlib_unit(self, relative: str) -> UnitData:
        root = self.__ctx.stdlib_root
        if root is None:
            raise CompilerError("standard library root is unavailable during formatting lowering")
        target = (root / relative).resolve()
        for unit in self.__ctx.unit_datas.values():
            if unit.path.resolve() == target:
                return unit
        raise CompilerError(f"standard library module '{relative}' is absent from the compilation inputs")

    def __unit_symbol(self, relative: str, name: str) -> Symbol:
        unit = self.__stdlib_unit(relative)
        symbol = unit.symbol_ctx.lookup_global(name)
        if not isinstance(symbol, Symbol):
            raise CompilerError(f"standard library symbol '{name}' is missing from {relative}")
        return symbol

    def __load_library(self) -> _FormatLibrary:
        ctx = self.__ctx.type_ctx
        formatter = self.__unit_symbol("core/fmt.an", "Formatter")
        format_trait = self.__unit_symbol("core/fmt.an", "Format")
        string = self.__unit_symbol("core/string.an", "String")
        io_error = self.__unit_symbol("core/io.an", "IoError")
        result = self.__unit_symbol("core/result.an", "Result")
        formatter_for = self.__unit_symbol("core/fmt.an", "formatter_for")
        string_writer = self.__unit_symbol("core/fmt.an", "string_writer")
        stdout_printer = self.__unit_symbol("core/io.an", "stdout_printer")
        stderr_printer = self.__unit_symbol("core/io.an", "stderr_printer")
        unit_type = ctx.alloc_tuple([])
        result_type = ctx.alloc_instance(result.type_id, [unit_type, io_error.type_id])
        return _FormatLibrary(
            formatter_type=formatter.type_id,
            format_trait=format_trait.type_id,
            string_type=string.type_id,
            io_error_type=io_error.type_id,
            unit_type=unit_type,
            result_type=result_type,
            formatter_for=formatter_for.type_id,
            string_writer=string_writer.type_id,
            stdout_printer=stdout_printer.type_id,
            stderr_printer=stderr_printer.type_id,
        )

    # ------------------------------------------------------------------
    # HIR helpers
    # ------------------------------------------------------------------

    def __hidden(self, span: SrcSpan, role: str) -> str:
        self.__serial += 1
        return f"%fmt_{span.start.row}_{span.start.col}_{self.__serial}_{role}"

    def __declare(self, span: SrcSpan, role: str, init: HIR.Expr) -> tuple[HIR.Let, int]:
        name = self.__hidden(span, role)
        identifier = AST.Identifier(span=span, name=name, synthetic=True)
        symbol_id = self.__ctx.declare_local(identifier, init.type_id)
        target = HIR.Var(span=span, symbol_id=symbol_id, type_id=init.type_id, is_place=True)
        # A declaration stores through an assignment, matching source `let` lowering.
        statement = self.__expr.assign(span, target, init)
        let = HIR.Let(span=span, init=statement, type_id=TypeCtx.void_id, is_place=False, symbol_id=symbol_id)
        return let, symbol_id

    def __var(self, span: SrcSpan, symbol_id: int, type_id: int) -> HIR.Var:
        return HIR.Var(span=span, symbol_id=symbol_id, type_id=type_id, is_place=True)

    def __address(self, span: SrcSpan, value: HIR.Expr) -> HIR.Unary:
        # Explicit borrows carry the pointer shape; argument coercion re-labels them as references.
        pointer = self.__ctx.type_ctx.alloc_pointer(value.type_id)
        return HIR.Unary(span=span, op=UnaryOperator.AddrOf, operand=value, type_id=pointer, is_place=False)

    def __block(self, span: SrcSpan, statements: list[HIR.Expr], type_id: int) -> HIR.Block:
        return HIR.Block(span=span, stmts=statements, type_id=type_id, is_place=False)

    def __str_literal(self, span: SrcSpan, text: str) -> HIR.StrLiteral:
        return HIR.StrLiteral(span=span, value=text, type_id=IntrinsicIds.str_id, is_place=False)

    def __void(self, span: SrcSpan) -> HIR.Block:
        return HIR.Block(span=span, stmts=[], type_id=TypeCtx.void_id, is_place=False)

    def __call(self, span: SrcSpan, function: int, arguments: list[HIR.Expr]) -> HIR.Call:
        ctx = self.__ctx.type_ctx
        parameters = ctx.get_params(function)
        if len(parameters) != len(arguments):
            raise CompilerError("standard library formatting helper has an unexpected signature")
        coerced = [self.__expr.coerce(argument, parameter.type_id) for argument, parameter in zip(arguments, parameters)]
        result = ctx.get_return_type(function)
        self.__ctx.report_def(function)
        return HIR.Call(span=span, func=function, args=coerced, type_id=result, is_place=False)

    def __string_new(self, span: SrcSpan) -> HIR.Expr:
        receiver = HIR.Ty(span=span, type_id=self.__library.string_type, is_place=False)
        return self.__expr.call_method(receiver, "new", None, [])

    def __drop(self, span: SrcSpan, target: HIR.Expr) -> HIR.Expr:
        return self.__expr.call_method(target, "drop", None, [])

    def __write_str(self, span: SrcSpan, receiver: HIR.Expr, text: str) -> HIR.Expr:
        return self.__expr.call_method(receiver, "write_str", None, [self.__str_literal(span, text)])

    def __write_value(self, span: SrcSpan, receiver: HIR.Expr, value: HIR.Expr) -> HIR.Expr:
        return self.__expr.call_method(receiver, "write_value", None, [self.__address(span, value)])

    def __panicking(self, span: SrcSpan, error: HIR.Expr) -> HIR.Expr:
        message = self.__expr.call_method(error, "msg", None, [])
        return HIR.Builtin(span=span, kind=BuiltinKind.Panic, type_args=[], args=[message], type_id=TypeCtx.never_id, is_place=False)

    def __result_variant(self, span: SrcSpan, name: str, value: HIR.Expr) -> HIR.VariantConstruct:
        ctx = self.__ctx.type_ctx
        enum_ty = ctx[self.__library.result_type]
        if not isinstance(enum_ty, Type.EnumType):
            raise CompilerError("standard Result is not an enum")
        variant = next((candidate for candidate in enum_ty.get_variants(ctx) if candidate.name == name), None)
        if variant is None or variant.payload_type is None:
            raise CompilerError(f"standard Result variant '{name}' is missing")
        fields = ctx.get_struct_fields(variant.payload_type)
        if len(fields) != 1:
            raise CompilerError("standard Result variant has an unexpected payload")
        field = fields[0]
        coerced = self.__expr.coerce(value, field.type_id)
        return HIR.VariantConstruct(
            span=span,
            enum_id=self.__library.result_type,
            variant=variant,
            args={field.name: coerced},
            type_id=self.__library.result_type,
            is_place=False,
        )

    # ------------------------------------------------------------------
    # patterns and ordered matches
    # ------------------------------------------------------------------

    def __ok_pattern(self, span: SrcSpan) -> HIR.Pattern:
        pattern = AST.ConstructPattern(
            span=span,
            name=AST.Identifier(span=span, name="Ok", synthetic=True),
            qualifier=None,
            positional=[AST.WildcardPattern(span=span)],
            named=None,
        )
        return self.__expr.check_format_pattern(pattern, self.__library.result_type)

    def __error_pattern(self, span: SrcSpan, name: str) -> HIR.Pattern:
        pattern = AST.ConstructPattern(
            span=span,
            name=AST.Identifier(span=span, name="Err", synthetic=True),
            qualifier=None,
            positional=[AST.NamePattern(span=span, name=AST.Identifier(span=span, name=name, synthetic=True))],
            named=None,
        )
        return self.__expr.check_format_pattern(pattern, self.__library.result_type)

    def __match(self, span: SrcSpan, write: HIR.Expr, next_expr: HIR.Expr,
                result_type: int, failure: FormatFailure) -> HIR.Match:
        ok = self.__ok_pattern(span)
        ok_body = self.__expr.coerce(next_expr, result_type)
        name = self.__hidden(span, "error")
        self.__ctx.enter_scope()
        try:
            failed = self.__error_pattern(span, name)
            symbol_ctx = self.__ctx.symbol_ctx
            assert symbol_ctx is not None
            symbol = symbol_ctx.lookup(name)
            if not isinstance(symbol, Symbol):
                raise CompilerError("format error binding is missing")
            held = self.__var(span, symbol.symbol_id, self.__library.io_error_type)
            error_body = (self.__panicking(span, held) if failure is FormatFailure.PANIC
                          else self.__result_variant(span, "Err", held))
            error_body = self.__expr.coerce(error_body, result_type)
        finally:
            self.__ctx.exit_scope()
        arms = [
            HIR.MatchArm(span=span, pattern=ok, guard=None,
                         body=self.__block(span, [ok_body], result_type),
                         origin=HIR.MatchArmOrigin.SYNTHETIC),
            HIR.MatchArm(span=span, pattern=failed, guard=None,
                         body=self.__block(span, [error_body], result_type),
                         origin=HIR.MatchArmOrigin.SYNTHETIC),
        ]
        return HIR.Match(span=span, value=write, arms=arms, type_id=result_type,
                         is_place=False, is_ref=False)

    def __chain(self, receiver: HIR.Expr, parts: list[AST.FormatPart], index: int,
                tail: HIR.Expr, tail_type: int, failure: FormatFailure) -> HIR.Expr:
        if index >= len(parts):
            return tail
        part = parts[index]
        if isinstance(part, AST.FormatLiteralPart):
            write = self.__write_str(part.span, receiver, part.text)
            span = part.span
        else:
            value = self.__expr.value(part.expr)
            defaulted = self.__ctx.type_ctx.default_literals(value.type_id)
            value = self.__expr.coerce(value, defaulted)
            if value.type_id == TypeCtx.never_id:
                # A non-returning interpolation neither formats a value nor reaches later positions.
                return self.__block(span=part.span, statements=[value], type_id=TypeCtx.never_id)
            write = self.__write_value(part.span, receiver, value)
            span = part.span
        next_expr = self.__chain(receiver, parts, index + 1, tail, tail_type, failure)
        return self.__match(span, write, next_expr, tail_type, failure)

    # ------------------------------------------------------------------
    # destinations
    # ------------------------------------------------------------------

    def __owned_string(self, span: SrcSpan, parts: list[AST.FormatPart]) -> HIR.Expr:
        library = self.__library
        statements: list[HIR.Expr] = []
        owned = self.__declare(span, "value", self.__string_new(span))
        statements.append(owned[0])
        statements.append(HIR.Defer(span=span, action=self.__drop(span, self.__var(span, owned[1], library.string_type)),
                                    type_id=TypeCtx.void_id, is_place=False))
        writer_value = self.__call(span, library.string_writer, [self.__address(span, self.__var(span, owned[1], library.string_type))])
        writer = self.__declare(span, "writer", writer_value)
        statements.append(writer[0])
        formatter_value = self.__call(span, library.formatter_for, [self.__address(span, self.__var(span, writer[1], writer_value.type_id))])
        formatter = self.__declare(span, "formatter", formatter_value)
        statements.append(formatter[0])
        receiver = self.__var(span, formatter[1], formatter_value.type_id)
        statements.append(self.__chain(receiver, parts, 0, self.__void(span), TypeCtx.void_id, FormatFailure.PANIC))
        result = self.__declare(span, "result", self.__var(span, owned[1], library.string_type))
        statements.append(result[0])
        replacement = self.__string_new(span)
        statements.append(HIR.Binary(span=span, op=BinaryOperator.Assign,
                                     left=self.__var(span, owned[1], library.string_type),
                                     right=self.__expr.coerce(replacement, library.string_type),
                                     type_id=TypeCtx.void_id, is_place=False))
        statements.append(self.__var(span, result[1], library.string_type))
        return self.__block(span, statements, library.string_type)

    def __borrowed_inner(self, type_id: int) -> int | None:
        ty = self.__ctx.type_ctx[type_id]
        if isinstance(ty, (Type.RefType, Type.PointerType)):
            return ty.pointee_type
        return None

    def __append(self, span: SrcSpan, target: AST.Expr, parts: list[AST.FormatPart]) -> HIR.Expr:
        library = self.__library
        value = self.__expr.value(target)
        if self.__borrowed_inner(value.type_id) != library.string_type:
            raise AnalysisError("'@append' requires a 'String&' target", target.span)
        statements: list[HIR.Expr] = []
        target_value = self.__declare(span, "append_target", value)
        statements.append(target_value[0])
        writer_value = self.__call(span, library.string_writer, [self.__var(span, target_value[1], value.type_id)])
        writer = self.__declare(span, "append_writer", writer_value)
        statements.append(writer[0])
        formatter_value = self.__call(span, library.formatter_for, [self.__address(span, self.__var(span, writer[1], writer_value.type_id))])
        formatter = self.__declare(span, "append_formatter", formatter_value)
        statements.append(formatter[0])
        receiver = self.__var(span, formatter[1], formatter_value.type_id)
        statements.append(self.__chain(receiver, parts, 0, self.__void(span), TypeCtx.void_id, FormatFailure.PANIC))
        return self.__block(span, statements, TypeCtx.void_id)

    def __writer(self, span: SrcSpan, target: AST.Expr, parts: list[AST.FormatPart]) -> HIR.Expr:
        library = self.__library
        value = self.__expr.value(target)
        inner = self.__borrowed_inner(value.type_id)
        if inner is None:
            raise AnalysisError("'@write' requires a borrowed target", target.span)
        statements: list[HIR.Expr] = []
        target_value = self.__declare(span, "write_target", value)
        statements.append(target_value[0])
        receiver_expr: HIR.Expr
        if inner == library.formatter_type:
            receiver_expr = self.__var(span, target_value[1], value.type_id)
        else:
            formatter_value = self.__call(span, library.formatter_for, [self.__var(span, target_value[1], value.type_id)])
            formatter = self.__declare(span, "write_formatter", formatter_value)
            statements.append(formatter[0])
            receiver_expr = self.__var(span, formatter[1], formatter_value.type_id)
        tail = self.__result_variant(span, "Ok", HIR.Tuple(span=span, field_values=[], type_id=library.unit_type, is_place=False))
        statements.append(self.__chain(receiver_expr, parts, 0, tail, library.result_type, FormatFailure.PROPAGATE))
        return self.__block(span, statements, library.result_type)

    def __printer(self, span: SrcSpan, parts: list[AST.FormatPart], standard_output: bool, newline: bool) -> HIR.Expr:
        library = self.__library
        statements: list[HIR.Expr] = []
        factory = library.stdout_printer if standard_output else library.stderr_printer
        target_value = self.__call(span, factory, [])
        target = self.__declare(span, "print_target", target_value)
        statements.append(target[0])
        formatter_value = self.__call(span, library.formatter_for, [self.__address(span, self.__var(span, target[1], target_value.type_id))])
        formatter = self.__declare(span, "print_formatter", formatter_value)
        statements.append(formatter[0])
        receiver = self.__var(span, formatter[1], formatter_value.type_id)
        finish = self.__expr.call_method(self.__var(span, target[1], target_value.type_id), "finish", None, [])
        tail = self.__match(span, finish, self.__void(span), TypeCtx.void_id, FormatFailure.PANIC)
        if newline:
            tail = self.__match(span, self.__write_str(span, receiver, "\n"), tail, TypeCtx.void_id, FormatFailure.PANIC)
        statements.append(self.__chain(receiver, parts, 0, tail, TypeCtx.void_id, FormatFailure.PANIC))
        return self.__block(span, statements, TypeCtx.void_id)

    # ------------------------------------------------------------------
    # public entry points
    # ------------------------------------------------------------------

    def lower_template(self, span: SrcSpan, destination: FormatDestination, parts: list[AST.FormatPart]) -> HIR.Expr:
        if destination is FormatDestination.OWNED_STRING:
            return self.__owned_string(span, parts)
        raise CompilerError("template destination must own a string")

    def lower_builtin(self, node: AST.Builtin) -> HIR.Expr:
        kind = node.kind
        expected = 2 if kind in (BuiltinKind.Append, BuiltinKind.Write) else 1
        if node.type_args:
            raise AnalysisError(f"'{kind.spelling}' does not take type arguments", node.span)
        if len(node.args) != expected:
            label = "argument" if expected == 1 else "arguments"
            raise AnalysisError(f"'{kind.spelling}' expects exactly {expected} {label}, got {len(node.args)}", node.span)
        if any(argument.name is not None for argument in node.args):
            raise AnalysisError(f"named arguments are not supported for '{kind.spelling}'", node.span)
        if kind.is_print_entry:
            template = node.args[0].value
            standard_output = kind in (BuiltinKind.Print, BuiltinKind.Println)
            newline = kind in (BuiltinKind.Println, BuiltinKind.Eprintln)
            return self.__printer(node.span, self.__template_parts(template), standard_output, newline)
        target = node.args[0].value
        template = node.args[1].value
        parts = self.__template_parts(template)
        if kind is BuiltinKind.Append:
            return self.__append(node.span, target, parts)
        if kind is BuiltinKind.Write:
            return self.__writer(node.span, target, parts)
        raise CompilerError(f"unregistered formatting builtin '{kind.spelling}'")

    def __template_parts(self, template: AST.Expr) -> list[AST.FormatPart]:
        if isinstance(template, AST.FormatTemplate):
            return template.parts
        if isinstance(template, AST.Literal) and isinstance(template.literal, Tok.StrLiteral):
            return [AST.FormatLiteralPart(span=template.span, text=template.literal.value)]
        raise AnalysisError("format template must be an f-string or string literal", template.span)
