#!/usr/bin/env python3
"""Record hashes and toolchain metadata for the two fixed native libraries."""

from __future__ import annotations

import hashlib
import json
import os
import platform
import shlex
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CFLAGS = shlex.split(os.environ.get("CFLAGS", "-O2 -fPIC -fno-lto"))
LDFLAGS = shlex.split(os.environ.get("LDFLAGS", ""))


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def command_text(command: list[str]) -> str:
    result = subprocess.run(command, text=True, capture_output=True, check=True)
    text = (result.stdout or result.stderr).strip()
    return text.splitlines()[0] if text else ""


def tool(command_value: str, fallback: str) -> tuple[str, Path, str]:
    parts = shlex.split(command_value) or [fallback]
    resolved = shutil.which(parts[0])
    if resolved is None:
        raise SystemExit(f"required tool not found: {parts[0]}")
    executable = Path(resolved).resolve(strict=True)
    return " ".join(parts), executable, command_text(parts + ["--version"])


def symbol_requirements(archive: Path) -> list[str]:
    undefined = subprocess.run(
        ["nm", "-A", "-u", str(archive)], text=True, capture_output=True, check=True
    ).stdout
    defined = subprocess.run(
        ["nm", "-A", "--defined-only", str(archive)],
        text=True,
        capture_output=True,
        check=True,
    ).stdout
    undefined_symbols = {line.split()[-1] for line in undefined.splitlines() if line.split()}
    defined_symbols = {line.split()[-1] for line in defined.splitlines() if line.split()}
    return sorted(undefined_symbols - defined_symbols)


def file_records(relative_paths: list[str]) -> list[dict[str, str]]:
    records = []
    for relative in relative_paths:
        path = ROOT / relative
        if not path.is_file():
            raise SystemExit(f"required native artifact is missing: {relative}")
        records.append({"path": relative, "sha256": sha256(path)})
    return records


def libc_identity() -> str:
    ldd = shutil.which("ldd")
    if ldd is None:
        raise SystemExit("ldd is required to record the host libc")
    version = command_text([ldd, "--version"])
    release = {}
    try:
        for line in Path("/etc/os-release").read_text(encoding="utf-8").splitlines():
            key, separator, value = line.partition("=")
            if separator and key in {"NAME", "VERSION_ID"}:
                release[key] = value.strip('"')
    except OSError:
        pass
    os_name = release.get("NAME", platform.system())
    os_version = release.get("VERSION_ID", "unknown")
    return f"{version}; {os_name} {os_version}; {platform.machine()}"


def build_record(
    *,
    compiler_name: str,
    compiler_path: Path,
    compiler_version: str,
    archiver_version: str,
    configure: list[str],
    isa: str,
    jit: bool,
    archive: Path,
    host: str,
) -> dict[str, object]:
    linker = shutil.which("ld")
    if linker is None:
        raise SystemExit("ld is required to record the host linker")
    return {
        "compiler": compiler_name,
        "compiler_sha256": sha256(compiler_path),
        "compiler_version": compiler_version,
        "linker_version": command_text([linker, "--version"]),
        "archiver_version": archiver_version,
        "libc": libc_identity(),
        "configure": configure,
        "cflags": CFLAGS,
        "ldflags": LDFLAGS,
        "isa": isa,
        "pic": True,
        "lto": False,
        "pcre2_jit": jit,
        "required_symbols": symbol_requirements(archive),
        "verified_hosts": [host],
    }


def library_record(
    *,
    name: str,
    version: str,
    source_url: str,
    source_archive: str,
    git_commit: str | None,
    headers: list[str],
    archives: list[str],
    licenses: list[str],
    build: dict[str, object],
) -> dict[str, object]:
    source_path = ROOT / source_archive
    return {
        "name": name,
        "version": version,
        "source_url": source_url,
        "source_sha256": sha256(source_path),
        "source_archive": source_archive,
        "git_commit": git_commit,
        "headers": file_records(headers),
        "archives": file_records(archives),
        "licenses": file_records(licenses),
        "build": build,
        "instrumentation": {"asan": False, "yian_guards": False},
    }


def main() -> None:
    if platform.system() != "Linux" or platform.machine() != "x86_64":
        raise SystemExit("native artifact manifest supports the verified Linux x86_64 build host only")
    if any(flag.startswith("-flto") or flag in {"-march=native", "-mcpu=native"} for flag in CFLAGS):
        raise SystemExit("CFLAGS must not enable LTO or host-native ISA tuning")

    cc_value = os.environ.get("CC", "clang")
    ar_value = os.environ.get("AR", "llvm-ar")
    compiler_name, compiler_path, compiler_version = tool(cc_value, "clang")
    _, _, archiver_version = tool(ar_value, "llvm-ar")
    host = f"{platform.system()} {platform.machine()}; {libc_identity()}"

    gmp_archive = ROOT / "gmp/lib/libgmp.a"
    pcre_archive = ROOT / "pcre2/lib/libpcre2-8.a"
    gmp = library_record(
        name="gmp",
        version="6.3.0",
        source_url="https://ftp.gnu.org/gnu/gmp/gmp-6.3.0.tar.xz",
        source_archive="sources/gmp-6.3.0.tar.xz",
        git_commit=None,
        headers=["gmp/include/gmp.h"],
        archives=["gmp/lib/libgmp.a"],
        licenses=[
            "licenses/gmp/COPYING",
            "licenses/gmp/COPYINGv2",
            "licenses/gmp/COPYINGv3",
            "licenses/gmp/COPYING.LESSERv3",
        ],
        build=build_record(
            compiler_name=compiler_name,
            compiler_path=compiler_path,
            compiler_version=compiler_version,
            archiver_version=archiver_version,
            configure=[
                "--disable-shared",
                "--enable-static",
                "--enable-fat",
                "--with-pic",
                "--disable-cxx",
                "ABI=64",
            ],
            isa="x86_64 LP64; --enable-fat runtime dispatch among bundled x86 implementations",
            jit=False,
            archive=gmp_archive,
            host=host,
        ),
    )
    pcre2 = library_record(
        name="pcre2-8",
        version="10.47",
        source_url="https://github.com/PCRE2Project/pcre2/releases/download/pcre2-10.47/pcre2-10.47.tar.gz",
        source_archive="sources/pcre2-10.47.tar.gz",
        git_commit="f454e231fe5006dd7ff8f4693fd2b8eb94333429",
        headers=["pcre2/include/pcre2.h", "pcre2/include/pcre2posix.h"],
        archives=["pcre2/lib/libpcre2-8.a"],
        licenses=["licenses/pcre2/LICENCE.md"],
        build=build_record(
            compiler_name=compiler_name,
            compiler_path=compiler_path,
            compiler_version=compiler_version,
            archiver_version=archiver_version,
            configure=[
                "--disable-shared",
                "--enable-static",
                "--enable-pcre2-8",
                "--disable-pcre2-16",
                "--disable-pcre2-32",
                "--enable-jit",
            ],
            isa="x86_64 baseline C; PCRE2 JIT emits host code at runtime",
            jit=True,
            archive=pcre_archive,
            host=host,
        ),
    )
    document = {"format": 1, "target": "linux-x86_64-lp64", "libraries": [gmp, pcre2]}
    destination = ROOT / "manifest.json"
    temporary = destination.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(destination)
    print(f"wrote {destination.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
