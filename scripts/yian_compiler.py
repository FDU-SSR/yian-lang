#!/usr/bin/env python3
import argparse
from pathlib import Path

from compiler_api import CompilationPipelineError, CompileRequest, compile_project, split_clang_args


def parse_cli() -> tuple[argparse.Namespace, list[str]]:
    parser = argparse.ArgumentParser(
        prog="yian_compiler.py",
        description=(
            "Yian compile entrypoint: run compiler/main.py to generate LLVM IR, "
            "then produce either LLVM IR (ll) or executable (exe)."
        ),
        epilog=(
            "Examples:\n"
            "  yian_compiler.py tests/control_flow/for.an\n"
            "  yian_compiler.py -d tests/control_flow/for.an\n"
            "  yian_compiler.py -p tests/array/init.an\n"
            "  yian_compiler.py tests/call/func.an -O2\n"
            "  yian_compiler.py --target ll tests/call/func.an --output build/func.ll\n"
            "  yian_compiler.py --target exe tests/call/func.an --output build/func\n\n"
            "Notes:\n"
            "  1) Unknown arguments are passed through to compiler/main.py\n"
            "  2) -O0/-O1/-O2/-O3/-Os (including lowercase variants) are passed to clang\n"
            "  3) --target exe links object file with clang to generate executable\n"
            "  4) -d forwards debug mode to compiler/main.py"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "-d",
        "--debug",
        action="store_true",
        help="Enable compiler debug output (forward -d to compiler/main.py)",
    )
    parser.add_argument(
        "-p",
        "--display",
        action="store_true",
        help="Run dfview after compilation to display intermediate results",
    )
    parser.add_argument(
        "--out",
        "--output",
        dest="output",
        type=Path,
        metavar="PATH",
        help="Set output path for the final artifact",
    )
    parser.add_argument(
        "--target",
        default="exe",
        choices=["ll", "exe"],
        help="Target artifact kind: ll/exe (default: exe)",
    )
    parsed, compiler_args = parser.parse_known_args()
    if not compiler_args:
        parser.error("missing compiler arguments (for example: <path>)")
    return parsed, compiler_args


def main() -> int:
    parsed, compiler_args = parse_cli()
    compiler_args, clang_args = split_clang_args(compiler_args)

    try:
        compile_project(
            CompileRequest(
                compiler_args=compiler_args,
                clang_args=clang_args,
                debug=parsed.debug,
                display=parsed.display,
                target=parsed.target,
                output=parsed.output,
                verbose=True,
            )
        )
        return 0
    except CompilationPipelineError as error:
        print(str(error))
        if error.stderr:
            print(error.stderr)
        return error.returncode


if __name__ == "__main__":
    raise SystemExit(main())
