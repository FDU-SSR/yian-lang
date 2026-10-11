"""Read-only facts about one analyzed program.

The compiler computes a great deal — symbol tables, a type space, recorded name
resolutions, a declaration index, the spans the parser kept — and an editor needs
to *read* those facts, not to run any analysis of its own. This view exposes
semantic facts to navigation and completion without giving either query direct
access to compiler storage details.

Two rules make the surface trustworthy:

* **nothing here mutates anything** — a view only reads the completed result it was
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

from compiler.analysis.queries.index import (
    Declaration,
    DeclarationIndex,
)
from compiler.analysis.queries.context import QueryContext
from compiler.analysis.queries.availability import Completeness
from compiler.analysis.facts.names import ExpressionFact, NameRef, ScopeFact
from compiler.analysis.symbol.symbol import AliasSymbol, Symbol
from compiler.analysis.resolution.aliases import AliasInfo
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

    A view holds references and indexes for one snapshot. Its caches are released
    with that snapshot and cannot be used to interpret another revision.
    """

    def __init__(self, context: QueryContext) -> None:
        result = context.result
        self.__result = result
        self.__type_ctx: TypeCtx | None = result.type_ctx
        self.__index: DeclarationIndex | None = context.index
        self.__canonical_paths: dict[Path, Path] = {}
        self.__texts_by_path: dict[Path, str] = {}
        self.__tokens_by_path: dict[Path, tuple[Tok.Token, ...]] = {}
        self.__units_by_path: dict[Path, UnitData] = {}

        for path, text in result.sources.items():
            canonical = self.canonical_path(path)
            self.__texts_by_path[canonical] = text
        for path, tokens in result.tokens.items():
            self.__tokens_by_path[self.canonical_path(path)] = tokens
        for unit in result.units.values():
            self.__units_by_path.setdefault(self.canonical_path(unit.path), unit)

        # Position → what is there, built once per view: a request may ask about
        # many positions (semantic tokens, completion), and each answer is then a
        # dictionary lookup rather than a scan of every reference.
        self.__references: dict[tuple[Path, int, int], NameRef] | None = None
        self.__declarations: dict[tuple[Path, int, int], Declaration] | None = None
        self.__references_by_path: dict[Path, tuple[NameRef, ...]] | None = None
        self.__unchecked_names: dict[str, tuple[SrcSpan, ...]] | None = None
        self._expression_ends: dict[tuple[Path, int, int], ExpressionFact | None] | None = None
        self.__scopes_by_path: dict[Path, list[ScopeFact]] = {}
        for scope in result.scope_facts:
            self.__scopes_by_path.setdefault(self.canonical_path(scope.span.path), []).append(scope)
        self.__receiver_types: dict[int, tuple[int, ...]] = {}
        self.__lines_by_path: dict[Path, tuple[str, ...]] = {}

    @property
    def reference_completeness(self) -> Completeness:
        return Completeness.COMPLETE if self.__result.ok() and self.__result.reference_facts_complete else Completeness.PARTIAL

    @property
    def semantic_complete(self) -> bool:
        return self.__result.ok()

    @property
    def editor_recovery(self) -> bool:
        return self.__result.editor_recovery

    def expression_ending_at(self, path: Path, row: int, column: int) -> ExpressionFact | None:
        self.__prepare_expression_index()
        assert self._expression_ends is not None
        return self._expression_ends.get((self.canonical_path(path), row, column))

    def __prepare_expression_index(self) -> None:
        if self._expression_ends is None:
            self._expression_ends = {}
            for fact in self.__result.expression_facts:
                key = (self.canonical_path(fact.span.path), fact.span.end.row, fact.span.end.col)
                previous = self._expression_ends.get(key)
                if previous is None or (fact.span.start.row, fact.span.start.col) < (previous.span.start.row, previous.span.start.col):
                    self._expression_ends[key] = fact
            for span in self.__result.conflicting_expression_spans:
                self._expression_ends[(self.canonical_path(span.path), span.end.row, span.end.col)] = None

    def visible_bindings_at(self, path: Path, row: int, column: int) -> tuple[Symbol | AliasSymbol, ...] | None:
        """Use the narrowest checked scope, including failed binding barriers."""
        canonical = self.canonical_path(path)
        candidates = [fact for fact in self.__scopes_by_path.get(canonical, ())
                      if (fact.span.start.row, fact.span.start.col) <= (row, column)
                      and (row, column) <= (fact.span.end.row, fact.span.end.col)]
        if not candidates:
            return None
        scope = max(candidates, key=lambda fact: (fact.span.start.row, fact.span.start.col,
                                                 -fact.span.end.row, -fact.span.end.col))
        items, _ = scope.symbols.visible_at(row, column)
        for other in candidates:
            if other.span != scope.span or other.context == scope.context:
                continue
            alternatives, _ = other.symbols.visible_at(row, column)
            items = tuple(item for item in items if any(
                candidate.name == item.name and (
                    isinstance(item, Symbol) and isinstance(candidate, Symbol)
                    and (candidate.kind, candidate.type_id) == (item.kind, item.type_id)
                    or isinstance(item, AliasSymbol) and isinstance(candidate, AliasSymbol)
                    and candidate.alias_id == item.alias_id
                ) for candidate in alternatives))
        return items

    def canonical_path(self, path: Path) -> Path:
        """Normalize a snapshot path once; query loops reuse the stored result."""
        canonical = self.__canonical_paths.get(path)
        if canonical is None:
            canonical = path.resolve()
            self.__canonical_paths[path] = canonical
        return canonical

    def prepare_query_indexes(self) -> None:
        """Build per-snapshot position and rename-safety maps before queries."""
        self.__reference_map()
        self.__references_by_path_map()
        self.__declaration_map()
        self.__unchecked_name_map()
        self.__prepare_expression_index()

    def identifier_name_at(self, span: SrcSpan) -> str | None:
        """Read the exact spelling for a recorded name, caching each file's lines."""
        if span.start.row != span.end.row:
            return None

        path = self.canonical_path(span.path)
        lines = self.__lines_by_path.get(path)
        if lines is None:
            lines = tuple(self.__texts_by_path.get(path, "").splitlines())
            self.__lines_by_path[path] = lines

        if not 0 <= span.start.row < len(lines):
            return None

        line = lines[span.start.row]
        return line[span.start.col : span.end.col]

    def unchecked_name_uses(self, name: str) -> tuple[SrcSpan, ...]:
        """Source names inside procedure bodies the type checker did not visit."""
        return self.__unchecked_name_map().get(name, ())

    def __unchecked_name_map(self) -> dict[str, tuple[SrcSpan, ...]]:
        """Index unchecked procedure names once so each rename is proportional to its result."""
        if self.__unchecked_names is None:
            grouped: dict[str, list[SrcSpan]] = {}
            if self.has_types:
                checked = set(self.def_points())
                for type_id, body, unit_id in self.procedures():
                    if type_id in checked:
                        continue

                    path = self.unit_path(unit_id)
                    extent = body.full_span()
                    if path is None or extent is None:
                        continue

                    for token in self.tokens_of(path):
                        if not isinstance(token, Tok.Identifier):
                            continue
                        if not _span_contains(extent, token.span):
                            continue
                        grouped.setdefault(token.name, []).append(token.span)

            self.__unchecked_names = {
                name: tuple(spans) for name, spans in grouped.items()
            }
        return self.__unchecked_names

    # ── sources and tokens ────────────────────────────────────────────────────

    def text_of(self, path: Path) -> str:
        """The analyzed text of *path*, which is what ranges are measured in."""
        return self.__texts_by_path.get(self.canonical_path(path), "")

    def tokens_of(self, path: Path) -> tuple[Tok.Token, ...]:
        """The lexed tokens of *path*, empty when the file was not analyzed."""
        return self.__tokens_by_path.get(self.canonical_path(path), ())

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
        return self.__declaration_map().get((self.canonical_path(path), row, col))

    def module_of(self, path: Path) -> str | None:
        """The ``<package>.<module>`` name of *path*, for hover's origin line."""
        return None if self.__index is None else self.__index.modules.get(self.canonical_path(path))

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

    def name_refs(self) -> tuple[NameRef, ...]:
        """Every name the analysis resolved, in the order it resolved them."""
        return self.__result.name_refs

    def reference_starting_at(self, path: Path, row: int, col: int) -> NameRef | None:
        """The resolved name whose span *starts* at the position.

        A recorded name is exactly one identifier, so its start position
        identifies it: this is the lookup semantic tokens and completion use.
        """
        return self.__reference_map().get((self.canonical_path(path), row, col))

    def references_of(self, path: Path) -> tuple[NameRef, ...]:
        """Every recorded name in *path* (grouped once per view)."""
        return self.__references_by_path_map().get(self.canonical_path(path), ())

    def reference_in_span(self, span: SrcSpan) -> NameRef | None:
        """The resolved name inside *span*, if one was recorded there."""
        for reference in self.references_of(span.path):
            start = reference.span.start
            if _contains(span, start.row, start.col):
                return reference
        return None

    # ── units and definitions ─────────────────────────────────────────────────

    def unit_of(self, path: Path) -> UnitData | None:
        """The compilation unit analyzed for *path*, if it was analyzed."""
        return self.__units_by_path.get(self.canonical_path(path))

    def unit_path(self, unit_id: int) -> Path | None:
        """The file a unit id belongs to."""
        unit = self.__result.units.get(unit_id)
        return None if unit is None else self.canonical_path(unit.path)

    def has_symbols(self, path: Path) -> bool:
        """True when the analysis produced a symbol table for *path*.

        That is true once resolution has run, even when type checking stopped:
        the names are then real, though little is known about them.
        """
        return self.__index is not None or self.unit_of(path) is not None

    def symbols_in(self, path: Path) -> tuple[Symbol | AliasSymbol, ...]:
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
            if symbol.span is not None and self.canonical_path(symbol.span.path) == self.canonical_path(path)
        )

    def symbol_named_in(self, path: Path, name: str) -> Symbol | AliasSymbol | None:
        """The global symbol *name* refers to in *path*, if the file declares it."""
        unit = self.unit_of(path)
        return None if unit is None else unit.symbol_ctx.lookup_global(name)

    def alias_info(self, symbol: AliasSymbol) -> AliasInfo | None:
        return self.__result.aliases.get(symbol.alias_id)

    def alias_signature(self, symbol: AliasSymbol) -> str | None:
        info = self.alias_info(symbol)
        if info is None:
            return None
        definition = info.definition
        parameters = ", ".join(
            f"const {parameter.name.name}: {parameter.value_type}"
            if isinstance(parameter, AST.ConstGenericParam) else parameter.name.name
            for parameter in definition.generics
        )
        suffix = f"<{parameters}>" if parameters else ""
        return f"typedef {definition.name.name}{suffix} = {definition.target};"

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
            if self.canonical_path(body.span.path) != self.canonical_path(path):
                continue
            span = body.full_span()
            if span is not None:
                bodies.append((type_id, span))
        return tuple(bodies)

    def procedures(self) -> tuple[tuple[int, AST.Block, int], ...]:
        """Every registered procedure as ``(type id, body, unit id)``."""
        return self.__result.procedures

    # ── the type space ────────────────────────────────────────────────────────

    @property
    def has_types(self) -> bool:
        """True when the run reached type checking (so types can be asked about)."""
        return self.__type_ctx is not None

    def type_of(self, type_id: int) -> Type.Ty | None:
        """The type a type id stands for, or ``None`` when it is unknown."""
        if self.__type_ctx is None or type_id not in self.__type_ctx or not self.__type_ctx.editor_type_available(type_id):
            return None
        return self.__type_ctx[type_id]

    def classification_type(self, type_id: int) -> Type.Ty | None:
        """Declaration identity can be known before its payload is complete."""
        if self.__type_ctx is None or type_id not in self.__type_ctx:
            return None
        return self.__type_ctx[type_id]

    def conflicting_references(self) -> tuple[NameRef, ...]:
        """Instance facts omitted from the reliable projection, retaining their identities."""
        projected = {(self.canonical_path(reference.span.path), reference.span.start.row, reference.span.start.col)
                     for reference in self.name_refs()}
        return tuple(reference for reference in self.__result.contextual_name_refs
                     if (self.canonical_path(reference.span.path), reference.span.start.row, reference.span.start.col) not in projected)

    def type_name(self, type_id: int | None) -> str | None:
        """The rendered name of a type, as the compiler would print it."""
        if type_id is None or self.__type_ctx is None or not self.__type_ctx.editor_type_available(type_id):
            return None
        return self.__type_ctx.get_name(type_id)


    def deref_type(self, type_id: int) -> int | None:
        """The type a pointer or reference points at, when it does."""
        if self.__type_ctx is None:
            return None
        return self.__type_ctx.try_deref(type_id)

    def receiver_types(self, type_id: int) -> tuple[int, ...]:
        if self.__type_ctx is None:
            return ()
        cached = self.__receiver_types.get(type_id)
        if cached is not None:
            return cached
        result: list[int] = []
        current = type_id
        budget = sum(1 for _ in self.__type_ctx.items())
        while current not in result and len(result) <= budget:
            result.append(current)
            inner = self.deref_type(current)
            if inner is None:
                break
            current = inner
        self.__receiver_types[type_id] = tuple(result)
        return tuple(result)

    def fields_of(self, type_id: int) -> tuple[Type.StructField, ...]:
        """The instantiated fields of a struct type."""
        if self.__type_ctx is None or not self.__type_ctx.editor_type_available(type_id):
            return ()
        if not isinstance(self.type_of(type_id), Type.StructType):
            return ()
        return tuple(self.__type_ctx.get_struct_fields(type_id))

    def variants_of(self, type_id: int) -> tuple[Type.EnumVariant, ...]:
        """The instantiated variants of an enum type."""
        if self.__type_ctx is None:
            return ()
        if not isinstance(self.type_of(type_id), Type.EnumType):
            return ()
        return tuple(self.__type_ctx.get_enum_variants(type_id))

    def methods_of(self, type_id: int) -> tuple[tuple[str, int], ...]:
        """Methods whose receiver can be this type, as ``(name, type id)``.

        Receiver inference and constraints use the semantic impl matcher. Failed
        candidates retain no substitutions or query facts in other candidates.
        """
        if self.__type_ctx is None or not self.__type_ctx.editor_type_available(type_id):
            return ()
        return tuple(self.__type_ctx.methods_of(type_id))

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
            ("_" if parameter.name.startswith("%arg_") else parameter.name,
             self.__type_ctx.get_name(parameter.type_id))
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

    def __reference_map(self) -> dict[tuple[Path, int, int], NameRef]:
        if self.__references is None:
            references: dict[tuple[Path, int, int], NameRef] = {}
            for reference in self.name_refs():
                start = reference.span.start
                key = (self.canonical_path(reference.span.path), start.row, start.col)
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
                    key = (self.canonical_path(span.path), span.start.row, span.start.col)
                    declarations.setdefault(key, declaration)
            self.__declarations = declarations
        return self.__declarations

    def __references_by_path_map(self) -> dict[Path, tuple[NameRef, ...]]:
        if self.__references_by_path is None:
            grouped: dict[Path, list[NameRef]] = {}
            for reference in self.name_refs():
                grouped.setdefault(self.canonical_path(reference.span.path), []).append(reference)
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


def _span_contains(span: SrcSpan, inner: SrcSpan) -> bool:
    return (span.start.row, span.start.col) <= (inner.start.row, inner.start.col) and (
        inner.end.row,
        inner.end.col,
    ) <= (span.end.row, span.end.col)
