# AGENTS.md

YIAN is a statically typed programming language with a Python compiler, a compiler written in YIAN, Python and native package managers, a standard library, a C runtime, and editor tooling. The Python compiler is the reference implementation; the self-hosted compiler and package manager are in `bootstrap/compiler/` and `bootstrap/anx/`. The native compiler supports the same source-to-executable pipeline:

`.an source → tokens → AST → desugared AST → HIR → CFG IR → LLVM IR → native executable`

| Component | Entry point | Responsibility |
| --- | --- | --- |
| Python compiler | `python -m compiler.main`, `compiler/main.py` | Reference source analysis, CFG/LLVM lowering, native emission |
| Native compiler | installed `yianc`, `bootstrap/compiler/src/main.an` | Source analysis, CFG/LLVM lowering, native emission |
| Python package manager | `python -m anx.main`, `anx/main.py` | Reference manifest validation, dependency graph, builds and package tests |
| Native package manager | installed `anx`, `bootstrap/anx/src/main.an` | Native manifest validation, dependency graph, builds and package tests |
| Language server | `yian-lsp`, `lsp/src/main.an` | Native stdio LSP backed by the self-hosted compiler and package manager |
| Standard library | `lib/src/` | Language-level containers, numeric operations, I/O and CLI APIs |
| Runtime | `runtime/src/` | Allocation, runtime failures and Linux I/O support |

## Environment and installation

The supported development environment is Linux with Python 3.12 or newer and the LLVM 22 toolchain: llvmlite 0.49 (which bundles LLVM 22), clang 22, and the LLVM tools (`opt`, `llvm-dis`, `llvm-link`, `llvm-config`). In this workspace, run compiler, package and validation commands in the `yian-env` conda environment. Invoke Python reference implementations as modules; installed `yianc` and `anx` are native binaries:

```bash
conda run --no-capture-output -n yian-env python -m compiler.main --help
conda run --no-capture-output -n yian-env python -m anx.main --help
conda run --no-capture-output -n yian-env bash scripts/install.sh --prefix "$HOME/.local" --python python
"$HOME/.local/bin/yianc" --help
"$HOME/.local/bin/anx" --help
conda run --no-capture-output -n yian-env bash scripts/setup_llvm_toolchain.sh --check
```

`scripts/setup_llvm_toolchain.sh` installs llvmlite into the selected environment. `--with-clang-22` also installs system clang/LLVM packages and switches unversioned commands to 22; `--with-conda-clang` installs the tools into the conda environment. `--check` only reports. Native linking and package C-shim compilation select `YIAN_CC`, then `clang`, then `cc`. `runtime/build.py` uses `clang` and `llvm-ar` on PATH independently of `YIAN_CC`. LLVM consumer packages also validate their native toolchain requirements.

`pyproject.toml` defines the Python reference compiler and package-manager packages plus their strict pyright scope. The language server is the native YIAN package in `lsp/`, built and installed separately. `requirements.txt` also contains benchmark and development dependencies, so it is not the minimal compiler dependency list.

```bash
conda run --no-capture-output -n yian-env bash scripts/install.sh --prefix "$HOME/.local" --python python
PATH="$HOME/.local/bin:$PATH" scripts/install_lsp.sh --prefix "$HOME/.local"
```

`scripts/install.sh` builds `bootstrap/compiler` with the selected Python reference compiler, then builds `bootstrap/anx` with the resulting native compiler. It installs `yianc`, `anx`, the standard library and runtime resources under the selected prefix. The installed commands do not need a Python environment or the source checkout at run time. The build step requires Python 3.12+, llvmlite 0.49+, LLVM 22 and a native C toolchain. `scripts/uninstall.sh --prefix PATH` removes the managed native installation; `--python PATH` also removes recognized legacy Python launchers.

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
- Every `yianc` change must update both `compiler/` and `bootstrap/compiler/` in the same implementation iteration. Every `anx` change must update both `anx/` and `bootstrap/anx/`. Keep supported features, user-visible behavior and package contracts aligned; implementation-specific internals may differ.
- Every compiler or package-manager test change must exercise both implementations against the same relevant cases. The default `scripts/run_tests.py` mode is `--implementation both`; the `python` or `native` selections are for focused diagnosis and do not replace the paired acceptance run.
- After each implementation iteration that changes `yianc` or `anx`, refresh the installed native binaries with `scripts/install.sh` before validating the native implementation. The default paired test runner performs this refresh automatically; direct native tests must invoke the installer explicitly. Use the same Python environment and install prefix for the refresh and the test run.
- Commit only when explicitly requested. Review tracked and untracked changes, run `git diff --check` and `git diff --cached --check`, stage only the requested scope, and report the commit plus remaining worktree changes. Do not push without authorization.
- Distinguish executed checks from static review and uncovered behavior. A generic declaration that builds without being instantiated has not been verified at run time; Python-suite success does not establish bootstrap semantic correctness.

## Python compiler CLI

For compilation and semantic analysis, always pass the standard library source root (`lib/src`) as a positional input, because the standard library is not implicit. Input paths may be files or directories; directories are searched recursively for `.an` files. Input-root order is preserved, with sorted files inside each directory. Formatting and frontend-only observation can use just the sources being inspected.

```bash
conda run --no-capture-output -n yian-env python -m compiler.main lib/src tests/basic/array/access.an
conda run --no-capture-output -n yian-env python -m compiler.main --analyze --json lib/src tests/basic/array/access.an
conda run --no-capture-output -n yian-env python -m compiler.main -t none lib/src tests/basic/array/access.an
conda run --no-capture-output -n yian-env python -m compiler.main -t ll lib/src tests/basic/array/access.an
conda run --no-capture-output -n yian-env python -m compiler.main -o build/func -O2 lib/src tests/basic/array/access.an
conda run --no-capture-output -n yian-env python -m compiler.main --dump --log-spec "type_check=DEBUG" lib/src tests/basic/array/access.an
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

`compiler/analysis/session.py` serves Python compiler builds and analysis-only runs. The native language server consumes the corresponding editor-analysis interface in `bootstrap/compiler/`. Both compiler implementations accept in-memory document overlays and produce diagnostics and semantic query data; native compilation uses strict failure and distinguishes all checked definitions from definitions selected for code generation.

## Native compiler

`bootstrap/compiler/` is a hybrid package with a binary and importable modules. It implements source preparation, semantic analysis, HIR-to-CFG lowering, LLVM emission and native linking. Its dependency on `bootstrap/json/` supplies JSON parsing and writing; `bootstrap/llvm/` supplies the LLVM C API bindings. `scripts/install.sh` builds and installs the native `yianc` command and its runtime resources.

```bash
conda run --no-capture-output -n yian-env bash scripts/install.sh --prefix "$HOME/.local" --python python
"$HOME/.local/bin/anx" build -O2 bootstrap/compiler
"$HOME/.local/bin/yianc" --frontend-json desugared bootstrap/compiler/src/main.an
"$HOME/.local/bin/yianc" --analyze --json --compiler-root . lib/src tests/basic/array/access.an
"$HOME/.local/bin/yianc" --compiler-root . -O2 -o build/access lib/src tests/basic/array/access.an
```

The native CLI supports `--frontend-json {tokens,ast,desugared}`, `--analyze`, `--format`, and compilation targets `none`, `exe`, `ll`, `bc`, `obj`, and `asm`. It also supports package maps, source-root configuration, raw-pointer mode, FFI permission, logging, profiling, stage dumps and native link options. The analysis JSON uses `format`, `ok`, `stage`, `summary`, and `diagnostics`. Successful analysis does not require `main`; a present entry is validated. Compilation targets that produce native artifacts require an entry.

The native compiler locates runtime resources through `--compiler-root`, nonempty `YIAN_ROOT`, or resources associated with the installed executable. `YIAN_LIB` or `YIAN_ROOT` also selects the standard-library trust root. Standalone compilation still requires `lib/src` as a positional input; package mode receives standard-library sources through its package map. Pass `--compiler-root` for deterministic checkout-local builds. `-t none` performs semantic analysis and CFG lowering; `--analyze` stops after semantic analysis and HIR checks. Both pointer modes are target options to the compiler. The mode used to build the compiler binary is a separate configuration.

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
- Ordinary `sizeof` retains the queried `TypeId` until backend lowering. Immediate compile-time evaluation queries the selected target layout only when execution reaches the size operation. The Python and native compiler backends provide target-aware size queries; a short-circuited operation does not query a size, and an unselected compile-time branch is not type-checked.

## Tests and checks

The `basic`, `safety`, `package`, and `bootstrap` suites share one runner. The default implementation mode runs Python modules and native installed binaries against the same selected cases. Before native execution, the runner calls `scripts/install.sh` with its active Python interpreter and install prefix:

```bash
conda run --no-capture-output -n yian-env python scripts/run_tests.py
conda run --no-capture-output -n yian-env python scripts/run_tests.py --suite safety
conda run --no-capture-output -n yian-env python scripts/run_tests.py --all -q
conda run --no-capture-output -n yian-env python scripts/run_tests.py --suite bootstrap
conda run --no-capture-output -n yian-env python scripts/run_tests.py -f call -v
conda run --no-capture-output -n yian-env python scripts/run_tests.py -x
conda run --no-capture-output -n yian-env python scripts/run_tests.py --no-run
conda run --no-capture-output -n yian-env python scripts/run_tests.py --implementation both --prefix "$HOME/.local"
```

`basic` is the default suite. `-q` prints summaries, `-v` includes failure details, `-f` filters names, and `-x` includes each suite's experimental sources. `--implementation both` is the default and refreshes native binaries before testing; `python` and `native` are diagnostic selections. `--prefix` or `YIAN_INSTALL_PREFIX` selects the install location. `--no-run` uses `-t none` for standalone sources and `anx check` for package fixtures; the bootstrap suite checks packages instead of running their package tests.

`basic` compiles standalone sources in fat and raw pointer modes; `safety` runs the fat-pointer safety regressions; `package` drives `package.anx` fixtures through their selected `anx` command in both pointer modes; `bootstrap` runs package-local tests for `bootstrap/compiler`, `bootstrap/json`, and `bootstrap/toml` in both modes. Each applicable suite runs once with Python `compiler.main`/`anx.main` and once with installed native `yianc`/`anx`. `tests/input/package/<fixture>.command` can select `build`, `check`, `test`, or `graph` with extra arguments such as `graph --json`. `scripts/run_safety_tests.py` is a wrapper for the safety suite and inherits the paired implementation mode. The LSP protocol tests and measurements start the installed native `yian-lsp`; refresh it separately with `scripts/install_lsp.sh`.

Sources are under `tests/<suite>/`; expected results are under `tests/output/<suite>/`. A `.err.an` source must fail compilation, and its `.ans` file contains the required diagnostic substring. For a normal test, `.ans` contains expected stdout; a first line of `Exit code <N>` specifies an expected nonzero exit code. A directory test — a multi-file standalone test or a package fixture — uses `<dir>.ans`, or `<dir>.err.ans` for an expected failure. `tests/input/<suite>/<name>.args` and `.stdin` provide process arguments and standard input.

An existing `tests/input/<suite>/<test>.compile.json` file attaches compiler variants to that test. Each variant supplies a name, a string-array `compiler_args`, and at most one compile-error substring or expected runtime exit code. The runner expands these variants without duplicating the source test.

Test data lives under `tests/`; the runner and other Python tooling live under `scripts/`. Bootstrap package tests live under each package's `tests/` directory and run through both Python and native `anx test` commands. `scripts/check_compiler_parity.py` compares the Python compiler module with a specified native compiler; `scripts/check_anx_parity.py` compares Python `anx.main` with a specified native `anx` while using the same native compiler for both.

### Select checks by the affected layer

```bash
# Python syntax and strict type checking.
conda run --no-capture-output -n yian-env python -m compileall -q compiler anx scripts
conda run --no-capture-output -n yian-env pyright

# Paired Python/native basic, safety, package and bootstrap package tests.
conda run --no-capture-output -n yian-env python scripts/run_tests.py --all -q

# Formatter fixtures, invariants and canonical-source check.
conda run --no-capture-output -n yian-env python -m compiler.format --cases
conda run --no-capture-output -n yian-env python -m compiler.format --verify bootstrap/compiler/src
conda run --no-capture-output -n yian-env python -m compiler.format --check bootstrap/compiler/src

# Runtime ABI consistency and existing normal/sanitized self-tests.
conda run --no-capture-output -n yian-env python runtime/build.py --check
conda run --no-capture-output -n yian-env python runtime/build.py --asan

git diff --check
```

For compiler or package-manager changes, use the paired runner so the same relevant cases reach both implementations. It refreshes the installed native binaries before native execution. Use the `python` or `native` implementation selection only to isolate a failure; acceptance still requires the paired run. The parity scripts supplement suite results by comparing CLI and compiler observations. Runtime safety-failure cases can be valid semantic inputs: do not translate an expected runtime exit code into an expected analysis rejection. Do not use `--bless` or alter expected results merely to make checks pass.

## anx package manager

`anx/` and `bootstrap/anx/` implement `new [--kind bin|lib|hybrid]` (`--lib` is shorthand for `--kind lib`), `build`, `run`, `check`, `test`, `fmt [--check]`, and `graph`. Projects declare metadata plus `[dependencies]` and `[dev-dependencies]` (visible only to `anx test`, and not compiled by a normal build) in `package.anx`; imports use package names. Both implementations produce the v2 package-map contract consumed by the compiler. Python `anx.main` calls the Python compiler in-process unless `YIANC` selects an executable. Native `anx` selects the executable named by nonempty `YIANC`, then `yianc` on `PATH`; it has no Python fallback. `anx test` runs the project's tests under `<project>/tests/`.

`bin` supplies an executable entry, `lib` exposes importable sources without an entry, and `hybrid` supplies both. `anx build`/`run` do not emit a library artifact for `lib`; use `check`/`test` for those packages. Executables are written to the root project's `build/app`. `--release` selects `-O3` unless an explicit `-O` is supplied. `anx run [project] -- ...` forwards arguments to the program. Keep Python and native command behavior aligned and run package fixtures through both implementations after changes.

`anx/manifest.py` parses TOML and native declarations; `anx/project.py` validates package layout, dependencies, ownership and file indexing; `anx/project_tests.py` drives Python package-local tests. `bootstrap/anx/src/` provides the corresponding native modules. Native LLVM shim and link requirements come from `[native.llvm]` and are passed as package-map data to the compiler.

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
- `bootstrap/compiler/` contains the YIAN frontend, middle end, CFG/LLVM backend and native driver; `bootstrap/json/` provides general JSON parsing/writing and its own package tests.
- `bootstrap/anx/` is the native package manager, with manifest loading, project graph and paths, build/test/format drivers, and diagnostics. It uses the standard-library process API to invoke the selected compiler and native tools.
- `bootstrap/ffi/` and `bootstrap/llvm/` are FFI consumer packages; the LLVM package uses LLVM 22 C API bindings and declares its C shim and link components through `[native.llvm]`.
- `runtime/src/{alloc,runtime,io}.c` provide allocation, runtime failures and synchronous Linux I/O. `runtime/include/` declares the ABI; `compiler/runtime_lib.py` builds runtime objects/archives for Python-generated programs.
- `lsp/` is the native YIAN LSP package. It owns stdio JSON-RPC, document/workspace coordination, diagnostics, semantic tokens, formatting, completion, navigation and refactoring. It depends on `bootstrap/compiler/`, `bootstrap/anx/`, the JSON/RPC packages and shared standard-library resources. stdout belongs to the protocol; logs go to stderr or the configured file.
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

- Pyright strict checking covers the Python `compiler/` and `anx/` packages; avoid `Any`, `object`, and `hasattr()`. Keep imports at the top of new files and preserve the existing handler-map dispatch style in the type checker and LLVM translator.
- Python private names use a **double** leading underscore: module-level functions and constants (`__helper`, `__CONSTANT`), methods (`self.__drain()`), nested helpers, and instance attributes. Use a **single** underscore only where the double form breaks the reference at run time: a name written as `__name` inside a class body is rewritten to `_ClassName__name`, so a *module-level* private that a class body mentions (for example `_SEP` in `compiler/utils/log.py`, `_is_within` in `compiler/analysis/source_provenance.py`) must keep one underscore. A private defined *and* used inside the same class body is `__name` like any other. Audit with `rg -n --glob '*.py' 'def _[^_]|^_[A-Za-z]|(self|cls)\._[^_]' compiler anx scripts`; review matches against the name-mangling exception.
- Organize modules by responsibility and dependency direction. Split long functions at meaningful interfaces, use guard clauses where they reduce nesting, and separate small logical blocks with blank lines. Comments explain ownership, prerequisites, non-obvious choices and algorithmic boundaries rather than restating individual statements.
- Keep user-program errors distinct from internal invariant failures. Preserve source identity and associated locations through errors; detach each location to owned path data before destroying a session whose diagnostics must outlive it. Never infer analysis error categories from message text outside the frontend compatibility boundary.
- `bak/` is ignored legacy material and `.conda/` is a local environment; do not modify them without explicit authorization. `build/` directories contain generated compiler/package/test output: regenerate artifacts through the relevant tools, do not hand-edit or commit them as source changes.
- User-facing language documentation is written in Chinese; this agent instruction file uses English. Update documentation only when explicitly requested and verify facts against implementation, manifests and runnable commands. Documentation can be stale and is not evidence of an implemented feature.
- Write all code comments and docstrings in English, including those in tests, benchmarks, and examples. Keep user-visible text and test data in their intended language.
- Documentation states the repository as it is now. Do not narrate the iteration that produced it: no history or chronology (此前/现在/改为/曾经/第一版/本轮/后续/待办), no changelogs, no plans or promises, no comparison with an earlier state, and no defect-story framing. Write the current fact in the present tense — what the code does, what the rule is, what a value means — and let it stand on its own; when a rule exists because of a hazard, describe the hazard as a property of the current design instead of recounting the defect that motivated the rule. Keep the voice neutral: 「X 是 Y」, not 「X 改成了 Y」 or 「X 以前是 Z」. This applies to every persistent text in the repository: `docs/`, `README` files, code comments, docstrings, and commit messages.
- Code comments and persistent documentation must be self-contained: never cite a temporary document or its content — an implementation plan (such as those under `docs/plan/`), a proposal, a TODO list, a bug log, or review notes — and never cite rule, section, or definition numbers that exist only there. State the fact itself instead; referring to another persistent document is fine. Existing violations are left in place: fix one when the surrounding code or document is next touched.
- `CLAUDE.md` must remain a relative symbolic link to `AGENTS.md`.
- There is no repository CI or installed project pre-commit hook. YIAN formatting is implemented in `compiler/format/`, without a separate formatter configuration file. Run relevant existing checks locally and inspect whitespace before committing.
