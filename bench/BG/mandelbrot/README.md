# BG/mandelbrot

This project implements the scalar `mandelbrot-gcc-2` task. For each row and
column it starts `Z` at zero, performs up to 50 iterations while
`Tr + Ti <= 4.0`, and emits a P4 bitmap. Pixels are packed most-significant bit
first; unused low bits at the end of each row are zero. The exact upstream C
source is archived at `c/upstream/main.c`. `c/src/main.c` differs only by using
an unsigned byte accumulator so its left shifts have defined wrap behavior.
The patch and all source/license/oracle hashes are recorded in
`provenance.json`.

The official N=200 bitmap is the binary fixture `fixtures/official-output.pbm`.
Compare it as bytes, including the P4 header, packed rows, and padding; do not
decode the file as UTF-8. The separate N=9 probe exercises a one-bit final byte
on every row and checks the zero-filled low seven bits against an independent
scalar packing formula. This is the BG bitmap task, not the AWFY/mand checksum
benchmark.

## Function mapping and representation

| C source | YIAN source | Representation and behavior |
| --- | --- | --- |
| `main` | `workload.run(n)` | Both traverse rows then columns, compute `Cr`/`Ci` from the same scalar `f64` formulas, run the same 50-iteration escape loop, and classify a pixel from `Tr + Ti <= 4.0`. C uses a floating loop variable and a byte accumulator; YIAN uses bounded integer coordinates and a `u64` accumulator. Each completes one output byte every eight pixels, packs the leftmost pixel into the high bit, and resets at each row. |
| `main` P4 header and row write | `workload.run(n)` P4 header and `BufWriter<Stdout>` | C prints the header and calls `putc` for each completed row byte. The YIAN workload writes the same ASCII header, accumulates one row in a `Vec<u8>`, then writes its exact byte slice through a buffered stdout writer. On a partial final byte, both shift the used bits to the high end and leave low padding bits zero. |

The official C uses plain `char` for left shifts; the build input changes this
field to `unsigned char` to make the intended eight-bit packing explicit,
including the N=9 padding probe. No checksum or text rendering replaces the
bitmap output.
