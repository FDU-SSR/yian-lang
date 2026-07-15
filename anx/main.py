#! /usr/bin/env python3
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

from anx.manifest import Manifest
from anx.resolver import resolve
from anx.scaffold import scaffold

_YIAN_ROOT = Path(__file__).resolve().parent.parent
_STD_LIB = _YIAN_ROOT / "lib"


def cmd_new(args: argparse.Namespace) -> None:
    pkg_dir = scaffold(args.name, is_lib=args.lib)
    print(f"Created package '{args.name}' at {pkg_dir}")


def _do_build(project_dir: str, extra_flags: list[str] | None = None) -> None:
    project = Path(project_dir).resolve()
    manifest = Manifest.from_file(project / "package.anx")
    all_files, pkg_roots = resolve(manifest, _STD_LIB, cwd=project)

    pkg_json = project / "build" / "pkg.json"
    pkg_json.parent.mkdir(parents=True, exist_ok=True)
    pkg_json.write_text(json.dumps({k: str(v) for k, v in pkg_roots.items()}, indent=2))

    cmd = [
        sys.executable, "-m", "compiler.main",
        "--packages", str(pkg_json),
        *(extra_flags or []),
        *(str(f) for f in all_files),
    ]
    result = subprocess.run(cmd, cwd=str(_YIAN_ROOT), capture_output=True, text=True, check=False)

    if result.returncode != 0:
        sys.stdout.write(result.stdout)
        sys.stderr.write(result.stderr)
        sys.exit(result.returncode)


def cmd_build(args: argparse.Namespace) -> None:
    project = Path(args.project).resolve()
    _do_build(args.project, [
        "-t", "exe",
        "-o", str(project / "build" / "app"),
    ])


def cmd_run(args: argparse.Namespace) -> None:
    project = Path(args.project).resolve()
    _do_build(args.project, [
        "-t", "exe",
        "-o", str(project / "build" / "app"),
    ])
    exe = project / "build" / "app"
    if exe.exists():
        subprocess.run([str(exe)], check=True)


def cmd_check(args: argparse.Namespace) -> None:
    _do_build(args.project, ["-t", "none"])


def cmd_test(args: argparse.Namespace) -> None:
    test_runner = _YIAN_ROOT / "anx" / "tests" / "run.py"
    subprocess.run([sys.executable, str(test_runner)], check=True, cwd=str(_YIAN_ROOT))


def _add_project_arg(sub: argparse.ArgumentParser) -> None:
    sub.add_argument("project", nargs="?", default=".", help="Project root directory (default: cwd)")


def main() -> None:
    parser = argparse.ArgumentParser(prog="anx")
    sub = parser.add_subparsers(dest="command")

    p = sub.add_parser("new")
    p.add_argument("name")
    p.add_argument("--lib", action="store_true", help="Create a library package (no main.an)")

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
