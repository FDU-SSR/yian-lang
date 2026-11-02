"""Typed parser for every bench/<SOURCE>/<name>/spec.json.

The three-state, ASan and allocator runners all consume BenchSpec from this
module, so name/source/scale/check/io and the fixed measurement policy have one
parser and one set of validation rules.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

try:
    from . import execution, projects
except ImportError:  # Direct script execution puts bench/ on sys.path.
    import execution
    import projects

DEFAULT_WARMUPS = 1
DEFAULT_RUNS = 5
SCALE_PROFILES = ("fast", "full")

FastaInput = execution.FastaInput
IOSpec = execution.IOSpec


@dataclass(frozen=True)
class Check:
    """Authoritative C result declared in each project's spec.json."""

    rc: int = 0
    stdout_eq: str | None = None
    stdout_contains: tuple[str, ...] = ()
    stdout_int_mod: tuple[int, int] | None = None

    def ok(self, rc: int, out: str) -> bool:
        if rc != self.rc:
            return False
        text = out.strip()
        if self.stdout_eq is not None and text != self.stdout_eq:
            return False
        for needle in self.stdout_contains:
            if needle not in out:
                return False
        if self.stdout_int_mod is not None:
            modulus, expected = self.stdout_int_mod
            try:
                value = int(text)
            except ValueError:
                return False
            if value % modulus != expected:
                return False
        return True

    def describe(self) -> str:
        parts = [f"rc=={self.rc}"]
        if self.stdout_eq is not None:
            parts.append(f"stdout=={self.stdout_eq!r}")
        for needle in self.stdout_contains:
            parts.append(f"stdout contains {needle!r}")
        if self.stdout_int_mod is not None:
            parts.append(f"stdout%{self.stdout_int_mod[0]}=={self.stdout_int_mod[1]}")
        return ", ".join(parts)


@dataclass(frozen=True)
class Scale:
    """Scale profile: default/fast source // bench-scale values and how C receives them."""

    default: int
    fast: int | None = None
    c: str = "none"  # 'argv' passes scale to the C reference; 'none' means C does not support scaling.


@dataclass(frozen=True)
class Measurement:
    warmups: int
    runs: int


@dataclass(frozen=True)
class BenchSpec:
    """One benchmark's parsed spec, shared by every performance runner."""

    name: str
    source: str
    project: projects.BenchmarkProject
    argv: tuple[str, ...]
    check: Check
    tags: tuple[str, ...]
    note: str
    scale: Scale | None = None
    io: IOSpec | None = None
    measurement: Measurement = Measurement(DEFAULT_WARMUPS, DEFAULT_RUNS)

    @property
    def key(self) -> str:
        return f"{self.source}/{self.name}"

    @property
    def raw_override(self) -> bool:
        return self.project.raw_overlay is not None

    def scale_value(self, profile: str) -> int | None:
        """Scale substituted in this profile's source copy; None uses the default (always None for full)."""
        if self.scale is None or profile not in SCALE_PROFILES or profile != "fast":
            return None
        return self.scale.fast

    def run_argv(self, profile: str) -> tuple[str, ...]:
        """Runtime arguments shared by all modes, including BG full/fast dimensions."""
        if profile not in SCALE_PROFILES:
            raise ValueError(f"profile must be one of {SCALE_PROFILES}, got {profile!r}")
        if self.source == "BG" and self.scale is not None and self.scale.c == "argv":
            value = self.scale.default
            if profile == "fast" and self.scale.fast is not None:
                value = self.scale.fast
            return (*self.argv, str(value))
        value = self.scale_value(profile)
        if value is not None and self.scale is not None and self.scale.c == "argv":
            return (*self.argv, str(value))
        return self.argv


def load_measurement(data: dict[str, object], where: str) -> Measurement:
    """Read the fixed warmup/run policy; absent means the shared default."""
    value = data.get("measurement")
    if value is None:
        return Measurement(DEFAULT_WARMUPS, DEFAULT_RUNS)
    if not isinstance(value, dict):
        raise SystemExit(f"[spec] {where}: measurement must be an object")
    unknown = set(value) - {"warmups", "runs"}
    if unknown:
        raise SystemExit(f"[spec] {where}: unsupported measurement field(s): {', '.join(sorted(unknown))}")
    numbers: dict[str, int] = {}
    for field in ("warmups", "runs"):
        raw = value.get(field)
        if not isinstance(raw, int) or isinstance(raw, bool) or raw < 1:
            raise SystemExit(f"[spec] {where}: measurement.{field} must be a positive integer")
        numbers[field] = raw
    return Measurement(numbers["warmups"], numbers["runs"])


def _load_json(path: Path, what: str) -> dict[str, object]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"[{what}] failed to read {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise SystemExit(f"[{what}] {path}: top level must be a JSON object")
    return data


def _as_str_list(value: object, where: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise SystemExit(f"[spec] {where}: expected a string array, got {value!r}")
    return tuple(value)


def _load_io(value: object, where: str) -> IOSpec:
    if not isinstance(value, dict):
        raise SystemExit(f"[spec] {where}: io must be an object")
    unknown = set(value) - {"stdin"}
    if unknown:
        raise SystemExit(f"[spec] {where}: unsupported io field(s): {', '.join(sorted(unknown))}")
    if "stdin" not in value:
        return IOSpec()
    stdin = value["stdin"]
    if not isinstance(stdin, dict) or set(stdin) != {"fasta_n"}:
        raise SystemExit(f"[spec] {where}: io.stdin must contain only fasta_n")
    fasta = stdin["fasta_n"]
    if not isinstance(fasta, dict) or set(fasta) != {"full", "fast"}:
        raise SystemExit(f"[spec] {where}: fasta_n must contain exactly full and fast")
    dimensions: dict[str, int] = {}
    for name in ("full", "fast"):
        number = fasta[name]
        if not isinstance(number, int) or isinstance(number, bool) or number <= 0:
            raise SystemExit(f"[spec] {where}: fasta_n.{name} must be a positive integer")
        dimensions[name] = number
    return IOSpec(stdin=FastaInput(**dimensions))


def load_spec(spec_path: Path, project: projects.BenchmarkProject) -> BenchSpec:
    data = _load_json(spec_path, "spec")
    if data.get("name") != project.name:
        raise SystemExit(f"[spec] {spec_path}: name should be {project.name!r}, got {data.get('name')!r}")
    if data.get("source") != project.source:
        raise SystemExit(f"[spec] {spec_path}: source should be {project.source!r}, got {data.get('source')!r}")
    check_raw = data.get("check")
    if not isinstance(check_raw, dict):
        raise SystemExit(f"[spec] {spec_path}: missing check object")
    int_mod_raw = check_raw.get("stdout_int_mod")
    int_mod: tuple[int, int] | None = None
    if int_mod_raw is not None:
        if not isinstance(int_mod_raw, list) or len(int_mod_raw) != 2 or any(
            not isinstance(item, int) for item in int_mod_raw
        ):
            raise SystemExit(f"[spec] {spec_path}: stdout_int_mod expects [modulus, expected remainder]")
        int_mod = (int(int_mod_raw[0]), int(int_mod_raw[1]))
    eq_raw = check_raw.get("stdout_eq")
    check = Check(
        rc=int(check_raw.get("rc", 0)),
        stdout_eq=str(eq_raw) if eq_raw is not None else None,
        stdout_contains=_as_str_list(check_raw.get("stdout_contains"), f"{spec_path}: stdout_contains"),
        stdout_int_mod=int_mod,
    )
    scale_raw = data.get("scale")
    scale: Scale | None = None
    if scale_raw is not None:
        if not isinstance(scale_raw, dict) or "default" not in scale_raw:
            raise SystemExit(f"[spec] {spec_path}: scale requires default (fast profile value optional)")
        default_raw = scale_raw["default"]
        fast_raw = scale_raw.get("fast")
        if not isinstance(default_raw, int) or isinstance(default_raw, bool) or default_raw <= 0:
            raise SystemExit(f"[spec] {spec_path}: scale.default must be a positive integer")
        if fast_raw is not None and (
            not isinstance(fast_raw, int) or isinstance(fast_raw, bool) or fast_raw <= 0
        ):
            raise SystemExit(f"[spec] {spec_path}: scale.fast must be a positive integer when present")
        scale = Scale(
            default=default_raw,
            fast=fast_raw,
            c=str(scale_raw.get("c", "none")),
        )
        if scale.c not in ("argv", "none"):
            raise SystemExit(f"[spec] {spec_path}: scale.c must be \"argv\" or \"none\"")
    io = _load_io(data["io"], f"{spec_path}: io") if "io" in data else None
    if project.source == "BG":
        if io is None:
            raise SystemExit(f"[spec] {spec_path}: BG cases must declare io (use {{}} for file stdout without stdin)")
        if check.rc != 0:
            raise SystemExit(f"[spec] {spec_path}: every aligned BG task must exit successfully (check.rc=0)")
        if check.stdout_eq is not None or check.stdout_contains or check.stdout_int_mod is not None:
            raise SystemExit(f"[spec] {spec_path}: BG file output uses rc checks only; byte checks use fixtures/references")
        if io.stdin is not None and (scale is not None or data.get("argv", [])):
            raise SystemExit(f"[spec] {spec_path}: BG stdin dimensions belong in io.stdin.fasta_n, not argv/scale")
        if io.stdin is None and scale is None:
            raise SystemExit(f"[spec] {spec_path}: BG numeric tasks require a scale dimension")
        if scale is not None and scale.c != "argv":
            raise SystemExit(f"[spec] {spec_path}: BG numeric dimensions require scale.c=\"argv\"")
        if scale is not None and data.get("argv", []) != []:
            raise SystemExit(f"[spec] {spec_path}: BG numeric dimensions use scale, so argv must be empty")
    return BenchSpec(
        name=project.name,
        source=project.source,
        project=project,
        argv=_as_str_list(data.get("argv"), f"{spec_path}: argv"),
        check=check,
        tags=_as_str_list(data.get("tags"), f"{spec_path}: tags"),
        note=str(data.get("note", "")),
        scale=scale,
        io=io,
        measurement=load_measurement(data, str(spec_path)),
    )
