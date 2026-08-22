from __future__ import annotations

#! /usr/bin/env python3

import argparse
import json
import shutil
import subprocess
import sys
import time
import traceback
from pathlib import Path
from typing import NoReturn

from compiler.analysis.error import AnalysisError
from compiler.analysis.passes.definite_assignment import DefiniteAssignment
from compiler.utils.log import CompilerLog
from compiler.utils.log import (
    format_ast_output, format_cfg_output,
    format_hir_output, format_token_output,
)
from compiler.analysis.passes.desugar import Desugar
from compiler.analysis.passes.global_resolve import GlobalResolve
from compiler.analysis.passes.prelude import inject_prelude
from compiler.analysis.passes.restricted_ops import check_restricted_ops
from compiler.analysis.passes.type_check import TypeCheck
from compiler.analysis.ty.context import TypeCtx
from compiler.analysis.unit.def_point import DefPoint
from compiler.analysis.unit.unit_data import UnitData
from compiler.codegen.cfg import ir as CFG_IR
from compiler.codegen.cfg.translator import CfgTranslator
from compiler.codegen.error import CodegenError
from compiler.codegen.llvm.emit import Emitter
from compiler.codegen.llvm.module import LLModule
from compiler.codegen.llvm.translator import LLTranslator
from compiler.error import CompilerError
from compiler.frontend.lex.lexer import Lexer, LexError
from compiler.frontend.lex.position import SrcSpan
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
        "--no-fat-checks",
        action="store_true",
        default=False,
        help=(
            "Skip emission of CFG-level fat-pointer access checks (CheckSafeAccess/"
            "CheckInBounds/CheckElementArith/CheckPtrDiff/CheckPtrCmp/CheckDelete) while "
            "keeping the 40-byte fat-pointer representation, lock slots and frame locks. "
            "评测专用:关闭检查仅为构造无检查基线;生产环境不应禁用检查."
        ),
    )
    parser.add_argument(
        "--raw-pointers",
        action="store_true",
        default=False,
        help=(
            "Use bare 8-byte pointers (no checks, no lock slots, no frame locks) across "
            "CFG and LLVM layers. 评测专用:裸指针仅为性能评测基准,不提供任何内存安全保证;"
            "生产环境不应使用."
        ),
    )
    return parser.parse_args(argv)


def collect_an_files(paths: list[Path]) -> list[Path]:
    """Collect all .an files from input file and directory paths."""
    an_files: list[Path] = []
    for path in paths:
        if path.is_file():
            if path.suffix == ".an":
                an_files.append(path)
            continue

        if path.is_dir():
            an_files.extend(file_path for file_path in path.rglob("*.an") if file_path.is_file())
            continue

        # Path does not exist — fail early instead of silently skipping
        print(f"error: path does not exist: {path}", file=sys.stderr)
        sys.exit(1)

    return an_files


def __print_traceback(error: Exception) -> None:
    print("Traceback (most recent call last):")
    for line in traceback.format_tb(error.__traceback__):
        print(line, end="")
    print()


def __print_source_error(span: SrcSpan, error: Exception) -> NoReturn:
    path = span.path
    source = path.read_text()

    print("-" * 20)
    __print_traceback(error)

    start_row = span.start.row
    start_col = span.start.col
    end_row = span.end.row
    end_col = span.end.col

    print(error)
    print(f"--> {path}:{start_row + 1}:{start_col}")

    lines = source.splitlines()
    if 0 <= start_row < len(lines):
        start_line = lines[start_row]
        print(f"    {start_line}")
        if start_row == end_row:
            marker_width = max(1, end_col - start_col)
        else:
            marker_width = max(1, len(start_line) - start_col + 1)
        marker = " " * (start_col - 1) + "^" * marker_width
        print(f"    {marker}")
    print()

    sys.exit(-1)


def __cfg(
    def_points: dict[int, DefPoint],
    type_ctx: TypeCtx,
    no_fat_checks: bool = False,
    raw_pointers: bool = False,
) -> dict[int, CFG_IR.Function]:
    """HIR → CFG IR pass. Lowers typed HIR function definitions into CFG Functions."""
    translator = CfgTranslator(type_ctx, no_fat_checks=no_fat_checks, raw_pointers=raw_pointers)
    try:
        translator.run(def_points)
    except CodegenError as error:
        __print_source_error(error.span, error)
    return translator.export()


def __build_unit_names(unit_datas: dict[int, UnitData]) -> dict[int, str]:
    """Build a mapping from unit_id to a unique name string for LLVM type mangling."""
    names: dict[int, str] = {}
    for unit_id, unit_data in unit_datas.items():
        stem = unit_data.path.stem
        parts = unit_data.path.parts
        if "lib" in parts:
            idx = parts.index("lib")
            stem = "_".join(parts[idx + 1:]) if idx + 1 < len(parts) else stem
            stem = stem.replace(".an", "")
        names[unit_id] = stem
    return names


def __llvm_codegen(
    cfg_functions: dict[int, CFG_IR.Function],
    type_ctx: TypeCtx,
    unit_names: dict[int, str],
    raw_pointers: bool = False,
) -> LLModule:
    """CFG IR → LLVM IR pass. Lowers CFG Functions into an LLVM Module."""
    translator = LLTranslator(type_ctx, unit_names, raw_pointers=raw_pointers)
    try:
        translator.run(cfg_functions)
    except CodegenError as error:
        __print_source_error(error.span, error)
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


def __link_exe(obj_path: Path, output_path: Path, opt_level: int) -> None:
    """Link a .o file to a native executable via clang (or cc as fallback)."""
    linker = shutil.which("clang") or shutil.which("cc")
    if linker is None:
        print("error: no linker found (tried clang, cc). Install clang to link executables.", file=sys.stderr)
        sys.exit(1)

    cmd = [linker, str(obj_path), "-o", str(output_path), f"-O{opt_level}"]
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        print(f"error: linker failed:\n{proc.stderr}", file=sys.stderr)
        sys.exit(proc.returncode)

    # Remove intermediate .o file
    if obj_path.exists():
        obj_path.unlink()


def __lex(src_files: list[Path]) -> list[list[Token]]:
    token_lists: list[list[Token]] = []
    for src_file in src_files:
        lexer = Lexer(src_file)

        try:
            lexer.lex()
        except LexError as error:
            __print_source_error(error.span, error)

        token_lists.append(lexer.export())
    return token_lists


def __parse(token_lists: list[list[Token]]) -> list[AST.Program]:
    programs: list[AST.Program] = []
    for tokens in token_lists:
        parser = Parser(tokens)

        try:
            program = parser.parse()
        except ParseError as error:
            __print_source_error(error.span, error)

        programs.append(program)
    return programs


def __desugar(programs: list[AST.Program]) -> list[AST.Program]:
    for program in programs:
        desugarer = Desugar(program)
        desugarer.run()
    return programs


def main(argv: list[str] | None = None) -> int:
    args = parse_cli(argv)

    # ── initialise compiler log ──────────────────────────────────────────
    log_file = str(args.log_file) if args.log_file else "build/compile.log"
    CompilerLog.init(spec=args.log_spec, file=log_file)
    ch_main = CompilerLog.get("main")

    ch_main.info(f"compiling {len(args.paths)} source file(s)")
    timings: dict[str, float] = {}
    t0 = time.perf_counter() if args.profile else 0.0

    # extract .an files from input paths
    src_files = collect_an_files(args.paths)

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

    # inject prelude imports into non-stdlib files
    inject_prelude(src_files, programs)

    # reject restricted operations (bitcast, raw syscalls, ...) in non-stdlib code
    restricted_start = time.perf_counter() if args.profile else 0.0
    try:
        check_restricted_ops(programs, src_files)
    except AnalysisError as error:
        __print_source_error(error.span, error)
    if args.profile:
        timings["restricted_ops"] = time.perf_counter() - restricted_start

    unit_datas = {i: UnitData(program=program, path=src_file, unit_id=i) for i, (program, src_file) in enumerate(zip(programs, src_files))}
    type_ctx = TypeCtx(raw_pointers=args.raw_pointers)

    pkg_roots: dict[str, Path] = {}
    if args.packages:
        pkg_roots = {k: Path(v) for k, v in json.loads(args.packages.read_text()).items()}

    resolve_start = time.perf_counter() if args.profile else 0.0
    global_resolver = GlobalResolve(unit_datas, type_ctx, pkg_roots)
    try:
        global_resolver.run()
    except AnalysisError as error:
        __print_source_error(error.span, error)
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
    type_checker = TypeCheck(unit_datas, type_ctx)
    try:
        type_checker.run()
    except AnalysisError as error:
        __print_source_error(error.span, error)
    except CompilerError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    def_points = type_checker.export()

    # --- Closure lowering pass ---
    from compiler.analysis.passes.closure_lowering import ClosureLowering
    ClosureLowering(def_points, type_ctx).run()

    ch_main.debug(f"type-checked {len(def_points)} definitions")
    if args.profile:
        timings["type_check"] = time.perf_counter() - type_check_start

    # --- Definite Assignment Analysis ---
    da_start = time.perf_counter() if args.profile else 0.0
    da_pass = DefiniteAssignment(def_points, type_ctx)
    da_pass.run()
    da_errors = da_pass.export_errors()
    if da_errors:
        __print_source_error(da_errors[0].span, da_errors[0])
    for type_id, dp in def_points.items():
        dp.validity = da_pass.export_analysis(type_id)
    if args.profile:
        timings["definite_assignment"] = time.perf_counter() - da_start

    # HIR → CFG IR pass
    cfg_start = time.perf_counter() if args.profile else 0.0
    cfg_functions = __cfg(def_points, type_ctx, no_fat_checks=args.no_fat_checks, raw_pointers=args.raw_pointers)
    ch_main.debug(f"generated {len(cfg_functions)} CFG functions")
    if args.dump:
        (Path("build") / "hir.txt").write_text(format_hir_output(unit_datas, def_points, type_ctx), encoding="utf-8")
        (Path("build") / "cfg.txt").write_text(format_cfg_output(cfg_functions), encoding="utf-8")
    if args.profile:
        timings["cfg_codegen"] = time.perf_counter() - cfg_start

    # Derive output path and run codegen (skip only when --target none)
    if args.target != "none":
        output_path = __derive_output(args, src_files)

        # Build unit_names mapping
        unit_names = __build_unit_names(unit_datas)

        # CFG → LLVM IR pass
        llvm_start = time.perf_counter() if args.profile else 0.0
        llvm_module = __llvm_codegen(cfg_functions, type_ctx, unit_names, raw_pointers=args.raw_pointers)
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
        if args.target in ("ll", "bc", "obj", "asm"):
            emitter.emit_module(llvm_module, str(out_dir), args.target, stem, opt_level=args.O)
        elif args.target == "exe":
            obj_path = out_dir / (stem + ".o")
            emitter.emit_module(llvm_module, str(out_dir), "obj", stem, opt_level=args.O)
            __link_exe(obj_path, output_path, args.O)
        if args.profile:
            timings["emit"] = time.perf_counter() - emit_start

    if args.profile:
        total = time.perf_counter() - t0
        print(f"\n{' Phase ':-^40}", file=sys.stderr)
        for phase, elapsed in timings.items():
            pct = elapsed / total * 100 if total > 0 else 0
            print(f"  {phase:<20} {elapsed:8.4f}s  ({pct:5.1f}%)", file=sys.stderr)
        print(f"  {'total':<20} {total:8.4f}s", file=sys.stderr)
        print(f"{'':-^40}", file=sys.stderr)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
