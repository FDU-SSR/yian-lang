"""Shared source-to-HIR pipeline for builds, analysis-only runs, and the editor.

Full analysis selects compile-time branches during type checking, then runs
definite-assignment analysis.
It returns diagnostics and code-generation inputs without emitting files. The
caller chooses strict failure or per-definition recovery; the editor can also
request a syntax-only result while a document is being edited.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from yianc.analysis.diagnostics import (
    Diagnostic,
    Severity,
    Stage,
    diagnostic_from_error,
    format_source_error,
)
from yianc.analysis.documents import DocumentStore
from yianc.analysis.error import AnalysisError, EditorIncomplete
from yianc.analysis.facts.names import NameRef, ExpressionFact, ScopeFact
from yianc.analysis.resolution.aliases import AliasInfo
from yianc.analysis.symbol.symbol import AliasDefId
from yianc.analysis.lowering.state import DefinitionState
from yianc.analysis.state import SemanticState
from yianc.analysis.package_map import PackageMap
from yianc.analysis.passes.desugar import Desugar
from yianc.analysis.passes.match_coverage import MatchCoverage
from yianc.analysis.passes.definite_assignment import DefiniteAssignment
from yianc.analysis.passes.global_resolve import GlobalResolve
from yianc.analysis.passes.prelude import inject_prelude
from yianc.analysis.passes.restricted_ops import check_restricted_ops
from yianc.analysis.passes.type_check import TypeCheck
from yianc.analysis.source_provenance import build_source_trust, resolve_stdlib_root
from yianc.analysis.ty.context import TypeCtx
from yianc.analysis.unit.def_point import DefPoint
from yianc.analysis.unit.unit_data import UnitData
from yianc.error import CompilerError
from yianc.frontend.lex import token as Tok
from yianc.frontend.lex.lexer import LexError, Lexer
from yianc.frontend.lex.token import Token
from yianc.frontend.lex.position import SrcSpan
from yianc.frontend.parse import ast as AST
from yianc.frontend.parse.error import ParseError
from yianc.frontend.parse.parser import Parser
from yianc.utils.log import format_ast_output

TypeSizeFactory = Callable[[TypeCtx, Mapping[int, str], bool], Callable[[int], int]]

#: The user-facing errors translated into diagnostics by the analysis pipeline.
#: Other exception types propagate as internal errors.
ANALYSIS_ERRORS = (CompilerError, LexError, ParseError, AnalysisError)


def collect_an_files(paths: Sequence[Path], *, overlay: DocumentStore | None = None) -> list[Path]:
    """Expand *paths* into a sorted list of ``.an`` files.

    A file path is taken as-is, a directory is searched recursively.  A path that
    exists only in *overlay* — an unsaved buffer for a file that is not on disk
    yet — is accepted too.  Anything else raises :class:`FileNotFoundError`
    instead of exiting the process, so the session stays usable inside a
    long-running server.
    """
    files: list[Path] = []
    for path in paths:
        if path.is_file():
            if path.suffix == ".an":
                files.append(path)
            continue
        if path.is_dir():
            files.extend(sorted(candidate for candidate in path.rglob("*.an") if candidate.is_file()))
            continue
        if overlay is not None and path in overlay:
            files.append(path)
            continue
        raise FileNotFoundError(f"path does not exist: {path}")
    return files


@dataclass
class AnalysisResult:
    """What one analysis run produced.

    ``diagnostics`` is empty when the document set is clean.  ``ok`` mirrors the
    CLI's notion of success (no error-severity diagnostic).
    """

    diagnostics: tuple[Diagnostic, ...] = ()
    sources: Mapping[Path, str] = field(default_factory=dict[Path, str])
    #: Lexed tokens per file.  Semantic highlighting needs the exact extent of
    #: every name and the primitive type keywords, which only the lexer knows
    # (TextMate stays the base grammar, these tokens overlay it).
    tokens: Mapping[Path, tuple[Token, ...]] = field(
        default_factory=dict[Path, tuple[Token, ...]]
    )
    units: Mapping[int, UnitData] = field(default_factory=dict[int, UnitData])
    programs: tuple[AST.Program, ...] = ()
    ast_dump: str | None = None
    type_ctx: TypeCtx | None = None
    #: Every definition the checker ran — the entry-reachable ones *and* the
    #: root package's remaining definitions, which are checked but never
    #: generated.  The editor needs both: navigation has to answer for a function
    #: `main` never calls, and rename has to know which bodies were actually
    # analysed before it can trust its reference set.
    def_points: Mapping[int, DefPoint] = field(default_factory=dict[int, DefPoint])
    generated_def_points: Mapping[int, DefPoint] = field(default_factory=dict[int, DefPoint])
    procedures: tuple[tuple[int, AST.Block, int], ...] = ()
    name_refs: tuple[NameRef, ...] = ()
    expression_facts: tuple[ExpressionFact, ...] = ()
    conflicting_expression_spans: tuple[SrcSpan, ...] = ()
    scope_facts: tuple[ScopeFact, ...] = ()
    editor_recovery: bool = False
    contextual_name_refs: tuple[NameRef, ...] = ()
    reference_facts_complete: bool = True
    aliases: Mapping[AliasDefId, AliasInfo] = field(default_factory=dict[AliasDefId, AliasInfo])
    entry_type_id: int | None = None
    unit_names: Mapping[int, str] = field(default_factory=dict[int, str])
    timings: Mapping[str, float] = field(default_factory=dict[str, float])
    #: The stage that stopped the run, or ``None`` when it completed.
    failed_stage: Stage | None = None
    #: Resolved import edges and package context for protocol-neutral queries.
    #: ``None`` means analysis stopped before these facts were complete.
    import_edges: Mapping[int, tuple[int, ...]] | None = None
    packages: PackageMap | None = None
    #: True when the run stopped after desugaring: its diagnostics are the
    # front end's, and it carries no names, types or index.
    syntax_only: bool = False

    def ok(self) -> bool:
        """True when no diagnostic has error severity."""
        return not any(diagnostic.severity is Severity.ERROR for diagnostic in self.diagnostics)

    def formatted(self) -> str:
        """Render every diagnostic against its own document text."""
        return "\n".join(
            format_source_error(diagnostic, self.sources.get(diagnostic.span.path, ""))
            for diagnostic in self.diagnostics
        )


class AnalysisSession:
    """Reusable analysis entry point.

    A session is cheap to keep alive: configuration (standard library root,
    package map, pointer mode) is resolved once, while every :meth:`analyze` call
    starts from text and therefore never observes a half-mutated AST — the passes
    rewrite their units in place, which is exactly why analysis always starts
    from the source text.
    """

    def __init__(
        self,
        *,
        compiler_root: Path | None = None,
        packages: PackageMap | None = None,
        raw_pointers: bool = False,
        allow_ffi: bool = False,
        type_size_factory: TypeSizeFactory,
    ) -> None:
        """Configure one session.

        ``compiler_root`` is a YIAN *checkout* root (the same value as
        ``--compiler-root``); the standard library source root is derived from it
        by :func:`resolve_stdlib_root`, which also covers ``$YIAN_LIB`` /
        ``$YIAN_ROOT`` and this checkout.  In package mode the map's ``std`` entry
        wins.
        """
        self.__packages = packages
        self.__raw_pointers = raw_pointers
        self.__allow_ffi = allow_ffi
        self.__type_size_factory = type_size_factory
        self.__std_root = (
            packages.packages["std"].source_root
            if packages is not None
            else resolve_stdlib_root(compiler_root)
        )

    @property
    def std_root(self) -> Path:
        """The resolved standard library *source* root (not the checkout root)."""
        return self.__std_root

    def analyze(
        self,
        paths: Sequence[Path],
        *,
        documents: DocumentStore | None = None,
        require_entry: bool = False,
        syntax_only: bool = False,
        entry_optional: bool = True,
        recover: bool = True,
        capture_ast_dump: bool = False,
        editor_recovery: bool = False,
    ) -> AnalysisResult:
        """Analyze *paths* and return diagnostics plus the resolved state.

        *documents* supplies in-memory text; anything not in the overlay is read
        from disk, which covers the standard library and untouched dependencies.
        A missing path raises :class:`FileNotFoundError` — that is a caller
        mistake, not a diagnostic about a document.

        With *syntax_only* the run stops after desugaring. Full analysis also
        validates compile-time conditions and definite assignment. ``recover``
        controls whether independently checked definitions continue after an error.
        """
        store = documents if documents is not None else DocumentStore()
        src_files = collect_an_files(paths, overlay=store)
        sources = {path: self.__text(store, path) for path in src_files}
        timings: dict[str, float] = {}

        frontend_diagnostics: list[Diagnostic] = []
        if editor_recovery:
            started = time.perf_counter()
            tokens: list[list[Tok.Token]] = []
            programs: list[AST.Program] = []
            for path in src_files:
                lexer = Lexer(path, text=sources[path])
                try:
                    lexer.lex()
                except LexError as error:
                    frontend_diagnostics.append(diagnostic_from_error(error, stage=Stage.LEX))
                    # An unsafe lexical suffix is never interpreted as source.
                    prefix = lexer.export()
                    unfinished: list[int] = []
                    for index, token in enumerate(prefix):
                        if isinstance(token, Tok.FStrStart):
                            unfinished.append(index)
                        elif isinstance(token, Tok.FStrEnd) and unfinished:
                            unfinished.pop()
                    if unfinished:
                        del prefix[unfinished[0]:]
                    prefix.append(Tok.Punctuator(Tok.PunctuatorKind.EOF, error.span))
                source_tokens = lexer.export()
                tokens.append(source_tokens)
                parser = Parser(source_tokens, editor=True)
                program = parser.parse()
                frontend_diagnostics.extend(diagnostic_from_error(error, stage=Stage.PARSE) for error in parser.errors)
                try:
                    Desugar(program).run()
                except ANALYSIS_ERRORS as error:
                    frontend_diagnostics.append(diagnostic_from_error(error, stage=Stage.DESUGAR))
                    program = AST.Program(span=program.span, items=[])
                programs.append(program)
            timings["editor_frontend"] = time.perf_counter() - started
            lexed = {path: tuple(tokens[index]) for index, path in enumerate(src_files)}
        else:
            started = time.perf_counter()
            lex_result = self.__lex(src_files, sources)
            if isinstance(lex_result, AnalysisResult):
                return self.__mark_syntax(lex_result, syntax_only)
            tokens = lex_result
            timings["lex"] = time.perf_counter() - started
            # Kept for the stages that can still fail: a file the parser rejects has
            # no symbol table, but its tokens are what a degraded completion or
            # semantic pass works from.
            lexed = {path: tuple(tokens[index]) for index, path in enumerate(src_files)}
            started = time.perf_counter()
            parse_result = self.__parse(tokens, sources)
            if isinstance(parse_result, AnalysisResult):
                return self.__mark_syntax(self.__with_tokens(parse_result, lexed), syntax_only)
            programs = parse_result
            timings["parse"] = time.perf_counter() - started

            started = time.perf_counter()
            try:
                for program in programs:
                    Desugar(program).run()
            except ANALYSIS_ERRORS as error:
                return self.__mark_syntax(
                    self.__with_tokens(self.__failed(error, Stage.DESUGAR, sources), lexed),
                    syntax_only,
                )
            timings["desugar"] = time.perf_counter() - started
        ast_dump = format_ast_output(src_files, programs) if capture_ast_dump else None

        if syntax_only:
            # Everything the front end can decide, and nothing that needs the
            # program's names or types.
            return AnalysisResult(
                diagnostics=tuple(frontend_diagnostics),
                sources=sources,
                tokens=lexed,
                programs=tuple(programs),
                ast_dump=ast_dump,
                syntax_only=True,
                timings=timings,
            )

        units: dict[int, UnitData] = {
            index: UnitData(program=program, path=path, unit_id=index)
            for index, (program, path) in enumerate(zip(programs, src_files, strict=True))
        }

        source_trust = build_source_trust(self.__std_root)
        for unit in units.values():
            unit.is_stdlib = source_trust.is_stdlib(unit.path)
            unit.allows_restricted_ops = source_trust.allows_restricted_ops(unit.path)
            package_name = self.__packages.package_of(unit.path) if self.__packages is not None else None
            unit.allows_ffi = (
                self.__packages.packages[package_name].ffi
                if package_name is not None and self.__packages is not None
                else unit.is_stdlib or self.__allow_ffi
            )
            unit.symbol_ctx.allows_ffi = unit.allows_ffi

        try:
            inject_prelude(units.values())
        except ANALYSIS_ERRORS as error:
            return self.__with_tokens(self.__failed(error, Stage.PRELUDE, sources, units, ast_dump=ast_dump), lexed)

        started = time.perf_counter()
        if editor_recovery:
            for unit in units.values():
                try:
                    check_restricted_ops((unit,))
                except ANALYSIS_ERRORS as error:
                    frontend_diagnostics.append(diagnostic_from_error(error, stage=Stage.RESTRICTED_OPS))
        else:
            try:
                check_restricted_ops(units.values())
            except ANALYSIS_ERRORS as error:
                return self.__with_tokens(
                    self.__failed(error, Stage.RESTRICTED_OPS, sources, units, ast_dump=ast_dump), lexed
                )
        timings["restricted_ops"] = time.perf_counter() - started

        type_ctx = TypeCtx(raw_pointers=self.__raw_pointers)
        unit_names = self.__unit_names(units)
        type_size = self.__type_size_factory(type_ctx, unit_names, self.__raw_pointers)
        semantic = SemanticState(
            type_ctx,
            self.__raw_pointers,
            units,
            self.__packages,
            source_trust.stdlib_root,
            type_size,
        )
        semantic.editor_recovery = editor_recovery
        if editor_recovery:
            semantic.editor_syntax_regions = tuple(diagnostic.span for diagnostic in frontend_diagnostics
                                                   if diagnostic.stage is Stage.PARSE)
        resolver = GlobalResolve(semantic)

        def __semantic_failure(error: Exception, stage: Stage) -> AnalysisResult:
            result = self.__with_tokens(
                self.__failed(error, stage, sources, units, type_ctx, ast_dump), lexed
            )
            result.aliases = semantic.aliases.snapshot()
            result.name_refs = semantic.names.entries()
            result.contextual_name_refs = semantic.names.contextual_entries()
            result.reference_facts_complete = not semantic.names.has_conflicts()
            result.expression_facts = semantic.names.expression_entries()
            result.conflicting_expression_spans = semantic.names.expression_conflicts
            result.scope_facts = tuple(semantic.names.scopes)
            result.editor_recovery = editor_recovery
            result.diagnostics = tuple(frontend_diagnostics) + result.diagnostics
            return result

        started = time.perf_counter()
        try:
            resolver.run()
        except ANALYSIS_ERRORS as error:
            return __semantic_failure(error, Stage.RESOLVE)
        frontend_diagnostics.extend(diagnostic_from_error(error, stage=Stage.RESOLVE) for error in semantic.editor_errors
                                    if not isinstance(error, EditorIncomplete))
        timings["global_resolve"] = time.perf_counter() - started

        try:
            type_ctx.finalize()
        except ANALYSIS_ERRORS as error:
            if not editor_recovery:
                return __semantic_failure(error, Stage.FINALIZE)
            frontend_diagnostics.append(diagnostic_from_error(error, stage=Stage.FINALIZE))

        started = time.perf_counter()
        checker = TypeCheck(
            DefinitionState(semantic),
            require_entry=require_entry,
            entry_optional=entry_optional,
            recover=recover,
        )
        try:
            checker.run()
        except ANALYSIS_ERRORS as error:
            # Everything recoverable was collected by the checker; what reaches
            # here is a failure before the worklist started (the program entry).
            return __semantic_failure(error, Stage.TYPE_CHECK)

        all_def_points = checker.export()
        timings["type_check"] = time.perf_counter() - started

        coverage = MatchCoverage(all_def_points, type_ctx)
        try:
            coverage_errors = coverage.run(recover=recover)
        except ANALYSIS_ERRORS as error:
            return __semantic_failure(error, Stage.MATCH_COVERAGE)

        started = time.perf_counter()
        definite_assignment = DefiniteAssignment(all_def_points, type_ctx)
        try:
            definite_assignment.run()
        except ANALYSIS_ERRORS as error:
            return __semantic_failure(error, Stage.DEFINITE_ASSIGNMENT)
        timings["definite_assignment"] = time.perf_counter() - started
        assignment_errors = definite_assignment.export_errors()
        if assignment_errors and not recover:
            return __semantic_failure(assignment_errors[0], Stage.DEFINITE_ASSIGNMENT)

        diagnostics = list(frontend_diagnostics) + list(checker.export_diagnostics())
        diagnostics.extend(diagnostic_from_error(error, stage=Stage.MATCH_COVERAGE) for error in coverage_errors)
        diagnostics.extend(coverage.warnings())
        diagnostics.extend(
            diagnostic_from_error(error, stage=Stage.DEFINITE_ASSIGNMENT) for error in assignment_errors
        )
        diagnostics.sort(key=lambda diagnostic: (
            str(diagnostic.span.path), diagnostic.span.start.row, diagnostic.span.start.col, diagnostic.code
        ))
        generated = checker.export_generated()

        return AnalysisResult(
            diagnostics=tuple(diagnostics),
            sources=sources,
            tokens=lexed,
            units=units,
            programs=tuple(programs),
            ast_dump=ast_dump,
            type_ctx=type_ctx,
            def_points=all_def_points,
            generated_def_points=generated,
            procedures=semantic.procedures.entries(),
            name_refs=semantic.names.entries(),
            contextual_name_refs=semantic.names.contextual_entries(),
            reference_facts_complete=not semantic.names.has_conflicts(),
            expression_facts=semantic.names.expression_entries(),
            conflicting_expression_spans=semantic.names.expression_conflicts,
            scope_facts=tuple(semantic.names.scopes),
            editor_recovery=editor_recovery,
            aliases=semantic.aliases.snapshot(),
            entry_type_id=checker.entry_type_id,
            unit_names=unit_names,
            import_edges=resolver.import_edges(),
            packages=self.__packages,
            timings=timings,
        )

    def __unit_names(self, units: Mapping[int, UnitData]) -> dict[int, str]:
        names: dict[int, str] = {}
        for unit_id, unit in units.items():
            if self.__packages is None:
                names[unit_id] = unit.path.stem
                continue
            package = self.__packages.package_of(unit.path)
            if package is None:
                names[unit_id] = unit.path.stem
                continue
            root = self.__packages.packages[package].source_root
            try:
                relative = unit.path.resolve().relative_to(root)
            except ValueError:
                names[unit_id] = unit.path.stem
                continue
            names[unit_id] = "_".join((package, *relative.parts[:-1], relative.stem))
        return names

    def __with_tokens(
        self, result: AnalysisResult, tokens: Mapping[Path, tuple[Token, ...]]
    ) -> AnalysisResult:
        """Attach the lexed tokens to a failed run.

        Preserve tokens whenever analysis fails after lexing so editor queries
        can build degraded responses from the available syntax.
        """
        result.tokens = tokens
        return result

    # ── pipeline stages ────────────────────────────────────────────────────────

    def __text(self, store: DocumentStore, path: Path) -> str:
        try:
            return store.text(path)
        except OSError:
            # Missing or unreadable: the passes report it against an empty
            # document rather than the session crashing on I/O.
            return ""

    def __lex(self, src_files: list[Path], sources: Mapping[Path, str]) -> list[list[Token]] | AnalysisResult:
        token_lists: list[list[Token]] = []
        for src_file in src_files:
            lexer = Lexer(src_file, text=sources[src_file])
            try:
                lexer.lex()
            except ANALYSIS_ERRORS as error:
                return self.__failed(error, Stage.LEX, sources)
            token_lists.append(lexer.export())
        return token_lists

    def __parse(self, token_lists: list[list[Token]], sources: Mapping[Path, str]) -> list[AST.Program] | AnalysisResult:
        programs: list[AST.Program] = []
        for tokens in token_lists:
            try:
                programs.append(Parser(tokens).parse())
            except ANALYSIS_ERRORS as error:
                return self.__failed(error, Stage.PARSE, sources)
        return programs

    def __mark_syntax(self, result: AnalysisResult, syntax_only: bool) -> AnalysisResult:
        """Flag a failed front-end result with the mode it was asked for.

        A run that stops during lexing, parsing or desugaring has no index and no
        types, so it *is* a syntax-only answer even though it took the error path
; a caller that checks the flag must not be told
        otherwise.
        """
        result.syntax_only = syntax_only
        return result

    def __failed(
        self,
        error: Exception,
        stage: Stage,
        sources: Mapping[Path, str],
        units: Mapping[int, UnitData] | None = None,
        type_ctx: TypeCtx | None = None,
        ast_dump: str | None = None,
    ) -> AnalysisResult:
        diagnostic = diagnostic_from_error(error, stage=stage)
        resolved_sources = dict(sources)
        path = diagnostic.span.path
        if path not in resolved_sources:
            resolved_sources[path] = self.__text(DocumentStore(), path)
        return AnalysisResult(
            diagnostics=(diagnostic,),
            sources=resolved_sources,
            units=units if units is not None else {},
            programs=() if units is None else tuple(unit.program for unit in units.values()),
            ast_dump=ast_dump,
            type_ctx=type_ctx,
            failed_stage=stage,
        )


__all__ = ["AnalysisResult", "AnalysisSession", "collect_an_files"]
