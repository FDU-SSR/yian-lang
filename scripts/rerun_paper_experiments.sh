#!/usr/bin/env bash
# Reproduce the paper measurements in one run: CVE suite, three-state
# performance, and the ASan cross-comparison.
#
# Usage:
#   scripts/rerun_paper_experiments.sh [options]
#
# Options:
#   --runs N         measured runs per state/leg (default 5, or $SECL_RUNS)
#   --pin CPU        taskset CPU (default 4, or $SECL_PIN; 0 disables pinning)
#   --bench LIST     restrict both benchmark runners (comma separated keys)
#   --skip-cve       skip the CVE correctness suite
#   --skip-perf      skip the three-state performance run
#   --skip-asan      skip the ASan cross-comparison
#   --refresh-data   copy the new samples into ../data via ../refresh_data.py
#   -h, --help       show this help
#
# Results go to bench/results/; command output is also kept under
# build/paper-rerun/<timestamp>/run.log.
set -Eeuo pipefail

ROOT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
cd "$ROOT_DIR"

PYTHON_BIN=${YIAN_PYTHON:-python3}
RUNS=${SECL_RUNS:-5}
PIN=${SECL_PIN:-4}
BENCH=""
SKIP_CVE=0
SKIP_PERF=0
SKIP_ASAN=0
REFRESH_DATA=0

usage() {
    sed -n '2,20p' "$0"
}

while (($#)); do
    case "$1" in
        --runs) RUNS=$2; shift 2 ;;
        --pin) PIN=$2; shift 2 ;;
        --bench) BENCH=$2; shift 2 ;;
        --skip-cve) SKIP_CVE=1; shift ;;
        --skip-perf) SKIP_PERF=1; shift ;;
        --skip-asan) SKIP_ASAN=1; shift ;;
        --refresh-data) REFRESH_DATA=1; shift ;;
        -h|--help) usage; exit 0 ;;
        *) echo "unknown argument: $1" >&2; usage >&2; exit 2 ;;
    esac
done

if ((SKIP_CVE && SKIP_PERF && SKIP_ASAN)); then
    echo "every stage is skipped; nothing to do" >&2
    exit 2
fi

for command_name in "$PYTHON_BIN" clang taskset git file sha256sum; do
    command -v "$command_name" >/dev/null || {
        echo "required command not found: $command_name" >&2
        exit 2
    }
done

YIANC_BIN=${YIANC:-$(command -v yianc || true)}
if [[ -z "$YIANC_BIN" || ! -x "$YIANC_BIN" ]]; then
    echo "native yianc not found; set YIANC or install it with scripts/bootstrap_toolchain.sh" >&2
    exit 2
fi
if ! file -b "$YIANC_BIN" | grep -q ELF; then
    echo "native yianc must be an ELF executable: $YIANC_BIN" >&2
    exit 2
fi
# Benchmark compilation also needs the checkout's standard library as trust root.
export YIAN_LIB=${YIAN_LIB:-"$ROOT_DIR/lib/src"}
export YIANC="$YIANC_BIN"

if [[ "$PIN" == "0" ]]; then
    PIN_ARG=()
    PIN_LABEL="none"
else
    PIN_ARG=(--pin "$PIN")
    PIN_LABEL="$PIN"
fi
BENCH_ARG=()
if [[ -n "$BENCH" ]]; then
    BENCH_ARG=(--bench "$BENCH")
fi

STAMP=$(date +%Y%m%d-%H%M%S)
RUN_DIR="$ROOT_DIR/build/paper-rerun/$STAMP"
mkdir -p "$RUN_DIR"
LOG_FILE="$RUN_DIR/run.log"
exec > >(tee "$LOG_FILE") 2>&1

echo "SecL paper experiment rerun"
echo "implementation_commit=$(git rev-parse HEAD)"
echo "yianc=$YIANC ($(sha256sum "$YIANC" | cut -d' ' -f1))"
echo "python=$("$PYTHON_BIN" --version 2>&1)"
echo "clang=$(clang --version | sed -n '1p')"
echo "runs=$RUNS pin=$PIN_LABEL bench=${BENCH:-paper set}"
echo

if ((SKIP_CVE == 0)); then
    echo "== CVE and safety suite =="
    "$PYTHON_BIN" scripts/run_tests.py --suite safety -q
    echo
fi

if ((SKIP_PERF == 0)); then
    echo "== three-state performance (C / raw / fat) =="
    "$PYTHON_BIN" bench/bench_three_way.py --set paper --runs "$RUNS" "${PIN_ARG[@]}" "${BENCH_ARG[@]}"
    echo
fi

if ((SKIP_ASAN == 0)); then
    echo "== ASan cross-comparison (C plain / C ASan / YIAN) =="
    "$PYTHON_BIN" bench/bench_asan.py --set paper --runs "$RUNS" \
        "${PIN_ARG[@]}" "${BENCH_ARG[@]}"
    echo
fi

echo "== summary =="
for artifact in bench/results/full.md bench/results/full.csv bench/results/asan.md \
                bench/results/asan.csv bench/results/asan-samples.txt; do
    if [[ -f "$artifact" ]]; then
        echo "wrote $artifact"
    fi
done
if [[ -f bench/results/asan.md ]]; then
    grep -m1 'Geometric means' bench/results/asan.md || true
fi

if ((REFRESH_DATA)); then
    if [[ -x "$ROOT_DIR/../refresh_data.py" ]]; then
        echo
        echo "== refresh paper data =="
        "$ROOT_DIR/../refresh_data.py"
    else
        echo "refresh_data.py not found; run it from the paper checkout" >&2
    fi
fi

echo
echo "log: ${LOG_FILE#"$ROOT_DIR/"}"
echo "next: review bench/results/*.md and update the numbers quoted in the paper"
