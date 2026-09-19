"""Read-only facts about one analyzed program.

The compiler computes a great deal — symbol tables, a type space, recorded name
resolutions, a declaration index, the spans the parser kept — and an editor needs
to *read* those facts, not to run any analysis of its own.  This module is the one
place where that reading happens: ``TypeCtx`` and the declaration index appear
here and nowhere else in the query layer, so every other module depends on
questions about the program rather than on how the compiler stores it (
).

Two rules make the surface trustworthy:

* **nothing here mutates anything** — a view only reads the frozen result it was
  built over, so it is exactly as valid as the snapshot that produced it;
* **every answer is a fact, not a guess** — a type that is not registered, a name
  that was not resolved or a span the parser never saw returns ``None`` (or an
  empty tuple) rather than a plausible-looking default.

``Navigator`` adds the one thing this layer deliberately leaves out — *resolution*,
i.e. "what does the position point at" — on top of a view.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

from compiler.analysis.index import (
    Declaration,
    DeclarationIndex,
)
from compiler.analysis.session import AnalysisResult
from compiler.analysis.symbol.symbol import Symbol
from compiler.analysis.ty import ty as Type
from compiler.analysis.ty.context import TypeCtx
from compiler.analysis.unit.def_point import DefPoint
from compiler.analysis.unit.unit_data import UnitData
from compiler.frontend.lex import token as Tok
from compiler.frontend.lex.position import SrcSpan
from compiler.frontend.parse import ast as AST

__all__ = ["AnalysisView"]


class AnalysisView:
    """Facts about one analysis result, for readers that must not guess.

    A view holds references only; it is created per request, so it stays valid
    exactly as long as the snapshot it was built from (a stale
    snapshot is not something to query, it is something to replace).
    """

    def __init__(self, result: AnalysisResult) -> None:
        self.__result = result
        self.__type_ctx: TypeCtx | None = result.type_ctx
        self.__index: DeclarationIndex | None = result.index
        # Position → what is there, built once per view: a request may ask about
        # many positions (semantic tokens, completion), and each answer is then a
        # dictionary lookup rather than a scan of every reference.
        self.__references: dict[tuple[Path, int, int], Type.NameRef] | None = None
        self.__declarations: dict[tuple[Path, int, int], Declaration] | None = None
        self.__references_by_path: dict[Path, tuple[Type.NameRef, ...]] | None = None

    # ── sources and tokens ────────────────────────────────────────────────────

    def text_of(self, path: Path) -> str:
        """The analyzed text of *path*, which is what ranges are measured in."""
        for candidate, text in self.__result.sources.items():
            if candidate.resolve() == path.resolve():
                return text
        return ""

    def tokens_of(self, path: Path) -> tuple[Tok.Token, ...]:
        """The lexed tokens of *path*, empty when the file was not analyzed."""
        return self.__result.tokens.get(path.resolve(), ())

    def document_version(self, path: Path) -> int | None:
        """The editor version the analysis read *path* at, when known."""
        return self.__result.versions.get(path.resolve())

    # ── declarations ──────────────────────────────────────────────────────────

    def has_declarations(self, path: Path) -> bool:
        """True when the analysis produced anything nameable for *path*.

        An index exists only for a run that reached type checking; a file whose
        syntax has not parsed yet has symbols at best (see :meth:`has_symbols`).
        """
        return self.__index is not None

    def declarations_in(self, path: Path) -> tuple[Declaration, ...]:
        """Declarations made in *path* (module-level and nested)."""
        return () if self.__index is None else self.__index.in_file(path)

    def declarations_by_name(self, name: str) -> tuple[Declaration, ...]:
        """Declarations with this exact name, anywhere in the project."""
        return () if self.__index is None else self.__index.by_name(name)

    def declaration_starting_at(self, path: Path, row: int, col: int) -> Declaration | None:
        """The declaration whose *name* starts at the position, if any."""
        return self.__declaration_map().get((path.resolve(), row, col))

    def module_of(self, path: Path) -> str | None:
        """The ``<package>.<module>`` name of *path*, for hover's origin line."""
        return None if self.__index is None else self.__index.modules.get(path.resolve())

    def package_names(self) -> tuple[str, ...]:
        """Package names in the project, sorted."""
        if self.__index is None:
            return ()
        return tuple(sorted(set(self.__index.packages.values())))

    def module_names(self) -> tuple[str, ...]:
        """Module names in the project, sorted."""
        if self.__index is None:
            return ()
        return tuple(sorted(set(self.__index.modules.values())))

    def module_file(self, dotted: str) -> Path | None:
        """The file a ``<package>.<module>`` name refers to, when it is one."""
        if self.__index is None:
            return None
        for path, module in self.__index.modules.items():
            if module == dotted:
                return path
        return None

    def dependents_of(self, path: Path) -> tuple[Path, ...]:
        """Files that import *path* directly."""
        return () if self.__index is None else self.__index.dependents_of(path)

    def import_statements(self, path: Path) -> tuple[SrcSpan, ...]:
        """The extent of every import statement in *path*.

        ``AST.Import.span`` is only the keyword; the parser also recorded where
        the statement's terminator ended, which is what removing the statement
        needs.
        """
        unit = self.unit_of(path)
        if unit is None:
            return ()
        return tuple(
            span
            for item in unit.program.items
            if isinstance(item, AST.Import) and (span := item.full_span()) is not None
        )

    # ── recorded names ────────────────────────────────────────────────────────

    def name_refs(self) -> tuple[Type.NameRef, ...]:
        """Every name the analysis resolved, in the order it resolved them."""
        if self.__type_ctx is None:
            return ()
        return tuple(self.__type_ctx.name_refs())

    def reference_starting_at(self, path: Path, row: int, col: int) -> Type.NameRef | None:
        """The resolved name whose span *starts* at the position.

        A recorded name is exactly one identifier, so its start position
        identifies it: this is the lookup semantic tokens and completion use.
        """
        return self.__reference_map().get((path.resolve(), row, col))

    def references_of(self, path: Path) -> tuple[Type.NameRef, ...]:
        """Every recorded name in *path* (grouped once per view)."""
        return self.__references_by_path_map().get(path.resolve(), ())

    def reference_in_span(self, span: SrcSpan) -> Type.NameRef | None:
        """The resolved name inside *span*, if one was recorded there."""
        for reference in self.references_of(span.path):
            start = reference.span.start
            if _contains(span, start.row, start.col):
                return reference
        return None

    # ── units and definitions ─────────────────────────────────────────────────

    def unit_of(self, path: Path) -> UnitData | None:
        """The compilation unit analyzed for *path*, if it was analyzed."""
        for unit in self.__result.units.values():
            if unit.path.resolve() == path.resolve():
                return unit
        return None

    def unit_path(self, unit_id: int) -> Path | None:
        """The file a unit id belongs to."""
        unit = self.__result.units.get(unit_id)
        return None if unit is None else unit.path.resolve()

    def has_symbols(self, path: Path) -> bool:
        """True when the analysis produced a symbol table for *path*.

        That is true once resolution has run, even when type checking stopped:
        the names are then real, though little is known about them.
        """
        return self.__index is not None or self.unit_of(path) is not None

    def symbols_in(self, path: Path) -> tuple[Symbol, ...]:
        """Symbols declared in *path*: its own declarations and its imports.

        The unit's symbol table is the authoritative "what names exist in this
        file" set, and it is where the prelude's injected imports appear — an
        import the compiler synthesized sits at the file's start, a position the
        index skips because an editor cannot jump there, but the *name* is
        visible.
        """
        unit = self.unit_of(path)
        if unit is None:
            return ()
        return tuple(
            symbol
            for _, symbol in unit.symbol_ctx.items()
            if symbol.span is not None and symbol.span.path.resolve() == path.resolve()
        )

    def symbol_named_in(self, path: Path, name: str) -> Symbol | None:
        """The global symbol *name* refers to in *path*, if the file declares it."""
        unit = self.unit_of(path)
        return None if unit is None else unit.symbol_ctx.lookup_global(name)

    def def_points(self) -> Mapping[int, DefPoint]:
        """Every definition the type checker ran."""
        return self.__result.def_points

    def definition_bodies_in(self, path: Path) -> tuple[tuple[int, SrcSpan], ...]:
        """``(type id, body extent)`` for every definition whose body is in *path*.

        A body's extent is opening brace through matching closing brace; a
        definition the compiler synthesized has no source extent and is left out.
        """
        bodies: list[tuple[int, SrcSpan]] = []
        for type_id, def_point in self.__result.def_points.items():
            body = def_point.ast_body
            if body.span.path.resolve() != path.resolve():
                continue
            span = body.full_span()
            if span is not None:
                bodies.append((type_id, span))
        return tuple(bodies)

    def procedures(self) -> tuple[tuple[int, AST.Block, int], ...]:
        """Every registered procedure as ``(type id, body, unit id)``."""
        if self.__type_ctx is None:
            return ()
        return tuple(self.__type_ctx.iter_procedures())

    # ── the type space ────────────────────────────────────────────────────────

    @property
    def has_types(self) -> bool:
        """True when the run reached type checking (so types can be asked about)."""
        return self.__type_ctx is not None

    def type_of(self, type_id: int) -> Type.Ty | None:
        """The type a type id stands for, or ``None`` when it is unknown."""
        if self.__type_ctx is None or type_id not in self.__type_ctx:
            return None
        return self.__type_ctx[type_id]

    def type_name(self, type_id: int | None) -> str | None:
        """The rendered name of a type, as the compiler would print it."""
        if type_id is None or self.__type_ctx is None:
            return None
        return self.__type_ctx.get_name(type_id)

    def canonical_type(self, type_id: int) -> int:
        """*type_id* with every transparent alias resolved."""
        if self.__type_ctx is None:
            return type_id
        return self.__type_ctx.resolve_aliases(type_id)

    def deref_type(self, type_id: int) -> int | None:
        """The type a pointer or reference points at, when it does."""
        if self.__type_ctx is None:
            return None
        return self.__type_ctx.try_deref(type_id)

    def fields_of(self, type_id: int) -> tuple[Type.StructField, ...]:
        """The fields of a struct type (an alias answers like its target)."""
        if self.__type_ctx is None:
            return ()
        resolved = self.canonical_type(type_id)
        if not isinstance(self.type_of(resolved), Type.StructType):
            return ()
        return tuple(self.__type_ctx.get_struct_fields(resolved))

    def variants_of(self, type_id: int) -> tuple[Type.EnumVariant, ...]:
        """The variants of an enum type (an alias answers like its target)."""
        if self.__type_ctx is None:
            return ()
        resolved = self.canonical_type(type_id)
        if not isinstance(self.type_of(resolved), Type.EnumType):
            return ()
        return tuple(self.__type_ctx.get_enum_variants(resolved))

    def methods_of(self, type_id: int) -> tuple[tuple[str, int], ...]:
        """Methods whose receiver can be this type, as ``(name, type id)``.

        This is the filtered view the editor wants: an ``impl<T> Vec<T>`` does
        not put ``push`` on a ``Point``.  Conditional generic impls are still
        offered — deciding those needs per-name inference, which completion
        deliberately does not run for every candidate.
        """
        if self.__type_ctx is None:
            return ()
        return tuple(self.__type_ctx.methods_of(self.canonical_type(type_id)))

    def is_static_callable(self, type_id: int) -> bool:
        """True when the callable is a static method (no receiver argument)."""
        resolved = self.type_of(type_id)
        return isinstance(resolved, Type.MethodType) and resolved.is_static

    def callable_name(self, type_id: int) -> str:
        """The declared name of a callable, else the rendered type name."""
        resolved = self.type_of(type_id)
        if isinstance(resolved, (Type.FunctionType, Type.MethodType)):
            return resolved.custom_def.name
        return self.type_name(type_id) or ""

    def parameters_of(self, type_id: int) -> tuple[tuple[str, str], ...]:
        """``(name, rendered type)`` for each parameter of a callable.

        The types come from the *instantiated* callable, so ``Pair<f64>.of``
        reports ``f64`` where the declaration is written ``T``.
        """
        if self.__type_ctx is None:
            return ()
        resolved = self.type_of(type_id)
        if not isinstance(resolved, (Type.FunctionType, Type.MethodType)):
            return ()
        return tuple(
            (parameter.name, self.__type_ctx.get_name(parameter.type_id))
            for parameter in resolved.parameters(self.__type_ctx)
        )

    def return_type_name(self, type_id: int) -> str | None:
        """The rendered return type of a callable, else its own name."""
        if self.__type_ctx is None:
            return None
        resolved = self.type_of(type_id)
        if isinstance(resolved, (Type.FunctionType, Type.MethodType)):
            return self.__type_ctx.get_name(resolved.return_type(self.__type_ctx))
        return self.__type_ctx.get_name(type_id)

    # ── internals ─────────────────────────────────────────────────────────────

    def __reference_map(self) -> dict[tuple[Path, int, int], Type.NameRef]:
        if self.__references is None:
            references: dict[tuple[Path, int, int], Type.NameRef] = {}
            for reference in self.name_refs():
                start = reference.span.start
                key = (reference.span.path.resolve(), start.row, start.col)
                references.setdefault(key, reference)
            self.__references = references
        return self.__references

    def __declaration_map(self) -> dict[tuple[Path, int, int], Declaration]:
        if self.__declarations is None:
            declarations: dict[tuple[Path, int, int], Declaration] = {}
            if self.__index is not None:
                for declaration in self.__index.declarations:
                    span = declaration.span
                    if span is None:
                        continue
                    key = (span.path.resolve(), span.start.row, span.start.col)
                    declarations.setdefault(key, declaration)
            self.__declarations = declarations
        return self.__declarations

    def __references_by_path_map(self) -> dict[Path, tuple[Type.NameRef, ...]]:
        if self.__references_by_path is None:
            grouped: dict[Path, list[Type.NameRef]] = {}
            for reference in self.name_refs():
                grouped.setdefault(reference.span.path.resolve(), []).append(reference)
            self.__references_by_path = {
                path: tuple(references) for path, references in grouped.items()
            }
        return self.__references_by_path


def _contains(span: SrcSpan, row: int, col: int) -> bool:
    """True when the position is inside *span* (inclusive at both ends).

    Single underscore on purpose: this is a module-level private that the class
    body would mangle if it were written ``__contains`` (AGENTS.md).
    """
    return (span.start.row, span.start.col) <= (row, col) <= (span.end.row, span.end.col)
