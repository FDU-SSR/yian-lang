# BG/nbody

This project implements the Benchmarks Game `nbody-gcc-1` task: initialize the
Sun, Jupiter, Saturn, Uranus, and Neptune; offset the Sun velocity to zero total
momentum; print system energy; advance the five bodies by `dt = 0.01` for the
requested number of steps; and print energy again. The exact upstream C source
is archived at `c/upstream/main.c` and copied unchanged to `c/src/main.c`.
`LICENSE-BenchmarksGame.txt` carries its license. The independent N=1000 output
fixture and source hashes are recorded in `provenance.json`.

## Function mapping and representation

| C source | YIAN source | Representation and behavior |
| --- | --- | --- |
| `advance` | `advance` | C mutates through two planet pointers; YIAN loads and stores `Planet` values through a checked `Planet[]` slice. Both visit pairs in `i` then `j = i + 1` order, update velocities for each pair, and update positions in body order after all pairs. |
| `energy` | `energy` | Both accumulate each body's kinetic term and then its `j > i` potential terms in the same order, using the same `f64` expressions and square root. |
| `offset_momentum` | `offset_momentum` | Both sum each body's momentum in array order and set the Sun's three velocity fields using the solar mass. |
| file-scope `bodies` initializer | `init_bodies` | C initializes a global five-element array; YIAN builds the same five bodies in a local fixed array, then passes its full slice to the task functions. The numeric constants and expression order are preserved. |
| `main` | `workload.run(n)` | C reads the step count from argv. The YIAN wrapper passes it to the workload; the `// bench-scale` marker supplies standalone default 5,000,000 and fast default 100,000. Both offset momentum once, print initial energy, perform exactly N advances at `0.01`, and print final energy. |
| `printf("%.9f\\n", ...)` | `@println(f"{energy:.9f}")` | The standard library emits the exact binary value rounded to nine fractional digits, so the C format's precision, rounding, sign and newline are preserved without a local helper. |

This is the Benchmarksgame five-body task with two energy outputs. The separate
AWFY/nbody benchmark has a different input contract and workload; its evidence
does not validate this project. The acceptance probe builds C/raw/fat once each
and compares the complete N=1000 stdout to the archived official fixture.
