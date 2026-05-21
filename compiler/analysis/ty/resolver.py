from compiler.analysis.symbol.context import SymbolCtx
from compiler.analysis.ty.context import TypeCtx
from compiler.frontend.parse.ast_type import ASTType


class TypeResolver:
    def __init__(self, type_ctx: TypeCtx):
        self.__ctx = type_ctx

    def resolve(self, ty: ASTType, symbol_ctx: SymbolCtx) -> int:
        """
        Resolve an ASTType to a type ID in the type context.

        This is used during type checking to convert the types written in the source code (AST) to the internal type representation.
        """
        raise NotImplementedError("TypeResolver.resolve is not implemented yet")
