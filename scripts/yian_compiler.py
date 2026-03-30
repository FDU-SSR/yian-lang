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
            "Yian compile entrypoint: run compiler/main.py to generate LLVM IR, "
            "then link and produce either LLVM IR or a binary executable."
        ),
        epilog=(
            "Examples:\n"
            "  yian_compiler.py tests/control_flow/for.an\n"
            "  yian_compiler.py -d tests/control_flow/for.an\n"
            "  yian_compiler.py -p tests/array/init.an\n"
            "  yian_compiler.py tests/call/func.an -O2\n"
            "  yian_compiler.py --emit-llvm tests/call/func.an -o build/func.ll\n\n"
            "Notes:\n"
            "  1) Unknown arguments are passed through to compiler/main.py\n"
            "  2) -O0/-O1/-O2/-O3/-Os (including lowercase variants) are passed to clang\n"
            "  3) --emit-llvm mode writes a .ll artifact and skips clang\n"
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
        "-o",
        "--output",
        type=Path,
        metavar="PATH",
        help="Set output path for the final artifact",
    )
    parser.add_argument(
        "--emit-llvm",
        action="store_true",
        help="Emit LLVM IR (*.ll) instead of a binary executable",
    )

    parsed, compiler_args = parser.parse_known_args()
    if not compiler_args:
        parser.error("missing compiler arguments (for example: <path>)")
    return parsed, compiler_args


def split_clang_args(compiler_args: list[str]) -> tuple[list[str], list[str]]:
    clang_args = [arg for arg in compiler_args if arg in OPTIMIZE_ARGS]
    passthrough_args = [arg for arg in compiler_args if arg not in OPTIMIZE_ARGS]
    return passthrough_args, clang_args


def run_compiler(root_dir: Path, compiler_args: list[str], debug: bool) -> None:
    exe_path = root_dir / "compiler" / "main.py"
    lib_path = root_dir / "lib"
    workspace_output = root_dir / "tests" / "yian_workspace"

    command = [
        sys.executable,
        str(exe_path),
        "compile",
        "-f",
        "-w",
        str(workspace_output),
        *compiler_args,
        str(lib_path),
    ]

    if debug:
        command.insert(4, "-d")

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


def emit_llvm_artifact(linked_ll: Path, root_dir: Path, output_path: Path | None) -> Path:
    default_output = root_dir / "tests" / "yian_workspace" / "bin" / "out.ll"
    final_output = output_path if output_path is not None else default_output

    if final_output.suffix != ".ll":
        final_output = final_output.with_suffix(".ll")

    final_output.parent.mkdir(parents=True, exist_ok=True)

    if linked_ll.resolve() != final_output.resolve():
        final_output.write_text(linked_ll.read_text(encoding="utf-8"), encoding="utf-8")

    print(f"emitted llvm ir to {final_output}")
    return final_output


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

    run_compiler(root_dir, compiler_args, parsed.debug)

    if parsed.display:
        run_view(root_dir)

    output_file = link_ll_files(llir_path)

    if parsed.emit_llvm:
        emit_llvm_artifact(output_file, root_dir, parsed.output)
        return 0

    compile_with_clang(output_file, clang_args, root_dir, parsed.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
