#!/usr/bin/env python3
import argparse
import subprocess
import sys
from pathlib import Path


OPTIMIZE_ARGS = {
    "-O0",
    "-O1",
    "-O2",
    "-O3",
    "-Os",
    "-o0",
    "-o1",
    "-o2",
    "-o3",
    "-os",
}


def parse_cli() -> tuple[argparse.Namespace, list[str]]:
    parser = argparse.ArgumentParser(
        prog="yian_compiler.py",
        description=(
            "Yian compile entrypoint: run scripts/compiler.py to generate LLVM IR, "
            "then link and produce an executable with clang."
        ),
        epilog=(
            "Examples:\n"
            "  yian_compiler.py tests/control_flow/for.an\n"
            "  yian_compiler.py -p tests/array/init.an\n"
            "  yian_compiler.py tests/call/func.an -O2\n\n"
            "Notes:\n"
            "  1) Unknown arguments are passed through to scripts/compiler.py\n"
            "  2) -O0/-O1/-O2/-O3/-Os (including lowercase variants) are passed to clang"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "-p",
        "--display",
        action="store_true",
        help="Run dfview after compilation to display intermediate results",
    )
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        metavar="PATH",
        help="Set output path for the final binary (default: tests/yian_workspace/bin/out)",
    )

    parsed, compiler_args = parser.parse_known_args()
    if not compiler_args:
        parser.error("missing compiler arguments (for example: <path>)")
    return parsed, compiler_args


def split_clang_args(compiler_args: list[str]) -> tuple[list[str], list[str]]:
    clang_args = [arg for arg in compiler_args if arg in OPTIMIZE_ARGS]
    passthrough_args = [arg for arg in compiler_args if arg not in OPTIMIZE_ARGS]
    return passthrough_args, clang_args


def run_compiler(root_dir: Path, compiler_args: list[str]) -> None:
    command = [sys.executable, str(root_dir / "scripts" / "compiler.py"), *compiler_args]
    result = subprocess.run(command)
    if result.returncode != 0:
        print(f"[ERROR] {' '.join(command)}")
        sys.exit(result.returncode)


def run_view(root_dir: Path) -> None:
    view_script = root_dir / "lian" / "scripts" / "dfview.py"
    view_output = root_dir / "tests" / "yian_workspace"
    print("=== view ===")
    subprocess.run([str(view_script), str(view_output)], check=True)


def link_ll_files(llir_path: Path) -> Path:
    ll_files = list(llir_path.glob("*.ll"))
    if not ll_files:
        raise FileNotFoundError(f"No .ll files found in {llir_path}")

    if len(ll_files) == 1:
        return ll_files[0]

    output_file = llir_path / "out.ll"
    cmd_link = ["llvm-link", "-S", "-o", str(output_file), *[str(f) for f in ll_files]]
    print("=== linking ===")
    subprocess.run(cmd_link, check=True)
    print(f"linked {len(ll_files)} files to {output_file}")

    for file in ll_files:
        file.unlink()
    return output_file


def compile_with_clang(
    output_file: Path, clang_args: list[str], root_dir: Path, binary_output: Path | None
) -> None:
    default_output = root_dir / "tests" / "yian_workspace" / "bin" / "out"
    clang_output_file = binary_output if binary_output is not None else default_output
    clang_output_file.parent.mkdir(parents=True, exist_ok=True)

    full_cmd = ["clang", str(output_file), *clang_args, "-o", str(clang_output_file), "-lm"]
    print("=== compiling ===")
    subprocess.run(full_cmd, check=True)
    print(f"compiled all files to {clang_output_file}")


def main() -> int:
    parsed, compiler_args = parse_cli()
    compiler_args, clang_args = split_clang_args(compiler_args)

    root_dir = Path(__file__).resolve().parent.parent
    llir_path = root_dir / "tests" / "yian_workspace" / "objects"

    run_compiler(root_dir, compiler_args)

    if parsed.display:
        run_view(root_dir)

    output_file = link_ll_files(llir_path)
    compile_with_clang(output_file, clang_args, root_dir, parsed.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
