#!/usr/bin/env bash
# Remove a native YIAN installation without requiring Python.
set -euo pipefail

PREFIX="${PREFIX:-}"
PYTHON="${PYTHON:-}"

usage() {
    cat <<'EOF'
Uninstall native yianc, anx, and yian-lsp binaries.

Usage: scripts/uninstall.sh [options]

Options:
  --prefix PATH  Installation prefix (default: $HOME/.local).
  --python PATH  Also remove legacy Python yianc/anx/yian-lsp launchers from this interpreter.
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
    command -v "$PYTHON" >/dev/null 2>&1 || die "Python was not found: $PYTHON"
fi

if [[ -z "$PREFIX" ]]; then
    [[ -n "${HOME:-}" ]] || die "HOME is unset; pass --prefix PATH"
    PREFIX="$HOME/.local"
fi
if [[ "$PREFIX" != /* ]]; then
    PREFIX="$PWD/$PREFIX"
fi

BIN_DIR="$PREFIX/bin"
DATA_DIR="$PREFIX/share/yian"
MARKER="$DATA_DIR/.native-install"

if [[ ! -f "$MARKER" ]] || ! grep -Fxq 'yian-native-install-v1' "$MARKER"; then
    printf 'No managed native YIAN installation found under %s.\n' "$PREFIX"
else
    keep_resources=0
    for command_name in yianc anx yian-lsp; do
        target="$BIN_DIR/$command_name"
        if [[ -e "$target" ]]; then
            expected="$(awk -F= -v name="$command_name" '$1 == name { print $2; exit }' "$MARKER")"
            actual="$(sha256sum "$target" | awk '{print $1}')"
            if [[ -n "$expected" && "$actual" == "$expected" ]]; then
                rm -f -- "$target"
                printf 'Removed %s\n' "$target"
            else
                printf 'Preserving %s because it no longer matches this install.\n' "$target" >&2
                keep_resources=1
            fi
        fi
    done

    if [[ "$keep_resources" -eq 0 ]]; then
        rm -rf -- "$DATA_DIR"
        printf 'Removed resources under %s\n' "$DATA_DIR"
        rmdir "$PREFIX/share" 2>/dev/null || true
    else
        printf 'Resources under %s were retained for the preserved command.\n' "$DATA_DIR" >&2
    fi
fi

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
    remove_legacy_entrypoint "$PYTHON_SCRIPTS_DIR/yian-lsp" 'from lsp.main import main'
fi
