#! /usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from anx.diagnostics import format_diagnostic
from anx.project import CycleError, PackageKind, Project, load
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


def cmd_new(args: argparse.Namespace) -> None:
    kind = args.kind
    if args.lib:
        kind = "lib"
    pkg_dir = scaffold(args.name, kind=kind)
    print(f"Created package '{args.name}' at {pkg_dir}")


def _load_project(project_dir: str) -> Project:
    """Load *project_dir* and report every diagnostic before giving up."""
    try:
        result = load(Path(project_dir), std_root=_STD_SRC)
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
        print(f"error: could not load a project from {project_dir}", file=sys.stderr)
        sys.exit(1)
    return project


def _do_build(
    project_dir: str,
    extra_flags: list[str] | None = None,
    *,
    command: str = "build",
) -> None:
    project = _load_project(project_dir)
    root = project.packages[project.root_package]

    if root.kind is PackageKind.LIB and command in ("build", "run"):
        print(
            f"error: 'anx {command}' is not supported for kind 'lib' packages: "
            "this stage produces no library artifact",
            file=sys.stderr,
        )
        sys.exit(1)

    build_dir = root.root / "build"
    pkg_json = build_dir / "pkg.json"
    pkg_json.parent.mkdir(parents=True, exist_ok=True)
    pkg_json.write_text(json.dumps(project.compiler_package_map(), indent=2))

    cmd = [
        *compiler_command(),
        "--packages", str(pkg_json),
        *(extra_flags or []),
        *(str(f) for f in project.files),
    ]
    result = subprocess.run(cmd, cwd=str(_YIAN_ROOT), capture_output=True, text=True, check=False)

    # Forward both streams whether or not the compiler succeeded, so warnings
    # (for example the shadowed-directory hint) are not swallowed.
    sys.stdout.write(result.stdout)
    sys.stderr.write(result.stderr)
    if result.returncode != 0:
        sys.exit(result.returncode)


def cmd_build(args: argparse.Namespace) -> None:
    project_dir = Path(args.project).resolve()
    _do_build(
        args.project,
        ["-t", "exe", "-o", str(project_dir / "build" / "app")],
        command="build",
    )


def cmd_run(args: argparse.Namespace) -> None:
    project_dir = Path(args.project).resolve()
    _do_build(
        args.project,
        ["-t", "exe", "-o", str(project_dir / "build" / "app")],
        command="run",
    )
    exe = project_dir / "build" / "app"
    if exe.exists():
        subprocess.run([str(exe)], check=True)


def cmd_check(args: argparse.Namespace) -> None:
    _do_build(args.project, ["-t", "none"], command="check")


def cmd_test(args: argparse.Namespace) -> None:
    """Run anx's own integration suite — the ``package`` suite under ``tests/``."""
    runner = _YIAN_ROOT / "scripts" / "run_tests.py"
    subprocess.run(
        [sys.executable, str(runner), "--suite", "package"],
        check=True,
        cwd=str(_YIAN_ROOT),
    )


def _add_project_arg(sub: argparse.ArgumentParser) -> None:
    sub.add_argument("project", nargs="?", default=".", help="Project root directory (default: cwd)")


def main() -> None:
    parser = argparse.ArgumentParser(prog="anx")
    sub = parser.add_subparsers(dest="command")

    p = sub.add_parser("new")
    p.add_argument("name")
    p.add_argument(
        "--kind",
        choices=KINDS,
        default="bin",
        help="Package kind (default: bin)",
    )
    p.add_argument("--lib", action="store_true", help="Shorthand for --kind lib")

    p = sub.add_parser("build")
    _add_project_arg(p)

    p = sub.add_parser("run")
    _add_project_arg(p)

    p = sub.add_parser("check")
    _add_project_arg(p)

    sub.add_parser("test")

    args = parser.parse_args()
    match args.command:
        case "new":
            cmd_new(args)
        case "build":
            cmd_build(args)
        case "run":
            cmd_run(args)
        case "check":
            cmd_check(args)
        case "test":
            cmd_test(args)
        case _:
            parser.print_help()
            sys.exit(1)


if __name__ == "__main__":
    main()
