# BG/pidigits

This project follows the Benchmarks Game `pidigits-gcc-2` two-approximation
stream. The selected full and fast sizes are 10,000 and 1,000 digits. The
official C source is retained byte-for-byte in `c/upstream/main.c` and copied
unchanged to `c/src/main.c`; its revised BSD notice is in
`LICENSE-BenchmarksGame.txt`.

The website's 45-byte output download contains 30 digits. The local N=27 oracle
is derived independently from those official digits: keep the first 27 digits,
then apply the selected gcc2 source's ten-column row format, including three
spaces before the final `\t:27` marker. Both the downloaded bytes and the
derivation, output hash, and N=27 argv are recorded in `provenance.json`.

## Function mapping and representation

| C source | YIAN source | Representation and behavior |
| --- | --- | --- |
| `main` argv parsing (`atoi`) | `c_decimal`, `main` | Both take the decimal prefix of argv[1]. YIAN uses the standard argument vector and drops each owned string before dropping the vector. |
| `mpz_t` globals `n1`, `n2`, `d`, `u`, `v`, `w` | `workload.run` local `Option<BigInt>` values | Both keep six independent arbitrary-precision integers initialized to 4, 3, 1, 0, 0, 0. C stores `mpz_t` directly; YIAN owns six opaque handles allocated by the package shim and closes each initialized handle on every return path. |
| quotient estimates and `mpz_cmp` decision in `main` | `workload.run` | Each iteration computes both truncating quotients and emits a digit only when they compare equal. Otherwise it performs the same `k`/`k2` production updates in source order. |
| `// extract` and `// produce` GMP operations | `workload.run` calls to `tdiv_q`, `cmp`, `get_si`, `mul_si`, `mul`, and `add` | The project-local GMP 6.3.0 archive supplies the same arithmetic primitives to the C reference and the YIAN shim. The C shim wraps only individual GMP operations; all LFT state transitions and digit decisions remain in YIAN. |
| `putchar`, `printf`, final partial-row padding | `append_unsigned`, `workload.run` | YIAN appends bytes in ten-digit rows, pads a partial row with a right-aligned empty interpolation at the remaining dynamic width, and writes through `BufWriter`. C uses stdio. The N=27 probe checks the incomplete-row bytes. |

The YIAN binding's public surface is the inventoried `gmp.api` package. Its
opaque handles have explicit cleanup; there is no implicit `Drop` ownership or
whole-algorithm C entry point. The packaged GMP archive is the project-local
`bench/native/gmp/lib/libgmp.a`; CMake names that archive and its matching
header directly, and the YIAN package uses the same native archive through its
declared package dependency. No system-library lookup is used by this project.

The local N=27 probe built C, raw, and fat through the shared builder, ran each
once with argv `27`, and compared complete stdout bytes against
`fixtures/official-output-n27.txt`. This is a correctness probe, not a full-size
run or a performance measurement. The external GMP arithmetic archive is not
instrumented by YIAN guards or ASan.
