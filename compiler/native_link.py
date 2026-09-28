"""Compile declared C shims and resolve LLVM link flags without shell evaluation."""

from __future__ import annotations

import platform
import shlex
import shutil
import struct
import subprocess
import sys
from pathlib import Path

from compiler.analysis.package_map import PackageMap
from compiler.error import CompilerError


class NativeLink:
    def __init__(self, packages: PackageMap | None) -> None:
        self.__sources: list[Path] = []
        self.__components: list[str] = []
        self.__llvm_config: str | None = None
        if packages is None:
            return
        reachable: set[str] = set()
        pending = [packages.root] if packages.root is not None else list(packages.packages)
        while pending:
            name = pending.pop()
            if name in reachable or name not in packages.packages:
                continue
            reachable.add(name)
            pending.extend(packages.packages[name].dependencies)
        requested = False
        for name in reachable:
            package = packages.packages[name]
            native = package.native_llvm
            if native is None:
                continue
            requested = True
            for source in native.sources:
                if source not in self.__sources:
                    self.__sources.append(source)
            for component in native.components:
                if component not in self.__components:
                    self.__components.append(component)
        if not requested:
            return
        if sys.platform != "linux" or platform.machine() != "x86_64" or struct.calcsize("P") != 8:
            raise CompilerError("LLVM FFI requires Linux x86-64 LP64")
        config = shutil.which("llvm-config")
        if config is None:
            raise CompilerError("LLVM FFI requires llvm-config 22")
        self.__llvm_config = config
        version = self.__query("--version").strip()
        if version.split(".", 1)[0] != "22":
            raise CompilerError(f"LLVM FFI requires LLVM 22, found {version}")
        host = self.__query("--host-target").strip()
        if not host.startswith("x86_64-") or "linux" not in host:
            raise CompilerError(f"LLVM FFI requires an x86-64 Linux LLVM installation, found {host}")

    def __query(self, *args: str) -> str:
        assert self.__llvm_config is not None
        result = subprocess.run(
            [self.__llvm_config, *args], capture_output=True, text=True, check=False,
        )
        if result.returncode != 0:
            raise CompilerError(f"llvm-config {' '.join(args)} failed: {result.stderr.strip()}")
        return result.stdout

    def compile_shims(self, compiler: str, directory: Path) -> list[Path]:
        if not self.__sources:
            return []
        flags = shlex.split(self.__query("--cflags"))
        objects: list[Path] = []
        for index, source in enumerate(self.__sources):
            output = directory / f"native_{index}.o"
            command = [compiler, *flags, "-c", str(source), "-o", str(output)]
            result = subprocess.run(command, capture_output=True, text=True, check=False)
            if result.returncode != 0:
                raise CompilerError(f"native C shim failed: {source}\n{result.stderr}")
            objects.append(output)
        return objects

    def verify_linker(self, compiler: str) -> None:
        if self.__llvm_config is None:
            return
        result = subprocess.run(
            [compiler, "-dumpmachine"], capture_output=True, text=True, check=False,
        )
        target = result.stdout.strip()
        if result.returncode != 0 or not target.startswith("x86_64-") or "linux" not in target:
            raise CompilerError(f"LLVM FFI requires an x86-64 Linux C linker, found {target or compiler}")

    def llvm_flags(self) -> list[str]:
        if self.__llvm_config is None:
            return []
        flags = shlex.split(self.__query("--ldflags"))
        flags += shlex.split(self.__query("--libs", *self.__components))
        flags += shlex.split(self.__query("--system-libs"))
        return flags
