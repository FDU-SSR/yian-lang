"""Declaration index: where every named thing in a project is declared.

The index answers the questions an editor asks about a *project* rather than a
single file: which package and module a file belongs to, what each file imports,
and where each declaration lives.  It is built from the analyzed units, so it
covers code the program never calls — analysis checks a root package's top-level
definitions whether or not `main` reaches them — and it lives on the
Python side, so the editor never re-implements name resolution.

References are deliberately *not* indexed here: resolving the name at a position
needs the type checker's view and belongs to navigation. This is the
declaration side that navigation resolves *to*.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Protocol

from compiler.analysis.package_map import PackageMap
from compiler.analysis.ty import ty as Type
from compiler.analysis.ty.context import TypeCtx
from compiler.analysis.unit.def_point import DefPoint
from compiler.analysis.unit.unit_data import UnitData
from compiler.frontend.lex.position import SrcSpan
from compiler.frontend.parse import ast as AST
from compiler.frontend.parse import ast_type as ASTTy
from compiler.frontend.parse.ast_type import ASTType
from compiler.frontend.parse.ast_traversal import AstVisitor


class DeclarationKind(Enum):
    """What kind of thing a declaration introduces."""

    MODULE = "module"
    IMPORT = "import"
    FUNCTION = "function"
    METHOD = "method"
    STRUCT = "struct"
    ENUM = "enum"
    TRAIT = "trait"
    ALIAS = "alias"
    FIELD = "field"
    VARIANT = "variant"
    PARAMETER = "parameter"
    VARIABLE = "variable"


@dataclass(frozen=True)
class Declaration:
    """One declaration, with the position an editor should jump to."""

    name: str
    kind: DeclarationKind
    path: Path
    #: The declaration's *name* span, which is what "go to definition" points at.
    #: ``None`` when the source never names it: a module (the file is the target
    #: then) or a declaration the compiler synthesized, which no position owns.
    span: SrcSpan | None
    #: Enclosing declaration's name (a field's struct, a method's type, …).
    container: str | None = None
    #: Rendered type of the declaration when it has one.
    type_name: str | None = None
    #: Which module declared it, as ``<package>.<module path>``.
    module: str | None = None
    public: bool = False

    @property
    def qualified_name(self) -> str:
        """``module`` for a module, else ``module.Container.name``."""
        if self.kind is DeclarationKind.MODULE:
            return self.module or self.name
        parts = [self.module] if self.module else []
        if self.container:
            parts.append(self.container)
        parts.append(self.name)
        return ".".join(parts)


class DeclarationIndex(Protocol):
    """What a caller may ask a declaration index, however it was produced.

    Query processing uses an index built on the first question
    (:class:`LazyIndex`); :class:`Index` is that same index once it exists. Both
    satisfy this surface, so a caller never needs to know which one it holds.
    """

    @property
    def declarations(self) -> tuple[Declaration, ...]: ...

    @property
    def imports(self) -> Mapping[Path, tuple[Path, ...]]: ...

    @property
    def packages(self) -> Mapping[Path, str]: ...

    @property
    def modules(self) -> Mapping[Path, str]: ...

    @property
    def built(self) -> bool:
        """True once the index has been materialized."""
        ...

    def in_file(self, path: Path) -> tuple[Declaration, ...]: ...

    def by_name(self, name: str) -> tuple[Declaration, ...]: ...

    def dependents_of(self, path: Path) -> tuple[Path, ...]: ...


@dataclass(frozen=True)
class Index:
    """Immutable declaration index for one analysis result."""

    declarations: tuple[Declaration, ...] = ()
    #: Resolved import edges, file → files it imports.
    imports: Mapping[Path, tuple[Path, ...]] = field(default_factory=dict[Path, tuple[Path, ...]])
    #: Package each file belongs to.
    packages: Mapping[Path, str] = field(default_factory=dict[Path, str])
    #: ``<package>.<module path>`` for each file.
    modules: Mapping[Path, str] = field(default_factory=dict[Path, str])
    # Single underscore on purpose: these two are module-level privates that the
    # class body writes as `__name`, which name-mangles them (AGENTS.md).
    _by_path: Mapping[Path, tuple[Declaration, ...]] = field(
        default_factory=dict[Path, tuple[Declaration, ...]]
    )
    _by_name: Mapping[str, tuple[Declaration, ...]] = field(
        default_factory=dict[str, tuple[Declaration, ...]]
    )

    @property
    def built(self) -> bool:
        """Always true: this form already exists."""
        return True

    def in_file(self, path: Path) -> tuple[Declaration, ...]:
        """Declarations made in *path* (module-level and nested)."""
        return self._by_path.get(path.resolve(), ())

    def by_name(self, name: str) -> tuple[Declaration, ...]:
        """Declarations with this exact name, anywhere in the project."""
        return self._by_name.get(name, ())

    def dependents_of(self, path: Path) -> tuple[Path, ...]:
        """Files that import *path* directly.

        This is the invalidation unit the snapshot layer needs: after a file
        changes, these are the files whose analysis can change because of it.
        """
        target = path.resolve()
        return tuple(sorted(source for source, targets in self.imports.items() if target in targets))


class LazyIndex:
    """A declaration index that is built the first time anything reads it.

    The index is a *projection* of facts the run already holds — the units'
    symbol tables, the type space, the resolver's import edges. It belongs to
    query processing; command-line analysis does not materialize it.
    """

    def __init__(
        self,
        *,
        units: Mapping[int, UnitData],
        type_ctx: TypeCtx | None,
        import_edges: Mapping[int, tuple[int, ...]],
        packages: PackageMap | None,
        def_points: Mapping[int, DefPoint],
    ) -> None:
        self.__units = units
        self.__type_ctx = type_ctx
        self.__import_edges = import_edges
        self.__packages = packages
        self.__def_points = def_points
        self.__index: Index | None = None

    @property
    def built(self) -> bool:
        """True once something has read the index."""
        return self.__index is not None

    @property
    def declarations(self) -> tuple[Declaration, ...]:
        return self.__current().declarations

    @property
    def imports(self) -> Mapping[Path, tuple[Path, ...]]:
        return self.__current().imports

    @property
    def packages(self) -> Mapping[Path, str]:
        return self.__current().packages

    @property
    def modules(self) -> Mapping[Path, str]:
        return self.__current().modules

    def in_file(self, path: Path) -> tuple[Declaration, ...]:
        return self.__current().in_file(path)

    def by_name(self, name: str) -> tuple[Declaration, ...]:
        return self.__current().by_name(name)

    def dependents_of(self, path: Path) -> tuple[Path, ...]:
        return self.__current().dependents_of(path)

    def __current(self) -> Index:
        built = self.__index
        if built is None:
            built = build_index(
                self.__units, self.__type_ctx, self.__import_edges, self.__packages,
                self.__def_points,
            )
            self.__index = built
        return built


def build_index(
    units: Mapping[int, UnitData],
    type_ctx: TypeCtx | None,
    import_edges: Mapping[int, tuple[int, ...]] | None = None,
    packages: PackageMap | None = None,
    def_points: Mapping[int, DefPoint] | None = None,
) -> Index:
    """Build the declaration index from analyzed units.

    *units* are the analyzed units, so their symbol tables hold every registered
    declaration.  *import_edges* comes from the resolver — the only component
    that knows how each import actually resolved — and *packages* names the
    modules using the same source roots the compiler uses. Checked definition
    locals distinguish pattern bindings from bare enum variant names.
    """
    module_of, package_of = __module_names(units, packages)
    pattern_bindings = {
        (symbol.span.path, symbol.span.start.row, symbol.span.start.col)
        for definition in (def_points or {}).values()
        if definition.body is not None
        for symbol in (definition.symbol_ctx.get(symbol_id) for symbol_id in definition.locals)
        if symbol.span is not None
    }

    declarations: list[Declaration] = []
    for unit in units.values():
        path = unit.path.resolve()
        module = module_of.get(path, unit.path.stem)
        declarations.append(
            Declaration(
                name=path.stem,
                kind=DeclarationKind.MODULE,
                path=path,
                span=None,
                module=module,
                public=True,
            )
        )
        declarations.extend(__unit_declarations(unit, type_ctx, module, path, pattern_bindings))

    imports: dict[Path, tuple[Path, ...]] = {}
    if import_edges is not None:
        for unit_id, targets in import_edges.items():
            source = units.get(unit_id)
            if source is None:
                continue
            target_paths: list[Path] = []
            for target_id in targets:
                target = units.get(target_id)
                if target is not None:
                    target_paths.append(target.path.resolve())
            imports[source.path.resolve()] = tuple(sorted(set(target_paths)))

    by_path: dict[Path, list[Declaration]] = {}
    by_name: dict[str, list[Declaration]] = {}
    for declaration in declarations:
        by_path.setdefault(declaration.path, []).append(declaration)
        by_name.setdefault(declaration.name, []).append(declaration)

    return Index(
        declarations=tuple(declarations),
        imports=imports,
        packages=package_of,
        modules=module_of,
        _by_path={path: tuple(items) for path, items in by_path.items()},
        _by_name={name: tuple(items) for name, items in by_name.items()},
    )


def __module_names(
    units: Mapping[int, UnitData], packages: PackageMap | None
) -> tuple[dict[Path, str], dict[Path, str]]:
    """Map each file to ``<package>.<module path>`` and to its package name.

    Module names are derived from the package's source root, exactly like the
    compiler's own unit names, so the index and the compiler agree.
    """
    module_of: dict[Path, str] = {}
    package_of: dict[Path, str] = {}
    roots = (
        {name: spec.source_root.resolve() for name, spec in packages.packages.items()}
        if packages is not None
        else {}
    )
    for unit in units.values():
        path = unit.path.resolve()
        owner = None
        for package, root in sorted(roots.items(), key=lambda item: len(item[1].parts), reverse=True):
            if path.is_relative_to(root):
                owner = (package, root)
                break
        if owner is None:
            module_of[path] = unit.path.stem
            continue
        package, root = owner
        relative = path.relative_to(root).with_suffix("")
        module_of[path] = ".".join([package, *relative.parts])
        package_of[path] = package
    return module_of, package_of


def __unit_declarations(
    unit: UnitData, type_ctx: TypeCtx | None, module: str, path: Path,
    pattern_bindings: set[tuple[Path, int, int]],
) -> list[Declaration]:
    declarations: list[Declaration] = []
    for item in unit.items():
        match item:
            case AST.FuncDef(name=name, attrs=attrs, params=params, body=body):
                declarations.append(__declaration(unit, type_ctx, name.name, DeclarationKind.FUNCTION, path, name.span, module, attrs=attrs))
                declarations.extend(__parameters(params, path, name.name, module))
                declarations.extend(__variables(body, path, module, name.name, pattern_bindings))
            case AST.Alias(name=name, attrs=attrs):
                declarations.append(__declaration(unit, type_ctx, name.name, DeclarationKind.ALIAS, path, name.span, module, attrs=attrs))
            case AST.StructDef(name=name, attrs=attrs):
                declarations.append(__declaration(unit, type_ctx, name.name, DeclarationKind.STRUCT, path, name.span, module, attrs=attrs))
                declarations.extend(__fields(unit, type_ctx, name.name, path, module))
            case AST.EnumDef(name=name, attrs=attrs):
                declarations.append(__declaration(unit, type_ctx, name.name, DeclarationKind.ENUM, path, name.span, module, attrs=attrs))
                declarations.extend(__variants(unit, type_ctx, name.name, path, module))
            case AST.TraitDef(name=name, attrs=attrs, items=trait_items):
                declarations.append(__declaration(unit, type_ctx, name.name, DeclarationKind.TRAIT, path, name.span, module, attrs=attrs))
                for decl, body in __trait_methods(trait_items):
                    declarations.append(__declaration(unit, type_ctx, decl.name.name, DeclarationKind.METHOD, path, decl.name.span, module, container=name.name, attrs=decl.attrs))
                    declarations.extend(__parameters(decl.params, path, decl.name.name, module))
                    if body is not None:
                        declarations.extend(__variables(body, path, module, decl.name.name, pattern_bindings))
            case AST.Impl(items=impl_items, target=target):
                container = __type_label(target)
                for method in impl_items:
                    decl = method.decl
                    declarations.append(__declaration(unit, type_ctx, decl.name.name, DeclarationKind.METHOD, path, decl.name.span, module, container=container, attrs=decl.attrs))
                    declarations.extend(__parameters(decl.params, path, decl.name.name, module))
                    declarations.extend(__variables(method.body, path, module, decl.name.name, pattern_bindings))
            case AST.Import(target=target, alias=alias):
                span = alias.span if alias is not None else target.span
                # Prelude injection adds imports that are not in the source; a
                # declaration the editor cannot jump to is not indexed.
                if not __is_real_span(span):
                    continue
                declarations.append(
                    Declaration(
                        name=alias.name if alias is not None else target.name,
                        kind=DeclarationKind.IMPORT,
                        path=path,
                        span=span,
                        module=module,
                    )
                )
                if alias is not None and __is_real_span(target.span):
                    # An aliased import names two things: the local alias (above)
                    # and the imported name, which rename has to be able to find.
                    declarations.append(
                        Declaration(
                            name=target.name,
                            kind=DeclarationKind.IMPORT,
                            path=path,
                            span=target.span,
                            module=module,
                        )
                    )
            case _:
                continue
    return declarations


def __trait_methods(items: list[AST.TraitItem]) -> list[tuple[AST.MethodDecl, AST.Block | None]]:
    """Trait items are a declaration, or a declaration with a default body."""
    methods: list[tuple[AST.MethodDecl, AST.Block | None]] = []
    for item in items:
        if isinstance(item, AST.MethodDecl):
            methods.append((item, None))
        else:
            methods.append((item.decl, item.body))
    return methods


def __declaration(
    unit: UnitData,
    type_ctx: TypeCtx | None,
    name: str,
    kind: DeclarationKind,
    path: Path,
    span: SrcSpan,
    module: str,
    *,
    container: str | None = None,
    attrs: list[AST.Attr] | None = None,
) -> Declaration:
    type_name = None
    if type_ctx is not None:
        symbol = unit.symbol_ctx.lookup_global(name)
        if symbol is not None:
            type_name = type_ctx.get_name(symbol.type_id)
    return Declaration(
        name=name,
        kind=kind,
        path=path,
        span=span,
        container=container,
        type_name=type_name,
        module=module,
        public=any(attr.kind == AST.AttrKind.Pub for attr in (attrs or [])),
    )


def __fields(
    unit: UnitData, type_ctx: TypeCtx | None, container: str, path: Path, module: str
) -> list[Declaration]:
    """Fields come from the resolved type space, which now records their spans."""
    if type_ctx is None:
        return []
    symbol = unit.symbol_ctx.lookup_global(container)
    if symbol is None:
        return []
    return [
        Declaration(
            name=field.name,
            kind=DeclarationKind.FIELD,
            path=path,
            span=field.span,
            container=container,
            type_name=type_ctx.get_name(field.type_id),
            module=module,
            public=field.access_mode is Type.AccessMode.Public,
        )
        for field in type_ctx.get_struct_fields(symbol.type_id)
    ]


def __variants(
    unit: UnitData, type_ctx: TypeCtx | None, container: str, path: Path, module: str
) -> list[Declaration]:
    """Enum variants come from the resolved type space, spans included."""
    if type_ctx is None:
        return []
    symbol = unit.symbol_ctx.lookup_global(container)
    if symbol is None:
        return []
    declarations: list[Declaration] = []
    for variant in type_ctx.get_enum_variants(symbol.type_id):
        payload = type_ctx.get_name(variant.payload_type) if variant.payload_type is not None else None
        declarations.append(
            Declaration(
                name=variant.name,
                kind=DeclarationKind.VARIANT,
                path=path,
                span=variant.span,
                container=container,
                type_name=payload,
                module=module,
            )
        )
    return declarations


def __parameters(
    params: list[AST.VarInfo | AST.PatternParam], path: Path, container: str, module: str
) -> list[Declaration]:
    return [
        Declaration(
            name=param.name.name,
            kind=DeclarationKind.PARAMETER,
            path=path,
            span=param.name.span,
            container=container,
            module=module,
        )
        for param in params
        if isinstance(param, AST.VarInfo) and not param.name.synthetic
    ]


def __type_label(target: ASTType) -> str:
    """A readable label for an impl target, used as the methods' container.

    ``impl<T> Pair<T>`` labels its methods ``Pair<T>`` so a method's qualified
    name stays unique and readable; anything unrecognized falls back to ``impl``.
    """
    match target:
        case ASTTy.NamedType(name=name):
            return name.name
        case ASTTy.InstanceType(base=base, generic_args=args):
            if isinstance(base, ASTTy.NamedType):
                rendered = ", ".join(__type_label(arg) for arg in args if isinstance(arg, ASTType))
                return f"{base.name.name}<{rendered}>"
            return "impl"
        case _:
            return "impl"


def __variables(
    body: AST.Block, path: Path, module: str, container: str,
    pattern_bindings: set[tuple[Path, int, int]],
) -> list[Declaration]:
    """Local bindings declared inside *container*'s body.

    ``let`` bindings, ``for`` loop variables, match payload bindings and closure
    parameters and captures all name something an editor can navigate to, so they
    are indexed like any other declaration.  Block nesting is *not* modelled:
    every local is recorded under its enclosing function, which is the scope the
    type checker already reconstructs from its symbol table when a position has
    to be resolved.
    """
    visitor = __LocalDeclarationVisitor(path, module, container, pattern_bindings)
    visitor.visit_expr(body)
    return visitor.declarations


class __LocalDeclarationVisitor(AstVisitor):
    def __init__(
        self, path: Path, module: str, container: str,
        pattern_bindings: set[tuple[Path, int, int]],
    ) -> None:
        self.__path = path
        self.__module = module
        self.__container = container
        self.__pattern_bindings = pattern_bindings
        self.__structural = False
        self.__binding_kind = DeclarationKind.VARIABLE
        self.declarations: list[Declaration] = []

    def __add(self, name: AST.Identifier, var_type: ASTType | None = None) -> None:
        if not name.synthetic:
            self.declarations.append(_variable(
                name, var_type, self.__path, self.__module, self.__container, self.__binding_kind,
            ))

    def enter_expr(self, expr: AST.Expr) -> bool:
        if isinstance(expr, AST.VarDecl):
            self.__add(expr.name, expr.var_type)
        return True

    def visit_expr(self, expr: AST.Expr) -> None:
        if isinstance(expr, AST.For):
            previous_structural = self.__structural
            self.__structural = True
            try:
                self.visit_pattern(expr.pattern)
            finally:
                self.__structural = previous_structural
            self.visit_expr(expr.iterable)
            self.visit_expr(expr.body)
            return
        if isinstance(expr, AST.PatternLet):
            previous_kind = self.__binding_kind
            previous_structural = self.__structural
            self.__binding_kind = DeclarationKind.PARAMETER if expr.is_parameter else DeclarationKind.VARIABLE
            self.__structural = True
            try:
                self.visit_pattern(expr.pattern)
            finally:
                self.__binding_kind = previous_kind
                self.__structural = previous_structural
            self.visit_expr(expr.init_expr)
            if expr.else_branch is not None:
                self.visit_expr(expr.else_branch)
            return
        if isinstance(expr, AST.ClosureExpr):
            for capture in expr.captures:
                self.__add(capture.name)
                self.visit_expr(capture.expr)
            previous_kind = self.__binding_kind
            self.__binding_kind = DeclarationKind.PARAMETER
            for param in expr.params:
                if isinstance(param, AST.VarInfo):
                    self.__add(param.name, param.var_type)
            self.__binding_kind = previous_kind
            self.visit_expr(expr.body)
            return
        super().visit_expr(expr)

    def visit_pattern(self, pattern: AST.Pattern) -> None:
        if isinstance(pattern, AST.NamePattern):
            span = pattern.name.span
            if self.__structural or (span.path, span.start.row, span.start.col) in self.__pattern_bindings:
                self.__add(pattern.name)
        elif isinstance(pattern, AST.BindPattern):
            self.__add(pattern.name)
            super().visit_pattern(pattern)
        elif isinstance(pattern, AST.OrPattern):
            if pattern.alternatives:
                self.visit_pattern(pattern.alternatives[0])
        elif isinstance(pattern, (AST.ConstructPattern, AST.TuplePattern, AST.SequencePattern)):
            structural = self.__structural
            self.__structural = True
            try:
                super().visit_pattern(pattern)
            finally:
                self.__structural = structural
        else:
            super().visit_pattern(pattern)


def _variable(
    name: AST.Identifier, var_type: ASTType | None, path: Path, module: str, container: str,
    kind: DeclarationKind = DeclarationKind.VARIABLE,
) -> Declaration:
    return Declaration(
        name=name.name,
        kind=kind,
        path=path,
        span=name.span,
        container=container,
        type_name=__declared_type_label(var_type),
        module=module,
    )


def __declared_type_label(var_type: ASTTy.ConstExpr | ASTType | None) -> str | None:
    """The written type of a local, when the source spells one out.

    ``let x = …`` has a deduced type that only the checker knows, so no label is
    recorded rather than a wrong one.  Const-generic arguments are rendered too,
    so ``Array<i32, 3>`` reads back the way it was written.
    """
    match var_type:
        case ASTTy.NamedType(name=name):
            return name.name
        case ASTTy.LiteralConstExpr(literal=literal):
            return str(literal)
        case ASTTy.GenericConstExpr(name=name):
            return name.name
        case ASTTy.InstanceType(base=base, generic_args=args):
            if isinstance(base, ASTTy.NamedType):
                rendered = ", ".join(
                    label
                    for label in (__declared_type_label(arg) for arg in args)
                    if label is not None
                )
                return f"{base.name.name}<{rendered}>"
            return None
        case _:
            return None


def __is_real_span(span: SrcSpan) -> bool:
    """True when *span* points at real source text.

    The prelude injector marks its imports with a synthetic span; they live in the AST but not in the
    file, so they must not become navigation targets.
    """
    return not span.is_synthetic()


__all__ = ["Declaration", "DeclarationKind", "Index", "build_index"]
