#!/usr/bin/env bash
# Build and install the native YIAN compiler and package manager.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_ROOT="$ROOT_DIR/python"
PYTHON="${PYTHON:-}"
PREFIX="${PREFIX:-}"

usage() {
    cat <<'EOF'
Build and install native yianc and anx binaries.

Usage: scripts/install.sh [options]

Options:
  --prefix PATH  Installation prefix (default: $HOME/.local).
  --python PATH  Python used for the seed build and legacy launcher cleanup (default: python3).
  -h, --help     Show this help.

The install requires Python 3.12+, llvmlite 0.49+, LLVM 22, and a native C
toolchain. Installed commands are native executables and do not use Python.
The standard library and runtime are copied under PREFIX/share/yian so the
commands continue to work after the source checkout is removed. Install the
language server separately with scripts/install_lsp.sh.
EOF
}

die() {
    printf 'error: %s\n' "$*" >&2
    exit 1
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
            PYTHON="$2"
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

if [[ -z "$PYTHON" ]]; then
    PYTHON="python3"
fi
command -v "$PYTHON" >/dev/null 2>&1 || die "Python was not found: $PYTHON"
PYTHON="$(command -v "$PYTHON")"

if ! "$PYTHON" -c 'import sys, llvmlite; raise SystemExit(0 if sys.version_info >= (3, 12) and tuple(map(int, llvmlite.__version__.split(".")[:2])) >= (0, 49) else 1)' >/dev/null 2>&1; then
    die "the build Python must be 3.12+ and provide llvmlite 0.49+"
fi

if [[ -z "$PREFIX" ]]; then
    [[ -n "${HOME:-}" ]] || die "HOME is unset; pass --prefix PATH"
    PREFIX="$HOME/.local"
fi
if [[ "$PREFIX" != /* ]]; then
    PREFIX="$PWD/$PREFIX"
fi

BIN_DIR="$PREFIX/bin"
SHARE_DIR="$PREFIX/share"
DATA_DIR="$SHARE_DIR/yian"
MARKER="$DATA_DIR/.native-install"
LSP_BIN="$BIN_DIR/yian-lsp"
LSP_DATA_DIR="$SHARE_DIR/yian-lsp"
LSP_MARKER="$LSP_DATA_DIR/.native-install"

if [[ -e "$DATA_DIR" && ! -f "$MARKER" ]]; then
    die "refusing to replace unmanaged resource directory: $DATA_DIR"
fi
if [[ -f "$MARKER" ]] && ! grep -Fxq 'yian-native-install-v1' "$MARKER"; then
    die "unrecognized install marker: $MARKER"
fi

# Transfer ownership of a previously co-installed server before replacing the
# shared toolchain marker and resources.
LEGACY_LSP_HASH=""
if [[ -f "$MARKER" && -f "$LSP_BIN" ]]; then
    LEGACY_LSP_HASH="$(awk -F= '$1 == "yian-lsp" { print $2; exit }' "$MARKER")"
    if [[ -n "$LEGACY_LSP_HASH" ]]; then
        CURRENT_LSP_HASH="$(sha256sum "$LSP_BIN" | awk '{print $1}')"
        if [[ "$CURRENT_LSP_HASH" != "$LEGACY_LSP_HASH" ]]; then
            LEGACY_LSP_HASH=""
        fi
    fi
fi

STAGE_DIR="$(mktemp -d "${TMPDIR:-/tmp}/yian-native-install.XXXXXX")"
DATA_STAGE=""
DATA_BACKUP=""
ACTIVE_BUILD_APP=""
ACTIVE_BUILD_BACKUP=""
ACTIVE_BUILD_HAD_ORIGINAL=0
restore_build_app() {
    [[ -n "$ACTIVE_BUILD_APP" ]] || return 0
    if [[ "$ACTIVE_BUILD_HAD_ORIGINAL" -eq 1 ]]; then
        if [[ -e "$ACTIVE_BUILD_BACKUP" || -L "$ACTIVE_BUILD_BACKUP" ]]; then
            rm -f -- "$ACTIVE_BUILD_APP" || return 1
            mv -- "$ACTIVE_BUILD_BACKUP" "$ACTIVE_BUILD_APP" || return 1
        elif [[ ! -e "$ACTIVE_BUILD_APP" && ! -L "$ACTIVE_BUILD_APP" ]]; then
            return 1
        fi
    else
        rm -f -- "$ACTIVE_BUILD_APP" || return 1
    fi
    ACTIVE_BUILD_APP=""
    ACTIVE_BUILD_BACKUP=""
    ACTIVE_BUILD_HAD_ORIGINAL=0
}
cleanup() {
    local status=$?
    trap - EXIT
    if restore_build_app; then
        rm -rf -- "$STAGE_DIR"
    else
        printf 'error: could not restore a previous bootstrap build artifact; staging was kept at %s\n' "$STAGE_DIR" >&2
        status=1
    fi
    if [[ -n "$DATA_STAGE" && -e "$DATA_STAGE" ]]; then
        rm -rf -- "$DATA_STAGE"
    fi
    if [[ -n "$DATA_BACKUP" && -e "$DATA_BACKUP" && ! -e "$DATA_DIR" ]]; then
        mv "$DATA_BACKUP" "$DATA_DIR"
    fi
    exit "$status"
}
trap cleanup EXIT
mkdir -p "$STAGE_DIR/bin" "$STAGE_DIR/share/yian/lib" "$STAGE_DIR/share/yian/runtime"

PYTHON_COMPILER="$STAGE_DIR/yianc-python"
PYTHON_SHELL_WORD="$(printf '%q' "$PYTHON")"
cat >"$PYTHON_COMPILER" <<EOF
#!/usr/bin/env bash
export PYTHONPATH="$PYTHON_ROOT\${PYTHONPATH:+:\$PYTHONPATH}"
exec $PYTHON_SHELL_WORD -m yianc.main "\$@"
EOF
chmod 0755 "$PYTHON_COMPILER"

run_native_anx() {
    local anx="$1"
    local compiler="$2"
    shift 2
    YIAN_ROOT="$ROOT_DIR" \
    YIAN_LIB="$ROOT_DIR/lib/src" \
    YIANC="$compiler" \
        "$anx" "$@"
}

build_native_package() {
    local anx="$1"
    local compiler="$2"
    local project="$3"
    local output="$4"
    local label="$5"
    local build_app="$project/build/app"
    local backup="$STAGE_DIR/build-backups/$label-app"
    local status=0

    mkdir -p "$(dirname "$build_app")" "$STAGE_DIR/build-backups"
    ACTIVE_BUILD_APP="$build_app"
    ACTIVE_BUILD_BACKUP="$backup"
    ACTIVE_BUILD_HAD_ORIGINAL=0
    if [[ -e "$build_app" || -L "$build_app" ]]; then
        ACTIVE_BUILD_HAD_ORIGINAL=1
        mv -- "$build_app" "$backup"
    fi

    if run_native_anx "$anx" "$compiler" build "$project" -O2; then
        if [[ ! -f "$build_app" ]]; then
            restore_build_app
            die "native anx did not create a fresh build output for $project"
        fi
        if install -m 0755 "$build_app" "$output"; then
            :
        else
            status=$?
            restore_build_app
            die "could not stage the native build output for $project (exit $status)"
        fi
    else
        status=$?
        restore_build_app
        die "native anx failed to build $project (exit $status)"
    fi

    restore_build_app
}

printf 'Bootstrapping a temporary native anx with the checkout Python compiler...\n'
"$PYTHON" "$ROOT_DIR/scripts/bootstrap_anx.py" \
    --python "$PYTHON" --output "$STAGE_DIR/bin/anx-seed"

printf 'Building native yianc with the temporary native anx and checkout Python compiler...\n'
build_native_package "$STAGE_DIR/bin/anx-seed" "$PYTHON_COMPILER" \
    "$ROOT_DIR/bootstrap/yianc" "$STAGE_DIR/bin/yianc" yianc

printf 'Building final native anx with the temporary native anx and staged yianc...\n'
build_native_package "$STAGE_DIR/bin/anx-seed" "$STAGE_DIR/bin/yianc" \
    "$ROOT_DIR/bootstrap/anx" "$STAGE_DIR/bin/anx" anx

"$STAGE_DIR/bin/yianc" --help >/dev/null
"$STAGE_DIR/bin/anx" --help >/dev/null

cp -a "$ROOT_DIR/lib/src" "$STAGE_DIR/share/yian/lib/src"
cp -a "$ROOT_DIR/runtime/src" "$STAGE_DIR/share/yian/runtime/src"
cp -a "$ROOT_DIR/runtime/include" "$STAGE_DIR/share/yian/runtime/include"

mkdir -p "$BIN_DIR" "$SHARE_DIR"
is_python_entrypoint() {
    local path="$1"
    local import_line="$2"
    local shebang=""

    [[ -f "$path" ]] || return 1
    IFS= read -r shebang <"$path" || true
    [[ "$shebang" == '#!'*python* ]] || return 1
    grep -Fq -- "$import_line" "$path"
}

for command_name in yianc anx; do
    target="$BIN_DIR/$command_name"
    if [[ -e "$target" && ! -f "$MARKER" ]]; then
        import_line='from compiler.main import main'
        if [[ "$command_name" == "anx" ]]; then
            import_line='from anx.main import main'
        fi
        if ! is_python_entrypoint "$target" "$import_line"; then
            die "refusing to replace an unmanaged command: $target"
        fi
    fi
done

PYTHON_SCRIPTS_DIR="$("$PYTHON" -c 'import sysconfig; print(sysconfig.get_path("scripts"))')"
remove_python_entrypoint() {
    local path="$1"
    local import_line="$2"

    # Leave non-Python commands with the same names untouched.
    if is_python_entrypoint "$path" "$import_line"; then
        rm -f -- "$path"
        printf 'Removed legacy Python command %s\n' "$path"
    fi
}

DATA_STAGE="$SHARE_DIR/.yian-native-stage.$$"
if [[ -e "$DATA_STAGE" ]]; then
    die "temporary resource path already exists: $DATA_STAGE"
fi
cp -a "$STAGE_DIR/share/yian" "$DATA_STAGE"
{
    printf 'yian-native-install-v1\n'
    sha256sum "$STAGE_DIR/bin/yianc" | awk '{print "yianc=" $1}'
    sha256sum "$STAGE_DIR/bin/anx" | awk '{print "anx=" $1}'
} >"$DATA_STAGE/.native-install"

if [[ -n "$LEGACY_LSP_HASH" && ! -f "$LSP_MARKER" ]]; then
    mkdir -p "$LSP_DATA_DIR"
    LSP_MARKER_STAGE="$LSP_DATA_DIR/.native-install.$$"
    {
        printf 'yian-lsp-install-v1\n'
        "$LSP_BIN" --version
        printf 'sha256=%s\n' "$LEGACY_LSP_HASH"
    } >"$LSP_MARKER_STAGE"
    mv "$LSP_MARKER_STAGE" "$LSP_MARKER"
    printf 'Transferred the managed language server to %s\n' "$LSP_MARKER"
fi

DATA_BACKUP="$SHARE_DIR/.yian-native-backup.$$"
if [[ -e "$DATA_DIR" ]]; then
    mv "$DATA_DIR" "$DATA_BACKUP"
fi
if ! mv "$DATA_STAGE" "$DATA_DIR"; then
    if [[ -e "$DATA_BACKUP" ]]; then
        mv "$DATA_BACKUP" "$DATA_DIR"
    fi
    die "could not publish resources under $DATA_DIR"
fi

install -m 0755 "$STAGE_DIR/bin/yianc" "$BIN_DIR/yianc"
install -m 0755 "$STAGE_DIR/bin/anx" "$BIN_DIR/anx"

remove_python_entrypoint "$PYTHON_SCRIPTS_DIR/yianc" 'from compiler.main import main'
remove_python_entrypoint "$PYTHON_SCRIPTS_DIR/anx" 'from anx.main import main'
remove_python_entrypoint "$BIN_DIR/yianc" 'from compiler.main import main'
remove_python_entrypoint "$BIN_DIR/anx" 'from anx.main import main'

if [[ -e "$DATA_BACKUP" ]]; then
    rm -rf -- "$DATA_BACKUP"
fi
DATA_BACKUP=""
DATA_STAGE=""

printf '\nInstalled native commands:\n  %s\n  %s\n' "$BIN_DIR/yianc" "$BIN_DIR/anx"
printf 'Installed resources:\n  %s\n' "$DATA_DIR"
path_update_needed=0
for command_name in yianc anx; do
    resolved="$(command -v "$command_name" 2>/dev/null || true)"
    if [[ "$resolved" != "$BIN_DIR/$command_name" ]]; then
        path_update_needed=1
    fi
done
if [[ "$path_update_needed" -eq 1 ]]; then
    printf '\nPut the native command directory first on PATH:\n  export PATH="%s:$PATH"\n' "$BIN_DIR"
fi
printf '\nThese commands do not require Python at run time.\n'
