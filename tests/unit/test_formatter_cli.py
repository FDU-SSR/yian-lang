"""Local process tests for the standalone yian-fmt command."""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from _toolchain.native import NativeToolError, resolve_native

SOURCE = "fn main(){let (a,b)=(1,2);}\n"
EXPECTED = "fn main() { let (a, b) = (1, 2); }\n"
UNICODE_SOURCE = "// 注释\r\nfn main(){}\r\n"
UNICODE_EXPECTED = "// 注释\nfn main() {}\n"


def _formatter_binary() -> Path:
    configured = os.environ.get("YIAN_FMT")
    if not configured:
        raise FileNotFoundError("YIAN_FMT must point at a built yian-fmt executable")
    try:
        return resolve_native(configured)
    except NativeToolError as error:
        raise FileNotFoundError(f"yian-fmt is unavailable: {error}") from error


class FormatterCliTests(unittest.TestCase):
    def __run(
        self,
        binary: Path,
        arguments: list[str],
        *,
        input_bytes: bytes | None = None,
        cwd: Path | None = None,
    ) -> tuple[int, str, str]:
        environment = os.environ.copy()
        environment.pop("YIAN_FMT", None)
        completed = subprocess.run(
            [str(binary), *arguments],
            input=input_bytes,
            cwd=cwd,
            env=environment,
            capture_output=True,
            check=False,
        )
        return (
            completed.returncode,
            completed.stdout.decode("utf-8"),
            completed.stderr.decode("utf-8"),
        )

    def test_modes_and_stdin(self) -> None:
        binary = _formatter_binary()
        with tempfile.TemporaryDirectory(prefix="yian-fmt modes ") as temporary:
            root = Path(temporary)
            source = root / "demo.an"
            source.write_text(SOURCE, encoding="utf-8")

            code, stdout, stderr = self.__run(binary, [str(source)])
            self.assertEqual((code, stderr), (0, ""))
            self.assertEqual(stdout, EXPECTED, "stdout mode prints the formatted source only")
            self.assertEqual(source.read_text(encoding="utf-8"), SOURCE, "stdout mode does not write")

            label = root / "from memory.an"
            label.write_text("fn main() {}\n", encoding="utf-8")
            code, stdout, stderr = self.__run(
                binary,
                ["--stdin", "--stdin-filepath", str(label)],
                input_bytes=SOURCE.encode("utf-8"),
            )
            self.assertEqual((code, stderr), (0, ""))
            self.assertEqual(stdout, EXPECTED, "stdin mode formats the in-memory text, not the disk file")
            self.assertEqual(label.read_text(encoding="utf-8"), "fn main() {}\n")

            code, stdout, stderr = self.__run(binary, ["--check", str(source)])
            self.assertEqual((code, stderr), (1, ""))
            self.assertEqual(stdout, f"{source}\n1 of 1 file(s) need formatting\n")
            self.assertEqual(source.read_text(encoding="utf-8"), SOURCE)

            code, stdout, stderr = self.__run(binary, ["--write", str(source)])
            self.assertEqual((code, stderr), (0, ""))
            self.assertEqual(stdout, "1 file(s) checked, 1 rewritten\n")
            self.assertEqual(source.read_text(encoding="utf-8"), EXPECTED)

            code, stdout, stderr = self.__run(binary, [str(source)])
            self.assertEqual((code, stderr), (0, ""))
            self.assertEqual(stdout, EXPECTED, "stdout mode prints already formatted sources too")

            code, stdout, stderr = self.__run(binary, ["--check", str(source)])
            self.assertEqual((code, stderr), (0, ""))
            self.assertEqual(stdout, "0 of 1 file(s) need formatting\n")

            code, stdout, stderr = self.__run(binary, ["--help"])
            self.assertEqual((code, stderr), (0, ""))
            self.assertIn("Usage: yian-fmt", stdout)
            code, stdout, stderr = self.__run(binary, ["--version"])
            self.assertEqual((code, stdout, stderr), (0, "yian-fmt 0.1.0\n", ""))

            code, stdout, stderr = self.__run(
                binary,
                ["--stdin", "--stdin-filepath", "unicode.an"],
                input_bytes=UNICODE_SOURCE.encode("utf-8"),
            )
            self.assertEqual((code, stderr), (0, ""))
            self.assertEqual(stdout, UNICODE_EXPECTED, "stdin keeps Unicode text and emits LF layout")

            dash = root / "-dash.an"
            dash.write_text(SOURCE, encoding="utf-8")
            code, stdout, stderr = self.__run(binary, ["--", "-dash.an"], cwd=root)
            self.assertEqual((code, stderr), (0, ""))
            self.assertEqual(stdout, EXPECTED, "-- separates option parsing from file names")

    def test_failures_and_partial_rewrite(self) -> None:
        binary = _formatter_binary()
        with tempfile.TemporaryDirectory(prefix="yian-fmt failures ") as temporary:
            root = Path(temporary)
            valid = root / "valid.an"
            other = root / "other.an"
            syntax = root / "syntax.an"
            readonly = root / "readonly.an"
            unreadable = root / "unreadable.an"
            for path in (valid, other, readonly, unreadable):
                path.write_text(SOURCE, encoding="utf-8")
            syntax.write_text("fn main() { let value = ; }\n", encoding="utf-8")
            readonly.chmod(0o444)
            unreadable.chmod(0o000)
            try:
                code, stdout, stderr = self.__run(binary, [str(syntax)])
                self.assertEqual((code, stdout), (3, ""))
                self.assertIn("skip", stderr)

                code, stdout, stderr = self.__run(
                    binary,
                    ["--stdin", "--stdin-filepath", "broken.an"],
                    input_bytes=b"fn main() { let value = ; }\n",
                )
                self.assertEqual((code, stdout), (3, ""))

                code, stdout, stderr = self.__run(
                    binary, ["--write", str(valid), str(syntax), str(readonly)]
                )
                self.assertEqual(code, 2, "IO failure outranks a syntax failure")
                self.assertEqual(stdout, "3 file(s) checked, 1 rewritten\n")
                self.assertIn("skip", stderr)
                self.assertIn("readonly.an", stderr)
                self.assertEqual(valid.read_text(encoding="utf-8"), EXPECTED)
                self.assertEqual(syntax.read_text(encoding="utf-8"), "fn main() { let value = ; }\n")
                self.assertEqual(readonly.read_text(encoding="utf-8"), SOURCE)

                valid.write_text(SOURCE, encoding="utf-8")
                code, stdout, stderr = self.__run(binary, ["--check", str(valid), str(syntax)])
                self.assertEqual(code, 3, "a syntax failure outranks a formatting difference")
                self.assertEqual(stdout, f"{valid}\n1 of 2 file(s) need formatting\n")
                self.assertIn("skip", stderr)
                self.assertEqual(valid.read_text(encoding="utf-8"), SOURCE)

                if os.geteuid() != 0:
                    code, stdout, stderr = self.__run(
                        binary, ["--check", str(unreadable), str(syntax)]
                    )
                    self.assertEqual(code, 2, "an unreadable input outranks a syntax failure")
                    self.assertIn("unreadable.an", stderr)

                for arguments in (
                    ["--write", "--check", str(valid)],
                    ["--stdin"],
                    ["--stdin", "--stdin-filepath", "label.an", str(valid)],
                    ["--stdin-filepath", "label.an", str(valid)],
                    [],
                    ["--unknown-option"],
                    [str(root / "missing")],
                    [str(valid), str(other)],
                ):
                    code, stdout, stderr = self.__run(binary, arguments)
                    self.assertEqual((code, stdout), (2, ""), f"{arguments!r} must be rejected")
                    self.assertNotEqual(stderr, "", f"{arguments!r} must report on stderr")
                self.assertEqual(valid.read_text(encoding="utf-8"), SOURCE)
                self.assertEqual(other.read_text(encoding="utf-8"), SOURCE)
            finally:
                readonly.chmod(0o644)
                unreadable.chmod(0o644)


if __name__ == "__main__":
    unittest.main()
