"""Shared source-to-HIR pipeline for builds, analysis-only runs, and the editor.

Full analysis runs through compile-time specialization and definite assignment.
It returns diagnostics and code-generation inputs without emitting files. The
caller chooses strict failure or per-definition recovery; the editor can also
request a syntax-only result while a document is being edited.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from compiler.analysis.diagnostics import (
    Diagnostic,
    Severity,
    Stage,
    diagnostic_from_error,
    format_source_error,
)
from compiler.analysis.documents import DocumentStore
from compiler.analysis.error import AnalysisError
from compiler.analysis.lowering.sem_ctx import SemCtx
from compiler.analysis.package_map import PackageMap
from compiler.analysis.passes.desugar import Desugar
from compiler.analysis.passes.comptime_if import ComptimeIfSpecializer
from compiler.analysis.passes.definite_assignment import DefiniteAssignment
from compiler.analysis.passes.global_resolve import GlobalResolve
from compiler.analysis.passes.prelude import inject_prelude
from compiler.analysis.passes.restricted_ops import check_restricted_ops
from compiler.analysis.passes.type_check import TypeCheck
from compiler.analysis.source_provenance import build_source_trust, resolve_stdlib_root
from compiler.analysis.ty.context import TypeCtx
from compiler.analysis.unit.def_point import DefPoint
from compiler.analysis.unit.unit_data import UnitData
from compiler.error import CompilerError
from compiler.frontend.lex.lexer import LexError, Lexer
from compiler.frontend.lex.token import Token
from compiler.frontend.parse import ast as AST
from compiler.frontend.parse.error import ParseError
from compiler.frontend.parse.parser import Parser
from compiler.utils.log import format_ast_output

TypeSizeFactory = Callable[[TypeCtx, Mapping[int, str], bool], Callable[[int], int]]

#: The errors the analysis pipeline is expected to raise.  Anything else is a
#: compiler bug: it is not swallowed here, so it stays visible while the analysis
# layers above (such as the language server) decide what to do with it.
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
    sem_ctx: SemCtx | None = None
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

        started = time.perf_counter()
        tokens = self.__lex(src_files, sources)
        if isinstance(tokens, AnalysisResult):
            return self.__mark_syntax(tokens, syntax_only)
        timings["lex"] = time.perf_counter() - started
        # Kept for the stages that can still fail: a file the parser rejects has
        # no symbol table, but its tokens are what a degraded completion or
        # semantic pass works from.
        lexed = {path: tuple(tokens[index]) for index, path in enumerate(src_files)}
        started = time.perf_counter()
        programs = self.__parse(tokens, sources)
        if isinstance(programs, AnalysisResult):
            return self.__mark_syntax(self.__with_tokens(programs, lexed), syntax_only)
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
                diagnostics=(),
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

        try:
            inject_prelude(units.values())
        except ANALYSIS_ERRORS as error:
            return self.__with_tokens(self.__failed(error, Stage.PRELUDE, sources, units, ast_dump=ast_dump), lexed)

        started = time.perf_counter()
        try:
            check_restricted_ops(units.values())
        except ANALYSIS_ERRORS as error:
            return self.__with_tokens(
                self.__failed(error, Stage.RESTRICTED_OPS, sources, units, ast_dump=ast_dump), lexed
            )
        timings["restricted_ops"] = time.perf_counter() - started

        type_ctx = TypeCtx(raw_pointers=self.__raw_pointers)
        ctx = SemCtx(type_ctx, self.__raw_pointers, units, self.__packages, source_trust.stdlib_root)
        resolver = GlobalResolve(ctx)
        started = time.perf_counter()
        try:
            resolver.run()
        except ANALYSIS_ERRORS as error:
            return self.__with_tokens(
                self.__failed(error, Stage.RESOLVE, sources, units, type_ctx, ast_dump), lexed
            )
        timings["global_resolve"] = time.perf_counter() - started

        try:
            type_ctx.finalize()
        except ANALYSIS_ERRORS as error:
            return self.__with_tokens(
                self.__failed(error, Stage.FINALIZE, sources, units, type_ctx, ast_dump), lexed
            )

        started = time.perf_counter()
        checker = TypeCheck(
            ctx,
            require_entry=require_entry,
            entry_optional=entry_optional,
            recover=recover,
        )
        try:
            checker.run()
        except ANALYSIS_ERRORS as error:
            # Everything recoverable was collected by the checker; what reaches
            # here is a failure before the worklist started (the program entry).
            return self.__with_tokens(
                self.__failed(error, Stage.TYPE_CHECK, sources, units, type_ctx, ast_dump), lexed
            )

        all_def_points = checker.export()
        ctx.declare_def_points(all_def_points)
        unit_names = self.__unit_names(units)
        specializer = ComptimeIfSpecializer(
            ctx, self.__type_size_factory(type_ctx, unit_names, self.__raw_pointers)
        )
        try:
            comptime_errors = specializer.run(recover=recover)
        except ANALYSIS_ERRORS as error:
            return self.__with_tokens(
                self.__failed(error, Stage.COMPTIME, sources, units, type_ctx, ast_dump), lexed
            )
        timings["type_check"] = time.perf_counter() - started

        started = time.perf_counter()
        definite_assignment = DefiniteAssignment(ctx)
        try:
            definite_assignment.run()
        except ANALYSIS_ERRORS as error:
            return self.__with_tokens(
                self.__failed(error, Stage.DEFINITE_ASSIGNMENT, sources, units, type_ctx, ast_dump), lexed
            )
        timings["definite_assignment"] = time.perf_counter() - started
        assignment_errors = definite_assignment.export_errors()
        if assignment_errors and not recover:
            return self.__with_tokens(
                self.__failed(assignment_errors[0], Stage.DEFINITE_ASSIGNMENT, sources, units, type_ctx, ast_dump), lexed
            )

        diagnostics = list(checker.export_diagnostics())
        diagnostics.extend(diagnostic_from_error(error, stage=Stage.COMPTIME) for error in comptime_errors)
        diagnostics.extend(
            diagnostic_from_error(error, stage=Stage.DEFINITE_ASSIGNMENT) for error in assignment_errors
        )
        diagnostics.sort(key=lambda diagnostic: (
            str(diagnostic.span.path), diagnostic.span.start.row, diagnostic.span.start.col, diagnostic.code
        ))
        generated = specializer.generated_definitions(checker.export_generated(), checker.entry_type_id)

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
            sem_ctx=ctx,
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

        The lexer succeeded even though a later stage did not, and those tokens
        are what a degraded editor answer is built from.
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
