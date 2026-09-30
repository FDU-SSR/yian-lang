"""
Resolves global all global items (functions, types, etc.) and imports.

This is the first pass of the analysis phase.
"""
from __future__ import annotations

import sys
from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING

from compiler.analysis.state import SemanticState
from compiler.analysis.error import AnalysisError
from compiler.analysis.resolution.enum_equality import build_unit_enum_equality_body
from compiler.analysis.source_provenance import default_stdlib_root
from compiler.analysis.symbol.symbol import AliasSymbol, SymbolAttribute, SymbolKind
from compiler.analysis.ty import ty as Type
from compiler.frontend.lex.position import SrcSpan
from compiler.frontend.parse import ast as AST

if TYPE_CHECKING:
    from compiler.analysis.unit.unit_data import UnitData


class GlobalResolve:
    def __init__(self, ctx: SemanticState) -> None:
        self.__ctx = ctx

        self.__path_lookup: dict[Path, UnitData] = {
            unit.path.resolve(): unit for unit in ctx.unit_datas.values()
        }
        self.__std_lookup: dict[tuple[str, ...], UnitData] = {}
        self.__stdlib_root = (ctx.stdlib_root or default_stdlib_root()).resolve()

        self.__strict_pkg = ctx.packages is not None
        self.__const_annotations: list[tuple[UnitData, list[AST.GenericParam], list[int]]] = []

        # Resolution is the only place that knows how an import actually
        # resolved, so record the edges here for the declaration index.
        self.__import_edges: dict[int, list[int]] = {}

        self.__build_std_lookup()

    def run(self) -> None:
        for unit in self.__ctx.unit_datas.values():
            self.__check_ffi_declarations(unit)
        for unit in self.__ctx.unit_datas.values():
            self.__collect_symbols(unit)

        for unit in self.__ctx.unit_datas.values():
            self.__resolve_imports(unit)

        # Alias scopes contain all declarations and imported name bindings.
        for unit in self.__ctx.unit_datas.values():
            for item in unit.items():
                if isinstance(item, AST.Alias):
                    symbol = unit.symbol_ctx.lookup(item.name.name)
                    if isinstance(symbol, AliasSymbol):
                        self.__ctx.aliases.bind_scope(symbol.alias_id, unit.symbol_ctx)

        for unit, parameters, generic_ids in self.__const_annotations:
            scope = unit.symbol_ctx.clone()
            scope.enter_scope()
            for parameter, generic_id in zip(parameters, generic_ids):
                kind = SymbolKind.ConstGeneric if isinstance(parameter, AST.ConstGenericParam) else SymbolKind.Type
                if scope.add_symbol(parameter.name.name, kind, generic_id, span=parameter.name.span) is None:
                    raise AnalysisError(f"Duplicate generic parameter: {parameter.name.name}", parameter.name.span)
            for parameter, generic_id in zip(parameters, generic_ids):
                if isinstance(parameter, AST.ConstGenericParam):
                    generic = self.__ctx.type_ctx[generic_id]
                    assert isinstance(generic, Type.ConstGenericType)
                    generic.value_type = self.__ctx.resolve_type_in(parameter.value_type, scope)
            scope.exit_scope()
        self.__ctx.aliases.resolve_all()

        for unit in self.__ctx.unit_datas.values():
            self.__resolve_definitions(unit)

        self.__ctx.evaluate_constants()

        for source_id, target_id in self.__ctx.type_ctx.check_impls():
            self.__ctx.procedures.copy(source_id, target_id)

    def import_edges(self) -> dict[int, tuple[int, ...]]:
        """Resolved import edges: unit id → unit ids it imports (deduplicated)."""
        return {
            unit_id: tuple(sorted(set(targets)))
            for unit_id, targets in self.__import_edges.items()
        }

    def __build_std_lookup(self) -> None:
        for unit in self.__ctx.unit_datas.values():
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
                case AST.AttrKind.PubFfi:
                    res.add(SymbolAttribute.FfiPublic)
                case _:
                    continue
        return res

    def __check_ffi_declarations(self, unit: UnitData) -> None:
        for item in unit.items():
            match item:
                case AST.FuncDef() | AST.Alias() | AST.ConstDef() | AST.StructDef() | AST.EnumDef() | AST.TraitDef() | AST.OpaqueTypeDef() | AST.ExternBlock():
                    attrs = item.attrs
                case _:
                    attrs = []
            if len({attr.kind for attr in attrs}) != len(attrs):
                raise AnalysisError("Duplicate declaration modifier", item.span)
            if any(attr.kind in (AST.AttrKind.Ffi, AST.AttrKind.PubFfi) for attr in attrs) and not unit.allows_ffi:
                raise AnalysisError("FFI requires package ffi = true or --allow-ffi", item.span)
            if isinstance(item, (AST.ExternBlock, AST.OpaqueTypeDef)) and not unit.allows_ffi:
                raise AnalysisError("FFI declaration requires package ffi = true or --allow-ffi", item.span)
            if AST.AttrKind.PubFfi in {attr.kind for attr in attrs} and AST.AttrKind.Pub in {attr.kind for attr in attrs}:
                raise AnalysisError("Use either pub or pub(ffi)", item.span)
            if isinstance(item, AST.FuncDef):
                if AST.AttrKind.Static in {attr.kind for attr in attrs}:
                    raise AnalysisError("Top-level function cannot be static", item.span)
                if AST.AttrKind.PubFfi in {attr.kind for attr in attrs} and AST.AttrKind.Ffi not in {attr.kind for attr in attrs}:
                    raise AnalysisError("pub(ffi) function must be ffi fn", item.span)
            elif isinstance(item, (AST.ExternBlock, AST.OpaqueTypeDef)):
                if any(attr.kind in (AST.AttrKind.Ffi, AST.AttrKind.Static, AST.AttrKind.Pub) for attr in attrs):
                    raise AnalysisError("Invalid modifier on FFI declaration", item.span)
            elif any(attr.kind in (AST.AttrKind.Ffi, AST.AttrKind.PubFfi) for attr in attrs):
                raise AnalysisError("FFI modifier is only valid on functions or FFI declarations", item.span)

    def __collect_symbols(self, unit: UnitData) -> None:
        """Collects all global symbols in the unit."""
        for item in unit.items():
            match item:
                case AST.OpaqueTypeDef(name=name, attrs=attrs, span=span):
                    type_id = self.__ctx.type_ctx.alloc_opaque(name.name, span)
                    if unit.symbol_ctx.add_symbol(name.name, SymbolKind.Type, type_id, self.__convert_attrs(attrs), name.span) is None:
                        raise AnalysisError(f"Duplicate symbol name: {name.name}", name.span)
                case AST.ExternBlock(functions=functions, attrs=attrs):
                    for function in functions:
                        type_id = self.__ctx.type_ctx.alloc_function(function.name.name, function.span)
                        if unit.symbol_ctx.add_symbol(function.name.name, SymbolKind.Function, type_id, self.__convert_attrs(attrs), function.name.span) is None:
                            raise AnalysisError(f"Duplicate symbol name: {function.name.name}", function.name.span)
                case AST.Alias(name=name, attrs=attrs, span=span):
                    generics = self.__alloc_generics(unit, item.generics)
                    alias_id = self.__ctx.aliases.declare(item, generics)
                    symbol_id = unit.symbol_ctx.add_alias(name.name, alias_id, self.__convert_attrs(attrs), name.span)
                    if symbol_id is None:
                        raise AnalysisError(f"Duplicate symbol name: {name.name}", name.span)

                case AST.FuncDef(name=name, attrs=attrs, span=span):
                    # alloc in type space
                    type_id = self.__ctx.type_ctx.alloc_function(name.name, span)

                    # alloc in symbol space
                    symbol_attrs = self.__convert_attrs(attrs)
                    symbol_id = unit.symbol_ctx.add_symbol(name.name, SymbolKind.Function, type_id, symbol_attrs, name.span)
                    if symbol_id is None:
                        raise AnalysisError(f"Duplicate symbol name: {name.name}", name.span)
                    symbol = unit.symbol_ctx.get_typed(symbol_id)

                    generics = self.__alloc_generics(unit, item.generics)
                    ty = self.__ctx.type_ctx[symbol.type_id]
                    assert isinstance(ty, Type.FunctionType)
                    self.__ctx.type_ctx.bind_template(symbol.type_id, generics)

                case AST.ConstDef(name=name, attrs=attrs):
                    symbol_attrs = self.__convert_attrs(attrs)
                    symbol_id = unit.symbol_ctx.add_symbol(
                        name.name,
                        SymbolKind.Constant,
                        self.__ctx.type_ctx.error_id,
                        symbol_attrs,
                        name.span,
                    )
                    if symbol_id is None:
                        raise AnalysisError(f"Duplicate symbol name: {name.name}", name.span)
                    symbol = unit.symbol_ctx.get_typed(symbol_id)
                    symbol.const_origin = (unit.unit_id, symbol_id)
                    self.__ctx.register_constant(unit.unit_id, symbol_id, item)

                case AST.StructDef(name=name, attrs=attrs, span=span):
                    # alloc in type space
                    type_id = self.__ctx.type_ctx.alloc_struct(name.name, span)

                    # alloc in symbol space
                    symbol_attrs = self.__convert_attrs(attrs)
                    symbol_id = unit.symbol_ctx.add_symbol(name.name, SymbolKind.Type, type_id, symbol_attrs, name.span)
                    if symbol_id is None:
                        raise AnalysisError(f"Duplicate symbol name: {name.name}", name.span)
                    symbol = unit.symbol_ctx.get_typed(symbol_id)

                    generics = self.__alloc_generics(unit, item.generics)
                    ty = self.__ctx.type_ctx[symbol.type_id]
                    assert isinstance(ty, Type.StructType)
                    self.__ctx.type_ctx.bind_template(symbol.type_id, generics)
                    ty.custom_def.unit_id = unit.unit_id

                case AST.EnumDef(name=name, attrs=attrs, span=span):
                    # alloc in type space
                    type_id = self.__ctx.type_ctx.alloc_enum(name.name, span)

                    # alloc in symbol space
                    symbol_attrs = self.__convert_attrs(attrs)
                    symbol_id = unit.symbol_ctx.add_symbol(name.name, SymbolKind.Type, type_id, symbol_attrs, name.span)
                    if symbol_id is None:
                        raise AnalysisError(f"Duplicate symbol name: {name.name}", name.span)
                    symbol = unit.symbol_ctx.get_typed(symbol_id)

                    generics = self.__alloc_generics(unit, item.generics)
                    ty = self.__ctx.type_ctx[symbol.type_id]
                    assert isinstance(ty, Type.EnumType)
                    self.__ctx.type_ctx.bind_template(symbol.type_id, generics)
                    ty.custom_def.unit_id = unit.unit_id

                case AST.TraitDef(name=name, attrs=attrs, span=span):
                    # alloc in type space
                    type_id = self.__ctx.type_ctx.alloc_trait(name.name, span)

                    # alloc in symbol space
                    symbol_attrs = self.__convert_attrs(attrs)
                    symbol_id = unit.symbol_ctx.add_symbol(name.name, SymbolKind.Type, type_id, symbol_attrs, name.span)
                    if symbol_id is None:
                        raise AnalysisError(f"Duplicate symbol name: {name.name}", name.span)
                    symbol = unit.symbol_ctx.get_typed(symbol_id)

                    generics = self.__alloc_generics(unit, item.generics)
                    ty = self.__ctx.type_ctx[symbol.type_id]
                    assert isinstance(ty, Type.TraitType)
                    self.__ctx.type_ctx.bind_template(symbol.type_id, generics)

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
                    generics.append(self.__ctx.type_ctx.alloc_generic(name.name))
                case AST.ConstGenericParam(name=name):
                    generic_id = self.__ctx.type_ctx.alloc_const_generic(name.name, -1)
                    generics.append(generic_id)
        if any(isinstance(param, AST.ConstGenericParam) for param in item_generics):
            self.__const_annotations.append((unit, item_generics, generics))
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
            if SymbolAttribute.FfiPublic in target_symbol.attributes and not unit.allows_ffi:
                raise AnalysisError(f"Symbol '{item.target.name}' requires FFI permission", item.target.span)

            imported_name = item.alias.name if item.alias is not None else item.target.name
            import_span = item.alias.span if item.alias is not None else item.target.span
            if isinstance(target_symbol, AliasSymbol):
                unit.symbol_ctx.add_alias(imported_name, target_symbol.alias_id, set(), import_span)
                target_type = None
            else:
                unit.symbol_ctx.add_symbol(
                    imported_name, target_symbol.kind, target_symbol.type_id,
                    span=import_span, const_origin=target_symbol.const_origin,
                )
                target_type = target_symbol.type_id
            # The written name is a resolved reference of its own, in both forms:
            # for `import A` it is the name that is bound, and for `import A as B`
            # the original spelling of `A` appears nowhere else in the file.  An
            # editor needs it to rename `A` without leaving the import behind.
            self.__ctx.names.record(item.target.span, target_symbol, target_type)
            self.__import_edges.setdefault(unit.unit_id, []).append(target_unit.unit_id)

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

        if self.__ctx.packages is not None:
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
        packages = self.__ctx.packages
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
        """The package-name segment takes precedence over a same-named directory.

        For example, ``from dup.foo import x`` resolves ``dup`` as a package
        name before considering a local ``src/dup`` directory. The resolver
        reports that shadowing as a warning.
        """
        packages = self.__ctx.packages
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
            if not isinstance(item, AST.Alias):
                continue
            try:
                self.__resolve_alias(unit, item)
            except AnalysisError:
                pass

        for item in unit.items():
            match item:
                case AST.StructDef():
                    self.__resolve_struct_def(unit, item)
                case AST.EnumDef():
                    self.__resolve_enum_def(unit, item)
                case AST.TraitDef():
                    self.__resolve_trait_def(unit, item)
                case _:
                    pass

        for item in unit.items():
            match item:
                case AST.ExternBlock():
                    for function in item.functions:
                        self.__resolve_extern_decl(unit, function)
                case AST.FuncDef():
                    self.__resolve_func_decl(unit, item)
                case AST.Impl():
                    self.__resolve_impl(unit, item)
                case _:
                    # other items are ignored in this pass
                    pass

    def __resolve_alias(self, unit: UnitData, alias: AST.Alias) -> None:
        """Validate the declaration's transparent target template."""
        symbol = unit.symbol_ctx.lookup(alias.name.name)
        assert isinstance(symbol, AliasSymbol)
        self.__ctx.aliases.template(symbol.alias_id)

    def __resolve_func_decl(self, unit: UnitData, func_def: AST.FuncDef) -> None:
        symbol = unit.symbol_ctx.lookup_typed(func_def.name.name)
        assert symbol is not None
        ty = self.__ctx.type_ctx[symbol.type_id]
        assert isinstance(ty, Type.FunctionType)

        # resolve generics, parameters and return type
        self.__enter_generic_scope(unit, func_def.generics, ty.custom_def.generics)

        parameters: list[Type.Parameter] = []
        for param in func_def.params:
            assert isinstance(param, AST.VarInfo)
            parameters.append(Type.Parameter(
                name=param.name.name,
                type_id=self.__ctx.resolve_type_in(param.var_type, unit.symbol_ctx),
                span=None if param.name.synthetic else param.name.span,
            ))
        if func_def.ret_type is None:
            ret_type_id = self.__ctx.type_ctx.void_id
        else:
            ret_type_id = self.__ctx.resolve_type_in(func_def.ret_type, unit.symbol_ctx)
        unit.symbol_ctx.exit_scope()

        # update the function symbol with the resolved type
        ty.custom_def.parameters = parameters
        ty.custom_def.return_type = ret_type_id
        self.__check_sized_signature(parameters, ret_type_id, func_def.span)
        ty.custom_def.is_ffi = any(attr.kind == AST.AttrKind.Ffi for attr in func_def.attrs)
        ty.custom_def.ffi_only = any(attr.kind == AST.AttrKind.PubFfi for attr in func_def.attrs)
        public = any(attr.kind == AST.AttrKind.Pub for attr in func_def.attrs)
        if not ty.custom_def.is_ffi or public:
            self.__check_yian_signature(parameters, ret_type_id, func_def.span)

        # Keep the resolved body associated with its callable definition.
        self.__ctx.procedures.register(ty.type_id, func_def.body, unit.unit_id)

    def __resolve_extern_decl(self, unit: UnitData, decl: AST.ExternFuncDecl) -> None:
        symbol = unit.symbol_ctx.lookup_typed(decl.name.name)
        assert symbol is not None
        ty = self.__ctx.type_ctx[symbol.type_id]
        assert isinstance(ty, Type.FunctionType)
        ty.custom_def.parameters = [
            Type.Parameter(name=param.name.name,
                           type_id=self.__ctx.resolve_type_in(param.var_type, unit.symbol_ctx),
                           span=param.name.span)
            for param in decl.params
        ]
        ty.custom_def.return_type = (
            self.__ctx.type_ctx.void_id if decl.ret_type is None
            else self.__ctx.resolve_type_in(decl.ret_type, unit.symbol_ctx)
        )
        self.__check_sized_signature(ty.custom_def.parameters, ty.custom_def.return_type, decl.span)
        for parameter in ty.custom_def.parameters:
            if not self.__ctx.type_ctx.is_c_abi_type(parameter.type_id):
                raise AnalysisError("extern C parameters require C ABI scalar or cptr<T>", decl.span)
        if not self.__ctx.type_ctx.is_c_abi_type(ty.custom_def.return_type, result=True):
            raise AnalysisError("extern C return type requires C ABI scalar, cptr<T>, or void", decl.span)
        ty.custom_def.is_extern = True

    def __check_yian_signature(self, parameters: list[Type.Parameter], result: int, span: SrcSpan) -> None:
        if any(self.__ctx.type_ctx.contains_ffi_type(item.type_id) for item in parameters) \
                or self.__ctx.type_ctx.contains_ffi_type(result):
            raise AnalysisError("C ABI types require a private or pub(ffi) ffi fn signature", span)

    def __check_sized_signature(self, parameters: list[Type.Parameter], result: int, span: SrcSpan) -> None:
        if any(self.__ctx.type_ctx.contains_bare_opaque(item.type_id) for item in parameters) \
                or self.__ctx.type_ctx.contains_bare_opaque(result):
            raise AnalysisError("opaque C type must be used through cptr<T>", span)

    def __enter_generic_scope(self, unit: UnitData, ast_generics: list[AST.GenericParam], ty_generic_ids: Sequence[int]) -> None:
        """进入泛型作用域，注册类型泛型和常量泛型符号。"""
        unit.symbol_ctx.enter_scope()
        for param, ty_id in zip(ast_generics, ty_generic_ids):
            match param:
                case AST.TypeGenericParam(name=name):
                    unit.symbol_ctx.add_symbol(name.name, SymbolKind.Type, ty_id, span=name.span)
                case AST.ConstGenericParam(name=name):
                    unit.symbol_ctx.add_symbol(name.name, SymbolKind.ConstGeneric, ty_id, span=name.span)

    def __resolve_struct_def(self, unit: UnitData, struct_def: AST.StructDef) -> None:
        symbol = unit.symbol_ctx.lookup_typed(struct_def.name.name)
        assert symbol is not None
        ty = self.__ctx.type_ctx[symbol.type_id]
        assert isinstance(ty, Type.StructType)

        # resolve generics and fields
        self.__enter_generic_scope(unit, struct_def.generics, ty.custom_def.generics)

        fields: list[Type.StructField] = []
        for index, field in enumerate(struct_def.fields):
            field_modifiers = [attr.kind for attr in field.attrs]
            if len(set(field_modifiers)) != len(field_modifiers) or any(
                kind != AST.AttrKind.Pub for kind in field_modifiers
            ):
                raise AnalysisError("Invalid struct field modifier", field.span)
            field_type_id = self.__ctx.resolve_type_in(field.field_type, unit.symbol_ctx)
            if self.__ctx.type_ctx.contains_bare_opaque(field_type_id):
                raise AnalysisError("opaque C type must be used through cptr<T>", field.span)
            is_pub = any(attr.kind == AST.AttrKind.Pub for attr in field.attrs)
            if is_pub and any(attr.kind == AST.AttrKind.Pub for attr in struct_def.attrs) \
                    and self.__ctx.type_ctx.contains_ffi_type(field_type_id):
                raise AnalysisError("Public struct field cannot expose a C ABI type", field.span)
            fields.append(Type.StructField(
                name=field.name.name,
                type_id=field_type_id,
                access_mode=Type.AccessMode.Public if is_pub else Type.AccessMode.Private,
                index=index,
                span=field.name.span,
            ))
        unit.symbol_ctx.exit_scope()

        # update the struct symbol with the resolved type
        ty.custom_def.fields = fields

    def __resolve_enum_def(self, unit: UnitData, enum_def: AST.EnumDef) -> None:
        symbol = unit.symbol_ctx.lookup_typed(enum_def.name.name)
        assert symbol is not None
        ty = self.__ctx.type_ctx[symbol.type_id]
        assert isinstance(ty, Type.EnumType)

        # resolve generics and variants
        self.__enter_generic_scope(unit, enum_def.generics, ty.custom_def.generics)

        variants: list[Type.EnumVariant] = []
        for index, variant in enumerate(enum_def.variants):
            payload_type_id = None
            if len(variant.fields) > 0:
                field_names = [field.name.name for field in variant.fields]
                field_types = [self.__ctx.resolve_type_in(field.var_type, unit.symbol_ctx) for field in variant.fields]
                if any(self.__ctx.type_ctx.contains_bare_opaque(field_type) for field_type in field_types):
                    raise AnalysisError("opaque C type must be used through cptr<T>", variant.span)
                if any(attr.kind == AST.AttrKind.Pub for attr in enum_def.attrs) and any(
                    self.__ctx.type_ctx.contains_ffi_type(field_type) for field_type in field_types
                ):
                    raise AnalysisError("Public enum variant cannot expose a C ABI type", variant.span)
                # The payload's fields are written in the variant declaration, so
                # their name spans are real source positions.
                field_spans = [field.name.span for field in variant.fields]
                payload_type_id = self.__ctx.type_ctx.alloc_unnamed_struct(symbol.name, field_names, field_types, generics=ty.custom_def.generics, span=variant.span, field_spans=field_spans)
            variants.append(Type.EnumVariant(
                name=variant.name.name,
                payload_type=payload_type_id,
                discriminant=index,
                span=variant.name.span,
            ))
        unit.symbol_ctx.exit_scope()

        # update the enum symbol with the resolved type
        ty.custom_def.variants = variants
        if all(variant.payload_type is None for variant in variants):
            self.__register_unit_enum_equality(unit, symbol.type_id, ty)

    def __register_unit_enum_equality(self, unit: UnitData, enum_type_id: int, enum_type: Type.EnumType) -> None:
        types = self.__ctx.type_ctx
        trait_id = types.alloc_instance(types.partial_eq_id, [enum_type_id])
        impl = types.register_impl(enum_type.custom_def.span, list(enum_type.custom_def.generics),
                                   enum_type_id, trait_id, automatic=True)
        method_id = types.alloc_method("eq", enum_type.custom_def.span)
        method = types[method_id]
        assert isinstance(method, Type.MethodType)
        self.__ctx.type_ctx.bind_template(method_id, enum_type.custom_def.generics)
        method.custom_def.receiver_type = enum_type_id
        method.custom_def.parameters = [Type.Parameter("other", types.alloc_ref(enum_type_id))]
        method.custom_def.return_type = types.bool_id
        method.custom_def.is_header = False
        impl.methods["eq"] = method_id
        self.__ctx.procedures.register(method_id, build_unit_enum_equality_body(enum_type), unit.unit_id)

    def __resolve_trait_def(self, unit: UnitData, trait_def: AST.TraitDef) -> None:
        symbol = unit.symbol_ctx.lookup_typed(trait_def.name.name)
        assert symbol is not None
        ty = self.__ctx.type_ctx[symbol.type_id]
        assert isinstance(ty, Type.TraitType)

        # resolve generics and methods
        self.__enter_generic_scope(unit, trait_def.generics, ty.custom_def.generics)
        self_type_id = self.__ctx.type_ctx.alloc_self_type(symbol.type_id)
        unit.symbol_ctx.add_symbol("Self", SymbolKind.Type, self_type_id)

        methods: dict[str, int] = {}
        for item in trait_def.items:
            match item:
                case AST.MethodDecl():
                    method_type_id = self.__resolve_method_decl(unit, item, ty.custom_def.generics, self_type_id, True)
                    method_name = item.name.name
                case AST.MethodDef():
                    method_type_id = self.__resolve_method_decl(unit, item.decl, ty.custom_def.generics, self_type_id, False)
                    method_name = item.decl.name.name
                    self.__ctx.procedures.register(method_type_id, item.body, unit.unit_id)
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
                    g_id = self.__ctx.type_ctx.alloc_generic(name.name)
                    unit.symbol_ctx.add_symbol(name.name, SymbolKind.Type, g_id, span=name.span)
                case AST.ConstGenericParam(name=name, value_type=vty):
                    vt_id = self.__ctx.resolve_type_in(vty, unit.symbol_ctx)
                    g_id = self.__ctx.type_ctx.alloc_const_generic(name.name, vt_id)
                    unit.symbol_ctx.add_symbol(name.name, SymbolKind.ConstGeneric, g_id, span=name.span)
            generics.append(g_id)

        target_type_id = self.__ctx.resolve_type_in(impl.target, unit.symbol_ctx)
        unit.symbol_ctx.add_symbol("Self", SymbolKind.Type, target_type_id)

        trait_type_id = None
        if impl.trait is not None:
            trait_type_id = self.__ctx.resolve_type_in(impl.trait, unit.symbol_ctx)

        conditions: dict[int, list[int]] = {}
        for param_name, trait_types in impl.conditions:
            symbol = unit.symbol_ctx.lookup_typed(param_name.name)
            assert symbol is not None, f"condition parameter '{param_name.name}' not found"
            generic_id = symbol.type_id
            conditions[generic_id] = [self.__ctx.resolve_type_in(tt, unit.symbol_ctx) for tt in trait_types]

        impl_obj = self.__ctx.type_ctx.register_impl(impl.span, generics, target_type_id, trait_type_id, conditions)

        for item in impl.items:
            method_id = self.__resolve_method_decl(unit, item.decl, generics, target_type_id, False)

            # Keep the resolved body associated with its callable definition.
            self.__ctx.procedures.register(method_id, item.body, unit.unit_id)

            impl_obj.methods[item.decl.name.name] = method_id

        unit.symbol_ctx.exit_scope()

    def __resolve_method_decl(self, unit: UnitData, decl: AST.MethodDecl, prev_generics: Sequence[int], receiver_type_id: int, is_header: bool) -> int:
        if len({attr.kind for attr in decl.attrs}) != len(decl.attrs):
            raise AnalysisError("Duplicate method modifier", decl.span)
        if AST.AttrKind.Pub in {attr.kind for attr in decl.attrs} and AST.AttrKind.PubFfi in {attr.kind for attr in decl.attrs}:
            raise AnalysisError("Use either pub or pub(ffi)", decl.span)
        if AST.AttrKind.PubFfi in {attr.kind for attr in decl.attrs} and AST.AttrKind.Ffi not in {attr.kind for attr in decl.attrs}:
            raise AnalysisError("pub(ffi) method must be ffi fn", decl.span)
        if any(attr.kind in (AST.AttrKind.Ffi, AST.AttrKind.PubFfi) for attr in decl.attrs) and not unit.allows_ffi:
            raise AnalysisError("FFI method requires package ffi = true or --allow-ffi", decl.span)
        if is_header and any(attr.kind == AST.AttrKind.Ffi for attr in decl.attrs):
            raise AnalysisError("FFI method requires a body", decl.span)
        # alloc in type space
        type_id = self.__ctx.type_ctx.alloc_method(decl.name.name, span=decl.span)

        # alloc in symbol space
        symbol_attrs = self.__convert_attrs(decl.attrs)
        symbol_id = unit.symbol_ctx.add_symbol(decl.name.name, SymbolKind.Function, type_id, symbol_attrs, decl.name.span)
        if symbol_id is None:
            raise AnalysisError(f"Duplicate method name: {decl.name.name}", decl.name.span)
        symbol = unit.symbol_ctx.get_typed(symbol_id)

        # resolve generics, parameters and return type
        unit.symbol_ctx.enter_scope()
        generics: list[int] = list(prev_generics)
        for param in decl.generics:
            match param:
                case AST.TypeGenericParam(name=name):
                    g_id = self.__ctx.type_ctx.alloc_generic(name.name)
                    unit.symbol_ctx.add_symbol(name.name, SymbolKind.Type, g_id, span=name.span)
                case AST.ConstGenericParam(name=name, value_type=vty):
                    vt_id = self.__ctx.resolve_type_in(vty, unit.symbol_ctx)
                    g_id = self.__ctx.type_ctx.alloc_const_generic(name.name, vt_id)
                    unit.symbol_ctx.add_symbol(name.name, SymbolKind.ConstGeneric, g_id, span=name.span)
            generics.append(g_id)

        parameters: list[Type.Parameter] = []
        for param in decl.params:
            assert isinstance(param, AST.VarInfo)
            parameters.append(Type.Parameter(
                name=param.name.name,
                type_id=self.__ctx.resolve_type_in(param.var_type, unit.symbol_ctx),
                span=None if param.name.synthetic else param.name.span,
            ))
        if decl.ret_type is None:
            ret_type_id = self.__ctx.type_ctx.void_id
        else:
            ret_type_id = self.__ctx.resolve_type_in(decl.ret_type, unit.symbol_ctx)
        unit.symbol_ctx.exit_scope()

        # update the method symbol with the resolved type
        ty = self.__ctx.type_ctx[symbol.type_id]
        assert isinstance(ty, Type.MethodType)
        ty.custom_def.receiver_type = receiver_type_id
        ty.custom_def.parameters = parameters
        ty.custom_def.return_type = ret_type_id
        self.__check_sized_signature(parameters, ret_type_id, decl.span)
        ty.custom_def.is_static = any(attr.kind == AST.AttrKind.Static for attr in decl.attrs)
        ty.custom_def.is_header = is_header
        ty.custom_def.is_ffi = any(attr.kind == AST.AttrKind.Ffi for attr in decl.attrs)
        ty.custom_def.ffi_only = any(attr.kind == AST.AttrKind.PubFfi for attr in decl.attrs)
        if not ty.custom_def.is_ffi or any(attr.kind == AST.AttrKind.Pub for attr in decl.attrs):
            self.__check_yian_signature(parameters, ret_type_id, decl.span)
        self.__ctx.type_ctx.bind_template(type_id, generics)

        return type_id
