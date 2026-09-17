"""
Resolves global all global items (functions, types, etc.) and imports.

This is the first pass of the analysis phase.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import TYPE_CHECKING

from compiler.analysis.error import AnalysisError, UnfilledAliasError
from compiler.analysis.package_map import PackageMap
from compiler.analysis.source_provenance import default_stdlib_root
from compiler.analysis.symbol.symbol import SymbolAttribute, SymbolKind
from compiler.analysis.ty import ty as Type
from compiler.frontend.lex.position import SrcSpan
from compiler.frontend.parse import ast as AST

if TYPE_CHECKING:
    from compiler.analysis.ty.context import TypeCtx
    from compiler.analysis.unit.unit_data import UnitData


class GlobalResolve:
    def __init__(self, units: dict[int, UnitData], type_ctx: TypeCtx,
                 packages: PackageMap | None = None,
                 stdlib_root: Path | None = None) -> None:
        self.__units = units
        self.__type_ctx = type_ctx

        self.__path_lookup: dict[Path, UnitData] = {unit.path.resolve(): unit for unit in units.values()}
        self.__std_lookup: dict[tuple[str, ...], UnitData] = {}
        self.__stdlib_root = (stdlib_root or default_stdlib_root()).resolve()

        self.__packages = packages
        self.__strict_pkg = packages is not None

        self.__build_std_lookup()

    def run(self) -> None:
        for unit in self.__units.values():
            self.__collect_symbols(unit)

        for unit in self.__units.values():
            self.__resolve_imports(unit)

        # Alias bodies must be resolved before any declaration signature is:
        # a signature may name an alias declared later in the same unit or in
        # another unit, and resolve_type collapses alias chains eagerly.
        self.__resolve_aliases()

        for unit in self.__units.values():
            self.__resolve_definitions(unit)

        self.__type_ctx.check_impls()

    def __build_std_lookup(self) -> None:
        for unit in self.__units.values():
            if not unit.is_stdlib:
                continue
            try:
                relative = unit.path.resolve().relative_to(self.__stdlib_root)
            except ValueError:
                continue
            rel_parts = relative.parts
            if not rel_parts:
                continue
            key = ("std",) + rel_parts[:-1] + (unit.path.stem,)
            self.__std_lookup[key] = unit

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
                case AST.Alias(name=name, attrs=attrs, span=span):
                    # alloc in type space
                    type_id = self.__type_ctx.alloc_alias(name.name, span)

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

                case AST.FuncDef(name=name, attrs=attrs, span=span):
                    # alloc in type space
                    type_id = self.__type_ctx.alloc_function(name.name, span)

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

                case AST.StructDef(name=name, attrs=attrs, span=span):
                    # alloc in type space
                    type_id = self.__type_ctx.alloc_struct(name.name, span)

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

                case AST.EnumDef(name=name, attrs=attrs, span=span):
                    # alloc in type space
                    type_id = self.__type_ctx.alloc_enum(name.name, span)

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
                    ty.custom_def.unit_id = unit.unit_id

                case AST.TraitDef(name=name, attrs=attrs, span=span):
                    # alloc in type space
                    type_id = self.__type_ctx.alloc_trait(name.name, span)

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
            target_unit = self.__resolve_import_path(unit, paths, item.span)

            target_symbol = target_unit.symbol_ctx.lookup_exportable(item.target.name)
            if target_symbol is None:
                if target_unit.symbol_ctx.lookup_global(item.target.name) is not None:
                    raise AnalysisError(
                        f"Symbol '{item.target.name}' exists in the imported unit but is not declared 'pub'",
                        item.target.span,
                    )
                raise AnalysisError(f"Symbol '{item.target.name}' is not found in the imported unit", item.target.span)
            if target_symbol.kind == SymbolKind.Variable:
                raise AnalysisError(f"Cannot import variable '{item.target.name}'", item.target.span)

            imported_name = item.alias.name if item.alias is not None else item.target.name
            unit.symbol_ctx.add_symbol(imported_name, target_symbol.kind, target_symbol.type_id)

    def __resolve_import_path(self, unit: UnitData, paths: list[str], span: SrcSpan) -> UnitData:
        """Resolve an import path to a UnitData, or raise with a diagnostic.

        - Package mode (``--packages``): the first segment must name a package
          visible to the importer, and the remaining segments must resolve to an
          existing ``.an`` file that is not a package entry.
        - Standalone mode: stdlib lookup plus relative resolution against the
          importing file's directory.
        """
        if len(paths) == 0:
            raise AnalysisError("Cannot resolve import path: <empty>", span)

        if self.__packages is not None:
            return self.__resolve_package_import(unit, paths, span)

        if paths[0] == "std":
            target = self.__std_lookup.get(tuple(paths))
            if target is None:
                raise AnalysisError(f"Cannot resolve import path: {'.'.join(paths)}", span)
            return target

        target_path = unit.path.parent.joinpath(*paths).with_suffix(".an")
        target = self.__path_lookup.get(target_path.resolve())
        if target is None:
            raise AnalysisError(f"Cannot resolve import path: {'.'.join(paths)}", span)
        return target

    def __resolve_package_import(self, unit: UnitData, paths: list[str], span: SrcSpan) -> UnitData:
        """Package-mode import resolution and its AX009/AX010/AX012/AX014 diagnostics."""
        packages = self.__packages
        assert packages is not None

        first = paths[0]
        spec = packages.packages.get(first)
        if spec is None:
            raise AnalysisError(
                f"error[AX012]: unknown package '{first}' in import 'from {'.'.join(paths)} import ...'",
                span,
            )

        importer = packages.package_of(unit.path)
        if first not in packages.visible_from(importer):
            where = importer if importer is not None else "a file outside every package"
            raise AnalysisError(
                f"error[AX009]: package '{first}' is not a dependency of {where}; "
                f"declare it in that package's [dependencies]",
                span,
            )

        # A directory is not a module: the path must end in a .an file.
        if len(paths) == 1:
            raise AnalysisError(
                f"error[AX010]: 'from {first} import ...' names a package, not a module; "
                f"write 'from {first}.<module> import ...'",
                span,
            )

        target_path = spec.source_root.joinpath(*paths[1:]).with_suffix(".an")
        target = self.__path_lookup.get(target_path.resolve())
        if target is None:
            raise AnalysisError(
                f"error[AX010]: import 'from {'.'.join(paths)} import ...' does not "
                f"resolve to a .an file under {spec.source_root}",
                span,
            )

        if target.path.resolve() in packages.entry_paths():
            raise AnalysisError(
                f"error[AX014]: '{'.'.join(paths)}' is the entry module of package "
                f"'{first}' and cannot be imported",
                span,
            )
        self.__warn_if_shadowed_directory(importer, first, span)
        return target

    def __warn_if_shadowed_directory(self, importer: str | None, first: str, span: SrcSpan) -> None:
        """G13: the package-name segment always wins over a same-named directory.

        ``from dup.foo import x`` resolves to package ``dup``, so a local
        ``src/dup/foo.an`` can never be imported. That is a consequence of the
        rule rather than a defect, so it is reported as a warning, not an error.
        """
        packages = self.__packages
        if packages is None or importer is None or importer == first:
            return
        spec = packages.packages.get(importer)
        if spec is None:
            return
        shadowed = spec.source_root / first
        if not shadowed.is_dir():
            return
        print(
            f"warning: '{first}' resolves to package '{first}', so {shadowed} of "
            f"package '{importer}' is not importable; rename the directory or the "
            f"dependency ({span.path}:{span.start.row + 1})",
            file=sys.stderr,
        )

    def __resolve_definitions(self, unit: UnitData) -> None:
        """Resolves all definitions in the unit and updates the symbol context and type context with the resolved types."""
        for item in unit.items():
            match item:
                case AST.Alias():
                    # Resolved by __resolve_aliases before this pass runs.
                    pass
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

    def __resolve_aliases(self) -> None:
        """Fill in every alias body, retrying the ones that depend on later aliases.

        An alias body may name an alias declared later in the same unit or in
        another unit, and it may carry an alias as a generic argument
        (``typedef PairOf = Pair<Meters>``).  Resolution therefore repeats until
        a whole round makes no progress; whatever is still pending then forms a
        cycle (or names something that does not exist) and stays unfilled, which
        surfaces as an error the first time it is used.
        """
        pending: list[tuple[UnitData, AST.Alias]] = [
            (unit, item)
            for unit in self.__units.values()
            for item in unit.items()
            if isinstance(item, AST.Alias)
        ]
        while pending:
            remaining: list[tuple[UnitData, AST.Alias]] = []
            for unit, item in pending:
                if not self.__resolve_alias(unit, item):
                    remaining.append((unit, item))
            if len(remaining) == len(pending):
                break
            pending = remaining

    def __resolve_alias(self, unit: UnitData, alias: AST.Alias) -> bool:
        """Fill in one alias body; return False when it must be retried later."""
        symbol = unit.symbol_ctx.lookup(alias.name.name)
        assert symbol is not None
        ty = self.__type_ctx[symbol.type_id]
        assert isinstance(ty, Type.AliasType)

        # resolve generics and aliased type
        self.__enter_generic_scope(unit, alias.generics, ty.custom_def.generics)

        try:
            aliased_type_id = self.__type_ctx.resolve_type(alias.target, unit.symbol_ctx)
        except UnfilledAliasError:
            return False
        finally:
            unit.symbol_ctx.exit_scope()

        # update the alias symbol with the resolved type
        ty.custom_def.aliased_type = aliased_type_id
        return True

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
                payload_type_id = self.__type_ctx.alloc_unnamed_struct(symbol.name, field_names, field_types, generics=ty.custom_def.generics, span=variant.span)
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

        conditions: dict[int, list[int]] = {}
        for param_name, trait_types in impl.conditions:
            symbol = unit.symbol_ctx.lookup(param_name.name)
            assert symbol is not None, f"condition parameter '{param_name.name}' not found"
            generic_id = symbol.type_id
            conditions[generic_id] = [self.__type_ctx.resolve_type(tt, unit.symbol_ctx) for tt in trait_types]

        impl_obj = self.__type_ctx.register_impl(impl.span, generics, target_type_id, trait_type_id, conditions)

        for item in impl.items:
            method_id = self.__resolve_method_decl(unit, item.decl, generics, target_type_id, False)

            # add the resolved procedure to the type context
            self.__type_ctx.add_procedure(method_id, item.body, unit.unit_id)

            impl_obj.methods[item.decl.name.name] = method_id

        unit.symbol_ctx.exit_scope()

    def __resolve_method_decl(self, unit: UnitData, decl: AST.MethodDecl, prev_generics: list[int], receiver_type_id: int, is_header: bool) -> int:
        # alloc in type space
        type_id = self.__type_ctx.alloc_method(decl.name.name, span=decl.span)

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
