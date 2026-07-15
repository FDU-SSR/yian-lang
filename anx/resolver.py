from __future__ import annotations

from pathlib import Path

from anx.manifest import Manifest


def resolve(manifest: Manifest, std_lib: Path, *, cwd: Path) -> tuple[list[Path], dict[str, Path]]:
    all_files: list[Path] = []
    pkg_roots: dict[str, str] = {}
    visited: set[str] = set()

    def collect_pkg(name: str, root: Path, *, is_stdlib: bool = False) -> None:
        if name in visited:
            return
        visited.add(name)

        if is_stdlib:
            src = root
        else:
            src = root / "src"
            if not src.is_dir():
                raise FileNotFoundError(f"Package '{name}': src/ directory not found at {src}")

        pkg_roots[name] = str(src.resolve())
        all_files.extend(sorted(src.rglob("*.an")))

    collect_pkg(manifest.name, cwd)

    for dep in manifest.dependencies.values():
        dep_manifest = Manifest.from_file(dep.path / "package.anx")
        collect_pkg(dep.name, dep.path)
        for sub_dep in dep_manifest.dependencies.values():
            collect_pkg(sub_dep.name, sub_dep.path)

    collect_pkg("std", std_lib, is_stdlib=True)

    return all_files, {k: Path(v) for k, v in pkg_roots.items()}
