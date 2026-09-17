#! /usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import cast

from anx import project_tests
from anx.diagnostics import (
    AX_DEPENDENCY_CYCLE,
    AX_NO_PROJECT_ROOT,
    Diagnostic,
    diagnostic_payload,
    format_diagnostic,
)
from anx.project import CycleError, PackageKind, Project, discover, load
from anx.scaffold import KINDS, scaffold
from compiler.analysis.source_provenance import resolve_stdlib_root

__CHECKOUT = Path(__file__).resolve().parent.parent
# The standard library an anx run compiles against. In a non-editable install
# this comes from YIAN_LIB / YIAN_ROOT.
__STD_SRC = resolve_stdlib_root()


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
    try:
        pkg_dir = scaffold(args.name, kind=kind)
    except FileExistsError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(f"Created package '{args.name}' at {pkg_dir}")
    return 0


def __require_stdlib() -> None:
    """Fail early when the standard library cannot be located."""
    if __STD_SRC.is_dir():
        return
    print(
        f"error: standard library source root {__STD_SRC} does not exist.\n"
        "       Set YIAN_LIB to the stdlib src directory, or YIAN_ROOT to a\n"
        "       checkout root; a non-editable install does not carry lib/.",
        file=sys.stderr,
    )
    sys.exit(1)


def __load_project(project_dir: str) -> tuple[Project, Path]:
    """Discover, load and validate the project at or above *project_dir*.

    Returns the project and its root directory; exits with 1 after reporting
    every diagnostic.
    """
    __require_stdlib()
    start = Path(project_dir)
    root_dir = discover(start)
    if root_dir is None:
        print(
            f"error[AX001]: no package.anx found in {start.resolve()} or any parent directory",
            file=sys.stderr,
        )
        sys.exit(1)

    try:
        result = load(root_dir, std_root=__STD_SRC)
    except CycleError as exc:
        print(f"error[{AX_DEPENDENCY_CYCLE}]: {exc}", file=sys.stderr)
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


def __compiler_flags(args: argparse.Namespace) -> list[str]:
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


def __write_package_map(project: Project) -> Path:
    """Write ``build/pkg.json`` when its content changed, and return its path."""
    build_dir = project.packages[project.root_package].root / "build"
    pkg_json = build_dir / "pkg.json"
    payload = json.dumps(project.compiler_package_map(), indent=2)
    if not pkg_json.is_file() or pkg_json.read_text(encoding="utf-8") != payload:
        build_dir.mkdir(parents=True, exist_ok=True)
        pkg_json.write_text(payload, encoding="utf-8")
    return pkg_json


def __do_build(
    project_dir: str,
    *,
    command: str,
    target: str,
    flags: list[str],
) -> Path | None:
    """Compile the project; exits with 1 on any user error.

    Returns the produced executable for ``-t exe`` runs, otherwise ``None``.
    """
    project, root_dir = __load_project(project_dir)
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

    pkg_json = __write_package_map(project)
    cmd = [
        *compiler_command(),
        "--packages", str(pkg_json),
        *extra_flags,
        *flags,
        *(str(f) for f in project.build_files()),
    ]
    result = subprocess.run(
        cmd,
        cwd=str(root_dir),
        capture_output=True,
        text=True,
        check=False,
        env=__compiler_env(),
    )

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
    __do_build(args.project, command="build", target="exe", flags=__compiler_flags(args))
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    exe = __do_build(args.project, command="run", target="exe", flags=__compiler_flags(args))
    if exe is None or not exe.exists():
        print(f"error: no executable was produced at {exe}", file=sys.stderr)
        return 1
    # After the program starts, its exit code is propagated verbatim;
    # stdio is inherited so arguments and standard input pass straight through.
    try:
        return subprocess.run([str(exe), *args.program_args]).returncode
    except KeyboardInterrupt:
        return 130


def cmd_check(args: argparse.Namespace) -> int:
    __do_build(args.project, command="check", target="none", flags=__compiler_flags(args))
    return 0


def cmd_graph(args: argparse.Namespace) -> int:
    """Print the dependency graph, file index and diagnostics.

    The payload is always printed, so a script can consume it even when the
    exit code reports a problem: 1 means the project was unusable or carried
    diagnostics, 0 means it loaded cleanly.
    """
    __require_stdlib()
    start = Path(args.project)
    root_dir = discover(start)
    clean = False

    if root_dir is None:
        payload = __empty_graph_payload(
            [
                Diagnostic(
                    AX_NO_PROJECT_ROOT,
                    f"No package.anx found in {start.resolve()} or any parent directory",
                    start.resolve(),
                )
            ]
        )
    else:
        try:
            result = load(root_dir, std_root=__STD_SRC)
        except CycleError as exc:
            # A cycle has no partial graph to describe, but `graph` still owes
            # its caller a payload, so the cycle is reported as one diagnostic.
            payload = __empty_graph_payload(
                [
                    Diagnostic(
                        AX_DEPENDENCY_CYCLE,
                        str(exc),
                        root_dir / "package.anx",
                        hint="Remove one of the dependency edges in the cycle, or "
                        "extract the shared code into a third package.",
                    )
                ]
            )
        else:
            project = result.project
            if project is None:
                payload = __empty_graph_payload(list(result.diagnostics))
            else:
                payload = project.describe(result.diagnostics)
                clean = not result.diagnostics

    if args.json:
        print(json.dumps(payload, indent=2))
    else:
        __print_graph(payload)
    return 0 if clean else 1


def __empty_graph_payload(diagnostics: list[Diagnostic]) -> dict[str, object]:
    """The payload for a project that could not be described."""
    return {
        "root": None,
        "std": None,
        "packages": {},
        "files": [],
        "dependencies": {},
        "devDependencies": {},
        "diagnostics": [diagnostic_payload(d) for d in diagnostics],
    }


def __print_graph(payload: dict[str, object]) -> None:
    """Human-readable rendering of the ``anx graph`` payload."""
    print(f"project: {payload.get('root')}")
    packages = __as_table(payload.get("packages"))
    if packages is not None:
        for name, value in packages.items():
            spec = __as_table(value)
            if spec is None:
                continue
            entry = spec.get("entry")
            suffix = f"  entry={entry}" if entry is not None else ""
            dev = spec.get("devDependencies")
            dev_suffix = f"  dev={dev}" if dev else ""
            print(
                f"  {name:<16} {spec.get('kind')!s:<7} {spec.get('sourceRoot')}"
                f"{suffix}  deps={spec.get('dependencies')}{dev_suffix}"
            )
    files_value = payload.get("files")
    files: list[object] = (
        cast("list[object]", files_value) if isinstance(files_value, list) else []
    )
    print(f"files: {len(files)}")
    diagnostics_value = payload.get("diagnostics")
    diagnostics: list[object] = (
        cast("list[object]", diagnostics_value) if isinstance(diagnostics_value, list) else []
    )
    if diagnostics:
        print("diagnostics:")
        for value in diagnostics:
            item = __as_table(value)
            if item is None:
                continue
            print(f"  error[{item.get('code')}]: {item.get('path')}: {item.get('message')}")


def __as_table(value: object) -> dict[str, object] | None:
    """Narrow an untyped JSON value to a string-keyed table."""
    if not isinstance(value, dict):
        return None
    table: dict[str, object] = {}
    for key, item in cast("dict[object, object]", value).items():
        if not isinstance(key, str):
            return None
        table[key] = item
    return table


def cmd_test(args: argparse.Namespace) -> int:
    """Run the project's own tests under ``<project>/tests``."""
    project, _root_dir = __load_project(args.project)
    return project_tests.run(
        project,
        compiler_command(),
        flags=__compiler_flags(args),
        env=__compiler_env(),
    )


def __compiler_env() -> dict[str, str]:
    """Environment for compiler subprocesses.

    Only a source checkout needs help: its fallback entry point is
    ``python3 -m compiler.main``, which requires the checkout on ``sys.path``
    regardless of the working directory. An installed compiler is already
    importable.
    """
    env = os.environ.copy()
    if not (__CHECKOUT / "compiler" / "main.py").is_file():
        return env
    existing = env.get("PYTHONPATH")
    env["PYTHONPATH"] = str(__CHECKOUT) if not existing else f"{__CHECKOUT}{os.pathsep}{existing}"
    return env


def __add_project_arg(sub: argparse.ArgumentParser) -> None:
    sub.add_argument("project", nargs="?", default=".", help="Project root directory (default: cwd)")


def __add_build_args(sub: argparse.ArgumentParser) -> None:
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
    __add_project_arg(p)
    __add_build_args(p)

    p = sub.add_parser(
        "run",
        epilog="Program arguments go after '--': anx run [project] -- arg1 arg2",
    )
    __add_project_arg(p)
    __add_build_args(p)

    p = sub.add_parser("check")
    __add_project_arg(p)
    __add_build_args(p)

    p = sub.add_parser("test")
    __add_project_arg(p)
    __add_build_args(p)

    p = sub.add_parser("graph")
    __add_project_arg(p)
    p.add_argument("--json", action="store_true", help="Emit machine-readable JSON")

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
        case "graph":
            return cmd_graph(args)
        case _:
            parser.print_help()
            return 1


if __name__ == "__main__":
    raise SystemExit(main())
