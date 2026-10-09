# AGENTS.md

YIAN is a statically typed programming language with a Python compiler, a compiler written in YIAN, a standard library, a C runtime, a package manager, and editor tooling. The Python compiler implements native compilation and is checked with pyright in strict mode. Its compilation pipeline is:

`.an source → tokens → AST → desugared AST → HIR → CFG IR → LLVM IR → native executable`

`bootstrap/compiler/` implements source preparation and semantic analysis through checked HIR. It has no CFG lowering, LLVM emission, or native linking. Building that package uses the Python compiler; analyzing its own sources is a middle-end check, not native self-hosting.

| Component | Entry point | Responsibility |
| --- | --- | --- |
| Python compiler | `yianc`, `compiler/main.py` | Source analysis, CFG/LLVM lowering, native emission |
| Bootstrap compiler | `bootstrap/compiler/src/main.an` | Frontend JSON and source-to-HIR analysis |
| Package manager | `anx`, `anx/main.py` | Manifest validation, dependency graph, builds and package tests |
| Language server | `yian-lsp`, `lsp/main.py` | Editor features backed by Python analysis |
| Standard library | `lib/src/` | Language-level containers, numeric operations, I/O and CLI APIs |
| Runtime | `runtime/src/` | Allocation, runtime failures and Linux I/O support |

## Environment and installation

The supported development environment is Linux with Python 3.12 or newer (the compiler uses generic function syntax; `anx` reads manifests with `tomllib`) and the LLVM 22 toolchain: llvmlite 0.49 (which bundles LLVM 22), clang 22, and the LLVM tools (`opt`, `llvm-dis`, `llvm-link`, `llvm-config`). In this workspace, run compiler, package and validation commands in the `yian-env` conda environment:

```bash
conda run --no-capture-output -n yian-env yianc --help
conda run --no-capture-output -n yian-env anx --help
conda run --no-capture-output -n yian-env bash scripts/setup_llvm_toolchain.sh --check
```

`scripts/setup_llvm_toolchain.sh` installs llvmlite into the selected environment. `--with-clang-22` also installs system clang/LLVM packages and switches unversioned commands to 22; `--with-conda-clang` installs the tools into the conda environment. `--check` only reports. Native linking and package C-shim compilation select `YIAN_CC`, then `clang`, then `cc`. `runtime/build.py` uses `clang` and `llvm-ar` on PATH independently of `YIAN_CC`. LLVM consumer packages also validate their native toolchain requirements.

`pyproject.toml` defines the Python package, console scripts and strict pyright scope. Its `lsp` extra supplies `pygls`; the compiler and package manager do not require that extra. `requirements.txt` also contains benchmark and development dependencies, so it is not the minimal compiler dependency list.

```bash
conda run --no-capture-output -n yian-env bash scripts/install.sh
# Install the optional language-server dependency when needed.
conda run --no-capture-output -n yian-env python -m pip install -e '.[lsp]'
```

`scripts/install.sh` defaults to an editable install without dependency resolution. `--with-deps` enables dependency resolution; it does not select the `lsp` extra. `--regular` copies the Python packages and does not include `lib/`. An editable install reflects source edits without reinstalling; console-script and packaging metadata changes require reinstalling. Run the script in the intended environment, because the suites invoke installed `yianc` and `anx` commands.

## Working rules

- When modifying project code, do not automatically synchronize documentation. Update documentation only when the user explicitly requests it.
- If required tools, dependencies, or other environment setup are missing, it is acceptable to stop and ask the user to install or configure them. Resume after the user confirms the environment is ready; a workaround is not always required.
- Preserve unrelated local changes. Inspect the worktree before editing or committing.
- Complete the requested implementation and relevant existing checks within the authorized scope. Do not silently report success for unsupported semantics or replace a required operation with a placeholder.
- Use `apply_patch` for local file edits when practical.
- Tests, assertions, expected outputs and test tooling may be added or updated as needed for the requested work.
- Prefer adding test items or assertions to an existing relevant test file or fixture. Reuse its setup and compilation unit where practical; separate test files increase compilation overhead and total suite time.
- Create a new test file when extending an existing test is unsuitable, such as incompatible success/failure expectations, distinct diagnostic cases, or independent fixture/setup requirements. Do not combine cases in ways that mask failures or weaken coverage.
- Test additions and modifications must form test-only commits, separate from production code, documentation and all other changes. This includes test sources, fixtures, expected outputs, test inputs and dedicated test runners or validation tools. Do not mix test and non-test changes in one commit; the explicit-authorization requirement for committing still applies.
- When adding tests, if a test exposes a bug, do not weaken or alter its intended assertion just to make it pass; report the bug instead.
- The bootstrap compiler needs the core compilation functionality, not strict behavioral equivalence with the Python compiler. Presentation details such as token and AST output or diagnostic formatting may differ.
- Commit only when explicitly requested. Review tracked and untracked changes, run `git diff --check` and `git diff --cached --check`, stage only the requested scope, and report the commit plus remaining worktree changes. Do not push without authorization.
- Distinguish executed checks from static review and uncovered behavior. A generic declaration that builds without being instantiated has not been verified at run time; Python-suite success does not establish bootstrap semantic correctness.

## Python compiler CLI

For compilation and semantic analysis, always pass the standard library source root (`lib/src`) as a positional input, because the standard library is not implicit. Input paths may be files or directories; directories are searched recursively for `.an` files. Input-root order is preserved, with sorted files inside each directory. Formatting and frontend-only observation can use just the sources being inspected.

```bash
conda run --no-capture-output -n yian-env yianc lib/src tests/basic/array/access.an
conda run --no-capture-output -n yian-env yianc --analyze --json lib/src tests/basic/array/access.an
conda run --no-capture-output -n yian-env yianc -t none lib/src tests/basic/array/access.an
conda run --no-capture-output -n yian-env yianc -t ll lib/src tests/basic/array/access.an
conda run --no-capture-output -n yian-env yianc -o build/func -O2 lib/src tests/basic/array/access.an
conda run --no-capture-output -n yian-env yianc --dump --log-spec "type_check=DEBUG" lib/src tests/basic/array/access.an
```

Options include:

- `-t/--target {none,exe,ll,bc,obj,asm}`; the default is `exe`.
- `-o/--output PATH`.
- `-O {0..3}`; for non-`ll` targets this controls the LLVM optimization pipeline, backend optimization, and executable linking. `-t ll` emits unoptimized LLVM IR.
- `--profile`, `--dump`, `--log-spec`, `--log-file`, and `--packages`.
- `--frontend-json {tokens,ast,desugared}` prints a JSON frontend observation without semantic analysis or code generation.
- `--compiler-root PATH` (locate the standard library in a checkout) and `--raw-pointers`.
- `--allow-ffi` permits FFI declarations and operations in standalone sources; package builds use each package's `[package].ffi` value. `--link-lib NAME` and `--link-search DIR` add native libraries and search paths when linking an executable.
- `--analyze` runs lexical, syntax, and semantic analysis including compile-time branch selection, match coverage, and definite-assignment analysis, without entering CFG/LLVM emission or creating build output. It does not require `main`, but checks its signature when present. Text diagnostics go to stderr; `--json` prints one diagnostic object on stdout with `format`, `ok`, `stage`, `summary`, and `diagnostics`. Diagnostic ranges use zero-based LSP positions with UTF-16 character offsets.
- `--format` re-emits the input sources with canonical whitespace (stdout by default), `-w/--write` rewrites them in place, and `--check` lists the files that differ and exits nonzero.

`-t none` uses the compilation path: it runs semantic analysis and the CFG path, can create logging or dump output, and skips LLVM emission. Use `--analyze` for a source-to-HIR check without build output. The two options are not interchangeable entry points.

`--dump` on the compilation path writes `tokens.txt`, `ast.txt`, `hir.txt`, and `cfg.txt` under `build/` as their stages complete; code generation also writes `ir.ll`. `ir.ll` is a dump, separate from an explicitly selected `-t ll` output. `--token`, `--ast`, `--hir`, `--cfg`, and `--emit-llvm` are not supported options. Use one action mode per invocation; Python frontend observation takes precedence over analysis and formatting flags.

Executables default to `build/a.out`; LLVM bitcode and object outputs default to `build/<stem>.<ext>`, while assembly defaults to `<stem>.s` in the current directory. Pass `-o` whenever the output name matters.

`-t exe` links the runtime archive. `-t obj` uses a relocatable link to merge the user object, runtime object and package C shims. External system and LLVM library requirements still apply at final linking. `-t ll`, `-t bc` and `-t asm` leave runtime symbols external. Runtime artifacts are built on demand under `build/runtime/`. `runtime/build.py --check` checks ABI constants against `compiler/codegen/abi/lockmech.py`, `compiler/runtime_error.py` and `compiler/runtime_lib.py` and runs the normal self-test; `--asan` runs the ASan/UBSan self-test.

For standalone Python analysis, standard-library configuration uses `--compiler-root` (a checkout root), then `YIAN_LIB` (the `lib/src` directory itself), then `YIAN_ROOT` (a checkout root), then the checkout containing the imported compiler. Python package-mode analysis uses the package map's `std.sourceRoot`. `anx` uses the environment/checkout lookup and supplies the standard-library files itself; its CLI has no `--compiler-root` option. Root configuration classifies trusted sources and does not implicitly add source inputs to `yianc`.

`--packages pkg.json` enables package-name import resolution using the v2 contract: a top-level `format: 2` and `root`, plus a `packages` table whose entries carry `sourceRoot`, `kind`, `entry`, `dependencies`, `ffi` and optional `nativeLLVM`. It is normally generated by `anx build`.

Package ownership uses canonical source roots and path-component boundaries. Standalone trust comes from configured canonical roots, never from an input directory being named `lib`, `std`, or `tests`. Restricted-operation permission and FFI permission are distinct checks; raw-pointer mode does not grant either permission.

## Python analysis and code generation

`compiler/main.py` uses `AnalysisSession` for source-to-HIR analysis, then lowers and emits the selected definitions:

```text
lex
→ parse
→ desugar
→ prelude injection
→ restricted-operation check
→ global resolution
→ type finalization
→ type checking (compile-time branch selection and AST-to-HIR lowering)
→ match coverage analysis
→ definite-assignment analysis
→ CFG lowering
→ LLVM lowering
→ emission
```

`ExprChecker.lower_comptime_if` evaluates the typed condition and lowers only the selected AST branch to HIR. Match coverage and definite-assignment analysis inspect that HIR. Closure environments and calls are represented as ordinary aggregates and functions during CFG lowering. `IS_RAW_MODE` is a reserved compile-time configuration name; the compiler supplies its value for the active compilation configuration.

Ordinary `sizeof` expressions retain the queried type identity through HIR and CFG; LLVM lowering supplies the size constant. Immediate compile-time size queries use `compiler/target_layout.py` to provide the emitted target's layout. Do not substitute the Python host's object sizes or infer aggregate layout in semantic passes.

`compiler/analysis/session.py` is shared by native builds, analysis-only runs and LSP. Python sessions are reusable and start each analysis from source text. `DocumentStore` supplies in-memory overlays; results contain diagnostics, source snapshots, semantic facts and HIR. Editor callers can request syntax-only analysis or per-definition recovery. Native compilation uses strict failure and distinguishes all checked definitions from definitions selected for code generation.

## Bootstrap compiler

The bootstrap compiler is a hybrid `anx` package with a binary and importable modules. Its dependency on `bootstrap/json/` supplies reusable JSON parsing and writing. Build output is `bootstrap/compiler/build/app`; `anx` supplies the standard library and generates the package map used to build it.

```bash
conda run --no-capture-output -n yian-env anx build -O2 bootstrap/compiler
bootstrap/compiler/build/app --frontend-json desugared bootstrap/compiler/src/main.an
bootstrap/compiler/build/app --analyze --json --compiler-root . lib/src tests/basic/array/access.an
```

The bootstrap CLI requires exactly one of `--frontend-json {tokens,ast,desugared}` and `--analyze`. `--json` requires `--analyze`. Analysis also accepts `--packages`, `--compiler-root`, `--raw-pointers`, and `--allow-ffi`. It has no output-target, native-linking or source-formatting CLI. Frontend JSON preserves input order and stops at the first failure.

Analysis mode does not require `main`; a present root entry must be nongeneric, take no parameters and return `void`. Text success is silent and failures go to stderr. JSON emits one object with `success` and `diagnostics`, including warnings. Exit codes are 0 for success, 1 for preparation/analysis/output failure and 2 for argument errors. Its JSON schema differs from the Python CLI schema; do not assume byte-for-byte compatibility.

`--compiler-root` defaults to `.` and configures `<root>/lib/src` plus existing low-level test roots. The bootstrap CLI does not implement Python's `YIAN_LIB`/`YIAN_ROOT` fallback. Pass the checkout root explicitly when running outside the checkout. Standard-library sources remain explicit positional inputs. Package metadata supplies module ownership, dependency visibility and FFI permissions; preparation does not build or link its native metadata.

The compiler binary's build mode and the analyzed program's pointer mode are separate: `anx build --raw-pointers` builds a raw-pointer analyzer, while passing `--raw-pointers` to that analyzer selects raw semantics for its input. Verify both dimensions when changing pointer-sensitive bootstrap behavior.

### Pipeline and module boundaries

`AnalysisSession.prepare(...)` collects and loads sources, lexes, parses and desugars them, classifies permissions, and retains the prepared units. `AnalysisSession.analyze(options)` checks restricted operations, registers primitive types and declaration identities, resolves explicit imports and injects prelude bindings, resolves definition signatures and impls, registers automatic implementations, finalizes and validates types, checks and lowers procedures, verifies HIR, checks pattern coverage, and checks definite assignment.

The middle end supports aggregate and pointer operations, generic types and procedures, trait/method lookup, closures, trait objects, FFI, patterns and compile-time branch selection. It checks root procedures and discovered concrete instances rather than limiting checks to calls reachable from `main`. An unused generic declaration is not evidence that every possible instantiation is valid.

| Directory under `bootstrap/compiler/src/` | Responsibility |
| --- | --- |
| `common/` | Strong ID wrappers, source positions and traversal control |
| `diagnostics/` | Owned diagnostic model, sink, text and JSON rendering |
| `package/` | Package-map data, loading/validation and provenance queries |
| `frontend/` | Source management, preparation, lexer/parser, AST and desugaring |
| `analysis/session/` | Session ownership, preparation, analysis and output |
| `analysis/resolution/` | Declarations, imports, aliases, signatures and impl registration |
| `analysis/ty/` | Type models/keys/stores, relations, substitution, inference and semantic queries |
| `analysis/symbol/`, `analysis/definition/` | Symbols/scopes, callable bodies and instance worklist |
| `analysis/hir/` | Typed expression/pattern models, stable stores and read-only traversal |
| `analysis/lowering/` | Type checking with AST-to-HIR construction |
| `analysis/constant/` | Values, integer/float operations, intervals and typed evaluation |
| `analysis/passes/` | Restricted operations, procedure checking, HIR validation, coverage and assignment |

`common` does not depend on analysis. Package loading does not depend on AST or HIR. Diagnostic models and renderers are separate. HIR models do not depend on checkers, sessions or the constant evaluator. Constant arithmetic and interval tools do not depend on HIR; typed evaluation does. Keep reusable containers in the standard library and compiler-specific identity/ownership rules in the compiler.

### Ownership, traversal and size queries

- A bootstrap session has a one-way state transition: prepared → analyzing → complete or failed. It cannot be analyzed twice. Failed analysis leaves partial semantic data owned by the session so diagnostics can still be rendered.
- `AnalysisOutput` owns its procedure/parameter/capture lists and warning diagnostics. Its IDs refer to session-owned records. Release output before session; session-local IDs cannot be persisted or mixed between sessions.
- Source records, compilation units, AST, HIR, types, definitions and symbols use stable storage where references escape. `Arena<T>` has O(1) indexed lookup; allocation preserves existing element addresses. `clear`/`drop` invalidate all references and release storage without dropping element payloads. Owners release payloads first, then arena storage; owning stores must not be shallow-copied and independently released.
- Structural types go through `TypeStore` interning. Nominal definitions have separate identities and declared/complete states. Queries requiring a complete definition reject an incomplete record. Do not mutate interned data in ways that disagree with its key.
- `frontend/ast/walk.an` owns read-only child enumeration and `TryAstVisitor<E>`. `frontend/ast/traversal.an` adapts the existing infallible visitor to that core and keeps AST rewriting separate. Skip omits children and `leave`; errors propagate immediately without calling ancestor `leave`. Shared nodes are visited per incoming edge; error cleanup belongs in `defer`.
- AST additions must cover node payload cleanup, arena allocation, JSON formatting, traversal/rewriting and relevant frontend/lowering dispatch. HIR additions must cover payload cleanup, traversal, verification and consuming passes. Stable addresses do not permit changing graph connections during read-only traversal.
- `?` desugaring uses trait-qualified `Try.branch` and `FromResidual.from_residual` calls with single evaluation of the operand. Preserve its generated type sources and pattern metadata through lowering.
- Ordinary `sizeof` creates HIR with the queried `TypeId`. Immediate evaluation calls the optional borrowed `TypeSizeProvider` only when execution reaches the size query. The CLI supplies no provider and reports an explicit unsupported diagnostic for such a query. A short-circuited query does not call the provider; an unselected compile-time branch is not type-checked. There is no bootstrap LLVM layout adapter or default host layout.

## Tests and checks

Three suites live under `tests/` and share one runner:

```bash
conda run --no-capture-output -n yian-env python scripts/run_tests.py
conda run --no-capture-output -n yian-env python scripts/run_tests.py --suite safety
conda run --no-capture-output -n yian-env python scripts/run_tests.py --all -q
conda run --no-capture-output -n yian-env python scripts/run_tests.py -f call -v
conda run --no-capture-output -n yian-env python scripts/run_tests.py -x
conda run --no-capture-output -n yian-env python scripts/run_tests.py --no-run
```

`basic` is the default suite. `-q` prints summaries, `-v` includes failure details, `-f` filters names, and `-x` includes each suite's experimental sources. `--no-run` uses `-t none` for standalone sources; package fixtures use `anx check` unless their command override selects another action. It is not a bootstrap analysis run.

`basic` compiles standalone sources twice, in fat- and raw-pointer modes; `safety` holds the fat-pointer regressions; `package` drives `package.anx` project fixtures through `anx build`, also in both pointer modes. `tests/input/package/<fixture>.command` can select `build`, `check`, `test`, or `graph` with extra arguments such as `graph --json`. `scripts/run_safety_tests.py` is a thin wrapper for the safety suite.

Sources are under `tests/<suite>/`; expected results are under `tests/output/<suite>/`. A `.err.an` source must fail compilation, and its `.ans` file contains the required diagnostic substring. For a normal test, `.ans` contains expected stdout; a first line of `Exit code <N>` specifies an expected nonzero exit code. A directory test — a multi-file standalone test or a package fixture — uses `<dir>.ans`, or `<dir>.err.ans` for an expected failure. `tests/input/<suite>/<name>.args` and `.stdin` provide process arguments and standard input.

An existing `tests/input/<suite>/<test>.compile.json` file attaches compiler variants to that test. Each variant supplies a name, a string-array `compiler_args`, and at most one compile-error substring or expected runtime exit code. The runner expands these variants without duplicating the source test.

Test data lives under `tests/`; the runner and other Python tooling live under `scripts/`. Bootstrap package tests live under each package's `tests/` directory and run through `anx test`. There are no separate security or paper-evaluation runners in the common test workflow.

### Select checks by the affected layer

```bash
# Python syntax and strict type checking.
conda run --no-capture-output -n yian-env python -m compileall -q compiler anx lsp scripts
conda run --no-capture-output -n yian-env pyright

# Existing bootstrap frontend, AST lifecycle, visitor/rewriter and desugaring tests.
conda run --no-capture-output -n yian-env anx test bootstrap/compiler
conda run --no-capture-output -n yian-env anx test --raw-pointers bootstrap/compiler
conda run --no-capture-output -n yian-env anx test bootstrap/json

# Formatter fixtures, invariants and canonical-source check.
conda run --no-capture-output -n yian-env python -m compiler.format --cases
conda run --no-capture-output -n yian-env python -m compiler.format --verify bootstrap/compiler/src
conda run --no-capture-output -n yian-env python -m compiler.format --check bootstrap/compiler/src

# Runtime ABI consistency and existing normal/sanitized self-tests.
conda run --no-capture-output -n yian-env python runtime/build.py --check
conda run --no-capture-output -n yian-env python runtime/build.py --asan

git diff --check
```

For bootstrap semantic changes, build fat and raw binaries and invoke their `--analyze` directly on relevant existing sources and package maps. Save distinct binaries outside tracked sources when comparing both build modes, because builds share `build/app`. Include both analyzed pointer configurations where relevant. Standard-library, JSON package and compiler-source analysis exercise larger generic call chains.

The repository suites invoke the Python compiler; bootstrap package tests exercise their existing package sources. Neither substitutes for direct bootstrap middle-end checks. Runtime safety-failure cases can be valid semantic inputs: do not translate an expected runtime exit code into an expected analysis rejection. Record the size-provider limitation separately from unrelated failures. Do not use `--bless` or alter expected results merely to make checks pass.

## anx package manager

`anx/` implements `new [--kind bin|lib|hybrid]` (`--lib` is shorthand for `--kind lib`), `build`, `run`, `check`, `test`, `fmt [--check]` (formats the root package's own sources; `--check` prints the files that differ and exits nonzero), and `graph`. Projects declare metadata plus `[dependencies]` and `[dev-dependencies]` (visible only to `anx test`, and not compiled by a normal build) in `package.anx`; imports use package names. `anx` invokes `compiler.main` with a generated v2 `build/pkg.json` package map. `anx test` runs the project's own tests under `<project>/tests/`; the repository fixtures for anx itself live in `tests/package/` and run via `scripts/run_tests.py --suite package`.

`bin` supplies an executable entry, `lib` exposes importable sources without an entry, and `hybrid` supplies both. `anx build`/`run` do not emit a library artifact for `lib`; use `check`/`test` for those packages. Executables are written to the root project's `build/app`. `--release` selects `-O3` unless an explicit `-O` is supplied. `anx run [project] -- ...` forwards arguments to the program. `YIANC` overrides the compiler executable; otherwise `anx` uses `yianc` on PATH, then the importable Python compiler.

`anx/manifest.py` parses TOML and native declarations; `anx/project.py` validates package layout, dependencies, ownership and file indexing; `anx/project_tests.py` drives package-local tests. Keep the generated v2 map contract aligned with both compiler consumers. Native LLVM shim and link requirements come from `[native.llvm]`; the bootstrap analyzer treats these as data, while the Python native pipeline consumes them.

## Architecture

- `compiler/frontend/` contains the hand-written lexer and recursive-descent parser. Expression and statement parsing is in `frontend/parse/parser_expr.py`; type syntax is in `parser_type.py`.
- `compiler/analysis/passes/` contains desugaring, prelude injection, restricted-operation checking, global resolution, type checking, match coverage, and definite-assignment analysis.
- `compiler/analysis/lowering/` lowers expressions, calls, and closure bodies into HIR through `ExprChecker`, `CallDispatcher`, `OpBuilder`, and related helpers.
- `compiler/analysis/ty/` contains type representations, unification, generic inference, trait implementation lookup, dereference lookup, and intrinsic type identifiers. `TypeCtx` owns the type space and its semantic queries.
- `compiler/analysis/state.py`, `facts/`, `resolution/`, `unit/`, and `symbol/` contain shared semantic state, recorded name facts, type resolution, callable bodies, HIR compilation units, `DefPoint`, and symbol contexts.
- `compiler/analysis/documents.py`, `diagnostics.py`, and `queries/` provide document snapshots, structured diagnostics and protocol-neutral completion/navigation/refactoring queries. `compiler/interop/positions.py` owns conversion to LSP positions; semantic passes do not depend on the JSON-RPC server.
- `compiler/codegen/cfg/` contains CFG IR and its builder/translator; closure environments and calls become ordinary CFG aggregates and functions during CFG lowering.
- `compiler/codegen/llvm/` lowers CFG nodes with llvmlite and emits target files through `Emitter`.
- `compiler/format/` owns YIAN formatting, fixtures and invariant checks. `compiler/cli.py` handles compiler argument scanning; the standard-library `std.cli` provides the argument parser used by the bootstrap CLI.
- `lib/src/core/`, `num/`, `collections/`, and `ffi/` contain the standard-library primitives, containers, numerical support, synchronous I/O/filesystem/path APIs and FFI helpers.
- `bootstrap/compiler/` contains the YIAN frontend and middle end described above; `bootstrap/json/` provides general JSON parsing/writing and its own package tests.
- `bootstrap/ffi/` and `bootstrap/llvm/` are FFI consumer packages; the LLVM package uses LLVM 22 C API bindings and declares its C shim and link components through `[native.llvm]`.
- `runtime/src/{alloc,runtime,io}.c` provide allocation, runtime failures and synchronous Linux I/O. `runtime/include/` declares the ABI; `compiler/runtime_lib.py` builds runtime objects/archives for Python-generated programs.
- `lsp/` implements stdio JSON-RPC through `pygls`, document/workspace coordination, diagnostics, semantic tokens, formatting, completion, navigation and refactoring. Source analysis and semantic queries remain in `compiler/analysis/`. stdout belongs to the protocol; logs go to stderr or the configured file.
- `ide-support/` contains the VS Code extension. `bench/` contains benchmark sources, specifications, runners and result data. Check algorithmic equivalence before comparing C/raw/fat timings; retain the environment and commit context of recorded results.
- `docs/grammar/`, `docs/manual/`, and `docs/anx/` contain language and tooling documentation; `mkdocs_cn.yml` configures the documentation site. `licenses/` contains license texts.

## Language and standard-library resource contracts

The shared language supports postfix reference types such as `T&`. For a concrete non-trait `T`, the common bare representation of non-ZST `T&` has the same data-pointer ABI and size as `T*`; it does not provide borrow checking or lifetime inference. When `T` is a trait, `T&` is a trait object with dynamic method dispatch. Concrete safety metadata and runtime checking mechanisms are implementation-specific and must not be inferred from the common ABI description.

The Python backend defaults to instrumented fat pointers. `--raw-pointers` selects bare pointers without runtime pointer checks. Safety instrumentation spans `compiler/codegen/cfg/passes/insert_checks.py`, `compiler/codegen/llvm/pointer/`, the ABI definitions and the C runtime; changes must keep their contracts consistent.

Assignment, argument passing, and return use shallow value copies. They do not invalidate the source or perform implicit cleanup. `Move`, `.move()`, the `bitcopy` builtin, `@BitCopy`, compiler-provided `Clone`, and simple-type assignment restrictions are absent. `Clone` and `Drop` remain explicit standard-library traits. Pointers do not implement `Clone`; method lookup auto-dereferences `ptr.clone()` and clones the pointed-to value.

- Owning values must have one explicit resource owner. A shallow copy of a `String`, container, arena or session is an alias of its resources, not an independently releasable copy. Use `defer` for error-path cleanup and make ownership transfer points explicit.
- `Vec`, `HashMap`, `HashSet`, and `Arena` release their storage without recursively dropping contained resource payloads. `Vec`, `HashMap`, and `HashSet` cloning duplicates container storage with shallow element copies; inspect each type's contract before assuming a deep clone. `String` owns its buffer; `str` is a borrowed view.
- `Vec` growth and hash-table structural mutation can invalidate element references or iterators. Use arena-backed records for references that must survive append operations; use `Vec` for ID lists, queues and temporary buffers.
- Hash-table `insert_or_replace` and `remove_entry` return displaced key/value resources. `get_or_insert_with` returns an unused input key on a hit; the caller handles it. Existing `insert`/`remove` do not automatically release discarded resources. Do not structurally mutate a table during iteration.
- `Hash<T>.hash` receives `T&` so nested writes update the caller's accumulator. Equal values must hash equally; floating-point hashing canonicalizes signed zero. Composite keys explicitly implement equality and hashing over the same identity fields.
- `del` releases an allocation, not recursive field payloads. `Drop` is explicit; do not add implicit move, cleanup or deep-copy behavior to analysis or lowering.
- Buffered output requires explicit `flush()` when errors must be observed; `BufWriter.drop()` releases its buffer without flushing. Keep system-call ABI, errno and Linux behavior in the C runtime thin layer, with owned errors and UTF-8-facing APIs in YIAN.

## Conventions and gotchas

- Pyright strict checking covers `compiler/`, `anx/`, and `lsp/`; avoid `Any`, `object`, and `hasattr()`. Keep imports at the top of new files and preserve the existing handler-map dispatch style in the type checker and LLVM translator.
- Python private names use a **double** leading underscore: module-level functions and constants (`__helper`, `__CONSTANT`), methods (`self.__drain()`), nested helpers, and instance attributes. Use a **single** underscore only where the double form breaks the reference at run time: a name written as `__name` inside a class body is rewritten to `_ClassName__name`, so a *module-level* private that a class body mentions (for example `_SEP` in `compiler/utils/log.py`, `_is_within` in `compiler/analysis/source_provenance.py`) must keep one underscore. A private defined *and* used inside the same class body is `__name` like any other. Audit with `rg -n --glob '*.py' 'def _[^_]|^_[A-Za-z]|(self|cls)\._[^_]' compiler anx lsp scripts`; review matches against the name-mangling exception.
- Organize modules by responsibility and dependency direction. Split long functions at meaningful interfaces, use guard clauses where they reduce nesting, and separate small logical blocks with blank lines. Comments explain ownership, prerequisites, non-obvious choices and algorithmic boundaries rather than restating individual statements.
- Keep user-program errors distinct from internal invariant failures. Preserve source identity and associated locations through errors; detach each location to owned path data before destroying a session whose diagnostics must outlive it. Never infer analysis error categories from message text outside the frontend compatibility boundary.
- `bak/` is ignored legacy material and `.conda/` is a local environment; do not modify them without explicit authorization. `build/` directories contain generated compiler/package/test output: regenerate artifacts through the relevant tools, do not hand-edit or commit them as source changes.
- User-facing language documentation is written in Chinese; this agent instruction file uses English. Update documentation only when explicitly requested and verify facts against implementation, manifests and runnable commands. Documentation can be stale and is not evidence of an implemented feature.
- Write all code comments and docstrings in English, including those in tests, benchmarks, and examples. Keep user-visible text and test data in their intended language.
- Documentation states the repository as it is now. Do not narrate the iteration that produced it: no history or chronology (此前/现在/改为/曾经/第一版/本轮/后续/待办), no changelogs, no plans or promises, no comparison with an earlier state, and no defect-story framing. Write the current fact in the present tense — what the code does, what the rule is, what a value means — and let it stand on its own; when a rule exists because of a hazard, describe the hazard as a property of the current design instead of recounting the defect that motivated the rule. Keep the voice neutral: 「X 是 Y」, not 「X 改成了 Y」 or 「X 以前是 Z」. This applies to every persistent text in the repository: `docs/`, `README` files, code comments, docstrings, and commit messages.
- Code comments and persistent documentation must be self-contained: never cite a temporary document or its content — an implementation plan (such as those under `docs/plan/`), a proposal, a TODO list, a bug log, or review notes — and never cite rule, section, or definition numbers that exist only there. State the fact itself instead; referring to another persistent document is fine. Existing violations are left in place: fix one when the surrounding code or document is next touched.
- `CLAUDE.md` must remain a relative symbolic link to `AGENTS.md`.
- There is no repository CI or installed project pre-commit hook. YIAN formatting is implemented in `compiler/format/`, without a separate formatter configuration file. Run relevant existing checks locally and inspect whitespace before committing.
