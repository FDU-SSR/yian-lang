from __future__ import annotations

#! /usr/bin/env python3

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import NoReturn, cast

from llvmlite import ir

from compiler.analysis.diagnostics import (
    Severity,
    Stage,
    diagnostic_from_error,
    format_source_error,
)
from compiler.analysis.documents import Document, DocumentStore
from compiler.analysis.error import AnalysisError
from compiler.analysis.package_map import PackageMap
from compiler.analysis.positions import path_to_uri, to_lsp_range
from compiler.analysis.session import AnalysisSession, collect_an_files
from compiler.analysis.lowering.sem_ctx import SemCtx
from compiler.analysis.passes.definite_assignment import DefiniteAssignment
from compiler.analysis.passes.comptime_if import ComptimeIfSpecializer
from compiler.utils.log import CompilerLog
from compiler.utils.log import (
    format_ast_output, format_cfg_output,
    format_hir_output, format_token_output,
)
from compiler.analysis.passes.desugar import Desugar
from compiler.analysis.passes.global_resolve import GlobalResolve
from compiler.analysis.passes.prelude import inject_prelude
from compiler.analysis.passes.restricted_ops import check_restricted_ops
from compiler.analysis.source_provenance import build_source_trust, resolve_stdlib_root
from compiler.format import format_text
from compiler.analysis.passes.type_check import TypeCheck
from compiler.analysis.ty.context import TypeCtx
from compiler.analysis.unit.unit_data import UnitData
from compiler.codegen.cfg import ir as CFG_IR
from compiler.codegen.cfg.passes.cleanup import Cleanup
from compiler.codegen.cfg.passes.insert_checks import InsertChecks
from compiler.codegen.cfg.passes.translator import CfgTranslator
from compiler.codegen.error import CodegenError
from compiler.codegen.llvm.emit import Emitter
from compiler.codegen.llvm.module import LLModule, apply_target
from compiler.codegen.llvm.translator import LLTranslator
from compiler.codegen.llvm.types import LLTypeCtx
from compiler.runtime_lib import RuntimeBuildError, ensure_archive, ensure_object
from compiler.error import CompilerError
from compiler.frontend.lex.lexer import Lexer, LexError
from compiler.frontend.lex.token import Token
from compiler.frontend.parse import ast as AST
from compiler.frontend.parse.parser import ParseError, Parser


def parse_cli(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse command line arguments.

    Current behavior only accepts path arguments. Keep this function as the
    single place for future CLI option extensions.
    """
    parser = argparse.ArgumentParser(
        prog="compiler/main.py",
        description="Yian compiler entrypoint.",
    )
    parser.add_argument(
        "paths",
        nargs="+",
        type=Path,
        metavar="PATH",
        help="Input path(s).",
    )
    parser.add_argument(
        "-t", "--target",
        choices=["none", "exe", "ll", "bc", "obj", "asm"],
        default="exe",
        help="Output target kind (default: exe). Use 'none' for analysis only.",
    )
    parser.add_argument(
        "-o", "--output",
        type=Path,
        metavar="PATH",
        default=None,
        help="Output file path (default: derived from first input file).",
    )
    parser.add_argument(
        "-O",
        type=int,
        metavar="LEVEL",
        default=0,
        choices=[0, 1, 2, 3],
        help="Optimization level: LLVM IR passes + backend + clang link (default: 0).",
    )
    parser.add_argument(
        "--profile",
        action="store_true",
        default=False,
        help="Print per-phase timing information.",
    )
    parser.add_argument(
        "--dump",
        action="store_true",
        default=False,
        help=(
            "Write intermediate dumps (tokens.txt/ast.txt/hir.txt/cfg.txt, plus "
            "ir.ll when codegen runs) into build/. Default: off."
        ),
    )
    parser.add_argument(
        "--log-spec",
        type=str,
        metavar="SPEC",
        default="",
        help="Log configuration, e.g. 'all=INFO,type_check=DEBUG'.",
    )
    parser.add_argument(
        "--log-file",
        type=Path,
        metavar="PATH",
        default=None,
        help="Write log output to PATH in addition to stderr.",
    )
    parser.add_argument(
        "--packages",
        type=Path,
        metavar="PATH",
        default=None,
        help="Package map JSON file (enables package-mode import resolution).",
    )
    parser.add_argument(
        "--compiler-root",
        type=Path,
        metavar="PATH",
        default=None,
        help=(
            "YIAN checkout root used to locate the standard library "
            "(default: $YIAN_LIB, then $YIAN_ROOT, then this checkout)."
        ),
    )
    parser.add_argument(
        "--analyze",
        action="store_true",
        default=False,
        help=(
            "Run the analysis prefix only (lex → parse → resolve → type check) and "
            "report diagnostics; no code generation, no build/ output."
        ),
    )
    parser.add_argument(
        "--json",
        action="store_true",
        default=False,
        help=(
            "With --analyze, print one JSON object with the diagnostics on stdout. "
            "stdout then carries only that object; everything else goes to stderr."
        ),
    )
    parser.add_argument(
        "--format",
        action="store_true",
        default=False,
        help=(
            "Format the given source files (lex, parse, then re-emit with canonical "
            "whitespace, line breaks and comment placement). Prints to stdout; use "
            "-w to rewrite in place, or --check to report the files that differ."
        ),
    )
    parser.add_argument(
        "-w",
        "--write",
        action="store_true",
        default=False,
        help="With --format, rewrite the files in place instead of printing them.",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        default=False,
        help="With --format, report the files that need formatting and exit 1 if any.",
    )
    parser.add_argument(
        "--raw-pointers",
        action="store_true",
        default=False,
        help=(
            "Use bare 8-byte pointers (no checks, no lock slots, no frame locks) across "
            "CFG and LLVM layers. Diagnostic mode only; raw pointers provide no memory-safety guarantee."
        ),
    )
    # Intermixed parsing keeps "paths … options … paths" valid; a plain
    # parse_args() would reject a positional that follows an option.
    return parser.parse_intermixed_args(argv)


class _CompilationFailed(Exception):
    """Internal: a diagnostic has been reported; unwind to main() and exit 1."""


#: Source text of the current run, so an error is rendered against the text the
#: compiler actually read rather than re-reading a file that may have moved on.
#: Module level and outside any class body, hence the double underscore.
__DOCUMENTS = DocumentStore()


def __report_error(error: Exception, *, stage: Stage | None = None) -> NoReturn:
    """Render one compiler error to stderr and unwind to ``main``.

    Replaces the old traceback-to-stdout + ``sys.exit(-1)`` path: diagnostics go
    to stderr and the process exit code is the CLI's usual success/failure, which
    is what ``docs/grammar/16.runtime_errors.md`` asks for.
    """
    diagnostic = diagnostic_from_error(error, stage=stage)
    path = diagnostic.span.path
    try:
        text = __DOCUMENTS.text(path)
    except OSError:
        text = ""
    print(format_source_error(diagnostic, text), file=sys.stderr)
    raise _CompilationFailed


def __analyze(args: argparse.Namespace) -> int:
    """Run the analysis prefix and report diagnostics; no codegen, no build/ output.

    With ``--json`` the diagnostics are printed as one JSON object on stdout and
    nothing else is written there, so the output can be consumed by a tool.  The
    exit code only distinguishes success from failure (docs/grammar/16).
    """
    CompilerLog.init(spec=args.log_spec, file="", noop=not args.log_spec)

    packages: PackageMap | None = None
    if args.packages:
        try:
            packages = PackageMap.parse(args.packages)
        except CompilerError as error:
            print(f"error: {error}", file=sys.stderr)
            return 1

    session = AnalysisSession(
        compiler_root=args.compiler_root,
        packages=packages,
        raw_pointers=args.raw_pointers,
    )
    result = session.analyze(args.paths, require_entry=False)

    if args.json:
        print(json.dumps(__analyze_payload(result), ensure_ascii=False, indent=2))
    elif result.diagnostics:
        print(result.formatted(), file=sys.stderr)

    return 0 if result.ok() else 1


def __analyze_payload(result: object) -> dict[str, object]:
    """Build the ``--analyze --json`` payload.

    Positions are LSP-shaped (0-based lines, UTF-16 characters, ``file://`` URIs)
    because they are produced by the single conversion module, so the same payload
    also serves the language server later on.
    """
    from compiler.analysis.session import AnalysisResult

    assert isinstance(result, AnalysisResult)
    diagnostics: list[dict[str, object]] = []
    errors = 0
    warnings = 0
    for diagnostic in result.diagnostics:
        text = result.sources.get(diagnostic.span.path, "")
        if diagnostic.severity is Severity.ERROR:
            errors += 1
        elif diagnostic.severity is Severity.WARNING:
            warnings += 1
        diagnostics.append(
            {
                "code": diagnostic.code,
                "severity": diagnostic.severity.value,
                "message": diagnostic.message,
                "uri": path_to_uri(diagnostic.span.path),
                "range": to_lsp_range(diagnostic.span, text),
                "recovered": diagnostic.recovered,
            }
        )
    return {
        "format": 1,
        "ok": result.ok(),
        "stage": result.failed_stage.value if result.failed_stage is not None else None,
        "summary": {"errors": errors, "warnings": warnings},
        "diagnostics": diagnostics,
    }


def __cfg(ctx: SemCtx) -> dict[int, CFG_IR.Function]:
    """CFG 三段 pass 的编排：下降 → 检查插入 → 后处理。

    输入取自中端共享上下文（`ctx.def_points` / `ctx.type_ctx` / `ctx.raw_pointers`）；
    CFG 侧自带 `CfgCtx`（每函数事实 + 函数表），两层的上下文各自管自己那一层。
    """
    cfg_lower = CfgTranslator(ctx.type_ctx, raw_pointers=ctx.raw_pointers)
    try:
        cfg_lower.run(ctx.def_points)
    except CodegenError as error:
        __report_error(error, stage=Stage.CODEGEN)
    InsertChecks(cfg_lower.ctx).run()
    Cleanup(cfg_lower.ctx).run()
    return cfg_lower.export()


def __build_unit_names(unit_datas: dict[int, UnitData], packages: PackageMap | None) -> dict[int, str]:
    """Build a mapping from unit_id to a unique name string for LLVM type mangling.

    A unit inside a package is named ``<package>_<module path>``, which is
    deterministic and collision-free across packages; files outside every
    source root keep their file stem.
    """
    names: dict[int, str] = {}
    for unit_id, unit_data in unit_datas.items():
        names[unit_id] = __unit_name(unit_data, packages)
    return names


def __unit_name(unit_data: UnitData, packages: PackageMap | None) -> str:
    if packages is None:
        return unit_data.path.stem
    package = packages.package_of(unit_data.path)
    if package is None:
        return unit_data.path.stem
    source_root = packages.packages[package].source_root
    try:
        relative = unit_data.path.resolve().relative_to(source_root)
    except ValueError:
        return unit_data.path.stem
    return "_".join((package, *relative.parts[:-1], relative.stem))


def __type_size_provider(type_ctx: TypeCtx, unit_names: dict[int, str], raw_pointers: bool) -> Callable[[int], int]:
    module = ir.Module(name="yian.comptime.layout")
    apply_target(module)
    ll_type_ctx = LLTypeCtx(type_ctx, module, unit_names, raw_pointers)
    return ll_type_ctx.get_type_size


def __llvm_codegen(
    cfg_functions: dict[int, CFG_IR.Function],
    type_ctx: TypeCtx,
    unit_names: dict[int, str],
    raw_pointers: bool = False,
    entry_type_id: int | None = None,
) -> LLModule:
    """CFG IR → LLVM IR pass. Lowers CFG Functions into an LLVM Module."""
    translator = LLTranslator(type_ctx, unit_names, raw_pointers=raw_pointers, entry_type_id=entry_type_id)
    try:
        translator.run(cfg_functions)
    except CodegenError as error:
        __report_error(error, stage=Stage.CODEGEN)
    return translator.export()


def __derive_output(args: argparse.Namespace, src_files: list[Path]) -> Path:
    """Determine the output file path from CLI args or derive from input files."""
    if args.output is not None:
        return args.output

    first_stem = src_files[0].stem if src_files else "output"
    out_dir = Path("build")
    out_dir.mkdir(parents=True, exist_ok=True)

    if args.target == "exe":
        return out_dir / "a.out"
    if args.target == "ll":
        return out_dir / (first_stem + ".ll")
    if args.target == "bc":
        return out_dir / (first_stem + ".bc")
    if args.target == "obj":
        return out_dir / (first_stem + ".o")
    if args.target == "asm":
        return Path(first_stem + ".s")
    return Path(first_stem)


def __select_linker() -> str:
    """链接目标文件所用的 C 编译器。

    顺序:``$YIAN_CC`` → ``clang`` → ``cc``。不隐式回落到带版本号的
    ``clang-18``/``clang-20``:工具链版本由环境显式给出(见
    ``scripts/setup_llvm_toolchain.sh``),避免"看起来能用但版本不对"。
    """
    override = os.environ.get("YIAN_CC", "")
    if override:
        resolved = shutil.which(override)
        if resolved is None and Path(override).exists():
            resolved = override
        if resolved is None:
            print(f"error: YIAN_CC={override!r} not found", file=sys.stderr)
            sys.exit(1)
        return resolved
    linker = shutil.which("clang") or shutil.which("cc")
    if linker is None:
        print(
            "error: no linker found (tried clang, cc). Install clang or point YIAN_CC at one"
            " (see scripts/setup_llvm_toolchain.sh --check).",
            file=sys.stderr,
        )
        sys.exit(1)
    return linker


def __link_exe(obj_path: Path, output_path: Path, opt_level: int, profile: bool = False) -> None:
    """Link a .o file plus the runtime library to a native executable via clang."""
    linker = __select_linker()
    if profile:
        version = subprocess.run([linker, "--version"], capture_output=True, text=True, check=False)
        first_line = version.stdout.splitlines()[0] if version.stdout else "version unknown"
        print(f"  linker: {linker} ({first_line})", file=sys.stderr)

    runtime_archive = ensure_archive()
    cmd = [linker, str(obj_path), str(runtime_archive), "-lm", "-o", str(output_path), f"-O{opt_level}"]
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        print(f"error: linker failed:\n{proc.stderr}", file=sys.stderr)
        sys.exit(proc.returncode)

    # Remove intermediate .o file
    if obj_path.exists():
        obj_path.unlink()


def __merge_runtime_object(obj_path: Path, output_path: Path) -> None:
    """Merge the user object with the runtime object into one relocatable object.

    ``-t obj`` 的产物保持单文件自包含：用户对象与运行时对象用 ``clang -r`` 合并，
    链接阶段与普通对象一样使用。
    """
    linker = __select_linker()
    runtime_object = ensure_object()
    merged_path = obj_path.with_suffix(".merged.o")
    proc = subprocess.run(
        [linker, "-r", "-nostdlib", str(obj_path), str(runtime_object), "-o", str(merged_path)],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        print(f"error: merging the runtime object failed:\n{proc.stderr}", file=sys.stderr)
        sys.exit(proc.returncode)
    merged_path.replace(output_path)


def __lex(src_files: list[Path]) -> list[list[Token]]:
    token_lists: list[list[Token]] = []
    for src_file in src_files:
        lexer = Lexer(src_file, text=__DOCUMENTS.text(src_file))

        try:
            lexer.lex()
        except LexError as error:
            __report_error(error, stage=Stage.LEX)

        token_lists.append(lexer.export())
    return token_lists


def __parse(token_lists: list[list[Token]]) -> list[AST.Program]:
    programs: list[AST.Program] = []
    for tokens in token_lists:
        parser = Parser(tokens)

        try:
            program = parser.parse()
        except ParseError as error:
            __report_error(error, stage=Stage.PARSE)

        programs.append(program)
    return programs


def __desugar(programs: list[AST.Program]) -> list[AST.Program]:
    for program in programs:
        desugarer = Desugar(program)
        desugarer.run()
    return programs


def main(argv: list[str] | None = None) -> int:
    """CLI entry point: report diagnostics on stderr and never exit mid-pass."""

    try:
        return __run(argv)
    except _CompilationFailed:
        # A diagnostic has already been rendered; failure is the exit code.
        return 1
    except FileNotFoundError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1


def __format(args: argparse.Namespace) -> int:
    """The ``--format`` path: no analysis, no build/ output, just layout."""
    files = collect_an_files(args.paths)
    if not files:
        print("error: --format needs at least one source file", file=sys.stderr)
        return 1
    if not args.write and not args.check and len(files) != 1:
        print("error: --format prints one file; add -w or --check", file=sys.stderr)
        return 1
    unformatted: list[Path] = []
    for path in files:
        text = path.read_text()
        formatted = format_text(text, path=path)
        if formatted is None:
            print(f"skip {path}: cannot be formatted", file=sys.stderr)
            continue
        if formatted == text:
            continue
        unformatted.append(path)
        if args.check:
            continue
        if args.write:
            path.write_text(formatted)
        else:
            sys.stdout.write(formatted)
    if args.check:
        for path in unformatted:
            print(path)
        print(f"{len(unformatted)} of {len(files)} file(s) need formatting")
        return 1 if unformatted else 0
    if args.write:
        print(f"{len(files)} file(s) checked, {len(unformatted)} rewritten")
    return 0


def __run(argv: list[str] | None = None) -> int:
    args = parse_cli(argv)

    if args.json and not args.analyze:
        print("error: --json requires --analyze", file=sys.stderr)
        return 1

    if args.format:
        return __format(args)

    if args.analyze:
        return __analyze(args)

    # ── initialise compiler log ──────────────────────────────────────────
    log_file = str(args.log_file) if args.log_file else "build/compile.log"
    CompilerLog.init(spec=args.log_spec, file=log_file)
    ch_main = CompilerLog.get("main")

    ch_main.info(f"compiling {len(args.paths)} source file(s)")
    timings: dict[str, float] = {}
    t0 = time.perf_counter() if args.profile else 0.0

    # extract .an files from input paths; keep the text so diagnostics do not
    # have to read the files again
    src_files = collect_an_files(args.paths)
    for src_file in src_files:
        __DOCUMENTS.add(Document(path=src_file, text=src_file.read_text()))

    # lex all source files
    lex_start = time.perf_counter() if args.profile else 0.0
    token_lists: list[list[Token]] = __lex(src_files)
    ch_main.debug(f"lexed {sum(len(tl) for tl in token_lists)} tokens from {len(src_files)} file(s)")
    Path("build").mkdir(parents=True, exist_ok=True)
    if args.dump:
        (Path("build") / "tokens.txt").write_text(format_token_output(src_files, token_lists), encoding="utf-8")
    if args.profile:
        timings["lex"] = time.perf_counter() - lex_start

    # parse all token lists into ASTs
    parse_start = time.perf_counter() if args.profile else 0.0
    programs: list[AST.Program] = __parse(token_lists)
    ch_main.debug(f"parsed {sum(len(p.items) for p in programs)} top-level items")
    if args.profile:
        timings["parse"] = time.perf_counter() - parse_start

    # desugar ASTs
    desugar_start = time.perf_counter() if args.profile else 0.0
    programs = __desugar(programs)
    ch_main.debug("desugaring complete")
    if args.profile:
        timings["desugar"] = time.perf_counter() - desugar_start

    if args.dump:
        (Path("build") / "ast.txt").write_text(format_ast_output(src_files, programs), encoding="utf-8")

    unit_datas = {
        i: UnitData(program=program, path=src_file, unit_id=i)
        for i, (program, src_file) in enumerate(zip(programs, src_files))
    }

    packages: PackageMap | None = None
    if args.packages:
        try:
            packages = PackageMap.parse(args.packages)
        except CompilerError as error:
            print(f"error: {error}", file=sys.stderr)
            return 1
    # Package mode names the standard library itself; otherwise the root is
    # configured explicitly (--compiler-root / YIAN_LIB / YIAN_ROOT).
    trust_root = (
        packages.packages["std"].source_root
        if packages is not None
        else resolve_stdlib_root(args.compiler_root)
    )
    if not trust_root.is_dir():
        print(
            f"error: standard library source root {trust_root} does not exist.\n"
            "       Point the compiler at a checkout with YIAN_LIB (the stdlib src\n"
            "       directory) or YIAN_ROOT / --compiler-root (a checkout root);\n"
            "       a non-editable install does not carry lib/.",
            file=sys.stderr,
        )
        return 1
    source_trust = build_source_trust(trust_root)
    for unit in unit_datas.values():
        unit.is_stdlib = source_trust.is_stdlib(unit.path)
        unit.allows_restricted_ops = source_trust.allows_restricted_ops(unit.path)

    # inject prelude imports into non-stdlib files
    inject_prelude(unit_datas.values())

    # Keep pointer-forging and raw ABI primitives inside the trusted stdlib.
    restricted_start = time.perf_counter() if args.profile else 0.0
    try:
        check_restricted_ops(unit_datas.values())
    except AnalysisError as error:
        __report_error(error, stage=Stage.RESTRICTED_OPS)
    if args.profile:
        timings["restricted_ops"] = time.perf_counter() - restricted_start

    type_ctx = TypeCtx(raw_pointers=args.raw_pointers)
    # 中端各段共享的上下文：session 资源 + 产物表 + 每 def 事实
    ctx = SemCtx(type_ctx, args.raw_pointers, unit_datas, packages, source_trust.stdlib_root)

    resolve_start = time.perf_counter() if args.profile else 0.0
    global_resolver = GlobalResolve(ctx)
    try:
        global_resolver.run()
    except AnalysisError as error:
        __report_error(error, stage=Stage.RESOLVE)
    ch_main.debug(f"global resolve complete — {len(unit_datas)} units")
    if args.profile:
        timings["global_resolve"] = time.perf_counter() - resolve_start

    # Run final checks on the type space (e.g. self-referential type detection)
    try:
        type_ctx.finalize()
    except CompilerError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1

    type_check_start = time.perf_counter() if args.profile else 0.0
    # The program entry comes from the package map; a `lib` root has none, which
    # is only acceptable for analysis-only runs. Code generation always consumes
    # the entry-reachable subset (G23).
    type_checker = TypeCheck(ctx, require_entry=args.target != "none")
    try:
        type_checker.run()
    except AnalysisError as error:
        __report_error(error, stage=Stage.TYPE_CHECK)
    except CompilerError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    ctx.declare_def_points(type_checker.export_generated())

    # --- Compile-time conditional specialization ---
    unit_names = __build_unit_names(unit_datas, packages)
    type_size = __type_size_provider(type_ctx, unit_names, args.raw_pointers)
    try:
        ComptimeIfSpecializer(ctx, type_size).run()
    except AnalysisError as error:
        __report_error(error, stage=Stage.COMPTIME)

    # --- Closure lowering pass ---
    from compiler.analysis.passes.closure_lowering import ClosureLowering
    ClosureLowering(ctx).run()

    ch_main.debug(f"type-checked {len(ctx.def_points)} definitions")
    if args.profile:
        timings["type_check"] = time.perf_counter() - type_check_start

    # --- Definite Assignment Analysis ---
    da_start = time.perf_counter() if args.profile else 0.0
    da_pass = DefiniteAssignment(ctx)
    da_pass.run()
    da_errors = da_pass.export_errors()
    if da_errors:
        __report_error(da_errors[0], stage=Stage.DEFINITE_ASSIGNMENT)
    if args.profile:
        timings["definite_assignment"] = time.perf_counter() - da_start

    # HIR → CFG IR pass
    cfg_start = time.perf_counter() if args.profile else 0.0
    cfg_functions = __cfg(ctx)
    ch_main.debug(f"generated {len(cfg_functions)} CFG functions")
    if args.dump:
        (Path("build") / "hir.txt").write_text(
            format_hir_output(ctx.unit_datas, ctx.def_points, ctx.type_ctx), encoding="utf-8"
        )
        (Path("build") / "cfg.txt").write_text(format_cfg_output(cfg_functions), encoding="utf-8")
    if args.profile:
        timings["cfg_codegen"] = time.perf_counter() - cfg_start

    # Derive output path and run codegen (skip only when --target none)
    if args.target != "none":
        output_path = __derive_output(args, src_files)

        # CFG → LLVM IR pass
        llvm_start = time.perf_counter() if args.profile else 0.0
        llvm_module = __llvm_codegen(
            cfg_functions,
            type_ctx,
            unit_names,
            raw_pointers=args.raw_pointers,
            entry_type_id=type_checker.entry_type_id,
        )
        if args.profile:
            timings["llvm_codegen"] = time.perf_counter() - llvm_start

        # Emitter
        emit_start = time.perf_counter() if args.profile else 0.0
        emitter = Emitter()

        if args.dump:
            (Path("build") / "ir.ll").write_text(str(llvm_module), encoding="utf-8")

        # Emit target output — all under build/ by default
        out_dir = output_path.parent
        stem = output_path.stem if output_path.suffix else output_path.name
        try:
            if args.target in ("ll", "bc", "obj", "asm"):
                emitted = Path(emitter.emit_module(llvm_module, str(out_dir), args.target, stem, opt_level=args.O))
                if args.target == "obj":
                    __merge_runtime_object(emitted, output_path)
            elif args.target == "exe":
                obj_path = out_dir / (stem + ".o")
                emitter.emit_module(llvm_module, str(out_dir), "obj", stem, opt_level=args.O)
                __link_exe(obj_path, output_path, args.O, profile=args.profile)
        except RuntimeBuildError as exc:
            print(f"error: {exc}", file=sys.stderr)
            sys.exit(1)
        if args.profile:
            timings["emit"] = time.perf_counter() - emit_start

    if args.profile:
        total = time.perf_counter() - t0
        print(f"\n{' Phase ':-^40}", file=sys.stderr)
        for phase, elapsed in timings.items():
            pct = elapsed / total * 100 if total > 0 else 0
            print(f"  {phase:<20} {elapsed:8.4f}s  ({pct:5.1f}%)", file=sys.stderr)
        print(f"  {'total':<20} {total:8.4f}s", file=sys.stderr)
        try:
            import llvmlite.binding as _binding

            version_info = cast("tuple[int, ...]", _binding.llvm_version_info)  # type: ignore[reportUnknownMemberType]
            llvm_version = ".".join(str(part) for part in version_info)
        except Exception:
            llvm_version = "unknown"
        print(f"  llvmlite: LLVM {llvm_version}", file=sys.stderr)
        print(f"{'':-^40}", file=sys.stderr)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
