"""In-process analysis session: the editor-facing half of the compiler pipeline.

The session runs the **analysis prefix** of the pipeline — lex, parse, desugar,
prelude injection, restricted-operation check, global resolution, type
finalization and type checking — and stops there.  CFG lowering, LLVM emission,
clang and executable generation are deliberately not involved, so a file can be
analyzed without producing a program (plan §5.8.2, §5.12).

It is designed to live inside a long-running process (the language server): it
takes text from a :class:`~compiler.analysis.documents.DocumentStore` rather than
reading files directly, returns diagnostics instead of printing or exiting, and
never writes to standard output.

Error handling follows plan §5.11 layer one: an error aborts the analysis and is
reported as a structured diagnostic.  "Keep going after an error" and multiple
diagnostics per document are the next layer (P4).
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
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
from compiler.analysis.index import Index, build_index
from compiler.analysis.package_map import PackageMap
from compiler.analysis.passes.desugar import Desugar
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

#: Bumped when analysis semantics change, so snapshot keys from an older
#: compiler never look reusable to a newer one (plan §5.12).
ANALYSIS_FORMAT = 1

#: The errors the analysis pipeline is expected to raise.  Anything else is a
#: compiler bug: it is not swallowed here, so it stays visible while the analysis
#: layers above (the language server, in P3) decide what to do with it.
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
    #: (plan §5.4: TextMate stays the base grammar, these tokens overlay it).
    tokens: Mapping[Path, tuple[Token, ...]] = field(
        default_factory=dict[Path, tuple[Token, ...]]
    )
    units: Mapping[int, UnitData] = field(default_factory=dict[int, UnitData])
    type_ctx: TypeCtx | None = None
    #: Every definition the checker ran — the entry-reachable ones *and* the
    #: root package's remaining definitions, which are checked but never
    #: generated.  The editor needs both: navigation has to answer for a function
    #: `main` never calls, and rename has to know which bodies were actually
    #: analysed before it can trust its reference set (plan §7 P5/P7).
    def_points: Mapping[int, DefPoint] = field(default_factory=dict[int, DefPoint])
    #: The stage that stopped the run, or ``None`` when it completed.
    failed_stage: Stage | None = None
    #: Declaration index built from the units, or ``None`` when the run stopped.
    index: Index | None = None
    #: Editor version of each analyzed document (``None`` when unknown).
    versions: Mapping[Path, int | None] = field(default_factory=dict[Path, int | None])
    #: Snapshot key: every input's hash and version plus the compile flags.  A
    #: result is only reusable when this matches, which is what keeps stale
    #: definitions or diagnostics from surviving an edit (plan §5.12).
    key: tuple[object, ...] = ()

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
    from the source text (plan §5.3).
    """

    def __init__(
        self,
        *,
        compiler_root: Path | None = None,
        packages: PackageMap | None = None,
        raw_pointers: bool = False,
    ) -> None:
        """Configure one session.

        ``compiler_root`` is a YIAN *checkout* root (the same value as
        ``--compiler-root``); the standard library source root is derived from it
        by :func:`resolve_stdlib_root`, which also covers ``$YIAN_LIB`` /
        ``$YIAN_ROOT`` and this checkout.  In package mode the map's ``std`` entry
        wins (plan §5.5).
        """
        self.__packages = packages
        self.__raw_pointers = raw_pointers
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
    ) -> AnalysisResult:
        """Analyze *paths* and return diagnostics plus the resolved state.

        *documents* supplies in-memory text; anything not in the overlay is read
        from disk, which covers the standard library and untouched dependencies.
        A missing path raises :class:`FileNotFoundError` — that is a caller
        mistake, not a diagnostic about a document.
        """
        store = documents if documents is not None else DocumentStore()
        src_files = collect_an_files(paths, overlay=store)
        sources = {path: self.__text(store, path) for path in src_files}
        key = self.__snapshot_key(sources, store)

        tokens = self.__lex(src_files, sources)
        if isinstance(tokens, AnalysisResult):
            return self.__keyed(tokens, key, store)
        # Kept for the stages that can still fail: a file the parser rejects has
        # no symbol table, but its tokens are what a degraded completion or
        # semantic pass works from (plan §7 P6).
        lexed = {path: tuple(tokens[index]) for index, path in enumerate(src_files)}
        programs = self.__parse(tokens, sources)
        if isinstance(programs, AnalysisResult):
            return self.__keyed(self.__with_tokens(programs, lexed), key, store)

        try:
            for program in programs:
                Desugar(program).run()
        except ANALYSIS_ERRORS as error:
            return self.__keyed(
                self.__with_tokens(self.__failed(error, Stage.DESUGAR, sources), lexed), key, store
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
            return self.__keyed(
                self.__with_tokens(self.__failed(error, Stage.PRELUDE, sources, units), lexed), key, store
            )

        try:
            check_restricted_ops(units.values())
        except ANALYSIS_ERRORS as error:
            return self.__keyed(
                self.__with_tokens(
                    self.__failed(error, Stage.RESTRICTED_OPS, sources, units), lexed
                ),
                key,
                store,
            )

        type_ctx = TypeCtx(raw_pointers=self.__raw_pointers)
        resolver = GlobalResolve(units, type_ctx, self.__packages, source_trust.stdlib_root)
        try:
            resolver.run()
        except ANALYSIS_ERRORS as error:
            return self.__keyed(
                self.__with_tokens(
                    self.__failed(error, Stage.RESOLVE, sources, units, type_ctx), lexed
                ),
                key,
                store,
            )

        try:
            type_ctx.finalize()
        except ANALYSIS_ERRORS as error:
            return self.__keyed(
                self.__with_tokens(
                    self.__failed(error, Stage.FINALIZE, sources, units, type_ctx), lexed
                ),
                key,
                store,
            )

        # The session analyzes text, not a build: an entry-less or entry-broken
        # file set is a normal editor state (plan §5.11 layer one), and a file
        # with several broken definitions reports all of them (layer three,
        # granularity = top-level definition).
        checker = TypeCheck(
            units,
            type_ctx,
            self.__packages,
            require_entry=require_entry,
            entry_optional=True,
            recover=True,
        )
        try:
            checker.run()
        except ANALYSIS_ERRORS as error:
            # Everything recoverable was collected by the checker; what reaches
            # here is a failure before the worklist started (the program entry).
            return self.__keyed(
                self.__with_tokens(
                    self.__failed(error, Stage.TYPE_CHECK, sources, units, type_ctx), lexed
                ),
                key,
                store,
            )

        # A recovered error does not stop the index from being built, so a file
        # with one broken definition still answers navigation for the others.
        index = build_index(units, type_ctx, resolver.import_edges(), self.__packages)
        return AnalysisResult(
            diagnostics=checker.export_diagnostics(),
            sources=sources,
            tokens=lexed,
            units=units,
            type_ctx=type_ctx,
            def_points=checker.export(),
            index=index,
            versions=store.versions(),
            key=key,
        )

    def snapshot_key(
        self, paths: Sequence[Path], *, documents: DocumentStore | None = None
    ) -> tuple[object, ...]:
        """The key :meth:`analyze` would produce for *paths* right now.

        A caller that keeps the result of an earlier run compares this key to
        ``result.key`` and reuses that result when they match, which is how the
        language server avoids re-analyzing an unchanged project (plan §5.12).
        The key covers each input's text and editor version plus the compile
        flags, so it never matches across an edit.
        """
        store = documents if documents is not None else DocumentStore()
        src_files = collect_an_files(paths, overlay=store)
        sources = {path: self.__text(store, path) for path in src_files}
        return self.__snapshot_key(sources, store)

    def __with_tokens(
        self, result: AnalysisResult, tokens: Mapping[Path, tuple[Token, ...]]
    ) -> AnalysisResult:
        """Attach the lexed tokens to a failed run.

        The lexer succeeded even though a later stage did not, and those tokens
        are what a degraded editor answer is built from.
        """
        result.tokens = tokens
        return result

    def __snapshot_key(self, sources: Mapping[Path, str], store: DocumentStore) -> tuple[object, ...]:
        """Key a snapshot by its inputs: text hashes, versions, and compile flags."""
        entries = tuple(
            sorted(
                (str(path), hashlib.sha256(text.encode("utf-8")).hexdigest(), store.version(path))
                for path, text in sources.items()
            )
        )
        return (ANALYSIS_FORMAT, entries, self.__raw_pointers)

    def __keyed(
        self, result: AnalysisResult, key: tuple[object, ...], store: DocumentStore
    ) -> AnalysisResult:
        """Attach the snapshot key to a failed run.

        A run that stopped at a diagnostic is still a complete description of its
        inputs, so it is keyed like a successful one: the editor reuses it while
        the text is unchanged instead of re-analyzing a broken file on every
        request (plan §5.12).
        """
        result.key = key
        result.versions = store.versions()
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

    def __failed(
        self,
        error: Exception,
        stage: Stage,
        sources: Mapping[Path, str],
        units: Mapping[int, UnitData] | None = None,
        type_ctx: TypeCtx | None = None,
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
            type_ctx=type_ctx,
            failed_stage=stage,
        )


__all__ = ["AnalysisResult", "AnalysisSession", "collect_an_files"]
