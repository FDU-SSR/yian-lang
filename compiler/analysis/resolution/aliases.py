"""Transparent alias declarations and their definition-scoped type templates."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from enum import Enum, auto
from types import MappingProxyType
from typing import TYPE_CHECKING

from compiler.analysis.error import AnalysisError
from compiler.analysis.symbol.symbol import AliasDefId, SymbolKind
from compiler.analysis.ty import ty as Type
from compiler.error import CompilerError
from compiler.frontend.lex.position import SrcSpan
from compiler.frontend.parse import ast as AST
from compiler.frontend.parse.ast_type import ASTType

if TYPE_CHECKING:
    from compiler.analysis.symbol.context import SymbolCtx
    from compiler.analysis.ty.context import TypeCtx


class _Status(Enum):
    Pending = auto()
    Resolving = auto()
    Resolved = auto()
    Failed = auto()


@dataclass(frozen=True)
class AliasInfo:
    definition: AST.Alias
    generics: tuple[int, ...]
    template: int | None


@dataclass
class _Definition:
    definition: AST.Alias
    generics: tuple[int, ...]
    scope: SymbolCtx | None = None
    template: int | None = None
    status: _Status = _Status.Pending
    error: AnalysisError | None = None


class AliasRegistry:
    def __init__(self, types: TypeCtx, resolve: Callable[[ASTType, SymbolCtx], int]):
        self.__types = types
        self.__resolve = resolve
        self.__definitions: dict[AliasDefId, _Definition] = {}
        self.__stack: list[AliasDefId] = []
        self.__instances: dict[tuple[AliasDefId, tuple[int, ...]], int] = {}

    def declare(self, definition: AST.Alias, generics: list[int]) -> AliasDefId:
        alias_id = AliasDefId(len(self.__definitions))
        self.__definitions[alias_id] = _Definition(definition, tuple(generics))
        return alias_id

    def bind_scope(self, alias_id: AliasDefId, scope: SymbolCtx) -> None:
        self.__definitions[alias_id].scope = scope.clone()

    def generics(self, alias_id: AliasDefId) -> tuple[int, ...]:
        return self.__definitions[alias_id].generics

    def template(self, alias_id: AliasDefId) -> int:
        entry = self.__definitions[alias_id]
        if entry.template is not None:
            return entry.template
        if entry.error is not None:
            raise entry.error
        if entry.status is _Status.Resolving:
            cycle = self.__stack[self.__stack.index(alias_id):] + [alias_id]
            names = [self.__definitions[item].definition.name.name for item in cycle]
            raise AnalysisError(f"Circular type alias: {' -> '.join(names)}", entry.definition.span)
        if entry.scope is None:
            raise CompilerError("alias definition scope is not registered")
        scope = entry.scope.clone()
        scope.enter_scope()
        entry.status = _Status.Resolving
        self.__stack.append(alias_id)
        try:
            for parameter, generic_id in zip(entry.definition.generics, entry.generics):
                kind = SymbolKind.ConstGeneric if isinstance(parameter, AST.ConstGenericParam) else SymbolKind.Type
                if scope.add_symbol(parameter.name.name, kind, generic_id, span=parameter.name.span) is None:
                    raise AnalysisError(f"Duplicate generic parameter: {parameter.name.name}", parameter.name.span)
            for parameter, generic_id in zip(entry.definition.generics, entry.generics):
                if isinstance(parameter, AST.ConstGenericParam):
                    generic = self.__types[generic_id]
                    if not isinstance(generic, Type.ConstGenericType):
                        raise CompilerError("constant alias parameter requires a constant generic")
                    generic.value_type = self.__resolve(parameter.value_type, scope)
            entry.template = self.__resolve(entry.definition.target, scope)
            entry.status = _Status.Resolved
            return entry.template
        except AnalysisError as error:
            entry.error = error
            entry.status = _Status.Failed
            raise
        finally:
            self.__stack.pop()
            scope.exit_scope()
            if entry.status is _Status.Resolving:
                entry.status = _Status.Pending

    def apply(self, alias_id: AliasDefId, arguments: Sequence[int], span: SrcSpan) -> int:
        entry = self.__definitions[alias_id]
        if len(arguments) != len(entry.generics):
            raise AnalysisError(
                f"Alias '{entry.definition.name.name}' expects {len(entry.generics)} generic arguments, got {len(arguments)}",
                span,
            )
        template = self.template(alias_id)
        substitutions = dict(zip(entry.generics, arguments))
        for parameter, argument in zip(entry.generics, arguments):
            expected = self.__types[parameter]
            actual = self.__types[argument]
            is_constant = isinstance(actual, (Type.ConstGenericType, Type.LiteralValueType))
            if isinstance(expected, Type.ConstGenericType):
                value_type = self.__types.instantiate(expected.value_type, substitutions)
                if not isinstance(actual, (Type.ConstGenericType, Type.LiteralValueType)) \
                        or actual.value_type != value_type:
                    raise AnalysisError("Alias constant argument has an incompatible type", span)
            elif is_constant:
                raise AnalysisError("Alias type parameter requires a type argument", span)
        key = alias_id, tuple(arguments)
        cached = self.__instances.get(key)
        if cached is None:
            cached = self.__types.instantiate(template, substitutions)
            self.__instances[key] = cached
        return cached

    def resolve_all(self) -> None:
        for alias_id in self.__definitions:
            self.template(alias_id)

    def snapshot(self) -> Mapping[AliasDefId, AliasInfo]:
        return MappingProxyType({
            alias_id: AliasInfo(entry.definition, entry.generics, entry.template)
            for alias_id, entry in self.__definitions.items()
        })
