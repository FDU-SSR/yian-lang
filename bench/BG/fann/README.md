# BG/fann

This case follows the Benchmarks Game `fannkuchredux-gcc-1` implementation and
the official fannkuch-redux task. The exact source extracted from the upstream
page is archived at `c/upstream/main.c`; `c/src/main.c` is a byte-identical
build input, with no C patch. `LICENSE-BenchmarksGame.txt` contains the
upstream license. The independent N=7 expected output is
`fixtures/official-output.txt`; its URL, argv, and SHA-256 are recorded in
`provenance.json`.

The C program parses n from argv and defaults to 7. YIAN’s `main` wrapper
parses the selected n and passes it to `workload.run(n)`; its `// bench-scale`
marker supplies standalone default 11 and fast default 9. The runner passes
the same selected n to C and YIAN. The exact N=7 acceptance probe builds both
references at 7.

## Function mapping and representation

| C source | YIAN source | Representation and behavior |
| --- | --- | --- |
| `max` | `max` | Both select the larger of the current maximum and the latest flip count. |
| `fannkuchredux` | `fannkuchredux` | Both enumerate every permutation in the source order, reverse prefixes of length `perm[0] + 1` until the first element is zero, update the maximum, and add or subtract each count according to permutation parity. C uses three VLAs of length n; YIAN uses three separately allocated, n-element `i32[]` slices and drops each before return. C prints the checksum from this function and returns the maximum; YIAN returns both values in `FannResult` for `main` to print. |
| `main` | `workload.run(n)` | C reads n from argv and defaults to 7. The YIAN wrapper passes the selected n to the workload, which prints the signed checksum first and `Pfannkuchen(n) = maximum` second. The wrapper exits with the returned status; the maximum is output data, not a process status. |

The YIAN implementation keeps the C permutation counter, counter-array
initialization and decrement schedule, prefix reversal loop, parity rule, and
termination point. The dynamic slices contain exactly n integers; the probe
and configured profiles use n=7, 9, and 11. The oracle remains the upstream
two-line output and is not generated from either candidate implementation.
