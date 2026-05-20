"""
Collect top-level symbols from the Unit and store them in the symbol context.

This is the first pass of the analysis phase.
"""
from __future__ import annotations

from compiler.analysis.error import AnalysisError
from compiler.analysis.symbol.symbol import SymbolAttribute, SymbolKind
from compiler.analysis.ty.context import TypeCtx
from compiler.analysis.unit.unit_data import UnitData

from compiler.frontend.parse import ast as AST


class SymbolCollect:
    def __init__(self, units: list[UnitData], type_ctx: TypeCtx) -> None:
        self.__units = units
        self.__type_ctx = type_ctx

    def run(self) -> None:
        for unit in self.__units:
            self.__collect_unit(unit)

    def __convert_attr(self, attr: AST.Attr) -> SymbolAttribute:
        match attr.kind:
            case AST.AttrKind.Pub:
                return SymbolAttribute.Public
            case AST.AttrKind.Static:
                raise AnalysisError(f"{attr} is not a valid attribute here", attr.span)

    def __collect_unit(self, unit: UnitData) -> None:
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
