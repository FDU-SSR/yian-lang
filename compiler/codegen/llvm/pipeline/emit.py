"""
LLVM IR serialization and compilation.
"""

from __future__ import annotations

import importlib
import os
import re
import tempfile

from compiler.codegen.llvm.base.module import LLModule

# Frame-exit SENTINEL stores (all-ones u64) must be volatile on optimized paths:
# LLVM considers reads of exited frames through dangling pointers undefined, allowing DSE to remove the store.
# A remaining entry key would make caller live checks pass, losing Exit -4 semantics.
_SENTINEL_STORE = re.compile(r"\bstore i64 18446744073709551615\b")


class Emitter:
    """Handles LLVM IR serialization and native code emission."""

    def __init__(self) -> None:
        self.__binding = None

    def __ensure_binding(self):
        if self.__binding is None:
            binding = importlib.import_module("llvmlite.binding")
            binding.initialize_native_target()
            binding.initialize_native_asmprinter()
            binding.initialize_native_asmparser()
            self.__binding = binding
        return self.__binding

    def emit_ll(self, llvm_module: LLModule, path: str) -> None:
        with open(path, "w", encoding="utf-8") as f:
            f.write(str(llvm_module))

    def emit_module(self, llvm_module: LLModule, output_dir: str, kind: str,
                    stem: str, intermediate_dir: str | None = None,
                    opt_level: int = 0) -> str:
        """Emit ``llvm_module`` to ``kind`` under ``output_dir``.

        *opt_level* (the ``-O`` value, 0-3) drives the -O mapping matrix:
        non-``ll`` targets run the LLVM IR pass pipeline at that level before
        emission (-O0 = no IR passes) and the backend target machine gets
        ``opt=opt_level``; ``-t ll`` always emits the unoptimized IR as-is.
        """
        normalized_kind = self.__normalize_kind(kind)
        paths = {"ll": f"{stem}.ll", "bc": f"{stem}.bc", "obj": f"{stem}.o", "asm": f"{stem}.s"}

        if normalized_kind == "ll":
            ll_path = os.path.join(intermediate_dir or output_dir, paths["ll"])
            self.emit_ll(llvm_module, ll_path)
            return ll_path

        # For non-ll targets the serialized IR is only needed as input to
        # llvmlite's parse_assembly.  Write it to a temp file so it is
        # automatically cleaned up (even when parsing fails).
        fd, ll_path = tempfile.mkstemp(suffix=".ll", prefix="yian_")
        os.close(fd)
        target_machine = None
        try:
            ir_text = str(llvm_module)
            binding = self.__ensure_binding()
            if opt_level > 0:
                # See _SENTINEL_STORE: optimized paths mark frame-exit stores volatile to prevent DSE deletion/reordering;
                # -t ll output retains the unmodified store representation.
                ir_text = _SENTINEL_STORE.sub("store volatile i64 18446744073709551615", ir_text)
            self.emit_ll(llvm_module, ll_path)
            llvm_mod = binding.parse_assembly(ir_text)
            llvm_mod.verify()
            if opt_level > 0 or normalized_kind in ("obj", "asm"):
                # Set the code model explicitly: llvmlite defaults to jitdefault, which selects large on 64-bit targets,
                # preventing +/-2 GiB PC-relative addressing. The backend materializes function and global addresses
                # with movabs and emits YIAN calls as call *%reg. The output is one executable/object file,
                # with code and static data well below 2 GiB. small matches clang's default:
                # direct calls and RIP-relative addressing. Images beyond +/-2 GiB cause linker relocation overflow,
                # rather than silently generating incorrect code.
                target_machine = binding.Target.from_triple(llvm_module.triple).create_target_machine(reloc="pic", codemodel="small", opt=opt_level)  # type: ignore[union-attr]
            if target_machine is not None and not llvm_mod.data_layout:
                # Emission ensures the triple; apply_target installs the matching layout. As a fallback, set data layout
                # when missing, since middle-end passes otherwise fold field offsets using an empty layout
                # while the backend uses the target layout, making read/write offsets inconsistent. See
                # docs/llvm_note.md for target triples and data layouts.
                llvm_mod.data_layout = str(target_machine.target_data)
            if opt_level > 0:
                # speed_level is the -O level used by LLVMPY_buildPerModuleDefaultPipeline
                # to construct the default per-module pipeline.
                pto = binding.PipelineTuningOptions(opt_level)  # type: ignore[attr-defined]
                builder = binding.create_pass_builder(target_machine, pto)  # type: ignore[attr-defined]
                builder.getModulePassManager().run(llvm_mod, builder)
                llvm_mod.verify()
        finally:
            if os.path.exists(ll_path):
                os.remove(ll_path)

        output_path = os.path.join(output_dir, paths[normalized_kind])

        if normalized_kind == "bc":
            with open(output_path, "wb") as f:
                f.write(llvm_mod.as_bitcode())
        elif normalized_kind in ("obj", "asm"):
            assert target_machine is not None
            if normalized_kind == "obj":
                with open(output_path, "wb") as f:
                    f.write(target_machine.emit_object(llvm_mod))
            else:
                with open(output_path, "w", encoding="utf-8") as f:
                    f.write(target_machine.emit_assembly(llvm_mod))

        return output_path

    @staticmethod
    def __normalize_kind(kind: str) -> str:
        kind_map = {"ll": "ll", "ir": "ll", "llvm-ir": "ll", "bc": "bc", "bytecode": "bc",
                    "o": "obj", "obj": "obj", "object": "obj", "s": "asm", "asm": "asm", "assembly": "asm"}
        value = kind_map.get(kind.lower())
        if value is None:
            raise ValueError(f"Unsupported emit kind: {kind}")
        return value
