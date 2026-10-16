"""Strict resolution and identity checks for installed native YIAN tools."""

from __future__ import annotations

import hashlib
import os
import shutil
from pathlib import Path

ELF_MAGIC = b"\x7fELF"


class NativeToolError(ValueError):
    """A selected tool is missing or is not an executable ELF binary."""


def resolve_native(program: str, *, search_path: str | None = None) -> Path:
    """Resolve *program* to a canonical, executable ELF file.

    A program containing a path separator is treated as an explicit path.
    Bare names are searched in PATH. Validation failures never trigger a
    second lookup or a compiler fallback.
    """
    if not program:
        raise NativeToolError("native tool name is empty")

    if os.path.sep in program or (os.path.altsep and os.path.altsep in program):
        candidate = Path(program)
    else:
        found = shutil.which(program, path=search_path)
        if found is None:
            raise NativeToolError(f"native tool {program!r} was not found on PATH")
        candidate = Path(found)

    try:
        resolved = candidate.resolve(strict=True)
    except OSError as error:
        raise NativeToolError(f"cannot resolve native tool {candidate}: {error}") from error

    if not resolved.is_file():
        raise NativeToolError(f"native tool is not a regular file: {resolved}")
    if not os.access(resolved, os.X_OK):
        raise NativeToolError(f"native tool is not executable: {resolved}")
    try:
        with resolved.open("rb") as stream:
            magic = stream.read(len(ELF_MAGIC))
    except OSError as error:
        raise NativeToolError(f"cannot read native tool {resolved}: {error}") from error
    if magic != ELF_MAGIC:
        raise NativeToolError(f"native tool is not an ELF executable: {resolved}")
    return resolved


def resolve_native_selection(
    *,
    explicit: str | None,
    configured: str | None,
    default: str,
    search_path: str | None = None,
) -> tuple[Path, str]:
    """Resolve explicit, environment, or PATH selection without fallback."""
    if explicit is not None:
        return resolve_native(explicit, search_path=search_path), "explicit"
    if configured:
        return resolve_native(configured, search_path=search_path), "YIANC"
    return resolve_native(default, search_path=search_path), "PATH"


def file_sha256(path: Path) -> str:
    """Return a streaming SHA-256 digest for a native tool artifact."""
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
