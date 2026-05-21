"""
Resolves global all global items (functions, types, etc.) and imports.

This is the first pass of the analysis phase.
"""
from __future__ import annotations

from pathlib import Path

from compiler.analysis.error import AnalysisError
from compiler.analysis.symbol.symbol import SymbolAttribute, SymbolKind
from compiler.analysis.ty import ty as Type
from compiler.analysis.ty.context import TypeCtx
from compiler.analysis.unit.unit_data import UnitData
from compiler.frontend.parse import ast as AST


class ResolveGlobal:
    def __init__(self, units: list[UnitData], type_ctx: TypeCtx) -> None:
        self.__units = units
        self.__type_ctx = type_ctx

        self.__path_lookup: dict[Path, UnitData] = {unit.path.resolve(): unit for unit in units}
        self.__std_lookup: dict[tuple[str, ...], UnitData] = {}

        self.__build_std_lookup()

    def __build_std_lookup(self) -> None:
        for unit in self.__units:
            parts = unit.path.parts
            for i in range(len(parts) - 1, -1, -1):
                if parts[i] == "lib":
                    rel_parts = parts[i + 1:]
                    key = ("std",) + rel_parts[:-1] + (unit.path.stem,)
                    self.__std_lookup[key] = unit
                    break

    def run(self) -> None:
        for unit in self.__units:
            self.__collect_symbols(unit)

        for unit in self.__units:
            self.__resolve_imports(unit)

        for unit in self.__units:
            self.__resolve_definitions(unit)

    def __convert_attr(self, attr: AST.Attr) -> SymbolAttribute:
        match attr.kind:
            case AST.AttrKind.Pub:
                return SymbolAttribute.Public
            case AST.AttrKind.Static:
                raise AnalysisError(f"{attr} is not a valid attribute here", attr.span)

    def __collect_symbols(self, unit: UnitData) -> None:
        """Collects all global symbols in the unit."""
        for item in unit.items():
            match item:
                case AST.Alias(name=name, attrs=attrs):
                    # alloc in type space
                    type_id = self.__type_ctx.alloc_alias(name.name)

                    # alloc in symbol space
                    symbol_attrs = {self.__convert_attr(attr) for attr in attrs}
                    unit.symbol_ctx.add_symbol(name.name, SymbolKind.Type, type_id, symbol_attrs)

                case AST.FuncDef(name=name, attrs=attrs):
                    # alloc in type space
                    type_id = self.__type_ctx.alloc_function(name.name)

                    # alloc in symbol space
                    symbol_attrs = {self.__convert_attr(attr) for attr in attrs}
                    unit.symbol_ctx.add_symbol(name.name, SymbolKind.Function, type_id, symbol_attrs)

                case AST.StructDef(name=name, attrs=attrs):
                    # alloc in type space
                    type_id = self.__type_ctx.alloc_struct(name.name)

                    # alloc in symbol space
                    symbol_attrs = {self.__convert_attr(attr) for attr in attrs}
                    unit.symbol_ctx.add_symbol(name.name, SymbolKind.Type, type_id, symbol_attrs)

                case AST.EnumDef(name=name, attrs=attrs):
                    # alloc in type space
                    type_id = self.__type_ctx.alloc_enum(name.name)

                    # alloc in symbol space
                    symbol_attrs = {self.__convert_attr(attr) for attr in attrs}
                    unit.symbol_ctx.add_symbol(name.name, SymbolKind.Type, type_id, symbol_attrs)

                case AST.TraitDef(name=name, attrs=attrs):
                    # alloc in type space
                    type_id = self.__type_ctx.alloc_trait(name.name)

                    # alloc in symbol space
                    symbol_attrs = {self.__convert_attr(attr) for attr in attrs}
                    unit.symbol_ctx.add_symbol(name.name, SymbolKind.Type, type_id, symbol_attrs)

                case _:
                    # other items are ignored in this pass
                    pass

    def __resolve_imports(self, unit: UnitData) -> None:
        """Resolves all import statements in the unit and adds the imported symbols to the symbol context."""
        for item in unit.items():
            if not isinstance(item, AST.Import):
                continue

            paths = [part.name for part in item.paths]
            target_unit = self.__resolve_import_path(unit, paths)

            if target_unit is None:
                raise AnalysisError(f"Cannot resolve import path: {'.'.join(paths)}", item.span)

            target_symbol = target_unit.symbol_ctx.lookup_exportable(item.target.name)
            if target_symbol is None:
                raise AnalysisError(f"Symbol '{item.target.name}' is not found in the imported unit", item.target.span)
            if target_symbol.kind == SymbolKind.Variable:
                raise AnalysisError(f"Cannot import variable '{item.target.name}'", item.target.span)

            imported_name = item.alias.name if item.alias is not None else item.target.name
            unit.symbol_ctx.add_symbol(imported_name, target_symbol.kind, target_symbol.type_id)

    def __resolve_import_path(self, unit: UnitData, paths: list[str]) -> UnitData | None:
        """
        Resolves the path of an import statement to a unit.

        Returns the resolved unit, or None if the path cannot be resolved.
        """
        if len(paths) == 0:
            return None

        # case 1: std lib import
        if paths[0] == "std":
            return self.__std_lookup.get(tuple(paths))

        # case 2: relative import
        target_path = unit.path.parent.joinpath(*paths).with_suffix(".yian")
        return self.__path_lookup.get(target_path.resolve())

    def __resolve_definitions(self, unit: UnitData) -> None:
        """Resolves all definitions in the unit and updates the symbol context and type context with the resolved types."""
        for item in unit.items():
            match item:
                case AST.Alias():
                    self.__resolve_alias(unit, item)
                case AST.FuncDef():
                    self.__resolve_func_def(unit, item)
                case AST.StructDef():
                    self.__resolve_struct_def(unit, item)
                case AST.EnumDef():
                    self.__resolve_enum_def(unit, item)
                case AST.TraitDef():
                    self.__resolve_trait_def(unit, item)
                case AST.Impl():
                    self.__resolve_impl(unit, item)
                case _:
                    # other items are ignored in this pass
                    pass

    def __resolve_alias(self, unit: UnitData, alias: AST.Alias) -> None:
        symbol = unit.symbol_ctx.lookup(alias.name.name)
        assert symbol is not None

        # resolve generics and aliased type
        unit.symbol_ctx.enter_scope()
        generics: list[int] = []
        for generic in alias.generics:
            generic_type_id = self.__type_ctx.alloc_generic(generic.name)
            generics.append(generic_type_id)
            unit.symbol_ctx.add_symbol(generic.name, SymbolKind.Type, generic_type_id)

        aliased_type_id = self.__type_ctx.resolve_type(alias.target, unit.symbol_ctx)
        unit.symbol_ctx.exit_scope()

        # update the alias symbol with the resolved type
        ty = self.__type_ctx[symbol.type_id]
        assert isinstance(ty, Type.AliasType)
        ty.custom_def.generics = generics.copy()
        ty.custom_def.aliased_type = aliased_type_id
        ty.generic_args = generics.copy()

    def __resolve_func_def(self, unit: UnitData, func_def: AST.FuncDef) -> None:
        raise NotImplementedError("Function definition resolution is not implemented yet")

    def __resolve_struct_def(self, unit: UnitData, struct_def: AST.StructDef) -> None:
        raise NotImplementedError("Struct definition resolution is not implemented yet")

    def __resolve_enum_def(self, unit: UnitData, enum_def: AST.EnumDef) -> None:
        raise NotImplementedError("Enum definition resolution is not implemented yet")

    def __resolve_trait_def(self, unit: UnitData, trait_def: AST.TraitDef) -> None:
        raise NotImplementedError("Trait definition resolution is not implemented yet")

    def __resolve_impl(self, unit: UnitData, impl: AST.Impl) -> None:
        raise NotImplementedError("Impl resolution is not implemented yet")
