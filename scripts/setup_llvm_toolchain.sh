#!/usr/bin/env bash
#
# Install missing LLVM and native C toolchain packages used by YIAN. Python
# package dependencies are managed separately through python/requirements.txt.
#
# By default, missing packages are installed from the system and apt.llvm.org
# repositories using sudo, then unversioned commands are switched to LLVM ${LLVM_MAJOR}.
#
# Usage: scripts/setup_llvm_toolchain.sh [options]
set -euo pipefail

LLVM_MAJOR=22
LLVM_PREFIX="/usr/lib/llvm-${LLVM_MAJOR}"
NATIVE_PACKAGES=(build-essential)
LLVM_PACKAGES=(
    clang-${LLVM_MAJOR}
    clang-tools-${LLVM_MAJOR}
    clang-format-${LLVM_MAJOR}
    clangd-${LLVM_MAJOR}
    lld-${LLVM_MAJOR}
    llvm-${LLVM_MAJOR}
    llvm-${LLVM_MAJOR}-dev
    llvm-${LLVM_MAJOR}-tools
    libclang-${LLVM_MAJOR}-dev
)
APT_PACKAGES=("${NATIVE_PACKAGES[@]}" "${LLVM_PACKAGES[@]}")
LLVM_KEY_URL="https://apt.llvm.org/llvm-snapshot.gpg.key"
LLVM_KEYRING="/etc/apt/keyrings/apt.llvm.org.asc"
LLVM_SOURCE_LIST="/etc/apt/sources.list.d/llvm-${LLVM_MAJOR}.list"

# Commands whose unversioned form is switched to the installed LLVM.  Each one
# is handled only when the corresponding binary exists in ${LLVM_PREFIX}/bin.
LLVM_TOOLS="clang clang++ clang-cpp clang-format clang-tidy clangd lld ld.lld \
opt llc llvm-as llvm-dis llvm-link llvm-ar llvm-ranlib llvm-nm llvm-objdump \
llvm-objcopy llvm-strip llvm-config llvm-symbolizer llvm-profdata llvm-cov"

CHECK_ONLY=0

usage() {
    cat <<EOF
Check and install the LLVM ${LLVM_MAJOR} toolchain used by YIAN.

Usage: scripts/setup_llvm_toolchain.sh [options]

Options:
  --check             Only report the current state; install nothing.
  -h, --help          Show this help.

Without --check, missing LLVM and native C toolchain packages are installed
from the system and apt.llvm.org repositories using sudo. Existing packages
are kept at their installed versions.

Typical fresh setup:

  conda create -n yian-env python=3.12 -y && conda activate yian-env
  python -m pip install -r python/requirements.txt
  scripts/setup_llvm_toolchain.sh
  export YIAN_CC="/usr/lib/llvm-${LLVM_MAJOR}/bin/clang"
  scripts/bootstrap_toolchain.sh --python "\$CONDA_PREFIX/bin/python"
  scripts/refresh_toolchain.sh --anx "\$HOME/.local/bin/anx" --yianc "\$HOME/.local/bin/yianc"
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
        -h|--help)           usage; exit 0 ;;
        *)                   die "unknown option: $1 (try --help)" ;;
    esac
done

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
        rm -f "$src" "$exe"
        die "$cc could not compile, link, and run a trivial program"
    fi
    rm -f "$src" "$exe"
}

package_is_installed() {
    local status
    status="$(dpkg-query -W -f='${db:Status-Status}' "$1" 2>/dev/null || true)"
    [[ "$status" == "installed" ]]
}

ensure_llvm_repository() {
    local codename
    local -a download_packages=()

    [[ -r /etc/os-release ]] || die "cannot determine the Linux distribution from /etc/os-release"
    # shellcheck disable=SC1091
    . /etc/os-release
    codename="${UBUNTU_CODENAME:-${VERSION_CODENAME:-}}"
    if [[ -z "$codename" ]] && command -v lsb_release >/dev/null 2>&1; then
        codename="$(lsb_release -cs 2>/dev/null || true)"
    fi
    [[ -n "$codename" ]] || die "cannot determine the distribution codename for apt.llvm.org"

    sudo install -d -m 0755 /etc/apt/keyrings
    if [[ ! -s "$LLVM_KEYRING" ]]; then
        package_is_installed ca-certificates || download_packages+=(ca-certificates)
        if ! command -v curl >/dev/null 2>&1 && ! command -v wget >/dev/null 2>&1; then
            download_packages+=(curl)
        fi
        if ((${#download_packages[@]} > 0)); then
            note "Installing HTTPS download prerequisites from the distribution repository ..."
            sudo apt-get update || die "apt-get update failed while preparing the repository key download"
            sudo apt-get install -y --no-upgrade "${download_packages[@]}" \
                || die "failed to install repository key download prerequisites"
        fi

        note "Downloading the official apt.llvm.org signing key ..."
        if command -v curl >/dev/null 2>&1; then
            if ! curl -fsSL "$LLVM_KEY_URL" | sudo tee "$LLVM_KEYRING" >/dev/null; then
                sudo rm -f "$LLVM_KEYRING"
                die "failed to download the apt.llvm.org signing key"
            fi
        elif ! wget -qO- "$LLVM_KEY_URL" | sudo tee "$LLVM_KEYRING" >/dev/null; then
            sudo rm -f "$LLVM_KEYRING"
            die "failed to download the apt.llvm.org signing key"
        fi
        sudo chmod 0644 "$LLVM_KEYRING"
    fi

    note "Configuring the official apt.llvm.org repository for ${codename} ..."
    printf 'deb [signed-by=%s] https://apt.llvm.org/%s/ llvm-toolchain-%s-%s main\n' \
        "$LLVM_KEYRING" "$codename" "$codename" "$LLVM_MAJOR" \
        | sudo tee "$LLVM_SOURCE_LIST" >/dev/null
}

install_missing_toolchain() {
    local package
    local -a missing=() missing_llvm=()

    command -v apt-get >/dev/null 2>&1 || die "automatic installation requires apt-get; use --check to inspect tools only"
    command -v dpkg-query >/dev/null 2>&1 || die "automatic installation requires dpkg-query; use --check to inspect tools only"
    command -v sudo >/dev/null 2>&1 || die "automatic installation requires sudo; use --check to inspect tools only"

    for package in "${NATIVE_PACKAGES[@]}"; do
        if ! package_is_installed "$package"; then
            missing+=("$package")
        fi
    done
    for package in "${LLVM_PACKAGES[@]}"; do
        if ! package_is_installed "$package"; then
            missing+=("$package")
            missing_llvm+=("$package")
        fi
    done

    if ((${#missing[@]} > 0)); then
        note "Missing system packages: ${missing[*]}"
        if ((${#missing_llvm[@]} > 0)); then
            ensure_llvm_repository
        fi
        sudo apt-get update || die "apt-get update failed"
        sudo apt-get install -y --no-upgrade "${missing[@]}" \
            || die "failed to install the missing LLVM toolchain packages"
    else
        note "All required system packages are already installed."
    fi

    for package in "${APT_PACKAGES[@]}"; do
        package_is_installed "$package" || die "required package is still missing after installation: $package"
    done

    switch_defaults_to "$LLVM_MAJOR"
    smoke_test_cc "${LLVM_PREFIX}/bin/clang"
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

# ---- install missing LLVM packages from apt.llvm.org (sudo) -----------------
note ""
note "Ensuring the LLVM ${LLVM_MAJOR} toolchain is installed ..."
install_missing_toolchain

# ---- final state ------------------------------------------------------------
note ""
note "Resulting state:"
report_state

note ""
note "Next:"
note "  scripts/bootstrap_toolchain.sh  # uses python3 by default"
note "  scripts/refresh_toolchain.sh --anx \"\$HOME/.local/bin/anx\" --yianc \"\$HOME/.local/bin/yianc\"  # full native refresh, including LSP"
note "  python3 scripts/run_tests.py --all -q"
note "  PYRIGHT_PYTHON_FORCE_VERSION=latest pyright   # strict, from the project root"
note "  export YIAN_CC=${LLVM_PREFIX}/bin/clang       # optional: pin the linker"
