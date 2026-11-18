#!/usr/bin/env bash
# Refresh yianc, anx, yian-fmt, yian-lsp and their shared resources with native tools.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
SCRIPT_DIR="$ROOT_DIR/scripts/_toolchain"
source "$SCRIPT_DIR/identity.sh"

PREFIX="${PREFIX:-}"
SELECTED_ANX=""
SELECTED_YIANC="${YIANC:-}"

usage() {
    cat <<'EOF'
Refresh the complete native YIAN toolchain from this checkout.

Usage: scripts/refresh_toolchain.sh [options]

Options:
  --prefix PATH  Installation prefix (default: $PREFIX, then $HOME/.local).
  --anx PATH     Native build driver (default: anx from PATH).
  --yianc PATH   Native compiler (default: $YIANC, then yianc from PATH).
  -h, --help     Show this help.

Builds staged yianc, anx, yian-fmt and a raw/O2 yian-lsp, runs a staged formatter
smoke check and LSP handshake, then publishes the complete toolchain as one
recoverable transaction. The native build requires LLVM 22, clang 22 and the
matching C/LLVM tools.
EOF
}

die() {
    printf 'error: %s\n' "$*" >&2
    exit 1
}

usage_error() {
    printf 'error: %s\n' "$*" >&2
    printf 'Try scripts/refresh_toolchain.sh --help for usage.\n' >&2
    exit 2
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --prefix)
            [[ $# -ge 2 ]] || usage_error '--prefix requires a path'
            PREFIX="$2"
            shift 2
            ;;
        --anx)
            [[ $# -ge 2 ]] || usage_error '--anx requires a path'
            SELECTED_ANX="$2"
            shift 2
            ;;
        --yianc)
            [[ $# -ge 2 ]] || usage_error '--yianc requires a path'
            SELECTED_YIANC="$2"
            shift 2
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *) usage_error "unknown option: $1" ;;
    esac
done

if [[ -z "$PREFIX" ]]; then
    [[ -n "${HOME:-}" ]] || die 'HOME is unset; pass --prefix PATH'
    PREFIX="$HOME/.local"
fi
[[ "$PREFIX" == /* ]] || PREFIX="$PWD/$PREFIX"
[[ ! -L "$PREFIX" ]] || die "refusing a symlinked installation prefix: $PREFIX"
PREFIX="$(realpath -m -- "$PREFIX")"
if [[ -e "$PREFIX" && ! -d "$PREFIX" ]]; then
    die "installation prefix is not a directory: $PREFIX"
fi

resolve_executable() {
    local requested="$1" found
    found="$(command -v -- "$requested" 2>/dev/null || true)"
    [[ -n "$found" && -x "$found" ]] || return 1
    realpath -e -- "$found"
}

require_elf() {
    local label="$1" path="$2" signature
    [[ -f "$path" && -x "$path" ]] || die "$label is not an executable file: $path"
    signature="$(od -An -N4 -tx1 "$path" | tr -d '[:space:]')"
    [[ "$signature" == 7f454c46 ]] || die "$label must be a native ELF executable: $path"
}

if [[ -z "$SELECTED_ANX" ]]; then SELECTED_ANX=anx; fi
if [[ -z "$SELECTED_YIANC" ]]; then SELECTED_YIANC=yianc; fi
ANX_SEED="$(resolve_executable "$SELECTED_ANX")" || die "native anx was not found: $SELECTED_ANX"
YIANC_SEED="$(resolve_executable "$SELECTED_YIANC")" || die "native yianc was not found: $SELECTED_YIANC"
require_elf anx "$ANX_SEED"
require_elf yianc "$YIANC_SEED"

ANX_HELP="$("$ANX_SEED" --help 2>&1)" || die "the selected anx failed its help check: $ANX_SEED"
[[ "$ANX_HELP" == *'Commands:'* ]] || die "the selected anx help output is unrecognized: $ANX_SEED"
YIANC_HELP="$("$YIANC_SEED" --help 2>&1)" || die "the selected yianc failed its help check: $YIANC_SEED"
[[ "$YIANC_HELP" == *'usage:'* ]] || die "the selected yianc help output is unrecognized: $YIANC_SEED"

declare -A TOOL_PATH=() TOOL_VERSION=() TOOL_SHA=()
tool_info() {
    local name="$1" requested="$2" version_command="$3" executable version major
    executable="$(resolve_executable "$requested")" || die "$name was not found: $requested"
    version="$("$executable" "$version_command" 2>&1)" || die "could not query $name version"
    major="$(grep -oE '(^|[^0-9])[0-9]+\.[0-9]+' <<<"$version" | head -1 | grep -oE '[0-9]+' | head -1)"
    [[ "$major" == 22 ]] || die "$name must be LLVM 22; found ${version%%$'\n'*}"
    TOOL_PATH["$name"]="$executable"
    TOOL_VERSION["$name"]="${version%%$'\n'*}"
    TOOL_SHA["$name"]="$(sha256sum -- "$executable")"
    TOOL_SHA["$name"]="${TOOL_SHA[$name]%% *}"
}

tool_info llvm-config "${LLVM_CONFIG:-llvm-config}" --version
tool_info clang clang --version
tool_info llvm-ar llvm-ar --version
CLANG_BIN="${TOOL_PATH[clang]}"

for command_name in timeout env flock sha256sum realpath; do
    command -v "$command_name" >/dev/null 2>&1 || die "required system command is missing: $command_name"
done

LOCK_ROOT="$ROOT_DIR/build/toolchain-workflows/locks"
mkdir -p -- "$LOCK_ROOT"
PREFIX_KEY="$(printf '%s' "$PREFIX" | sha256sum)"
PREFIX_KEY="${PREFIX_KEY%% *}"
exec {LOCK_FD}>"$LOCK_ROOT/$PREFIX_KEY.lock"
flock -n "$LOCK_FD" || die "another toolchain operation is using this checkout and prefix: $PREFIX"

RUN_ROOT="$ROOT_DIR/build/toolchain-workflows"
mkdir -p -- "$RUN_ROOT"
RUN_DIR="$(mktemp -d "$RUN_ROOT/warm-XXXXXX")"
BUNDLE="$RUN_DIR/bundle"
JOURNAL="$RUN_DIR/commands.jsonl"
mkdir -p -- "$BUNDLE/bin" "$BUNDLE/share/yian/lib" "$BUNDLE/share/yian/runtime" "$BUNDLE/share/yian-lsp"
KEEP_RUN_DIR=0
ACTIVE_BUILD_APP=""
ACTIVE_BUILD_BACKUP=""
ACTIVE_BUILD_HAD_ORIGINAL=0

restore_active_build() {
    [[ -n "$ACTIVE_BUILD_APP" ]] || return 0
    if [[ "$ACTIVE_BUILD_HAD_ORIGINAL" -eq 1 ]]; then
        if [[ -e "$ACTIVE_BUILD_BACKUP" || -L "$ACTIVE_BUILD_BACKUP" ]]; then
            if [[ -e "$ACTIVE_BUILD_APP" || -L "$ACTIVE_BUILD_APP" ]]; then
                if [[ -d "$ACTIVE_BUILD_APP" && ! -L "$ACTIVE_BUILD_APP" ]]; then rm -rf -- "$ACTIVE_BUILD_APP"; else rm -f -- "$ACTIVE_BUILD_APP"; fi
            fi
            mv -- "$ACTIVE_BUILD_BACKUP" "$ACTIVE_BUILD_APP" || return 1
        elif [[ ! -e "$ACTIVE_BUILD_APP" && ! -L "$ACTIVE_BUILD_APP" ]]; then
            return 1
        fi
    elif [[ -e "$ACTIVE_BUILD_APP" || -L "$ACTIVE_BUILD_APP" ]]; then
        if [[ -d "$ACTIVE_BUILD_APP" && ! -L "$ACTIVE_BUILD_APP" ]]; then rm -rf -- "$ACTIVE_BUILD_APP"; else rm -f -- "$ACTIVE_BUILD_APP"; fi
    fi
    ACTIVE_BUILD_APP=""
    ACTIVE_BUILD_BACKUP=""
    ACTIVE_BUILD_HAD_ORIGINAL=0
}

handle_signal() {
    local code="$1"
    KEEP_RUN_DIR=1
    if ! restore_active_build; then
        printf 'error: interrupted build output could not be restored; preserve backup at %s\n' "$ACTIVE_BUILD_BACKUP" >&2
    fi
    exit "$code"
}

cleanup() {
    local status=$?
    trap - EXIT INT TERM
    if ! restore_active_build; then
        printf 'error: build output restoration failed; preserve backup at %s\n' "$ACTIVE_BUILD_BACKUP" >&2
        KEEP_RUN_DIR=1
        status=1
    fi
    if [[ "$KEEP_RUN_DIR" -eq 0 ]]; then
        rm -rf -- "$RUN_DIR"
    else
        printf 'Build staging and command journal retained at %s\n' "$RUN_DIR" >&2
    fi
    exit "$status"
}
trap cleanup EXIT
trap 'handle_signal 130' INT
trap 'handle_signal 143' TERM

record_command() {
    local label="$1" code="$2" arg first=1
    shift 2
    {
        printf '{"time":"%s","label":' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
        yian_json_string "$label"
        printf ',"cwd":'
        yian_json_string "$ROOT_DIR"
        printf ',"exit_code":%s,"argv":[' "$code"
        for arg in "$@"; do
            ((first)) || printf ','
            yian_json_string "$arg"
            first=0
        done
        printf ']}\n'
    } >>"$JOURNAL"
}

build_component() {
    local label="$1" project="$2" compiler="$3" builder="$4"
    local build_app="$project/build/app" backup="$RUN_DIR/backups/$label-app" had_original=0 code=0
    mkdir -p -- "$(dirname "$build_app")" "$RUN_DIR/backups"
    if [[ -e "$build_app" || -L "$build_app" ]]; then
        [[ ! -L "$build_app" ]] || die "refusing a symlinked build output: $build_app"
        ACTIVE_BUILD_APP="$build_app"
        ACTIVE_BUILD_BACKUP="$backup"
        ACTIVE_BUILD_HAD_ORIGINAL=1
        mv -- "$build_app" "$backup" || die "could not preserve the previous build output: $build_app"
        had_original=1
    else
        ACTIVE_BUILD_APP="$build_app"
        ACTIVE_BUILD_BACKUP="$backup"
        ACTIVE_BUILD_HAD_ORIGINAL=0
    fi
    local -a arguments=("$builder" build "$project" -O2 --raw-pointers)
    printf 'Building %s with %s and %s...\n' "$label" "$builder" "$compiler"
    (cd "$ROOT_DIR" && env -u PYTHONPATH YIAN_ROOT="$ROOT_DIR" YIAN_LIB="$ROOT_DIR/lib/src" YIANC="$compiler" YIAN_CC="$CLANG_BIN" "${arguments[@]}") || code=$?
    record_command "build-$label" "$code" "${arguments[@]}"
    if [[ "$code" -ne 0 ]]; then
        restore_active_build || { KEEP_RUN_DIR=1; die "previous build output could not be restored; backup remains at $backup"; }
        KEEP_RUN_DIR=1
        die "build-$label failed with exit code $code; command journal: $JOURNAL"
    fi
    if [[ ! -f "$build_app" || -L "$build_app" ]]; then
        restore_active_build || { KEEP_RUN_DIR=1; die "previous build output could not be restored; backup remains at $backup"; }
        KEEP_RUN_DIR=1
        die "build-$label did not create a fresh regular build output: $build_app"
    fi
    cp -p -- "$build_app" "$BUNDLE/bin/$label" || { restore_active_build; die "could not stage $label"; }
    chmod 0755 "$BUNDLE/bin/$label"
    restore_active_build || { KEEP_RUN_DIR=1; die "previous build output could not be restored; backup remains at $backup"; }
}

initial_yianc_source="$(yian_component_sha256 "$ROOT_DIR" yianc)" || die 'could not identify yianc source inputs'
initial_anx_source="$(yian_component_sha256 "$ROOT_DIR" anx)" || die 'could not identify anx source inputs'
initial_fmt_source="$(yian_component_sha256 "$ROOT_DIR" yian-fmt)" || die 'could not identify formatter source inputs'
initial_lsp_source="$(yian_component_sha256 "$ROOT_DIR" yian-lsp)" || die 'could not identify LSP source inputs'
initial_resources_source="$(yian_resource_sha256 "$ROOT_DIR")" || die 'could not identify installed resources'
seed_anx_sha="$(sha256sum -- "$ANX_SEED")"; seed_anx_sha="${seed_anx_sha%% *}"
seed_yianc_sha="$(sha256sum -- "$YIANC_SEED")"; seed_yianc_sha="${seed_yianc_sha%% *}"

build_component yianc "$ROOT_DIR/yianc" "$YIANC_SEED" "$ANX_SEED"
staged_yianc="$BUNDLE/bin/yianc"
build_component anx "$ROOT_DIR/anx" "$staged_yianc" "$ANX_SEED"
staged_anx="$BUNDLE/bin/anx"
build_component yian-fmt "$ROOT_DIR/fmt" "$staged_yianc" "$staged_anx"
staged_fmt="$BUNDLE/bin/yian-fmt"
build_component yian-lsp "$ROOT_DIR/lsp" "$staged_yianc" "$staged_anx"
staged_lsp="$BUNDLE/bin/yian-lsp"

for command_name in yianc anx; do
    help_output="$("$BUNDLE/bin/$command_name" --help 2>&1)" || die "staged $command_name failed its help check"
    if [[ "$command_name" == yianc ]]; then
        [[ "$help_output" == *'usage:'* ]] || die 'staged yianc help output is unrecognized'
    else
        [[ "$help_output" == *'Commands:'* ]] || die 'staged anx help output is unrecognized'
    fi
    require_elf "$command_name" "$BUNDLE/bin/$command_name"
done
require_elf yian-fmt "$staged_fmt"
fmt_version="$(env -u YIAN_FMT "$staged_fmt" --version 2>&1)" || die 'staged yian-fmt failed its version check'
[[ "$fmt_version" == yian-fmt\ * ]] || die "unexpected yian-fmt version output: $fmt_version"
fmt_help="$(env -u YIAN_FMT "$staged_fmt" --help 2>&1)" || die 'staged yian-fmt failed its help check'
[[ "$fmt_help" == *'Usage: yian-fmt'* ]] || die 'staged yian-fmt help output is unrecognized'
printf 'fn main(){let (a,b)=(1,2);}\n' | env -u YIAN_FMT -u YIAN_ROOT -u YIAN_LIB timeout 10 "$staged_fmt" --stdin --stdin-filepath smoke.an >"$RUN_DIR/fmt.stdout" 2>"$RUN_DIR/fmt.stderr" || {
    KEEP_RUN_DIR=1
    die "staged yian-fmt stdin smoke check failed; see $RUN_DIR/fmt.stderr"
}
[[ "$(cat "$RUN_DIR/fmt.stdout")" == 'fn main() { let (a, b) = (1, 2); }' ]] || { KEEP_RUN_DIR=1; die 'staged yian-fmt stdin output is unrecognized'; }
[[ ! -s "$RUN_DIR/fmt.stderr" ]] || { KEEP_RUN_DIR=1; die 'staged yian-fmt stdin check wrote to stderr'; }
require_elf yian-lsp "$staged_lsp"
lsp_version="$("$staged_lsp" --version 2>&1)" || die 'staged yian-lsp failed its version check'
[[ "$lsp_version" == yian-lsp\ * ]] || die "unexpected yian-lsp version output: $lsp_version"

cp -a -- "$ROOT_DIR/lib/src" "$BUNDLE/share/yian/lib/src"
cp -a -- "$ROOT_DIR/lib/package.anx" "$BUNDLE/share/yian/lib/package.anx"
cp -a -- "$ROOT_DIR/runtime/src" "$BUNDLE/share/yian/runtime/src"
cp -a -- "$ROOT_DIR/runtime/include" "$BUNDLE/share/yian/runtime/include"

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
    YIAN_FMT="$staged_fmt" timeout 10 "$staged_lsp" --stdio >"$RUN_DIR/lsp.stdout" 2>"$RUN_DIR/lsp.stderr" || {
        KEEP_RUN_DIR=1
        die "staged yian-lsp stdio handshake failed; see $RUN_DIR/lsp.stderr"
    }
grep -Fq 'serverInfo' "$RUN_DIR/lsp.stdout" || { KEEP_RUN_DIR=1; die 'yian-lsp initialize response has no serverInfo'; }
grep -Fq '"id":1' "$RUN_DIR/lsp.stdout" || { KEEP_RUN_DIR=1; die 'yian-lsp initialize response is missing id 1'; }
grep -Fq '"id":2' "$RUN_DIR/lsp.stdout" || { KEEP_RUN_DIR=1; die 'yian-lsp shutdown response is missing id 2'; }

if [[ "$(yian_component_sha256 "$ROOT_DIR" yianc)" != "$initial_yianc_source" \
    || "$(yian_component_sha256 "$ROOT_DIR" anx)" != "$initial_anx_source" \
    || "$(yian_component_sha256 "$ROOT_DIR" yian-fmt)" != "$initial_fmt_source" \
    || "$(yian_component_sha256 "$ROOT_DIR" yian-lsp)" != "$initial_lsp_source" \
    || "$(yian_resource_sha256 "$ROOT_DIR")" != "$initial_resources_source" ]]; then
    KEEP_RUN_DIR=1
    die 'toolchain source inputs changed during the warm build; refusing publication'
fi

yianc_sha="$(sha256sum -- "$BUNDLE/bin/yianc")"; yianc_sha="${yianc_sha%% *}"
anx_sha="$(sha256sum -- "$BUNDLE/bin/anx")"; anx_sha="${anx_sha%% *}"
fmt_sha="$(sha256sum -- "$BUNDLE/bin/yian-fmt")"; fmt_sha="${fmt_sha%% *}"
lsp_sha="$(sha256sum -- "$BUNDLE/bin/yian-lsp")"; lsp_sha="${lsp_sha%% *}"
resource_source_sha="$initial_resources_source"
resource_artifact_sha="$(yian_tree_sha256 "$BUNDLE/share/yian")"
revision="$(git -C "$ROOT_DIR" rev-parse HEAD 2>/dev/null || true)"

{
    printf '{\n  "format": 1,\n  "revision": '
    if [[ -n "$revision" ]]; then yian_json_string "$revision"; else printf 'null'; fi
    printf ',\n  "components": {\n'
    printf '    "yianc": {"source_sha256":"%s","artifact_sha256":"%s","seed_sha256":"%s","builder":"native-seed","build_mode":"raw","optimization":2,"builder_inputs":{"anx_sha256":"%s","yianc_sha256":"%s"}},\n' \
        "$initial_yianc_source" "$yianc_sha" "$seed_anx_sha" "$seed_anx_sha" "$seed_yianc_sha"
    printf '    "anx": {"source_sha256":"%s","artifact_sha256":"%s","seed_sha256":"%s","builder":"staged-yianc","build_mode":"raw","optimization":2,"builder_inputs":{"anx_sha256":"%s","yianc_sha256":"%s"}},\n' \
        "$initial_anx_source" "$anx_sha" "$seed_anx_sha" "$seed_anx_sha" "$yianc_sha"
    printf '    "yian-fmt": {"source_sha256":"%s","artifact_sha256":"%s","seed_sha256":"%s","builder":"staged-anx","build_mode":"raw","optimization":2,"builder_inputs":{"anx_sha256":"%s","yianc_sha256":"%s"}},\n' \
        "$initial_fmt_source" "$fmt_sha" "$anx_sha" "$anx_sha" "$yianc_sha"
    printf '    "yian-lsp": {"source_sha256":"%s","artifact_sha256":"%s","seed_sha256":"%s","builder":"staged-anx","build_mode":"raw","optimization":2,"builder_inputs":{"anx_sha256":"%s","yianc_sha256":"%s"}}\n' \
        "$initial_lsp_source" "$lsp_sha" "$anx_sha" "$anx_sha" "$yianc_sha"
    printf '  },\n'
    printf '  "resources":{"source_sha256":"%s","artifact_sha256":"%s"},\n' "$resource_source_sha" "$resource_artifact_sha"
    printf '  "build_options":{"optimization":"-O2","pointer_mode":"raw","lsp_pointer_mode":"raw","workflow":"warm"},\n'
    printf '  "build_tools": {\n'
    first=1
    for name in llvm-config clang llvm-ar; do
        ((first)) || printf ',\n'
        printf '    %s:{"path":' "$(yian_json_string "$name")"
        yian_json_string "${TOOL_PATH[$name]}"
        printf ',"version":'
        yian_json_string "${TOOL_VERSION[$name]}"
        printf ',"sha256":"%s"}' "${TOOL_SHA[$name]}"
        first=0
    done
    printf '\n  },\n  "seed_anx_sha256":"%s",\n  "seed_yianc_sha256":"%s"\n}\n' "$seed_anx_sha" "$seed_yianc_sha"
} >"$BUNDLE/share/yian/.build-info"

cat >"$BUNDLE/share/yian-lsp/.build-info" <<EOF
{"format":1,"component":"yian-lsp","source_sha256":"$initial_lsp_source","artifact_sha256":"$lsp_sha","seed_sha256":"$anx_sha","build_mode":"raw","optimization":2,"resources_source_sha256":"$resource_source_sha","resources_artifact_sha256":"$resource_artifact_sha"}
EOF
lsp_build_info_sha="$(sha256sum -- "$BUNDLE/share/yian-lsp/.build-info")"
lsp_build_info_sha="${lsp_build_info_sha%% *}"
cat >"$BUNDLE/share/yian-lsp/.native-install" <<EOF
yian-lsp-install-v1
version=$lsp_version
sha256=$lsp_sha
build_info_sha256=$lsp_build_info_sha
build_mode=raw
optimization=2
shared_resources_sha256=$resource_artifact_sha
EOF
cat >"$BUNDLE/share/yian/.native-install" <<EOF
yian-native-install-v1
yianc=$yianc_sha
anx=$anx_sha
yian-fmt=$fmt_sha
resources=$resource_artifact_sha
build_info_sha256=$(sha256sum -- "$BUNDLE/share/yian/.build-info" | cut -d' ' -f1)
yianc_build_mode=raw
anx_build_mode=raw
fmt_build_mode=raw
optimization=2
pointer_mode=raw
EOF

if ! bash "$SCRIPT_DIR/publish.sh" --prefix "$PREFIX" --bundle "$BUNDLE"; then
    KEEP_RUN_DIR=1
    die "toolchain publication failed; build staging and command journal retained at $RUN_DIR"
fi
printf '%s\n' "Refreshed yianc, anx, yian-fmt and yian-lsp under $PREFIX" "Formats and LSP build: raw pointers, -O2; smoke test and initialization handshake passed."
