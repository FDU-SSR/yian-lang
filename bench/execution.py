"""Binary-safe benchmark input preparation and timed file execution."""

from __future__ import annotations

import hashlib
import os
import re
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .bench_three_way import BenchSpec


ROOT = Path(__file__).resolve().parents[1]
BENCH_DIR = ROOT / "bench"
_TIME_RSS_RE = re.compile(r"Maximum resident set size \(kbytes\):\s*(\d+)")
_HASH_CHUNK = 1024 * 1024


@dataclass(frozen=True)
class FastaInput:
    full: int
    fast: int

    def __post_init__(self) -> None:
        for name, value in (("full", self.full), ("fast", self.fast)):
            if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
                raise ValueError(f"fasta_n.{name} must be a positive integer")

    def for_profile(self, profile: str) -> int:
        if profile == "full":
            return self.full
        if profile == "fast":
            return self.fast
        raise ValueError(f"profile must be 'full' or 'fast', got {profile!r}")


@dataclass(frozen=True)
class IOSpec:
    stdin: FastaInput | None = None


@dataclass(frozen=True)
class RunResult:
    wall_ms: float
    rss_kb: int
    rc: int
    stdout: Path
    stdout_size: int
    stdout_sha256: str


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as stream:
            while chunk := stream.read(_HASH_CHUNK):
                digest.update(chunk)
    except OSError as error:
        raise RuntimeError(f"cannot hash output file {path}: {error}") from error
    return digest.hexdigest()


def _files_equal(left: Path, right: Path) -> bool:
    try:
        with left.open("rb") as left_stream, right.open("rb") as right_stream:
            while True:
                left_chunk = left_stream.read(_HASH_CHUNK)
                right_chunk = right_stream.read(_HASH_CHUNK)
                if left_chunk != right_chunk:
                    return False
                if not left_chunk:
                    return True
    except OSError as error:
        raise RuntimeError(f"cannot compare output files {left} and {right}: {error}") from error


def _build_root(path: Path) -> Path:
    try:
        path.mkdir(parents=True, exist_ok=True)
        result = path.resolve(strict=True)
    except OSError as error:
        raise RuntimeError(f"cannot prepare benchmark build directory {path}: {error}") from error
    bench_root = BENCH_DIR.resolve()
    if result.is_relative_to(bench_root):
        raise ValueError(f"benchmark output directory must be outside the read-only source tree: {path}")
    return result


def prepare_input(spec: BenchSpec, profile: str, build_root: Path) -> Path | None:
    """Generate a BG FASTA input before timing any consumer."""
    if spec.io is None or spec.io.stdin is None:
        return None
    if spec.source != "BG":
        raise ValueError(f"{spec.key}: file stdin generation is currently defined only for BG")
    n = spec.io.stdin.for_profile(profile)
    root = _build_root(Path(build_root))

    # Import at call time so the shared project builder and benchmark spec loader
    # do not form a module initialization cycle.
    try:
        from . import bench_three_way as btw
        from . import projects
    except ImportError:  # Direct script execution puts bench/ on sys.path.
        import bench_three_way as btw
        import projects

    matches = [project for project in projects.discover_projects(btw.BENCH_DIR) if project.key == "BG/fasta"]
    if len(matches) != 1:
        raise RuntimeError(f"{spec.key}: expected one registered BG/fasta generator project, found {len(matches)}")
    generator_project = matches[0]
    generator_binary = root / "generators" / "BG_fasta"
    try:
        projects.build_c(generator_project, asan=False, output=generator_binary)
    except (OSError, ValueError, RuntimeError) as error:
        raise RuntimeError(f"{spec.key}: cannot build source-matched BG/fasta input generator: {error}") from error

    oracle = btw.BENCH_DIR / "BG" / "fasta" / "fixtures" / "official-output.fasta"
    if not oracle.is_file():
        raise RuntimeError(f"{spec.key}: missing independent official FASTA N=1000 oracle {oracle}")
    input_dir = root / "inputs"
    input_dir.mkdir(parents=True, exist_ok=True)
    output_path = input_dir / f"fasta-n{n}.fasta"
    validation_path = input_dir / f".fasta-n1000-validation-{os.getpid()}.tmp"

    def generate(target: Path, size: int) -> None:
        try:
            with target.open("wb") as output_stream:
                result = subprocess.run(
                    [str(generator_binary), str(size)],
                    stdin=subprocess.DEVNULL,
                    stdout=output_stream,
                    stderr=subprocess.PIPE,
                    text=True,
                    check=False,
                )
        except (OSError, subprocess.SubprocessError) as error:
            raise RuntimeError(f"{spec.key}: FASTA generator failed for N={size}: {error}") from error
        if result.returncode != 0:
            raise RuntimeError(
                f"{spec.key}: FASTA generator failed for N={size} with exit {result.returncode}: {result.stderr}"
            )

    try:
        generate(validation_path, 1000)
        validation_size = validation_path.stat().st_size
        if validation_size != oracle.stat().st_size or not _files_equal(validation_path, oracle):
            raise RuntimeError(
                f"{spec.key}: selected BG/fasta generator does not match the official N=1000 fixture"
            )

        with tempfile.NamedTemporaryFile(prefix=f".fasta-n{n}-", suffix=".tmp", dir=input_dir, delete=False) as temporary:
            temporary_path = Path(temporary.name)
        try:
            generate(temporary_path, n)
            os.replace(temporary_path, output_path)
        finally:
            temporary_path.unlink(missing_ok=True)
        return output_path
    finally:
        validation_path.unlink(missing_ok=True)


def run_file(
    binary: Path,
    argv: tuple[str, ...],
    stdin: Path | None,
    stdout: Path,
    pin: int | None,
    env: dict[str, str] | None = None,
) -> RunResult:
    """Run a benchmark with fresh binary file descriptors and hash after timing."""
    output_path = Path(stdout).expanduser().absolute()
    try:
        resolved_output = output_path.resolve(strict=False)
        if resolved_output.is_relative_to(BENCH_DIR.resolve()):
            raise ValueError(f"benchmark stdout must be owned build output, not a source-tree file: {stdout}")
        if output_path.is_symlink():
            raise ValueError(f"benchmark stdout cannot be a symlink: {stdout}")
        repo_root = ROOT.resolve()
        managed_build = (ROOT / "build").resolve()
        if resolved_output.is_relative_to(repo_root) and not resolved_output.is_relative_to(managed_build):
            raise ValueError(f"benchmark stdout cannot replace a repository source or tracked file: {stdout}")
        if stdin is not None and resolved_output == Path(stdin).expanduser().resolve(strict=True):
            raise ValueError("benchmark stdin and stdout paths must be different")
        output_path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as error:
        raise RuntimeError(f"cannot prepare benchmark stdout {stdout}: {error}") from error

    try:
        from . import bench_three_way as btw
    except ImportError:  # Direct script execution puts bench/ on sys.path.
        import bench_three_way as btw

    command = (["taskset", "-c", str(pin)] if pin is not None else []) + [
        btw.TIME_BIN,
        "-v",
        str(binary),
        *argv,
    ]
    environment = os.environ.copy()
    environment["LANG"] = "C"
    if env:
        environment.update(env)
    try:
        with output_path.open("wb") as output_stream:
            if stdin is None:
                start = time.monotonic()
                result = subprocess.run(
                    command,
                    stdin=subprocess.DEVNULL,
                    stdout=output_stream,
                    stderr=subprocess.PIPE,
                    text=True,
                    env=environment,
                    check=False,
                )
                wall_ms = (time.monotonic() - start) * 1000.0
            else:
                with Path(stdin).open("rb") as input_stream:
                    start = time.monotonic()
                    result = subprocess.run(
                        command,
                        stdin=input_stream,
                        stdout=output_stream,
                        stderr=subprocess.PIPE,
                        text=True,
                        env=environment,
                        check=False,
                    )
                    wall_ms = (time.monotonic() - start) * 1000.0
    except (OSError, subprocess.SubprocessError) as error:
        raise RuntimeError(f"benchmark process failed to run {binary}: {error}") from error

    match = btw._RSS_RE.search(result.stderr or "")
    if match is None:
        raise RuntimeError(
            f"{binary}: cannot parse Maximum resident set size from /usr/bin/time -v output:\n"
            f"{result.stderr or ''}"
        )
    try:
        size = output_path.stat().st_size
    except OSError as error:
        raise RuntimeError(f"cannot inspect benchmark stdout {output_path}: {error}") from error
    digest = _sha256_file(output_path)
    return RunResult(
        wall_ms=wall_ms,
        rss_kb=int(match.group(1)),
        rc=result.returncode,
        stdout=output_path.resolve(strict=True),
        stdout_size=size,
        stdout_sha256=digest,
    )


def output_equal(left: RunResult, right: RunResult) -> bool:
    """Compare complete output identity and then stream bytes without materializing them."""
    return (
        left.rc == right.rc
        and left.stdout_size == right.stdout_size
        and left.stdout_sha256 == right.stdout_sha256
        and _files_equal(left.stdout, right.stdout)
    )
