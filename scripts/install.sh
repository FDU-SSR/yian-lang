#!/usr/bin/env bash
#
# Install the YIAN compiler (`yianc`) and package manager (`anx`) into the
# active Python environment.
#
# The default install is editable: both commands point at this checkout, so
# edits under compiler/ and anx/ take effect without reinstalling. A regular
# (non-editable) install is also supported; it does not carry the standard
# library, so it needs YIAN_LIB (or YIAN_ROOT) at run time.
#
# Usage: scripts/install.sh [--regular] [--with-deps] [--user] [--python PATH]
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON="${PYTHON:-}"
WITH_DEPS=0
USER_INSTALL=0
REGULAR=0

usage() {
    cat <<'EOF'
Install yianc and anx into the active Python environment.

Usage: scripts/install.sh [options]

Options:
  --regular      Copy the packages instead of linking them (pip install .).
                 The installed commands then need YIAN_LIB or YIAN_ROOT to find
                 the standard library; the script prints the exact export line.
  --with-deps    Let pip resolve dependencies. Without it the environment is left
                 untouched and llvmlite must already be importable.
  --user         Install into the user site-packages instead of the environment.
  --python PATH  Python interpreter to use (default: $PYTHON, then python3).
  -h, --help     Show this help.

Environment:
  PYTHON         Same as --python.

The default install is editable: both commands point at this checkout, so edits
to compiler/ and anx/ take effect immediately, including newly added modules.
Only changes to [project.scripts], dependencies, or a new top-level package
require reinstalling.

A --regular install copies compiler/ and anx/ into site-packages without lib/,
so point the tools at a checkout first:

  export YIAN_LIB="/path/to/yian/lib/src"     # the source root itself
  export YIAN_ROOT="/path/to/yian"            # or a checkout root
EOF
}

die() { printf 'error: %s\n' "$*" >&2; exit 1; }

while [[ $# -gt 0 ]]; do
    case "$1" in
        --regular)   REGULAR=1; shift ;;
        --with-deps) WITH_DEPS=1; shift ;;
        --user)      USER_INSTALL=1; shift ;;
        --python)
            [[ $# -ge 2 ]] || die "--python requires a path"
            PYTHON="$2"; shift 2 ;;
        -h|--help)   usage; exit 0 ;;
        *)           die "unknown option: $1 (try --help)" ;;
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

"$PYTHON" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)' \
    || die "$("$PYTHON" -V 2>&1) is too old; YIAN needs Python >= 3.11 (tomllib)"
"$PYTHON" -m pip --version >/dev/null 2>&1 || die "pip is unavailable for $PYTHON"

if [[ "$WITH_DEPS" -eq 0 ]] && ! "$PYTHON" -c 'import llvmlite' >/dev/null 2>&1; then
    die "llvmlite is missing; re-run with --with-deps, or install requirements.txt first"
fi

# ---- assemble pip arguments -------------------------------------------------
if [[ "$REGULAR" -eq 1 ]]; then
    PIP_ARGS=("$ROOT_DIR")
else
    PIP_ARGS=(--editable "$ROOT_DIR")
fi

# Build against the installed setuptools when it is new enough, so installing
# also works without network access.
if "$PYTHON" -c '
import sys
try:
    import setuptools
    major, minor = (int(p) for p in setuptools.__version__.split(".")[:2])
except Exception:
    sys.exit(1)
sys.exit(0 if (major, minor) >= (68, 0) else 1)
' >/dev/null 2>&1; then
    PIP_ARGS+=(--no-build-isolation)
fi

[[ "$WITH_DEPS" -eq 0 ]] && PIP_ARGS+=(--no-deps)
[[ "$USER_INSTALL" -eq 1 ]] && PIP_ARGS+=(--user)

# ---- install ----------------------------------------------------------------
MODE="editable"
[[ "$REGULAR" -eq 1 ]] && MODE="regular"
printf 'Installing (%s) from %s with %s\n' \
    "$MODE" "$ROOT_DIR" "$("$PYTHON" -c 'import sys; print(sys.executable)')"
"$PYTHON" -m pip install "${PIP_ARGS[@]}"

# ---- verify entry points ----------------------------------------------------
SCRIPTS_DIR="$("$PYTHON" -c 'import sysconfig; print(sysconfig.get_path("scripts"))')"
MISSING=0
printf '\nEntry points:\n'
for cmd in yianc anx; do
    if command -v "$cmd" >/dev/null 2>&1; then
        printf '  %-6s -> %s\n' "$cmd" "$(command -v "$cmd")"
    else
        printf '  %-6s -> NOT on PATH\n' "$cmd"
        MISSING=1
    fi
done

if [[ "$MISSING" -eq 1 ]]; then
    printf '\nwarning: %s is not on PATH. Add it, for example:\n' "$SCRIPTS_DIR"
    printf '  export PATH="%s:$PATH"\n' "$SCRIPTS_DIR"
fi

cat <<'EOF'

Try it:
  yianc lib path/to/main.an -o build/app
  anx new myapp && cd myapp && anx run
EOF

if [[ "$REGULAR" -eq 1 ]]; then
    cat <<EOF

This was a regular (non-editable) install, which does not include lib/.
Point the tools at a checkout before using them:

  export YIAN_LIB="$ROOT_DIR/lib/src"
  # or: export YIAN_ROOT="$ROOT_DIR"
EOF
fi
