#!/usr/bin/env bash
# Build and install the native YIAN compiler, package manager, and language server.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON="${PYTHON:-}"
PREFIX="${PREFIX:-}"

usage() {
    cat <<'EOF'
Build and install native yianc, anx, and yian-lsp binaries.

Usage: scripts/install.sh [options]

Options:
  --prefix PATH  Installation prefix (default: $HOME/.local).
  --python PATH  Python used to build and remove legacy launchers (default: python3).
  -h, --help     Show this help.

The install requires Python 3.12+, llvmlite 0.49+, LLVM 22, and a native C
toolchain. Installed commands are native executables and do not use Python.
The standard library and runtime are copied under PREFIX/share/yian so the
commands continue to work after the source checkout is removed.
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

if [[ -e "$DATA_DIR" && ! -f "$MARKER" ]]; then
    die "refusing to replace unmanaged resource directory: $DATA_DIR"
fi
if [[ -f "$MARKER" ]] && ! grep -Fxq 'yian-native-install-v1' "$MARKER"; then
    die "unrecognized install marker: $MARKER"
fi

STAGE_DIR="$(mktemp -d "${TMPDIR:-/tmp}/yian-native-install.XXXXXX")"
DATA_STAGE=""
DATA_BACKUP=""
cleanup() {
    rm -rf -- "$STAGE_DIR"
    if [[ -n "$DATA_STAGE" && -e "$DATA_STAGE" ]]; then
        rm -rf -- "$DATA_STAGE"
    fi
    if [[ -n "$DATA_BACKUP" && -e "$DATA_BACKUP" && ! -e "$DATA_DIR" ]]; then
        mv "$DATA_BACKUP" "$DATA_DIR"
    fi
}
trap cleanup EXIT
mkdir -p "$STAGE_DIR/bin" "$STAGE_DIR/share/yian/lib" "$STAGE_DIR/share/yian/runtime"

PYTHON_COMPILER="$STAGE_DIR/yianc-python"
PYTHON_SHELL_WORD="$(printf '%q' "$PYTHON")"
cat >"$PYTHON_COMPILER" <<EOF
#!/usr/bin/env bash
exec $PYTHON_SHELL_WORD -m compiler.main "\$@"
EOF
chmod 0755 "$PYTHON_COMPILER"

run_source_anx() {
    local compiler="$1"
    shift
    PYTHONPATH="$ROOT_DIR${PYTHONPATH:+:$PYTHONPATH}" \
    YIAN_ROOT="$ROOT_DIR" \
    YIAN_LIB="$ROOT_DIR/lib/src" \
    YIANC="$compiler" \
        "$PYTHON" -m anx.main "$@"
}

printf 'Building native yianc with the checkout Python compiler...\n'
run_source_anx "$PYTHON_COMPILER" build "$ROOT_DIR/bootstrap/compiler" -O2
install -m 0755 "$ROOT_DIR/bootstrap/compiler/build/app" "$STAGE_DIR/bin/yianc"

printf 'Building native anx with the new yianc binary...\n'
run_source_anx "$STAGE_DIR/bin/yianc" build "$ROOT_DIR/bootstrap/anx" -O2
install -m 0755 "$ROOT_DIR/bootstrap/anx/build/app" "$STAGE_DIR/bin/anx"

printf 'Building native yian-lsp in raw host mode with the native anx and yianc binaries...\n'
# Keep the long-lived editor host raw; fat metadata exhausts while analyzing compiler-sized workspaces.
YIAN_ROOT="$ROOT_DIR" \
YIAN_LIB="$ROOT_DIR/lib/src" \
YIANC="$STAGE_DIR/bin/yianc" \
    "$STAGE_DIR/bin/anx" build "$ROOT_DIR/bootstrap/lsp" -O2 --raw-pointers
install -m 0755 "$ROOT_DIR/bootstrap/lsp/build/app" "$STAGE_DIR/bin/yian-lsp"

"$STAGE_DIR/bin/yianc" --help >/dev/null
"$STAGE_DIR/bin/anx" --help >/dev/null
"$STAGE_DIR/bin/yian-lsp" --version >/dev/null

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

for command_name in yianc anx yian-lsp; do
    target="$BIN_DIR/$command_name"
    if [[ -e "$target" && ! -f "$MARKER" ]]; then
        import_line='from compiler.main import main'
        case "$command_name" in
            anx) import_line='from anx.main import main' ;;
            yian-lsp) import_line='from lsp.main import main' ;;
        esac
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
    sha256sum "$STAGE_DIR/bin/yian-lsp" | awk '{print "yian-lsp=" $1}'
} >"$DATA_STAGE/.native-install"

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
install -m 0755 "$STAGE_DIR/bin/yian-lsp" "$BIN_DIR/yian-lsp"

remove_python_entrypoint "$PYTHON_SCRIPTS_DIR/yianc" 'from compiler.main import main'
remove_python_entrypoint "$PYTHON_SCRIPTS_DIR/anx" 'from anx.main import main'
remove_python_entrypoint "$PYTHON_SCRIPTS_DIR/yian-lsp" 'from lsp.main import main'
remove_python_entrypoint "$BIN_DIR/yianc" 'from compiler.main import main'
remove_python_entrypoint "$BIN_DIR/anx" 'from anx.main import main'
remove_python_entrypoint "$BIN_DIR/yian-lsp" 'from lsp.main import main'

if [[ -e "$DATA_BACKUP" ]]; then
    rm -rf -- "$DATA_BACKUP"
fi
DATA_BACKUP=""
DATA_STAGE=""

printf '\nInstalled native commands:\n  %s\n  %s\n  %s\n' "$BIN_DIR/yianc" "$BIN_DIR/anx" "$BIN_DIR/yian-lsp"
printf 'Installed resources:\n  %s\n' "$DATA_DIR"
path_update_needed=0
for command_name in yianc anx yian-lsp; do
    resolved="$(command -v "$command_name" 2>/dev/null || true)"
    if [[ "$resolved" != "$BIN_DIR/$command_name" ]]; then
        path_update_needed=1
    fi
done
if [[ "$path_update_needed" -eq 1 ]]; then
    printf '\nPut the native command directory first on PATH:\n  export PATH="%s:$PATH"\n' "$BIN_DIR"
fi
printf '\nThese commands do not require Python at run time.\n'
