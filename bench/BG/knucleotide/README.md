# BG/knucleotide

This project follows the Benchmarks Game `knucleotide-gcc-1` task. It reads the
FASTA stream from stdin, selects the `THREE` record, encodes each nucleotide in
two bits, builds frequency maps for lengths one and two, sorts and prints those
tables, then computes the five requested sequence counts. The complete N=1000
FASTA input and official output are archived under `fixtures/`; the input is
provided to each process through a fresh stdin file descriptor.

The C source is archived unchanged at `c/upstream/main.c` and included unchanged
as `c/src/main.c`. Its only additional source dependency is the header-only
`khash.h`, pinned to klib commit
[`97a0fcb790b43b9e5da8994f4671021fec036f19`](https://github.com/attractivechaos/klib/blob/97a0fcb790b43b9e5da8994f4671021fec036f19/khash.h).
The header retains its MIT notice. This is source code, not an external binary
library.

## Function mapping and representation

| C source | YIAN source | Representation and behavior |
| --- | --- | --- |
| `element_Compare` | `comes_before`, `sort_counts` | Both order counts descending, then encoded keys ascending. The YIAN insertion sort handles the small one/two-nucleotide frequency tables; query counts use direct map lookup. |
| `generate_Frequencies_For_Desired_Length_Oligonucleotides` | `count_oligonucleotides`, `sorted_counts`, `append_frequencies` | Both use rolling two-bit keys, count every complete window in a real hash map, materialize unique key/count entries, and emit each oligonucleotide with the source's binary32 percentage rounded to three decimals through the standard library's `:.3f` specification. |
| `generate_Count_For_Oligonucleotide` | `count_oligonucleotides`, `append_query` | Both build a count map for each requested k, encode the query using A/C/G/T = 0/1/2/3, look up its key, and print the count, tab, and original query in the same order. |
| `main` | `read_three`, `workload.run()` | C reads lines from stdin and grows a byte buffer; the YIAN workload reads stdin in 8192-byte chunks, scans FASTA line boundaries, and stores encoded `THREE` bases in a `Vec<u8>`. Both stop at the next record and run lengths 1, 2, 3, 4, 6, 12, and 18. |

The C reference uses klib's open-addressed `khash` table and custom XOR/shift
hash. YIAN ports the same `uint64_t -> uint32_t` table into `yian/src/khash.an`:
the two-bit bucket flags, `key ^ (key >> 7)` hash, quadratic probing, 0.77 load
factor, and power-of-two growth match khash. The port omits deletion because
knucleotide never deletes; keys and values are stored in `Vec` buffers and a
resize allocates the next pair, rehashes, and releases the old pair. Bucket
assignment after a resize can differ from khash's in-place kick-out; capacity,
probing, load factor, and output match. Table iteration order is discarded
before output sorting. The official C pragmas
remain in the exact source; the benchmark CMake build does not enable OpenMP,
so this reference runs its sections serially. YIAN keeps reading, counting,
sorting, formatting, and query control flow in YIAN.
