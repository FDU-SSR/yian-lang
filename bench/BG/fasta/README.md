# BG/fasta

This case follows the Benchmarks Game `fasta-gcc-1` implementation and the
official FASTA task. The exact source extracted from the upstream page is
archived at `c/upstream/main.c`; `c/src/main.c` is a byte-identical build input
with no patch. `LICENSE-BenchmarksGame.txt` contains the upstream license. The
independent N=1000 complete FASTA output is
`fixtures/official-output.fasta`; its URL, argv and SHA-256 are recorded in
`provenance.json`.

The C program reads n from argv. YIAN’s `main` wrapper passes the selected n
to `workload.run(n)`; its `// bench-scale` marker supplies standalone default
25,000,000 and fast default 100,000. The runner passes the same n to C and
YIAN. The exact acceptance probe builds both references at n=1000.

## Function mapping and representation

| C source | YIAN source | Representation and behavior |
| --- | --- | --- |
| `gen_random` | `gen_random` | Both update the LCG state with the fixed IA/IC/IM constants and compute `maximum * seed / IM`. C keeps the seed in function-static `long` state; YIAN passes one owned `i64&` explicitly from `main`. |
| `makeCumulative` | `makeCumulative` | Both replace each probability with the cumulative sum in array order. YIAN stores ASCII symbols as `u8` and probabilities as `f64`. |
| `selectRandom` | `selectRandom` | Both draw once and use the source's binary search over cumulative probabilities. |
| `makeRandomFasta` | `makeRandomFasta` | Both emit the header and generated symbols in lines of at most 60 bytes. YIAN writes each line through a buffered stdout writer. |
| `makeRepeatFasta` | `makeRepeatFasta` | C copies the ALU sequence into a temporary mutable buffer; YIAN emits the same cyclic bytes using a modulo offset into the 287-byte source sequence. |
| `main` | `workload.run(n)` | Both accumulate IUB and Homo sapiens probabilities, then emit ONE at 2n, TWO at 3n, and THREE at 5n in that order. The same RNG stream continues from TWO into THREE. The YIAN wrapper passes the selected argv dimension to the workload, which owns generation and output. |

The IUB and Homo sapiens probabilities match the pinned source, including the
four distinct Homo sapiens values. Sequence text is complete FASTA output;
there is no checksum substitute. YIAN owns its amino-acid arrays and LCG state
and uses an 8192-byte `BufWriter<Stdout>` to batch writes. It releases this
state after flushing. All output bytes are ASCII, and line breaks and final
newlines are compared as bytes against the independent official fixture.
