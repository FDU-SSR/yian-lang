from __future__ import annotations

from pathlib import Path

from anx.manifest import Manifest


class CycleError(Exception):
    def __init__(self, cycle: list[str]) -> None:
        self.cycle = cycle
        super().__init__(f"Circular dependency: {' → '.join(cycle)}")


def _check_cycles(adj: dict[str, list[str]]) -> None:
    visited: set[str] = set()
    in_stack: list[str] = []

    def dfs(node: str) -> None:
        if node in in_stack:
            idx = in_stack.index(node)
            raise CycleError(in_stack[idx:] + [node])
        if node in visited:
            return
        visited.add(node)
        in_stack.append(node)
        for dep in adj.get(node, []):
            dfs(dep)
        in_stack.pop()

    for pkg in adj:
        dfs(pkg)


def resolve(manifest: Manifest, std_lib: Path, *, cwd: Path) -> tuple[list[Path], dict[str, Path]]:
    adj: dict[str, list[str]] = {}
    pkg_paths: dict[str, Path] = {}
    manifests: dict[str, Manifest] = {}

    def walk(name: str, path: Path) -> None:
        if name in adj:
            return
        m = Manifest.from_file(path / "package.anx")
        manifests[name] = m
        pkg_paths[name] = path
        deps = list(m.dependencies.keys())
        adj[name] = deps
        for dep_name, dep_spec in m.dependencies.items():
            walk(dep_name, dep_spec.path)

    walk(manifest.name, cwd)
    _check_cycles(adj)

    all_files: list[Path] = []
    pkg_roots: dict[str, str] = {}

    for name, path in pkg_paths.items():
        src = path / "src"
        if not src.is_dir():
            raise FileNotFoundError(f"Package '{name}': src/ directory not found at {src}")
        pkg_roots[name] = str(src.resolve())
        all_files.extend(sorted(src.rglob("*.an")))

    for f in sorted(std_lib.rglob("*.an")):
        all_files.append(f)
    pkg_roots["std"] = str(std_lib.resolve())

    return all_files, {k: Path(v) for k, v in pkg_roots.items()}
