# BG/binarytree

This case follows the Benchmarks Game `binarytrees-gcc-1` implementation and
the official binary-trees task. The exact source extracted from the upstream
page is archived at `c/upstream/main.c`; `c/src/main.c` is a byte-identical
build input, so the C implementation has no patch. `LICENSE-BenchmarksGame.txt`
contains the upstream license. The independently published N=10 expected
output is `fixtures/official-output.txt`; its URL, arguments, and SHA-256 are
recorded in `provenance.json`.

The C program reads N from argv. YIAN’s `main` wrapper parses the selected N
and passes it to `workload.run(n)`. Its `// bench-scale` marker supplies the
standalone default and stages the fast profile; the runner passes the same N
to C and YIAN. The exact N=10 acceptance probe builds YIAN at 10 and passes
`10` to C.

## Function mapping and representation

| C source | YIAN source | Representation and behavior |
| --- | --- | --- |
| `NewTreeNode` | `NewTreeNode` | Both create one node per call. C uses `malloc(sizeof(treeNode))`; YIAN uses `dyn treeNode(left, right)`. |
| `ItemCheck` | `ItemCheck` | Both count each node and recursively sum both children. YIAN represents an empty child as `None` in `Option<treeNode&>`; every leaf still has the same two-child node layout as an interior node. |
| `BottomUpTree` | `BottomUpTree` | Both recursively build a complete tree, allocating one equally laid-out node at each position, including leaves. |
| `DeleteTree` | `DeleteTree` | Both recursively release children before the node. C calls `free`; YIAN uses `del` on each node after its children. |
| `main` | `workload.run(n)` | Both select `max(minDepth + 2, N)`, check and release the stretch tree, retain the long-lived tree while creating and releasing every per-depth tree, print each official check line, then check and release the long-lived tree. The YIAN entry wrapper parses N and passes it to the workload; C and YIAN receive the same argv value. |

The output has the stretch-tree check, one check for every even depth beginning
at 4, and the long-lived-tree check. It does not collapse the temporary trees
into one aggregate or use a custom arena/free-list. Tree construction keeps the
official left/right recursive allocation order. The C implementation returns
`long` from `ItemCheck`; YIAN uses `i64` for the corresponding result.

The accepted N=10 run is compared byte-for-byte with the archived official
six-line output. That probe establishes this input and these three build modes;
it is not a performance measurement or a full-scale benchmark result.
