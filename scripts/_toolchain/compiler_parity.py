#!/usr/bin/env python3
"""Run the stable compiler case catalog against native binaries and fixed oracles."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import TypeAlias, cast

__SCRIPT_ROOT = Path(__file__).resolve().parents[1]
__ROOT = __SCRIPT_ROOT.parent
sys.path.insert(0, str(__SCRIPT_ROOT))

from _toolchain.identity import component_source_sha256, file_sha256
from _toolchain.native import NativeToolError, resolve_native

Json: TypeAlias = None | bool | int | float | str | list["Json"] | dict[str, "Json"]


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
    expected_error_code: str = ""
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
    expected_exit_code: int | None = None
    expected_output: str = ""


class __Arguments(argparse.Namespace):
    native: str
    anx: str | None
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
        Case("integer-conversion", "analyze", ("tests/basic/control_flow/comptime_untyped_integer.an",), ("--analyze", "--json"), json_output=True),
        Case("float-conversion", "analyze", ("tests/basic/control_flow/comptime_untyped_integer.an",), ("--analyze", "--json"), json_output=True),
        Case("nonfinite-cast", "analyze", ("tests/basic/diagnostics/comptime_nonfinite_cast.err.an",), ("--analyze", "--json"), json_output=True),
        Case("bool-float-cast", "analyze", ("tests/basic/diagnostics/comptime_bool_float_cast.err.an",), ("--analyze", "--json"), json_output=True),
        Case("bool-integer-constant-error", "analyze", ("tests/basic/diagnostics/comptime_bool_integer_cast.err.an",), ("--analyze", "--json"), json_output=True),
        Case("bool-integer-casts", "analyze", ("tests/compiler_parity/bool_conversion.an",), ("--analyze", "--json", "--allow-ffi"), json_output=True, expected_error="cannot convert bool to an integer", expected_error_count=12),
        Case("division-by-zero", "analyze", ("tests/basic/error/comptime_if_div_zero.err.an",), ("--analyze", "--json"), json_output=True, expected_error_code="E503", expected_error_count=1),
        Case("defer-uninitialized", "analyze", ("tests/basic/error/defer_uninitialized.err.an",), ("--analyze", "--json"), json_output=True, expected_error_code="E502", expected_error_count=1),
        Case("uncalled-concrete-definition", "analyze", ("tests/basic/error/uncalled_concrete_definition.err.an",), ("--analyze", "--json"), json_output=True, expected_error_code="E502", expected_error_count=1),
        Case("uncalled-comptime-condition", "analyze", ("tests/basic/error/uncalled_comptime_condition.err.an",), ("--analyze", "--json"), json_output=True, expected_error_code="E503", expected_error_count=1),
        Case("ffi-alias-permission", "analyze", ("tests/basic/type/alias/ffi_alias_permission.err.an",), ("--analyze", "--json", "--allow-ffi"), json_output=True, expected_error="C ABI types require an ffi fn", expected_error_count=1),
        Case("negative-shift", "analyze", ("tests/basic/error/comptime_if_negative_shift.err.an",), ("--analyze", "--json"), json_output=True),
        Case("multiple-errors", "analyze", ("tests/compiler_parity/multiple_errors.an",), ("--analyze", "--json"), json_output=True),
        Case("strict-partial-input", "analyze", ("tests/compiler_parity/partial_analysis_invalid.an",), ("--analyze", "--json"), json_output=True, expected_error="Unknown identifier 'missing'", expected_error_code="E301", expected_error_count=1),
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
        Case("multiple-main", "analyze", ("tests/basic/array/access.an", "tests/basic/literal/str.an"), ("--analyze", "--json"), json_output=True),
        Case("default-roots", "cli", ("tests/basic/array/access.an",), ("--analyze", "--json"), json_output=True, root_option=False),
        Case("explicit-root-precedence", "cli", ("tests/basic/array/access.an",), ("--analyze", "--json"), json_output=True, environment=(("YIAN_LIB", "/nonexistent/library"), ("YIAN_ROOT", "/nonexistent/checkout"))),
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
        Case("restricted-bitcast", "analyze", ("tests/basic/error/restricted_bitcast.err.an",), ("--analyze", "--json"), json_output=True, expected_error="restricted operation '@bitcast' is only allowed in the standard library", expected_error_code="E501", expected_error_count=1),
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
        cases.append(Case("crlf-format-" + name, "format", ("tests/compiler_parity/format.an",), ("--format", *flags), standard_library=False, newlines="\r\n"))
    for phase in ("tokens", "ast", "desugared"):
        cases.append(Case(phase, "frontend", ("tests/basic/literal/str.an", "tests/basic/fmt/fstring_basic.an", "tests/basic/try/generic.an"), ("--frontend-json", phase), standard_library=False, json_output=True))
    for name, flags in (("format-stdout", ()), ("format-check", ("--check",)), ("format-write", ("--write",))):
        cases.append(Case(name, "format", ("tests/compiler_parity/format.an",), ("--format", *flags), standard_library=False))
    format_fixtures = __ROOT / "tests/format/fixtures"
    for fixture in sorted(format_fixtures.rglob("*.an")):
        if not fixture.name.endswith(".expected.an"):
            cases.append(Case("fixture-" + fixture.relative_to(format_fixtures).as_posix(), "format", (fixture.relative_to(__ROOT).as_posix(),), ("--format",), standard_library=False))
    for name in ("mixed", "only"):
        for flag in ("--write", "--check"):
            cases.append(Case(f"comments-{name}-{flag[2:]}", "format", (f"tests/format/fixtures/comments/{name}.an",), ("--format", flag), standard_library=False))
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
    cases.append(Case("integer-conversion-run", "native", ("tests/basic/control_flow/comptime_untyped_integer.an",), ("-O3", "-o", "result"), run=True))
    cases.append(Case("float-conversion-run", "native", ("tests/basic/control_flow/comptime_untyped_integer.an",), ("-O3", "-o", "result"), run=True))
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


cases = __cases


def __run(
    native: list[str],
    case: Case,
    directory: Path,
    raw: bool,
    default_library: Path,
    anx: list[str] | None,
) -> dict[str, Json]:
    if case.package_build:
        return __run_package_build(native, case, directory, raw, default_library, anx)

    sources: list[str] = []
    for path, contents in case.preexisting:
        destination = directory / path
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(contents, encoding="utf-8")
    for source in case.sources:
        destination = directory / case.source_prefix / source
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(__ROOT / source, destination)
        if case.newlines:
            text = destination.read_text(encoding="utf-8")
            destination.write_bytes(text.replace("\n", case.newlines).encode())
        sources.append(str(destination))

    source_inputs = {
        source: (directory / case.source_prefix / source).read_bytes().decode("utf-8")
        for source in case.sources
    }
    mapping: dict[str, Json] | None = None
    arguments = list(case.arguments)
    if case.root_option:
        arguments.extend(("--compiler-root", str(__ROOT)))
    if case.package:
        mapping: dict[str, Json] = {
            "format": 2,
            "root": "probe",
            "packages": {
                "probe": {
                    "sourceRoot": str(directory / "entry"),
                    "kind": "bin",
                    "entry": str(directory / "entry/main.an"),
                    "dependencies": [],
                    "ffi": False,
                },
                "std": {
                    "sourceRoot": str(default_library.resolve()),
                    "kind": "lib",
                    "dependencies": [],
                    "ffi": True,
                },
            },
        }
        entry_root = directory / "entry"
        entry_root.mkdir(parents=True, exist_ok=True)
        (entry_root / "main.an").write_text("fn main() {}\n", encoding="utf-8")
        (directory / "packages.json").write_text(json.dumps(mapping), encoding="utf-8")
        arguments.extend(("--packages", str(directory / "packages.json")))
    if raw:
        arguments.append("--raw-pointers")
    if case.standard_library:
        environment_values = dict(case.environment)
        library_root = default_library
        if environment_values.get("YIAN_LIB") == "" and environment_values.get("YIAN_ROOT") == "":
            library_root = default_library
        sources.insert(0, str(library_root.resolve()))

    command = [*native, *arguments, *(["--"] if case.separator else []), *sources]
    if case.interleave:
        command = [*native, sources[0], *arguments, *sources[1:]]
    environment = os.environ.copy()
    for name in ("YIAN_LIB", "YIAN_ROOT", "YIAN_LOG", "YIAN_LOG_FILE", "YIANC"):
        environment.pop(name, None)
    environment["YIAN_ROOT"] = str(__ROOT)
    environment["YIAN_LIB"] = str(default_library.resolve())
    environment["YIANC"] = native[0]
    environment.update(dict(case.environment))
    process = subprocess.run(command, cwd=directory, env=environment, capture_output=True,
                             text=True, check=False)

    stdout: Json = process.stdout
    if case.json_output:
        try:
            stdout = cast(Json, json.loads(process.stdout))
        except json.JSONDecodeError:
            pass
    artifacts: list[Json] = [
        name
        for name in sorted({
            path.relative_to(directory).as_posix()
            for pattern in case.artifact_patterns
            for path in directory.glob(pattern)
            if path.is_file()
        })
    ]
    result: dict[str, Json] = {
        "status": process.returncode,
        "stdout": stdout,
        "stderr": process.stderr,
        "artifacts": artifacts,
    }
    if mapping is not None:
        result["package_map"] = cast(Json, mapping)
    if case.preexisting:
        result["preserved"] = {
            path: (directory / path).read_text(encoding="utf-8") == contents
            for path, contents in case.preexisting
        }
    expected_error = __expected_error_text(case)
    if expected_error:
        if case.json_output:
            diagnostics = cast(list[dict[str, Json]], stdout.get("diagnostics", [])) if isinstance(stdout, dict) else []
            errors = [item for item in diagnostics if item.get("severity") == "error"]
            rendered_errors = "\n".join(
                " ".join(str(item.get(key, "")) for key in ("code", "message")) for item in errors
            )
            result["expected_error"] = (
                process.returncode == 1
                and bool(errors)
                and expected_error in rendered_errors
            )
            if case.expected_error_count is not None:
                result["expected_error"] = result["expected_error"] is True and len(errors) == case.expected_error_count
            if case.expected_error_code:
                result["expected_error_code"] = process.returncode == 1 and any(
                    item.get("code") == case.expected_error_code for item in errors
                )
        else:
            result["expected_error"] = process.returncode != 0 and expected_error in process.stderr
    if case.group == "format":
        result["source_inputs"] = cast(Json, source_inputs)
        result["sources"] = {
            source: (directory / case.source_prefix / source).read_bytes().decode("utf-8")
            for source in case.sources
        }
    if case.observability == "profile":
        durations = [float(value) for value in re.findall(r"(\d+\.\d+)(?:\s*ms|s\s*\()", process.stderr)]
        result["profile_measured"] = len(durations) >= 3 and sum(durations) > 0
    elif case.observability in ("log", "off"):
        log = directory / "requested.log"
        result["log_created"] = log.is_file()
        contents = log.read_text(encoding="utf-8") if log.is_file() else ""
        result["log_enabled"] = bool(contents)
        result["stderr_logging"] = bool(process.stderr)
    elif case.observability == "dump":
        required = ("tokens.txt", "ast.txt", "hir.txt", "cfg.txt")
        result["dumps"] = {
            name: (directory / "build" / name).is_file()
            and (directory / "build" / name).stat().st_size > 0
            for name in required
        }
        result["llvm_dump"] = (directory / "build/ir.ll").is_file()

    if case.run and process.returncode == 0:
        executable = directory / case.executable
        if executable.is_file():
            run = subprocess.run([str(executable)], cwd=directory, env=environment,
                                 capture_output=True, text=True, check=False)
            result["run"] = {"status": run.returncode, "stdout": run.stdout, "stderr": run.stderr}
        else:
            result["run"] = "missing executable"
    return {
        "command": [str(value) for value in command],
        "observation": __normalize(result, directory, default_library),
        "raw_stdout": process.stdout,
        "raw_stderr": process.stderr,
    }


def run_case(
    native: list[str],
    case: Case,
    directory: Path,
    raw: bool,
    default_library: Path,
    anx: list[str] | None,
) -> dict[str, Json]:
    """Run one native compiler or package case in an isolated work directory."""
    return __run(native, case, directory, raw, default_library, anx)


def __run_package_build(
    native: list[str],
    case: Case,
    directory: Path,
    raw: bool,
    default_library: Path,
    anx: list[str] | None,
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
    for name in ("YIAN_LOG", "YIAN_LOG_FILE", "YIANC"):
        environment.pop(name, None)
    environment["YIAN_ROOT"] = str(__ROOT)
    environment["YIAN_LIB"] = str(default_library.resolve())
    environment["YIANC"] = native[0]
    environment["PATH"] = str(Path(native[0]).resolve().parent) + os.pathsep + environment.get("PATH", "")
    process = subprocess.run(
        command,
        cwd=directory,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    mapping_path = project / "build" / "pkg.json"
    package_map: Json = None
    if mapping_path.is_file():
        try:
            package_map = cast(Json, json.loads(mapping_path.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError):
            package_map = None

    run_result: Json = None
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
        "stdout": process.stdout,
        "stderr": process.stderr,
        "package_map": package_map,
        "run": run_result,
    }
    return {
        "command": [str(value) for value in command],
        "environment": {"YIANC": environment["YIANC"], "YIAN_LIB": environment["YIAN_LIB"]},
        "observation": __normalize(observation, directory, default_library),
        "raw_stdout": process.stdout,
        "raw_stderr": process.stderr,
    }


__ANALYSIS_ERRORS = frozenset({
    "nonfinite-cast", "bool-float-cast", "bool-integer-constant-error",
    "bool-integer-casts", "division-by-zero", "negative-shift", "multiple-errors",
    "stage-error-order", "syntax-error", "entry-errors", "unicode-range",
    "nested-witness", "character-witness", "multiple-main",
})
__CLI_ERRORS = frozenset({
    "strict-entry-first", "missing-package-entry", "log-invalid-spec", "log-empty-path",
    "no-input", "invalid-json-mode", "invalid-option", "invalid-optimization",
    "invalid-package-map", "invalid-library", "empty-library", "unicode-library",
    "nonalnum-library", "no-main-exe", "no-main-ll", "no-main-bc", "no-main-obj",
    "no-main-asm",
})
__NATIVE_ERRORS = frozenset({
    "blocked-output-directory", "missing-native-tool", "failed-native-tool",
    "boolean-conversion", "boolean-integer-conversion",
})


def __expects_error(case: Case) -> bool:
    if case.expected_error or case.name in __ANALYSIS_ERRORS | __CLI_ERRORS | __NATIVE_ERRORS:
        return True
    return any(source.endswith(".err.an") for source in case.sources)


def __expected_error_text(case: Case) -> str:
    if case.expected_error:
        return case.expected_error
    for source in case.sources:
        path = Path(source)
        if path.parts[:2] != ("tests", "basic") or not path.name.endswith(".err.an"):
            continue
        relative = Path(*path.parts[2:])
        expected_name = relative.with_name(relative.name[:-7] + ".an.ans")
        expected = __ROOT / "tests/output/basic" / expected_name
        if expected.is_file():
            return expected.read_text(encoding="utf-8").rstrip("\n")
    return ""


def __output_expectation(source: str) -> tuple[int, str] | None:
    path = Path(source)
    if path.parts[:2] == ("tests", "basic"):
        relative = Path(*path.parts[2:])
        if relative.name.endswith(".err.an"):
            relative = relative.with_name(relative.name[:-7] + ".an")
        expected = __ROOT / "tests/output/basic" / (relative.as_posix() + ".ans")
    elif path.parts[:2] == ("tests", "safety"):
        relative = Path(*path.parts[2:])
        expected = __ROOT / "tests/output/safety" / (relative.as_posix() + ".ans")
    else:
        return None
    if not expected.is_file():
        return (0, "")
    text = expected.read_text(encoding="utf-8").rstrip("\n")
    if text.startswith("Exit code "):
        header, _, text = text.partition("\n")
        return (int(header.removeprefix("Exit code ")), text)
    return (0, text)


def __package_map_valid(value: Json) -> bool:
    if not isinstance(value, dict) or value.get("format") != 2:
        return False
    packages = value.get("packages")
    if not isinstance(packages, dict) or not isinstance(value.get("root"), str):
        return False
    if value["root"] not in packages:
        return False
    for entry in packages.values():
        if not isinstance(entry, dict):
            return False
        if not all(key in entry for key in ("sourceRoot", "kind", "dependencies", "ffi")):
            return False
        source_root = entry["sourceRoot"]
        if not isinstance(source_root, str) or not (source_root.startswith("<") or Path(source_root).is_absolute()):
            return False
        if not isinstance(entry["dependencies"], list) or not isinstance(entry["ffi"], bool):
            return False
    return True


def __format_expected(case: Case, source: str) -> str | None:
    path = __ROOT / source
    expected = path.with_name(path.stem + ".expected.an")
    if expected.is_file():
        return expected.read_text(encoding="utf-8")
    return None


def oracle_passes(case: Case, record: dict[str, Json]) -> bool:
    """Check one native observation against a fixed source, output or behavior oracle."""
    observation = cast(dict[str, Json], record["observation"])
    status = observation.get("status")
    expects_error = __expects_error(case)

    if case.group == "format":
        source_texts = observation.get("sources")
        source_inputs = observation.get("source_inputs")
        if not isinstance(source_texts, dict):
            return False
        for source in case.sources:
            arguments = set(case.arguments)
            actual = source_texts.get(source)
            original = source_inputs.get(source) if isinstance(source_inputs, dict) else None
            if not isinstance(actual, str) or not isinstance(original, str):
                return False
            if case.name.startswith("crlf-format-"):
                normalized_path = "<work>/" + "/".join(part for part in (case.source_prefix, source) if part)
                formatted = __format_expected(case, source)
                if formatted is None:
                    return False
                if "--check" in arguments:
                    expected_stdout = normalized_path + "\n1 of 1 file(s) need formatting\n"
                    return status == 1 and actual == original and "\r" in original and observation.get("stdout") == expected_stdout
                if "--write" in arguments:
                    expected_stdout = "1 file(s) checked, 1 rewritten\n"
                    return status == 0 and actual == formatted and "\r" not in actual and observation.get("stdout") == expected_stdout
                output = observation.get("stdout")
                return status == 0 and output == formatted and "\r" not in output
            formatted = __format_expected(case, source)
            if formatted is None:
                return False
            if "--write" in arguments:
                expected_stdout = f"1 file(s) checked, {1 if original != formatted else 0} rewritten\n"
                if status != 0 or actual != formatted or observation.get("stdout") != expected_stdout:
                    return False
            elif "--check" in arguments:
                expected_status = 0 if original == formatted else 1
                expected_stdout = ""
                if original != formatted:
                    expected_stdout += "<work>/" + "/".join(part for part in (case.source_prefix, source) if part) + "\n"
                changed = 1 if original != formatted else 0
                expected_stdout += f"{changed} of 1 file(s) need formatting\n"
                if status != expected_status or actual != original or observation.get("stdout") != expected_stdout:
                    return False
            else:
                if status != 0 or observation.get("stdout") != formatted:
                    return False
        return True

    if case.package_build:
        package_map = observation.get("package_map")
        run = observation.get("run")
        if not (
            status == 0
            and __package_map_valid(package_map)
            and isinstance(run, dict)
            and run.get("status") == 0
            and run.get("stdout") == ""
            and run.get("stderr") == ""
        ):
            return False
        if not isinstance(package_map, dict):
            return False
        packages = package_map.get("packages")
        root_package = packages.get("ffi_llvm_test") if isinstance(packages, dict) else None
        llvm_package = packages.get("llvm") if isinstance(packages, dict) else None
        if not isinstance(root_package, dict) or "llvm" not in root_package.get("dependencies", []):
            return False
        if not isinstance(llvm_package, dict):
            return False
        native_llvm = llvm_package.get("nativeLLVM")
        return (
            isinstance(native_llvm, dict)
            and native_llvm.get("major") == 22
            and "core" in native_llvm.get("components", [])
            and len(native_llvm.get("sources", [])) == 1
        )

    if case.group == "analyze" and case.json_output:
        result = observation.get("stdout")
        if not isinstance(result, dict):
            return False
        diagnostics = result.get("diagnostics")
        if not isinstance(diagnostics, list):
            return False
        errors = [item for item in diagnostics if isinstance(item, dict) and item.get("severity") == "error"]
        expected_ok = not expects_error
        expected_status = 0 if expected_ok else 1
        if result.get("ok") is not expected_ok or status != expected_status:
            return False
        if result.get("format") != 1 or result.get("stage") != "semantic":
            return False
        if expected_ok and errors:
            return False
        if expects_error and not errors:
            return False
        if __expected_error_text(case) and observation.get("expected_error") is not True:
            return False
        if case.expected_error_code and observation.get("expected_error_code") is not True:
            return False
        if case.expected_error_count is not None and len(errors) != case.expected_error_count:
            return False
        if case.name in {"pattern-warnings", "existing-warning"}:
            warnings = [item for item in diagnostics if isinstance(item, dict) and item.get("severity") == "warning"]
            return bool(warnings)
        return True

    if case.group == "frontend" and case.json_output:
        result = observation.get("stdout")
        if not isinstance(result, dict):
            return False
        expected_error = case.name.startswith("error-")
        if status != (1 if expected_error else 0):
            return False
        phase = case.arguments[1] if len(case.arguments) > 1 else ""
        files = result.get("files")
        if result.get("version") != 1 or result.get("phase") != phase or not isinstance(files, list):
            return False
        if expected_error:
            diagnostic = result.get("diagnostic")
            expected_stage = "lex" if phase == "tokens" else "parse"
            return (
                isinstance(diagnostic, dict)
                and diagnostic.get("stage") == expected_stage
                and isinstance(diagnostic.get("code"), str)
                and isinstance(diagnostic.get("message"), str)
                and bool(diagnostic.get("message"))
            )
        if result.get("diagnostic") is not None or len(files) != len(case.sources):
            return False
        return all(
            isinstance(item, dict) and ("tokens" in item if phase == "tokens" else "ast" in item)
            for item in files
        )

    if case.group == "cli":
        if expects_error:
            if not isinstance(status, int) or status == 0:
                return False
            result = observation.get("stdout")
            if isinstance(result, dict):
                diagnostics = result.get("diagnostics")
                if result.get("ok") is not False or not isinstance(diagnostics, list):
                    return False
                if not any(isinstance(item, dict) and item.get("severity") == "error" for item in diagnostics):
                    return False
            elif not observation.get("stderr"):
                return False
            if __expected_error_text(case) and observation.get("expected_error") is not True:
                return False
            return True
        return status == 0

    if case.group == "observability":
        if case.observability == "profile":
            return status == 0 and observation.get("profile_measured") is True
        if case.observability == "dump":
            dumps = observation.get("dumps")
            if not isinstance(dumps, dict):
                return False
            if case.name == "dump-parse-failure":
                return (
                    status != 0
                    and dumps.get("tokens.txt") is True
                    and dumps.get("ast.txt") is False
                    and dumps.get("hir.txt") is False
                    and dumps.get("cfg.txt") is False
                    and observation.get("llvm_dump") is False
                )
            return (
                status == 0
                and all(value is True for value in dumps.values())
                and observation.get("llvm_dump") is False
            )
        if case.observability == "off":
            return status == 0 and observation.get("log_enabled") is False and observation.get("stderr_logging") is False
        return status == 0 and observation.get("log_created") is True and observation.get("log_enabled") is True

    if expects_error:
        if not isinstance(status, int) or status == 0:
            return False
        if __expected_error_text(case) and observation.get("expected_error") is not True:
            return False
        if case.preexisting and not all(cast(dict[str, Json], observation.get("preserved", {})).values()):
            return False
        return True
    if status != 0:
        return False
    if case.run or case.package_build:
        run = observation.get("run")
        if not isinstance(run, dict):
            return False
        expected = __output_expectation(case.sources[0]) if case.sources else (0, "")
        if expected is not None:
            expected_status, expected_output = expected
            actual_output = cast(str, run.get("stdout", ""))
            actual_error = cast(str, run.get("stderr", ""))
            if run.get("status") != expected_status:
                return False
            if expected_status == 0 and actual_output.rstrip("\n") != expected_output:
                return False
            if expected_status != 0 and (actual_output + actual_error).rstrip("\n") != expected_output:
                return False
        elif run.get("status") != 0 or run.get("stdout") != "" or run.get("stderr") != "":
            return False
    if case.package:
        return __package_map_valid(observation.get("package_map"))
    if case.group == "native":
        expected_artifact = {
            "output-exe": "result",
            "default-output-exe": "build/a.out",
            "output-ll": "result.ll",
            "default-output-ll": "build/access.ll",
            "output-bc": "result.bc",
            "default-output-bc": "build/access.bc",
            "output-obj": "result.o",
            "default-output-obj": "build/access.o",
            "output-asm": "result.s",
            "default-output-asm": "access.s",
            "output-space": "result with space",
            "output-extension": "result.old",
        }.get(case.name)
        artifacts = observation.get("artifacts")
        if expected_artifact is not None and (
            not isinstance(artifacts, list) or expected_artifact not in artifacts
        ):
            return False
        if case.name == "blocked-output-directory":
            preserved = observation.get("preserved")
            return isinstance(preserved, dict) and preserved.get("result") is True
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--native", required=True, help="Native yianc executable.")
    parser.add_argument("--anx", help="Native anx executable for package build cases.")
    parser.add_argument("--groups", default="analyze,cli,frontend,format,native,observability")
    parser.add_argument("--filter", default="")
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--full-corpus", action="store_true", help="include all nonexperimental basic frontend sources")
    parser.add_argument("--full-analysis", action="store_true", help="include semantic analysis cases from the basic suite")
    parser.add_argument("--failed-from", type=Path, help="select cases that failed in a previous native report")
    args = parser.parse_args(namespace=__Arguments())

    try:
        native_path = resolve_native(args.native)
    except NativeToolError as error:
        parser.error(str(error))
    native = [str(native_path)]
    installed_root = native_path.parent.parent / "share/yian"
    native_library = installed_root / "lib/src"
    if not native_library.is_dir():
        native_library = __ROOT / "lib/src"

    available = __cases()
    if args.full_analysis:
        from run_tests import discover_tests

        for test in discover_tests():
            if "experimental" in Path(test.name).parts:
                continue
            available.append(Case(
                "analysis-" + test.name,
                "analyze",
                tuple(path.relative_to(__ROOT).as_posix() for path in test.source_files),
                ("--analyze", "--json", *test.compiler_args),
                json_output=True,
                expected_error=test.expected_substring,
                expected_exit_code=test.expected_exit_code,
                expected_output=test.expected_output,
            ))
    if args.full_corpus:
        for source in sorted((__ROOT / "tests/basic").rglob("*.an")):
            if "experimental" in source.parts or source.name.endswith(".err.an"):
                continue
            name = source.relative_to(__ROOT).as_posix()
            for phase in ("tokens", "ast", "desugared"):
                available.append(Case(
                    "corpus-" + name + "-" + phase,
                    "frontend",
                    (name,),
                    ("--frontend-json", phase),
                    standard_library=False,
                    json_output=True,
                ))

    if len({case.name for case in available}) != len(available):
        parser.error("case names must be unique")
    if args.failed_from:
        try:
            previous = cast(dict[str, Json], json.loads(args.failed_from.read_text(encoding="utf-8")))
            previous_cases = cast(list[dict[str, Json]], previous["cases"])
        except (OSError, json.JSONDecodeError, KeyError, TypeError) as error:
            parser.error(f"cannot read previous report {args.failed_from}: {error}")
        failed_names = {
            cast(str, item["case"])
            for item in previous_cases
            if item.get("passed") is False and isinstance(item.get("case"), str)
        }
        missing = failed_names - {case.name for case in available}
        if missing:
            parser.error("previous failures are absent from this corpus: " + ", ".join(sorted(missing)))
        available = [case for case in available if case.name in failed_names]

    groups = set(args.groups.split(","))
    selected = [case for case in available if case.group in groups and args.filter in case.name]
    if not selected:
        parser.error("no matching cases; an empty native oracle selection is not a verification")
    anx_command: list[str] | None = None
    if any(case.package_build for case in selected):
        if not args.anx:
            parser.error("--anx PATH is required when the selection contains a package build case")
        try:
            anx_command = [str(resolve_native(args.anx))]
        except NativeToolError as error:
            parser.error(str(error))

    revision = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=__ROOT, text=True, capture_output=True, check=False
    ).stdout.strip()
    changes = subprocess.run(
        ["git", "status", "--porcelain"], cwd=__ROOT, text=True, capture_output=True, check=False
    ).stdout
    source_identity = file_sha256(native_path)
    records: list[Json] = []
    failures = 0
    args.report.parent.mkdir(parents=True, exist_ok=True)
    failure_log = args.report.with_suffix(".failures.jsonl")
    failure_log.write_text("", encoding="utf-8")

    for case in selected:
        for raw in case.target_modes:
            mode = "raw" if raw else "fat"
            with tempfile.TemporaryDirectory(prefix="yian-native-parity-") as temporary:
                observed = run_case(
                    native,
                    case,
                    Path(temporary),
                    raw,
                    native_library,
                    anx_command,
                )
            passed = oracle_passes(case, observed)
            record: dict[str, Json] = {
                "case": case.name,
                "group": case.group,
                "pointer_mode": mode,
                "native": observed,
                "oracle_passed": passed,
                "passed": passed,
            }
            records.append(record)
            if not passed:
                with failure_log.open("a", encoding="utf-8") as stream:
                    stream.write(json.dumps(record, ensure_ascii=False) + "\n")
            print(f"{'PASS' if passed else 'FAIL'} {case.name}@{mode}", flush=True)
            failures += not passed

    report: dict[str, Json] = {
        "format": 2,
        "mode": "native-oracle",
        "revision": revision,
        "source_worktree": changes,
        "native": str(native_path),
        "native_sha256": source_identity,
        "native_source_sha256": component_source_sha256(__ROOT, "yianc"),
        "anx": anx_command[0] if anx_command else None,
        "total": len(records),
        "failures": failures,
        "cases": records,
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Total: {len(records)}; failures: {failures}; report: {args.report}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
