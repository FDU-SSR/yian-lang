#!/usr/bin/env bash
#
# Uninstall the YIAN compiler (`yianc`) and package manager (`anx`) from a
# Python environment.
#
# Usage: scripts/uninstall.sh [--python PATH]
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON="${PYTHON:-}"

usage() {
    cat <<'EOF'
Uninstall yianc and anx (the `yian` distribution).

Usage: scripts/uninstall.sh [options]

Options:
  --python PATH  Python interpreter to use (default: $PYTHON, then python3).
  -h, --help     Show this help.

Environment:
  PYTHON         Same as --python.

This removes the installed distribution only. It never deletes source files or
build output under the checkout, except for a leftover legacy *.egg-info
directory left behind by an old-style editable install.
EOF
}

die() { printf 'error: %s\n' "$*" >&2; exit 1; }

while [[ $# -gt 0 ]]; do
    case "$1" in
        --python)
            [[ $# -ge 2 ]] || die "--python requires a path"
            PYTHON="$2"; shift 2 ;;
        -h|--help) usage; exit 0 ;;
        *)         die "unknown option: $1 (try --help)" ;;
    esac
done

# ---- locate and validate the interpreter ------------------------------------
if [[ -z "$PYTHON" ]]; then
    if command -v python3 >/dev/null 2>&1; then
        PYTHON="python3"
    elif command -v python >/dev/null 2>&1; then
        PYTHON="python"
    else
        die "no python3 on PATH; pass --python PATH"
    fi
fi
command -v "$PYTHON" >/dev/null 2>&1 || die "python not found: $PYTHON"
"$PYTHON" -m pip --version >/dev/null 2>&1 || die "pip is unavailable for $PYTHON"

# ---- uninstall --------------------------------------------------------------
if "$PYTHON" -m pip show yian >/dev/null 2>&1; then
    "$PYTHON" -m pip uninstall --yes yian
else
    printf 'The "yian" distribution is not installed in %s.\n' \
        "$("$PYTHON" -c 'import sys; print(sys.executable)')"
fi

# ---- clean up leftovers -----------------------------------------------------
# Old-style editable installs dropped an egg-info directory in the checkout.
for dir in "$ROOT_DIR"/*.egg-info; do
    [[ -e "$dir" ]] || continue
    rm -rf "$dir"
    printf 'removed leftover %s\n' "$dir"
done

# ---- verify -----------------------------------------------------------------
STILL_PRESENT=0
for cmd in yianc anx; do
    if command -v "$cmd" >/dev/null 2>&1; then
        printf 'warning: %s is still on PATH (%s); it may belong to another environment.\n' \
            "$cmd" "$(command -v "$cmd")"
        STILL_PRESENT=1
    fi
done

if [[ "$STILL_PRESENT" -eq 0 ]]; then
    printf 'yianc and anx are no longer on PATH.\n'
fi
