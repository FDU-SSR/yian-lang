#!/usr/bin/env python3

import argparse
import shutil
import subprocess
import sys
import traceback
from pathlib import Path
from typing import NoReturn

from compiler.analysis.error import AnalysisError
from compiler.analysis.passes.desugar import Desugar
from compiler.analysis.passes.global_resolve import GlobalResolve
from compiler.analysis.passes.prelude import inject_prelude
from compiler.analysis.passes.type_check import TypeCheck
from compiler.analysis.ty.context import TypeCtx
from compiler.analysis.unit.def_point import DefPoint
from compiler.analysis.unit.hir_export import export_hir_bundle
from compiler.analysis.unit.unit_data import UnitData
from compiler.codegen.cfg import ir as CFG_IR
from compiler.codegen.cfg.dump import dump as dump_cfg
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
        "--token",
        type=Path,
        metavar="PATH",
        default=None,
        help="Write token output to PATH.",
    )
    parser.add_argument(
        "--ast",
        type=Path,
        metavar="PATH",
        default=None,
        help="Write AST output to PATH.",
    )
    parser.add_argument(
        "--hir",
        type=Path,
        metavar="PATH",
        default=None,
        help="Write HIR output to PATH.",
    )
    parser.add_argument(
        "--cfg",
        type=Path,
        metavar="PATH",
        default=None,
        help="Write CFG output to PATH.",
    )
    parser.add_argument(
        "--emit-llvm",
        type=Path,
        metavar="PATH",
        default=None,
        help="Write LLVM IR output to PATH.",
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
        help="Optimization level passed to clang (default: 0).",
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


def __write_text_output(output_path: Path, content: str) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(content)


def __format_token_output(src_files: list[Path], token_lists: list[list[Token]]) -> str:
    sections: list[str] = []
    for src_file, tokens in zip(src_files, token_lists):
        lines = [f"Tokens for {src_file}:"]
        lines.extend(f"  {token}" for token in tokens)
        sections.append("\n".join(lines))
    return "\n\n".join(sections) + ("\n" if sections else "")


def __format_ast_output(src_files: list[Path], programs: list[AST.Program]) -> str:
    sections: list[str] = []
    for src_file, program in zip(src_files, programs):
        sections.append(f"AST for {src_file}:\n{program.export().rstrip()}")
    return "\n\n".join(sections) + ("\n" if sections else "")


def __format_hir_output(unit_datas: dict[int, UnitData], def_points: dict[int, DefPoint], type_ctx: TypeCtx) -> str:
    return export_hir_bundle(unit_datas, def_points, type_ctx)


def __cfg(def_points: dict[int, DefPoint], type_ctx: TypeCtx) -> dict[int, CFG_IR.Function]:
    """HIR → CFG IR pass. Lowers typed HIR function definitions into CFG Functions."""
    translator = CfgTranslator(type_ctx)
    try:
        translator.run(def_points)
    except CodegenError as error:
        __print_source_error(error.span, error)
    return translator.export()


def __format_cfg_output(functions: dict[int, CFG_IR.Function]) -> str:
    sections: list[str] = []
    for type_id in sorted(functions.keys()):
        func = functions[type_id]
        sections.append(dump_cfg(func))
    return "\n\n".join(sections) + ("\n" if sections else "")


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
) -> LLModule:
    """CFG IR → LLVM IR pass. Lowers CFG Functions into an LLVM Module."""
    translator = LLTranslator(type_ctx, unit_names)
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

    if args.target == "exe":
        return Path("a.out")
    if args.target == "ll":
        return Path(first_stem + ".ll")
    if args.target == "bc":
        return Path(first_stem + ".bc")
    if args.target == "obj":
        return Path(first_stem + ".o")
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

    # extract .an files from input paths
    src_files = collect_an_files(args.paths)

    # lex all source files
    token_lists: list[list[Token]] = __lex(src_files)

    if args.token is not None:
        __write_text_output(args.token, __format_token_output(src_files, token_lists))

    # parse all token lists into ASTs
    programs: list[AST.Program] = __parse(token_lists)

    # desugar ASTs
    programs = __desugar(programs)

    if args.ast is not None:
        __write_text_output(args.ast, __format_ast_output(src_files, programs))

    # inject prelude imports into non-stdlib files
    inject_prelude(src_files, programs)

    unit_datas = {i: UnitData(program=program, path=src_file, unit_id=i) for i, (program, src_file) in enumerate(zip(programs, src_files))}
    type_ctx = TypeCtx()

    global_resolver = GlobalResolve(unit_datas, type_ctx)
    try:
        global_resolver.run()
    except AnalysisError as error:
        __print_source_error(error.span, error)

    # Run final checks on the type space (e.g. self-referential type detection)
    try:
        type_ctx.finalize()
    except CompilerError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1

    type_checker = TypeCheck(unit_datas, type_ctx)
    try:
        type_checker.run()
    except AnalysisError as error:
        __print_source_error(error.span, error)
    except CompilerError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    def_points = type_checker.export()

    if args.hir is not None:
        __write_text_output(args.hir, __format_hir_output(unit_datas, def_points, type_ctx))

    # HIR → CFG IR pass
    cfg_functions = __cfg(def_points, type_ctx)

    if args.cfg is not None:
        __write_text_output(args.cfg, __format_cfg_output(cfg_functions))

    # Derive output path and run codegen (skip only when --target none)
    if args.target != "none":
        output_path = __derive_output(args, src_files)

        # Build unit_names mapping
        unit_names = __build_unit_names(unit_datas)

        # CFG → LLVM IR pass
        llvm_module = __llvm_codegen(cfg_functions, type_ctx, unit_names)

        # Emitter
        emitter = Emitter()

        # Debug: dump LLVM IR
        if args.emit_llvm is not None:
            emitter.emit_ll(llvm_module, str(args.emit_llvm))

        # Emit target output
        if args.target in ("ll", "bc", "obj", "asm"):
            out_dir = output_path.parent if output_path.parent != Path() else Path(".")
            emitter.emit_module(llvm_module, str(out_dir), args.target, output_path.name.rsplit(".", 1)[0] if "." in output_path.name else output_path.name)
        elif args.target == "exe":
            build_dir = Path("build")
            build_dir.mkdir(exist_ok=True)
            stem = output_path.stem if output_path.suffix else output_path.name
            obj_path = build_dir / (stem + ".o")
            emitter.emit_module(llvm_module, str(build_dir), "obj", stem)
            __link_exe(obj_path, output_path, args.O)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
