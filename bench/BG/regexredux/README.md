# BG/regexredux

This project follows the Benchmarks Game `regexredux-gcc-5` task. It reads a
FASTA byte stream from stdin, removes record descriptions and newlines, counts
nine regular expressions, applies five ordered replacements, and prints the
original, cleaned, and replaced byte lengths. Full and fast input sizes are
5,000,000 and 100,000; the local correctness probe uses the fixed official
N=1000 input and output under `fixtures/`.

The upstream C source is retained unchanged at `c/upstream/main.c` and copied
unchanged to `c/src/main.c`. Its revised BSD notice is retained in
`LICENSE-BenchmarksGame.txt`. CMake explicitly selects the project-local
PCRE2 10.47 8-bit static archive and its matching headers. The YIAN project
depends on the `pcre2` package, which builds the narrow C ABI shim against the
same archive. JIT compilation is mandatory; any pattern that fails compile or
`PCRE2_JIT_COMPLETE` causes the program to fail rather than use the interpreter.

## Function mapping and representation

| C source | YIAN source | Representation and behavior |
| --- | --- | --- |
| `replace` | `replace_into` | Both search from the previous match end, append unmatched source bytes and replacement bytes, then append the remaining suffix. YIAN uses explicit byte offsets and two `Vec<u8>` buffers; `Vec.take()` swaps their backing allocations between the five replacement passes. Each PCRE2 pattern is compiled with options 0, JIT-compiled, used for the pass, and freed. |
| `main` input read and first cleanup | `read_input`, `process`, `replace_into` | Both read all stdin bytes and remove FASTA headers/newlines with `>.*\n|\n`. C starts with 16 KiB capacity and grows the `string`; YIAN reads in 16 KiB chunks into an owned vector. |
| `count_Info` loop and output | `count_pattern`, `count_matches`, `append_unsigned`, `process` | Both count all non-overlapping matches for the nine patterns in the original order, then emit each pattern and count. YIAN keeps the loop and counts in language code and formats the exact output bytes. |
| `replace_Info` loop and final sizes | `replacement_pattern`, `replacement_text`, `replace_into`, `process` | Both apply all five pattern/replacement pairs in source order using two buffers, then print original input size, cleaned size, and post-replacement size. |
| PCRE2 matcher/context/JIT stack setup in `main` | `workload.run()`, `pcre2.api.create_matcher`, `destroy_matcher` | C and YIAN each own a match context, a 16-pair match-data block, and a 16 KiB JIT stack. YIAN represents handles as private opaque pointers and explicitly destroys pattern and matcher handles; the shim holds no subject pointer after a synchronous match. |

The C source's OpenMP pragmas are inactive because this project's CMake build
does not enable OpenMP; the selected C reference therefore runs serially, as
does YIAN. The shim implements only matcher/pattern lifecycle, one PCRE2 JIT
match call, and returned offsets. YIAN retains input reading, scanning loops,
buffer growth/append/swap, counts, replacement order, output formatting, and
cleanup of each pattern.

The local probe built C, raw, and fat through the shared builder. Each process
received a fresh stdin descriptor for the 10,245-byte official N=1000 FASTA
fixture and returned the complete 262-byte output byte-for-byte
(`9f417691b946e86f2a86df4340c17f8b5c7bd5f977df525072c66c89b8a656f1`). This
is a correctness probe, not a full-size run or performance measurement. The
packaged PCRE2 archive itself is not instrumented by YIAN guards or ASan.
