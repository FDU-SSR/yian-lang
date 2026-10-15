#!/usr/bin/env python3
"""Compare compiler behavior; native anx supplies metadata for package fixtures."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import TypeAlias, cast

__SCRIPT_ROOT = Path(__file__).resolve().parents[1]
__ROOT = __SCRIPT_ROOT.parent
__PYTHON_ROOT = __ROOT / "python"
sys.path.insert(0, str(__SCRIPT_ROOT))
sys.path.insert(0, str(__PYTHON_ROOT))

from run_tests import discover_tests
from yianc.analysis.package_map import PackageMap
from yianc.error import CompilerError

Json: TypeAlias = None | bool | int | float | str | list["Json"] | dict[str, "Json"]
FIXTURE_DEPENDENCIES = {
    "tests/package/ffi_llvm": ("bootstrap/llvm", "bootstrap/ffi"),
}


@dataclass(frozen=True)
class FixtureMetadata:
    package_map: dict[str, Json]
    source_files: tuple[str, ...]
    capture_argv: tuple[str, ...]


@dataclass(frozen=True)
class Case:
    name: str
    group: str
    sources: tuple[str, ...]
    arguments: tuple[str, ...]
    standard_library: bool = True
    json_output: bool = False
    run: bool = False
    root_option: bool = True
    package: bool = False
    observability: str = ""
    expected_error: str = ""
    expected_error_count: int | None = None
    fixture: str = ""
    environment: tuple[tuple[str, str], ...] = ()
    newlines: str = ""
    separator: bool = False
    executable: str = "result"
    interleave: bool = False
    source_prefix: str = ""
    artifact_patterns: tuple[str, ...] = ("result*",)
    preexisting: tuple[tuple[str, str], ...] = ()
    target_modes: tuple[bool, ...] = (False, True)
    package_build: bool = False


class __Arguments(argparse.Namespace):
    reference: str
    bootstrap: str
    anx: str | None
    build_mode: str
    groups: str
    filter: str
    report: Path
    full_corpus: bool
    full_analysis: bool
    failed_from: Path | None


def __cases() -> list[Case]:
    cases = [
        Case("untyped-integer", "analyze", ("tests/basic/control_flow/comptime_untyped_integer.an",), ("--analyze", "--json"), json_output=True),
        Case("untyped-type-argument", "analyze", ("tests/basic/type/comptime_untyped_argument.an",), ("--analyze", "--json"), json_output=True),
        Case("integer-conversion", "analyze", ("tests/basic/control_flow/comptime_integer_conversion.an",), ("--analyze", "--json"), json_output=True),
        Case("float-conversion", "analyze", ("tests/basic/control_flow/comptime_float_conversion.an",), ("--analyze", "--json"), json_output=True),
        Case("nonfinite-cast", "analyze", ("tests/basic/diagnostics/comptime_nonfinite_cast.err.an",), ("--analyze", "--json"), json_output=True),
        Case("bool-float-cast", "analyze", ("tests/basic/diagnostics/comptime_bool_float_cast.err.an",), ("--analyze", "--json"), json_output=True),
        Case("bool-integer-constant-error", "analyze", ("tests/basic/diagnostics/comptime_bool_integer_cast.err.an",), ("--analyze", "--json"), json_output=True),
        Case("bool-integer-casts", "analyze", ("tests/compiler_parity/bool_conversion.an",), ("--analyze", "--json", "--allow-ffi"), json_output=True, expected_error="cannot convert bool to an integer", expected_error_count=12),
        Case("division-by-zero", "analyze", ("tests/basic/error/comptime_if_div_zero.err.an",), ("--analyze", "--json"), json_output=True),
        Case("negative-shift", "analyze", ("tests/basic/error/comptime_if_negative_shift.err.an",), ("--analyze", "--json"), json_output=True),
        Case("multiple-errors", "analyze", ("tests/compiler_parity/multiple_errors.an",), ("--analyze", "--json"), json_output=True),
        Case("stage-error-order", "analyze", ("tests/compiler_parity/parse_before_lex.an", "tests/compiler_parity/later_lex_error.an"), ("--analyze", "--json"), json_output=True),
        Case("syntax-error", "analyze", ("tests/compiler_parity/parse_before_lex.an",), ("--analyze", "--json"), standard_library=False, json_output=True),
        Case("entry-errors", "analyze", ("tests/compiler_parity/entry_error_order.an",), ("--analyze", "--json"), json_output=True),
        Case("strict-entry-first", "cli", ("tests/compiler_parity/entry_error_order.an",), ("-t", "none"), expected_error="Unknown identifier 'missing_called'"),
        Case("unicode-range", "analyze", ("tests/compiler_parity/unicode_error.an",), ("--analyze", "--json"), json_output=True),
        Case("pattern-warnings", "analyze", ("tests/compiler_parity/irrefutable_else.an",), ("--analyze", "--json"), json_output=True),
        Case("existing-warning", "analyze", ("tests/basic/match/unreachable.an",), ("--analyze", "--json"), json_output=True),
        Case("pattern-condition", "analyze", ("tests/basic/pattern/positions.an",), ("--analyze", "--json"), json_output=True),
        Case("nested-witness", "analyze", ("tests/basic/diagnostics/non_exhaustive_nested.err.an",), ("--analyze", "--json"), json_output=True),
        Case("character-witness", "analyze", ("tests/basic/diagnostics/non_exhaustive_char.err.an",), ("--analyze", "--json"), json_output=True),
        Case("multiple-main", "analyze", ("tests/basic/array/access.an", "tests/basic/literal/float.an"), ("--analyze", "--json"), json_output=True),
        Case("default-roots", "cli", ("tests/basic/array/access.an",), ("--analyze", "--json"), json_output=True, root_option=False),
        Case("analysis-observation-flags", "cli", ("tests/basic/array/access.an",), ("--analyze", "--json", "--profile", "--dump"), json_output=True),
        Case("missing-package-entry", "cli", ("tests/basic/array/access.an",), ("--analyze", "--json"), json_output=True, package=True),
        Case("mode-precedence", "cli", ("tests/basic/array/access.an",), ("--frontend-json", "tokens", "--analyze"), standard_library=False, json_output=True),
        Case("json-mode-precedence", "cli", ("tests/basic/array/access.an",), ("--frontend-json", "tokens", "--json"), standard_library=False, json_output=True),
        Case("profile", "observability", ("tests/basic/array/access.an",), ("-t", "none", "--profile"), observability="profile"),
        Case("log-file", "observability", ("tests/basic/array/access.an",), ("-t", "none", "--log-spec", "main=INFO", "--log-file", "requested.log"), observability="log"),
        Case("log-off", "observability", ("tests/basic/array/access.an",), ("-t", "none", "--log-spec", "all=OFF", "--log-file", "requested.log"), observability="off"),
        Case("log-empty-environment", "observability", ("tests/basic/array/access.an",), ("-t", "none", "--log-spec", "", "--log-file", "requested.log"), observability="log", environment=(("YIAN_LOG", "main=INFO"),)),
        Case("log-invalid-spec", "cli", ("tests/basic/array/access.an",), ("-t", "none", "--log-spec", "OFF")),
        Case("log-empty-path", "cli", ("tests/basic/array/access.an",), ("-t", "none", "--log-file", "")),
        Case("log-file-environment", "observability", ("tests/basic/array/access.an",), ("-t", "none"), observability="log", environment=(("YIAN_LOG", "main=INFO"), ("YIAN_LOG_FILE", "requested.log"))),
        Case("dump-none", "observability", ("tests/basic/array/access.an",), ("-t", "none", "--dump"), observability="dump"),
        Case("dump-parse-failure", "observability", ("tests/compiler_parity/parse_before_lex.an",), ("-t", "none", "--dump"), observability="dump"),
        Case("empty-root-environment", "cli", ("tests/basic/array/access.an",), ("--analyze", "--json"), json_output=True, root_option=False, environment=(("YIAN_LIB", ""), ("YIAN_ROOT", ""))),
        Case("library-environment", "cli", ("tests/basic/array/access.an",), ("--analyze", "--json"), json_output=True, root_option=False, environment=(("YIAN_LIB", str(__ROOT / "lib/src")), ("YIAN_ROOT", "/nonexistent"))),
        Case("no-input", "cli", (), ("--analyze", "--json"), standard_library=False),
        Case("invalid-json-mode", "cli", ("tests/basic/array/access.an",), ("--json",)),
        Case("invalid-option", "cli", (), ("--unknown-option",), standard_library=False),
        Case("invalid-optimization", "cli", (), ("-O4",), standard_library=False),
        Case("invalid-package-map", "cli", ("tests/basic/array/access.an",), ("--analyze", "--json", "--packages", "packages.json"), preexisting=(("packages.json", '{"format": 1}'),)),
        Case("blocked-output-directory", "native", ("tests/basic/array/access.an",), ("-o", "result/app"), preexisting=(("result", "preserved output"),)),
        Case("invalid-library", "cli", ("tests/basic/array/access.an",), ("-t", "none", "--link-lib", "bad/name")),
        Case("empty-library", "cli", ("tests/basic/array/access.an",), ("-t", "none", "--link-lib", "")),
        Case("unicode-library", "cli", ("tests/basic/array/access.an",), ("-t", "none", "--link-lib", "中²")),
        Case("nonalnum-library", "cli", ("tests/basic/array/access.an",), ("-t", "none", "--link-lib", "bad😀")),
        Case("input-separator", "cli", ("tests/basic/array/access.an",), ("--analyze", "--json"), json_output=True, separator=True),
        Case("interleaved-options", "cli", ("tests/basic/array/access.an",), ("--analyze", "--json"), json_output=True, interleave=True),
        Case("unicode-source-path", "analyze", ("tests/compiler_parity/unicode_error.an",), ("--analyze", "--json"), json_output=True, source_prefix="source space 中文"),
        Case("attached-options", "native", ("tests/basic/array/access.an",), ("-texe", "-O2", "-oresult"), run=True),
        Case("output-space", "native", ("tests/basic/array/access.an",), ("-O2", "-o", "result with space"), run=True, executable="result with space"),
        Case("output-extension", "native", ("tests/basic/array/access.an",), ("-t", "ll", "-o", "result.old")),
    ]
    for phase in ("tokens", "ast", "desugared"):
        cases.append(Case("crlf-" + phase, "frontend", ("tests/basic/array/access.an",), ("--frontend-json", phase), standard_library=False, json_output=True, newlines="\r\n"))
        source = "tests/compiler_parity/later_lex_error.an" if phase == "tokens" else "tests/compiler_parity/parse_before_lex.an"
        cases.append(Case("error-" + phase, "frontend", (source,), ("--frontend-json", phase), standard_library=False, json_output=True))
    for name, flags in (
        ("analyze", ("--analyze", "--json")), ("frontend", ("--frontend-json", "tokens")),
        ("format", ("--format",)), ("compile", ("-t", "none")),
    ):
        cases.append(Case("missing-input-" + name, "cli", (), (*flags, "missing.an"), standard_library=False))
        cases.append(Case("empty-input-" + name, "cli", (), (*flags, "empty"), standard_library=False, json_output=name in ("analyze", "frontend"), preexisting=(("empty/.keep", ""),)))
    for flags, name in ((("--check",), "check"), (("--write",), "write"), ((), "stdout")):
        cases.append(Case("crlf-format-" + name, "format", ("tests/basic/array/access.an",), ("--format", *flags), standard_library=False, newlines="\r\n"))
    for phase in ("tokens", "ast", "desugared"):
        cases.append(Case(phase, "frontend", ("tests/basic/literal/float.an", "tests/basic/fmt/fstring_basic.an", "tests/basic/try/generic.an"), ("--frontend-json", phase), standard_library=False, json_output=True))
    for name, flags in (("format-stdout", ()), ("format-check", ("--check",)), ("format-write", ("--write",))):
        cases.append(Case(name, "format", ("tests/compiler_parity/format.an",), ("--format", *flags), standard_library=False))
    for fixture in sorted((__PYTHON_ROOT / "yianc/format/fixtures").rglob("*.an")):
        if not fixture.name.endswith(".expected.an"):
            cases.append(Case("fixture-" + fixture.relative_to(__PYTHON_ROOT / "yianc/format/fixtures").as_posix(), "format", (fixture.relative_to(__ROOT).as_posix(),), ("--format",), standard_library=False))
    for name in ("mixed", "only"):
        for flag in ("--write", "--check"):
            cases.append(Case(f"comments-{name}-{flag[2:]}", "format", (f"python/yianc/format/fixtures/comments/{name}.an",), ("--format", flag), standard_library=False))
    for target in ("none", "exe", "ll", "bc", "obj", "asm"):
        cases.append(Case("no-main-" + target, "cli", ("tests/compiler_parity/no_main.an",), ("-t", target, "-o", "result")))
        cases.append(Case("output-" + target, "native", ("tests/basic/type/sizeof_full.an",), ("-t", target, "-o", "result"), run=target == "exe"))
        cases.append(Case("default-output-" + target, "native", ("tests/basic/array/access.an",), ("-t", target), run=target == "exe", executable="build/a.out", artifact_patterns=("build/a.out", "build/*.o", "build/*.ll", "build/*.bc", "*.s")))
    cases.append(Case("repeated-target", "native", ("tests/basic/array/access.an",), ("-t", "ll", "--target", "exe", "-o", "result"), run=True))
    cases.append(Case("missing-native-tool", "native", ("tests/basic/array/access.an",), ("-o", "result"), environment=(("YIAN_CC", "/missing/yian-compiler-cc"),)))
    cases.append(Case("empty-native-tool", "native", ("tests/basic/array/access.an",), ("-o", "result"), run=True, environment=(("YIAN_CC", ""),)))
    cases.append(Case("failed-native-tool", "native", ("tests/basic/array/access.an",), ("-o", "result"), environment=(("YIAN_CC", "/bin/false"),)))
    cases.append(Case("untyped-run", "native", ("tests/basic/control_flow/comptime_untyped_integer.an",), ("-O3", "-o", "result"), run=True))
    cases.append(Case("untyped-type-run", "native", ("tests/basic/type/comptime_untyped_argument.an",), ("-O3", "-o", "result"), run=True))
    cases.append(Case("integer-conversion-run", "native", ("tests/basic/control_flow/comptime_integer_conversion.an",), ("-O3", "-o", "result"), run=True))
    cases.append(Case("float-conversion-run", "native", ("tests/basic/control_flow/comptime_float_conversion.an",), ("-O3", "-o", "result"), run=True))
    cases.append(Case("untyped-runtime-comparison", "native", ("tests/basic/control_flow/assert.an",), ("-O3", "-o", "result"), run=True))
    cases.append(Case("boolean-conversion", "native", ("tests/compiler_parity/bool_conversion.an",), ("--allow-ffi", "-O0", "-o", "result"), expected_error="cannot convert bool to an integer"))
    cases.append(Case("boolean-integer-conversion", "native", ("tests/basic/type/bool_integer_cast.err.an",), ("-O0", "-o", "result"), expected_error="cannot convert bool to an integer"))
    cases.append(Case("llvm-consumer", "native", ("tests/package/ffi_llvm/src/main.an",), ("-O2", "-o", "result"), run=True, fixture="tests/package/ffi_llvm"))
    # Dangling and out-of-bounds programs have defined failure behavior only in fat mode.
    for source in (
        "fat_stack_dangle.an", "pool_reuse_uaf.an", "fat_oob_read_one_past.an",
        "pointer/frame_lock_shadow_stack_nested.an", "pointer/fat_one_past_end.an", "pointer/del_slice_whole.an",
    ):
        for optimization in (0, 3):
            cases.append(Case(f"safety-{source}-O{optimization}", "native", ("tests/safety/" + source,), (f"-O{optimization}", "-o", "result"), run=True, target_modes=(False,)))
    return cases


def __normalize(value: Json, directory: Path, stdlib_root: Path) -> Json:
    if isinstance(value, str):
        return value.replace(str(stdlib_root), "<stdlib>").replace(str(directory), "<work>")
    if isinstance(value, list):
        return [__normalize(item, directory, stdlib_root) for item in value]
    if isinstance(value, dict):
        return {key: __normalize(item, directory, stdlib_root) for key, item in value.items()}
    return value


def __capture_native_package_metadata(
    anx: list[str],
    fixture: str,
    directory: Path,
    root_source_root: Path,
    root_inputs: Mapping[Path, Path],
    stdlib_root: Path,
) -> FixtureMetadata:
    """Capture the v2 map and compiler inputs for the one LLVM package fixture.

    The native anx check runs against isolated copies with a capture-only
    compiler. The selected fixture source is then relocated into the parity
    work directory; dependency and shim paths stay inside the isolated copy.
    """
    dependencies = FIXTURE_DEPENDENCIES.get(fixture)
    if dependencies is None:
        raise RuntimeError(f"no isolated native anx fixture recipe for {fixture}")

    metadata_root = directory / "native-anx-metadata"
    project_root = metadata_root / fixture
    shutil.copytree(
        __ROOT / fixture,
        project_root,
        ignore=shutil.ignore_patterns("build", "__pycache__", ".pytest_cache"),
    )
    for dependency in dependencies:
        shutil.copytree(
            __ROOT / dependency,
            metadata_root / dependency,
            ignore=shutil.ignore_patterns("build", "__pycache__", ".pytest_cache"),
        )

    capture_path = directory / "native-anx-capture.json"
    capture_launcher = directory / "native-anx-capture"
    capture_launcher.write_text(
        f"#!{sys.executable}\n"
        "import json, os, sys\n"
        "with open(os.environ['ANX_CAPTURE_RECORD'], 'w', encoding='utf-8') as stream:\n"
        "    json.dump({'argv': sys.argv[1:], 'cwd': os.getcwd()}, stream)\n",
        encoding="utf-8",
    )
    capture_launcher.chmod(0o755)

    environment = os.environ.copy()
    environment["YIAN_ROOT"] = str(__ROOT)
    environment["YIAN_LIB"] = str(stdlib_root.resolve())
    environment["YIANC"] = str(capture_launcher)
    environment["ANX_CAPTURE_RECORD"] = str(capture_path)
    process = subprocess.run(
        [*anx, "check", str(project_root)],
        cwd=directory,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    if process.returncode != 0:
        raise RuntimeError(f"native anx metadata check failed: {process.stdout}\n{process.stderr}")

    map_path = project_root / "build" / "pkg.json"
    if not map_path.is_file() or not capture_path.is_file():
        raise RuntimeError("native anx metadata check did not produce a package map and capture record")
    package_map = cast(dict[str, Json], json.loads(map_path.read_text(encoding="utf-8")))
    captured = cast(dict[str, Json], json.loads(capture_path.read_text(encoding="utf-8")))
    capture_argv = cast(list[str], captured.get("argv", []))
    if capture_argv[:4] != ["--packages", str(map_path), "-t", "none"]:
        raise RuntimeError("native anx did not invoke the capture compiler with a metadata-only check")

    compiler_inputs = [value for value in capture_argv[4:] if value.endswith(".an")]
    metadata_source_root = (project_root / "src").resolve()
    root_package = cast(str, package_map.get("root", ""))
    package_table = cast(dict[str, Json], package_map.get("packages", {}))
    root_spec = cast(dict[str, Json], package_table.get(root_package, {}))
    raw_entry = root_spec.get("entry")
    if not isinstance(raw_entry, str):
        raise RuntimeError("native anx package map omitted the fixture entry")
    relative_entry = Path(raw_entry).resolve().relative_to(metadata_source_root)
    root_source_root = root_source_root.resolve()
    if not root_source_root.is_dir():
        raise RuntimeError(f"relocated fixture source root is missing: {root_source_root}")
    root_spec["sourceRoot"] = str(root_source_root)
    root_spec["entry"] = str((root_source_root / relative_entry).resolve())

    relocated_inputs: list[str] = []
    matched_root_inputs: set[Path] = set()
    for source in compiler_inputs:
        source_path = Path(source).resolve()
        try:
            relative = source_path.relative_to(metadata_source_root)
        except ValueError:
            relocated_inputs.append(str(source_path))
            continue
        destination = root_inputs.get(relative)
        if destination is None or not destination.is_file():
            raise RuntimeError(f"no parity input corresponds to fixture source {relative}")
        matched_root_inputs.add(relative)
        relocated_inputs.append(str(destination.resolve()))
    if matched_root_inputs != set(root_inputs):
        raise RuntimeError("native anx source selection does not match the parity fixture inputs")
    if not compiler_inputs or any(not Path(source).is_file() for source in relocated_inputs):
        raise RuntimeError("native anx produced an empty or incomplete compiler input list")

    parsed_map = PackageMap.from_document(package_map, source="native anx fixture metadata")
    for package in parsed_map.packages.values():
        if package.native_llvm is not None and any(not source.is_file() for source in package.native_llvm.sources):
            raise RuntimeError(f"native anx metadata references a missing LLVM shim in {package.name}")
    return FixtureMetadata(
        package_map=package_map,
        source_files=tuple(relocated_inputs),
        capture_argv=tuple(capture_argv),
    )


cases = __cases
normalize = __normalize
capture_native_package_metadata = __capture_native_package_metadata


def __run(
    compiler: list[str],
    case: Case,
    directory: Path,
    raw: bool,
    default_library: Path,
    anx: list[str] | None,
    reference_launcher: Path | None = None,
) -> dict[str, Json]:
    if case.package_build:
        return __run_package_build(
            compiler,
            case,
            directory,
            raw,
            default_library,
            anx,
            reference_launcher,
        )

    sources: list[str] = []
    for path, contents in case.preexisting:
        destination = directory / path
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(contents)
    for source in case.sources:
        destination = directory / case.source_prefix / source
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(__ROOT / source, destination)
        if case.newlines:
            text = destination.read_text()
            destination.write_bytes(text.replace("\n", case.newlines).encode())
        sources.append(str(destination))
    arguments = list(case.arguments)
    if case.root_option:
        arguments.extend(("--compiler-root", str(__ROOT)))
    if case.package:
        mapping: dict[str, Json] = {
            "format": 2, "root": "probe", "packages": {
                "probe": {"sourceRoot": str(directory / "entry"), "kind": "bin", "entry": str(directory / "entry/main.an"), "dependencies": [], "ffi": False},
                "std": {"sourceRoot": str(__ROOT / "lib/src"), "kind": "lib", "dependencies": [], "ffi": True},
            },
        }
        (directory / "entry").mkdir()
        (directory / "entry/main.an").write_text("fn main() {}\n")
        (directory / "packages.json").write_text(json.dumps(mapping))
        arguments.extend(("--packages", str(directory / "packages.json")))
    if case.fixture:
        if anx is None:
            raise RuntimeError(f"native anx is required for package fixture {case.fixture}")
        fixture_source_root = Path(case.fixture) / "src"
        root_inputs = {
            Path(source).relative_to(fixture_source_root): directory / case.source_prefix / source
            for source in case.sources
        }
        relocated_source_root = directory / case.source_prefix / fixture_source_root
        metadata = __capture_native_package_metadata(
            anx,
            case.fixture,
            directory,
            relocated_source_root,
            root_inputs,
            default_library,
        )
        mapping = metadata.package_map
        sources = list(metadata.source_files)
        (directory / "packages.json").write_text(json.dumps(mapping))
        arguments.extend(("--packages", str(directory / "packages.json")))
    if raw:
        arguments.append("--raw-pointers")
    if case.standard_library and not case.fixture:
        environment_values = dict(case.environment)
        library_root = __ROOT / "lib/src"
        if environment_values.get("YIAN_LIB") == "" and environment_values.get("YIAN_ROOT") == "":
            library_root = default_library
        sources.insert(0, str(library_root))
    command = [*compiler, *arguments, *(["--"] if case.separator else []), *sources]
    if case.interleave:
        command = [*compiler, sources[0], *arguments, *sources[1:]]
    environment = os.environ.copy()
    for name in ("YIAN_LIB", "YIAN_ROOT", "YIAN_LOG", "YIAN_LOG_FILE", "YIANC"):
        environment.pop(name, None)
    environment["YIAN_ROOT"] = str(__ROOT)
    environment["YIAN_LIB"] = str(default_library if case.fixture else __ROOT / "lib/src")
    source_path = str(__PYTHON_ROOT)
    if environment.get("PYTHONPATH"):
        source_path += os.pathsep + environment["PYTHONPATH"]
    environment["PYTHONPATH"] = source_path
    environment.update(case.environment)
    process = subprocess.run(command, cwd=directory, env=environment, capture_output=True, text=True)
    stdout: Json = process.stdout
    if case.json_output:
        try:
            stdout = cast(Json, json.loads(process.stdout))
        except json.JSONDecodeError:
            pass
    # Diagnostic text layout is implementation-specific; machine output is compared in full.
    stderr: Json = process.stderr
    artifacts: list[Json] = [name for name in sorted({path.relative_to(directory).as_posix() for pattern in case.artifact_patterns for path in directory.glob(pattern) if path.is_file()})]
    result: dict[str, Json] = {"status": process.returncode, "stdout": stdout, "artifacts": artifacts}
    if case.preexisting:
        result["preserved"] = {path: (directory / path).read_text() == contents for path, contents in case.preexisting}
    if case.expected_error:
        if case.json_output:
            diagnostics = cast(list[dict[str, Json]], stdout.get("diagnostics", [])) if isinstance(stdout, dict) else []
            errors = [item for item in diagnostics if item.get("severity") == "error"]
            result["expected_error"] = process.returncode == 1 and bool(errors) and all(case.expected_error in cast(str, item.get("message", "")) for item in errors)
            if case.expected_error_count is not None:
                result["expected_error"] = result["expected_error"] is True and len(errors) == case.expected_error_count
        else:
            result["expected_error"] = process.returncode == 1 and case.expected_error in process.stderr
    if case.json_output or case.group == "format":
        result["stderr"] = stderr
    if case.group == "format":
        result["sources"] = {source: (directory / case.source_prefix / source).read_bytes().decode() for source in case.sources}
    if case.observability == "profile":
        # Timing magnitudes and labels may differ. A real profile contains multiple measured durations.
        durations = [float(value) for value in re.findall(r"(\d+\.\d+)(?:\s*ms|s\s*\()", process.stderr)]
        result["profile_measured"] = len(durations) >= 3 and sum(durations) > 0
    elif case.observability in ("log", "off"):
        log = directory / "requested.log"
        result["log_created"] = log.is_file()
        contents = log.read_text() if log.is_file() else ""
        result["log_enabled"] = bool(contents)
        result["stderr_logging"] = bool(process.stderr)
    elif case.observability == "dump":
        required = ("tokens.txt", "ast.txt", "hir.txt", "cfg.txt")
        result["dumps"] = {name: (directory / "build" / name).is_file() and (directory / "build" / name).stat().st_size > 0 for name in required}
        result["llvm_dump"] = (directory / "build/ir.ll").is_file()
    if case.run and process.returncode == 0:
        executable = directory / case.executable
        if executable.is_file():
            run = subprocess.run([str(executable)], cwd=directory, capture_output=True, text=True)
            result["run"] = {"status": run.returncode, "stdout": run.stdout, "stderr": run.stderr}
        else:
            result["run"] = "missing executable"
    return {"command": [str(value) for value in command], "observation": __normalize(result, directory, default_library), "raw_stdout": process.stdout, "raw_stderr": process.stderr}


def run_case(
    compiler: list[str],
    case: Case,
    directory: Path,
    raw: bool,
    default_library: Path,
    anx: list[str] | None,
    reference_launcher: Path | None = None,
) -> dict[str, Json]:
    """Run one compiler or package observation in an isolated work directory."""
    return __run(compiler, case, directory, raw, default_library, anx, reference_launcher)


def __run_package_build(
    compiler: list[str],
    case: Case,
    directory: Path,
    raw: bool,
    default_library: Path,
    anx: list[str] | None,
    reference_launcher: Path | None,
) -> dict[str, Json]:
    if not case.fixture:
        raise RuntimeError(f"package build case {case.name} has no fixture")
    if anx is None:
        raise RuntimeError(f"native anx is required for package build case {case.name}")

    project = directory / "project"
    shutil.copytree(
        __ROOT / case.fixture,
        project,
        ignore=shutil.ignore_patterns("build", "__pycache__", ".pytest_cache"),
    )
    command = [*anx, "build", str(project)]
    if raw:
        command.append("--raw-pointers")

    environment = os.environ.copy()
    for name in ("YIAN_LOG", "YIAN_LOG_FILE"):
        environment.pop(name, None)
    environment["YIAN_ROOT"] = str(__ROOT)
    environment["YIAN_LIB"] = str(default_library)
    environment["PATH"] = str(Path(compiler[0]).resolve().parent) + os.pathsep + environment.get("PATH", "")
    python_path = str(__PYTHON_ROOT)
    if environment.get("PYTHONPATH"):
        python_path += os.pathsep + environment["PYTHONPATH"]
    environment["PYTHONPATH"] = python_path
    if compiler == [sys.executable, "-m", "yianc.main"]:
        if reference_launcher is None:
            raise RuntimeError("Python package builds require a reference compiler launcher")
        environment["YIANC"] = str(reference_launcher)
    else:
        environment["YIANC"] = compiler[0]

    process = subprocess.run(
        command,
        cwd=directory,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    mapping_path = project / "build" / "pkg.json"
    mapping: dict[str, Json] | None = None
    mapping_error: str | None = None
    if mapping_path.is_file():
        try:
            mapping = cast(dict[str, Json], json.loads(mapping_path.read_text(encoding="utf-8")))
            PackageMap.from_document(mapping, source="native anx package parity build")
        except (OSError, json.JSONDecodeError, CompilerError, ValueError) as error:
            mapping_error = str(error)

    run_result: dict[str, Json] | None = None
    executable = project / "build" / "app"
    if process.returncode == 0 and executable.is_file():
        run = subprocess.run(
            [str(executable)],
            cwd=directory,
            env=environment,
            capture_output=True,
            text=True,
            check=False,
        )
        run_result = {"status": run.returncode, "stdout": run.stdout, "stderr": run.stderr}

    observation: dict[str, Json] = {
        "status": process.returncode,
        "package_map": mapping,
        "package_map_error": mapping_error,
        "run": run_result,
    }
    return {
        "command": [str(value) for value in command],
        "environment": {"YIANC": environment["YIANC"], "YIAN_LIB": str(default_library)},
        "observation": __normalize(observation, directory, default_library),
        "raw_stdout": process.stdout,
        "raw_stderr": process.stderr,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reference", default="python", help="Python module (default) or compiler executable path.")
    parser.add_argument("--bootstrap", required=True)
    parser.add_argument("--anx", help="Native anx executable used only to load package-fixture metadata.")
    parser.add_argument("--build-mode", choices=("fat", "raw", "unspecified"), default="unspecified")
    parser.add_argument("--groups", default="analyze,cli,frontend,format,native,observability")
    parser.add_argument("--filter", default="")
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--full-corpus", action="store_true", help="also compare all existing nonexperimental basic frontend sources")
    parser.add_argument("--full-analysis", action="store_true", help="also compare the existing basic suite's semantic JSON")
    parser.add_argument("--failed-from", type=Path, help="select cases that failed in a previous complete report")
    args = parser.parse_args(namespace=__Arguments())
    if args.reference == "python":
        reference = [sys.executable, "-m", "yianc.main"]
        reference_label = "python -m yianc.main"
    else:
        reference_path = shutil.which(args.reference)
        if reference_path is None:
            parser.error(f"reference compiler not found: {args.reference}")
        reference = [str(Path(reference_path).resolve())]
        reference_label = reference[0]

    bootstrap_path = shutil.which(args.bootstrap)
    if bootstrap_path is None:
        parser.error(f"bootstrap compiler not found: {args.bootstrap}")
    bootstrap = [str(Path(bootstrap_path).resolve())]
    bootstrap_label = bootstrap[0]
    bootstrap_executable = Path(bootstrap[0])
    installed_root = bootstrap_executable.parent.parent / "share/yian"
    bootstrap_library = installed_root / "lib/src"
    if not bootstrap_library.is_dir():
        bootstrap_library = __ROOT / "lib/src"
    groups = set(args.groups.split(","))
    available = __cases()
    if args.full_analysis:
        for test in discover_tests():
            if "experimental" in Path(test.name).parts:
                continue
            available.append(Case("analysis-" + test.name, "analyze", tuple(path.relative_to(__ROOT).as_posix() for path in test.source_files), ("--analyze", "--json", *test.compiler_args), json_output=True))
    if args.full_corpus:
        for source in sorted((__ROOT / "tests/basic").rglob("*.an")):
            if "experimental" in source.parts or source.name.endswith(".err.an"):
                continue
            name = source.relative_to(__ROOT).as_posix()
            for phase in ("tokens", "ast", "desugared"):
                available.append(Case("corpus-" + name + "-" + phase, "frontend", (name,), ("--frontend-json", phase), standard_library=False, json_output=True))
    cases = [case for case in available if case.group in groups and args.filter in case.name]
    if len({case.name for case in available}) != len(available):
        parser.error("case names must be unique")
    if args.failed_from:
        previous = cast(dict[str, Json], json.loads(args.failed_from.read_text()))
        previous_cases = cast(list[dict[str, Json]], previous["cases"])
        failed_names = {cast(str, case["case"]) for case in previous_cases if case["equal"] is False}
        missing = failed_names - {case.name for case in available}
        if missing:
            parser.error("previous failures are absent from the selected corpus: " + ", ".join(sorted(missing)))
        cases = [case for case in cases if case.name in failed_names]
    if not cases:
        parser.error("no matching cases; an empty selection is not a parity check")
    anx_command: list[str] | None = None
    if any(case.fixture for case in cases):
        if not args.anx:
            parser.error("--anx PATH is required when the selection includes a package fixture")
        anx_path = shutil.which(args.anx)
        if anx_path is None:
            parser.error(f"native anx not found: {args.anx}")
        anx_command = [str(Path(anx_path).resolve())]
    revision = subprocess.run(["git", "rev-parse", "HEAD"], cwd=__ROOT, text=True, capture_output=True).stdout.strip()
    changes = subprocess.run(["git", "status", "--porcelain"], cwd=__ROOT, text=True, capture_output=True).stdout
    compiler_hashes = [
        hashlib.sha256(Path(command[0]).read_bytes()).hexdigest()
        if len(command) == 1 and Path(command[0]).is_file()
        else None
        for command in (reference, bootstrap)
    ]
    # A dirty checkout needs a content identity in addition to its committed revision.
    paths = subprocess.run(["git", "ls-files", "-co", "--exclude-standard", "-z"], cwd=__ROOT, capture_output=True, check=True).stdout.decode().split("\0")
    source_hash = hashlib.sha256()
    for name in sorted(set(paths)):
        source = __ROOT / name
        if source.suffix not in (".an", ".anx", ".py", ".c", ".h", ".inc", ".toml") or not source.is_file():
            continue
        contents = source.read_bytes()
        source_hash.update(name.encode() + b"\0" + str(len(contents)).encode() + b"\0" + contents)
    records: list[Json] = []
    failures = 0
    args.report.parent.mkdir(parents=True, exist_ok=True)
    failure_log = args.report.with_suffix(".failures.jsonl")
    failure_log.write_text("")
    for case in cases:
        for raw in case.target_modes:
            with tempfile.TemporaryDirectory(prefix="yian-parity-") as temporary:
                directories = [Path(temporary) / "reference", Path(temporary) / "bootstrap"]
                for directory in directories:
                    directory.mkdir()
                expected = __run(reference, case, directories[0], raw, __ROOT / "lib/src", anx_command)
                actual = __run(bootstrap, case, directories[1], raw, bootstrap_library, anx_command)
                equal = expected["observation"] == actual["observation"]
                if case.expected_error:
                    observations = [cast(dict[str, Json], item["observation"]) for item in (expected, actual)]
                    equal = equal and all(item.get("expected_error") is True for item in observations)
                failures += not equal
                record: dict[str, Json] = {"case": case.name, "group": case.group, "raw": raw, "equal": equal, "reference": expected, "bootstrap": actual}
                records.append(record)
                if not equal:
                    # Preserve raw evidence immediately, including when a later compiler invocation fails.
                    with failure_log.open("a") as output:
                        output.write(json.dumps(record, ensure_ascii=False) + "\n")
                print(f"{'PASS' if equal else 'FAIL'} {case.name}@{'raw' if raw else 'fat'}", flush=True)
    report: dict[str, Json] = {
        "revision": revision, "source_sha256": source_hash.hexdigest(), "worktree_changes": changes, "build_mode": args.build_mode,
        "reference": reference_label, "bootstrap": bootstrap_label,
        "reference_sha256": compiler_hashes[0], "bootstrap_sha256": compiler_hashes[1],
        "total": len(records), "failures": failures, "cases": records,
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(f"Total: {len(records)}; failures: {failures}; report: {args.report}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
