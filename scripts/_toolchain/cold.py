"""Cold bootstrap and transactionally install the native compiler tools."""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Any

SCRIPTS_ROOT = Path(__file__).resolve().parents[1]
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

from _toolchain.identity import (  # noqa: E402
    component_source_sha256,
    dump_build_info,
    file_sha256,
    resources_source_sha256,
    tool_build_info,
    tree_sha256,
)


class ColdInstallError(Exception):
    """The cold build could not produce a complete, safe installation."""


class InterruptedBuild(ColdInstallError):
    """A handled termination signal interrupted the cold build."""


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True, type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--prefix", required=True, type=Path)
    parser.add_argument("--python", required=True, type=Path)
    return parser.parse_args(argv)


def _resolve_executable(path: Path) -> Path:
    raw = str(path)
    resolved = shutil.which(raw)
    if resolved is None:
        raise ColdInstallError(f"required executable was not found: {raw}")
    candidate = Path(resolved).resolve()
    if not candidate.is_file() or not os.access(candidate, os.X_OK):
        raise ColdInstallError(f"required executable is not executable: {candidate}")
    return candidate


def _version_major(output: str) -> int | None:
    match = re.search(r"(?<!\d)(\d+)\.(?:\d+|x)", output)
    return int(match.group(1)) if match else None


def _preflight(python: Path) -> dict[str, dict[str, str]]:
    check = (
        "import sys, llvmlite, llvmlite.binding as llvm; "
        "sys.exit(0 if sys.version_info >= (3, 12) and "
        "tuple(map(int, llvmlite.__version__.split('.')[:2])) >= (0, 49) and "
        "llvm.llvm_version_info[0] == 22 else 1)"
    )
    python_check = subprocess.run(
        [str(python), "-c", check], check=False, capture_output=True, text=True
    )
    if python_check.returncode != 0:
        raise ColdInstallError(
            "cold bootstrap requires Python 3.12+, llvmlite 0.49+, and bundled LLVM 22"
        )

    tools: dict[str, dict[str, str]] = {}
    for name in ("llvm-config", "clang", "llvm-ar"):
        executable = _resolve_executable(Path(name))
        version_flag = "--version" if name != "llvm-config" else "--version"
        completed = subprocess.run(
            [str(executable), version_flag], check=False, capture_output=True, text=True
        )
        version = f"{completed.stdout}\n{completed.stderr}".strip()
        if completed.returncode != 0 or _version_major(version) != 22:
            raise ColdInstallError(f"{name} must be LLVM 22; found {version or 'no version output'}")
        tools[name] = {"path": str(executable), "version": version, "sha256": file_sha256(executable)}
    return tools


def _record_command(path: Path, *, label: str, argv: list[str], cwd: Path, code: int) -> None:
    event = {
        "time": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "label": label,
        "argv": argv,
        "cwd": str(cwd),
        "exit_code": code,
    }
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(event, ensure_ascii=False) + "\n")


def _run(
    argv: list[str],
    *,
    cwd: Path,
    environment: dict[str, str],
    journal: Path,
    label: str,
) -> None:
    completed = subprocess.run(argv, cwd=cwd, env=environment, check=False)
    _record_command(journal, label=label, argv=argv, cwd=cwd, code=completed.returncode)
    if completed.returncode != 0:
        raise ColdInstallError(f"{label} failed with exit code {completed.returncode}: {argv!r}")


def _copy_fresh_build(
    *,
    anx: Path,
    compiler: Path,
    project: Path,
    output: Path,
    label: str,
    root: Path,
    environment: dict[str, str],
    journal: Path,
    backup_root: Path,
) -> None:
    build_app = project / "build" / "app"
    backup = backup_root / f"{label}-app"
    build_app.parent.mkdir(parents=True, exist_ok=True)
    backup_root.mkdir(parents=True, exist_ok=True)
    had_original = build_app.exists() or build_app.is_symlink()
    if had_original:
        os.replace(build_app, backup)
    try:
        build_environment = environment.copy()
        build_environment.update(
            {"YIAN_ROOT": str(root), "YIAN_LIB": str(root / "lib" / "src"), "YIANC": str(compiler)}
        )
        _run(
            [str(anx), "build", str(project), "-O2"],
            cwd=root,
            environment=build_environment,
            journal=journal,
            label=f"build-{label}",
        )
        if not build_app.is_file() or build_app.is_symlink():
            raise ColdInstallError(f"native anx did not create a fresh build output for {project}")
        output.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(build_app, output)
        output.chmod(0o755)
    finally:
        if build_app.exists() or build_app.is_symlink():
            if build_app.is_dir() and not build_app.is_symlink():
                shutil.rmtree(build_app)
            else:
                build_app.unlink()
        if had_original and (backup.exists() or backup.is_symlink()):
            os.replace(backup, build_app)
        elif had_original:
            raise ColdInstallError(f"previous build output could not be restored: {build_app}")


def _lock(root: Path, prefix: Path):
    lock_root = root / "build" / "toolchain-workflows" / "locks"
    lock_root.mkdir(parents=True, exist_ok=True)
    key = __import__("hashlib").sha256(str(prefix).encode("utf-8")).hexdigest()
    stream = (lock_root / f"{key}.lock").open("a+")
    try:
        fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        stream.close()
        raise ColdInstallError(f"another toolchain operation is using this checkout and prefix: {prefix}")
    return stream


def _prepare_legacy_lsp_marker(prefix: Path, stage: Path) -> tuple[Path, Path] | None:
    data_dir = prefix / "share" / "yian"
    old_marker = data_dir / ".native-install"
    lsp_binary = prefix / "bin" / "yian-lsp"
    lsp_marker = prefix / "share" / "yian-lsp" / ".native-install"
    if lsp_marker.exists() or not old_marker.is_file():
        return None
    try:
        values = {}
        for line in old_marker.read_text(encoding="utf-8").splitlines():
            key, separator, value = line.partition("=")
            if separator:
                values[key] = value
        expected = values.get("yian-lsp", "")
        if not expected:
            return None
        if lsp_binary.is_symlink() or not lsp_binary.is_file():
            raise ColdInstallError(f"legacy install record refers to a missing or symlinked LSP binary: {lsp_binary}")
        if file_sha256(lsp_binary) != expected:
            raise ColdInstallError(f"legacy LSP binary no longer matches its install record: {lsp_binary}")
        version = subprocess.run(
            [str(lsp_binary), "--version"], check=False, capture_output=True, text=True
        )
        if version.returncode != 0 or not version.stdout.startswith("yian-lsp "):
            raise ColdInstallError(f"legacy LSP binary failed its version check: {lsp_binary}")
    except ColdInstallError:
        raise
    except OSError:
        raise ColdInstallError(f"could not inspect the recorded legacy LSP binary: {lsp_binary}") from None
    marker_stage = stage / "legacy-lsp-marker"
    marker_stage.write_text(
        "yian-lsp-install-v1\n"
        + f"version={version.stdout.rstrip()}\n"
        + f"\nsha256={expected}\n",
        encoding="utf-8",
    )
    return lsp_marker, marker_stage


def _source_snapshot(root: Path) -> dict[str, str]:
    return {
        "yianc": component_source_sha256(root, "yianc"),
        "anx": component_source_sha256(root, "anx"),
        "resources": resources_source_sha256(root),
    }


def _build(args: argparse.Namespace) -> None:
    root = args.root.resolve()
    python = _resolve_executable(args.python)
    prefix_input = args.prefix
    if prefix_input.is_symlink():
        raise ColdInstallError(f"refusing to use a symlinked installation prefix: {prefix_input}")
    prefix = prefix_input.resolve()
    if prefix.exists() and not prefix.is_dir():
        raise ColdInstallError(f"installation prefix is not a directory: {prefix}")

    tools = _preflight(python)
    lock = _lock(root, prefix)
    try:
        run_root = root / "build" / "toolchain-workflows"
        run_root.mkdir(parents=True, exist_ok=True)
        run_id = f"cold-{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}-{os.getpid()}-{uuid.uuid4().hex[:8]}"
        run_dir = run_root / run_id
        run_dir.mkdir()
        stage = run_dir / "stage"
        stage.mkdir()
        journal = run_dir / "commands.jsonl"
        bundle = stage / "bundle"
        binaries = bundle / "bin"
        data = bundle / "share" / "yian"
        bootstrap = stage / "bootstrap"
        binaries.mkdir(parents=True)
        bootstrap.mkdir(parents=True)
        (data / "lib").mkdir(parents=True)
        (data / "runtime").mkdir(parents=True)

        initial_sources = _source_snapshot(root)
        python_compiler = bootstrap / "yianc-python"
        python_compiler.write_text(
            "#!/usr/bin/env bash\n"
            f"export PYTHONPATH={shlex_quote(str(root / 'python'))}\n"
            f"exec {shlex_quote(str(python))} -m yianc.main \"$@\"\n",
            encoding="utf-8",
        )
        python_compiler.chmod(0o755)

        clean_environment = os.environ.copy()
        for variable in ("YIANC", "YIAN_ROOT", "YIAN_LIB", "PYTHONPATH"):
            clean_environment.pop(variable, None)
        seed = bootstrap / "anx-seed"
        seed_command = [
            str(python),
            str(root / "scripts" / "_toolchain" / "bootstrap_anx.py"),
            "--python",
            str(python),
            "--output",
            str(seed),
        ]
        _run(
            seed_command,
            cwd=root,
            environment=clean_environment,
            journal=journal,
            label="bootstrap-seed-anx",
        )
        if not seed.is_file() or not os.access(seed, os.X_OK):
            raise ColdInstallError(f"Python yianc did not create an executable seed anx: {seed}")

        print("Building native yianc with the temporary native anx and checkout Python compiler...", flush=True)
        _copy_fresh_build(
            anx=seed,
            compiler=python_compiler,
            project=root / "bootstrap" / "yianc",
            output=binaries / "yianc",
            label="yianc",
            root=root,
            environment=clean_environment,
            journal=journal,
            backup_root=stage / "build-backups",
        )

        print("Building final native anx with the temporary native anx and staged yianc...", flush=True)
        _copy_fresh_build(
            anx=seed,
            compiler=binaries / "yianc",
            project=root / "bootstrap" / "anx",
            output=binaries / "anx",
            label="anx",
            root=root,
            environment=clean_environment,
            journal=journal,
            backup_root=stage / "build-backups",
        )

        for command_name in ("yianc", "anx"):
            command = binaries / command_name
            if not command.is_file() or not os.access(command, os.X_OK):
                raise ColdInstallError(f"cold build did not produce executable {command_name}: {command}")
            _run(
                [str(command), "--help"],
                cwd=root,
                environment=clean_environment,
                journal=journal,
                label=f"help-{command_name}",
            )

        shutil.copytree(root / "lib" / "src", data / "lib" / "src")
        shutil.copytree(root / "runtime" / "src", data / "runtime" / "src")
        shutil.copytree(root / "runtime" / "include", data / "runtime" / "include")
        if _source_snapshot(root) != initial_sources:
            raise ColdInstallError("toolchain source inputs changed during the cold build; refusing publication")

        info = tool_build_info(
            root,
            yianc=binaries / "yianc",
            anx=binaries / "anx",
            seed_anx=seed,
            python=python,
            build_options={"optimization": "-O2", "pointer_mode": "fat", "workflow": "cold"},
            build_tools=tools,
            resource_stage=data,
        )
        dump_build_info(data / ".build-info", info)
        resource_hash = tree_sha256(
            data, excluded_names=frozenset({".native-install", ".build-info"})
        )
        build_info_hash = file_sha256(data / ".build-info")
        (data / ".native-install").write_text(
            "yian-native-install-v1\n"
            f"yianc={file_sha256(binaries / 'yianc')}\n"
            f"anx={file_sha256(binaries / 'anx')}\n"
            f"resources={resource_hash}\n",
            encoding="utf-8",
        )
        with (data / ".native-install").open("a", encoding="utf-8") as stream:
            stream.write(f"build_info_sha256={build_info_hash}\n")

        additional = _prepare_legacy_lsp_marker(prefix, bootstrap)
        publish_command = [
            "bash",
            str(root / "scripts" / "_toolchain" / "publish.sh"),
            "--prefix",
            str(prefix),
            "--bundle",
            str(bundle),
        ]
        if additional is not None:
            _target, staged_marker = additional
            publish_command.extend(("--legacy-lsp-marker", str(staged_marker)))
        _run(
            publish_command,
            cwd=root,
            environment=clean_environment,
            journal=journal,
            label="publish-toolchain",
        )
        print(f"Installed native yianc and anx under {prefix}", flush=True)
        shutil.rmtree(run_dir)
    finally:
        fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
        lock.close()


def shlex_quote(value: str) -> str:
    import shlex

    return shlex.quote(value)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    previous_term = signal.getsignal(signal.SIGTERM)

    def interrupt(signum: int, _frame: Any) -> None:
        raise InterruptedBuild(f"received signal {signum}; restoring the active build output")

    signal.signal(signal.SIGTERM, interrupt)
    try:
        _build(args)
        return 0
    except (ColdInstallError, OSError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    finally:
        signal.signal(signal.SIGTERM, previous_term)


if __name__ == "__main__":
    raise SystemExit(main())
