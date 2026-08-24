#!/usr/bin/env bash
# Reproduce and freeze all correctness, three-state, and ASan paper results.
#
# Run the complete protocol on the designated experiment machine:
#   ./scripts/rerun_paper_experiments.sh
#
# The script fixes CPU 4 and validates the committed machine/toolchain profile
# before running. Use --print-fingerprint only to diagnose a profile mismatch.

set -Eeuo pipefail

ROOT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
cd "$ROOT_DIR"

PYTHON_BIN=${YIAN_PYTHON:-python3}
readonly PIN=4
readonly EXPECTED_FINGERPRINT_RECORD='architecture=x86_64
logical_cpus=28
cpu_model=Intel(R) Core(TM) i7-14700
clang=Ubuntu clang version 20.1.8 (++20250708082409+6fb913d3e2ec-1~exp1~20250708202428.132)'
PRINT_FINGERPRINT=0

usage() {
    sed -n '2,8p' "$0"
}

while (($#)); do
    case "$1" in
        --print-fingerprint)
            PRINT_FINGERPRINT=1
            shift
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            echo "unknown argument: $1" >&2
            usage >&2
            exit 2
            ;;
    esac
done

machine_record() {
    printf 'kernel=%s\n' "$(uname -sr)"
    machine_fingerprint_record
}

machine_fingerprint_record() {
    printf 'architecture=%s\n' "$(uname -m)"
    printf 'logical_cpus=%s\n' "$(getconf _NPROCESSORS_ONLN)"
    printf 'cpu_model=%s\n' "$(lscpu | awk -F: '/Model name/ {sub(/^[[:space:]]+/, "", $2); print $2; exit}')"
    printf 'clang=%s\n' "$(clang --version | sed -n '1p')"
}

for command_name in uname getconf lscpu awk sed clang sha256sum taskset git; do
    command -v "$command_name" >/dev/null || {
        echo "required command not found: $command_name" >&2
        exit 2
    }
done

MACHINE_RECORD=$(machine_record)
FINGERPRINT_RECORD=$(machine_fingerprint_record)
MACHINE_FINGERPRINT=$(printf '%s\n' "$FINGERPRINT_RECORD" | sha256sum | awk '{print $1}')
EXPECTED_FINGERPRINT=$(printf '%s\n' "$EXPECTED_FINGERPRINT_RECORD" | sha256sum | awk '{print $1}')

if ((PRINT_FINGERPRINT)); then
    printf '%s\n' "$MACHINE_RECORD"
    printf 'fingerprint_sha256=%s\n' "$MACHINE_FINGERPRINT"
    exit 0
fi

if [[ "$EXPECTED_FINGERPRINT" != "$MACHINE_FINGERPRINT" ]]; then
    echo "designated experiment-machine profile mismatch" >&2
    echo "expected fingerprint: $EXPECTED_FINGERPRINT" >&2
    printf '%s\n' "$EXPECTED_FINGERPRINT_RECORD" >&2
    echo "actual fingerprint:   $MACHINE_FINGERPRINT" >&2
    printf '%s\n' "$MACHINE_RECORD" >&2
    exit 2
fi
taskset -c "$PIN" true >/dev/null

PYTHON_BIN=$(command -v "$PYTHON_BIN") || {
    echo "Python interpreter not found: ${YIAN_PYTHON:-python3}" >&2
    exit 2
}
"$PYTHON_BIN" - <<'PY'
import llvmlite
import matplotlib
import numpy
import pandas
PY

PYRIGHT_BIN=${YIAN_PYRIGHT:-"$(dirname "$PYTHON_BIN")/pyright"}
if [[ ! -x "$PYRIGHT_BIN" ]]; then
    PYRIGHT_BIN=$(command -v pyright || true)
fi
if [[ -z "$PYRIGHT_BIN" ]]; then
    echo "pyright not found; install dependencies with:" >&2
    echo "  $PYTHON_BIN -m pip install -r requirements.txt" >&2
    exit 2
fi

DIRTY_TRACKED=$(git status --porcelain --untracked-files=no -- . \
    ':(exclude)scripts/rerun_paper_experiments.sh' \
    ':(exclude)paper/README.md' \
    ':(exclude)requirements.txt')
if [[ -n "$DIRTY_TRACKED" ]]; then
    echo "refusing to overwrite paper data from a dirty tracked worktree" >&2
    printf '%s\n' "$DIRTY_TRACKED" >&2
    exit 2
fi

TRACKED_DATA=(
    docs/performance.csv
    docs/shootout-results.md
    paper/data/performance.csv
    paper/data/shootout-results.md
    paper/data/asan-results.md
)
SUCCESS=0
restore_on_failure() {
    status=$?
    trap - EXIT
    if ((SUCCESS == 0)); then
        git restore -- "${TRACKED_DATA[@]}"
        echo "run failed or was interrupted; tracked experiment data restored" >&2
    fi
    exit "$status"
}
trap restore_on_failure EXIT

RUN_STAMP=$(date +%Y%m%d-%H%M%S)
RUN_DIR="$ROOT_DIR/build/paper-rerun/$RUN_STAMP"
mkdir -p "$RUN_DIR"
LOG_FILE="$RUN_DIR/run.log"
exec > >(tee "$LOG_FILE") 2>&1

echo "SecL paper experiment rerun"
echo "implementation_commit=$(git rev-parse HEAD)"
echo "python=$($PYTHON_BIN --version 2>&1)"
echo "pin=$PIN"
printf '%s\n' "$MACHINE_RECORD"
echo "fingerprint_sha256=$MACHINE_FINGERPRINT"

"$PYTHON_BIN" scripts/run_tests.py -q
"$PYTHON_BIN" scripts/run_fat_tests.py -q
"$PYTHON_BIN" scripts/run_raw_tests.py -q
"$PYTHON_BIN" scripts/run_fat_cve.py -q
"$PYTHON_BIN" tests/unit/test_lockmech.py
"$PYTHON_BIN" scripts/verify_bench_c.py
"$PYTHON_BIN" scripts/check_raw_free_ir.py
"$PYTHON_BIN" scripts/check_frame_lock_ir.py
"$PYRIGHT_BIN" --pythonpath "$PYTHON_BIN"

"$PYTHON_BIN" scripts/bench_fat.py --suite shootout --runs 5 --pin "$PIN"
"$PYTHON_BIN" scripts/bench_fat.py --asan --runs 5 --pin "$PIN"

"$PYTHON_BIN" - <<'PY'
import re
from pathlib import Path

shootout = Path("build/bench/shootout-results.md").read_text(encoding="utf-8")
asan = Path("build/bench/asan-results.md").read_text(encoding="utf-8")

shootout_samples = [
    line for line in shootout.split("## 6) 原始样本", 1)[1].splitlines()
    if line.startswith("- ") and " / " in line
]
assert len(shootout_samples) == 42, "shootout must contain 14 x 3 raw-sample rows"
assert all(len(re.findall(r"\d+\.\d+ \(\d+\)", line)) == 5 for line in shootout_samples), \
    "every shootout leg must contain five raw samples"
assert asan.count("(5 runs):") == 43, "ASan report must contain 42 primary plus one sensitivity leg"
PY

cp build/bench/shootout-results.md docs/shootout-results.md

FREEZE_DATE=$(date -I)
IMPLEMENTATION_COMMIT=$(git rev-parse HEAD)

PERF_TMP="$RUN_DIR/performance.csv"
{
    echo "# 论文数据冻结快照；来源 docs/performance.csv；冻结日期 $FREEZE_DATE"
    echo "# 当前实现提交: $IMPLEMENTATION_COMMIT；协议: CPU $PIN, warmup 1, runs 5, check→nocheck→raw 紧邻"
    cat docs/performance.csv
} > "$PERF_TMP"
cp "$PERF_TMP" paper/data/performance.csv

SHOOTOUT_TMP="$RUN_DIR/shootout-results.md"
{
    echo "> **论文数据冻结快照**"
    echo ">"
    echo "> 来源：build/bench/shootout-results.md；冻结日期：$FREEZE_DATE；实现提交：$IMPLEMENTATION_COMMIT。"
    echo "> 协议：14 个 benchmark，CPU $PIN，1 次 warmup、每态 5 次正式样本，check→nocheck→raw 紧邻。"
    echo
    echo "---"
    cat build/bench/shootout-results.md
} > "$SHOOTOUT_TMP"
cp "$SHOOTOUT_TMP" paper/data/shootout-results.md

ASAN_TMP="$RUN_DIR/asan-results.md"
{
    echo "> **论文数据冻结快照**"
    echo ">"
    echo "> 来源：build/bench/asan-results.md；冻结日期：$FREEZE_DATE；实现提交：$IMPLEMENTATION_COMMIT。"
    echo "> 协议：CPU $PIN，1 次 warmup、每腿 5 次；C plain/ASan 为 clang -O2，SecL check 为 -O3。"
    echo
    echo "---"
    cat build/bench/asan-results.md
} > "$ASAN_TMP"
cp "$ASAN_TMP" paper/data/asan-results.md

"$PYTHON_BIN" paper/figures/fig1_cost_decomposition.py "$RUN_DIR/performance.pdf"
"$PYTHON_BIN" paper/figures/fig4_asan_compare.py "$RUN_DIR/asan.pdf"
git diff --check

SUCCESS=1
echo "rerun complete; frozen data updated"
echo "log=$LOG_FILE"
echo "validation_figures=$RUN_DIR/performance.pdf,$RUN_DIR/asan.pdf"
git status --short -- "${TRACKED_DATA[@]}"
