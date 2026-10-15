#!/usr/bin/env bash
# Remove only the language server recorded by refresh_toolchain.sh.
set -euo pipefail

PREFIX="${PREFIX:-}"

usage() {
    cat <<'EOF'
Uninstall the separately managed native yian-lsp command.

Usage: scripts/uninstall_lsp.sh [options]

Options:
  --prefix PATH  Installation prefix (default: $PREFIX, then $HOME/.local).
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
            [[ $# -ge 2 ]] || die '--prefix requires a path'
            PREFIX="$2"
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

if [[ -z "$PREFIX" ]]; then
    [[ -n "${HOME:-}" ]] || die 'HOME is unset; pass --prefix PATH'
    PREFIX="$HOME/.local"
fi
if [[ "$PREFIX" != /* ]]; then
    PREFIX="$PWD/$PREFIX"
fi

TARGET="$PREFIX/bin/yian-lsp"
DATA_DIR="$PREFIX/share/yian-lsp"
MARKER="$DATA_DIR/.native-install"

[[ ! -L "$DATA_DIR" ]] || die "refusing a symlinked language-server metadata directory: $DATA_DIR"
if [[ ! -f "$MARKER" ]] || ! grep -Fxq 'yian-lsp-install-v1' "$MARKER"; then
    printf 'No managed yian-lsp installation found under %s.\n' "$PREFIX"
    exit 0
fi

EXPECTED_HASH="$(awk -F= '$1 == "sha256" { print $2; exit }' "$MARKER")"
if [[ -e "$TARGET" || -L "$TARGET" ]]; then
    [[ -n "$EXPECTED_HASH" ]] || die "install record has no binary digest: $MARKER"
    ACTUAL_HASH="$(sha256sum "$TARGET" | awk '{print $1}')"
    if [[ "$ACTUAL_HASH" != "$EXPECTED_HASH" ]]; then
        printf 'Preserving modified yian-lsp binary and its record: %s\n' "$TARGET" >&2
        exit 1
    fi
    rm -f -- "$TARGET"
    printf 'Removed %s\n' "$TARGET"
fi

rm -f -- "$MARKER"
if rmdir "$DATA_DIR" 2>/dev/null; then
    printf 'Removed language-server management data under %s\n' "$DATA_DIR"
else
    printf 'Kept nonempty management directory %s\n' "$DATA_DIR"
fi
printf 'Shared toolchain resources were left in place at %s/share/yian.\n' "$PREFIX"
