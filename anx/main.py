#! /usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from anx import project_tests
from anx.diagnostics import format_diagnostic
from anx.project import CycleError, PackageKind, Project, discover, load
from anx.scaffold import KINDS, scaffold

_YIAN_ROOT = Path(__file__).resolve().parent.parent
_STD_SRC = _YIAN_ROOT / "lib" / "src"


def compiler_command() -> list[str]:
    """Return the argv prefix used to invoke the YIAN compiler.

    Prefer an explicit ``YIANC`` override, then a ``yianc`` executable on
    ``PATH``, so that anx and user scripts share one entry point.  Fall back to
    ``python3 -m compiler.main`` when the compiler is only importable from this
    checkout.
    """
    override = os.environ.get("YIANC")
    if override:
        return [override]
    found = shutil.which("yianc")
    if found is not None:
        return [found]
    return [sys.executable, "-m", "compiler.main"]


def cmd_new(args: argparse.Namespace) -> int:
    kind = args.kind
    if args.lib:
        kind = "lib"
    pkg_dir = scaffold(args.name, kind=kind)
    print(f"Created package '{args.name}' at {pkg_dir}")
    return 0


def _load_project(project_dir: str) -> tuple[Project, Path]:
    """Discover, load and validate the project at or above *project_dir*.

    Returns the project and its root directory; exits with 1 after reporting
    every diagnostic.
    """
    start = Path(project_dir)
    root_dir = discover(start)
    if root_dir is None:
        print(
            f"error[AX001]: no package.anx found in {start.resolve()} or any parent directory",
            file=sys.stderr,
        )
        sys.exit(1)

    try:
        result = load(root_dir, std_root=_STD_SRC)
    except CycleError as exc:
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(1)

    for diagnostic in result.diagnostics:
        print(format_diagnostic(diagnostic), file=sys.stderr)
    if result.diagnostics:
        sys.exit(1)
    project = result.project
    if project is None:
        # load() always pairs a missing project with at least one diagnostic.
        print(f"error: could not load a project from {root_dir}", file=sys.stderr)
        sys.exit(1)
    return project, root_dir


def _compiler_flags(args: argparse.Namespace) -> list[str]:
    """Compiler options whitelisted from the anx command line."""
    flags: list[str] = []
    level = args.opt
    if args.release and level is None:
        level = 3
    if level is not None:
        flags += ["-O", str(level)]
    if args.raw_pointers:
        flags.append("--raw-pointers")
    return flags


def _write_package_map(project: Project) -> Path:
    """Write ``build/pkg.json`` when its content changed, and return its path."""
    build_dir = project.packages[project.root_package].root / "build"
    pkg_json = build_dir / "pkg.json"
    payload = json.dumps(project.compiler_package_map(), indent=2)
    if not pkg_json.is_file() or pkg_json.read_text(encoding="utf-8") != payload:
        build_dir.mkdir(parents=True, exist_ok=True)
        pkg_json.write_text(payload, encoding="utf-8")
    return pkg_json


def _do_build(
    project_dir: str,
    *,
    command: str,
    target: str,
    flags: list[str],
) -> Path | None:
    """Compile the project; exits with 1 on any user error (docs §8.3).

    Returns the produced executable for ``-t exe`` runs, otherwise ``None``.
    """
    project, root_dir = _load_project(project_dir)
    root = project.packages[project.root_package]

    if root.kind is PackageKind.LIB and command in ("build", "run"):
        print(
            f"error: 'anx {command}' is not supported for kind 'lib' packages: "
            "this stage produces no library artifact",
            file=sys.stderr,
        )
        sys.exit(1)

    extra_flags = ["-t", target]
    exe: Path | None = None
    if target == "exe":
        exe = root_dir / "build" / "app"
        extra_flags += ["-o", str(exe)]

    pkg_json = _write_package_map(project)
    cmd = [
        *compiler_command(),
        "--packages", str(pkg_json),
        *extra_flags,
        *flags,
        *(str(f) for f in project.files),
    ]
    result = subprocess.run(cmd, cwd=str(_YIAN_ROOT), capture_output=True, text=True, check=False)

    # Forward both streams whether or not the compiler succeeded, so warnings
    # (for example the shadowed-directory hint) are not swallowed.
    sys.stdout.write(result.stdout)
    sys.stderr.write(result.stderr)
    if result.returncode != 0:
        # anx reports user errors with its own code; the compiler's internal exit
        # codes (including 255 from a diagnostic) are not propagated verbatim.
        sys.exit(1)
    return exe


def cmd_build(args: argparse.Namespace) -> int:
    _do_build(args.project, command="build", target="exe", flags=_compiler_flags(args))
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    exe = _do_build(args.project, command="run", target="exe", flags=_compiler_flags(args))
    if exe is None or not exe.exists():
        print(f"error: no executable was produced at {exe}", file=sys.stderr)
        return 1
    # After the program starts, its exit code is propagated verbatim (§8.3);
    # stdio is inherited so arguments and standard input pass straight through.
    try:
        return subprocess.run([str(exe), *args.program_args]).returncode
    except KeyboardInterrupt:
        return 130


def cmd_check(args: argparse.Namespace) -> int:
    _do_build(args.project, command="check", target="none", flags=_compiler_flags(args))
    return 0


def cmd_test(args: argparse.Namespace) -> int:
    """Run the project's own tests under ``<project>/tests`` (D6, §8.5)."""
    _project, root_dir = _load_project(args.project)
    return project_tests.run(
        root_dir,
        compiler_command(),
        _STD_SRC,
        flags=_compiler_flags(args),
        env=_compiler_env(),
    )


def _compiler_env() -> dict[str, str]:
    """Environment for compiler subprocesses started outside the checkout.

    The fallback entry point is ``python3 -m compiler.main``, which needs the
    checkout on ``sys.path`` regardless of the working directory.
    """
    env = os.environ.copy()
    existing = env.get("PYTHONPATH")
    env["PYTHONPATH"] = str(_YIAN_ROOT) if not existing else f"{_YIAN_ROOT}{os.pathsep}{existing}"
    return env


def _add_project_arg(sub: argparse.ArgumentParser) -> None:
    sub.add_argument("project", nargs="?", default=".", help="Project root directory (default: cwd)")


def _add_build_args(sub: argparse.ArgumentParser) -> None:
    sub.add_argument(
        "-O",
        dest="opt",
        type=int,
        choices=[0, 1, 2, 3],
        default=None,
        help="Optimization level forwarded to the compiler (default: the compiler's own default)",
    )
    sub.add_argument("--release", action="store_true", help="Shorthand for -O3")
    sub.add_argument(
        "--raw-pointers",
        action="store_true",
        help="Build with bare 8-byte pointers (diagnostic mode, no memory-safety guarantee)",
    )


def main(argv: list[str] | None = None) -> int:
    raw_argv = list(sys.argv[1:] if argv is None else argv)

    # `anx run [project] -- <program args>`: split the program arguments off
    # before argparse sees them, so they are never mistaken for anx options.
    program_args: list[str] = []
    if raw_argv and raw_argv[0] == "run" and "--" in raw_argv:
        split = raw_argv.index("--")
        program_args = raw_argv[split + 1 :]
        raw_argv = raw_argv[:split]

    parser = argparse.ArgumentParser(prog="anx")
    sub = parser.add_subparsers(dest="command")

    p = sub.add_parser("new")
    p.add_argument("name")
    p.add_argument("--kind", choices=KINDS, default="bin", help="Package kind (default: bin)")
    p.add_argument("--lib", action="store_true", help="Shorthand for --kind lib")

    p = sub.add_parser("build")
    _add_project_arg(p)
    _add_build_args(p)

    p = sub.add_parser("run")
    _add_project_arg(p)
    _add_build_args(p)

    p = sub.add_parser("check")
    _add_project_arg(p)
    _add_build_args(p)

    p = sub.add_parser("test")
    _add_project_arg(p)
    _add_build_args(p)

    args = parser.parse_args(raw_argv)
    args.program_args = program_args

    match args.command:
        case "new":
            return cmd_new(args)
        case "build":
            return cmd_build(args)
        case "run":
            return cmd_run(args)
        case "check":
            return cmd_check(args)
        case "test":
            return cmd_test(args)
        case _:
            parser.print_help()
            return 1


if __name__ == "__main__":
    raise SystemExit(main())
