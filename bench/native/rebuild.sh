#!/bin/sh
set -eu

root=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
case "$(uname -s):$(uname -m)" in
    Linux:x86_64) ;;
    *) echo "native benchmark libraries target Linux x86_64" >&2; exit 2 ;;
esac

CC=${CC:-clang}
AR=${AR:-llvm-ar}
RANLIB=${RANLIB:-llvm-ranlib}
CFLAGS=${CFLAGS:--O2 -fPIC -fno-lto}
LDFLAGS=${LDFLAGS:-}
JOBS=${JOBS:-2}
case "$JOBS" in
    ''|*[!0-9]*|0) echo "JOBS must be a positive integer" >&2; exit 2 ;;
esac
case " $CFLAGS " in
    *" -flto"*|*" -flto="*|*" -march=native"*|*" -mcpu=native"*)
        echo "CFLAGS must not enable LTO or host-native ISA tuning" >&2
        exit 2
        ;;
esac
export CC AR RANLIB CFLAGS LDFLAGS

work=$(mktemp -d "${TMPDIR:-/tmp}/yian-native-rebuild.XXXXXX")
trap 'rm -rf "$work"' EXIT HUP INT TERM

report_failure() {
    label=$1
    log=$2
    echo "$label failed; final log lines:" >&2
    tail -n 80 "$log" >&2
    exit 1
}

build_gmp() {
    src="$work/gmp"
    mkdir -p "$src"
    tar -xJf "$root/sources/gmp-6.3.0.tar.xz" -C "$src" --strip-components=1
    test -x "$src/configure"
    log="$work/gmp-configure.log"
    if ! (cd "$src" && ./configure --disable-shared --enable-static \
        --enable-fat --with-pic --disable-cxx ABI=64) >"$log" 2>&1; then
        report_failure "GMP configure" "$log"
    fi
    log="$work/gmp-make.log"
    if ! make -C "$src" -j "$JOBS" \
        fib_table.h fac_table.h sieve_table.h mpn/fib_table.c mp_bases.h \
        mpn/mp_bases.c trialdivtab.h mpn/jacobitab.h mpn/perfsqr.h >"$log" 2>&1; then
        report_failure "GMP generated tables" "$log"
    fi
    if ! make -C "$src/mpn" -j "$JOBS" >>"$log" 2>&1; then
        report_failure "GMP architecture-specific objects" "$log"
    fi
    if ! make -C "$src" -j "$JOBS" libgmp.la >>"$log" 2>&1; then
        report_failure "GMP static library build" "$log"
    fi
    test -s "$src/gmp.h"
    test -s "$src/.libs/libgmp.a"
    mkdir -p "$root/gmp/include" "$root/gmp/lib"
    cp "$src/gmp.h" "$root/gmp/include/gmp.h"
    cp "$src/.libs/libgmp.a" "$root/gmp/lib/libgmp.a"
}

build_pcre2() {
    src="$work/pcre2-src"
    build="$work/pcre2-build"
    mkdir -p "$src" "$build"
    tar -xzf "$root/sources/pcre2-10.47.tar.gz" -C "$src" --strip-components=1
    test -x "$src/configure"
    log="$work/pcre2-configure.log"
    if ! (cd "$build" && "$src/configure" --disable-shared --enable-static \
        --enable-pcre2-8 --disable-pcre2-16 --disable-pcre2-32 --enable-jit) >"$log" 2>&1; then
        report_failure "PCRE2 configure" "$log"
    fi
    log="$work/pcre2-make.log"
    if ! make -C "$build" -j "$JOBS" libpcre2-8.la >"$log" 2>&1; then
        report_failure "PCRE2 8-bit static library build" "$log"
    fi
    test -s "$build/src/pcre2.h"
    test -s "$build/.libs/libpcre2-8.a"
    mkdir -p "$root/pcre2/include" "$root/pcre2/lib"
    cp "$build/src/pcre2.h" "$root/pcre2/include/pcre2.h"
    cp "$src/src/pcre2posix.h" "$root/pcre2/include/pcre2posix.h"
    cp "$build/.libs/libpcre2-8.a" "$root/pcre2/lib/libpcre2-8.a"
}

build_gmp
build_pcre2
python3 "$root/record_manifest.py"
echo "Built GMP 6.3.0 and PCRE2 10.47 for Linux x86_64; no test suite was run."
