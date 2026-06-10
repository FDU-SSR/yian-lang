"""
LLVM IR serialization and compilation.
"""

from __future__ import annotations

import importlib
import os

from compiler.codegen.llvm.module import LLModule


class Emitter:
    """Handles LLVM IR serialization and native code emission."""

    def __init__(self) -> None:
        self.__binding = None

    def __ensure_binding(self):
        if self.__binding is None:
            binding = importlib.import_module("llvmlite.binding")
            self.__binding = binding
        return self.__binding

    def emit_ll(self, llvm_module: LLModule, path: str) -> None:
        with open(path, "w", encoding="utf-8") as f:
            f.write(str(llvm_module))

    def emit_module(self, llvm_module: LLModule, output_dir: str, kind: str,
                    stem: str, intermediate_dir: str | None = None) -> str:
        normalized_kind = self._normalize_kind(kind)
        paths = {"ll": f"{stem}.ll", "bc": f"{stem}.bc", "obj": f"{stem}.o", "asm": f"{stem}.s"}
        ll_path = os.path.join(intermediate_dir or output_dir, paths["ll"])
        self.emit_ll(llvm_module, ll_path)
        if normalized_kind == "ll":
            return ll_path

        binding = self.__ensure_binding()
        llvm_mod = binding.parse_assembly(str(llvm_module))
        llvm_mod.verify()
        output_path = os.path.join(output_dir, paths[normalized_kind])

        if normalized_kind == "bc":
            with open(output_path, "wb") as f:
                f.write(llvm_mod.as_bitcode())
        elif normalized_kind in ("obj", "asm"):
            target_machine = binding.Target.from_triple(llvm_module.triple).create_target_machine(reloc="pic")  # type: ignore[union-attr]
            if normalized_kind == "obj":
                with open(output_path, "wb") as f:
                    f.write(target_machine.emit_object(llvm_mod))
            else:
                with open(output_path, "w", encoding="utf-8") as f:
                    f.write(target_machine.emit_assembly(llvm_mod))
        return output_path

    @staticmethod
    def _normalize_kind(kind: str) -> str:
        kind_map = {"ll": "ll", "ir": "ll", "llvm-ir": "ll", "bc": "bc", "bytecode": "bc",
                    "o": "obj", "obj": "obj", "object": "obj", "s": "asm", "asm": "asm", "assembly": "asm"}
        value = kind_map.get(kind.lower())
        if value is None:
            raise ValueError(f"Unsupported emit kind: {kind}")
        return value
