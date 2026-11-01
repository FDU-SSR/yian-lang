# CVE mechanism witness project

The dataset contains 38 identifiers, each of which is a pair of programs under
`cve/<identifier>/`: `c/` is the vulnerable reference program and `yian/` is the
corresponding YIAN rewrite. Translation, representation differences and semantic
correspondence are reviewed uniformly against the
[C → YIAN specification](../docs/c-to-yian.md); this document records only the
current dataset and runner behavior. Each case README records its concrete
representation and local verification scope.

All 38 cases have a `README.md` recording the C-to-YIAN function correspondence,
representation differences and first illegal operation. Fifteen were rewritten
according to the note.md P0/P1 list and upstream records (the Linux cleanup case
rewrites only the YIAN side); the other 23 retain earlier minimized witnesses,
audited against the same contract: input is driven by the same `args` or the same
byte sequence, and hard-coded trigger values are derived from the input. The
mutable static tables of tx, svc_insts and tinyproxy use heap objects held by the
entry point. The read-only static arrays of ScreenPressor, FreeType, HyperKit,
LibreDWG, RemoteFX and stb_image use array constants and caller-held local
copies; the FreeType PNG signature also uses an array constant. Every array
element matches C item by item, and the storage representation is checked against
the read-only static variable clause.

Status return values and output parameters are represented separately: parsing,
decoding and allocation functions preserve C's status/count and write-back order;
nullable pointers use Option<pointer or slice>. HyperKit callers ignore the
failure status and continue using the retained iovec. This does not claim that
all 38 pairs fully conform to the specification. The project is independent of
the compiler regression tests under `tests/`; directory completeness or a guard
hit alone does not prove correspondence with the upstream defect.

## Layout

```text
cve/
  index.json
  CVE-2021-33304/
    case.json
    c/
      CMakeLists.txt
      include/reproducer.h
      src/main.c
      src/reproducer.c
    yian/
      package.anx
      expected.txt
      src/main.an
      src/scenario.an
      src/support.an
  CVE-2020-15999/
    README.md               # source-mapped witness notes
    case.json
    c/
    yian/
```

YIAN `main.an` is the process entry, `scenario.an` organizes the trigger input
and execution, and `support.an` holds the original witness's types and helper
functions; cases without helper definitions have no `support.an`. The package
name is `witness`, and imports use `witness.scenario` and `witness.support`. The
projects do not use FFI.

The C process entry and reproducer implementation are in `src/main.c` and
`src/reproducer.c`, with the interface in `include/reproducer.h`. CMake compiles
the translation units and links them as `build/reproducer`. `anx build` produces
`build/app` for YIAN.

## Witnesses and provenance

Each identifier keeps only one C project and one YIAN project currently in use;
`case.json` records that witness's arguments, ASan class, YIAN exit code and
safety diagnostic, and does not record old copies or per-case hashes.

Each case's `README.md` records the C-to-YIAN function correspondence, input and
first illegal operation, representation differences and run command; the 15
upstream rewrite cases additionally record the version/fix and replacement
facilities. All cases are reduced models and do not claim to be complete ports
of the upstream projects; some upstream projects are not C projects, and the C
files are models rather than original source.

### Language facilities and failure semantics

YIAN provides `@print`/`@println`/`@eprint`/`@eprintln` and `std.core.io`, but
the witnesses do not use standard output. C `printf` statements were removed
because they do not participate in triggering; the only one retained is
CVE-2022-31003's `printf("option o, value: %s", rest)`, which is itself the
carrier of the first out-of-bounds read. The runner compares only exit codes and
safety diagnostics, not stdout.

A failed `dyn` allocation is runtime resource error `R002`
(`yian: runtime error [R002]: memory allocation failed`): after writing to
standard error the program calls `_exit(1)` immediately, does not unwind the call
stack, and does not return NULL. C's `malloc` failure branch therefore has no
return value to correspond to; allocations on the trigger path do not fail.

The earlier C witnesses come from historical dataset commit
`70db192f4299eb63765f561999ef87ac5362704f`, with original paths
`tests/fat_cve/cve/<identifier>/<identifier>.c`. The current C projects adapt
that shape with a local entry: they include a local header and rename `main` to
`cve_reproduce`, with the trigger logic unchanged. This records only file
provenance and does not prove line-by-line correspondence with the upstream
defect.

## Running

Run from the repository root; the machine needs native `yianc` and `anx`
matching the sources. C comparison also needs CMake and a C compiler that
supports AddressSanitizer, clang by default. The runner does not install,
refresh or fall back to a toolchain.

```sh
python scripts/run_cve.py --list
python scripts/run_cve.py --check-layout
python scripts/run_cve.py --case CVE-2021-33304
python scripts/run_cve.py --case CVE-2021-33304 --language both
python scripts/run_cve.py --case CVE-2023-45664 --language both
python scripts/run_cve.py --case CVE-2020-15999 --language c --build-only
```

`--case` may be repeated and selects by full identifier exactly; a missing
selection exits 2 and does not implicitly run the whole set. `--list` and
`--check-layout` start no compiler. The layout check verifies that project
files, `expected.txt` and `case.json` expectations agree. `--build-only`
verifies only the build, reports `BUILD_ONLY`, and does not mean the
vulnerability was triggered.

`--language yian|c|both` defaults to `yian`. `--anx`, `--yianc` and `--cc` select
tools and default to `ANX`, `YIANC` and `CC` respectively, falling back to PATH.
`--timeout` defaults to 60 seconds per child process. YIAN builds with `-O0` and
default checked fat pointers. CMake enables ASan and disables leak checking to
focus on memory access and release errors. Clang builds explicitly enable
use-after-return detection. Both sides use the same `args` from `case.json`.

The whole-set command is `python scripts/run_cve.py --all`, selecting 38 YIAN
projects; adding `--language both` selects 38 C projects and 38 YIAN projects.
In agent work these are batch runs requiring explicit authorization;
documentation examples do not constitute execution authorization. The CVE phase
of `scripts/rerun_paper_experiments.sh` invokes only the default YIAN selection
and does not include the compiler safety suite or C comparison.

## Verdict and reporting

- YIAN: the build must succeed, the run exit code must be 1, and the output must
  contain the complete safety diagnostic declared for the case.
- C: the build must succeed and the run must complete. The original defect may
  exist silently; whether ASan fires is only an observation (the report's `asan`
  field), not a failure condition. `c.expected_asan` describes the expected
  observation class. A complete directory does not mean every C project can
  trigger the corresponding error.
- A build failure, timeout, ordinary crash or diagnostic mismatch is a failure
  and does not count as a safety check success.
- Success exits 0, a selection error exits 2, and a tool, build or run
  verification failure exits 1.

Output logs and `report.json` are under `build/cve/`; the report records the
executions actually selected, tool paths, tool binary hashes and current project
source hashes. Each run overwrites the summary report, and only the selected
scope in the report can explain the results. Earlier positive/negative results
are not equivalent to one complete run of this layout.
