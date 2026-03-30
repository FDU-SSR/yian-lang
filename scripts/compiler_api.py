#!/usr/bin/env python3

from dataclasses import dataclass, field
from pathlib import Path
import subprocess
import sys


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


class CompilationPipelineError(RuntimeError):
    def __init__(
        self,
        stage: str,
        command: list[str],
        returncode: int,
        stdout: str,
        stderr: str,
    ) -> None:
        self.stage = stage
        self.command = command
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr
        super().__init__(self._build_message())

    def _build_message(self) -> str:
        return (
            f"[{self.stage}] command failed with exit code {self.returncode}: "
            f"{' '.join(self.command)}"
        )


@dataclass
class CompileRequest:
    compiler_args: list[str]
    clang_args: list[str] = field(default_factory=list)
    debug: bool = False
    display: bool = False
    emit_llvm: bool = False
    output: Path | None = None
    root_dir: Path | None = None
    capture_output: bool = False
    verbose: bool = True


@dataclass
class CompileResult:
    linked_ll_path: Path
    artifact_path: Path
    emitted_llvm: bool


def split_clang_args(compiler_args: list[str]) -> tuple[list[str], list[str]]:
    clang_args = [arg for arg in compiler_args if arg in OPTIMIZE_ARGS]
    passthrough_args = [arg for arg in compiler_args if arg not in OPTIMIZE_ARGS]
    return passthrough_args, clang_args


def _run_checked(
    command: list[str],
    stage: str,
    *,
    capture_output: bool,
) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(command, text=True, capture_output=capture_output)
    if completed.returncode != 0:
        raise CompilationPipelineError(
            stage=stage,
            command=command,
            returncode=completed.returncode,
            stdout=completed.stdout or "",
            stderr=completed.stderr or "",
        )
    return completed


def _log(verbose: bool, message: str) -> None:
    if verbose:
        print(message)


def run_compiler(
    root_dir: Path,
    compiler_args: list[str],
    debug: bool,
    *,
    capture_output: bool,
) -> None:
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

    _run_checked(command, "main.py compile", capture_output=capture_output)


def run_view(root_dir: Path, *, capture_output: bool, verbose: bool) -> None:
    view_script = root_dir / "lian" / "scripts" / "dfview.py"
    view_output = root_dir / "tests" / "yian_workspace"
    _log(verbose, "=== view ===")
    _run_checked(
        [str(view_script), str(view_output)],
        "dfview",
        capture_output=capture_output,
    )


def link_ll_files(llir_path: Path, *, capture_output: bool, verbose: bool) -> Path:
    ll_files = list(llir_path.glob("*.ll"))
    if not ll_files:
        raise FileNotFoundError(f"No .ll files found in {llir_path}")

    if len(ll_files) == 1:
        return ll_files[0]

    output_file = llir_path / "out.ll"
    cmd_link = ["llvm-link", "-S", "-o", str(output_file), *[str(f) for f in ll_files]]
    _log(verbose, "=== linking ===")
    _run_checked(cmd_link, "llvm-link", capture_output=capture_output)
    _log(verbose, f"linked {len(ll_files)} files to {output_file}")

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

    return final_output


def compile_with_clang(
    linked_ll_file: Path,
    clang_args: list[str],
    root_dir: Path,
    binary_output: Path | None,
    *,
    capture_output: bool,
    verbose: bool,
) -> Path:
    default_output = root_dir / "tests" / "yian_workspace" / "bin" / "out"
    clang_output_file = binary_output if binary_output is not None else default_output
    clang_output_file.parent.mkdir(parents=True, exist_ok=True)

    full_cmd = ["clang", str(linked_ll_file), *clang_args, "-o", str(clang_output_file), "-lm"]
    _log(verbose, "=== compiling ===")
    _run_checked(full_cmd, "clang", capture_output=capture_output)
    _log(verbose, f"compiled all files to {clang_output_file}")
    return clang_output_file


def compile_project(request: CompileRequest) -> CompileResult:
    root_dir = request.root_dir or Path(__file__).resolve().parent.parent
    llir_path = root_dir / "tests" / "yian_workspace" / "objects"

    run_compiler(
        root_dir,
        request.compiler_args,
        request.debug,
        capture_output=request.capture_output,
    )

    if request.display:
        run_view(
            root_dir,
            capture_output=request.capture_output,
            verbose=request.verbose,
        )

    linked_ll_file = link_ll_files(
        llir_path,
        capture_output=request.capture_output,
        verbose=request.verbose,
    )

    if request.emit_llvm:
        llvm_output = emit_llvm_artifact(linked_ll_file, root_dir, request.output)
        _log(request.verbose, f"emitted llvm ir to {llvm_output}")
        return CompileResult(
            linked_ll_path=linked_ll_file,
            artifact_path=llvm_output,
            emitted_llvm=True,
        )

    binary_output = compile_with_clang(
        linked_ll_file,
        request.clang_args,
        root_dir,
        request.output,
        capture_output=request.capture_output,
        verbose=request.verbose,
    )
    return CompileResult(
        linked_ll_path=linked_ll_file,
        artifact_path=binary_output,
        emitted_llvm=False,
    )
