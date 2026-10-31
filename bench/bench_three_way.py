#!/usr/bin/env python3
"""bench_three_way.py: measure C/raw/fat performance with the C reference as baseline.

Benchmarks are organized by source, focus set, and fast/full scale:

  bench/<SOURCE>/<name>/spec.json   Arguments, checks, tags, and scales
  bench/<SOURCE>/<name>/yian/       YIAN package; raw/ is an optional source overlay
  bench/<SOURCE>/<name>/c/          Authoritative CMake project
  bench/sets/<set>.json             Set members, default scale, and sample count
  bench/results/<set>.{md,csv}      Generated results with environment fingerprint and commit

Protocol:
  - CMake builds the C target with C11, clang -O2, and -ffp-contract=off; anx builds staged
    YIAN package roots with yianc -O2, adding --raw-pointers in raw mode.
  - fast substitutes declared scale.fast values in an isolated YIAN package copy. BG numeric tasks pass the
    selected full/fast dimension to C as one argv value; BG stdin tasks use the selected FASTA input file.
  - Artifacts use equal-length paths under build/bench/{cbin,yraw,yfat}/<SOURCE>_<name>.
  - CPU affinity via taskset -c <cpu> is optional, but required for formal records.
  - Warm up each mode once, then rotate C/raw/fat for N measured rounds. Report minimum time,
    median, interquartile range, CV, and peak RSS per mode.
  - Semantic guards: C warmup must pass its rc check. BG warmups and every measured sample must also match the
    C warmup's complete stdout bytes and expected rc. A failed sample remains in the row and disables its ratios.

Usage:
  python3 bench/bench_three_way.py --list-sets          # List sets, scales, and member counts
  python3 bench/bench_three_way.py --set full           # Default: full scale, formal records
  python3 bench/bench_three_way.py --set fast           # Fast development scale
  python3 bench/bench_three_way.py --set ptr --scale full
  python3 bench/bench_three_way.py --bench AWFY/queen,BG/fasta
  python3 bench/bench_three_way.py --source AWFY --set fast
  python3 bench/bench_three_way.py --set fast --yianc /path/to/yianc
  python3 bench/bench_three_way.py --set fast --anx /path/to/anx
  python3 bench/bench_three_way.py --compile-only | --no-compile | --runs N | --pin 4

Output:
  bench/results/<set>.{md,csv}   Named-set results with fingerprint/protocol/commit
  bench/results/partial.{md,csv} Temporary --bench/--source subsets, leaving set results intact

Standalone script: does not alter scripts/run_tests.py or sources; fast copies stay under build/.
"""

from __future__ import annotations

import argparse
import datetime
import json
import math
import os
import platform
import re
import subprocess
import statistics
import sys
import time
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from _toolchain.native import file_sha256, resolve_native_selection

try:
    from . import execution, projects
except ImportError:  # Direct script execution puts bench/ on sys.path.
    import execution
    import projects

ROOT = Path(__file__).resolve().parent.parent
BENCH_DIR = ROOT / "bench"
SETS_DIR = BENCH_DIR / "sets"
RESULTS_DIR = BENCH_DIR / "results"
OUT_DIR = ROOT / "build" / "bench"
PARTIAL_MD = RESULTS_DIR / "partial.md"
PARTIAL_CSV = RESULTS_DIR / "partial.csv"

DEFAULT_SET = "full"
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
class BenchSpec:
    """One three-mode benchmark: YIAN source, C reference, and argv/check/tag/scale specification."""

    name: str
    source: str
    project: projects.BenchmarkProject
    argv: tuple[str, ...]
    check: Check
    tags: tuple[str, ...]
    note: str
    scale: Scale | None = None
    io: IOSpec | None = None

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


@dataclass(frozen=True)
class SetDef:
    """Set: member patterns (exact key / SOURCE/* / tag:x / *) with default scale and sample count."""

    name: str
    scale: str
    runs: int
    include: tuple[str, ...]
    exclude: tuple[str, ...]
    note: str


@dataclass(frozen=True)
class Stats:
    med: float
    iqr: float
    cv: float
    min: float
    max: float
    samples: list[float]


@dataclass
class Row:
    spec: BenchSpec
    times: dict[str, Stats]
    rss_mb: dict[str, float]
    rss_samples: dict[str, list[int]]
    runs: int
    yian_stdout_match: bool
    c_check_ok: bool
    exit_codes: dict[str, int]


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
    )


def discover() -> list[BenchSpec]:
    """Discover paired projects under bench/<SOURCE>/<name>.

    Sources without c/, such as allocator-only bench/ALLOC, are handled by bench_allocator.py.
    """
    try:
        projects_found = projects.discover_projects(BENCH_DIR)
    except ValueError as error:
        raise SystemExit(f"[discover] {error}") from error
    specs = [
        load_spec(project.spec_path, project)
        for project in projects_found
        if project.c is not None
    ]
    if not specs:
        raise SystemExit(f"[discover] no benchmarks with a C reference and spec under {BENCH_DIR}")
    return specs


def load_sets() -> dict[str, SetDef]:
    """Read bench/sets/*.json."""
    if not SETS_DIR.is_dir():
        raise SystemExit(f"[sets] missing directory {SETS_DIR}")
    sets: dict[str, SetDef] = {}
    for path in sorted(SETS_DIR.glob("*.json")):
        data = _load_json(path, "sets")
        name = str(data.get("name", path.stem))
        scale = str(data.get("scale", "full"))
        if scale not in SCALE_PROFILES:
            raise SystemExit(f"[sets] {path}: scale must be one of {'/'.join(SCALE_PROFILES)}")
        sets[name] = SetDef(
            name=name,
            scale=scale,
            runs=int(data.get("runs", DEFAULT_RUNS)),
            include=_as_str_list(data.get("include", ["*"]), f"{path}: include"),
            exclude=_as_str_list(data.get("exclude"), f"{path}: exclude"),
            note=str(data.get("note", "")),
        )
    if DEFAULT_SET not in sets:
        raise SystemExit(f"[sets] missing default set {DEFAULT_SET}")
    return sets


def _match(pattern: str, spec: BenchSpec) -> bool:
    if pattern == "*":
        return True
    if pattern.startswith("tag:"):
        return pattern[4:] in spec.tags
    if pattern.endswith("/*"):
        return spec.source == pattern[:-2]
    return pattern == spec.key or pattern == spec.name


def resolve_set(
    specs: list[BenchSpec],
    set_def: SetDef,
    bench_filter: tuple[str, ...] = (),
    source_filter: tuple[str, ...] = (),
) -> list[BenchSpec]:
    """Expand set patterns into benchmarks; reject patterns matching nothing to catch misspellings."""
    for pattern in (*set_def.include, *set_def.exclude):
        if not any(_match(pattern, spec) for spec in specs):
            raise SystemExit(f"[select] set {set_def.name} member {pattern!r} matched no benchmark")
    chosen = [
        spec
        for spec in specs
        if any(_match(pattern, spec) for pattern in set_def.include)
        and not any(_match(pattern, spec) for pattern in set_def.exclude)
    ]
    if source_filter:
        chosen = [spec for spec in chosen if spec.source in source_filter]
    if bench_filter:
        wanted = set(bench_filter)
        unknown = sorted(item for item in wanted if not any(_match(item, spec) for spec in specs))
        if unknown:
            raise SystemExit(f"[select] unknown benchmark(s): {', '.join(unknown)}")
        chosen = [spec for spec in chosen if spec.key in wanted or spec.name in wanted]
    if not chosen:
        raise SystemExit("[select] selected no benchmarks (check --set/--scale/--source/--bench)")
    return sorted(chosen, key=lambda spec: spec.key)


def spec_bin(spec: BenchSpec, state: str) -> Path:
    return OUT_DIR / STATE_DIR[state] / f"{spec.source}_{spec.name}"


def compile_c(spec: BenchSpec) -> Path:
    """Build the project's plain CMake target; stop on failure."""
    binary = spec_bin(spec, "c")
    try:
        return projects.build_c(spec.project, asan=False, output=binary)
    except (ValueError, RuntimeError) as error:
        raise SystemExit(f"[compile] {spec.key} (c) failed: {error}") from error


def compile_an(
    spec: BenchSpec, profile: str, raw: bool, compiler: Path, *, anx: Path | None = None
) -> Path:
    """Build one isolated YIAN package mode through the shared project builder."""
    state = "raw" if raw else "fat"
    binary = spec_bin(spec, state)
    try:
        anx_path = anx
        if anx_path is None:
            anx_path, _ = _resolve_anx_selection(None)
        return projects.build_yian(
            spec.project, profile, raw, spec.scale_value(profile), compiler, anx_path, ROOT, binary
        )
    except (ValueError, RuntimeError) as error:
        raise SystemExit(f"[compile] {spec.key} ({state}) failed: {error}") from error


OPT_LEVEL = "-O2"
C_COMPILER = "clang"
C_FLAGS = ["-O2", "-ffp-contract=off"]
TIME_BIN = "/usr/bin/time"
DEFAULT_RUNS = 5
DEFAULT_MAX_STATE_SEC = 120.0
STATES = ("c", "raw", "fat")
STATE_DIR = {"c": "cbin", "raw": "yraw", "fat": "yfat"}

_RSS_RE = re.compile(r"Maximum resident set size \(kbytes\): (\d+)")


def _resolve_anx_selection(
    explicit: str | None, *, search_path: str | None = None
) -> tuple[Path, str]:
    selected, source = resolve_native_selection(
        explicit=explicit, configured=os.environ.get("ANX"), default="anx", search_path=search_path
    )
    return selected, "ANX" if source == "YIANC" else source


def _selected_report_identity(
    selections: list[tuple[BenchSpec, str]], input_root: Path
) -> dict[str, str]:
    """Serialize project, input, oracle, and fixed native dependency identities for reports."""
    native_root = BENCH_DIR / "native"
    manifest_path = native_root / "manifest.json"
    manifest: dict[str, object] = {}
    if manifest_path.is_file():
        manifest = _load_json(manifest_path, "native manifest")
    native_libraries = manifest.get("libraries", [])
    archive_index: dict[Path, dict[str, object]] = {}
    if isinstance(native_libraries, list):
        for library in native_libraries:
            if not isinstance(library, dict):
                continue
            archives = library.get("archives", [])
            if not isinstance(archives, list):
                continue
            for archive in archives:
                if isinstance(archive, dict) and isinstance(archive.get("path"), str):
                    archive_path = native_root / archive["path"]
                    archive_index[archive_path.resolve()] = {
                        "name": library.get("name"),
                        "version": library.get("version"),
                        "path": archive["path"],
                        "manifest_sha256": archive.get("sha256"),
                        "actual_sha256": file_sha256(archive_path) if archive_path.is_file() else "missing",
                        "instrumentation": library.get("instrumentation", {}),
                    }

    cases: list[dict[str, object]] = []
    selected_archives: dict[str, dict[str, object]] = {}
    fasta_generator: projects.BenchmarkProject | None = None
    for spec, profile in selections:
        project = spec.project
        case: dict[str, object] = {
            "project": spec.key,
            "profile": profile,
            "project_digest": "n/a",
            "dimension": None,
            "input": None,
            "oracles": [],
            "dependencies": [],
        }
        project_root = getattr(project, "root", None)
        if isinstance(project_root, Path) and project_root.is_dir():
            case["project_digest"] = projects.project_digest(project)
            if spec.source == "BG" and spec.scale is not None and spec.scale.c == "argv":
                argv = spec.run_argv(profile)
                case["dimension"] = int(argv[-1]) if argv else None
            elif spec.source == "BG" and spec.io is not None and spec.io.stdin is not None:
                n = spec.io.stdin.for_profile(profile)
                case["dimension"] = n
                input_path = input_root / f"fasta-n{n}.fasta"
                input_identity: dict[str, object] = {
                    "path": input_path.relative_to(ROOT).as_posix()
                    if input_path.is_relative_to(ROOT) else str(input_path),
                    "fasta_n": n,
                }
                if input_path.is_file():
                    input_identity["size_bytes"] = input_path.stat().st_size
                    input_identity["sha256"] = file_sha256(input_path)
                if spec.io is not None and spec.io.stdin is not None:
                    if fasta_generator is None:
                        generators = [
                            item for item in projects.discover_projects(BENCH_DIR)
                            if item.key == "BG/fasta"
                        ]
                        if len(generators) != 1:
                            raise ValueError(
                                f"{spec.key}: expected one registered BG/fasta input generator, found {len(generators)}"
                            )
                        fasta_generator = generators[0]
                    input_identity["generator_project"] = fasta_generator.key
                    input_identity["generator_project_digest"] = projects.project_digest(fasta_generator)
                    metadata_path = input_path.with_suffix(".json")
                    if metadata_path.is_file():
                        metadata = _load_json(metadata_path, "generated input metadata")
                        input_identity["generation_metadata_sha256"] = file_sha256(metadata_path)
                        input_identity["generator_project_digest_used"] = metadata.get(
                            "generator_project_digest", "unknown"
                        )
                        input_identity["generator_binary_sha256"] = metadata.get(
                            "generator_binary_sha256", "unknown"
                        )
                        input_identity["oracle_sha256"] = metadata.get("oracle_sha256", "unknown")
                case["input"] = input_identity

            provenance_path = project_root / "provenance.json"
            if provenance_path.is_file():
                provenance = _load_json(provenance_path, "project provenance")
                oracles: list[dict[str, object]] = []
                fixtures = provenance.get("fixtures", [])
                if isinstance(fixtures, list):
                    for fixture in fixtures:
                        if not isinstance(fixture, dict):
                            continue
                        selected = fixture.get("derived_output")
                        if isinstance(selected, dict):
                            selected_path = selected.get("path")
                            selected_sha = selected.get("sha256")
                        else:
                            selected_path = fixture.get("output_path")
                            selected_sha = fixture.get("output_sha256")
                        record: dict[str, object] = {
                            "path": selected_path,
                            "sha256": selected_sha,
                            "official_path": fixture.get("output_path"),
                            "official_sha256": fixture.get("output_sha256"),
                        }
                        if isinstance(selected_path, str) and isinstance(selected_sha, str):
                            oracle_path = project_root / selected_path
                            if oracle_path.is_file():
                                record["size_bytes"] = oracle_path.stat().st_size
                                record["actual_sha256"] = file_sha256(oracle_path)
                        oracles.append(record)
                case["oracles"] = oracles

                dependencies: list[dict[str, object]] = []
                declared = provenance.get("dependencies", [])
                if isinstance(declared, list):
                    for dependency in declared:
                        if not isinstance(dependency, dict):
                            continue
                        item = {
                            "name": dependency.get("name"),
                            "path": dependency.get("path"),
                            "sha256": dependency.get("sha256"),
                            "license_id": dependency.get("license_id"),
                        }
                        dependencies.append(item)
                        relative = dependency.get("path")
                        if isinstance(relative, str):
                            resolved = (project_root / relative).resolve()
                            native = archive_index.get(resolved)
                            if native is not None:
                                dependencies[-1]["native_archive"] = native
                                selected_archives[str(native["name"])] = native
                case["dependencies"] = dependencies
        cases.append(case)

    result = _summarize_case_identity(cases)
    result["native_manifest_sha256"] = file_sha256(manifest_path) if manifest_path.is_file() else "n/a"
    return result


def _summarize_case_identity(cases: list[dict[str, object]]) -> dict[str, str]:
    selected_archives: dict[str, dict[str, object]] = {}
    for case in cases:
        dependencies = case.get("dependencies", [])
        if not isinstance(dependencies, list):
            continue
        for dependency in dependencies:
            if not isinstance(dependency, dict):
                continue
            archive = dependency.get("native_archive")
            if isinstance(archive, dict) and isinstance(archive.get("name"), str):
                selected_archives[archive["name"]] = archive
    instrumentation = []
    for name, archive in sorted(selected_archives.items()):
        flags = archive.get("instrumentation", {})
        if isinstance(flags, dict):
            instrumentation.append(
                f"{name}: ASan={flags.get('asan', 'unknown')}, YIAN guards={flags.get('yian_guards', 'unknown')}"
            )
    return {
        "native_archives": json.dumps(list(selected_archives.values()), sort_keys=True, separators=(",", ":")),
        "external_instrumentation": "; ".join(instrumentation) if instrumentation else "no selected static library archive",
        "project_metadata": json.dumps(cases, sort_keys=True, separators=(",", ":")),
    }


def _env_plain() -> dict[str, str]:
    env = os.environ.copy()
    env["LANG"] = "C"
    return env


def _run(binary: Path, argv: list[str], pin: int | None) -> tuple[float, int, str, int]:
    """Run once and return (wall time ms,peak RSS KB,stdout,exit code)."""
    pre = ["taskset", "-c", str(pin)] if pin is not None else []
    t0 = time.monotonic()
    res = subprocess.run(
        pre + [TIME_BIN, "-v", str(binary), *argv],
        capture_output=True,
        text=True,
        env=_env_plain(),
    )
    wall_ms = (time.monotonic() - t0) * 1000.0
    match = _RSS_RE.search(res.stderr)
    if match is None:
        raise SystemExit(
            f"[run] {binary}: cannot parse Maximum resident set size from /usr/bin/time -v output:\n"
            f"{res.stderr}"
        )
    return wall_ms, int(match.group(1)), res.stdout, res.returncode


def _check_file_result(spec: BenchSpec, result: execution.RunResult) -> bool:
    """Check a file result without decoding BG's complete stdout."""
    if spec.source == "BG" and spec.io is not None:
        return result.rc == spec.check.rc
    try:
        output = result.stdout.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return False
    return spec.check.ok(result.rc, output)


def _stats(samples: list[float]) -> Stats:
    ordered = sorted(samples)
    try:
        qs = statistics.quantiles(ordered, n=4, method="exclusive")
        iqr = qs[2] - qs[0]
    except statistics.StatisticsError:
        iqr = ordered[-1] - ordered[0]
    mean = statistics.fmean(ordered)
    try:
        cv = statistics.stdev(ordered) / mean * 100.0 if mean > 0 else 0.0
    except statistics.StatisticsError:  # Single sample.
        cv = 0.0
    return Stats(
        med=statistics.median(ordered),
        iqr=iqr,
        cv=cv,
        min=ordered[0],
        max=ordered[-1],
        samples=list(samples),
    )


def measure_triple(
    spec: BenchSpec, runs: int, pin: int | None, max_state_sec: float, profile: str
) -> Row:
    """Warm up each mode, then rotate C/raw/fat for used_runs rounds.

    All modes share argv, including the optional workload scale. Source markers
    retain the default scale when the command line supplies no override.
    """
    argv = list(spec.run_argv(profile))
    bins = {state: spec_bin(spec, state) for state in STATES}

    if spec.source == "BG" and spec.io is not None:
        # Prepare or validate the FASTA input before the consumer timing window.
        stdin_path = execution.prepare_input(spec, profile, OUT_DIR / "inputs")
        output_root = OUT_DIR / "file-runs" / "three-way" / spec.source / spec.name / profile
        output_root.mkdir(parents=True, exist_ok=True)

        def run_file(state: str, kind: str) -> execution.RunResult:
            output = output_root / f"{kind}-{state}.stdout"
            return execution.run_file(bins[state], tuple(argv), stdin_path, output, pin)

        warm_start = time.monotonic()
        warm_files = {state: run_file(state, "warm") for state in STATES}
        warm_sec = time.monotonic() - warm_start
        c_reference = warm_files["c"]
        c_check_ok = _check_file_result(spec, c_reference)
        yian_stdout_match = all(
            _check_file_result(spec, warm_files[state])
            and execution.output_equal(warm_files[state], c_reference)
            for state in ("raw", "fat")
        )

        used_runs = runs
        if warm_sec > max_state_sec and runs > 3:
            used_runs = 3
            print(
                f"[{spec.key}] warmup {warm_sec:.1f}s > {max_state_sec:.0f}s, "
                f"measurement runs reduced to {used_runs}",
                file=sys.stderr,
            )

        samples: dict[str, list[float]] = {state: [] for state in STATES}
        rss: dict[str, list[int]] = {state: [] for state in STATES}
        for _ in range(used_runs):
            for state in STATES:
                result = run_file(state, "sample")
                samples[state].append(result.wall_ms)
                rss[state].append(result.rss_kb)
                sample_ok = _check_file_result(spec, result) and execution.output_equal(result, c_reference)
                if state == "c":
                    c_check_ok = c_check_ok and sample_ok
                else:
                    yian_stdout_match = yian_stdout_match and sample_ok

        return Row(
            spec=spec,
            times={state: _stats(samples[state]) for state in STATES},
            rss_mb={state: max(rss[state]) / 1024.0 for state in STATES},
            rss_samples=rss,
            runs=used_runs,
            yian_stdout_match=yian_stdout_match,
            c_check_ok=c_check_ok,
            exit_codes={state: warm_files[state].rc for state in STATES},
        )

    t0 = time.monotonic()
    warm = {state: _run(bins[state], argv, pin) for state in STATES}
    warm_sec = time.monotonic() - t0

    c_check_ok = spec.check.ok(warm["c"][3], warm["c"][2])
    if not c_check_ok:
        print(
            f"[{spec.key}] warning: C baseline failed the authoritative check "
            f"(exit={warm['c'][3]}, stdout={warm['c'][2].strip()!r}); C ratios for this benchmark are unusable",
            file=sys.stderr,
        )
    yian_stdout_match = (
        warm["raw"][2] == warm["fat"][2]
        and warm["raw"][3] == warm["fat"][3]
        and all(spec.check.ok(warm[state][3], warm[state][2]) for state in ("raw", "fat"))
    )
    if not yian_stdout_match:
        print(
            f"[{spec.key}] warning: the two YIAN states' warmup results disagree or failed spec.check "
            f"(raw exit={warm['raw'][3]}, fat exit={warm['fat'][3]})",
            file=sys.stderr,
        )
    for state in ("raw", "fat"):
        if warm[state][3] != spec.check.rc:
            print(
                f"[{spec.key}] warning: {state} warmup exit code {warm[state][3]} (expected {spec.check.rc})",
                file=sys.stderr,
            )

    used_runs = runs
    if warm_sec > max_state_sec and runs > 3:
        used_runs = 3
        print(
            f"[{spec.key}] warmup {warm_sec:.1f}s > {max_state_sec:.0f}s, "
            f"measurement runs reduced to {used_runs}",
            file=sys.stderr,
        )

    samples: dict[str, list[float]] = {state: [] for state in STATES}
    rss: dict[str, list[int]] = {state: [] for state in STATES}
    for _ in range(used_runs):
        for state in STATES:
            wall, peak, _, _ = _run(bins[state], argv, pin)
            samples[state].append(wall)
            rss[state].append(peak)

    return Row(
        spec=spec,
        times={state: _stats(samples[state]) for state in STATES},
        rss_mb={state: max(rss[state]) / 1024.0 for state in STATES},
        rss_samples=rss,
        runs=used_runs,
        yian_stdout_match=yian_stdout_match,
        c_check_ok=c_check_ok,
        exit_codes={state: warm[state][3] for state in STATES},
    )


def write_samples(rows: list[Row], path: Path) -> None:
    """Write per-run samples in the format the paper's data/ files use."""
    lines = [
        f"C / raw / fat raw samples, measured {datetime.date.today():%Y-%m-%d}.",
        "Each pair is wall_ms (peak_rss_kb); one line per benchmark and state.",
    ]
    name_counts: dict[str, int] = {}
    for row in rows:
        name_counts[row.spec.name] = name_counts.get(row.spec.name, 0) + 1
    for row in sorted(rows, key=lambda item: item.spec.name):
        label = row.spec.key if name_counts[row.spec.name] > 1 else row.spec.name
        for state in STATES:
            stats = row.times[state]
            pairs = ", ".join(
                f"{wall:.1f} ({peak})"
                for wall, peak in zip(stats.samples, row.rss_samples[state])
            )
            lines.append(
                f"- {label} / {state} ({len(stats.samples)} runs): {pairs}"
            )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _tool_version(tool: str) -> str:
    try:
        res = subprocess.run(
            [tool, "--version"], capture_output=True, text=True, timeout=10
        )
    except (OSError, subprocess.SubprocessError):
        return "n/a"
    if res.returncode != 0 or not res.stdout:
        return "n/a"
    return res.stdout.splitlines()[0].strip()


def _git_head() -> str:
    try:
        res = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            timeout=10,
            cwd=ROOT,
        )
    except (OSError, subprocess.SubprocessError):
        return "n/a"
    return res.stdout.strip() if res.returncode == 0 else "n/a"


def machine_fingerprint(
    pin: int | None,
    runs: int,
    bench_set: str,
    profile: str,
    compiler: Path,
    compiler_source: str,
    compiler_used: bool,
    *,
    anx: Path | None = None,
    anx_source: str = "n/a",
) -> dict[str, str]:
    if anx is None:
        anx, anx_source = _resolve_anx_selection(None)
    return {
        "set": bench_set,
        "scale": profile,
        "hostname": platform.node() or "unknown",
        "machine": platform.machine(),
        "cpu_count": str(os.cpu_count() or 0),
        "clang": _tool_version(C_COMPILER),
        "cflags": " ".join(C_FLAGS),
        "python": platform.python_version(),
        "yianc": str(compiler),
        "yianc_sha256": file_sha256(compiler),
        "yianc_source": compiler_source,
        "yianc_used": "yes" if compiler_used else "no (--no-compile; existing artifacts reused)",
        "anx": str(anx),
        "anx_sha256": file_sha256(anx),
        "anx_source": anx_source,
        "cmake": _tool_version("cmake"),
        "project_layout": "2",
        "project_digests": "n/a",
        "opt": OPT_LEVEL,
        "pin": str(pin) if pin is not None else "none",
        "runs": str(runs),
        "commit": _git_head(),
        "date": datetime.date.today().isoformat(),
    }


def _fmt_ms(value: float) -> str:
    return f"{value:.1f}"


def _fmt_mb(kb_mb: float) -> str:
    return f"{kb_mb:.1f}"


def _geomean(values: list[float]) -> float | None:
    if not values:
        return None
    return math.exp(sum(math.log(v) for v in values) / len(values))


def _ratio(row: Row, num: str, den: str) -> float:
    denom = row.times[den].min
    return row.times[num].min / denom if denom else float("nan")


def _ratio_usable(row: Row) -> bool:
    """Ratios require every recorded C/YIAN sample to satisfy the semantic guard."""
    return row.c_check_ok and row.yian_stdout_match


def render_md(rows: list[Row], fingerprint: dict[str, str]) -> str:
    lines: list[str] = []
    lines.append(
        f"# C / raw / fat three-state measurements - set `{fingerprint['set']}` (scale `{fingerprint['scale']}`)\n"
    )
    lines.append(
        "Generated by `bench/bench_three_way.py`; all three states use the same algorithm and scale, "
        "differing only in implementation and pointer representation (C is the clang `-O2` reference). "
        "Set members and scale profiles are in `bench/sets/<set>.json` and each benchmark's "
        "`spec.json`.\n"
    )
    lines.append("## Environment fingerprint\n")
    lines.append("| item | value |")
    lines.append("| --- | --- |")
    lines.append(f"| host | {fingerprint['hostname']} |")
    lines.append(f"| architecture / logical cores | {fingerprint['machine']} / {fingerprint['cpu_count']} |")
    lines.append(f"| C compiler | `{fingerprint['clang']}` (`{fingerprint['cflags']}`) |")
    lines.append(f"| Python | {fingerprint['python']} |")
    lines.append(
        f"| YIAN compiler | `{fingerprint['yianc']}` (SHA-256 `{fingerprint['yianc_sha256']}`, "
        f"source `{fingerprint['yianc_source']}`, used this run: {fingerprint['yianc_used']}) |"
    )
    lines.append(f"| YIAN opt level | `{fingerprint['opt']}` (same for both modes) |")
    lines.append(f"| pinning | `taskset -c {fingerprint['pin']}` |")
    lines.append(f"| runs per mode | {fingerprint['runs']} (plus 1 warmup) |")
    lines.append(f"| set / scale | `{fingerprint['set']}` / `{fingerprint['scale']}` |")
    lines.append(f"| commit | `{fingerprint['commit']}` |")
    lines.append(f"| date | {fingerprint['date']} |")
    for key in (
        "anx", "anx_sha256", "anx_source", "cmake", "project_layout",
        "project_digests", "native_manifest_sha256", "native_archives", "external_instrumentation",
    ):
        if key in fingerprint:
            lines.append(f"| {key} | `{fingerprint[key]}` |")
    lines.append("")
    if "project_metadata" in fingerprint:
        lines.append("## Project, input, and oracle identity\n")
        for case in json.loads(fingerprint["project_metadata"]):
            lines.append(
                f"- `{case['project']}`: `{json.dumps(case, sort_keys=True, separators=(',', ':'))}`"
            )
        lines.append("")

    lines.append("## Results\n")
    lines.append(
        "| benchmark | C min (ms) | raw min (ms) | fat min (ms) | raw/C | fat/C | fat/raw "
        "| C peak RSS (MB) | raw peak RSS (MB) | fat peak RSS (MB) | raw source | raw/fat stdout match | C check |"
    )
    lines.append("| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- | --- | --- |")
    for row in rows:
        ratio_rc = _ratio(row, "raw", "c")
        ratio_fc = _ratio(row, "fat", "c")
        ratio_fr = _ratio(row, "fat", "raw")
        raw_src = "`raw/src/main.an`" if row.spec.raw_override else "`yian/src/main.an`"
        c_flag = "yes" if row.c_check_ok else "**no**"
        if not _ratio_usable(row):
            ratio_rc = ratio_fc = ratio_fr = float("nan")
        lines.append(
            f"| {row.spec.key} | {_fmt_ms(row.times['c'].min)} | {_fmt_ms(row.times['raw'].min)} "
            f"| {_fmt_ms(row.times['fat'].min)} | {ratio_rc:.2f}× | {ratio_fc:.2f}× | {ratio_fr:.2f}× "
            f"| {_fmt_mb(row.rss_mb['c'])} | {_fmt_mb(row.rss_mb['raw'])} | {_fmt_mb(row.rss_mb['fat'])} "
            f"| {raw_src} | {'yes' if row.yian_stdout_match else '**no**'} | {c_flag} |"
        )
    usable = [row for row in rows if _ratio_usable(row)]
    geo_raw = _geomean([_ratio(row, "raw", "c") for row in usable])
    geo_fat = _geomean([_ratio(row, "fat", "c") for row in usable])
    if geo_raw is not None and geo_fat is not None:
        lines.append("")
        lines.append(
            f"Geometric mean ratio (C baseline, n={len(usable)}): raw/C **{geo_raw:.2f}×** / "
            f"fat/C **{geo_fat:.2f}×** (every warmup and measured sample passed its rc/output checks)."
        )
        totals = {state: sum(row.times[state].min for row in rows) / 1000.0 for state in STATES}
        lines.append(
            f"Single full pass (sum of per-benchmark minima): C **{totals['c']:.1f} s** / "
            f"raw **{totals['raw']:.1f} s** / fat **{totals['fat']:.1f} s**."
        )
    lines.append("")
    lines.append(
        "All ratios are ratios of minima; benchmarks are not additive. `--raw-pointers` disables all pointer "
        "safety checks and lock-slot/frame-lock emission, so raw/C is the raw implementation's overhead relative "
        "to C, and fat/raw is the total overhead of full fat pointers over raw pointers.\n"
    )

    lines.append("## Repeatability\n")
    lines.append(
        "| benchmark | C median | C min | C max | C CV% | raw median | raw min | raw max | raw CV% "
        "| fat median | fat min | fat max | fat CV% | runs |"
    )
    lines.append(
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |"
    )
    for row in rows:
        cells = " | ".join(
            f"{_fmt_ms(row.times[state].med)} | {_fmt_ms(row.times[state].min)} "
            f"| {_fmt_ms(row.times[state].max)} | {row.times[state].cv:.1f}"
            for state in STATES
        )
        lines.append(f"| {row.spec.key} | {cells} | {row.runs} |")
    lines.append("")
    lines.append(
        "Formal metrics use the **minimum**: when the machine is under load or hot, repeated runs of the same "
        "binary fall into two performance states about 20% apart (a process-level memory-layout/frequency artifact "
        "unrelated to the code). The minimum estimates interference-free performance, and the median and CV judge "
        "repeatability.\n"
    )

    lines.append("## Protocol\n")
    lines.append(
        f"- Compilation: CMake builds each project's `bench_app` target with `{C_COMPILER} {' '.join(C_FLAGS)}`; "
        f"anx builds each staged YIAN package with `yianc {OPT_LEVEL}`, adding `--raw-pointers` in raw mode."
    )
    lines.append(
        "- Scale and input: numeric BG tasks pass the selected full/fast dimension once to C and compile the "
        "same YIAN dimension into an isolated package copy. BG stdin tasks generate FASTA input from the selected, "
        "source-matched C fasta project before consumer timing; all legs reopen that same file for every run."
    )
    lines.append(
        "- Measurement: after one warmup per state per benchmark (excluded from samples), measure in "
        "**round-robin** order C->raw->fat; formal metrics use each state's minimum and record median/IQR/CV/peak RSS."
    )
    lines.append(
        f"- Noise reduction: `taskset -c {fingerprint['pin']}`; round-robin measurement reduces drift across time; "
        "all three artifacts use the same directory and equal-length paths (argv[0] length affects process layout "
        "for allocation-heavy benchmarks)."
    )
    lines.append(
        "- Output and semantic guard: BG stdout is written to binary files; size/hash and complete-byte checks "
        "run after timing. Every BG warmup and sample must pass the expected rc and match the same-scale C "
        "warmup. A failed run remains recorded and makes all ratios for that benchmark unavailable."
    )
    return "\n".join(lines).rstrip("\n") + "\n"


def write_csv(rows: list[Row], fingerprint: dict[str, str], path: Path) -> None:
    header = (
        f"# bench/results/{path.name} - C / raw / fat three-state performance results "
        "(generated by bench/bench_three_way.py)"
    )
    comments = [
        header,
        "# columns: bench is <SOURCE>/<name>; <state>_min_ms/<state>_med_ms/<state>_cv_pct are each state's "
        "minimum (formal metric)/median/coefficient of variation, state ∈ {c,raw,fat}; ratio_raw_c = raw_min/c_min, "
        "ratio_fat_c = fat_min/c_min, ratio_fat_raw = fat_min/raw_min; <state>_rss_mb is peak resident memory; "
        "runs, stdout_match (raw/fat), c_check (C authoritative value), c_argv, raw_source",
        f"# fingerprint: hostname={fingerprint['hostname']} machine={fingerprint['machine']} "
        f"cpu_count={fingerprint['cpu_count']} clang={fingerprint['clang']} cflags={fingerprint['cflags']}",
        f"# env: python={fingerprint['python']} yianc={fingerprint['yianc']} "
        f"yianc_sha256={fingerprint['yianc_sha256']} yianc_source={fingerprint['yianc_source']} "
        f"yianc_used={fingerprint['yianc_used']} opt={fingerprint['opt']} pin={fingerprint['pin']} "
        f"runs={fingerprint['runs']}",
        f"# anx: {fingerprint.get('anx', 'n/a')} sha256={fingerprint.get('anx_sha256', 'n/a')} "
        f"source={fingerprint.get('anx_source', 'n/a')} cmake={fingerprint.get('cmake', 'n/a')} "
        f"project_layout={fingerprint.get('project_layout', 'n/a')}",
        f"# set: {fingerprint['set']}  scale: {fingerprint['scale']}",
        f"# commit: {fingerprint['commit']}",
        f"# date: {fingerprint['date']}",
        f"# project_digests: {fingerprint.get('project_digests', 'n/a')}",
        f"# native_manifest_sha256: {fingerprint.get('native_manifest_sha256', 'n/a')}",
        f"# native_archives: {fingerprint.get('native_archives', '[]')}",
        f"# external_instrumentation: {fingerprint.get('external_instrumentation', 'n/a')}",
        f"# project_metadata: {fingerprint.get('project_metadata', '[]')}",
    ]
    lines = list(comments)
    lines.append(
        "bench,"
        + ",".join(f"{state}_min_ms,{state}_med_ms,{state}_cv_pct" for state in STATES)
        + ",ratio_raw_c,ratio_fat_c,ratio_fat_raw,"
        + ",".join(f"{state}_rss_mb" for state in STATES)
        + ",runs,stdout_match,c_check,c_argv,raw_source"
    )
    for row in rows:
        raw_src = "raw/src/main.an" if row.spec.raw_override else "yian/src/main.an"
        argv = " ".join(row.spec.run_argv(fingerprint["scale"]))
        ratios = (
            (f"{_ratio(row, 'raw', 'c'):.3f}", f"{_ratio(row, 'fat', 'c'):.3f}", f"{_ratio(row, 'fat', 'raw'):.3f}")
            if _ratio_usable(row)
            else ("nan", "nan", "nan")
        )
        fields = [row.spec.key]
        for state in STATES:
            stats = row.times[state]
            fields += [f"{stats.min:.1f}", f"{stats.med:.1f}", f"{stats.cv:.2f}"]
        fields += [
            *ratios,
        ]
        fields += [f"{row.rss_mb[state]:.1f}" for state in STATES]
        fields += [
            str(row.runs),
            "yes" if row.yian_stdout_match else "no",
            "pass" if row.c_check_ok else "fail",
            argv,
            raw_src,
        ]
        lines.append(",".join(fields))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="C / raw / fat three-state performance measurement (C as baseline)")
    parser.add_argument("--set", dest="bench_set", default=DEFAULT_SET,
                        help=f"set name (default {DEFAULT_SET}; see --list-sets)")
    parser.add_argument("--scale", choices=SCALE_PROFILES,
                        help="scale profile (default: the set's scale)")
    parser.add_argument("--source", help="only measure the named sources (comma-separated, e.g. AWFY,BG)")
    parser.add_argument("--bench", dest="bench_keys",
                        help="only measure the named benchmarks (SOURCE/name or name, comma-separated)")
    parser.add_argument("--names", dest="bench_keys", help=argparse.SUPPRESS)  # Compatibility option aliases.
    parser.add_argument("--list-sets", action="store_true", help="list sets and member counts, then exit")
    parser.add_argument("--runs", type=int, default=None,
                        help="measured runs per state (default: the set's runs)")
    parser.add_argument("--pin", type=int, help="CPU number for taskset pinning")
    parser.add_argument("--yianc", metavar="PATH", help="Native compiler (default: nonempty YIANC, then PATH)")
    parser.add_argument("--anx", metavar="PATH", help="Native project builder (default: nonempty ANX, then PATH)")
    parser.add_argument("--max-state-sec", type=float, default=DEFAULT_MAX_STATE_SEC,
                        help="reduce measured runs to 3 when the combined three-state warmup exceeds this many seconds (fast profile never reduces)")
    parser.add_argument("--no-compile", action="store_true", help="reuse existing binaries without recompiling")
    parser.add_argument("--compile-only", action="store_true", help="compile only, do not measure")
    args = parser.parse_args()

    specs = discover()
    sets = load_sets()

    if args.list_sets:
        for set_def in sorted(sets.values(), key=lambda item: item.name):
            members = resolve_set(specs, set_def)
            print(
                f"{set_def.name:12s} scale={set_def.scale:4s} runs={set_def.runs} "
                f"members={len(members):2d}  {set_def.note}"
            )
        return 0

    try:
        compiler, compiler_source = resolve_native_selection(
            explicit=args.yianc,
            configured=os.environ.get("YIANC"),
            default="yianc",
        )
        anx, anx_source = _resolve_anx_selection(args.anx)
    except ValueError as error:
        parser.error(str(error))

    if args.bench_set not in sets:
        raise SystemExit(
            f"[args] unknown set {args.bench_set!r}; available: {', '.join(sorted(sets))}"
        )
    set_def = sets[args.bench_set]
    profile = args.scale or set_def.scale
    bench_filter = tuple(item.strip() for item in (args.bench_keys or "").split(",") if item.strip())
    source_filter = tuple(item.strip() for item in (args.source or "").split(",") if item.strip())
    chosen = resolve_set(specs, set_def, bench_filter, source_filter)
    runs = args.runs if args.runs is not None else set_def.runs

    print(
        f"[bench] set {set_def.name} scale {profile} benchmarks {len(chosen)} "
        f"measuring {runs} runs (fast profile never reduces)",
        file=sys.stderr,
    )
    if not args.no_compile:
        for spec in chosen:
            compile_c(spec)
            compile_an(spec, profile, raw=False, compiler=compiler, anx=anx)
            compile_an(spec, profile, raw=True, compiler=compiler, anx=anx)
            scale_value = spec.scale_value(profile)
            scale_note = f" scale={scale_value}" if scale_value is not None else ""
            print(f"[compile] {spec.key} ok{scale_note}", file=sys.stderr)
    if args.compile_only:
        return 0

    fingerprint = machine_fingerprint(
        args.pin, runs, set_def.name, profile, compiler, compiler_source, not args.no_compile,
        anx=anx, anx_source=anx_source,
    )
    fingerprint["project_digests"] = ";".join(
        f"{spec.key}={projects.project_digest(spec.project)}" for spec in chosen
    )
    max_state_sec = float("inf") if profile == "fast" else args.max_state_sec
    rows: list[Row] = []
    for spec in chosen:
        row = measure_triple(spec, runs, args.pin, max_state_sec, profile)
        rows.append(row)
        scale_value = spec.scale_value(profile)
        scale_note = f" scale={scale_value}" if scale_value is not None else ""
        ratio_note = (
            f"raw/C {_ratio(row, 'raw', 'c'):.2f}x fat/C {_ratio(row, 'fat', 'c'):.2f}x"
            if _ratio_usable(row)
            else "ratios unavailable (semantic check failed)"
        )
        print(
            f"[measure] {spec.key}{scale_note}: C {row.times['c'].min:.1f}ms "
            f"raw {row.times['raw'].min:.1f}ms fat {row.times['fat'].min:.1f}ms "
            f"{ratio_note} "
            f"({row.runs} runs)",
            file=sys.stderr,
        )

    fingerprint.update(_selected_report_identity(
        [(spec, profile) for spec in chosen], OUT_DIR / "inputs"
    ))

    # Temporary --bench/--source subsets leave set results intact; named sets write results/<set>.{md,csv}.
    partial = bool(bench_filter or source_filter)
    if partial:
        md_path, csv_path = PARTIAL_MD, PARTIAL_CSV
    else:
        md_path = RESULTS_DIR / f"{set_def.name}.md"
        csv_path = RESULTS_DIR / f"{set_def.name}.csv"
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    md_path.write_text(render_md(rows, fingerprint), encoding="utf-8")
    write_csv(rows, fingerprint, csv_path)
    samples_path = RESULTS_DIR / "three-way-samples.txt"
    write_samples(rows, samples_path)
    print(f"[write] {md_path.relative_to(ROOT)}", file=sys.stderr)
    print(f"[write] {csv_path.relative_to(ROOT)}", file=sys.stderr)
    print(f"[write] {samples_path.relative_to(ROOT)}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
