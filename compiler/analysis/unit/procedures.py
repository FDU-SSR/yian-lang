"""AST bodies associated with callable type definitions."""

from __future__ import annotations

from compiler.analysis.ty import ty as Type
from compiler.analysis.ty.context import TypeCtx
from compiler.error import CompilerError
from compiler.frontend.parse import ast as AST


class ProcedureRegistry:
    def __init__(self, type_ctx: TypeCtx) -> None:
        self.__type_ctx = type_ctx
        self.__procedures: dict[int, tuple[int, AST.Block, int]] = {}

    def __key(self, type_id: int) -> int:
        ty = self.__type_ctx[type_id]
        if isinstance(ty, (Type.FunctionType, Type.MethodType)):
            return id(ty.custom_def)
        if isinstance(ty, Type.ClosureType):
            return type_id
        raise CompilerError(f"Type ID {type_id} is not a function, method, or closure type and cannot be associated with a procedure")

    def register(self, type_id: int, body: AST.Block, unit_id: int) -> None:
        self.__procedures[self.__key(type_id)] = (type_id, body, unit_id)

    def copy(self, source_type_id: int, target_type_id: int) -> None:
        body, unit_id = self.get(source_type_id)
        self.register(target_type_id, body, unit_id)

    def entries(self) -> tuple[tuple[int, AST.Block, int], ...]:
        return tuple(self.__procedures.values())

    def get(self, type_id: int) -> tuple[AST.Block, int]:
        key = self.__key(type_id)
        if key not in self.__procedures:
            raise CompilerError(f"No procedure found for type ID {type_id} with definition ID {key}")
        _, body, unit_id = self.__procedures[key]
        return body, unit_id
