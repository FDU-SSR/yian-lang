# BG/revcomp

This case follows the Benchmarks Game `revcomp-gcc-5` implementation and the
official reverse-complement task. The exact source extracted from the upstream
page is archived at `c/upstream/main.c`; `c/src/main.c` contains the minimal
read/write, byte-index, allocation-error and cursor-boundary patch recorded in
`provenance.json`. The patch handles interrupted and partial system calls and
avoids forming the source's pointer-before-buffer cursor. It does not alter
output for valid FASTA input. `LICENSE-BenchmarksGame.txt` contains the upstream
license.

The input fixture `fixtures/fasta-n1000-input.fasta` is the independently
archived output from the official fasta task (10,245 bytes). The official
reverse-complement result is `fixtures/official-output.fasta` (10,245 bytes).
Both URLs, SHA-256 values, and the empty argv are recorded in `provenance.json`.
Neither fixture is generated from the YIAN candidate.

## Function mapping and representation

| C source | YIAN source | Representation and behavior |
| --- | --- | --- |
| `process` | `process` | C receives start/end pointers, finds the sequence after the header, inserts line breaks in place, then reverses/complements bytes. YIAN receives the input byte slice and record/header/sequence offsets, walks sequence offsets backward, skips existing LF bytes, complements each symbol, and writes output lines of at most 60 bytes. Both retain the header and task record order. |
| `main` | `workload.run()` | C reads with `read`/`realloc` and processes records in place. The YIAN wrapper calls the workload, which reads real stdin in 8192-byte chunks into a growing `Vec<u8>`, scans headers forward and calls `process` with u64 offsets. Both use the same IUPAC complement pairs and emit the complete FASTA output. |

YIAN uses a 256-entry table indexed by `u8`, so input bytes cannot become
negative table indexes. It stores no reverse cursor before the input buffer
base. `BufWriter<Stdout>` batches each record's header and sequence output;
`write_all` handles short writes. The C reference's read path uses `ssize_t`
and distinguishes EOF from errors; its output loop retries interrupted writes
and completes partial writes. The official input contains three records, and
the N=1000 acceptance run compares the entire output byte-for-byte with the
independent official fixture.
