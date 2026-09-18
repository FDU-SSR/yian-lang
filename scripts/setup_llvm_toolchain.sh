#!/usr/bin/env bash
#
# Install the LLVM toolchain YIAN needs: llvmlite (which bundles the LLVM
# libraries the compiler links against) and, optionally, clang plus the LLVM
# tools at the same major version.
#
#   llvmlite   pip into the target environment; no sudo.  Always installed.
#   clang      either conda-forge into the environment (no sudo) or
#              apt.llvm.org system-wide (sudo).  The system-wide route also
#              switches the unversioned commands (clang, clang-format, opt,
#              llvm-dis, lld, ...) to that version with update-alternatives.
#
# Usage: scripts/setup_llvm_toolchain.sh [options]
set -euo pipefail

# llvmlite 0.49.0 bundles LLVM 22.1.0.  Keep in sync with the llvmlite pin in
# requirements.txt / pyproject.toml and with the version note in AGENTS.md.
TARGET_LLVMLITE="0.49.0"
LLVM_MAJOR=22
LLVM_PREFIX="/usr/lib/llvm-${LLVM_MAJOR}"
APT_PACKAGES="clang-${LLVM_MAJOR} clang-tools-${LLVM_MAJOR} clang-format-${LLVM_MAJOR} clangd-${LLVM_MAJOR} lld-${LLVM_MAJOR} llvm-${LLVM_MAJOR}-tools libclang-${LLVM_MAJOR}-dev"
CONDA_PACKAGES="clang=${LLVM_MAJOR} clangxx=${LLVM_MAJOR} llvm-tools=${LLVM_MAJOR} clang-format=${LLVM_MAJOR}"
WHEEL_CACHE="/tmp/whl"

# Commands whose unversioned form is switched to the installed LLVM.  Each one
# is handled only when the corresponding binary exists in ${LLVM_PREFIX}/bin.
LLVM_TOOLS="clang clang++ clang-cpp clang-format clang-tidy clangd lld ld.lld \
opt llc llvm-as llvm-dis llvm-link llvm-ar llvm-ranlib llvm-nm llvm-objdump \
llvm-objcopy llvm-strip llvm-config llvm-symbolizer llvm-profdata llvm-cov"

PYTHON="${PYTHON:-}"
PYTHON_EXPLICIT=0
[[ -n "$PYTHON" ]] && PYTHON_EXPLICIT=1
CHECK_ONLY=0
WITH_CLANG=0
WITH_CONDA_CLANG=0

usage() {
    cat <<EOF
Install the LLVM ${LLVM_MAJOR} toolchain used by YIAN.

Usage: scripts/setup_llvm_toolchain.sh [options]

Options:
  --check             Only report the current state; install nothing.
  --with-conda-clang  Also install clang / clangxx / llvm-tools / clang-format
                      from conda-forge into the target environment (no sudo).
                      The environment's bin/ then shadows the system commands
                      while it is active.
  --with-clang-22     Also install the LLVM ${LLVM_MAJOR} packages from
                      apt.llvm.org (sudo) and switch the unversioned commands
                      (clang, clang++, clang-format, opt, llvm-dis, lld, ...) to
                      ${LLVM_MAJOR} with update-alternatives.
  --python PATH       Python interpreter to use (default: \$PYTHON, then python3).
  -h, --help          Show this help.

Environment:
  PYTHON              Same as --python.

Typical fresh setup:

  conda create -n yian-env python=3.12 -y && conda activate yian-env
  scripts/setup_llvm_toolchain.sh --with-clang-22
  scripts/install.sh --python "\$CONDA_PREFIX/bin/python"
  python3 scripts/run_tests.py --all -q

Select the compiler for YIAN explicitly with YIAN_CC, for example:
  export YIAN_CC=${LLVM_PREFIX}/bin/clang
EOF
}

die() { printf 'error: %s\n' "$*" >&2; exit 1; }
note() { printf '%s\n' "$*"; }

while [[ $# -gt 0 ]]; do
    case "$1" in
        --check)             CHECK_ONLY=1; shift ;;
        --with-clang-22)     WITH_CLANG=1; shift ;;
        --with-conda-clang)  WITH_CONDA_CLANG=1; shift ;;
        --python)
            [[ $# -ge 2 ]] || die "--python requires a path"
            PYTHON="$2"; PYTHON_EXPLICIT=1; shift 2 ;;
        -h|--help)           usage; exit 0 ;;
        *)                   die "unknown option: $1 (try --help)" ;;
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
    || die "$("$PYTHON" -V 2>&1) is too old; YIAN needs Python >= 3.11"
note "Interpreter: $("$PYTHON" -c 'import sys; print(sys.executable)')"
if [[ "$PYTHON_EXPLICIT" -eq 0 && "${CONDA_DEFAULT_ENV:-}" == "base" ]]; then
    note "warning: auto-detected interpreter belongs to the 'base' conda environment."
    note "         YIAN is developed in its own environment; use 'conda activate <env>'"
    note "         or pass --python <env>/bin/python unless you really mean to install there."
fi

# ---- reporting helpers ------------------------------------------------------
version_of() {
    local tool="$1" out
    command -v "$tool" >/dev/null 2>&1 || { printf 'not installed'; return 0; }
    out="$("$tool" --version 2>/dev/null | head -1 || true)"
    if printf '%s' "$out" | grep -qE 'version [0-9]'; then
        printf '%s' "$out" | sed -n 's/.*version \([0-9][0-9.]*\).*/\1/p' | head -1
    else
        # llvm-config prints a bare version number; `lld` is a generic driver
        # with no version line of its own (see ld.lld).
        printf '%s' "$out" | grep -oE '[0-9]+\.[0-9]+(\.[0-9]+)?' | head -1 || printf 'version unknown'
    fi
}

report_llvmlite() {
    "$PYTHON" - <<'PYEOF' || true
try:
    import llvmlite
    import llvmlite.binding as binding
except Exception as exc:                      # pragma: no cover - diagnostic path
    print(f"  llvmlite:     not importable ({exc})")
else:
    print(f"  llvmlite:     {llvmlite.__version__}  ->  LLVM {'.'.join(map(str, binding.llvm_version_info))}")
PYEOF
}

report_tools() {
    local t path
    for t in clang clang++ clang-format opt llc llvm-dis llvm-link llvm-config lld ld.lld; do
        path="$(command -v "$t" 2>/dev/null || true)"
        if [[ -z "$path" ]]; then
            printf '  %-13s (not installed)\n' "$t"
        else
            printf '  %-13s %-34s %s\n' "$t" "$(readlink -f "$path")" "$(version_of "$t")"
        fi
    done
}

report_state() {
    report_llvmlite
    if "$PYTHON" -c 'import numba' >/dev/null 2>&1; then
        note "  warning: numba is installed and pins llvmlite; installing may break it."
    fi
    report_tools
}

# Point every unversioned command at <major>'s binaries using update-alternatives.
switch_defaults_to() {
    local major="$1" t path switched=0
    for t in $LLVM_TOOLS; do
        path="/usr/lib/llvm-${major}/bin/$t"
        [[ -x "$path" ]] || continue
        sudo update-alternatives --install "/usr/bin/$t" "$t" "$path" "$((major * 10))" >/dev/null
        if sudo update-alternatives --set "$t" "$path" >/dev/null 2>&1; then
            switched=$((switched + 1))
        fi
    done
    note "  switched $switched commands to LLVM ${major}"
}

smoke_test_cc() {
    local cc="$1" src="/tmp/yian_cc_smoke_$$.c" exe="/tmp/yian_cc_smoke_$$"
    printf 'int main(void){return 0;}\n' > "$src"
    if "$cc" -O2 "$src" -o "$exe" 2>/dev/null && "$exe"; then
        note "  smoke test: $("$cc" --version | head -1) -> compile+link+run OK"
    else
        note "  warning: $cc could not compile and link a trivial program;"
        note "           check the installation before pointing YIAN at it."
    fi
    rm -f "$src" "$exe"
}

# ---- report current state ---------------------------------------------------
note ""
note "Current state:"
report_state

if [[ "$CHECK_ONLY" -eq 1 ]]; then
    note ""
    note "--check: nothing installed."
    exit 0
fi

# ---- llvmlite ---------------------------------------------------------------
note ""
note "Installing llvmlite==${TARGET_LLVMLITE} (LLVM ${LLVM_MAJOR}) ..."
PIP_ARGS=(install --upgrade "llvmlite==${TARGET_LLVMLITE}")
if ! "$PYTHON" -m pip "${PIP_ARGS[@]}"; then
    if [[ -d "$WHEEL_CACHE" ]]; then
        note "pip failed; retrying from the local wheel cache $WHEEL_CACHE ..."
        "$PYTHON" -m pip "${PIP_ARGS[@]}" --find-links "$WHEEL_CACHE" --no-index
    else
        die "pip install failed and no local wheel cache is available"
    fi
fi

actual="$("$PYTHON" -c 'import llvmlite.binding as b; print(b.llvm_version_info[0])' 2>/dev/null || echo "?")"
if [[ "$actual" != "$LLVM_MAJOR" ]]; then
    note "warning: llvmlite reports LLVM $actual, expected $LLVM_MAJOR."
fi

# ---- optional clang (conda-forge, no sudo) ----------------------------------
if [[ "$WITH_CONDA_CLANG" -eq 1 ]]; then
    note ""
    note "Installing clang / clangxx / llvm-tools / clang-format from conda-forge ..."
    CONDA_EXE="$(command -v conda || true)"
    if [[ -z "$CONDA_EXE" ]]; then
        note "  warning: conda not found on PATH; use --with-clang-22 instead."
    else
        CONDA_PREFIX_TARGET="$("$PYTHON" -c 'import sys; print(sys.prefix)')"
        note "  target environment: $CONDA_PREFIX_TARGET"
        # shellcheck disable=SC2086
        if "$CONDA_EXE" install -y -p "$CONDA_PREFIX_TARGET" -c conda-forge $CONDA_PACKAGES; then
            ENV_CLANG="$CONDA_PREFIX_TARGET/bin/clang"
            if [[ -x "$ENV_CLANG" ]]; then
                smoke_test_cc "$ENV_CLANG"
                note "  use it with: export YIAN_CC=$ENV_CLANG"
            fi
        else
            note "  warning: conda install failed; see the output above."
        fi
    fi
fi

# ---- optional clang (apt.llvm.org, sudo) ------------------------------------
if [[ "$WITH_CLANG" -eq 1 ]]; then
    note ""
    note "Installing LLVM ${LLVM_MAJOR} from apt.llvm.org and switching the defaults (needs sudo) ..."
    if ! command -v apt-get >/dev/null 2>&1; then
        note "  warning: apt-get not found; skipping."
    else
        CODENAME="$(lsb_release -cs 2>/dev/null || echo unknown)"
        SOURCE_LIST="/etc/apt/sources.list.d/llvm-${LLVM_MAJOR}.list"
        if [[ "$CODENAME" == "unknown" ]]; then
            note "  warning: cannot detect the distribution codename; install LLVM ${LLVM_MAJOR} manually."
            note "           See https://apt.llvm.org/ for the suite name."
        else
            if [[ ! -f "$SOURCE_LIST" ]]; then
                note "  adding: deb http://apt.llvm.org/${CODENAME}/ llvm-toolchain-${CODENAME}-${LLVM_MAJOR} main"
                printf 'deb http://apt.llvm.org/%s/ llvm-toolchain-%s-%s main\n' \
                    "$CODENAME" "$CODENAME" "$LLVM_MAJOR" | sudo tee "$SOURCE_LIST" >/dev/null
            fi
            if sudo apt-get update && sudo apt-get install -y $APT_PACKAGES; then
                switch_defaults_to "$LLVM_MAJOR"
                smoke_test_cc "/usr/bin/clang"
            else
                note "  warning: installing the LLVM ${LLVM_MAJOR} packages failed."
                note "           apt.llvm.org may not publish LLVM ${LLVM_MAJOR} for ${CODENAME};"
                note "           nothing was switched."
            fi
        fi
    fi
fi

# ---- final state ------------------------------------------------------------
note ""
note "Resulting state:"
report_state

note ""
note "Next:"
note "  scripts/install.sh --python \"$("$PYTHON" -c 'import sys; print(sys.executable)')\""
note "  python3 scripts/run_tests.py --all -q"
note "  PYRIGHT_PYTHON_FORCE_VERSION=latest pyright   # strict, from the project root"
note "  export YIAN_CC=${LLVM_PREFIX}/bin/clang       # optional: pin the linker"
