"""
LLVM IR serialization and compilation.
"""

from __future__ import annotations

import importlib
import os
import re
import tempfile

from compiler.codegen.llvm.module import LLModule

# 帧退出 SENTINEL 写(规则 3.7.2,all-ones u64)在优化路径上须为 volatile:
# LLVM 内存模型把"经悬垂指针读已退出帧"视为 UB,DSE 可证明该写为死存储而
# 删除,锁槽残留入口键 → 调用方 live 检查误通过,丢失 Exit -4 语义。
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
        normalized_kind = self._normalize_kind(kind)
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
        try:
            ir_text = str(llvm_module)
            binding = self.__ensure_binding()
            if opt_level > 0:
                # 见 _SENTINEL_STORE 注释:仅优化路径把帧退出 SENTINEL 写标
                # volatile(DSE 不可删除/重排),`-t ll` 输出保持逐字节不变。
                ir_text = _SENTINEL_STORE.sub("store volatile i64 18446744073709551615", ir_text)
            self.emit_ll(llvm_module, ll_path)
            llvm_mod = binding.parse_assembly(ir_text)
            llvm_mod.verify()
            if opt_level > 0:
                # C1: IR-level optimization pipeline.  llvmlite 0.44 shape
                # (verified by the C1 spike): PassManagerBuilder's opt_level
                # is a SETTER property, NOT a constructor kwarg.
                pm = binding.create_module_pass_manager()
                pmb = binding.PassManagerBuilder()
                pmb.opt_level = opt_level
                pmb.populate(pm)
                pm.run(llvm_mod)
                llvm_mod.verify()
        finally:
            if os.path.exists(ll_path):
                os.remove(ll_path)

        output_path = os.path.join(output_dir, paths[normalized_kind])

        if normalized_kind == "bc":
            with open(output_path, "wb") as f:
                f.write(llvm_mod.as_bitcode())
        elif normalized_kind in ("obj", "asm"):
            target_machine = binding.Target.from_triple(llvm_module.triple).create_target_machine(reloc="pic", opt=opt_level)  # type: ignore[union-attr]
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
