#!/usr/bin/env python3

import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

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

TARGET_ALIASES: dict[str, str] = {
    "ll": "ll",
    "ir": "ll",
    "llvm-ir": "ll",
    "bc": "bc",
    "bytecode": "bc",
    "o": "obj",
    "obj": "obj",
    "object": "obj",
    "s": "asm",
    "asm": "asm",
    "assembly": "asm",
    "exe": "exe",
    "bin": "exe",
    "binary": "exe",
    "executable": "exe",
}

TARGET_TO_SUFFIX: dict[str, str] = {
    "ll": ".ll",
    "bc": ".bc",
    "obj": ".o",
    "asm": ".s",
    "exe": "",
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
    target: Literal["ll", "bc", "obj", "asm", "exe"] = "exe"
    output: Path | None = None
    root_dir: Path | None = None
    capture_output: bool = False
    verbose: bool = True


@dataclass
class CompileResult:
    compiler_artifact_path: Path
    artifact_path: Path
    target: str


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
    emit_kind: str,
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
        "--emit",
        emit_kind,
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


def normalize_target(target: str) -> str:
    normalized = TARGET_ALIASES.get(target.lower())
    if normalized is None:
        choices = ", ".join(sorted({"ll", "bc", "obj", "asm", "exe"}))
        raise ValueError(f"Unsupported target: {target}. Supported targets: {choices}")
    return normalized


def compiler_emit_kind(target: str) -> str:
    # Executable output uses object file as compiler backend artifact.
    return "obj" if target == "exe" else target


def expected_compiler_artifact_path(root_dir: Path, emit_kind: str) -> Path:
    suffix = TARGET_TO_SUFFIX[emit_kind]
    return root_dir / "tests" / "yian_workspace" / "objects" / f"output{suffix}"


def resolve_output_path(root_dir: Path, output_path: Path | None, target: str) -> Path:
    default_name = "out" if target == "exe" else f"out{TARGET_TO_SUFFIX[target]}"
    default_output = root_dir / "tests" / "yian_workspace" / "bin" / default_name
    final_output = output_path if output_path is not None else default_output

    expected_suffix = TARGET_TO_SUFFIX[target]
    if expected_suffix and final_output.suffix != expected_suffix:
        final_output = final_output.with_suffix(expected_suffix)

    final_output.parent.mkdir(parents=True, exist_ok=True)
    return final_output


def emit_compiler_artifact(
    compiler_artifact: Path,
    root_dir: Path,
    target: str,
    output_path: Path | None,
) -> Path:
    final_output = resolve_output_path(root_dir, output_path, target)
    if compiler_artifact.resolve() != final_output.resolve():
        final_output.write_bytes(compiler_artifact.read_bytes())
    return final_output


def compile_with_clang(
    object_file: Path,
    clang_args: list[str],
    root_dir: Path,
    binary_output: Path | None,
    *,
    capture_output: bool,
    verbose: bool,
) -> Path:
    clang_output_file = resolve_output_path(root_dir, binary_output, "exe")

    full_cmd = [
        "clang",
        str(object_file),
        *clang_args,
        "-pie",
        "-o",
        str(clang_output_file),
        "-lm",
    ]
    _log(verbose, "=== compiling ===")
    _run_checked(full_cmd, "clang", capture_output=capture_output)
    _log(verbose, f"compiled all files to {clang_output_file}")
    return clang_output_file


def compile_project(request: CompileRequest) -> CompileResult:
    root_dir = request.root_dir or Path(__file__).resolve().parent.parent
    target = normalize_target(request.target)
    emit_kind = compiler_emit_kind(target)

    run_compiler(
        root_dir,
        request.compiler_args,
        emit_kind,
        request.debug,
        capture_output=request.capture_output,
    )

    if request.display:
        run_view(
            root_dir,
            capture_output=request.capture_output,
            verbose=request.verbose,
        )

    compiler_artifact = expected_compiler_artifact_path(root_dir, emit_kind)
    if not compiler_artifact.exists():
        raise FileNotFoundError(f"Expected compiler artifact not found: {compiler_artifact}")

    if target != "exe":
        output = emit_compiler_artifact(compiler_artifact, root_dir, target, request.output)
        _log(request.verbose, f"emitted {target} artifact to {output}")
        return CompileResult(
            compiler_artifact_path=compiler_artifact,
            artifact_path=output,
            target=target,
        )

    binary_output = compile_with_clang(
        compiler_artifact,
        request.clang_args,
        root_dir,
        request.output,
        capture_output=request.capture_output,
        verbose=request.verbose,
    )
    return CompileResult(
        compiler_artifact_path=compiler_artifact,
        artifact_path=binary_output,
        target=target,
    )
