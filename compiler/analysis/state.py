"""Shared resources for one semantic-analysis run."""

from __future__ import annotations

from pathlib import Path

from compiler.analysis.facts.names import NameReferences
from compiler.analysis.package_map import PackageMap
from compiler.analysis.resolution.types import TypeResolver
from compiler.analysis.symbol.context import SymbolCtx
from compiler.analysis.ty.context import TypeCtx
from compiler.analysis.unit.procedures import ProcedureRegistry
from compiler.analysis.unit.unit_data import UnitData
from compiler.frontend.parse.ast_type import ASTType


class SemanticState:
    def __init__(
        self,
        type_ctx: TypeCtx,
        raw_pointers: bool,
        unit_datas: dict[int, UnitData],
        packages: PackageMap | None = None,
        stdlib_root: Path | None = None,
    ) -> None:
        self.type_ctx = type_ctx
        self.raw_pointers = raw_pointers
        self.unit_datas = unit_datas
        self.packages = packages
        self.stdlib_root = stdlib_root
        self.procedures = ProcedureRegistry(type_ctx)
        self.names = NameReferences()
        self.__type_resolver = TypeResolver(type_ctx, self.names)

    def resolve_type_in(self, ast_type: ASTType, symbol_ctx: SymbolCtx) -> int:
        return self.__type_resolver.resolve(ast_type, symbol_ctx)
