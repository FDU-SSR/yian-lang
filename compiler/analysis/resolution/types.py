from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from compiler.analysis.const_eval import ConstantExpressionEvaluator, ConstantValue
from compiler.analysis.error import AnalysisError
from compiler.analysis.facts.names import NameReferences
from compiler.analysis.symbol.symbol import Symbol, SymbolKind
from compiler.analysis.ty import ty as Type
from compiler.analysis.unit import hir as HIR
from compiler.error import CompilerError
from compiler.frontend.lex.position import SrcSpan
from compiler.frontend.lex.token import IntLiteral
from compiler.frontend.parse import ast as AST
from compiler.frontend.parse import ast_type as ASTTy
from compiler.frontend.parse.ast_type import (ASTType, ConstExpr,
                                              GenericConstExpr,
                                              LiteralConstExpr)

if TYPE_CHECKING:
    from compiler.analysis.symbol.context import SymbolCtx
    from compiler.analysis.ty.context import TypeCtx


class TypeResolver:
    def __init__(
        self,
        type_ctx: TypeCtx,
        names: NameReferences,
        constant_value: Callable[[Symbol], tuple[ConstantValue, int]],
    ):
        self.__ctx = type_ctx
        self.__names = names
        self.__constant_value = constant_value
        self.__aliases: dict[int, tuple[AST.Alias, SymbolCtx]] = {}
        self.__filling_aliases: set[int] = set()
        self.__const_eval = ConstantExpressionEvaluator(
            type_ctx, lambda _type_id: 0, "type-level constant expression"
        )

    INT_MAPPING = {
        (True, 1): 14,
        (True, 2): 15,
        (True, 4): 16,
        (True, 8): 17,
        (False, 1): 18,
        (False, 2): 19,
        (False, 4): 20,
        (False, 8): 21,
    }

    FLOAT_MAPPING = {
        2: 22,
        4: 23,
        8: 24,
    }

    def resolve(self, ty: ASTType, symbol_ctx: SymbolCtx) -> int:
        """
        Resolve an ASTType to a type ID in the type context.

        This is used during type checking to convert the types written in the
        source code (AST) to the internal type representation. A name that refers
        to an alias retains the alias's own type ID. Its body is resolved in the
        definition scope on demand, independently of declaration order.
        Consumers inspect the underlying type with :meth:`TypeCtx.resolve_aliases`.
        """
        return self.__resolve(ty, symbol_ctx)

    def resolve_const_expr(self, const_expr: ConstExpr, symbol_ctx: SymbolCtx) -> int:
        """Resolve a type-level integer expression to its constant type ID."""
        return self.__resolve_const_expr(const_expr, symbol_ctx)

    def register_alias(self, type_id: int, definition: AST.Alias, symbol_ctx: SymbolCtx) -> None:
        """Retain a definition scope for demand-driven alias body resolution."""
        ty = self.__ctx[type_id]
        if not isinstance(ty, Type.AliasType):
            raise CompilerError("alias registration requires an alias type")
        self.__aliases[id(ty.custom_def)] = definition, symbol_ctx.clone()

    def resolve_alias(self, type_id: int) -> None:
        """Fill an alias body before a consumer inspects its concrete kind."""
        ty = self.__ctx[type_id]
        if not isinstance(ty, Type.AliasType) or ty.custom_def.aliased_type != -1:
            return
        key = id(ty.custom_def)
        entry = self.__aliases.get(key)
        if entry is None:
            return
        definition, symbol_ctx = entry
        if key in self.__filling_aliases:
            raise AnalysisError(f"Circular type alias: {definition.name.name}", definition.span)
        self.__filling_aliases.add(key)
        symbol_ctx.enter_scope()
        try:
            for parameter, generic_id in zip(definition.generics, ty.custom_def.generics):
                kind = (
                    SymbolKind.ConstGeneric
                    if isinstance(parameter, AST.ConstGenericParam)
                    else SymbolKind.Type
                )
                symbol_ctx.add_symbol(
                    parameter.name.name, kind, generic_id, span=parameter.name.span
                )
            ty.custom_def.aliased_type = self.resolve(definition.target, symbol_ctx)
        finally:
            symbol_ctx.exit_scope()
            self.__filling_aliases.discard(key)

    def __resolve(self, ty: ASTType, symbol_ctx: SymbolCtx) -> int:
        """Resolve *ty* without collapsing its top-level alias.

        Sub-components are resolved through the public :meth:`resolve`. A
        NamedType/InstanceType that names an alias is returned uncollapsed, both
        so a generic alias (``typedef Ptr<T> = T*``) can be instantiated with its
        arguments before its body is substituted, and so the alias keeps its own
        identity where it is used.
        """
        match ty:
            case ASTTy.IntType(signed=signed, width=width):
                return self.INT_MAPPING[(signed, width)]
            case ASTTy.CScalarType(name=name):
                if not symbol_ctx.allows_ffi:
                    raise AnalysisError("C ABI types require FFI permission", ty.span)
                return {
                    "c_int": self.__ctx.c_int_id,
                    "c_uint": self.__ctx.c_uint_id,
                    "c_size": self.__ctx.c_size_id,
                    "c_char": self.__ctx.c_char_id,
                }[name]
            case ASTTy.CPtrType(pointee_type=pointee_type):
                if not symbol_ctx.allows_ffi:
                    raise AnalysisError("cptr<T> requires FFI permission", ty.span)
                return self.__ctx.alloc_cptr(self.resolve(pointee_type, symbol_ctx))
            case ASTTy.FloatType(width=width):
                return self.FLOAT_MAPPING[width]
            case ASTTy.BoolType():
                return self.__ctx.bool_id
            case ASTTy.StrType():
                return self.__ctx.str_id
            case ASTTy.CharType():
                return self.__ctx.char_id
            case ASTTy.VoidType():
                return self.__ctx.void_id
            case ASTTy.NeverType():
                return self.__ctx.never_id
            case ASTTy.ArrayType(element_type=element_type, size=size):
                element_type_id = self.resolve(element_type, symbol_ctx)
                size_id = self.__resolve_const_expr(size, symbol_ctx)
                length_ty = self.__ctx[size_id]
                if isinstance(length_ty, Type.LiteralValueType) and (
                    type(length_ty.value) is not int or length_ty.value < 0
                ):
                    raise AnalysisError("array length must be a non-negative integer", size.span)
                return self.__ctx.alloc_array(element_type_id, size_id)
            case ASTTy.TupleType(element_types=element_types):
                element_type_ids = [self.resolve(et, symbol_ctx) for et in element_types]
                return self.__ctx.alloc_tuple(element_type_ids)
            case ASTTy.PointerType(pointee_type=pointee_type):
                pointee_type_id = self.resolve(pointee_type, symbol_ctx)
                return self.__ctx.alloc_pointer(pointee_type_id)
            case ASTTy.RefType(pointee_type=pointee_type):
                pointee_type_id = self.resolve(pointee_type, symbol_ctx)
                self.__check_trait_ref_arguments(pointee_type, pointee_type_id, symbol_ctx)
                return self.__ctx.alloc_ref(pointee_type_id)
            case ASTTy.SliceType(element_type=element_type):
                element_type_id = self.resolve(element_type, symbol_ctx)
                return self.__ctx.alloc_slice(element_type_id)
            case ASTTy.NamedType(name=name):
                symbol = symbol_ctx.lookup(name.name)
                if symbol is None:
                    raise AnalysisError(f"Undefined type: {name}", ty.span)
                if symbol.kind not in (SymbolKind.Type, SymbolKind.ConstGeneric):
                    raise AnalysisError(f"{name} is not a type", ty.span)
                # Record the name as written: editors navigate and hover type
                # annotations, which no HIR expression represents.
                self.__names.record(name.span, symbol.type_id)
                self.resolve_alias(symbol.type_id)
                return symbol.type_id
            case ASTTy.InstanceType(base=base, generic_args=generic_args):
                # Hardcoded type constructors (Tuple / Fn) are not
                # registered symbols — intercept by name first.
                arg_ids = [self.__resolve_generic_arg(arg, symbol_ctx) for arg in generic_args]
                if isinstance(base, ASTTy.NamedType):
                    type_id = self.__ctx.try_builtin_ctor(base.name.name, arg_ids)
                    if type_id is not None:
                        return type_id
                base_type_id = self.__resolve(base, symbol_ctx)
                return self.__ctx.alloc_instance(base_type_id, arg_ids)
            case ASTTy.FunctionType(param_types=param_types, return_type=return_type):
                param_type_ids = [self.resolve(pt, symbol_ctx) for pt in param_types]
                return_type_id = self.resolve(return_type, symbol_ctx)
                return self.__ctx.alloc_function_pointer(param_type_ids, return_type_id)
            case ASTTy.DeducedType():
                raise AnalysisError("Cannot resolve deduced type '_'", ty.span)

    def __check_trait_ref_arguments(
        self,
        ast_type: ASTType,
        type_id: int,
        symbol_ctx: SymbolCtx,
    ) -> None:
        """Reject bare generic trait names before `T&` erases source syntax."""
        if not isinstance(ast_type, ASTTy.NamedType):
            return
        symbol = symbol_ctx.lookup(ast_type.name.name)
        if symbol is None or symbol.kind is not SymbolKind.Type:
            return
        declared_ty = self.__ctx[symbol.type_id]
        if isinstance(declared_ty, Type.AliasType) and declared_ty.custom_def.generics \
                and declared_ty.generic_args == declared_ty.custom_def.generics:
            raise AnalysisError(
                f"trait object type '{self.__ctx.get_name(type_id)}' requires all generic arguments",
                ast_type.span,
            )
        trait_type_id = self.__ctx.resolve_aliases(type_id)
        trait_ty = self.__ctx[trait_type_id]
        if isinstance(trait_ty, Type.TraitType) and trait_ty.custom_def.generics \
                and trait_ty.generic_args == trait_ty.custom_def.generics:
            raise AnalysisError(
                f"trait object type '{self.__ctx.get_name(trait_type_id)}' requires all generic arguments",
                ast_type.span,
            )

    def __resolve_const_expr(self, const_expr: ConstExpr, symbol_ctx: SymbolCtx) -> int:
        match const_expr:
            case LiteralConstExpr(literal=IntLiteral(value=value, suffix=suffix)):
                value_type = self.__integer_literal_type(suffix, self.__ctx.u64_id, const_expr.span)
                return self.__ctx.alloc_literal_value(value, value_type)
            case GenericConstExpr(name=name):
                symbol = symbol_ctx.lookup(name.name)
                if symbol is None:
                    raise AnalysisError(f"Undefined const generic '{name.name}'", const_expr.span)
                if symbol.kind == SymbolKind.Constant:
                    value, value_type = self.__constant_value(symbol)
                    self.__names.record(name.span, symbol, value_type)
                    value_ty = self.__ctx[self.__ctx.resolve_aliases(value_type)]
                    if type(value) is int and isinstance(value_ty, Type.IntType):
                        return self.__ctx.alloc_literal_value(value, value_type)
                    if type(value) is bool and isinstance(value_ty, Type.BoolType):
                        return self.__ctx.alloc_literal_value(value, value_type)
                    raise AnalysisError(
                        f"'{name.name}' cannot be used as a compile-time generic argument",
                        const_expr.span,
                    )
                if symbol.kind not in (SymbolKind.Type, SymbolKind.ConstGeneric):
                    raise AnalysisError(f"'{name.name}' is not a compile-time constant", const_expr.span)
                self.__names.record(name.span, symbol, symbol.type_id)
                return symbol.type_id
            case ASTTy.UnaryConstExpr() | ASTTy.BinaryConstExpr():
                value, type_id = self.__evaluate_const_tree(const_expr, symbol_ctx)
                if value is None:
                    raise AnalysisError(
                        "arithmetic on an unresolved const generic is not supported",
                        const_expr.span,
                    )
                assert type(value) is int
                if isinstance(self.__ctx[type_id], Type.IntLiteralType):
                    type_id = self.__ctx.i64_id if value < 0 else self.__ctx.u64_id
                return self.__ctx.alloc_literal_value(value, type_id)
            case _:
                raise AnalysisError(f"Unsupported const expression: {const_expr}", const_expr.span)

    def __resolve_generic_arg(self, arg: ASTType | ConstExpr, symbol_ctx: SymbolCtx) -> int:
        match arg:
            case LiteralConstExpr() | GenericConstExpr() | ASTTy.UnaryConstExpr() | ASTTy.BinaryConstExpr():
                return self.__resolve_const_expr(arg, symbol_ctx)
            case ASTTy.NamedType(name=name):
                symbol = symbol_ctx.lookup(name.name)
                if symbol is not None and symbol.kind == SymbolKind.Constant:
                    return self.__resolve_const_expr(
                        ASTTy.GenericConstExpr(span=name.span, name=name), symbol_ctx
                    )
                return self.resolve(arg, symbol_ctx)
            case _:
                return self.resolve(arg, symbol_ctx)

    def __evaluate_const_tree(self, expr: ConstExpr, symbol_ctx: SymbolCtx) -> tuple[int | None, int]:
        """Evaluate one concrete integer subtree; preserve direct generic values."""
        match expr:
            case LiteralConstExpr(literal=IntLiteral(value=value, suffix=suffix)):
                value_type = self.__integer_literal_type(
                    suffix, self.__ctx.int_literal_id, expr.span
                )
                return value, value_type
            case LiteralConstExpr():
                raise AnalysisError("type-level constants must be integer literals", expr.span)
            case GenericConstExpr(name=name):
                symbol = symbol_ctx.lookup(name.name)
                if symbol is None:
                    raise AnalysisError(f"Undefined const generic '{name.name}'", expr.span)
                if symbol.kind == SymbolKind.Constant:
                    value, value_type = self.__constant_value(symbol)
                    self.__names.record(name.span, symbol, value_type)
                    if type(value) is not int or not self.__is_integer_type(value_type):
                        raise AnalysisError(
                            f"'{name.name}' is not an integer compile-time constant", expr.span
                        )
                    return value, value_type
                if symbol.kind == SymbolKind.ConstGeneric:
                    self.__names.record(name.span, symbol, symbol.type_id)
                    return None, symbol.type_id
                if symbol.kind == SymbolKind.Type:
                    self.__names.record(name.span, symbol, symbol.type_id)
                    ty = self.__ctx[self.__ctx.resolve_aliases(symbol.type_id)]
                    if isinstance(ty, Type.LiteralValueType) and type(ty.value) is int:
                        return ty.value, ty.value_type
                raise AnalysisError(f"'{name.name}' is not a compile-time integer", expr.span)
            case ASTTy.UnaryConstExpr(op=op, operand=operand):
                value, type_id = self.__evaluate_const_tree(operand, symbol_ctx)
                if value is None:
                    return None, type_id
                inner = HIR.IntLiteral(span=operand.span, value=value, type_id=type_id, is_place=False)
                expression = HIR.Unary(
                    span=expr.span, op=op, operand=inner, type_id=type_id, is_place=False
                )
                result, result_type = self.__const_eval.evaluate(expression)
                assert type(result) is int
                return result, result_type
            case ASTTy.BinaryConstExpr(op=op, left=left, right=right):
                left_value, left_type = self.__evaluate_const_tree(left, symbol_ctx)
                right_value, right_type = self.__evaluate_const_tree(right, symbol_ctx)
                if left_value is None or right_value is None:
                    return None, left_type
                result_type = self.__ctx.merge_types([left_type, right_type], expr.span)
                expression = HIR.Binary(
                    span=expr.span,
                    op=op,
                    left=HIR.IntLiteral(span=left.span, value=left_value, type_id=result_type, is_place=False),
                    right=HIR.IntLiteral(span=right.span, value=right_value, type_id=result_type, is_place=False),
                    type_id=result_type,
                    is_place=False,
                )
                result, result_type = self.__const_eval.evaluate(expression)
                assert type(result) is int
                return result, result_type
        raise AnalysisError(f"Unsupported const expression: {expr}", expr.span)

    def __is_integer_type(self, type_id: int) -> bool:
        return isinstance(self.__ctx[self.__ctx.resolve_aliases(type_id)], Type.IntType)

    def __integer_literal_type(self, suffix: str | None, default: int, span: SrcSpan) -> int:
        if suffix is None:
            return default
        intrinsic = Type.IntrinsicType.from_str(suffix)
        if intrinsic is None:
            raise AnalysisError(f"Unknown integer suffix '{suffix}'", span)
        type_id = self.__ctx.intrinsic_type(intrinsic)
        if not self.__is_integer_type(type_id):
            raise AnalysisError(f"Suffix '{suffix}' is not an integer type", span)
        return type_id
