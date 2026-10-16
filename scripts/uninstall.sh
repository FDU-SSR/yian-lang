#!/usr/bin/env bash
# Remove a managed native YIAN installation without requiring Python.
set -euo pipefail

PREFIX="${PREFIX:-}"
PYTHON="${PYTHON:-}"
LSP_ONLY=0

usage() {
    cat <<'EOF'
Uninstall the managed native YIAN toolchain.

Usage: scripts/uninstall.sh [options]

Options:
  --prefix PATH  Installation prefix (default: $HOME/.local).
  --lsp-only     Remove only yian-lsp; keep yianc, anx and shared resources.
  --python PATH  Also remove recognized legacy Python yianc/anx launchers.
  -h, --help     Show this help.
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
        --lsp-only)
            LSP_ONLY=1
            shift
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

if [[ -n "$PYTHON" ]]; then
    command -v -- "$PYTHON" >/dev/null 2>&1 || die "Python was not found: $PYTHON"
fi

if [[ -z "$PREFIX" ]]; then
    [[ -n "${HOME:-}" ]] || die "HOME is unset; pass --prefix PATH"
    PREFIX="$HOME/.local"
fi
if [[ "$PREFIX" != /* ]]; then
    PREFIX="$PWD/$PREFIX"
fi
[[ ! -L "$PREFIX" ]] || die "refusing a symlinked installation prefix: $PREFIX"
PREFIX="$(realpath -m -- "$PREFIX")"

BIN_DIR="$PREFIX/bin"
SHARE_DIR="$PREFIX/share"
NATIVE_DATA_DIR="$SHARE_DIR/yian"
NATIVE_MARKER="$NATIVE_DATA_DIR/.native-install"
LSP_TARGET="$BIN_DIR/yian-lsp"
LSP_DATA_DIR="$SHARE_DIR/yian-lsp"
LSP_MARKER="$LSP_DATA_DIR/.native-install"

for directory in "$BIN_DIR" "$SHARE_DIR"; do
    [[ ! -L "$directory" ]] || die "refusing a symlinked installation directory: $directory"
done
[[ ! -L "$LSP_DATA_DIR" ]] || die "refusing a symlinked language-server metadata directory: $LSP_DATA_DIR"
[[ ! -L "$LSP_MARKER" ]] || die "refusing a symlinked language-server install record: $LSP_MARKER"

matches_digest() {
    local path="$1" expected="$2" actual
    [[ -n "$expected" && -f "$path" ]] || return 1
    actual="$(sha256sum -- "$path" 2>/dev/null | awk '{print $1}')" || return 1
    [[ "$actual" == "$expected" ]]
}

LSP_REMAINS=0
LSP_FAILURE=0
if [[ -f "$LSP_MARKER" ]] && grep -Fxq 'yian-lsp-install-v1' "$LSP_MARKER"; then
    EXPECTED_LSP_HASH="$(awk -F= '$1 == "sha256" { print $2; exit }' "$LSP_MARKER")"
    if [[ -e "$LSP_TARGET" || -L "$LSP_TARGET" ]]; then
        if matches_digest "$LSP_TARGET" "$EXPECTED_LSP_HASH"; then
            rm -f -- "$LSP_TARGET"
            printf 'Removed %s\n' "$LSP_TARGET"
        else
            printf 'Preserving modified yian-lsp binary and its record: %s\n' "$LSP_TARGET" >&2
            LSP_REMAINS=1
            LSP_FAILURE=1
        fi
    fi

    if [[ "$LSP_REMAINS" -eq 0 ]]; then
        rm -f -- "$LSP_MARKER"
        if rmdir "$LSP_DATA_DIR" 2>/dev/null; then
            printf 'Removed language-server management data under %s\n' "$LSP_DATA_DIR"
        else
            printf 'Kept nonempty management directory %s\n' "$LSP_DATA_DIR"
        fi
    fi
else
    if [[ -e "$LSP_TARGET" || -L "$LSP_TARGET" ]]; then
        printf 'Preserving unmanaged yian-lsp binary; shared resources will be retained: %s\n' "$LSP_TARGET" >&2
        LSP_REMAINS=1
    else
        printf 'No managed yian-lsp installation found under %s.\n' "$PREFIX"
    fi
fi

if [[ "$LSP_ONLY" -eq 1 ]]; then
    printf 'Shared toolchain resources and yianc/anx were left in place under %s.\n' "$PREFIX"
else
    [[ ! -L "$NATIVE_DATA_DIR" ]] || die "refusing a symlinked native resource directory: $NATIVE_DATA_DIR"
    [[ ! -L "$NATIVE_MARKER" ]] || die "refusing a symlinked native install record: $NATIVE_MARKER"

    if [[ -f "$NATIVE_MARKER" ]] && grep -Fxq 'yian-native-install-v1' "$NATIVE_MARKER"; then
        KEEP_RESOURCES=0
        for command_name in yianc anx; do
            target="$BIN_DIR/$command_name"
            if [[ -e "$target" || -L "$target" ]]; then
                expected="$(awk -F= -v name="$command_name" '$1 == name { print $2; exit }' "$NATIVE_MARKER")"
                if matches_digest "$target" "$expected"; then
                    rm -f -- "$target"
                    printf 'Removed %s\n' "$target"
                else
                    printf 'Preserving %s because it no longer matches this install.\n' "$target" >&2
                    KEEP_RESOURCES=1
                fi
            fi
        done

        if [[ "$LSP_REMAINS" -eq 1 ]]; then
            KEEP_RESOURCES=1
        fi

        if [[ "$KEEP_RESOURCES" -eq 0 ]]; then
            rm -rf -- "$NATIVE_DATA_DIR"
            printf 'Removed shared resources under %s\n' "$NATIVE_DATA_DIR"
        else
            printf 'Shared resources under %s were retained for a preserved command.\n' "$NATIVE_DATA_DIR" >&2
        fi
    else
        printf 'No managed native YIAN installation found under %s.\n' "$PREFIX"
    fi
fi

rmdir "$SHARE_DIR" 2>/dev/null || true

if [[ -n "$PYTHON" ]]; then
    PYTHON_SCRIPTS_DIR="$("$PYTHON" -c 'import sysconfig; print(sysconfig.get_path("scripts"))')"
    remove_legacy_entrypoint() {
        local path="$1"
        local import_line="$2"
        local shebang=""

        [[ -f "$path" ]] || return 0
        IFS= read -r shebang <"$path" || true
        [[ "$shebang" == '#!'*python* ]] || return 0
        if grep -Fq -- "$import_line" "$path"; then
            rm -f -- "$path"
            printf 'Removed legacy Python command %s\n' "$path"
        fi
    }
    remove_legacy_entrypoint "$PYTHON_SCRIPTS_DIR/yianc" 'from compiler.main import main'
    remove_legacy_entrypoint "$PYTHON_SCRIPTS_DIR/anx" 'from anx.main import main'
fi

exit "$LSP_FAILURE"
