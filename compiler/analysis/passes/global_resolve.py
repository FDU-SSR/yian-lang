"""
Resolves global all global items (functions, types, etc.) and imports.

This is the first pass of the analysis phase.
"""
from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from compiler.analysis.error import AnalysisError
from compiler.analysis.symbol.symbol import SymbolAttribute, SymbolKind
from compiler.analysis.ty import ty as Type
from compiler.frontend.parse import ast as AST

if TYPE_CHECKING:
    from compiler.analysis.ty.context import TypeCtx
    from compiler.analysis.unit.unit_data import UnitData


class GlobalResolve:
    def __init__(self, units: dict[int, UnitData], type_ctx: TypeCtx) -> None:
        self.__units = units
        self.__type_ctx = type_ctx

        self.__path_lookup: dict[Path, UnitData] = {unit.path.resolve(): unit for unit in units.values()}
        self.__std_lookup: dict[tuple[str, ...], UnitData] = {}

        self.__build_std_lookup()

    def run(self) -> None:
        for unit in self.__units.values():
            self.__collect_symbols(unit)

        for unit in self.__units.values():
            self.__resolve_imports(unit)

        for unit in self.__units.values():
            self.__resolve_definitions(unit)

        self.__type_ctx.check_impls()

    def __build_std_lookup(self) -> None:
        for unit in self.__units.values():
            parts = unit.path.parts
            for i in range(len(parts) - 1, -1, -1):
                if parts[i] == "lib":
                    rel_parts = parts[i + 1:]
                    key = ("std",) + rel_parts[:-1] + (unit.path.stem,)
                    self.__std_lookup[key] = unit
                    break

    def __convert_attrs(self, attrs: list[AST.Attr]) -> set[SymbolAttribute]:
        res: set[SymbolAttribute] = set()
        for attr in attrs:
            match attr.kind:
                case AST.AttrKind.Pub:
                    res.add(SymbolAttribute.Public)
                case _:
                    continue
        return res

    def __collect_symbols(self, unit: UnitData) -> None:
        """Collects all global symbols in the unit."""
        for item in unit.items():
            match item:
                case AST.Alias(name=name, attrs=attrs):
                    # alloc in type space
                    type_id = self.__type_ctx.alloc_alias(name.name)

                    # alloc in symbol space
                    symbol_attrs = self.__convert_attrs(attrs)
                    symbol_id = unit.symbol_ctx.add_symbol(name.name, SymbolKind.Type, type_id, symbol_attrs)
                    if symbol_id is None:
                        raise AnalysisError(f"Duplicate symbol name: {name.name}", name.span)
                    symbol = unit.symbol_ctx.get(symbol_id)

                    generics = self.__alloc_generics(unit, item.generics)
                    ty = self.__type_ctx[symbol.type_id]
                    assert isinstance(ty, Type.AliasType)
                    ty.custom_def.generics = generics.copy()
                    ty.generic_args = generics.copy()

                case AST.FuncDef(name=name, attrs=attrs):
                    # alloc in type space
                    type_id = self.__type_ctx.alloc_function(name.name)

                    # alloc in symbol space
                    symbol_attrs = self.__convert_attrs(attrs)
                    symbol_id = unit.symbol_ctx.add_symbol(name.name, SymbolKind.Function, type_id, symbol_attrs)
                    if symbol_id is None:
                        raise AnalysisError(f"Duplicate symbol name: {name.name}", name.span)
                    symbol = unit.symbol_ctx.get(symbol_id)

                    generics = self.__alloc_generics(unit, item.generics)
                    ty = self.__type_ctx[symbol.type_id]
                    assert isinstance(ty, Type.FunctionType)
                    ty.custom_def.generics = generics.copy()
                    ty.generic_args = generics.copy()

                case AST.StructDef(name=name, attrs=attrs):
                    # alloc in type space
                    type_id = self.__type_ctx.alloc_struct(name.name)

                    # alloc in symbol space
                    symbol_attrs = self.__convert_attrs(attrs)
                    symbol_id = unit.symbol_ctx.add_symbol(name.name, SymbolKind.Type, type_id, symbol_attrs)
                    if symbol_id is None:
                        raise AnalysisError(f"Duplicate symbol name: {name.name}", name.span)
                    symbol = unit.symbol_ctx.get(symbol_id)

                    generics = self.__alloc_generics(unit, item.generics)
                    ty = self.__type_ctx[symbol.type_id]
                    assert isinstance(ty, Type.StructType)
                    ty.custom_def.generics = generics.copy()
                    ty.generic_args = generics.copy()
                    ty.custom_def.unit_id = unit.unit_id

                case AST.EnumDef(name=name, attrs=attrs):
                    # alloc in type space
                    type_id = self.__type_ctx.alloc_enum(name.name)

                    # alloc in symbol space
                    symbol_attrs = self.__convert_attrs(attrs)
                    symbol_id = unit.symbol_ctx.add_symbol(name.name, SymbolKind.Type, type_id, symbol_attrs)
                    if symbol_id is None:
                        raise AnalysisError(f"Duplicate symbol name: {name.name}", name.span)
                    symbol = unit.symbol_ctx.get(symbol_id)

                    generics = self.__alloc_generics(unit, item.generics)
                    ty = self.__type_ctx[symbol.type_id]
                    assert isinstance(ty, Type.EnumType)
                    ty.custom_def.generics = generics.copy()
                    ty.generic_args = generics.copy()

                case AST.TraitDef(name=name, attrs=attrs):
                    # alloc in type space
                    type_id = self.__type_ctx.alloc_trait(name.name)

                    # alloc in symbol space
                    symbol_attrs = self.__convert_attrs(attrs)
                    symbol_id = unit.symbol_ctx.add_symbol(name.name, SymbolKind.Type, type_id, symbol_attrs)
                    if symbol_id is None:
                        raise AnalysisError(f"Duplicate symbol name: {name.name}", name.span)
                    symbol = unit.symbol_ctx.get(symbol_id)

                    generics = self.__alloc_generics(unit, item.generics)
                    ty = self.__type_ctx[symbol.type_id]
                    assert isinstance(ty, Type.TraitType)
                    ty.custom_def.generics = generics.copy()
                    ty.generic_args = generics.copy()

                case _:
                    # other items are ignored in this pass
                    pass

    def __alloc_generics(self, unit: UnitData, item_generics: list[AST.GenericParam]) -> list[int]:
        """Allocate generic parameters from AST GenericParam list.

        Handles both TypeGenericParam (→ GenericType) and
        ConstGenericParam (→ ConstGenericType). Does NOT register
        symbols — that happens during __resolve_definitions.
        """
        generics: list[int] = []
        for param in item_generics:
            match param:
                case AST.TypeGenericParam(name=name):
                    generics.append(self.__type_ctx.alloc_generic(name.name))
                case AST.ConstGenericParam(name=name, value_type=vty):
                    vt_id = self.__type_ctx.resolve_type(vty, unit.symbol_ctx)
                    generics.append(self.__type_ctx.alloc_const_generic(name.name, vt_id))
        return generics

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
        target_path = unit.path.parent.joinpath(*paths).with_suffix(".an")
        return self.__path_lookup.get(target_path.resolve())

    def __resolve_definitions(self, unit: UnitData) -> None:
        """Resolves all definitions in the unit and updates the symbol context and type context with the resolved types."""
        for item in unit.items():
            match item:
                case AST.Alias():
                    self.__resolve_alias(unit, item)
                case AST.FuncDef():
                    self.__resolve_func_decl(unit, item)
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
        ty = self.__type_ctx[symbol.type_id]
        assert isinstance(ty, Type.AliasType)

        # resolve generics and aliased type
        self.__enter_generic_scope(unit, alias.generics, ty.custom_def.generics)

        aliased_type_id = self.__type_ctx.resolve_type(alias.target, unit.symbol_ctx)
        unit.symbol_ctx.exit_scope()

        # update the alias symbol with the resolved type
        ty.custom_def.aliased_type = aliased_type_id

    def __resolve_func_decl(self, unit: UnitData, func_def: AST.FuncDef) -> None:
        symbol = unit.symbol_ctx.lookup(func_def.name.name)
        assert symbol is not None
        ty = self.__type_ctx[symbol.type_id]
        assert isinstance(ty, Type.FunctionType)

        # resolve generics, parameters and return type
        self.__enter_generic_scope(unit, func_def.generics, ty.custom_def.generics)

        parameters = [
            Type.Parameter(
                name=param.name.name,
                type_id=self.__type_ctx.resolve_type(param.var_type, unit.symbol_ctx),
            )
            for param in func_def.params
        ]
        if func_def.ret_type is None:
            ret_type_id = self.__type_ctx.void_id
        else:
            ret_type_id = self.__type_ctx.resolve_type(func_def.ret_type, unit.symbol_ctx)
        unit.symbol_ctx.exit_scope()

        # update the function symbol with the resolved type
        ty.custom_def.parameters = parameters
        ty.custom_def.return_type = ret_type_id

        # add the resolved procedure to the type context
        self.__type_ctx.add_procedure(ty.type_id, func_def.body, unit.unit_id)

    def __enter_generic_scope(self, unit: UnitData, ast_generics: list[AST.GenericParam], ty_generic_ids: list[int]) -> None:
        """进入泛型作用域，注册类型泛型和常量泛型符号。"""
        unit.symbol_ctx.enter_scope()
        for param, ty_id in zip(ast_generics, ty_generic_ids):
            match param:
                case AST.TypeGenericParam(name=name):
                    unit.symbol_ctx.add_symbol(name.name, SymbolKind.Type, ty_id)
                case AST.ConstGenericParam(name=name):
                    unit.symbol_ctx.add_symbol(name.name, SymbolKind.ConstGeneric, ty_id)

    def __resolve_struct_def(self, unit: UnitData, struct_def: AST.StructDef) -> None:
        symbol = unit.symbol_ctx.lookup(struct_def.name.name)
        assert symbol is not None
        ty = self.__type_ctx[symbol.type_id]
        assert isinstance(ty, Type.StructType)

        # resolve generics and fields
        self.__enter_generic_scope(unit, struct_def.generics, ty.custom_def.generics)

        fields: list[Type.StructField] = []
        for index, field in enumerate(struct_def.fields):
            field_type_id = self.__type_ctx.resolve_type(field.field_type, unit.symbol_ctx)
            is_pub = any(attr.kind == AST.AttrKind.Pub for attr in field.attrs)
            fields.append(Type.StructField(
                name=field.name.name,
                type_id=field_type_id,
                access_mode=Type.AccessMode.Public if is_pub else Type.AccessMode.Private,
                index=index,
            ))
        unit.symbol_ctx.exit_scope()

        # update the struct symbol with the resolved type
        ty.custom_def.fields = fields

    def __resolve_enum_def(self, unit: UnitData, enum_def: AST.EnumDef) -> None:
        symbol = unit.symbol_ctx.lookup(enum_def.name.name)
        assert symbol is not None
        ty = self.__type_ctx[symbol.type_id]
        assert isinstance(ty, Type.EnumType)

        # resolve generics and variants
        self.__enter_generic_scope(unit, enum_def.generics, ty.custom_def.generics)

        variants: list[Type.EnumVariant] = []
        for index, variant in enumerate(enum_def.variants):
            payload_type_id = None
            if len(variant.fields) > 0:
                field_names = [field.name.name for field in variant.fields]
                field_types = [self.__type_ctx.resolve_type(field.var_type, unit.symbol_ctx) for field in variant.fields]
                payload_type_id = self.__type_ctx.alloc_unnamed_struct(symbol.name, field_names, field_types, generics=ty.custom_def.generics)
            variants.append(Type.EnumVariant(
                name=variant.name.name,
                payload_type=payload_type_id,
                discriminant=index,
            ))
        unit.symbol_ctx.exit_scope()

        # update the enum symbol with the resolved type
        ty.custom_def.variants = variants

    def __resolve_trait_def(self, unit: UnitData, trait_def: AST.TraitDef) -> None:
        symbol = unit.symbol_ctx.lookup(trait_def.name.name)
        assert symbol is not None
        ty = self.__type_ctx[symbol.type_id]
        assert isinstance(ty, Type.TraitType)

        # resolve generics and methods
        self.__enter_generic_scope(unit, trait_def.generics, ty.custom_def.generics)
        unit.symbol_ctx.add_symbol("Self", SymbolKind.Type, symbol.type_id)

        methods: dict[str, int] = {}
        for item in trait_def.items:
            match item:
                case AST.MethodDecl():
                    method_type_id = self.__resolve_method_decl(unit, item, ty.custom_def.generics, symbol.type_id, True)
                    method_name = item.name.name
                case AST.MethodDef():
                    method_type_id = self.__resolve_method_decl(unit, item.decl, ty.custom_def.generics, symbol.type_id, False)
                    method_name = item.decl.name.name
                    self.__type_ctx.add_procedure(method_type_id, item.body, unit.unit_id)
            methods[method_name] = method_type_id
        unit.symbol_ctx.exit_scope()

        # update the trait symbol with the resolved type
        ty.custom_def.methods = methods

    def __resolve_impl(self, unit: UnitData, impl: AST.Impl) -> None:
        # resolve generics, target type and trait
        unit.symbol_ctx.enter_scope()
        generics: list[int] = []
        for param in impl.generics:
            match param:
                case AST.TypeGenericParam(name=name):
                    g_id = self.__type_ctx.alloc_generic(name.name)
                    unit.symbol_ctx.add_symbol(name.name, SymbolKind.Type, g_id)
                case AST.ConstGenericParam(name=name, value_type=vty):
                    vt_id = self.__type_ctx.resolve_type(vty, unit.symbol_ctx)
                    g_id = self.__type_ctx.alloc_const_generic(name.name, vt_id)
                    unit.symbol_ctx.add_symbol(name.name, SymbolKind.ConstGeneric, g_id)
            generics.append(g_id)

        target_type_id = self.__type_ctx.resolve_type(impl.target, unit.symbol_ctx)
        unit.symbol_ctx.add_symbol("Self", SymbolKind.Type, target_type_id)

        trait_type_id = None
        if impl.trait is not None:
            trait_type_id = self.__type_ctx.resolve_type(impl.trait, unit.symbol_ctx)

        impl_obj = self.__type_ctx.register_impl(impl.span, generics, target_type_id, trait_type_id)

        for item in impl.items:
            method_id = self.__resolve_method_decl(unit, item.decl, generics, target_type_id, False)

            # add the resolved procedure to the type context
            self.__type_ctx.add_procedure(method_id, item.body, unit.unit_id)

            impl_obj.methods[item.decl.name.name] = method_id

        unit.symbol_ctx.exit_scope()

    def __resolve_method_decl(self, unit: UnitData, decl: AST.MethodDecl, prev_generics: list[int], receiver_type_id: int, is_header: bool) -> int:
        # alloc in type space
        type_id = self.__type_ctx.alloc_method(decl.name.name)

        # alloc in symbol space
        symbol_attrs = self.__convert_attrs(decl.attrs)
        symbol_id = unit.symbol_ctx.add_symbol(decl.name.name, SymbolKind.Function, type_id, symbol_attrs)
        if symbol_id is None:
            raise AnalysisError(f"Duplicate method name: {decl.name.name}", decl.name.span)
        symbol = unit.symbol_ctx.get(symbol_id)

        # resolve generics, parameters and return type
        unit.symbol_ctx.enter_scope()
        generics: list[int] = list(prev_generics)
        for param in decl.generics:
            match param:
                case AST.TypeGenericParam(name=name):
                    g_id = self.__type_ctx.alloc_generic(name.name)
                    unit.symbol_ctx.add_symbol(name.name, SymbolKind.Type, g_id)
                case AST.ConstGenericParam(name=name, value_type=vty):
                    vt_id = self.__type_ctx.resolve_type(vty, unit.symbol_ctx)
                    g_id = self.__type_ctx.alloc_const_generic(name.name, vt_id)
                    unit.symbol_ctx.add_symbol(name.name, SymbolKind.ConstGeneric, g_id)
            generics.append(g_id)

        parameters = [
            Type.Parameter(
                name=param.name.name,
                type_id=self.__type_ctx.resolve_type(param.var_type, unit.symbol_ctx),
            )
            for param in decl.params
        ]
        if decl.ret_type is None:
            ret_type_id = self.__type_ctx.void_id
        else:
            ret_type_id = self.__type_ctx.resolve_type(decl.ret_type, unit.symbol_ctx)
        unit.symbol_ctx.exit_scope()

        # update the method symbol with the resolved type
        ty = self.__type_ctx[symbol.type_id]
        assert isinstance(ty, Type.MethodType)
        ty.custom_def.generics = generics.copy()
        ty.custom_def.receiver_type = receiver_type_id
        ty.custom_def.parameters = parameters
        ty.custom_def.return_type = ret_type_id
        ty.custom_def.is_static = any(attr.kind == AST.AttrKind.Static for attr in decl.attrs)
        ty.custom_def.is_header = is_header
        ty.generic_args = generics.copy()

        return type_id
