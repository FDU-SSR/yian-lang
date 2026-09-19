"""What a position points at, answered from the analyzed result.

`definition` and `hover` are two renderings of one question: which declaration
does this position name, and what is the type of the expression here?  Both are
answered from the analysis through two tables that the
analysis itself filled in:

* the **declaration index** knows every declaration's name span, which is
  the declaration side of navigation — a function name, a struct field, an enum
  variant, a parameter, a type, an import;
* the checker's **resolved names** (:class:`~compiler.analysis.ty.ty.NameRef`)
  record, for every name it resolved anywhere in a body or a signature, what that
  name turned out to be — the reference side: locals, parameters, calls, methods,
  fields, enum variants, constructed structs, and type names in annotations.

Nothing is re-derived from the text: the text is consulted for one thing only,
to convert the client's position into a compiler position. A
position in a comment, in whitespace, or in a definition the analysis never
checked resolves to ``None`` rather than to something unrelated.

One consequence of recovery being per *definition*: a
body that fails stops contributing references at the point it failed, so
positions after the first error in that body resolve to nothing.  Resolving to
nothing is the honest answer there — the alternative would be a jump to whatever
the name meant in some earlier revision, which is exactly the stale result the
snapshot rule forbids.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from compiler.analysis.index import Declaration, DeclarationKind
from compiler.analysis.session import AnalysisResult
from compiler.analysis.symbol.symbol import Symbol, SymbolKind
from compiler.analysis.ty import ty as Type
from compiler.analysis.view import AnalysisView
from compiler.frontend.lex import token as Tok
from compiler.frontend.lex.position import SrcSpan

__all__ = ["Navigator", "Resolution", "Target"]


@dataclass(frozen=True)
class Target:
    """A declaration a position refers to."""

    name: str
    kind: DeclarationKind
    #: The declaration's *name* span: what "go to definition" points at.
    span: SrcSpan
    #: Rendered type of the declaration (a field's type, a local's type).
    type_name: str | None = None
    #: Rendered signature for a callable (``fn new(x: Meters) -> Point``).
    signature: str | None = None
    #: Enclosing declaration's name (a field's struct, a method's type, …).
    container: str | None = None
    #: True when the declaration lives in the standard library.
    stdlib: bool = False


@dataclass(frozen=True)
class Resolution:
    """What one position resolves to.

    ``target`` answers "go to definition"; ``expression_type`` is the type the
    analysis gave the expression at the position.
    """

    target: Target | None = None
    expression_type: str | None = None


class Navigator:
    """Editor queries over one analyzed result.

    The navigator holds references only: it is created per request, so it stays
    valid exactly as long as the snapshot it was built from (a stale
    snapshot is not something to query, it is something to replace).
    """

    def __init__(self, result: AnalysisResult, *, std_root: Path | None = None) -> None:
        self.__view = AnalysisView(result)
        self.__std_root = std_root

    @property
    def view(self) -> AnalysisView:
        """The facts this navigator answers from.

        Resolution is all this class adds; everything it reads — tokens, symbols,
        declarations, the type space, the recorded names — comes from the view,
        which is the only place that touches the compiler's internals.
        """
        return self.__view

    # ── sources ───────────────────────────────────────────────────────────────

    def text_of(self, path: Path) -> str:
        """The analyzed text of *path*, which is what ranges are measured in."""
        return self.__view.text_of(path)

    def tokens_of(self, path: Path) -> tuple[Tok.Token, ...]:
        """The lexed tokens of *path*, for the queries that read the text."""
        return self.__view.tokens_of(path)

    def declarations_in(self, path: Path) -> tuple[Declaration, ...]:
        """Declarations made in *path* — the document symbol tree's input."""
        return self.__view.declarations_in(path)

    def declarations_by_name(self, name: str) -> tuple[Declaration, ...]:
        """Declarations with this exact name, anywhere in the project."""
        return self.__view.declarations_by_name(name)

    def module_of(self, path: Path) -> str | None:
        """The ``<package>.<module>`` name of *path*, for hover's origin line."""
        return self.__view.module_of(path)

    def enclosing_definition_name(self, path: Path, row: int, col: int) -> str | None:
        """The name of the definition whose body covers the position.

        A body's ``span`` is only its opening brace, so the extent comes from the
        parser's recorded end position; the innermost such definition wins, which
        is what a closure inside a function needs.
        """
        best: tuple[tuple[int, int], str] | None = None
        for type_id, span in self.__view.definition_bodies_in(path):
            if not self.__spans(span, path, row, col):
                continue
            size = self.__size(span)
            if best is None or size < best[0]:
                best = (size, self.__name(type_id) or "")
        return None if best is None else best[1]

    # ── the one query ─────────────────────────────────────────────────────────

    def resolve(self, path: Path, row: int, col: int) -> Resolution | None:
        """Resolve the position ``(row, col)`` in *path*.

        *row* is 0-based and *col* is 1-based in code points, i.e. the compiler's
        own position model: the LSP layer converts at its boundary.
        """
        target: Target | None = None
        expression_type: str | None = None

        reference = self.__reference_at(path, row, col)
        if reference is not None:
            target = self.target_of(reference.target)
            expression_type = self.__name(reference.expression_type)

        if target is None:
            # Nothing was resolved *here*, which is what a declaration site looks
            # like: the name is being introduced, not used.
            declaration = self.__declaration_at(path, row, col)
            if declaration is not None:
                target = self.target_of_declaration(declaration, path)
                if expression_type is None:
                    expression_type = declaration.type_name

        if target is None and expression_type is None:
            return None
        return Resolution(target=target, expression_type=expression_type)

    def __reference_at(self, path: Path, row: int, col: int) -> Type.NameRef | None:
        """The innermost resolved name the position is inside."""
        best: Type.NameRef | None = None
        for reference in self.__view.references_of(path):
            if not self.__spans(reference.span, path, row, col):
                continue
            if best is None or self.__size(reference.span) < self.__size(best.span):
                best = reference
        return best

    def reference_starting_at(self, path: Path, row: int, col: int) -> Type.NameRef | None:
        """The resolved name whose span *starts* at the position.

        A recorded name is exactly one identifier, so its start position
        identifies it: this is the lookup semantic tokens and completion use.
        """
        return self.__view.reference_starting_at(path, row, col)

    def declaration_starting_at(self, path: Path, row: int, col: int) -> Declaration | None:
        """The declaration whose *name* starts at the position, if any."""
        return self.__view.declaration_starting_at(path, row, col)

    def all_references(self) -> tuple[Type.NameRef, ...]:
        """Every recorded name in the analysis, for project-wide queries."""
        return self.__view.name_refs()

    def reference_in_span(self, span: SrcSpan) -> Type.NameRef | None:
        """The resolved name inside *span*, if one was recorded there."""
        return self.__view.reference_in_span(span)

    # ── the declaration index ─────────────────────────────────────────────────

    def __declaration_at(self, path: Path, row: int, col: int) -> Declaration | None:
        """The innermost declaration whose *name* spans the position."""
        best: Declaration | None = None
        for declaration in self.declarations_in(path):
            span = declaration.span
            if span is None or not self.__spans(span, path, row, col):
                continue
            if best is None or self.__size(span) < self.__size(self.__span_of(best)):
                best = declaration
        return best

    def target_of_declaration(self, declaration: Declaration, path: Path) -> Target | None:
        """Turn a declaration into a target, following imports to their source.

        A position on an import names the imported thing, not the import
        statement, so it resolves to the declaration it came from (
        导入符号).
        """
        if declaration.kind is DeclarationKind.IMPORT:
            imported = self.__imported_target(path, declaration.name)
            if imported is not None:
                return imported
        return self.__target_from_declaration(declaration, self.__is_stdlib(declaration.path))

    def __imported_target(self, path: Path, name: str) -> Target | None:
        symbol = self.__view.symbol_named_in(path, name)
        return None if symbol is None else self.target_of(symbol)

    # ── targets from what the analysis resolved ──────────────────────────────

    def target_of(self, target: Type.NameTarget) -> Target | None:
        """The declaration a recorded resolution points at."""
        if isinstance(target, int):
            return self.__target_of_type(target)
        if isinstance(target, Symbol):
            return self.__target_of_symbol(target)
        if isinstance(target, Type.StructField):
            return self.__target_of_field(target)
        return self.__target_of_variant(target)

    def __target_of_symbol(self, symbol: Symbol) -> Target | None:
        """A symbol's declaration site.

        ``Symbol.span`` is the name span registered at declaration time,
        which for a local or a parameter *is* the declaration.  A callable or
        type symbol has a type whose ``custom_def`` knows the declaration it came
        from, which is what an imported symbol resolves to — its original
        definition rather than the import statement.
        """
        if symbol.kind is SymbolKind.Variable and symbol.span is not None and self.__real(symbol.span):
            return Target(
                name=symbol.name,
                kind=DeclarationKind.VARIABLE,
                span=symbol.span,
                type_name=self.__name(symbol.type_id),
                stdlib=self.__is_stdlib(symbol.span.path),
            )
        from_type = self.__target_of_type(symbol.type_id)
        if from_type is not None:
            return from_type
        if symbol.span is None or not self.__real(symbol.span):
            return None
        return Target(
            name=symbol.name,
            kind=self.__kind_of_symbol(symbol.kind),
            span=symbol.span,
            type_name=self.__name(symbol.type_id),
            stdlib=self.__is_stdlib(symbol.span.path),
        )

    def __target_of_type(self, type_id: int) -> Target | None:
        """The declaration a type id came from, if it has one."""
        match self.__view.type_of(type_id):
            case Type.StructType(custom_def=custom_def):
                return self.__target_of_def(custom_def.name, DeclarationKind.STRUCT, custom_def.span)
            case Type.EnumType(custom_def=custom_def):
                return self.__target_of_def(custom_def.name, DeclarationKind.ENUM, custom_def.span)
            case Type.TraitType(custom_def=custom_def):
                return self.__target_of_def(custom_def.name, DeclarationKind.TRAIT, custom_def.span)
            case Type.AliasType(custom_def=custom_def):
                return self.__target_of_def(custom_def.name, DeclarationKind.ALIAS, custom_def.span)
            case Type.FunctionType(custom_def=custom_def):
                return self.__target_of_def(
                    custom_def.name,
                    DeclarationKind.FUNCTION,
                    custom_def.span,
                    signature=self.__signature(type_id, custom_def.name),
                )
            case Type.MethodType(custom_def=custom_def):
                return self.__target_of_def(
                    custom_def.name,
                    DeclarationKind.METHOD,
                    custom_def.span,
                    signature=self.__signature(type_id, custom_def.name),
                )
            case _:
                return None

    def __target_of_def(
        self,
        name: str,
        kind: DeclarationKind,
        span: SrcSpan,
        type_name: str | None = None,
        signature: str | None = None,
    ) -> Target | None:
        if not self.__real(span):
            return None
        return Target(
            name=name,
            kind=kind,
            span=span,
            type_name=type_name,
            signature=signature,
            stdlib=self.__is_stdlib(span.path),
        )

    def __signature(self, type_id: int, name: str) -> str | None:
        """Render a callable's signature from the analysis, not from the text.

        The parameters and return type are taken from the instantiated type, so a
        call through ``Pair<f64>.of`` shows ``f64`` where the declaration is
        written with ``T``.
        """
        resolved = self.__view.type_of(type_id)
        if not isinstance(resolved, (Type.FunctionType, Type.MethodType)):
            return None
        rendered = ", ".join(
            f"{parameter}: {type_name}"
            for parameter, type_name in self.__view.parameters_of(type_id)
        )
        return f"fn {name}({rendered}) -> {self.__view.return_type_name(type_id)}"

    def __target_of_field(self, field: Type.StructField) -> Target | None:
        if field.span is None or not self.__real(field.span):
            return None
        return Target(
            name=field.name,
            kind=DeclarationKind.FIELD,
            span=field.span,
            type_name=self.__name(field.type_id),
            stdlib=self.__is_stdlib(field.span.path),
        )

    def __target_of_variant(self, variant: Type.EnumVariant) -> Target | None:
        if variant.span is None or not self.__real(variant.span):
            return None
        payload = None if variant.payload_type is None else self.__name(variant.payload_type)
        return Target(
            name=variant.name,
            kind=DeclarationKind.VARIANT,
            span=variant.span,
            type_name=payload,
            stdlib=self.__is_stdlib(variant.span.path),
        )

    # ── small helpers ─────────────────────────────────────────────────────────
    #
    # These are methods rather than module functions on purpose: a module-level
    # private referenced from a class body would be name-mangled (AGENTS.md).

    def __name(self, type_id: int | None) -> str | None:
        """Rendered name of a type, from the analysis."""
        return self.__view.type_name(type_id)

    def __is_stdlib(self, path: Path) -> bool:
        """True when *path* is part of the standard library."""
        root = self.__std_root
        if root is None:
            return False
        try:
            return path.resolve().is_relative_to(root.resolve())
        except OSError:
            return False

    @staticmethod
    def __kind_of_symbol(kind: SymbolKind) -> DeclarationKind:
        match kind:
            case SymbolKind.Function:
                return DeclarationKind.FUNCTION
            case SymbolKind.Type:
                return DeclarationKind.STRUCT
            case _:
                return DeclarationKind.VARIABLE

    @staticmethod
    def __target_from_declaration(declaration: Declaration, stdlib: bool) -> Target:
        return Target(
            name=declaration.name,
            kind=declaration.kind,
            span=Navigator.__span_of(declaration),
            type_name=declaration.type_name,
            container=declaration.container,
            stdlib=stdlib,
        )

    @staticmethod
    def __span_of(declaration: Declaration) -> SrcSpan:
        return declaration.span if declaration.span is not None else SrcSpan.empty()

    @staticmethod
    def __real(span: SrcSpan) -> bool:
        """True when *span* points at real source text (synthesized spans do not)."""
        return not (
            span.start.row == 0
            and span.start.col == 0
            and span.end.row == 0
            and span.end.col == 0
        )

    @staticmethod
    def __contains(span: SrcSpan, row: int, col: int) -> bool:
        """True when a caret at ``(row, col)`` is inside *span*.

        The end is inclusive: a caret right after an identifier still refers to
        it, which is what "go to definition" on the tail of a name means.
        """
        if (row, col) < (span.start.row, span.start.col):
            return False
        return (row, col) <= (span.end.row, span.end.col)

    def __spans(self, span: SrcSpan, path: Path, row: int, col: int) -> bool:
        return span.path.resolve() == path.resolve() and self.__contains(span, row, col)

    @staticmethod
    def __size(span: SrcSpan) -> tuple[int, int]:
        return (span.end.row - span.start.row, span.end.col - span.start.col)
