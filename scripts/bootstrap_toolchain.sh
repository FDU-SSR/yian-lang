#!/usr/bin/env bash
# Build and install native yianc/anx without relying on installed native tools.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BUILD_PYTHON="${PYTHON:-}"
PREFIX="${PREFIX:-}"

usage() {
    cat <<'EOF'
Build and install native yianc and anx from this checkout using the Python reference compiler.

Usage: scripts/bootstrap_toolchain.sh [options]

Options:
  --prefix PATH  Installation prefix (default: $HOME/.local; PREFIX is honored).
  --python PATH  Python 3.12+ with llvmlite 0.49+ and LLVM 22 (default: python3).
  -h, --help     Show this help.

Requires llvm-config, clang and llvm-ar from LLVM 22. The workflow builds a
temporary seed anx, native yianc, then final anx; it does not use installed
yianc/anx commands and does not install the language server.
EOF
}

die() {
    printf 'error: %s\n' "$*" >&2
    exit 2
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --prefix)
            [[ $# -ge 2 ]] || die "--prefix requires a path"
            PREFIX="$2"
            shift 2
            ;;
        --python)
            [[ $# -ge 2 ]] || die "--python requires a path"
            BUILD_PYTHON="$2"
            shift 2
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            die "unknown option: $1 (try --help)"
            ;;
    esac
done

if [[ -z "$BUILD_PYTHON" ]]; then
    BUILD_PYTHON="python3"
fi
command -v "$BUILD_PYTHON" >/dev/null 2>&1 || die "Python was not found: $BUILD_PYTHON"
BUILD_PYTHON="$(command -v "$BUILD_PYTHON")"

if [[ -z "$PREFIX" ]]; then
    [[ -n "${HOME:-}" ]] || die "HOME is unset; pass --prefix PATH"
    PREFIX="$HOME/.local"
fi
if [[ "$PREFIX" != /* ]]; then
    PREFIX="$PWD/$PREFIX"
fi

exec "$BUILD_PYTHON" "$ROOT_DIR/scripts/_toolchain/cold.py" \
    --root "$ROOT_DIR" --prefix "$PREFIX" --python "$BUILD_PYTHON"
