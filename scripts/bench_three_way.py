#!/usr/bin/env python3
"""bench_three_way.py — C / raw / fat 三态性能实测 (以 C 参考为基线)。

评测集按来源分目录、按侧重分集合、按规模分快速/完全两档:

  bench/<SOURCE>/an/<name>.an       胖态源 (`<name>.raw.an` 为裸态覆盖源)
  bench/<SOURCE>/c/<name>.c         C 参考 (三态的权威值)
  bench/<SOURCE>/specs/<name>.json  argv / 权威校验 / 侧重 tag / 规模档
  bench/sets/<set>.json             集合: 成员 + 默认规模档 + 测量次数
  bench/results/<set>.{md,csv}      生成物 (含环境指纹与 commit)

协议:
  - 三态同一算法、同一规模: C `clang -O2 -lm bench/<SOURCE>/c/<name>.c`;
    raw `yianc -O2 --raw-pointers lib/src <src>`; fat `yianc -O2 lib/src <src>`。
  - 规模档 fast: 对声明了 `scale.fast` 的基准, 把源里 `// bench-scale` 标记行的数字换成
    fast 值, 生成构建副本 `build/bench/src/<profile>/<SOURCE>/<name>.an` 再编译; 同一个值
    作为 argv 传给 C 参考 (`scale.c == "argv"`)。未声明的基准两档跑同一规模。
  - 产物同目录、等长路径 (`build/bench/{cbin,yraw,yfat}/<SOURCE>_<name>`)。
  - 带任务的绑核 (`taskset -c <cpu>`) 可选, 正式记录必须绑核。
  - 三态逐次轮转测量: 各 1 次 warmup (不计入样本) → 按 C→raw→fat 轮转 N 次; 正式指标取
    各态最小值, 同时记录中位数/四分位距/CV/峰值 RSS。
  - 语义护栏: C warmup 必须通过 spec 的权威值校验; raw 与 fat 的 warmup stdout/退出码必须
    逐字节一致; 两态退出码必须为 0。任一不成立, 该基准的比值标注为不可用。

用法:
  python3 scripts/bench_three_way.py                      # 默认 --set full
  python3 scripts/bench_three_way.py --set fast            # 快速档 (开发中频繁跑)
  python3 scripts/bench_three_way.py --set ptr --scale full
  python3 scripts/bench_three_way.py --bench AWFY/queen,BG/fasta
  python3 scripts/bench_three_way.py --source AWFY --set fast
  python3 scripts/bench_three_way.py --list-sets
  python3 scripts/bench_three_way.py --compile-only | --no-compile | --runs N | --pin 4

输出:
  bench/results/<set>.{md,csv}   命名集合的结果 (含指纹/协议/commit)
  bench/results/partial.{md,csv} --bench/--source 的临时子集, 不覆盖集合结果

独立脚本: 不触碰 scripts/run_tests.py; 不修改基准源码 (fast 档只在 build/ 下生成副本)。
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

ROOT = Path(__file__).resolve().parent.parent
LIB = ROOT / "lib" / "src"
BENCH_DIR = ROOT / "bench"
SETS_DIR = BENCH_DIR / "sets"
RESULTS_DIR = BENCH_DIR / "results"
OUT_DIR = ROOT / "build" / "bench"
SRC_BUILD_DIR = OUT_DIR / "src"
PARTIAL_MD = RESULTS_DIR / "partial.md"
PARTIAL_CSV = RESULTS_DIR / "partial.csv"

RESERVED_DIRS = {"sets", "results"}
DEFAULT_SET = "full"
SCALE_PROFILES = ("fast", "full")
SCALE_MARKER = "// bench-scale"


@dataclass(frozen=True)
class Check:
    """C 参考的权威结果 (声明式, 见 bench/<SOURCE>/specs/<name>.json)。"""

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
            parts.append(f"stdout 含 {needle!r}")
        if self.stdout_int_mod is not None:
            parts.append(f"stdout%{self.stdout_int_mod[0]}=={self.stdout_int_mod[1]}")
        return ", ".join(parts)


@dataclass(frozen=True)
class Scale:
    """规模档: 源里 `// bench-scale` 标记行的默认值与快速档值, 以及 C 侧如何接收。"""

    default: int
    fast: int | None = None
    c: str = "none"  # "argv" = 把规模值作为 argv 传给 C 参考; "none" = C 不支持缩放


@dataclass(frozen=True)
class BenchSpec:
    """一个三态基准: 源码 + C 参考 + spec (argv/权威值/tag/规模)。"""

    name: str
    source: str
    fat_src: Path
    raw_src: Path
    c_src: Path
    argv: tuple[str, ...]
    check: Check
    tags: tuple[str, ...]
    note: str
    scale: Scale | None = None

    @property
    def key(self) -> str:
        return f"{self.source}/{self.name}"

    @property
    def raw_override(self) -> bool:
        return self.raw_src != self.fat_src

    def scale_value(self, profile: str) -> int | None:
        """该档要写入源副本的规模值; None = 用源里的默认值 (full 档总是 None)。"""
        if self.scale is None or profile != "fast":
            return None
        return self.scale.fast

    def run_argv(self, profile: str) -> tuple[str, ...]:
        """三态共用的运行参数 (含 fast 档传给 C 参考的规模值)。"""
        value = self.scale_value(profile)
        if value is not None and self.scale is not None and self.scale.c == "argv":
            return (*self.argv, str(value))
        return self.argv


@dataclass(frozen=True)
class SetDef:
    """集合: 成员模式 (精确键 / `SOURCE/*` / `tag:x` / `*`) + 默认规模档与测量次数。"""

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
    runs: int
    yian_stdout_match: bool
    c_check_ok: bool
    exit_codes: dict[str, int]


def _load_json(path: Path, what: str) -> dict[str, object]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"[{what}] 读取 {path} 失败: {exc}") from exc
    if not isinstance(data, dict):
        raise SystemExit(f"[{what}] {path}: 顶层必须是 JSON 对象")
    return data


def _as_str_list(value: object, where: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise SystemExit(f"[spec] {where}: 期望字符串数组, 实际 {value!r}")
    return tuple(value)


def load_spec(
    spec_path: Path, source: str, name: str, fat_src: Path, raw_src: Path, c_src: Path
) -> BenchSpec:
    data = _load_json(spec_path, "spec")
    if data.get("name") != name:
        raise SystemExit(f"[spec] {spec_path}: name 应为 {name!r}, 实际 {data.get('name')!r}")
    if data.get("source") != source:
        raise SystemExit(f"[spec] {spec_path}: source 应为 {source!r}, 实际 {data.get('source')!r}")
    check_raw = data.get("check")
    if not isinstance(check_raw, dict):
        raise SystemExit(f"[spec] {spec_path}: 缺少 check 对象")
    int_mod_raw = check_raw.get("stdout_int_mod")
    int_mod: tuple[int, int] | None = None
    if int_mod_raw is not None:
        if not isinstance(int_mod_raw, list) or len(int_mod_raw) != 2 or any(
            not isinstance(item, int) for item in int_mod_raw
        ):
            raise SystemExit(f"[spec] {spec_path}: stdout_int_mod 期望 [模, 期望余数]")
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
            raise SystemExit(f"[spec] {spec_path}: scale 需要 default (快速档值 fast 可选)")
        fast_raw = scale_raw.get("fast")
        scale = Scale(
            default=int(scale_raw["default"]),
            fast=int(fast_raw) if fast_raw is not None else None,
            c=str(scale_raw.get("c", "none")),
        )
        if scale.c not in ("argv", "none"):
            raise SystemExit(f"[spec] {spec_path}: scale.c 只能是 \"argv\" 或 \"none\"")
    return BenchSpec(
        name=name,
        source=source,
        fat_src=fat_src,
        raw_src=raw_src,
        c_src=c_src,
        argv=_as_str_list(data.get("argv"), f"{spec_path}: argv"),
        check=check,
        tags=_as_str_list(data.get("tags"), f"{spec_path}: tags"),
        note=str(data.get("note", "")),
        scale=scale,
    )


def discover() -> list[BenchSpec]:
    """发现 bench/<SOURCE>/{an,c,specs} 下的三态基准。

    没有 `c/` 的来源 (纯分配器专项 bench/ALLOC) 不在这里发现, 由 scripts/bench_allocator.py 负责。
    """
    specs: list[BenchSpec] = []
    for source_dir in sorted(
        path for path in BENCH_DIR.iterdir() if path.is_dir() and path.name not in RESERVED_DIRS
    ):
        an_dir, c_dir, spec_dir = source_dir / "an", source_dir / "c", source_dir / "specs"
        if not an_dir.is_dir() or not c_dir.is_dir():
            continue
        fat: dict[str, Path] = {}
        raw: dict[str, Path] = {}
        for path in sorted(an_dir.glob("*.an")):
            if path.name.endswith(RAW_SUFFIX):
                raw[path.name[: -len(RAW_SUFFIX)]] = path
            else:
                fat[path.stem] = path
        if not fat:
            raise SystemExit(f"[discover] {an_dir} 下没有 .an 基准")
        orphans = sorted(set(raw) - set(fat))
        if orphans:
            raise SystemExit(f"[discover] 裸态覆盖源缺少同名胖态源: {', '.join(orphans)}")
        for name in sorted(fat):
            c_src = c_dir / f"{name}.c"
            if not c_src.exists():
                raise SystemExit(f"[discover] {source_dir.name}/{name} 缺少 C 参考: {c_src}")
            spec_path = spec_dir / f"{name}.json"
            if not spec_path.exists():
                raise SystemExit(f"[discover] {source_dir.name}/{name} 缺少 spec: {spec_path}")
            specs.append(
                load_spec(spec_path, source_dir.name, name, fat[name], raw.get(name, fat[name]), c_src)
            )
    if not specs:
        raise SystemExit(f"[discover] {BENCH_DIR} 下没有带 C 参考与 spec 的基准")
    return specs


def load_sets() -> dict[str, SetDef]:
    """读取 bench/sets/*.json。"""
    if not SETS_DIR.is_dir():
        raise SystemExit(f"[sets] 缺少目录 {SETS_DIR}")
    sets: dict[str, SetDef] = {}
    for path in sorted(SETS_DIR.glob("*.json")):
        data = _load_json(path, "sets")
        name = str(data.get("name", path.stem))
        scale = str(data.get("scale", "full"))
        if scale not in SCALE_PROFILES:
            raise SystemExit(f"[sets] {path}: scale 只能是 {'/'.join(SCALE_PROFILES)}")
        sets[name] = SetDef(
            name=name,
            scale=scale,
            runs=int(data.get("runs", DEFAULT_RUNS)),
            include=_as_str_list(data.get("include", ["*"]), f"{path}: include"),
            exclude=_as_str_list(data.get("exclude"), f"{path}: exclude"),
            note=str(data.get("note", "")),
        )
    if DEFAULT_SET not in sets:
        raise SystemExit(f"[sets] 缺少默认集合 {DEFAULT_SET}")
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
    """把集合定义展开成基准列表, 并校验模式确实命中 (拼错集合项要立刻报错)。"""
    for pattern in (*set_def.include, *set_def.exclude):
        if not any(_match(pattern, spec) for spec in specs):
            raise SystemExit(f"[select] 集合 {set_def.name} 的成员 {pattern!r} 没有匹配任何基准")
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
            raise SystemExit(f"[select] 未知基准: {', '.join(unknown)}")
        chosen = [spec for spec in chosen if spec.key in wanted or spec.name in wanted]
    if not chosen:
        raise SystemExit("[select] 选出的基准为空 (检查 --set/--scale/--source/--bench)")
    return sorted(chosen, key=lambda spec: spec.key)


_SCALE_LINE_RE = re.compile(
    r"^(?P<head>\s*let\s+[A-Za-z_]\w*\s*:\s*[A-Za-z_]\w*\s*=\s*)(?P<value>\d+)(?P<tail>;\s*"
    + re.escape(SCALE_MARKER)
    + r".*)$",
    re.MULTILINE,
)


def materialize(spec: BenchSpec, profile: str) -> tuple[Path, Path]:
    """返回该规模档下的 (胖态源, 裸态源)。

    fast 档且 spec 声明了 `scale.fast` 时, 把 `// bench-scale` 标记行的数字替换掉, 生成
    `build/bench/src/<profile>/<SOURCE>/<name>.an` 作为构建副本; 否则直接用仓库里的源。
    """
    value = spec.scale_value(profile)
    if value is None:
        return spec.fat_src, spec.raw_src
    out_dir = SRC_BUILD_DIR / profile / spec.source
    out_dir.mkdir(parents=True, exist_ok=True)

    def emit(src: Path) -> Path:
        text = src.read_text(encoding="utf-8")
        patched, count = _SCALE_LINE_RE.subn(
            lambda match: f"{match.group('head')}{value}{match.group('tail')}", text
        )
        if count != 1:
            raise SystemExit(
                f"[scale] {src}: 需要恰好 1 行 `{SCALE_MARKER}` 标记, 实际 {count} 行"
            )
        dst = out_dir / src.name
        dst.write_text(patched, encoding="utf-8")
        return dst

    fat = emit(spec.fat_src)
    raw = emit(spec.raw_src) if spec.raw_override else fat
    return fat, raw


def spec_bin(spec: BenchSpec, state: str) -> Path:
    return OUT_DIR / STATE_DIR[state] / f"{spec.source}_{spec.name}"


def compile_c(spec: BenchSpec) -> Path:
    """编译 C 参考 (clang -O2 -lm); 失败即终止 (C 基线失效, 不做部分结果)。"""
    binary = spec_bin(spec, "c")
    binary.parent.mkdir(parents=True, exist_ok=True)
    res = subprocess.run(
        [C_COMPILER, *C_FLAGS, str(spec.c_src), "-o", str(binary)],
        capture_output=True,
        text=True,
        cwd=ROOT,
    )
    if res.returncode != 0:
        raise SystemExit(
            f"[compile] {spec.key} (c) 失败 ({C_COMPILER} {' '.join(C_FLAGS)}):\n{res.stdout}\n{res.stderr}"
        )
    return binary


def compile_an(spec: BenchSpec, profile: str, raw: bool) -> Path:
    """编译单态 YIAN 基准; 失败即终止 (编译错误属于基线失效, 不做部分结果)。"""
    state = "raw" if raw else "fat"
    binary = spec_bin(spec, state)
    fat_src, raw_src = materialize(spec, profile)
    src = raw_src if raw else fat_src
    cmd = [sys.executable, "-m", "compiler.main", OPT_LEVEL]
    if raw:
        cmd.append("--raw-pointers")
    cmd += [str(LIB), str(src), "-o", str(binary)]
    binary.parent.mkdir(parents=True, exist_ok=True)
    res = subprocess.run(cmd, capture_output=True, text=True, cwd=ROOT)
    if res.returncode != 0:
        raise SystemExit(f"[compile] {spec.key} ({state}) 失败:\n{res.stdout}\n{res.stderr}")
    return binary


OPT_LEVEL = "-O2"
C_COMPILER = "clang"
C_FLAGS = ["-O2", "-lm"]
TIME_BIN = "/usr/bin/time"
DEFAULT_RUNS = 5
DEFAULT_MAX_STATE_SEC = 120.0
RAW_SUFFIX = ".raw.an"

STATES = ("c", "raw", "fat")
STATE_DIR = {"c": "cbin", "raw": "yraw", "fat": "yfat"}

_RSS_RE = re.compile(r"Maximum resident set size \(kbytes\): (\d+)")


def _env_plain() -> dict[str, str]:
    env = os.environ.copy()
    env["LANG"] = "C"
    return env


def _run(binary: Path, argv: list[str], pin: int | None) -> tuple[float, int, str, int]:
    """运行一次: 返回 (墙钟 ms, 峰值 RSS KB, stdout, 退出码)。"""
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
            f"[run] {binary}: 无法从 /usr/bin/time -v 输出解析 Maximum resident set size:\n"
            f"{res.stderr}"
        )
    return wall_ms, int(match.group(1)), res.stdout, res.returncode


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
    except statistics.StatisticsError:  # 单样本
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
    """三态逐次轮转测量: warmup 各 1 次 → 按 C→raw→fat 轮转 used_runs 次。

    三态共用同一份 argv (fast 档下含传给 C 参考的规模值); YIAN 侧的规模已经在编译期
    由源副本固化, 因此两态跑的是与 C 相同的工作量。
    """
    argv = list(spec.run_argv(profile))
    bins = {state: spec_bin(spec, state) for state in STATES}

    t0 = time.monotonic()
    warm = {state: _run(bins[state], argv, pin) for state in STATES}
    warm_sec = time.monotonic() - t0

    c_check_ok = spec.check.ok(warm["c"][3], warm["c"][2])
    if not c_check_ok:
        print(
            f"[{spec.key}] 警告: C 基线未通过权威值校验 "
            f"(exit={warm['c'][3]}, stdout={warm['c'][2].strip()!r}); 该基准的 C 比值不可用",
            file=sys.stderr,
        )
    yian_stdout_match = warm["raw"][2] == warm["fat"][2] and warm["raw"][3] == warm["fat"][3]
    if not yian_stdout_match:
        print(
            f"[{spec.key}] 警告: 两态 warmup 的 stdout/退出码不一致 "
            f"(raw exit={warm['raw'][3]}, fat exit={warm['fat'][3]})",
            file=sys.stderr,
        )
    for state in ("raw", "fat"):
        if warm[state][3] != 0:
            print(
                f"[{spec.key}] 警告: {state} 态 warmup 退出码 {warm[state][3]} (应为 0)",
                file=sys.stderr,
            )

    used_runs = runs
    if warm_sec > max_state_sec and runs > 3:
        used_runs = 3
        print(
            f"[{spec.key}] warmup {warm_sec:.1f}s > {max_state_sec:.0f}s, "
            f"测量次数降到 {used_runs}",
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
        runs=used_runs,
        yian_stdout_match=yian_stdout_match,
        c_check_ok=c_check_ok,
        exit_codes={state: warm[state][3] for state in STATES},
    )


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


def _llvmlite_info() -> tuple[str, str]:
    """返回 (llvmlite 版本, LLVM 版本); 环境不可用时返回 ("n/a", "n/a")。"""
    try:
        import llvmlite  # noqa: PLC0415
        import llvmlite.binding as llvm_binding  # noqa: PLC0415
    except ImportError:
        return "n/a", "n/a"
    info = getattr(llvm_binding, "llvm_version_info", None)
    llvm_ver = ".".join(str(part) for part in info) if info else "n/a"
    return str(llvmlite.__version__), llvm_ver


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


def machine_fingerprint(pin: int | None, runs: int, bench_set: str, profile: str) -> dict[str, str]:
    llvmlite_ver, llvm_ver = _llvmlite_info()
    return {
        "set": bench_set,
        "scale": profile,
        "hostname": platform.node() or "unknown",
        "machine": platform.machine(),
        "cpu_count": str(os.cpu_count() or 0),
        "clang": _tool_version(C_COMPILER),
        "cflags": " ".join(C_FLAGS),
        "python": platform.python_version(),
        "llvmlite": llvmlite_ver,
        "llvm": llvm_ver,
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
    """C 基线校验与两态语义护栏都通过时, 该基准的 C 比值才可用。"""
    return row.c_check_ok and row.yian_stdout_match


def render_md(rows: list[Row], fingerprint: dict[str, str]) -> str:
    lines: list[str] = []
    lines.append(
        f"# C / raw / fat 三态实测结果 — 集合 `{fingerprint['set']}` (规模档 `{fingerprint['scale']}`)\n"
    )
    lines.append(
        "由 `scripts/bench_three_way.py` 生成; 三态同一算法与规模, 差异只在实现与指针表示"
        " (C 为 clang `-O2` 参考实现)。集合成员与规模档见 `bench/sets/<set>.json` 与各基准的"
        " `specs/<name>.json`。\n"
    )
    lines.append("## 环境指纹\n")
    lines.append("| 项 | 值 |")
    lines.append("| --- | --- |")
    lines.append(f"| 主机 | {fingerprint['hostname']} |")
    lines.append(f"| 架构 / 逻辑核 | {fingerprint['machine']} / {fingerprint['cpu_count']} |")
    lines.append(f"| C 编译器 | `{fingerprint['clang']}` (`{fingerprint['cflags']}`) |")
    lines.append(f"| Python | {fingerprint['python']} |")
    lines.append(
        f"| llvmlite | `{fingerprint['llvmlite']}` (LLVM {fingerprint['llvm']}) |"
    )
    lines.append(f"| YIAN 优化级 | `{fingerprint['opt']}` (两态相同) |")
    lines.append(f"| 绑核 | `taskset -c {fingerprint['pin']}` |")
    lines.append(f"| 每态测量次数 | {fingerprint['runs']} (另加 1 次 warmup) |")
    lines.append(f"| 集合 / 规模档 | `{fingerprint['set']}` / `{fingerprint['scale']}` |")
    lines.append(f"| commit | `{fingerprint['commit']}` |")
    lines.append(f"| 日期 | {fingerprint['date']} |")
    lines.append("")

    lines.append("## 结果\n")
    lines.append(
        "| 基准 | C 最小 (ms) | raw 最小 (ms) | fat 最小 (ms) | raw/C | fat/C | fat/raw "
        "| C 峰值 RSS (MB) | raw 峰值 RSS (MB) | fat 峰值 RSS (MB) | 裸态源 | raw/fat stdout 一致 | C 校验 |"
    )
    lines.append("| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- | --- | --- |")
    for row in rows:
        ratio_rc = _ratio(row, "raw", "c")
        ratio_fc = _ratio(row, "fat", "c")
        ratio_fr = _ratio(row, "fat", "raw")
        raw_src = f"`{row.spec.raw_src.name}`" if row.spec.raw_override else "同名"
        c_flag = "是" if row.c_check_ok else "**否**"
        if not _ratio_usable(row):
            ratio_rc = ratio_fc = float("nan")
        lines.append(
            f"| {row.spec.key} | {_fmt_ms(row.times['c'].min)} | {_fmt_ms(row.times['raw'].min)} "
            f"| {_fmt_ms(row.times['fat'].min)} | {ratio_rc:.2f}× | {ratio_fc:.2f}× | {ratio_fr:.2f}× "
            f"| {_fmt_mb(row.rss_mb['c'])} | {_fmt_mb(row.rss_mb['raw'])} | {_fmt_mb(row.rss_mb['fat'])} "
            f"| {raw_src} | {'是' if row.yian_stdout_match else '**否**'} | {c_flag} |"
        )
    usable = [row for row in rows if _ratio_usable(row)]
    geo_raw = _geomean([_ratio(row, "raw", "c") for row in usable])
    geo_fat = _geomean([_ratio(row, "fat", "c") for row in usable])
    if geo_raw is not None and geo_fat is not None:
        lines.append("")
        lines.append(
            f"几何平均比值 (C 为基线, n={len(usable)}): raw/C **{geo_raw:.2f}×** / "
            f"fat/C **{geo_fat:.2f}×** (比值可用 = C 校验通过且 raw/fat 语义一致)。"
        )
        totals = {state: sum(row.times[state].min for row in rows) / 1000.0 for state in STATES}
        lines.append(
            f"单趟全量耗时 (各基准最小值之和): C **{totals['c']:.1f} s** / "
            f"raw **{totals['raw']:.1f} s** / fat **{totals['fat']:.1f} s**。"
        )
    lines.append("")
    lines.append(
        "比值均为最小值之比; 各基准不可加。`--raw-pointers` 关闭全部指针安全检查与锁槽/帧锁"
        "发射, 因此 raw/C 是裸态实现相对 C 的开销, fat/raw 是完整胖指针相对裸指针的总开销。\n"
    )

    lines.append("## 重复性\n")
    lines.append(
        "| 基准 | C 中位 | C 最小 | C 最大 | C CV% | raw 中位 | raw 最小 | raw 最大 | raw CV% "
        "| fat 中位 | fat 最小 | fat 最大 | fat CV% | 次数 |"
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
        "正式指标取**最小值**: 机器处于高压/热态时, 同一二进制重复运行会落在相差约 20% 的"
        "两个性能状态上 (进程级内存布局/频率假象, 与代码无关)。最小值估计无干扰性能, "
        "中位数与 CV 用于判断重复性。\n"
    )

    lines.append("## 协议\n")
    lines.append(
        f"- 编译: C `{C_COMPILER} {' '.join(C_FLAGS)} bench/<SOURCE>/c/<name>.c`; "
        f"YIAN `yianc {OPT_LEVEL} lib/src <src>`, 裸态追加 `--raw-pointers`。"
    )
    lines.append(
        "- 规模与 argv: 见各基准 `specs/<name>.json` 的 `argv` (如 `cd 100 80`、`richards 2400`) "
        "与 `scale`; fast 档对声明了 `scale.fast` 的基准生成缩小规模的构建副本, 并把同一数值"
        "作为 argv 传给 C 参考, 保证三态工作量一致。"
    )
    lines.append(
        "- 测量: 每基准三态各 1 次 warmup (不计入样本) 后按 C→raw→fat **逐次轮转**测量; "
        "正式指标取各态最小值, 并记录中位数/四分位距/CV/峰值 RSS。"
    )
    lines.append(
        f"- 降噪: `taskset -c {fingerprint['pin']}`; 逐次轮转消除跨时段漂移; 三态产物同目录、"
        "等长路径 (argv[0] 长度影响分配密集型基准的进程布局)。"
    )
    lines.append(
        "- 语义护栏: C warmup 必须通过 spec 的权威值校验 (见 `specs/<name>.json` 的 `check`); "
        "raw 与 fat 的 stdout/退出码必须逐字节一致。报告中标 `否` 即该基准比值不可用。"
    )
    return "\n".join(lines).rstrip("\n") + "\n"


def write_csv(rows: list[Row], fingerprint: dict[str, str], path: Path) -> None:
    header = (
        f"# bench/results/{fingerprint['set']}.csv — C / raw / fat 三态性能结果 "
        "(scripts/bench_three_way.py 生成)"
    )
    comments = [
        header,
        "# 列: bench 形如 <SOURCE>/<name>; <state>_min_ms/<state>_med_ms/<state>_cv_pct 各态最小值"
        "(正式指标)/中位数/变异系数, state ∈ {c,raw,fat}; ratio_raw_c = raw_min/c_min, "
        "ratio_fat_c = fat_min/c_min, ratio_fat_raw = fat_min/raw_min; <state>_rss_mb 峰值常驻; "
        "runs, stdout_match (raw/fat), c_check (C 权威值), c_argv, raw_source",
        f"# fingerprint: hostname={fingerprint['hostname']} machine={fingerprint['machine']} "
        f"cpu_count={fingerprint['cpu_count']} clang={fingerprint['clang']} cflags={fingerprint['cflags']}",
        f"# env: python={fingerprint['python']} llvmlite={fingerprint['llvmlite']} "
        f"llvm={fingerprint['llvm']} opt={fingerprint['opt']} pin={fingerprint['pin']} "
        f"runs={fingerprint['runs']}",
        f"# set: {fingerprint['set']}  scale: {fingerprint['scale']}",
        f"# commit: {fingerprint['commit']}",
        f"# date: {fingerprint['date']}",
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
        raw_src = row.spec.raw_src.name if row.spec.raw_override else row.spec.fat_src.name
        argv = " ".join(row.spec.run_argv(fingerprint["scale"]))
        fields = [row.spec.key]
        for state in STATES:
            stats = row.times[state]
            fields += [f"{stats.min:.1f}", f"{stats.med:.1f}", f"{stats.cv:.2f}"]
        fields += [
            f"{_ratio(row, 'raw', 'c'):.3f}",
            f"{_ratio(row, 'fat', 'c'):.3f}",
            f"{_ratio(row, 'fat', 'raw'):.3f}",
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
    parser = argparse.ArgumentParser(description="C / raw / fat 三态性能实测 (以 C 为基线)")
    parser.add_argument("--set", dest="bench_set", default=DEFAULT_SET,
                        help=f"集合名 (默认 {DEFAULT_SET}; --list-sets 查看)")
    parser.add_argument("--scale", choices=SCALE_PROFILES,
                        help="规模档 (默认取集合的 scale)")
    parser.add_argument("--source", help="只测指定来源 (逗号分隔, 如 AWFY,BG)")
    parser.add_argument("--bench", dest="bench_keys",
                        help="只测指定基准 (SOURCE/name 或 name, 逗号分隔)")
    parser.add_argument("--names", dest="bench_keys", help=argparse.SUPPRESS)  # 旧名, 保留兼容
    parser.add_argument("--list-sets", action="store_true", help="列出集合与成员数后退出")
    parser.add_argument("--runs", type=int, default=None,
                        help="每态测量次数 (默认取集合的 runs)")
    parser.add_argument("--pin", type=int, help="taskset 绑定的 CPU 编号")
    parser.add_argument("--max-state-sec", type=float, default=DEFAULT_MAX_STATE_SEC,
                        help="三态 warmup 合计超过该秒数则测量次数降到 3 (fast 档不降次)")
    parser.add_argument("--no-compile", action="store_true", help="不重新编译, 复用已有二进制")
    parser.add_argument("--compile-only", action="store_true", help="只编译不测量")
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

    if args.bench_set not in sets:
        raise SystemExit(
            f"[args] 未知集合 {args.bench_set!r}; 可用: {', '.join(sorted(sets))}"
        )
    set_def = sets[args.bench_set]
    profile = args.scale or set_def.scale
    bench_filter = tuple(item.strip() for item in (args.bench_keys or "").split(",") if item.strip())
    source_filter = tuple(item.strip() for item in (args.source or "").split(",") if item.strip())
    chosen = resolve_set(specs, set_def, bench_filter, source_filter)
    runs = args.runs if args.runs is not None else set_def.runs

    print(
        f"[bench] 集合 {set_def.name} 规模档 {profile} 基准 {len(chosen)} 项 "
        f"测量 {runs} 次 (fast 档不降次)",
        file=sys.stderr,
    )
    if not args.no_compile:
        for spec in chosen:
            compile_c(spec)
            compile_an(spec, profile, raw=False)
            compile_an(spec, profile, raw=True)
            scale_value = spec.scale_value(profile)
            scale_note = f" scale={scale_value}" if scale_value is not None else ""
            print(f"[compile] {spec.key} ok{scale_note}", file=sys.stderr)
    if args.compile_only:
        return 0

    fingerprint = machine_fingerprint(args.pin, runs, set_def.name, profile)
    max_state_sec = float("inf") if profile == "fast" else args.max_state_sec
    rows: list[Row] = []
    for spec in chosen:
        row = measure_triple(spec, runs, args.pin, max_state_sec, profile)
        rows.append(row)
        scale_value = spec.scale_value(profile)
        scale_note = f" scale={scale_value}" if scale_value is not None else ""
        print(
            f"[measure] {spec.key}{scale_note}: C {row.times['c'].min:.1f}ms "
            f"raw {row.times['raw'].min:.1f}ms fat {row.times['fat'].min:.1f}ms "
            f"raw/C {_ratio(row, 'raw', 'c'):.2f}x fat/C {_ratio(row, 'fat', 'c'):.2f}x "
            f"({row.runs} runs)",
            file=sys.stderr,
        )

    # 临时子集 (--bench/--source) 不覆盖集合结果; 命名集合写入 results/<set>.{md,csv}
    partial = bool(bench_filter or source_filter)
    if partial:
        md_path, csv_path = PARTIAL_MD, PARTIAL_CSV
    else:
        md_path = RESULTS_DIR / f"{set_def.name}.md"
        csv_path = RESULTS_DIR / f"{set_def.name}.csv"
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    md_path.write_text(render_md(rows, fingerprint), encoding="utf-8")
    write_csv(rows, fingerprint, csv_path)
    print(f"[write] {md_path.relative_to(ROOT)}", file=sys.stderr)
    print(f"[write] {csv_path.relative_to(ROOT)}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
