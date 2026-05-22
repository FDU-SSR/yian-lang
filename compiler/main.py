#!/usr/bin/env python3

import argparse
import sys
import traceback
from pathlib import Path
from typing import NoReturn

from compiler.analysis.error import AnalysisError
from compiler.analysis.passes.global_resolve import GlobalResolve
from compiler.analysis.passes.type_check import TypeCheck
from compiler.analysis.ty.context import TypeCtx
from compiler.analysis.unit.unit_data import UnitData
from compiler.frontend.lex.lexer import Lexer, LexError
from compiler.frontend.lex.token import Token
from compiler.frontend.parse import ast as AST
from compiler.frontend.parse.parser import ParseError, Parser
from compiler.utils.IR.position import SrcSpan


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
        help="Write token output to PATH.",
    )
    parser.add_argument(
        "--ast",
        type=Path,
        metavar="PATH",
        help="Write AST output to PATH.",
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


def main(argv: list[str] | None = None) -> int:
    args = parse_cli(argv)

    # extract .an files from input paths
    src_files = collect_an_files(args.paths)

    # lex all source files
    token_lists: list[list[Token]] = []
    for src_file in src_files:
        lexer = Lexer(src_file)

        try:
            lexer.lex()
        except LexError as error:
            __print_source_error(error.span, error)

        token_lists.append(lexer.export())

    programs: list[AST.Program] = []
    for src_file, tokens in zip(src_files, token_lists):
        parser = Parser(tokens)

        try:
            program = parser.parse()
        except ParseError as error:
            __print_source_error(error.span, error)

        programs.append(program)

    unit_datas = {i: UnitData(program=program, path=src_file, unit_id=i) for i, (program, src_file) in enumerate(zip(programs, src_files))}
    type_ctx = TypeCtx()

    global_resolver = GlobalResolve(unit_datas, type_ctx)
    try:
        global_resolver.run()
    except AnalysisError as error:
        __print_source_error(error.span, error)

    type_checker = TypeCheck(unit_datas, type_ctx)
    try:
        type_checker.run()
    except AnalysisError as error:
        __print_source_error(error.span, error)
    def_points = type_checker.export()

    if args.token is not None:
        __write_text_output(args.token, __format_token_output(src_files, token_lists))

    if args.ast is not None:
        __write_text_output(args.ast, __format_ast_output(src_files, programs))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
