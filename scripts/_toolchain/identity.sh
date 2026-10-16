#!/usr/bin/env bash
# Source and artifact digest helpers for the native refresh and shared publisher.

yian_component_root() {
    case "$1" in
        yianc) printf '%s\n' 'yianc' ;;
        anx) printf '%s\n' 'anx' ;;
        yian-lsp) printf '%s\n' 'lsp' ;;
        *) printf 'unknown toolchain component: %s\n' "$1" >&2; return 1 ;;
    esac
}

yian_component_files() {
    local root="$1" component="$2" package_root manifest dependency relative
    local -a queue=() packages=() files=()
    local -A seen=()
    local list_file
    list_file="$(mktemp "${TMPDIR:-/tmp}/yian-input-list.XXXXXX")" || return 1
    queue+=("$root/$(yian_component_root "$component")")
    while ((${#queue[@]})); do
        package_root="${queue[0]}"
        queue=("${queue[@]:1}")
        package_root="$(realpath -e -- "$package_root")" || { rm -f -- "$list_file"; return 1; }
        case "$package_root" in
            "$root"/*) ;;
            *) printf 'package source escapes the checkout: %s\n' "$package_root" >&2; rm -f -- "$list_file"; return 1 ;;
        esac
        [[ -z "${seen[$package_root]+present}" ]] || continue
        seen[$package_root]=1
        manifest="$package_root/package.anx"
        [[ -f "$manifest" ]] || { printf 'package manifest is missing: %s\n' "$manifest" >&2; rm -f -- "$list_file"; return 1; }
        files+=("$manifest")
        for relative in src native; do
            if [[ -d "$package_root/$relative" ]]; then
                while IFS= read -r -d '' dependency; do files+=("$dependency"); done \
                    < <(find "$package_root/$relative" -type f \( -name '*.an' -o -name '*.c' -o -name '*.h' \) -print0)
            fi
        done
        while IFS= read -r dependency; do
            [[ -n "$dependency" ]] && queue+=("$package_root/$dependency")
        done < <(awk '
            /^\[dependencies\][[:space:]]*$/ { in_dependencies = 1; next }
            /^\[/ { in_dependencies = 0 }
            in_dependencies && /path[[:space:]]*=[[:space:]]*"[^"]+"/ {
                line = $0
                sub(/^.*path[[:space:]]*=[[:space:]]*"/, "", line)
                sub(/".*$/, "", line)
                print line
            }
        ' "$manifest")
    done

    if [[ ! -d "$root/lib/src" ]]; then
        printf 'standard library sources are missing: %s/lib/src\n' "$root" >&2
        rm -f -- "$list_file"
        return 1
    fi
    while IFS= read -r -d '' dependency; do files+=("$dependency"); done \
        < <(find "$root/lib/src" -type f -name '*.an' -print0)
    printf '%s\0' "${files[@]}" | while IFS= read -r -d '' dependency; do
        realpath -z -e -- "$dependency"
    done | LC_ALL=C sort -zu >"$list_file"
    cat -- "$list_file"
    rm -f -- "$list_file"
}

yian_paths_sha256() {
    local root="$1" path relative length length_hex content_hash index
    shift
    local path_list encoded_file result
    path_list="$(mktemp "${TMPDIR:-/tmp}/yian-hash-paths.XXXXXX")" || return 1
    encoded_file="$(mktemp "${TMPDIR:-/tmp}/yian-hash-bytes.XXXXXX")" || { rm -f -- "$path_list"; return 1; }
    printf '%s\0' "$@" | LC_ALL=C sort -zu >"$path_list"
    : >"$encoded_file"
    while IFS= read -r -d '' path; do
        [[ -f "$path" ]] || { printf 'source input is not a file: %s\n' "$path" >&2; rm -f -- "$path_list" "$encoded_file"; return 1; }
        path="$(realpath -e -- "$path")" || { rm -f -- "$path_list" "$encoded_file"; return 1; }
        relative="${path#"$root"/}"
        [[ "$relative" != "$path" ]] || { printf 'source input is outside the checkout: %s\n' "$path" >&2; rm -f -- "$path_list" "$encoded_file"; return 1; }
        length="$(LC_ALL=C printf '%s' "$relative" | wc -c)"
        length="${length//[[:space:]]/}"
        printf -v length_hex '%016x' "$length"
        for ((index = 0; index < 16; index += 2)); do
            printf '%b' "\\x${length_hex:index:2}" >>"$encoded_file"
        done
        printf '%s' "$relative" >>"$encoded_file"
        content_hash="$(sha256sum -- "$path")"
        content_hash="${content_hash%% *}"
        for ((index = 0; index < 64; index += 2)); do
            printf '%b' "\\x${content_hash:index:2}" >>"$encoded_file"
        done
    done <"$path_list"
    result="$(sha256sum -- "$encoded_file")"
    result="${result%% *}"
    rm -f -- "$path_list" "$encoded_file"
    printf '%s\n' "$result"
}

yian_component_sha256() {
    local root="$1" component="$2" list_file path
    list_file="$(mktemp "${TMPDIR:-/tmp}/yian-component-files.XXXXXX")" || return 1
    yian_component_files "$root" "$component" >"$list_file" || { rm -f -- "$list_file"; return 1; }
    local -a files=()
    while IFS= read -r -d '' path; do files+=("$path"); done <"$list_file"
    rm -f -- "$list_file"
    yian_paths_sha256 "$root" "${files[@]}"
}

yian_resource_sha256() {
    local root="$1" list_file relative path
    list_file="$(mktemp "${TMPDIR:-/tmp}/yian-resource-files.XXXXXX")" || return 1
    for relative in lib/src runtime/src runtime/include; do
        [[ -d "$root/$relative" ]] || { printf 'resource source is missing: %s/%s\n' "$root" "$relative" >&2; rm -f -- "$list_file"; return 1; }
        find "$root/$relative" -type f -print0 >>"$list_file"
    done
    local -a files=()
    while IFS= read -r -d '' path; do files+=("$path"); done <"$list_file"
    rm -f -- "$list_file"
    yian_paths_sha256 "$root" "${files[@]}"
}

yian_tree_sha256() {
    local root="$1" exclude_marker="${2:-.native-install}" exclude_info="${3:-.build-info}" path
    local list_file
    list_file="$(mktemp "${TMPDIR:-/tmp}/yian-tree-files.XXXXXX")" || return 1
    while IFS= read -r -d '' path; do
        [[ "${path##*/}" == "$exclude_marker" || "${path##*/}" == "$exclude_info" ]] || printf '%s\0' "$path" >>"$list_file"
    done < <(find "$root" -type f -print0)
    local -a files=()
    while IFS= read -r -d '' path; do files+=("$path"); done <"$list_file"
    rm -f -- "$list_file"
    yian_paths_sha256 "$root" "${files[@]}"
}

yian_json_string() {
    local value="$1"
    value="${value//\\/\\\\}"
    value="${value//\"/\\\"}"
    value="${value//$'\n'/\\n}"
    value="${value//$'\r'/\\r}"
    printf '"%s"' "$value"
}
