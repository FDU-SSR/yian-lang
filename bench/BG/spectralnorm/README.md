# BG/spectralnorm

This case follows the Benchmarks Game `spectralnorm-gcc-3` implementation and
the official spectral-norm task. The exact source extracted from the upstream
page is archived at `c/upstream/main.c`. The C build input makes one recorded
C11 linkage patch: `A` changes from `inline` to `static inline`, giving the
translation unit a local definition without changing the formula or arithmetic
order. `LICENSE-BenchmarksGame.txt` contains the upstream license. The
independent N=100 output is `fixtures/official-output.txt`; its URL, argv, and
SHA-256 are recorded in `provenance.json`.

The C program reads n from argv. YIAN’s `main` wrapper passes the selected n
to `workload.run(n)`; the `// bench-scale` marker supplies standalone default
8000 and fast default 1000. The runner passes the same n to C and YIAN. The
exact acceptance probe builds both references at n=100.

## Function mapping and representation

| C source | YIAN source | Representation and behavior |
| --- | --- | --- |
| `A` | `A` | Both compute `((i+j)*(i+j+1)/2)+i+1`; YIAN uses u64 indices and returns the bounded matrix element as i32. |
| `dot` | `dot` | C uses double pointers and YIAN uses checked f64 slices. Both sum products in increasing index order. |
| `mult_Av` | `mult_Av` | Both use serial i-then-j loops and add `v[j] / A(i,j)` in the source order. |
| `mult_Atv` | `mult_Atv` | Both use serial i-then-j loops and add `v[j] / A(j,i)` in the source order. |
| `mult_AtAv` | `mult_AtAv` | C uses global `tmp`; YIAN passes the same owned temporary slice explicitly. Both compute `Av` and then `Atv`. |
| `main` | `workload.run(n)` | Both initialize u to one, execute exactly ten pairs `AtAv(u,v)` then `AtAv(v,u)`, and print `sqrt(dot(u,v)/dot(v,v))` to nine fractional digits. The YIAN wrapper passes the selected argv dimension to the workload, which owns the calculation and output. |

YIAN allocates u, v, and tmp as three n-element f64 slices and drops them after
printing. The upstream C leaves its three malloc buffers for process teardown.
No normalization is performed inside the iteration loop, and no additional
AtAv is run before the final dot products. The C compiler uses the plan's
`-ffp-contract=off` flag; the scalar loops preserve the source's f64
accumulation order and do not enable OpenMP.

The YIAN-only `print_fixed_9` helper converts the positive result to the
nearest integer at a 1e-9 scale and emits exactly nine fractional digits. It
implements the output precision of `printf("%.9f")`; it does not change the
spectral-norm calculation. The N=100 acceptance result is checked byte-for-byte
against the independently archived oracle, which is not produced by either
candidate program.
