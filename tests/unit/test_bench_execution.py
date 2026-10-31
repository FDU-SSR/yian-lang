from __future__ import annotations

import csv
import hashlib
import json
import re
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "bench"))

import bench_asan  # noqa: E402
import bench_three_way as btw  # noqa: E402
import execution  # noqa: E402


PLAN = ROOT / ".agent" / "plans" / "benchmark-projects"
UPSTREAM = PLAN / "UPSTREAM_APIS.md"
SELECTION = PLAN / "SELECTION.md"


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _assert_pinned_file(path: Path, expected: str) -> None:
    actual = _file_sha256(path)
    if actual != expected:
        raise ValueError(f"source drift at {path}: expected {expected}, got {actual}")


class BenchExecutionTests(unittest.TestCase):
    def test_pinned_official_sources_and_oracles(self) -> None:
        plan_text = UPSTREAM.read_text(encoding="utf-8")
        sections = re.split(r"(?m)^## BG/", plan_text)[1:]
        source_contracts: dict[str, tuple[str, str, str]] = {}
        for section in sections:
            case = section.splitlines()[0].strip()
            source = re.search(r"(?m)^- Source: (\S+)$", section)
            digest = re.search(
                r"(?m)^- Extracted UTF-8 bytes: \d+; SHA-256: `([0-9a-f]{64})`\.$",
                section,
            )
            archive = re.search(r"(?m)^- Target original: (\S+);", section)
            if source and digest and archive:
                source_contracts[case] = (source.group(1), digest.group(1), archive.group(1))
        self.assertEqual(set(source_contracts), {
            "binarytree", "fann", "spectralnorm", "fasta", "revcomp",
            "nbody", "mandelbrot", "knucleotide", "pidigits", "regexredux",
        })

        expected_args = {
            "binarytree": ["10"], "fann": ["7"], "spectralnorm": ["100"],
            "fasta": ["1000"], "revcomp": [], "nbody": ["1000"],
            "mandelbrot": ["200"], "knucleotide": [], "pidigits": ["27"],
            "regexredux": [],
        }
        oracle_contracts: dict[str, dict[str, tuple[int, str]]] = {}
        for match in re.finditer(
            r"(?m)^\| ([^|]+) \| (https://benchmarksgame-team\.pages\.debian\.net/benchmarksgame/download/[^|]+) \| (\d+) \| `([0-9a-f]{64})` \|$",
            plan_text,
        ):
            case, url, size, digest = match.group(1).strip(), match.group(2), int(match.group(3)), match.group(4)
            oracle_contracts.setdefault(case, {})[url] = (size, digest)

        cases = {"binary-trees": "binarytree", "fannkuch-redux": "fann", "spectral-norm": "spectralnorm",
                 "fasta": "fasta", "reverse-complement": "revcomp", "n-body": "nbody",
                 "mandelbrot": "mandelbrot", "k-nucleotide": "knucleotide", "pidigits": "pidigits",
                 "regex-redux": "regexredux"}
        task_slugs = {
            "binarytree": "binarytrees", "fann": "fannkuchredux", "spectralnorm": "spectralnorm",
            "fasta": "fasta", "revcomp": "revcomp", "nbody": "nbody", "mandelbrot": "mandelbrot",
            "knucleotide": "knucleotide", "pidigits": "pidigits", "regexredux": "regexredux",
        }
        self.assertEqual(len(cases), 10)
        license_text = (
            ROOT / "bench" / "BG" / "binarytree" / "LICENSE-BenchmarksGame.txt"
        ).read_text(encoding="utf-8")
        self.assertIn("Copyright © 2004-2008 Brent Fulgham, 2005-2025 Isaac Gouy", license_text)
        self.assertIn("3. Neither the name", license_text)
        self.assertIn("AS IS", license_text)

        for case in cases.values():
            source_url, expected_digest, archive = source_contracts[case]
            case_root = ROOT / "bench" / "BG" / case
            provenance = json.loads((case_root / "provenance.json").read_text(encoding="utf-8"))
            self.assertEqual(provenance["format"], 2)
            self.assertEqual(provenance["task"], task_slugs[case])
            self.assertEqual(
                provenance["description_url"],
                f"https://benchmarksgame-team.pages.debian.net/benchmarksgame/description/{task_slugs[case]}.html",
            )
            self.assertEqual(provenance["source"]["url"], source_url)
            self.assertEqual(provenance["source"]["extracted_sha256"], expected_digest)
            self.assertEqual(provenance["source"]["archive_path"], archive.removeprefix(f"bench/BG/{case}/"))
            self.assertEqual(
                provenance["source"]["extraction"],
                "First HTML pre block; remove HTML tags, HTML-unescape once, UTF-8 encode; preserve leading and trailing whitespace.",
            )
            _assert_pinned_file(case_root / archive.removeprefix(f"bench/BG/{case}/"), expected_digest)
            self.assertNotIn("git_commit", provenance["source"])

            files = {entry["path"]: entry for entry in provenance["files"]}
            source_rel = archive.removeprefix(f"bench/BG/{case}/")
            self.assertEqual(files[source_rel]["sha256"], expected_digest)
            license_rel = "LICENSE-BenchmarksGame.txt"
            self.assertEqual(files[license_rel]["license_id"], "BSD-3-Clause")
            self.assertEqual(
                files[license_rel]["sha256"],
                _file_sha256(case_root / license_rel),
            )
            for patch_record in provenance["patches"]:
                self.assertTrue(patch_record["reason"].strip())
                self.assertEqual(
                    patch_record["sha256"],
                    _file_sha256(case_root / patch_record["path"]),
                )
            self.assertIsInstance(provenance["translation"], list)

            seen_urls: dict[str, tuple[Path, str]] = {}
            for fixture in provenance["fixtures"]:
                self.assertEqual(fixture["argv"], expected_args[case])
                if fixture["input_url"] is not None:
                    seen_urls[fixture["input_url"]] = (
                        case_root / fixture["input_path"], fixture["input_sha256"]
                    )
                seen_urls[fixture["output_url"]] = (
                    case_root / fixture["output_path"], fixture["output_sha256"]
                )
            self.assertEqual(set(seen_urls), set(oracle_contracts[case]))
            for url, (path, digest) in seen_urls.items():
                expected_size, expected_digest = oracle_contracts[case][url]
                self.assertEqual(digest, expected_digest)
                self.assertEqual(path.stat().st_size, expected_size)
                self.assertEqual(_file_sha256(path), expected_digest)

        with tempfile.TemporaryDirectory() as directory:
            drifted = Path(directory) / "main.c"
            drifted.write_bytes((ROOT / "bench/BG/binarytree/c/upstream/main.c").read_bytes() + b"\n")
            with self.assertRaisesRegex(ValueError, "source drift"):
                _assert_pinned_file(drifted, source_contracts["binarytree"][1])

        selection = SELECTION.read_text(encoding="utf-8")
        self.assertIn("binarytree | depth16 | depth12 | depth10", selection)
        self.assertIn("mandelbrot | side2000 | side200 | side200 official PBM", selection)

    def test_binary_file_io_contract(self) -> None:
        fasta_oracle = ROOT / "bench/BG/fasta/fixtures/official-output.fasta"
        fasta_bytes = fasta_oracle.read_bytes()
        self.assertEqual(len(fasta_bytes), 10245)
        self.assertEqual(_sha256(fasta_bytes), "62d1e8d0df7938d2aefda9a37887e0389231ea72c099c29a51afb6edca1bdc73")

        bg_specs = {spec.name: spec for spec in btw.discover() if spec.source == "BG"}
        self.assertEqual(bg_specs["binarytree"].run_argv("full"), ("16",))
        self.assertEqual(bg_specs["binarytree"].run_argv("fast"), ("12",))
        self.assertEqual(bg_specs["fann"].run_argv("full"), ("11",))
        self.assertEqual(bg_specs["spectralnorm"].run_argv("fast"), ("1000",))
        self.assertEqual(bg_specs["fasta"].run_argv("full"), ("25000000",))
        self.assertEqual(bg_specs["revcomp"].run_argv("fast"), ())
        self.assertEqual(bg_specs["revcomp"].io.stdin.for_profile("fast"), 100000)
        self.assertEqual(bg_specs["revcomp"].io.stdin.for_profile("full"), 25000000)
        for invalid_io in (
            {"unsupported": {}},
            {"stdin": {"unknown": {"full": 1, "fast": 1}}},
            {"stdin": {"fasta_n": {"full": 0, "fast": 1}}},
            {"stdin": {"fasta_n": {"full": 1, "fast": True}}},
        ):
            invalid_spec = {
                "name": "io-probe", "source": "BG", "tags": [], "argv": [],
                "check": {"rc": 0}, "io": invalid_io,
            }
            with tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "spec.json"
                path.write_text(json.dumps(invalid_spec), encoding="utf-8")
                with self.assertRaises(SystemExit):
                    btw.load_spec(path, SimpleNamespace(name="io-probe", source="BG"))

        project = SimpleNamespace(key="BG/revcomp", raw_overlay=None)
        spec = btw.BenchSpec(
            name="revcomp", source="BG", project=project, argv=(), check=btw.Check(rc=0),
            tags=(), note="probe", io=execution.IOSpec(execution.FastaInput(full=1001, fast=1000)),
        )
        with tempfile.TemporaryDirectory() as directory:
            build_root = Path(directory) / "generated"
            generator_project = SimpleNamespace(key="BG/fasta")

            def build_generator(_project: object, *, asan: bool, output: Path) -> Path:
                self.assertFalse(asan)
                output.parent.mkdir(parents=True, exist_ok=True)
                output.write_bytes(b"selected-source generator binary")
                return output

            generated_sizes: list[int] = []

            def generator_run(command: list[str], **kwargs: object) -> SimpleNamespace:
                size = int(command[-1])
                generated_sizes.append(size)
                output = kwargs["stdout"]
                assert hasattr(output, "write")
                output.write(fasta_bytes if size == 1000 else b">generated N=1001\n")
                return SimpleNamespace(returncode=0, stderr="")

            with patch.object(btw.projects, "discover_projects", return_value=[generator_project]), \
                    patch.object(btw.projects, "build_c", side_effect=build_generator), \
                    patch.object(execution.subprocess, "run", side_effect=generator_run), \
                    patch.object(execution.time, "monotonic") as preparation_clock:
                with patch.object(btw.projects, "project_digest", return_value="fasta-project-digest"):
                    generated_input = execution.prepare_input(spec, "full", build_root)
                    reused_input = execution.prepare_input(spec, "full", build_root)
                self.assertEqual(reused_input, generated_input)
                self.assertEqual(generated_sizes, [1000, 1001, 1000])
                with patch.object(btw.projects, "project_digest", return_value="changed-fasta-project-digest"):
                    rebuilt_input = execution.prepare_input(spec, "full", build_root)
                self.assertEqual(rebuilt_input, generated_input)
                self.assertEqual(generated_sizes, [1000, 1001, 1000, 1000, 1001])
            preparation_clock.assert_not_called()
            self.assertIsNotNone(generated_input)
            assert generated_input is not None
            self.assertEqual(generated_input.read_bytes(), b">generated N=1001\n")
            metadata = json.loads(generated_input.with_suffix(".json").read_text(encoding="utf-8"))
            self.assertEqual(metadata["generator_project_digest"], "changed-fasta-project-digest")
            self.assertEqual(metadata["oracle_sha256"], _sha256(fasta_bytes))
            self.assertEqual(metadata["input_size_bytes"], len(b">generated N=1001\n"))
            self.assertEqual(metadata["input_sha256"], _sha256(b">generated N=1001\n"))

            binary_stdout = b"header\x00payload\xff\n"
            run_calls: list[tuple[object, bytes | None, dict[str, object]]] = []
            return_codes = [0, 0, 9]

            def timed_run(command: list[str], **kwargs: object) -> SimpleNamespace:
                stdin = kwargs["stdin"]
                input_bytes = stdin.read() if hasattr(stdin, "read") else None
                kwargs["stdout"].write(binary_stdout)
                run_calls.append((stdin, input_bytes, kwargs))
                return SimpleNamespace(
                    returncode=return_codes[len(run_calls) - 1],
                    stderr="Maximum resident set size (kbytes): 256\n",
                )

            real_hash = execution._sha256_file
            with tempfile.TemporaryDirectory() as run_directory:
                run_root = Path(run_directory)
                (run_root / "one.out").write_bytes(b"old longer output that must be truncated")
                times = iter((10.0, 10.25, 20.0, 20.5, 30.0, 30.5))
                with (
                    patch.object(execution.subprocess, "run", side_effect=timed_run) as process,
                    patch.object(execution.time, "monotonic", side_effect=lambda: next(times)) as clock,
                    patch.object(
                        execution,
                        "_sha256_file",
                        side_effect=lambda path: (
                            self.assertIn(clock.call_count, (2, 4, 6)) or real_hash(path)
                        ),
                    ),
                ):
                    first = execution.run_file(Path("probe-bin"), ("arg",), fasta_oracle, run_root / "one.out", 4, {"TEST_ENV": "yes"})
                    second = execution.run_file(Path("probe-bin"), ("arg",), fasta_oracle, run_root / "two.out", 4, {"TEST_ENV": "yes"})
                    failed = execution.run_file(
                        Path("probe-bin"), ("arg",), fasta_oracle, run_root / "failed.out", 4, {"TEST_ENV": "yes"}
                    )

                self.assertEqual(process.call_count, 3)
                self.assertEqual(clock.call_count, 6)
                self.assertEqual([call[1] for call in run_calls], [fasta_bytes, fasta_bytes, fasta_bytes])
                self.assertIsNot(run_calls[0][0], run_calls[1][0])
                for _, _, kwargs in run_calls:
                    self.assertEqual(kwargs["stdout"].mode, "wb")
                    self.assertNotIn("capture_output", kwargs)
                    self.assertEqual(kwargs["env"]["LANG"], "C")
                    self.assertEqual(kwargs["env"].get("TEST_ENV"), "yes")
                self.assertEqual(run_calls[0][2]["stdout"].name, str(run_root / "one.out"))
                self.assertEqual(first.stdout_size, len(binary_stdout))
                self.assertEqual(first.stdout_sha256, _sha256(binary_stdout))
                self.assertEqual(first.stdout.read_bytes(), binary_stdout)
                self.assertEqual(first.wall_ms, 250.0)
                self.assertEqual(second.wall_ms, 500.0)
                self.assertEqual(failed.rc, 9)
                self.assertTrue(execution.output_equal(first, second))
                self.assertFalse(execution.output_equal(first, failed))

            triple_spec = btw.BenchSpec(
                name="probe", source="BG", project=SimpleNamespace(key="BG/probe", raw_overlay=None),
                argv=(), check=btw.Check(rc=0), tags=(), note="probe", io=execution.IOSpec(),
            )
            triple_outputs = [b"reference", b"reference", b"reference", b"reference", b"changed", b"reference"]

            def triple_run(_binary: Path, _argv: tuple[str, ...], _stdin: Path | None, stdout: Path,
                           _pin: int | None, _env: dict[str, str] | None = None) -> execution.RunResult:
                content = triple_outputs.pop(0)
                stdout.parent.mkdir(parents=True, exist_ok=True)
                stdout.write_bytes(content)
                return execution.RunResult(1.0, 128, 0, stdout, len(content), _sha256(content))

            binaries = {state: Path(state) for state in btw.STATES}
            with (
                patch.object(btw, "OUT_DIR", Path(directory) / "runner-build"),
                patch.object(btw, "spec_bin", side_effect=lambda _spec, state: binaries[state]),
                patch.object(execution, "prepare_input", return_value=None),
                patch.object(execution, "run_file", side_effect=triple_run),
            ):
                row = btw.measure_triple(triple_spec, 1, None, float("inf"), "fast")
            self.assertFalse(btw._ratio_usable(row))
            report_fingerprint = {
                "set": "probe", "scale": "fast", "commit": "test", "date": "today", "pin": "none",
                "hostname": "test", "machine": "test", "cpu_count": "1", "clang": "clang", "cflags": "-O2",
                "python": "python", "yianc": "yianc", "yianc_sha256": "0" * 64,
                "yianc_source": "test", "yianc_used": "test", "opt": "-O2", "runs": "1",
            }
            report_line = next(line for line in btw.render_md(
                [row], report_fingerprint
            ).splitlines() if line.startswith("| BG/probe |"))
            self.assertEqual([cell.strip() for cell in report_line.split("|")[5:8]], ["nan×", "nan×", "nan×"])
            csv_path = Path(directory) / "invalid.csv"
            fingerprint = {
                "hostname": "test", "machine": "test", "cpu_count": "1", "clang": "clang",
                "cflags": "-O2", "python": "python", "yianc": "yianc", "yianc_sha256": "0" * 64,
                "yianc_source": "test", "yianc_used": "test", "opt": "-O2", "pin": "none",
                "runs": "1", "set": "probe", "scale": "fast", "commit": "test", "date": "today",
            }
            btw.write_csv([row], fingerprint, csv_path)
            csv_rows = [line for line in csv_path.read_text(encoding="utf-8").splitlines() if not line.startswith("#")]
            parsed = list(csv.DictReader(csv_rows))
            self.assertEqual(
                (parsed[0]["ratio_raw_c"], parsed[0]["ratio_fat_c"], parsed[0]["ratio_fat_raw"]),
                ("nan", "nan", "nan"),
            )

            asan_spec = btw.BenchSpec(
                name="asanprobe", source="BG", project=SimpleNamespace(key="BG/asanprobe", raw_overlay=None),
                argv=(), check=btw.Check(rc=0), tags=(), note="probe", io=execution.IOSpec(),
            )
            asan_outputs = [b"reference", b"reference", b"reference", b"reference", b"changed", b"reference"]

            def asan_run(_binary: Path, _argv: tuple[str, ...], _stdin: Path | None, stdout: Path,
                         _pin: int | None, _env: dict[str, str] | None = None) -> execution.RunResult:
                content = asan_outputs.pop(0)
                stdout.parent.mkdir(parents=True, exist_ok=True)
                stdout.write_bytes(content)
                return execution.RunResult(1.0, 128, 0, stdout, len(content), _sha256(content))

            with (
                patch.object(btw, "OUT_DIR", Path(directory) / "asan-runner-build"),
                patch.object(execution, "prepare_input", return_value=None),
                patch.object(execution, "run_file", side_effect=asan_run),
            ):
                _, _, output_records, sample_valid = bench_asan.measure(
                    asan_spec,
                    {"c_plain": (Path("c"), None), "c_asan": (Path("asan"), None), "an_check": (Path("yian"), None)},
                    "fast", 1, None,
                )
            self.assertFalse(sample_valid)
            self.assertEqual(output_records["c_plain"].stdout.name, "warm-c_plain.stdout")
            asan_result = {
                "key": "BG/asanprobe", "name": "asanprobe", "source": "BG",
                "legs": {key: {"ms": [1.0], "kb": [128]} for key in ("c_plain", "c_asan", "an_check")},
                "codes": {"c_plain": 0, "c_asan": 0, "an_check": 0}, "c_check_ok": True,
                "asan_match": True, "yian_ok": True, "sample_valid": sample_valid,
            }
            asan_report = bench_asan.render_md(
                [asan_result],
                {}, 1, None, "probe", "fast",
            )
            self.assertIn("| BG/asanprobe | n/a | n/a | n/a |", asan_report)
            self.assertIn("one or more warmup/sample checks failed; ratios are unavailable", asan_report)
            asan_csv = Path(directory) / "asan-invalid.csv"
            asan_samples = Path(directory) / "asan-invalid-samples.txt"
            bench_asan.write_csv([asan_result], asan_csv)
            bench_asan.emit_samples([asan_result], asan_samples)
            self.assertIn("# sample_valid=false: BG/asanprobe", asan_csv.read_text(encoding="utf-8"))
            self.assertIn("# sample_valid=false: BG/asanprobe", asan_samples.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
