from __future__ import annotations

from typing import TYPE_CHECKING

from compiler.analysis.error import AnalysisError
from compiler.analysis.symbol.symbol import SymbolKind
from compiler.frontend.lex.token import IntLiteral
from compiler.frontend.parse import ast_type as ASTTy
from compiler.frontend.parse.ast_type import (ASTType, ConstExpr,
                                              GenericConstExpr,
                                              LiteralConstExpr)

if TYPE_CHECKING:
    from compiler.analysis.symbol.context import SymbolCtx
    from compiler.analysis.ty.context import TypeCtx


class TypeResolver:
    def __init__(self, type_ctx: TypeCtx):
        self.__ctx = type_ctx

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

        This is used during type checking to convert the types written in the source code (AST) to the internal type representation.
        """
        match ty:
            case ASTTy.IntType(signed=signed, width=width):
                return self.INT_MAPPING[(signed, width)]
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
            case ASTTy.ArrayType(element_type=element_type, size=size):
                element_type_id = self.resolve(element_type, symbol_ctx)
                size_id = self.__resolve_const_expr(size, symbol_ctx)
                return self.__ctx.alloc_array(element_type_id, size_id)
            case ASTTy.TupleType(element_types=element_types):
                element_type_ids = [self.resolve(et, symbol_ctx) for et in element_types]
                return self.__ctx.alloc_tuple(element_type_ids)
            case ASTTy.PointerType(pointee_type=pointee_type):
                pointee_type_id = self.resolve(pointee_type, symbol_ctx)
                return self.__ctx.alloc_pointer(pointee_type_id)
            case ASTTy.SliceType(element_type=element_type):
                element_type_id = self.resolve(element_type, symbol_ctx)
                return self.__ctx.alloc_slice(element_type_id)
            case ASTTy.NamedType(name=name):
                # Look up the named type in the symbol context
                symbol = symbol_ctx.lookup(name.name)
                if symbol is None:
                    raise AnalysisError(f"Undefined type: {name}", ty.span)
                if symbol.kind not in (SymbolKind.Type, SymbolKind.ConstGeneric):
                    raise AnalysisError(f"{name} is not a type", ty.span)
                return symbol.type_id
            case ASTTy.InstanceType(base=base, generic_args=generic_args):
                base_type_id = self.resolve(base, symbol_ctx)
                arg_ids = [self.__resolve_generic_arg(arg, symbol_ctx) for arg in generic_args]
                return self.__ctx.alloc_instance(base_type_id, arg_ids)
            case ASTTy.FunctionType(param_types=param_types, return_type=return_type):
                param_type_ids = [self.resolve(pt, symbol_ctx) for pt in param_types]
                return_type_id = self.resolve(return_type, symbol_ctx)
                return self.__ctx.alloc_function_pointer(param_type_ids, return_type_id)
            case ASTTy.DeducedType():
                raise AnalysisError("Cannot resolve deduced type '_'", ty.span)

    def __resolve_const_expr(self, const_expr: ConstExpr, symbol_ctx: SymbolCtx) -> int:
        match const_expr:
            case LiteralConstExpr(literal=IntLiteral(value=v)):
                return self.__ctx.alloc_literal_value(v, self.__ctx.u64_id)
            case GenericConstExpr(name=name):
                symbol = symbol_ctx.lookup(name.name)
                assert symbol is not None, f"Undefined const generic '{name.name}'"
                return symbol.type_id
            case _:
                raise AnalysisError(f"Unsupported const expression: {const_expr}", const_expr.span)

    def __resolve_generic_arg(self, arg: ASTType | ConstExpr, symbol_ctx: SymbolCtx) -> int:
        match arg:
            case LiteralConstExpr() | GenericConstExpr():
                return self.__resolve_const_expr(arg, symbol_ctx)
            case _:
                return self.resolve(arg, symbol_ctx)
