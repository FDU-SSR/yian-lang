from __future__ import annotations

from pathlib import Path

TEMPLATE_MANIFEST = """\
[package]
name = "{name}"
kind = "{kind}"
version = "0.1.0"

[dependencies]
"""

TEMPLATE_MAIN = """\
from std.core.io import print;

fn main() {{
    print("Hello from {name}!\\n");
}}
"""

#: A project's own test: `anx test` compiles each file under tests/ as its own
#: program and compares the result with tests/output/<name>.an.ans (docs §8.5).
TEMPLATE_TEST = """\
// `anx test` runs every .an file under tests/ as its own program.
// Expected output lives in tests/output/<name>.an.ans.
from std.core.io import print;

fn main() {
    print("tests ok\\n");
}
"""

TEMPLATE_GITIGNORE = """\
# anx build artifacts
build/
"""

#: Accepted ``kind`` values for ``anx new --kind``.
KINDS = ("bin", "lib", "hybrid")


def scaffold(name: str, root: Path | None = None, *, kind: str = "bin") -> Path:
    if kind not in KINDS:
        raise ValueError(f"kind must be one of {', '.join(KINDS)}, got {kind!r}")
    if root is None:
        root = Path.cwd()
    pkg_name = Path(name).name
    pkg_dir = (root / name).resolve()
    if pkg_dir.exists():
        raise FileExistsError(f"Directory already exists: {pkg_dir}")

    src_dir = pkg_dir / "src"
    src_dir.mkdir(parents=True)

    (pkg_dir / "package.anx").write_text(
        TEMPLATE_MANIFEST.format(name=pkg_name, kind=kind)
    )
    if kind != "lib":
        (src_dir / "main.an").write_text(TEMPLATE_MAIN.format(name=pkg_name))

    tests_dir = pkg_dir / "tests"
    (tests_dir / "output").mkdir(parents=True)
    (tests_dir / "smoke.an").write_text(TEMPLATE_TEST)
    (tests_dir / "output" / "smoke.an.ans").write_text("tests ok\n")

    (pkg_dir / ".gitignore").write_text(TEMPLATE_GITIGNORE)

    return pkg_dir
