# Local native benchmark dependencies

This directory contains the pinned external libraries used by the
`BG/pidigits` (GMP) and `BG/regexredux` (PCRE2 8-bit) projects. C, raw, fat,
and ASan variants link the same project-local static archives. Library code is
not compiled with ASan or YIAN guards; arithmetic and regex operations inside
these archives are outside YIAN checking.

## Fixed inputs and artifacts

| Library | Source | Target | Header and archive |
| --- | --- | --- | --- |
| GMP 6.3.0 | [GNU release archive](https://ftp.gnu.org/gnu/gmp/gmp-6.3.0.tar.xz) | Linux x86-64 LP64, ABI=64, PIC, static, `--enable-fat` runtime dispatch, C++ disabled | `gmp/include/gmp.h`, `gmp/lib/libgmp.a` |
| PCRE2 10.47 | [10.47 release archive](https://github.com/PCRE2Project/pcre2/releases/download/pcre2-10.47/pcre2-10.47.tar.gz), tag commit `f454e231fe5006dd7ff8f4693fd2b8eb94333429` | Linux x86-64, 8-bit only, PIC, static, JIT enabled | `pcre2/include/pcre2.h`, `pcre2/include/pcre2posix.h`, `pcre2/lib/libpcre2-8.a` |

`manifest.json` records the SHA-256 of each archive, configured header, license,
and source tarball, along with compiler, linker, archiver, libc, host, flags,
ISA, JIT, and unresolved external symbol requirements. The archives were built
with Clang 22.1.8 on Ubuntu 22.04 with glibc 2.35 and GNU ld 2.38. This records
the verified build host; it does not claim compatibility with every glibc or
CPU. The declared target is Linux x86-64 LP64.

Original source tarballs and detached signatures are under `sources/`.
GMP's archive SHA-256 is
`a3c2b80201b89e68616f4ad30bc66aee4927c3ce50e33929ca819d5c43538898`; its
detached signature SHA-256 is
`94def8c1a731854de684689126046ec93589147abd4cd0025f12d741d323aa82`. The
signature verified with the GNU release key for Niels Möller,
`343C2FF0FBEE5EC2EDBEF399F3599FF828C67298`.

PCRE2's archive SHA-256 is
`c08ae2388ef333e8403e670ad70c0a11f1eed021fd88308d7e02f596fcd9dc16`; its
detached signature SHA-256 is
`113b9d73680472e3d7e51e7e4c6bce9b48a9a1db4d986adcea17a35ac97a5326`. The
signature verified with release signing subkey
`BACF71F10404D5761C09D392021DE40BFB63B406` (primary key fingerprint
`A95536204A3BB489715231282A98E77EB6F24CA8`). GnuPG reported that key as
untrusted in the local trust database.

The license files are copied from their respective source distributions into
`licenses/` and individually hashed by the manifest. GMP's GPL/LGPL options
are retained in full; PCRE2's license includes its JIT exception.

## Offline rebuild

The build helper reads only the checked-in source archives and writes the two
static archives and configured public headers in this directory. It does not
download sources, install system libraries, run `make check`, or build the
benchmark projects. Host tools required are GNU make, `m4`, Clang, `llvm-ar`,
`llvm-ranlib`, Python 3, GNU/LLVM `nm`, `readelf`, `ld`, `ldd`, and the host C
development files. A compatible build toolchain must be supplied separately.

From the YIAN repository root, run:

```sh
CC=clang AR=llvm-ar RANLIB=llvm-ranlib \
CFLAGS='-O2 -fPIC -fno-lto' LDFLAGS= JOBS=2 \
  ./bench/native/rebuild.sh
```

GMP is configured with `ABI=64 --enable-fat --with-pic --disable-shared
--enable-static --disable-cxx`. Its architecture-specific objects are built in
the `mpn` subdirectory before the single `libgmp.la` target; generated tables
are explicit prerequisites because a direct library target does not request
Automake's full `BUILT_SOURCES` set. PCRE2 is configured for the 8-bit library
only with JIT enabled, and only `libpcre2-8.la` is requested. Both use PIC,
`-O2`, and `-fno-lto`; the helper rejects LTO and host-native ISA flags.

The `package.anx` manifests in `gmp/` and `pcre2/` expose explicit local header
and archive paths for native C consumers. Normal benchmark builds consume
these checked-in files and do not invoke this rebuild script or a system
package manager.
