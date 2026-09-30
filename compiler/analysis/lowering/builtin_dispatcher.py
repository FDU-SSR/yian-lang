from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Callable

from compiler.analysis.error import AnalysisError
from compiler.analysis.lowering.expr_evaluator import ExprEvaluator
from compiler.analysis.ty import ty as Type
from compiler.analysis.unit import hir as HIR
from compiler.builtins import BuiltinKind
from compiler.error import CompilerError
from compiler.frontend.lex.position import SrcSpan
from compiler.frontend.lex import token as Tok
from compiler.frontend.parse import ast as AST
from compiler.runtime_error import parse_runtime_error_code

if TYPE_CHECKING:
    from compiler.analysis.lowering.state import DefinitionState


@dataclass(frozen=True)
class _BuiltinSpec:
    type_arg_count: int
    value_arg_count: int
    lower: Callable[[AST.Builtin, list[int]], HIR.Builtin]


# Built-in instruction names live exclusively in the ``@`` namespace.  The
# AST parser resolves the spelling to ``AST.BuiltinKind`` before lowering, so
# ordinary user-defined functions with the same bare names are never
# intercepted here.
class BuiltinDispatcher:
    def __init__(self, ctx: DefinitionState, expr: ExprEvaluator):
        self.__ctx = ctx
        self.__expr = expr
        self.__builtin_specs: dict[BuiltinKind, _BuiltinSpec] = {
            BuiltinKind.SizeOf: _BuiltinSpec(1, 0, self.__lower_size_of),
            BuiltinKind.Undef: _BuiltinSpec(1, 0, self.__lower_undef),
            BuiltinKind.Dangling: _BuiltinSpec(1, 0, self.__lower_dangling),
            BuiltinKind.BitCast: _BuiltinSpec(1, 1, self.__lower_bit_cast),
            BuiltinKind.Alloc: _BuiltinSpec(1, 1, self.__lower_alloc),
            BuiltinKind.Realloc: _BuiltinSpec(1, 2, self.__lower_realloc),
            BuiltinKind.Panic: _BuiltinSpec(0, 1, self.__lower_panic),
            BuiltinKind.RuntimeFail: _BuiltinSpec(0, 1, self.__lower_runtime_fail),
            BuiltinKind.AssumeInit: _BuiltinSpec(0, 1, self.__lower_assume_init),
            BuiltinKind.MemCopy: _BuiltinSpec(0, 3, self.__lower_mem_copy),
            BuiltinKind.SliceFromParts: _BuiltinSpec(0, 2, self.__lower_slice_from_parts),
            BuiltinKind.SliceGetPtr: _BuiltinSpec(0, 1, self.__lower_slice_get_ptr),
            BuiltinKind.SliceGetLen: _BuiltinSpec(0, 1, self.__lower_slice_get_len),
            BuiltinKind.StrFromParts: _BuiltinSpec(0, 2, self.__lower_str_from_parts),
            BuiltinKind.StrGetPtr: _BuiltinSpec(0, 1, self.__lower_str_get_ptr),
            BuiltinKind.StrGetLen: _BuiltinSpec(0, 1, self.__lower_str_get_len),
            BuiltinKind.SysRead: _BuiltinSpec(0, 2, self.__lower_sys_read),
            BuiltinKind.SysReadBytes: _BuiltinSpec(0, 2, self.__lower_sys_read_bytes),
            BuiltinKind.SysWrite: _BuiltinSpec(0, 2, self.__lower_sys_write),
            BuiltinKind.SysWriteBytes: _BuiltinSpec(0, 2, self.__lower_sys_write_bytes),
            BuiltinKind.Open: _BuiltinSpec(0, 2, self.__lower_open),
            BuiltinKind.Close: _BuiltinSpec(0, 1, self.__lower_close),
            BuiltinKind.Sqrt: _BuiltinSpec(0, 1, self.__lower_sqrt),
            BuiltinKind.Sin: _BuiltinSpec(0, 1, self.__lower_sin),
            BuiltinKind.Cos: _BuiltinSpec(0, 1, self.__lower_cos),
            BuiltinKind.Argc: _BuiltinSpec(0, 0, self.__lower_argc),
            BuiltinKind.ArgBytes: _BuiltinSpec(0, 1, self.__lower_arg_bytes),
            BuiltinKind.Exit: _BuiltinSpec(0, 1, self.__lower_exit),
            BuiltinKind.FfiAddr: _BuiltinSpec(0, 1, self.__lower_ffi_addr),
            BuiltinKind.FfiParts: _BuiltinSpec(0, 1, self.__lower_ffi_parts),
            BuiltinKind.FfiNull: _BuiltinSpec(1, 0, self.__lower_ffi_null),
            BuiltinKind.FfiPtrCast: _BuiltinSpec(1, 1, self.__lower_ffi_ptr_cast),
            BuiltinKind.FfiCopyFrom: _BuiltinSpec(0, 3, self.__lower_ffi_copy_from),
            BuiltinKind.FfiCopyTo: _BuiltinSpec(0, 3, self.__lower_ffi_copy_to),
        }

    def handle(self, node: AST.Builtin) -> HIR.Builtin:
        """Validate and lower a parsed instruction through the builtin registry."""
        spec = self.__builtin_specs.get(node.kind)
        if spec is None:
            raise CompilerError(f"Unregistered builtin instruction '{node.kind.spelling}'")
        if node.kind in (BuiltinKind.FfiAddr, BuiltinKind.FfiParts, BuiltinKind.FfiNull,
                         BuiltinKind.FfiPtrCast, BuiltinKind.FfiCopyFrom, BuiltinKind.FfiCopyTo):
            if not self.__ctx.ffi_allowed:
                raise AnalysisError(f"'{node.kind.spelling}' requires an ffi fn", node.span)
        if len(node.type_args) != spec.type_arg_count:
            type_arg_label = "type argument" if spec.type_arg_count == 1 else "type arguments"
            raise AnalysisError(
                f"'{node.kind.spelling}' expects exactly {spec.type_arg_count} {type_arg_label}, "
                f"got {len(node.type_args)}",
                node.span,
            )
        if len(node.args) != spec.value_arg_count:
            arg_label = "argument" if spec.value_arg_count == 1 else "arguments"
            raise AnalysisError(
                f"'{node.kind.spelling}' expects exactly {spec.value_arg_count} {arg_label}, got {len(node.args)}",
                node.span,
            )
        if self.__has_named_arg(node.args):
            raise AnalysisError(f"named arguments are not supported for '{node.kind.spelling}'", node.span)
        type_args = [self.__ctx.resolve_type(type_arg) for type_arg in node.type_args]
        return spec.lower(node, type_args)

    def __builtin(self, node: AST.Builtin, type_args: list[int], args: list[HIR.Expr], type_id: int) -> HIR.Builtin:
        return HIR.Builtin(span=node.span, kind=node.kind, type_args=type_args, args=args, type_id=type_id, is_place=False)

    def __arg(self, node: AST.Builtin, index: int) -> HIR.Expr:
        return self.__expr.value(node.args[index].value)

    def __coerced_arg(self, node: AST.Builtin, index: int, type_id: int) -> HIR.Expr:
        return self.__expr.coerce(self.__arg(node, index), type_id)

    def __ffi_element(self, type_id: int, span: SrcSpan, *, incomplete: bool = False) -> None:
        ty = self.__ctx.type_ctx[type_id]
        if isinstance(ty, Type.OpaqueType) and incomplete:
            return
        if not self.__ctx.type_ctx.is_c_abi_type(type_id):
            raise AnalysisError("FFI pointer element must have a C-compatible type", span)

    def __lower_ffi_addr(self, node: AST.Builtin, types: list[int]) -> HIR.Builtin:
        value = self.__arg(node, 0)
        ty = self.__ctx.type_ctx[value.type_id]
        if isinstance(ty, Type.PointerType):
            value = self.__expr.coerce(value, self.__ctx.type_ctx.alloc_ref(ty.pointee_type))
            ty = self.__ctx.type_ctx[value.type_id]
        if not isinstance(ty, Type.RefType):
            raise AnalysisError("@ffi_addr expects a reference", node.span)
        self.__ffi_element(ty.pointee_type, node.span)
        return self.__builtin(node, types, [value], self.__ctx.type_ctx.alloc_cptr(ty.pointee_type))

    def __lower_ffi_parts(self, node: AST.Builtin, types: list[int]) -> HIR.Builtin:
        value = self.__arg(node, 0)
        ty = self.__ctx.type_ctx[value.type_id]
        if isinstance(ty, Type.SliceType):
            element = ty.element_type
            self.__ffi_element(element, node.span)
        elif isinstance(ty, Type.StrType):
            element = self.__ctx.type_ctx.u8_id
        else:
            raise AnalysisError("@ffi_parts expects a slice or str", node.span)
        result = self.__ctx.type_ctx.alloc_tuple([self.__ctx.type_ctx.alloc_cptr(element), self.__ctx.type_ctx.u64_id])
        return self.__builtin(node, types, [value], result)

    def __lower_ffi_null(self, node: AST.Builtin, types: list[int]) -> HIR.Builtin:
        self.__ffi_element(types[0], node.span, incomplete=True)
        return self.__builtin(node, types, [], self.__ctx.type_ctx.alloc_cptr(types[0]))

    def __lower_ffi_ptr_cast(self, node: AST.Builtin, types: list[int]) -> HIR.Builtin:
        value = self.__arg(node, 0)
        source = self.__ctx.type_ctx[value.type_id]
        if not isinstance(source, Type.CPtrType):
            raise AnalysisError("@ffi_ptr_cast expects cptr<T>", node.span)
        self.__ffi_element(types[0], node.span, incomplete=True)
        return self.__builtin(node, types, [value], self.__ctx.type_ctx.alloc_cptr(types[0]))

    def __lower_ffi_copy_from(self, node: AST.Builtin, types: list[int]) -> HIR.Builtin:
        target = self.__coerced_arg(node, 0, self.__ctx.type_ctx.alloc_slice(self.__ctx.type_ctx.u8_id))
        source = self.__coerced_arg(node, 1, self.__ctx.type_ctx.alloc_cptr(self.__ctx.type_ctx.u8_id))
        length = self.__coerced_arg(node, 2, self.__ctx.type_ctx.u64_id)
        return self.__builtin(node, types, [target, source, length], self.__ctx.type_ctx.void_id)

    def __lower_ffi_copy_to(self, node: AST.Builtin, types: list[int]) -> HIR.Builtin:
        target = self.__coerced_arg(node, 0, self.__ctx.type_ctx.alloc_cptr(self.__ctx.type_ctx.u8_id))
        source = self.__coerced_arg(node, 1, self.__ctx.type_ctx.alloc_slice(self.__ctx.type_ctx.u8_id))
        length = self.__coerced_arg(node, 2, self.__ctx.type_ctx.u64_id)
        return self.__builtin(node, types, [target, source, length], self.__ctx.type_ctx.void_id)

    def __lower_size_of(self, node: AST.Builtin, types: list[int]) -> HIR.Builtin:
        return self.__builtin(node, types, [], self.__ctx.type_ctx.u64_id)

    def __lower_undef(self, node: AST.Builtin, types: list[int]) -> HIR.Builtin:
        return self.__builtin(node, types, [], types[0])

    def __lower_dangling(self, node: AST.Builtin, types: list[int]) -> HIR.Builtin:
        ptr_type = self.__ctx.type_ctx.alloc_pointer(types[0])
        return self.__builtin(node, types, [], ptr_type)

    def __lower_bit_cast(self, node: AST.Builtin, types: list[int]) -> HIR.Builtin:
        value = self.__arg(node, 0)
        source = self.__ctx.type_ctx[value.type_id]
        target = self.__ctx.type_ctx[types[0]]
        if not isinstance(source, (Type.PointerType, Type.RefType)):
            raise AnalysisError(f"'@bitcast' expects a pointer expression, got '{self.__ctx.type_ctx.get_name(value.type_id)}'", node.span)
        if not isinstance(target, Type.PointerType):
            raise AnalysisError(f"'@bitcast' target type must be a pointer type, got '{self.__ctx.type_ctx.get_name(types[0])}'", node.span)
        return self.__builtin(node, types, [value], types[0])

    def __lower_alloc(self, node: AST.Builtin, types: list[int]) -> HIR.Builtin:
        count = self.__coerced_arg(node, 0, self.__ctx.type_ctx.u64_id)
        return self.__builtin(node, types, [count], self.__ctx.type_ctx.alloc_pointer(types[0]))

    def __lower_realloc(self, node: AST.Builtin, types: list[int]) -> HIR.Builtin:
        pointer = self.__arg(node, 0)
        pointer_ty = self.__ctx.type_ctx[pointer.type_id]
        expected = self.__ctx.type_ctx.alloc_pointer(types[0])
        if not isinstance(pointer_ty, Type.PointerType) or not self.__ctx.type_ctx.is_same_type(pointer_ty.pointee_type, types[0]):
            raise AnalysisError(
                f"'@realloc' expects a '{self.__ctx.type_ctx.get_name(expected)}' pointer, "
                f"got '{self.__ctx.type_ctx.get_name(pointer.type_id)}'",
                node.args[0].value.span,
            )
        count = self.__coerced_arg(node, 1, self.__ctx.type_ctx.u64_id)
        return self.__builtin(node, types, [pointer, count], expected)

    def __lower_panic(self, node: AST.Builtin, types: list[int]) -> HIR.Builtin:
        message = self.__coerced_arg(node, 0, self.__ctx.type_ctx.str_id)
        return self.__builtin(node, types, [message], self.__ctx.type_ctx.never_id)

    def __lower_runtime_fail(self, node: AST.Builtin, types: list[int]) -> HIR.Builtin:
        arg = node.args[0].value
        if not isinstance(arg, AST.Literal) or not isinstance(arg.literal, Tok.StrLiteral):
            raise AnalysisError("'@runtime_fail' expects a runtime error code string literal", node.span)
        if parse_runtime_error_code(arg.literal.value) is None:
            raise AnalysisError(f"unknown runtime error code '{arg.literal.value}'", node.span)
        return self.__builtin(node, types, [self.__arg(node, 0)], self.__ctx.type_ctx.never_id)

    def __lower_assume_init(self, node: AST.Builtin, types: list[int]) -> HIR.Builtin:
        value = self.__arg(node, 0)
        return self.__builtin(node, types, [value], value.type_id)

    def __lower_mem_copy(self, node: AST.Builtin, types: list[int]) -> HIR.Builtin:
        dest, src = self.__arg(node, 0), self.__arg(node, 1)
        for name, value in (("dest", dest), ("src", src)):
            if not isinstance(self.__ctx.type_ctx[value.type_id], Type.PointerType):
                raise AnalysisError(f"'@memcpy' {name} must be a pointer, got '{self.__ctx.type_ctx.get_name(value.type_id)}'", node.span)
        count = self.__coerced_arg(node, 2, self.__ctx.type_ctx.u64_id)
        return self.__builtin(node, types, [dest, src, count], self.__ctx.type_ctx.void_id)

    def __lower_slice_from_parts(self, node: AST.Builtin, types: list[int]) -> HIR.Builtin:
        ptr = self.__arg(node, 0)
        ptr_ty = self.__ctx.type_ctx[ptr.type_id]
        if not isinstance(ptr_ty, Type.PointerType):
            raise AnalysisError(f"'@slice_from_parts' expects a pointer as its first argument, got '{self.__ctx.type_ctx.get_name(ptr.type_id)}'", node.span)
        size = self.__coerced_arg(node, 1, self.__ctx.type_ctx.u64_id)
        return self.__builtin(node, types, [ptr, size], self.__ctx.type_ctx.alloc_slice(ptr_ty.pointee_type))

    def __lower_slice_get_ptr(self, node: AST.Builtin, types: list[int]) -> HIR.Builtin:
        value = self.__arg(node, 0)
        ty = self.__ctx.type_ctx[value.type_id]
        if not isinstance(ty, Type.SliceType):
            raise AnalysisError(f"'@slice_get_ptr' expects a slice argument, got '{self.__ctx.type_ctx.get_name(value.type_id)}'", node.span)
        return self.__builtin(node, types, [value], self.__ctx.type_ctx.alloc_pointer(ty.element_type))

    def __lower_slice_get_len(self, node: AST.Builtin, types: list[int]) -> HIR.Builtin:
        value = self.__arg(node, 0)
        if not isinstance(self.__ctx.type_ctx[value.type_id], Type.SliceType):
            raise AnalysisError(f"'@slice_get_len' expects a slice argument, got '{self.__ctx.type_ctx.get_name(value.type_id)}'", node.span)
        return self.__builtin(node, types, [value], self.__ctx.type_ctx.u64_id)

    def __lower_str_from_parts(self, node: AST.Builtin, types: list[int]) -> HIR.Builtin:
        ptr = self.__coerced_arg(node, 0, self.__ctx.type_ctx.alloc_pointer(self.__ctx.type_ctx.u8_id))
        size = self.__coerced_arg(node, 1, self.__ctx.type_ctx.u64_id)
        return self.__builtin(node, types, [ptr, size], self.__ctx.type_ctx.str_id)

    def __lower_str_get_ptr(self, node: AST.Builtin, types: list[int]) -> HIR.Builtin:
        value = self.__arg(node, 0)
        if not isinstance(self.__ctx.type_ctx[value.type_id], Type.StrType):
            raise AnalysisError(f"'@str_get_ptr' expects a 'str' argument, got '{self.__ctx.type_ctx.get_name(value.type_id)}'", node.span)
        return self.__builtin(node, types, [value], self.__ctx.type_ctx.alloc_pointer(self.__ctx.type_ctx.u8_id))

    def __lower_str_get_len(self, node: AST.Builtin, types: list[int]) -> HIR.Builtin:
        value = self.__arg(node, 0)
        if not isinstance(self.__ctx.type_ctx[value.type_id], Type.StrType):
            raise AnalysisError(f"'@str_get_len' expects a 'str' argument, got '{self.__ctx.type_ctx.get_name(value.type_id)}'", node.span)
        return self.__builtin(node, types, [value], self.__ctx.type_ctx.u64_id)

    def __lower_sys_read(self, node: AST.Builtin, types: list[int]) -> HIR.Builtin:
        fd = self.__coerced_arg(node, 0, self.__ctx.type_ctx.i32_id)
        buf = self.__arg(node, 1)
        return self.__builtin(node, types, [fd, buf], self.__ctx.type_ctx.str_id)

    def __lower_sys_read_bytes(self, node: AST.Builtin, types: list[int]) -> HIR.Builtin:
        fd = self.__coerced_arg(node, 0, self.__ctx.type_ctx.i32_id)
        buf = self.__coerced_arg(node, 1, self.__ctx.type_ctx.alloc_slice(self.__ctx.type_ctx.u8_id))
        return self.__builtin(node, types, [fd, buf], self.__ctx.type_ctx.i64_id)

    def __lower_sys_write(self, node: AST.Builtin, types: list[int]) -> HIR.Builtin:
        fd = self.__coerced_arg(node, 0, self.__ctx.type_ctx.i32_id)
        buf = self.__coerced_arg(node, 1, self.__ctx.type_ctx.str_id)
        return self.__builtin(node, types, [fd, buf], self.__ctx.type_ctx.void_id)

    def __lower_sys_write_bytes(self, node: AST.Builtin, types: list[int]) -> HIR.Builtin:
        fd = self.__coerced_arg(node, 0, self.__ctx.type_ctx.i32_id)
        buf = self.__coerced_arg(node, 1, self.__ctx.type_ctx.alloc_slice(self.__ctx.type_ctx.u8_id))
        return self.__builtin(node, types, [fd, buf], self.__ctx.type_ctx.i64_id)

    def __lower_open(self, node: AST.Builtin, types: list[int]) -> HIR.Builtin:
        path = self.__coerced_arg(node, 0, self.__ctx.type_ctx.str_id)
        flags = self.__coerced_arg(node, 1, self.__ctx.type_ctx.i32_id)
        return self.__builtin(node, types, [path, flags], self.__ctx.type_ctx.i32_id)

    def __lower_close(self, node: AST.Builtin, types: list[int]) -> HIR.Builtin:
        fd = self.__coerced_arg(node, 0, self.__ctx.type_ctx.i32_id)
        return self.__builtin(node, types, [fd], self.__ctx.type_ctx.i32_id)

    def __lower_sqrt(self, node: AST.Builtin, types: list[int]) -> HIR.Builtin:
        value = self.__coerced_arg(node, 0, self.__ctx.type_ctx.f64_id)
        return self.__builtin(node, types, [value], self.__ctx.type_ctx.f64_id)

    def __lower_sin(self, node: AST.Builtin, types: list[int]) -> HIR.Builtin:
        return self.__lower_sqrt(node, types)

    def __lower_cos(self, node: AST.Builtin, types: list[int]) -> HIR.Builtin:
        return self.__lower_sqrt(node, types)

    def __lower_argc(self, node: AST.Builtin, types: list[int]) -> HIR.Builtin:
        return self.__builtin(node, types, [], self.__ctx.type_ctx.u64_id)

    def __lower_arg_bytes(self, node: AST.Builtin, types: list[int]) -> HIR.Builtin:
        index = self.__coerced_arg(node, 0, self.__ctx.type_ctx.u64_id)
        return self.__builtin(node, types, [index], self.__ctx.type_ctx.alloc_slice(self.__ctx.type_ctx.u8_id))

    def __lower_exit(self, node: AST.Builtin, types: list[int]) -> HIR.Builtin:
        code = self.__coerced_arg(node, 0, self.__ctx.type_ctx.i32_id)
        return self.__builtin(node, types, [code], self.__ctx.type_ctx.never_id)

    def __has_named_arg(self, args: list[AST.Arg]) -> bool:
        return any(arg.name is not None for arg in args)
