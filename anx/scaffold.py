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

    return pkg_dir
