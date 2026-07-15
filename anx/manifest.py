from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Dependency:
    name: str
    path: Path


@dataclass
class Manifest:
    name: str
    version: str
    dependencies: dict[str, Dependency] = field(default_factory=dict[str, Dependency])

    @classmethod
    def from_file(cls, path: Path) -> Manifest:
        with open(path, "rb") as f:
            data = tomllib.load(f)

        pkg = data["package"]
        deps: dict[str, Dependency] = {}
        for dep_name, spec in data.get("dependencies", {}).items():
            dep_path = (path.parent / spec["path"]).resolve()
            deps[dep_name] = Dependency(name=dep_name, path=dep_path)
        return cls(
            name=pkg["name"],
            version=pkg.get("version", "0.1.0"),
            dependencies=deps,
        )
