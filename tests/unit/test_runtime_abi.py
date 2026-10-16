"""Native/C runtime ABI protocol checks without importing the Python compiler."""

from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("yian_runtime_build", ROOT / "runtime/build.py")
assert SPEC is not None and SPEC.loader is not None
runtime_build = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(runtime_build)


class RuntimeAbiTests(unittest.TestCase):
    def test_native_abi_matches_the_c_header(self) -> None:
        runtime_build.check_consistency()

    def test_native_abi_rejects_a_header_bytes_mismatch(self) -> None:
        original = runtime_build.HEADER.read_text(encoding="utf-8")
        self.assertIn("#define YIAN_HDR_BYTES 8u", original)
        with tempfile.TemporaryDirectory(prefix="runtime ABI mismatch ") as temporary:
            header = Path(temporary) / "yian_rt.h"
            header.write_text(original.replace("#define YIAN_HDR_BYTES 8u", "#define YIAN_HDR_BYTES 9u", 1), encoding="utf-8")
            with self.assertRaisesRegex(SystemExit, "HEADER_BYTES disagrees with YIAN_HDR_BYTES"):
                runtime_build.check_consistency(header_path=header)


if __name__ == "__main__":
    unittest.main()
