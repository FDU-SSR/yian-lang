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

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Protocol

from compiler.analysis.package_map import PackageMap
from compiler.analysis.ty import ty as Type
from compiler.analysis.ty.context import TypeCtx
from compiler.analysis.unit.unit_data import UnitData
from compiler.frontend.lex.position import SrcSpan
from compiler.frontend.parse import ast as AST
from compiler.frontend.parse import ast_type as ASTTy
from compiler.frontend.parse.ast_type import ASTType


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

    Analysis hands out an index that is built on the first question
    (:class:`LazyIndex`); :class:`Index` is that same index once it exists.  Both
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
        changes, these are the files whose analysis can change because of it
.
        """
        target = path.resolve()
        return tuple(sorted(source for source, targets in self.imports.items() if target in targets))


class LazyIndex:
    """A declaration index that is built the first time anything reads it.

    The index is a *projection* of facts the run already holds — the units'
    symbol tables, the type space, the resolver's import edges — so building it
    is not extra analysis, and only an editor ever reads it.  Building it inside
    :meth:`AnalysisSession.analyze` charged every caller for work most of them
    never use: ``yianc --analyze`` never looks at the index at all, and an editor
    save that no query follows does not either (7 / 17 / 59 ms at 39 / 95 / 285
    files).  The projection happens on the first question instead.
    """

    def __init__(
        self,
        *,
        units: Mapping[int, UnitData],
        type_ctx: TypeCtx | None,
        import_edges: Callable[[], Mapping[int, tuple[int, ...]] | None],
        packages: PackageMap | None,
    ) -> None:
        self.__units = units
        self.__type_ctx = type_ctx
        self.__import_edges = import_edges
        self.__packages = packages
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
                self.__units, self.__type_ctx, self.__import_edges(), self.__packages
            )
            self.__index = built
        return built


def build_index(
    units: Mapping[int, UnitData],
    type_ctx: TypeCtx | None,
    import_edges: Mapping[int, tuple[int, ...]] | None = None,
    packages: PackageMap | None = None,
) -> Index:
    """Build the declaration index from analyzed units.

    *units* are the analyzed units, so their symbol tables hold every registered
    declaration.  *import_edges* comes from the resolver — the only component
    that knows how each import actually resolved — and *packages* names the
    modules using the same source roots the compiler uses.
    """
    module_of, package_of = __module_names(units, packages)

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
        declarations.extend(__unit_declarations(unit, type_ctx, module, path))

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
    unit: UnitData, type_ctx: TypeCtx | None, module: str, path: Path
) -> list[Declaration]:
    declarations: list[Declaration] = []
    for item in unit.items():
        match item:
            case AST.FuncDef(name=name, attrs=attrs, params=params, body=body):
                declarations.append(__declaration(unit, type_ctx, name.name, DeclarationKind.FUNCTION, path, name.span, module, attrs=attrs))
                declarations.extend(__parameters(params, path, name.name, module))
                declarations.extend(__variables(body, path, module, name.name))
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
                        declarations.extend(__variables(body, path, module, decl.name.name))
            case AST.Impl(items=impl_items, target=target):
                container = __type_label(target)
                for method in impl_items:
                    decl = method.decl
                    declarations.append(__declaration(unit, type_ctx, decl.name.name, DeclarationKind.METHOD, path, decl.name.span, module, container=container, attrs=decl.attrs))
                    declarations.extend(__parameters(decl.params, path, decl.name.name, module))
                    declarations.extend(__variables(method.body, path, module, decl.name.name))
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
    params: list[AST.VarInfo], path: Path, container: str, module: str
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


def __variables(body: AST.Block, path: Path, module: str, container: str) -> list[Declaration]:
    """Local bindings declared inside *container*'s body.

    ``let`` bindings, ``for`` loop variables, match payload bindings and closure
    parameters and captures all name something an editor can navigate to, so they
    are indexed like any other declaration.  Block nesting is *not* modelled:
    every local is recorded under its enclosing function, which is the scope the
    type checker already reconstructs from its symbol table when a position has
    to be resolved.
    """
    declarations: list[Declaration] = []
    __walk_block(body, path, module, container, declarations)
    return declarations


def __walk_block(
    block: AST.Block, path: Path, module: str, container: str, out: list[Declaration]
) -> None:
    for statement in block.stmts:
        __walk_statement(statement, path, module, container, out)


def __walk_statement(
    statement: AST.Expr, path: Path, module: str, container: str, out: list[Declaration]
) -> None:
    match statement:
        case AST.Block(stmts=stmts):
            for inner in stmts:
                __walk_statement(inner, path, module, container, out)
        case AST.VarDecl(name=name, var_type=var_type, init_expr=init_expr):
            out.append(__variable(name, var_type, path, module, container))
            if init_expr is not None:
                __walk_expression(init_expr, path, module, container, out)
        case AST.For(var_name=var_name, iterable=iterable, body=body):
            out.append(__variable(var_name, None, path, module, container))
            __walk_expression(iterable, path, module, container, out)
            __walk_block(body, path, module, container, out)
        case AST.If(
            condition=condition,
            then_branch=then_branch,
            elif_branches=elif_branches,
            else_branch=else_branch,
        ):
            __walk_expression(condition, path, module, container, out)
            __walk_block(then_branch, path, module, container, out)
            for branch_condition, branch in elif_branches:
                __walk_expression(branch_condition, path, module, container, out)
                __walk_block(branch, path, module, container, out)
            if else_branch is not None:
                __walk_block(else_branch, path, module, container, out)
        case AST.ComptimeIf(condition=condition, then_branch=then_branch, else_branch=else_branch):
            __walk_expression(condition, path, module, container, out)
            __walk_block(then_branch, path, module, container, out)
            __walk_block(else_branch, path, module, container, out)
        case AST.While(condition=condition, body=body):
            __walk_expression(condition, path, module, container, out)
            __walk_block(body, path, module, container, out)
        case AST.Loop(body=body):
            __walk_block(body, path, module, container, out)
        case AST.Match(expr=expr, arms=arms):
            __walk_expression(expr, path, module, container, out)
            for pattern, body in arms:
                if isinstance(pattern, AST.PayloadPattern):
                    for field in pattern.fields:
                        out.append(
                            Declaration(
                                name=field.name,
                                kind=DeclarationKind.VARIABLE,
                                path=path,
                                span=field.span,
                                container=container,
                                module=module,
                            )
                        )
                __walk_block(body, path, module, container, out)
        case AST.Semi(expr=inner):
            # Every statement in a block is wrapped in `Semi`, and the wrapper
            # hides what it wraps: re-dispatch so `let`, `for` and friends inside
            # it are still seen as statements rather than as bare expressions.
            __walk_statement(inner, path, module, container, out)
        case AST.Return(expr=value):
            if value is not None:
                __walk_expression(value, path, module, container, out)
        case AST.Break(expr=value):
            if value is not None:
                __walk_expression(value, path, module, container, out)
        case AST.Defer(action=action):
            __walk_statement(action, path, module, container, out)
        case AST.Assert(condition=condition, message=message):
            __walk_expression(condition, path, module, container, out)
            if message is not None:
                __walk_expression(message, path, module, container, out)
        case AST.Delete(target=target):
            __walk_expression(target, path, module, container, out)
        case _:
            __walk_expression(statement, path, module, container, out)


def __walk_expression(
    expr: AST.Expr, path: Path, module: str, container: str, out: list[Declaration]
) -> None:
    """Enter closures, which are the only expressions that bind names."""
    match expr:
        case (
            AST.Semi()
            | AST.Return()
            | AST.Break()
            | AST.Defer()
            | AST.Assert()
            | AST.Delete()
            | AST.VarDecl()
            | AST.For()
            | AST.While()
            | AST.Loop()
            | AST.If()
            | AST.ComptimeIf()
            | AST.Match()
            | AST.Block()
        ):
            # `If`, `Match` and `VarDecl` are both expressions and statements;
            # going through the statement walk keeps their bodies reachable.
            __walk_statement(expr, path, module, container, out)
        case AST.ClosureExpr(captures=captures, params=params, body=body):
            for capture in captures:
                out.append(__variable(capture.name, None, path, module, container))
                __walk_expression(capture.expr, path, module, container, out)
            for param in params:
                out.append(__variable(param.name, param.var_type, path, module, container))
            __walk_block(body, path, module, container, out)
        case AST.Call(callee=callee, args=args):
            __walk_expression(callee, path, module, container, out)
            __walk_arguments(args, path, module, container, out)
        case AST.BuiltinCall(args=args):
            __walk_arguments(args, path, module, container, out)
        case AST.MethodCall(receiver=receiver, args=args):
            __walk_expression(receiver, path, module, container, out)
            __walk_arguments(args, path, module, container, out)
        case AST.Binary(left=left, right=right):
            __walk_expression(left, path, module, container, out)
            __walk_expression(right, path, module, container, out)
        case AST.Unary(operand=operand):
            __walk_expression(operand, path, module, container, out)
        case AST.FieldAccess(receiver=receiver):
            __walk_expression(receiver, path, module, container, out)
        case AST.Tuple(elements=elements) | AST.Array(elements=elements):
            for element in elements:
                __walk_expression(element, path, module, container, out)
        case AST.ArrayRepeat(element=element, count=count):
            __walk_expression(element, path, module, container, out)
            __walk_expression(count, path, module, container, out)
        case AST.DynValue(value=value) | AST.BitCast(value=value):
            __walk_expression(value, path, module, container, out)
        case AST.DynBuffer(size=size):
            __walk_expression(size, path, module, container, out)
        case _:
            return


def __walk_arguments(
    args: list[AST.Arg], path: Path, module: str, container: str, out: list[Declaration]
) -> None:
    for arg in args:
        __walk_expression(arg.value, path, module, container, out)


def __variable(
    name: AST.Identifier, var_type: ASTType | None, path: Path, module: str, container: str
) -> Declaration:
    return Declaration(
        name=name.name,
        kind=DeclarationKind.VARIABLE,
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

    The prelude injector marks the imports it synthesizes with a zero-width span
    at (0, 0) (``prelude.__make_span``); those live in the AST but not in the
    file, so they must not become navigation targets.
    """
    return not (
        span.start.row == 0
        and span.start.col == 0
        and span.end.row == 0
        and span.end.col == 0
    )


__all__ = ["Declaration", "DeclarationKind", "Index", "build_index"]
