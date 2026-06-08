"""
LLVM IR serialization and compilation.
"""

from __future__ import annotations

import importlib
import os

from llvmlite import ir  # type: ignore[import-untyped]
from compiler.codegen.llvm.module import LLModule


_bind_ok: bool = False


def _bind():
    global _bind_ok
    if not _bind_ok:
        b = importlib.import_module("llvmlite.binding")
        b.initialize()
        b.initialize_native_target()
        b.initialize_native_asmprinter()
        b.initialize_native_asmparser()
        _bind_ok = True
    return importlib.import_module("llvmlite.binding")


def emit_ll(m: LLModule, path: str) -> None:
    with open(path, "w", encoding="utf-8") as f:
        f.write(str(m))


def emit_module(m: LLModule, output_dir: str, kind: str, stem: str, intermediate_dir: str | None = None) -> str:
    k = _normalize(kind)
    paths = {"ll": f"{stem}.ll", "bc": f"{stem}.bc", "obj": f"{stem}.o", "asm": f"{stem}.s"}
    ll_path = os.path.join(intermediate_dir or output_dir, paths["ll"])
    emit_ll(m, ll_path)
    if k == "ll":
        return ll_path

    b = _bind()
    mod = b.parse_assembly(str(m))
    mod.verify()
    out = os.path.join(output_dir, paths[k])

    if k == "bc":
        with open(out, "wb") as f:
            f.write(mod.as_bitcode())
    elif k in ("obj", "asm"):
        tm = b.Target.from_triple(m.triple).create_target_machine(reloc="pic")  # type: ignore[union-attr]
        if k == "obj":
            with open(out, "wb") as f:
                f.write(tm.emit_object(mod))
        else:
            with open(out, "w", encoding="utf-8") as f:
                f.write(tm.emit_assembly(mod))
    return out


def _normalize(kind: str) -> str:
    a = {"ll": "ll", "ir": "ll", "llvm-ir": "ll", "bc": "bc", "bytecode": "bc",
         "o": "obj", "obj": "obj", "object": "obj", "s": "asm", "asm": "asm", "assembly": "asm"}
    v = a.get(kind.lower())
    if v is None:
        raise ValueError(f"Unsupported emit kind: {kind}")
    return v
