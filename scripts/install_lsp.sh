#!/usr/bin/env bash
# Build and install the native YIAN language server with the native toolchain.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PREFIX="${PREFIX:-}"

usage() {
    cat <<'EOF'
Build and install the native yian-lsp command.

Usage: scripts/install_lsp.sh [options]

Options:
  --prefix PATH  Toolchain installation prefix (default: $PREFIX, then $HOME/.local).
  -h, --help     Show this help.

The prefix must contain share/yian from scripts/install.sh. The build requires
native anx and yianc commands, LLVM 22, clang, and the YIAN source checkout.
The server binary is built in raw mode; analyzed source defaults to fat mode.
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

BIN_DIR="$PREFIX/bin"
SHARE_DIR="$PREFIX/share"
TOOLCHAIN_DIR="$SHARE_DIR/yian"
DATA_DIR="$SHARE_DIR/yian-lsp"
MARKER="$DATA_DIR/.native-install"
TARGET="$BIN_DIR/yian-lsp"

resolve_executable() {
    local requested="$1"
    local resolved
    resolved="$(command -v -- "$requested" 2>/dev/null || true)"
    [[ -n "$resolved" && -x "$resolved" ]] || return 1
    printf '%s\n' "$resolved"
}

require_native_binary() {
    local label="$1"
    local path="$2"
    local signature
    [[ -f "$path" && -x "$path" ]] || die "$label is not an executable file: $path"
    signature="$(od -An -N4 -tx1 "$path" | tr -d '[:space:]')"
    [[ "$signature" == '7f454c46' ]] || die "$label must be a native ELF executable: $path"
}

ANX_BIN="$(resolve_executable anx)" || die 'native anx is required on PATH'
require_native_binary anx "$ANX_BIN"

SELECTED_YIANC="${YIANC:-yianc}"
YIANC_BIN="$(resolve_executable "$SELECTED_YIANC")" \
    || die "the selected native yianc was not found: $SELECTED_YIANC"
require_native_binary yianc "$YIANC_BIN"

[[ -d "$TOOLCHAIN_DIR/lib/src" ]] || die "standard library resources are missing: $TOOLCHAIN_DIR/lib/src"
[[ -d "$TOOLCHAIN_DIR/runtime/src" ]] || die "runtime resources are missing: $TOOLCHAIN_DIR/runtime/src"
[[ -d "$TOOLCHAIN_DIR/runtime/include" ]] || die "runtime headers are missing: $TOOLCHAIN_DIR/runtime/include"
[[ -f "$TOOLCHAIN_DIR/.native-install" ]] || die "managed toolchain resources are missing: $TOOLCHAIN_DIR/.native-install"

LLVM_CONFIG_BIN="$(resolve_executable "${LLVM_CONFIG:-llvm-config}")" \
    || die 'llvm-config is required to build yian-lsp'
LLVM_VERSION="$("$LLVM_CONFIG_BIN" --version)" \
    || die 'could not query the LLVM version'
[[ "${LLVM_VERSION%%.*}" == 22 ]] \
    || die "LLVM 22 is required; found $LLVM_VERSION"
CLANG_BIN="$(resolve_executable clang)" || die 'clang is required to build yian-lsp'
CLANG_VERSION="$("$CLANG_BIN" --version | sed -n '1s/.*version \([0-9][0-9]*\).*/\1/p')"
[[ "$CLANG_VERSION" == 22 ]] \
    || die "clang 22 is required to build yian-lsp; found ${CLANG_VERSION:-unknown}"
command -v timeout >/dev/null 2>&1 || die 'timeout is required for the stdio smoke test'
command -v env >/dev/null 2>&1 || die 'env is required for the stdio smoke test'

ANX_VERSION="$("$ANX_BIN" --help 2>&1 || true)"
[[ "$ANX_VERSION" == *'Commands:'* ]] || die "the selected anx command did not pass its help check: $ANX_BIN"
YIANC_VERSION="$("$YIANC_BIN" --help 2>&1 || true)"
[[ "$YIANC_VERSION" == *'usage:'* ]] || die "the selected yianc command did not pass its help check: $YIANC_BIN"

if [[ -e "$TARGET" || -L "$TARGET" ]]; then
    [[ -f "$MARKER" ]] || die "refusing to replace an unmanaged language server: $TARGET"
    grep -Fxq 'yian-lsp-install-v1' "$MARKER" \
        || die "unrecognized language-server install record: $MARKER"
    expected_hash="$(awk -F= '$1 == "sha256" { print $2; exit }' "$MARKER")"
    [[ -n "$expected_hash" ]] || die "language-server install record has no binary digest: $MARKER"
    actual_hash="$(sha256sum "$TARGET" | awk '{print $1}')"
    [[ "$actual_hash" == "$expected_hash" ]] \
        || die "refusing to replace a modified language server: $TARGET"
elif [[ -e "$DATA_DIR" && ! -f "$MARKER" ]]; then
    die "refusing to use unmanaged language-server metadata directory: $DATA_DIR"
fi
[[ ! -L "$DATA_DIR" ]] || die "refusing a symlinked language-server metadata directory: $DATA_DIR"

mkdir -p "$PREFIX" "$BIN_DIR" "$SHARE_DIR"
STAGE_DIR="$(mktemp -d "$PREFIX/.yian-lsp-install.XXXXXX")"
MARKER_STAGE=""
CREATED_DATA_DIR=0
cleanup() {
    if [[ -n "${STAGE_DIR:-}" && -e "$STAGE_DIR" ]]; then
        rm -rf -- "$STAGE_DIR"
    fi
    if [[ -n "$MARKER_STAGE" && -e "$MARKER_STAGE" ]]; then
        rm -f -- "$MARKER_STAGE"
    fi
    if [[ "$CREATED_DATA_DIR" -eq 1 ]]; then
        rmdir "$DATA_DIR" 2>/dev/null || true
    fi
}
trap cleanup EXIT

printf 'Building yian-lsp with %s and %s...\n' "$ANX_BIN" "$YIANC_BIN"
(
    cd "$ROOT_DIR"
    YIAN_ROOT="$ROOT_DIR" \
    YIAN_LIB="$ROOT_DIR/lib/src" \
    YIANC="$YIANC_BIN" \
        "$ANX_BIN" build "$ROOT_DIR/lsp" -O2 --raw-pointers
)

BUILT_BINARY="$ROOT_DIR/lsp/build/app"
[[ -f "$BUILT_BINARY" && -x "$BUILT_BINARY" ]] \
    || die "anx did not produce the expected native binary: $BUILT_BINARY"
require_native_binary yian-lsp "$BUILT_BINARY"
LSP_VERSION="$("$BUILT_BINARY" --version 2>&1)" \
    || die 'the newly built yian-lsp failed its version check'
[[ "$LSP_VERSION" == yian-lsp\ * ]] || die "unexpected yian-lsp version output: $LSP_VERSION"

mkdir -p "$STAGE_DIR/bin" "$STAGE_DIR/share"
install -m 0755 "$BUILT_BINARY" "$STAGE_DIR/bin/yian-lsp"
ln -s "$TOOLCHAIN_DIR" "$STAGE_DIR/share/yian"

# Exercise initialization against the resources associated with the staged
# executable before replacing any previously installed server.
initialize_body='{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"processId":null,"capabilities":{}}}'
initialized_body='{"jsonrpc":"2.0","method":"initialized","params":{}}'
shutdown_body='{"jsonrpc":"2.0","id":2,"method":"shutdown","params":null}'
exit_body='{"jsonrpc":"2.0","method":"exit","params":null}'
frame() {
    local body="$1"
    LC_ALL=C printf 'Content-Length: %s\r\n\r\n%s' "${#body}" "$body"
}
{
    frame "$initialize_body"
    frame "$initialized_body"
    frame "$shutdown_body"
    frame "$exit_body"
} | env -u YIAN_ROOT -u YIAN_LIB -u YIAN_LSP_TRACE_FILE -u YIAN_LSP_PROFILE_FILE \
    timeout 10 "$STAGE_DIR/bin/yian-lsp" --stdio \
    >"$STAGE_DIR/stdout" 2>"$STAGE_DIR/stderr" \
    || die "yian-lsp stdio smoke test failed: $(cat "$STAGE_DIR/stderr")"
grep -Fq 'serverInfo' "$STAGE_DIR/stdout" \
    || die 'yian-lsp did not complete the initialize handshake'

install -m 0755 "$STAGE_DIR/bin/yian-lsp" "$STAGE_DIR/yian-lsp"
sha256sum "$STAGE_DIR/yian-lsp" | awk '{ print $1 }' >"$STAGE_DIR/sha256"
if [[ -d "$DATA_DIR" ]]; then
    [[ -f "$MARKER" ]] || die "refusing to replace unmanaged metadata: $DATA_DIR"
else
    mkdir -p "$DATA_DIR"
    CREATED_DATA_DIR=1
fi
MARKER_STAGE="$DATA_DIR/.native-install.$$"
{
    printf 'yian-lsp-install-v1\n'
    printf 'version=%s\n' "$LSP_VERSION"
    printf 'sha256=%s\n' "$(cat "$STAGE_DIR/sha256")"
    printf 'build_mode=raw\noptimization=2\n'
} >"$MARKER_STAGE"

mv -f "$STAGE_DIR/yian-lsp" "$TARGET"
mv -f "$MARKER_STAGE" "$MARKER"
MARKER_STAGE=""
CREATED_DATA_DIR=0

printf 'Installed %s (%s)\n' "$TARGET" "$LSP_VERSION"
printf 'The LSP uses the shared toolchain resources at %s\n' "$TOOLCHAIN_DIR"
