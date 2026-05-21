from compiler.analysis.error import AnalysisError
from compiler.analysis.symbol.context import SymbolCtx
from compiler.analysis.symbol.symbol import SymbolKind
from compiler.analysis.ty.context import TypeCtx
from compiler.frontend.parse.ast_type import ASTType
from compiler.frontend.parse import ast_type as ASTTy


class TypeResolver:
    def __init__(self, type_ctx: TypeCtx):
        self.__ctx = type_ctx

    INT_MAPPING = {
        (True, 1): TypeCtx.i8_id,
        (True, 2): TypeCtx.i16_id,
        (True, 4): TypeCtx.i32_id,
        (True, 8): TypeCtx.i64_id,
        (False, 1): TypeCtx.u8_id,
        (False, 2): TypeCtx.u16_id,
        (False, 4): TypeCtx.u32_id,
        (False, 8): TypeCtx.u64_id,
    }

    FLOAT_MAPPING = {
        2: TypeCtx.f16_id,
        4: TypeCtx.f32_id,
        8: TypeCtx.f64_id,
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
                return self.__ctx.alloc_array(element_type_id, size)
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
                if symbol.kind != SymbolKind.Type:
                    raise AnalysisError(f"{name} is not a type", ty.span)
                return symbol.type_id
            case ASTTy.InstanceType(base=base, generic_args=generic_args):
                base_type_id = self.resolve(base, symbol_ctx)
                generic_arg_type_ids = [self.resolve(arg, symbol_ctx) for arg in generic_args]
                return self.__ctx.alloc_instance(base_type_id, generic_arg_type_ids)
            case ASTTy.FunctionType(param_types=param_types, return_type=return_type):
                param_type_ids = [self.resolve(pt, symbol_ctx) for pt in param_types]
                return_type_id = self.resolve(return_type, symbol_ctx)
                return self.__ctx.alloc_function_pointer(param_type_ids, return_type_id)
