#!/usr/bin/env bash
# Transactionally publish a staged native toolchain bundle.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/identity.sh"

PREFIX=""
BUNDLE=""
LEGACY_LSP_MARKER=""

usage() {
    cat <<'EOF'
Internal toolchain publisher.

Usage: publish.sh --prefix PATH --bundle DIR [--legacy-lsp-marker FILE]
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
        --bundle)
            [[ $# -ge 2 ]] || die '--bundle requires a path'
            BUNDLE="$2"
            shift 2
            ;;
        --legacy-lsp-marker)
            [[ $# -ge 2 ]] || die '--legacy-lsp-marker requires a path'
            LEGACY_LSP_MARKER="$2"
            shift 2
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *) die "unknown option: $1 (try --help)" ;;
    esac
done

[[ -n "$PREFIX" && -n "$BUNDLE" ]] || die '--prefix and --bundle are required'
[[ ! -L "$PREFIX" ]] || die "refusing a symlinked installation prefix: $PREFIX"
PREFIX="$(realpath -m -- "$PREFIX")"
BUNDLE="$(realpath -e -- "$BUNDLE")" || die "staged bundle does not exist: $BUNDLE"
[[ -d "$BUNDLE" ]] || die "staged bundle is not a directory: $BUNDLE"

BIN_DIR="$PREFIX/bin"
SHARE_DIR="$PREFIX/share"
DATA_DIR="$SHARE_DIR/yian"
MARKER="$DATA_DIR/.native-install"
LSP_BIN="$BIN_DIR/yian-lsp"
LSP_DATA_DIR="$SHARE_DIR/yian-lsp"
LSP_MARKER="$LSP_DATA_DIR/.native-install"
STAGED_LSP_BIN="$BUNDLE/bin/yian-lsp"
STAGED_LSP_DATA="$BUNDLE/share/yian-lsp"
HAS_LSP=0
if [[ -e "$STAGED_LSP_BIN" || -L "$STAGED_LSP_BIN" ]]; then HAS_LSP=1; fi

[[ -f "$BUNDLE/bin/yianc" && ! -L "$BUNDLE/bin/yianc" ]] || die "staged yianc is missing: $BUNDLE/bin/yianc"
[[ -f "$BUNDLE/bin/anx" && ! -L "$BUNDLE/bin/anx" ]] || die "staged anx is missing: $BUNDLE/bin/anx"
[[ -f "$BUNDLE/bin/yian-fmt" && ! -L "$BUNDLE/bin/yian-fmt" ]] || die "staged yian-fmt is missing: $BUNDLE/bin/yian-fmt"
[[ -x "$BUNDLE/bin/yianc" && -x "$BUNDLE/bin/anx" && -x "$BUNDLE/bin/yian-fmt" ]] || die 'staged yianc, anx and yian-fmt must be executable'
[[ -d "$BUNDLE/share/yian" && ! -L "$BUNDLE/share/yian" ]] || die "staged resource directory is missing or symlinked: $BUNDLE/share/yian"
[[ -f "$BUNDLE/share/yian/.native-install" && ! -L "$BUNDLE/share/yian/.native-install" ]] || die "staged native install marker is missing or symlinked: $BUNDLE/share/yian/.native-install"
[[ -f "$BUNDLE/share/yian/.build-info" && ! -L "$BUNDLE/share/yian/.build-info" ]] || die "staged build identity is missing or symlinked: $BUNDLE/share/yian/.build-info"
if [[ -n "$(find "$BUNDLE/share/yian" -type l -print -quit)" ]]; then
    die 'staged resources contain symlinks'
fi

staged_marker="$BUNDLE/share/yian/.native-install"
grep -Fxq 'yian-native-install-v1' "$staged_marker" || die "unrecognized staged native install marker: $staged_marker"
for identity_line in yianc_build_mode=raw anx_build_mode=raw fmt_build_mode=raw optimization=2 pointer_mode=raw; do
    grep -Fxq "$identity_line" "$staged_marker" \
        || die "staged toolchain identity is missing required setting: $identity_line"
done
for command_name in yianc anx yian-fmt; do
    staged_hash="$(sha256sum -- "$BUNDLE/bin/$command_name")"
    staged_hash="${staged_hash%% *}"
    recorded_hash="$(awk -F= -v name="$command_name" '$1 == name { print $2; exit }' "$staged_marker")"
    [[ -n "$recorded_hash" && "$staged_hash" == "$recorded_hash" ]] \
        || die "staged $command_name digest does not match its install marker"
done
staged_resources="$(yian_tree_sha256 "$BUNDLE/share/yian")"
recorded_resources="$(awk -F= '$1 == "resources" { print $2; exit }' "$staged_marker")"
[[ -n "$recorded_resources" && "$staged_resources" == "$recorded_resources" ]] \
    || die 'staged resource digest does not match its install marker'
recorded_build_info="$(awk -F= '$1 == "build_info_sha256" { print $2; exit }' "$staged_marker")"
if [[ -n "$recorded_build_info" ]]; then
    actual_build_info="$(sha256sum -- "$BUNDLE/share/yian/.build-info")"
    actual_build_info="${actual_build_info%% *}"
    [[ "$recorded_build_info" == "$actual_build_info" ]] \
        || die 'staged build-info digest does not match its install marker'
fi

if [[ "$HAS_LSP" -eq 1 ]]; then
    [[ -f "$STAGED_LSP_BIN" && ! -L "$STAGED_LSP_BIN" && -x "$STAGED_LSP_BIN" \
        && -d "$STAGED_LSP_DATA" && ! -L "$STAGED_LSP_DATA" \
        && -f "$STAGED_LSP_DATA/.native-install" && ! -L "$STAGED_LSP_DATA/.native-install" \
        && -f "$STAGED_LSP_DATA/.build-info" && ! -L "$STAGED_LSP_DATA/.build-info" ]] \
        || die 'staged yian-lsp bundle is incomplete'
    if [[ -n "$(find "$STAGED_LSP_DATA" -type l -print -quit)" ]]; then
        die 'staged yian-lsp metadata contains symlinks'
    fi
    grep -Fxq 'yian-lsp-install-v1' "$STAGED_LSP_DATA/.native-install" \
        || die 'staged yian-lsp install record is unrecognized'
    grep -Fxq 'build_mode=raw' "$STAGED_LSP_DATA/.native-install" \
        || die 'staged yian-lsp is not recorded as a raw-pointer build'
    grep -Fxq 'optimization=2' "$STAGED_LSP_DATA/.native-install" \
        || die 'staged yian-lsp is not recorded as an -O2 build'
    lsp_hash="$(sha256sum -- "$STAGED_LSP_BIN")"
    lsp_hash="${lsp_hash%% *}"
    recorded_lsp_hash="$(awk -F= '$1 == "sha256" { print $2; exit }' "$STAGED_LSP_DATA/.native-install")"
    [[ -n "$recorded_lsp_hash" && "$recorded_lsp_hash" == "$lsp_hash" ]] \
        || die 'staged yian-lsp digest does not match its install record'
    recorded_lsp_info="$(awk -F= '$1 == "build_info_sha256" { print $2; exit }' "$STAGED_LSP_DATA/.native-install")"
    [[ -n "$recorded_lsp_info" ]] || die 'staged yian-lsp install record has no build-info digest'
    actual_lsp_info="$(sha256sum -- "$STAGED_LSP_DATA/.build-info")"
    actual_lsp_info="${actual_lsp_info%% *}"
    [[ "$recorded_lsp_info" == "$actual_lsp_info" ]] \
        || die 'staged yian-lsp build-info digest does not match its install record'
fi

[[ ! -e "$BIN_DIR" || -d "$BIN_DIR" ]] || die "command directory is not a directory: $BIN_DIR"
[[ ! -e "$SHARE_DIR" || -d "$SHARE_DIR" ]] || die "resource directory is not a directory: $SHARE_DIR"
[[ ! -L "$BIN_DIR" && ! -L "$SHARE_DIR" ]] || die 'refusing a symlinked bin/share directory'

old_marker_values=""
if [[ -L "$DATA_DIR" ]]; then
    die "refusing a symlinked resource directory: $DATA_DIR"
fi
if [[ -e "$DATA_DIR" ]]; then
    [[ -d "$DATA_DIR" && -f "$MARKER" && ! -L "$MARKER" ]] || die "refusing to replace unmanaged resource directory: $DATA_DIR"
    if [[ -n "$(find "$DATA_DIR" -type l -print -quit)" ]]; then
        die "refusing to replace managed resources containing symlinks: $DATA_DIR"
    fi
    grep -Fxq 'yian-native-install-v1' "$MARKER" || die "unrecognized install marker: $MARKER"
    old_marker_values="$MARKER"
    current_resources="$(yian_tree_sha256 "$DATA_DIR")"
    expected_resources="$(awk -F= '$1 == "resources" { print $2; exit }' "$MARKER")"
    if [[ -n "$expected_resources" && "$current_resources" != "$expected_resources" ]]; then
        die "refusing to replace modified managed resources: $DATA_DIR"
    fi
    expected_build_info="$(awk -F= '$1 == "build_info_sha256" { print $2; exit }' "$MARKER")"
    if [[ -n "$expected_build_info" ]]; then
        [[ -f "$DATA_DIR/.build-info" && ! -L "$DATA_DIR/.build-info" ]] \
            || die "managed build-info is missing or symlinked: $DATA_DIR/.build-info"
        actual_build_info="$(sha256sum -- "$DATA_DIR/.build-info")"
        actual_build_info="${actual_build_info%% *}"
        [[ "$expected_build_info" == "$actual_build_info" ]] \
            || die "refusing to replace modified managed build-info: $DATA_DIR/.build-info"
    fi
fi

is_legacy_python_entrypoint() {
    local path="$1" name="$2" first import_line
    [[ -f "$path" && ! -L "$path" ]] || return 1
    IFS= read -r first <"$path" || true
    [[ "$first" == '#!'*python* ]] || return 1
    import_line='from compiler.main import main'
    [[ "$name" == anx ]] && import_line='from anx.main import main'
    grep -Fq -- "$import_line" "$path"
}

for command_name in yianc anx yian-fmt; do
    target="$BIN_DIR/$command_name"
    [[ ! -L "$target" ]] || die "refusing to replace symlinked command: $target"
    [[ ! -e "$target" ]] && continue
    [[ -f "$target" ]] || die "refusing to replace non-file command: $target"
    if [[ -n "$old_marker_values" ]]; then
        expected="$(awk -F= -v name="$command_name" '$1 == name { print $2; exit }' "$old_marker_values")"
        actual="$(sha256sum -- "$target")"
        actual="${actual%% *}"
        [[ -n "$expected" && "$actual" == "$expected" ]] \
            || die "refusing to replace a modified managed command: $target"
    elif [[ "$command_name" == "yian-fmt" ]]; then
        die "refusing to replace an unmanaged command: $target"
    elif ! is_legacy_python_entrypoint "$target" "$command_name"; then
        die "refusing to replace an unmanaged command: $target"
    fi
done

if [[ "$HAS_LSP" -eq 1 ]]; then
    [[ ! -L "$LSP_DATA_DIR" && ! -L "$LSP_BIN" ]] || die 'refusing a symlinked language-server target'
    if [[ -e "$LSP_DATA_DIR" ]]; then
        [[ -d "$LSP_DATA_DIR" && -f "$LSP_MARKER" && ! -L "$LSP_MARKER" ]] || die "refusing to replace unmanaged language-server data: $LSP_DATA_DIR"
        grep -Fxq 'yian-lsp-install-v1' "$LSP_MARKER" || die "unrecognized language-server install record: $LSP_MARKER"
        if [[ -n "$(find "$LSP_DATA_DIR" -type l -print -quit)" ]]; then
            die "refusing to replace language-server metadata containing symlinks: $LSP_DATA_DIR"
        fi
        expected_lsp_info="$(awk -F= '$1 == "build_info_sha256" { print $2; exit }' "$LSP_MARKER")"
        if [[ -n "$expected_lsp_info" ]]; then
            [[ -f "$LSP_DATA_DIR/.build-info" && ! -L "$LSP_DATA_DIR/.build-info" ]] || die "managed language-server build-info is missing or symlinked: $LSP_DATA_DIR/.build-info"
            actual_lsp_info="$(sha256sum -- "$LSP_DATA_DIR/.build-info")"
            actual_lsp_info="${actual_lsp_info%% *}"
            [[ "$actual_lsp_info" == "$expected_lsp_info" ]] \
                || die "refusing to replace modified language-server build-info: $LSP_DATA_DIR/.build-info"
        fi
        while IFS= read -r -d '' entry; do
            case "${entry##*/}" in
                .native-install|.build-info) [[ -f "$entry" && ! -L "$entry" ]] || die "refusing unsafe language-server metadata: $entry" ;;
                *) die "refusing to remove unmanaged language-server metadata: $entry" ;;
            esac
        done < <(find "$LSP_DATA_DIR" -mindepth 1 -maxdepth 1 -print0)
    fi
    if [[ -e "$LSP_BIN" ]]; then
        [[ -f "$LSP_BIN" && -f "$LSP_MARKER" ]] || die "refusing to replace unmanaged language server: $LSP_BIN"
        expected="$(awk -F= '$1 == "sha256" { print $2; exit }' "$LSP_MARKER")"
        actual="$(sha256sum -- "$LSP_BIN")"
        actual="${actual%% *}"
        [[ -n "$expected" && "$actual" == "$expected" ]] \
            || die "refusing to replace a modified language server: $LSP_BIN"
    fi
fi

if [[ -n "$LEGACY_LSP_MARKER" ]]; then
    [[ -f "$LEGACY_LSP_MARKER" && ! -L "$LEGACY_LSP_MARKER" ]] || die "legacy LSP marker is missing: $LEGACY_LSP_MARKER"
    [[ "$HAS_LSP" -eq 0 ]] || die 'cannot mix legacy LSP marker transfer with a staged LSP bundle'
    if [[ -e "$LSP_MARKER" || -L "$LSP_MARKER" ]]; then
        die "refusing to replace an existing language-server record: $LSP_MARKER"
    fi
    [[ ! -L "$LSP_DATA_DIR" ]] || die "refusing a symlinked language-server data directory: $LSP_DATA_DIR"
    if [[ -e "$LSP_DATA_DIR" ]]; then
        [[ -d "$LSP_DATA_DIR" ]] || die "refusing to add a legacy language-server record under a non-directory: $LSP_DATA_DIR"
        if [[ -n "$(find "$LSP_DATA_DIR" -mindepth 1 -maxdepth 1 -print -quit)" ]]; then
            die "refusing to add a legacy language-server record to nonempty metadata: $LSP_DATA_DIR"
        fi
    fi
fi

mkdir -p -- "$PREFIX" "$BIN_DIR" "$SHARE_DIR"
TRANSACTION="$(mktemp -d "$PREFIX/.yian-publish.XXXXXX")"
COMMITTED=0
declare -a CANDIDATE_SOURCES=() TARGETS=() BACKUPS=() PUBLISHED=()
add_replacement() {
    local source="$1" target="$2"
    CANDIDATE_SOURCES+=("$source")
    TARGETS+=("$target")
    BACKUPS+=("$TRANSACTION/backups/${#TARGETS[@]}")
    PUBLISHED+=(0)
}

add_replacement "$BUNDLE/share/yian" "$DATA_DIR"
add_replacement "$BUNDLE/bin/yianc" "$BIN_DIR/yianc"
add_replacement "$BUNDLE/bin/anx" "$BIN_DIR/anx"
add_replacement "$BUNDLE/bin/yian-fmt" "$BIN_DIR/yian-fmt"
if [[ "$HAS_LSP" -eq 1 ]]; then
    add_replacement "$STAGED_LSP_DATA" "$LSP_DATA_DIR"
    add_replacement "$STAGED_LSP_BIN" "$LSP_BIN"
fi
if [[ -n "$LEGACY_LSP_MARKER" ]]; then
    add_replacement "$LEGACY_LSP_MARKER" "$LSP_MARKER"
fi

rollback_transaction() {
    local index restore_error=0
    set +e
    for ((index = ${#TARGETS[@]} - 1; index >= 0; index--)); do
        if [[ "${PUBLISHED[$index]}" -eq 1 ]]; then
            rm -rf -- "${TARGETS[$index]}" || restore_error=1
        fi
        if [[ -e "${BACKUPS[$index]}" || -L "${BACKUPS[$index]}" ]]; then
            mv -- "${BACKUPS[$index]}" "${TARGETS[$index]}" || restore_error=1
        fi
    done
    if [[ "$restore_error" -eq 0 ]]; then
        rm -rf -- "$TRANSACTION"
        return 0
    fi
    printf 'error: installation recovery failed; preserve transaction backups at %s\n' "$TRANSACTION" >&2
    return 1
}

cleanup_transaction() {
    local status=$?
    trap - EXIT INT TERM
    if [[ "$COMMITTED" -eq 1 ]]; then
        if ! rm -rf -- "$TRANSACTION"; then
            printf 'warning: installation succeeded; transaction cleanup remains at %s\n' "$TRANSACTION" >&2
        fi
    elif [[ -n "${TRANSACTION:-}" && -d "$TRANSACTION" ]]; then
        if rollback_transaction; then
            [[ "$status" -ne 0 ]] || status=1
        else
            status=1
        fi
    fi
    exit "$status"
}
trap cleanup_transaction EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

mkdir -p "$TRANSACTION/candidates" "$TRANSACTION/backups"
for index in "${!TARGETS[@]}"; do
    mkdir -p -- "$(dirname "${TARGETS[$index]}")"
    cp -a -- "${CANDIDATE_SOURCES[$index]}" "$TRANSACTION/candidates/$index"
    if [[ "${TARGETS[$index]}" == "$BIN_DIR/yianc" || "${TARGETS[$index]}" == "$BIN_DIR/anx" \
        || "${TARGETS[$index]}" == "$BIN_DIR/yian-fmt" || "${TARGETS[$index]}" == "$LSP_BIN" ]]; then
        chmod 0755 "$TRANSACTION/candidates/$index"
    fi
done

for index in "${!TARGETS[@]}"; do
    target="${TARGETS[$index]}"
    if [[ -e "$target" || -L "$target" ]]; then
        if ! mv -- "$target" "${BACKUPS[$index]}"; then
            die "could not back up the existing install target: $target"
        fi
    fi
    PUBLISHED[$index]=1
    if ! mv -- "$TRANSACTION/candidates/$index" "$target"; then
        die "could not publish staged install target: $target"
    fi
done
COMMITTED=1
printf 'Published native toolchain under %s\n' "$PREFIX"
