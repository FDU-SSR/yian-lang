# AGENTS.md

## Planning and execution protocols

- Complex development tasks must follow [.agent/PLANS.md](.agent/PLANS.md), the authoritative source for planning, execution, acceptance, recovery, and templates. Its protocol rules take precedence over conflicting repository engineering rules; all other effective rules below remain in force.
- The user chooses the workflow: a planning request follows Planning Protocol and does not authorize business implementation; an execution request follows Execution Protocol and carries the specified plan through final acceptance without repeated permission for in-scope work. These protocols are model-independent and do not select or switch models.
- Persist requirements and important design decisions in `.agent/plans/<task-name>/PLAN.md`; persist actual task status, verification evidence, and recovery instructions only in the sibling `PROGRESS.md`. Never rely on chat history as the sole task source or maintain conflicting completion trackers.
- Do not change confirmed requirements, core design, semantics, public contracts, compatibility constraints, or acceptance standards without the required user decision. Ordinary engineering details within the contract are autonomous.
- `.agent/PLANS.md` and `.agent/plans/` may contain templates, future work, decision history, and checkpoints. They are persistent operational documents and are exempt from the present-state-only documentation rule below. Do not create fictional task plans.

## Strict Local Verification Policy

This is a mandatory execution constraint for every task, including planning experiments, implementation, diagnosis, milestone acceptance, final acceptance and recovery. It takes precedence over conflicting repository rules, existing plans and templates unless the user explicitly authorizes the broader operation. A request to execute a plan, continue work or complete acceptance, and ordinary acceptance text in a PLAN, do not themselves authorize batch testing. Documentation command examples describe capabilities and grant no execution permission.

- By default, run only a minimal probe, one or a few existing tests directly related to the current change, or low-cost local static checks. Before every run, identify the exact property, minimal input, expected output and pass condition; inspect the command's actual selection and indirect setup/build/test effects.
- A minimal probe targets one concrete behavior, uses explicit input and expected results, minimizes dependencies and setup, runs quickly, and distinguishes success from failure. Temporary files or an isolated test directory are allowed. Prefer reusing a relevant probe or fixture; do not build large test infrastructure for a small change.
- Existing local tests must select an exact file, function or case and only a small directly relevant set. A module/package selector or name filter is insufficient if it still runs many cases. Use a minimal probe when the runner hides expensive batch builds or tests. For cross-module changes, use the smallest integration probe covering the necessary interaction.
- Without explicit user authorization, do not run project-wide suites, all tests in a module/package/crate/workspace, full regressions, batch integration/end-to-end tests, large unrelated selections, or long project-wide builds/checks/tests. This includes unfiltered `pytest`, `cargo test`, `ctest`, `cargo test --workspace`, `go test ./...`, full-suite `make test`/`npm test`/`pnpm test`, and equivalent scripts, Makefiles, CI commands or other wrappers. Renaming or wrapping a command does not change this rule.
- Neither task/milestone completion, final acceptance, multiple affected modules, passing local tests, nor a desire for more confidence authorizes wider testing. Plans must separate default local verification from extended verification requiring explicit user authorization; a recommendation is not authorization. Record the user's specific authorization and scope before executing an extended operation, and stay within it.
- Use the loop: implement → construct a minimal probe or select a local test → verify → analyze → fix if needed → update progress. On failure, inspect output/code/logs, reduce the reproducer if needed, fix and rerun the corresponding local check. Do not broaden to batch tests for diagnosis.
- Keep probes proportional to the change's actual risk. Avoid redundant cases, repeated verification of unaffected passing behavior, coverage-driven probe proliferation, and combining many independent probes into a disguised batch process.
- Task, milestone and final acceptance default to requirement/design/invariant review, reuse of still-valid local evidence, and minimal probes for essential gaps. Final acceptance does not require a fresh test run when existing evidence remains valid. Rerun only checks affected by fixes, changed requirements or invalid evidence.
- If broader verification is indispensable, explain the concrete need and request authorization once. Without authorization, record it as unexecuted extended verification and disclose the remaining risk; continue independent work. Unrun batch tests do not fail default local acceptance unless the user explicitly made them required.
- Record implementation and verification separately: `IMPLEMENTED` means implementation complete; `LOCALLY_VERIFIED` means the defined local checks/probes passed; `FULLY_VERIFIED` means every explicitly user-required acceptance item passed within its stated scope; `UNVERIFIED` means necessary verification is missing or invalid; `BLOCKED` means work cannot proceed, with a cause and release condition. `FULLY_VERIFIED` does not imply exhaustive coverage or a full regression unless that exact scope was required and executed.
- Final reports state actual implementation, local checks/probes and results, batch tests not run, and uncovered risks. Local acceptance can support delivery; never describe it as full-suite/full-regression success or proof that all potential problems are excluded.

YIAN is a statically typed programming language with a Python recovery compiler, a compiler and package manager written in YIAN, a standard library, a C runtime, and editor tooling. The self-hosted compiler and package manager are in `bootstrap/yianc/` and `bootstrap/anx/`. The native compiler is the daily authority; the Python compiler is retained for the explicit cold recovery path and the compatibility it needs. The native source-to-executable pipeline is:

`.an source → tokens → AST → desugared AST → HIR → CFG IR → LLVM IR → native executable`

The Python recovery compiler is maintained for the explicit cold bootstrap inputs; its continued presence does not promise feature parity with the native compiler.

| Component | Entry point | Responsibility |
| --- | --- | --- |
| Python recovery compiler | `PYTHONPATH=python python -m yianc.main`, `python/yianc/main.py` | Builds the raw native `yianc` during explicit cold recovery |
| Native compiler | installed `yianc`, `bootstrap/yianc/src/main.an` | Source analysis, CFG/LLVM lowering, native emission |
| Native package manager | installed `anx`, `bootstrap/anx/src/main.an` | Manifest validation, dependency graph, builds and package tests |
| Language server | `yian-lsp`, `lsp/src/main.an` | Native stdio LSP backed by the self-hosted compiler and package manager |
| Standard library | `lib/src/` | Language-level containers, numeric operations, I/O and CLI APIs |
| Runtime | `runtime/src/` | Allocation, runtime failures and Linux I/O support |

## Environment and installation

The supported development environment is Linux with Python 3.12 or newer and the LLVM 22 toolchain: llvmlite 0.49 (which bundles LLVM 22), clang 22, and the LLVM tools (`opt`, `llvm-dis`, `llvm-link`, `llvm-config`). In this workspace, run compiler, package and validation commands in the `yian-env` conda environment. Daily compilation, package operations, tests and benchmarks use installed native binaries; Python runs their orchestration scripts. Python and llvmlite are required by cold recovery. Install Python dependencies from `python/requirements.txt`; `scripts/setup_llvm_toolchain.sh` installs missing system LLVM and native C build packages and does not install Python packages:

```bash
conda create -n yian-env python=3.12 -y
conda activate yian-env
python -m pip install -r python/requirements.txt
bash scripts/setup_llvm_toolchain.sh
export YIAN_CC=/usr/lib/llvm-22/bin/clang
conda run --no-capture-output -n yian-env bash scripts/bootstrap_toolchain.sh --prefix "$HOME/.local" --python python
"$HOME/.local/bin/yianc" --help
"$HOME/.local/bin/anx" --help
conda run --no-capture-output -n yian-env bash scripts/setup_llvm_toolchain.sh --check
```

`python/requirements.txt` pins llvmlite for the Python recovery compiler. `scripts/setup_llvm_toolchain.sh` does not depend on Python; by default it installs missing native C build packages from the distribution repository and LLVM 22 packages from the official `apt.llvm.org` repository using sudo, then switches unversioned commands to LLVM 22. It does not install Python packages; `--check` only reports LLVM tool versions. Native linking and package C-shim compilation select `YIAN_CC`, then `clang`, then `cc`. `runtime/build.py` uses `clang` and `llvm-ar` on PATH independently of `YIAN_CC`. LLVM consumer packages also validate their native toolchain requirements.

`python/pyproject.toml` defines the Python recovery compiler package and its strict Pyright scope. The native package manager is in `bootstrap/anx/`. The language server is the native YIAN package in `lsp/`; only the warm refresh builds and installs it, together with both compiler tools. `python/requirements.txt` contains the pinned llvmlite dependency needed for cold recovery; the Python compiler package also declares llvmlite in `pyproject.toml`. Direct Python compiler module invocations from a checkout need `PYTHONPATH=python`; these are reserved for explicit recovery work.

```bash
conda run --no-capture-output -n yian-env bash scripts/bootstrap_toolchain.sh --prefix "$HOME/.local" --python python
scripts/refresh_toolchain.sh --anx "$HOME/.local/bin/anx" --yianc "$HOME/.local/bin/yianc" --prefix "$HOME/.local"
"$HOME/.local/bin/yian-lsp" --version
```

`scripts/bootstrap_toolchain.sh` uses the Python recovery compiler and a fixed five-package recipe to build raw `yianc`; staged native `yianc` then compiles raw `anx` from a fixed seven-package recipe. It publishes the two commands, standard library and runtime resources only after both builds and help checks succeed. The install record includes source, builder, pointer-mode and artifact identities. Installed commands do not need Python or the source checkout at run time. The build requires Python 3.12+, llvmlite 0.49+ with LLVM 22, `llvm-config`, `clang`, `llvm-ar`, and a native C toolchain. `scripts/uninstall.sh --prefix PATH` removes the managed native installation; `--python PATH` also removes recognized legacy Python launchers.

`scripts/refresh_toolchain.sh` freezes existing native `anx` and `yianc` seeds, rebuilds all three tools as raw/O2, checks the LSP handshake against staged resources, then publishes commands and resources as one recoverable transaction. It does not invoke Python or fall back to the cold workflow. The cold bootstrap does not build `yian-lsp`; run the warm refresh when the language server is needed. Build records identify each component's sources, actual builders and builder inputs.

## Working rules

- For every task, inspect all Git-tracked project documentation and keep it synchronized with the current project state in real time. Update stale or contradictory tracked documents as the implementation or understanding changes, including documents outside the files directly changed by the implementation. Do not wait for a separate documentation request.
- If required tools, dependencies, or other environment setup are missing, it is acceptable to stop and ask the user to install or configure them. Resume after the user confirms the environment is ready; a workaround is not always required.
- Preserve unrelated local changes. Inspect the worktree before editing or committing.
- Complete the requested implementation and required local acceptance within the authorized scope under the Strict Local Verification Policy. Do not silently report success for unsupported semantics or replace a required operation with a placeholder.
- Use `apply_patch` for local file edits when practical.
- Tests, assertions, expected outputs and test tooling may be added or updated as needed for the requested work.
- Prefer adding test items or assertions to an existing relevant test file or fixture. Reuse its setup and compilation unit where practical; separate test files increase compilation overhead and total suite time.
- Create a new test file when extending an existing test is unsuitable, such as incompatible success/failure expectations, distinct diagnostic cases, or independent fixture/setup requirements. Do not combine cases in ways that mask failures or weaken coverage.
- Test additions and modifications may be committed together with production code, documentation and other scoped changes; test-only commits are not required.
- Define implementation units as independently useful, end-to-end features or fixes, not by files, APIs, tool calls, or compilation steps. Keep each unit's required layers, callers, ownership and error paths together. Native `yianc` is the daily compiler authority; update Python `yianc` only when a change is needed for explicit cold recovery compatibility. Validate package-map output against the consumers required by that workflow. When one task includes many independent features, split them into scoped commits as useful instead of forcing them all into one commit. Do not create scaffold-only, plumbing-only, or progress-checkpoint commits for unfinished features.
- Keep an authorized feature in progress across turns until its defined boundary is complete. A user saying “continue” means resume that feature, not start a new checkpoint. Commits are review units: do not commit a partially wired or knowingly unusable feature just because a focused check passes, and do not stop after a small patch to ask whether to continue while the authorized feature remains unfinished. A task may contain multiple commits for independently reviewable feature units.
- For a multi-part approved plan, do not stop when one low-level slice passes if dependent work is still required for the planned behavior. Continue through the connected implementation units until the plan's user-visible completion criteria are met; report intermediate findings as progress, not as a request to restart or approve each next slice.
- Record implementation completion separately from local acceptance. Check task acceptance before closing a task, milestone acceptance before dependent milestones, and final acceptance before the final commit or report, using valid local evidence and minimal probes only by default. Acceptance gates never authorize batch tests or repeated builds. Disclose required gaps and unexecuted extended verification separately.
- If a test exposes a bug, preserve its intended assertion, investigate and fix it within the authorized design, and revalidate. If blocked by a core design conflict or missing environment, record and report the cause; never weaken the assertion to make it pass.
- Native compiler verification targets native `yianc` with minimal relevant cases. Check Python recovery behavior only when the changed source or package contract participates in cold bootstrap; that check supports recovery compatibility only. Package-manager changes target `bootstrap/anx/` and verify the native package-map consumer; include the Python recovery compiler only when the cold recipe consumes the changed contract. Record other applicable consumers as unverified if they cannot be checked within the authorized scope.
- Native evidence must use binaries matching the relevant source changes, environment and install prefix. Reuse a verified current binary; do not reinstall after an iteration that leaves it current. If it is stale, `scripts/refresh_toolchain.sh` is the full native refresh path; a cold bootstrap is a separate prerequisite when no compatible native seed exists. An install or refresh command is not implicit permission for an expensive build. Apply the same rule to LSP-specific checks.
- Commit only when explicitly requested. A task may be delivered in multiple scoped commits when it contains many independent features. Review tracked and untracked changes, run `git diff --check` and `git diff --cached --check`, stage only the requested scope, and report all commits plus remaining worktree changes. Do not push without authorization.
- Distinguish executed checks from static review and uncovered behavior. A generic declaration that builds without being instantiated has not been verified at run time; Python-suite success does not establish bootstrap semantic correctness.

## Python recovery compiler CLI

The commands below are for maintaining or diagnosing the explicitly invoked cold recovery compiler. Daily compilation, semantic analysis and formatting use native `yianc`; a Python CLI example does not define the daily tool workflow.

For compilation and semantic analysis, always pass the standard library source root (`lib/src`) as a positional input, because the standard library is not implicit. Input paths may be files or directories; directories are searched recursively for `.an` files. Input-root order is preserved, with sorted files inside each directory. Formatting and frontend-only observation can use just the sources being inspected.

```bash
conda run --no-capture-output -n yian-env env PYTHONPATH=python python -m yianc.main lib/src tests/basic/array/access.an
conda run --no-capture-output -n yian-env env PYTHONPATH=python python -m yianc.main --analyze --json lib/src tests/basic/array/access.an
conda run --no-capture-output -n yian-env env PYTHONPATH=python python -m yianc.main -t none lib/src tests/basic/array/access.an
conda run --no-capture-output -n yian-env env PYTHONPATH=python python -m yianc.main -t ll lib/src tests/basic/array/access.an
conda run --no-capture-output -n yian-env env PYTHONPATH=python python -m yianc.main -o build/func -O2 lib/src tests/basic/array/access.an
conda run --no-capture-output -n yian-env env PYTHONPATH=python python -m yianc.main --dump --log-spec "type_check=DEBUG" lib/src tests/basic/array/access.an
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

`--dump` on the compilation path writes `tokens.txt`, `ast.txt`, `hir.txt`, and `cfg.txt` under `build/` as their stages complete; code generation also writes `ir.ll`. `ir.ll` is a dump, separate from an explicitly selected `-t ll` output. `--token`, `--ast`, `--hir`, `--cfg`, and `--emit-llvm` are not supported options. Use one action mode per invocation; in the recovery Python CLI, frontend observation takes precedence over analysis and formatting flags.

Executables default to `build/a.out`; LLVM bitcode and object outputs default to `build/<stem>.<ext>`, while assembly defaults to `<stem>.s` in the current directory. Pass `-o` whenever the output name matters.

`-t exe` links the runtime archive. `-t obj` uses a relocatable link to merge the user object, runtime object and package C shims. External system and LLVM library requirements still apply at final linking. `-t ll`, `-t bc` and `-t asm` leave runtime symbols external. Runtime artifacts are built on demand under `build/runtime/`. `runtime/build.py --check-consistency-only` checks native ABI declarations against the C header without building or self-testing. `--check` also builds and runs the normal self-test; `--asan` runs the ASan/UBSan self-test.

For standalone Python analysis, standard-library configuration uses `--compiler-root` (a checkout root), then `YIAN_LIB` (the `lib/src` directory itself), then `YIAN_ROOT` (a checkout root), then the checkout containing the imported compiler. Python package-mode analysis uses the package map's `std.sourceRoot`. Native `anx` resolves the standard-library root from `YIAN_LIB`, `YIAN_ROOT`, or its associated installation resources and writes that root into the package map; its CLI has no `--compiler-root` option. Root configuration classifies trusted sources and does not implicitly add source inputs to `yianc`.

`--packages pkg.json` enables package-name import resolution using the v2 contract: a top-level `format: 2` and `root`, plus a `packages` table whose entries carry `sourceRoot`, `kind`, `entry`, `dependencies`, `ffi` and optional `nativeLLVM`. It is normally generated by `anx build`.

Package ownership uses canonical source roots and path-component boundaries. Standalone trust comes from configured canonical roots, never from an input directory being named `lib`, `std`, or `tests`. Restricted-operation permission and FFI permission are distinct checks; raw-pointer mode does not grant either permission.

## Python recovery analysis and code generation

`python/yianc/main.py` is the recovery compiler entry and uses `AnalysisSession` for source-to-HIR analysis, then lowers and emits selected definitions. These implementation details do not make Python a daily compiler or require full feature parity:

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

Ordinary `sizeof` expressions retain the queried type identity through HIR and CFG; LLVM lowering supplies the size constant. Immediate compile-time size queries use `python/yianc/target_layout.py` to provide the emitted target's layout. Do not substitute the Python host's object sizes or infer aggregate layout in semantic passes.

`python/yianc/analysis/session.py` serves recovery compiler builds and analysis-only runs. The native language server consumes its native editor-analysis interface in `bootstrap/yianc/`. The recovery compiler retains in-memory document overlays and semantic query APIs in its own implementation; daily editor behavior is provided by native `yianc` and `yian-lsp`, and Python feature parity is not required. Native compilation uses strict failure and distinguishes all checked definitions from definitions selected for code generation.

## Native compiler

`bootstrap/yianc/` is a hybrid package with a binary and importable modules. It implements source preparation, semantic analysis, HIR-to-CFG lowering, LLVM emission and native linking. Its dependency on `bootstrap/json/` supplies JSON parsing and writing; `bootstrap/llvm/` supplies the LLVM C API bindings. `scripts/bootstrap_toolchain.sh` cold-builds and installs native `yianc` and `anx`; `scripts/refresh_toolchain.sh` refreshes both plus `yian-lsp`.

```bash
conda run --no-capture-output -n yian-env bash scripts/bootstrap_toolchain.sh --prefix "$HOME/.local" --python python
"$HOME/.local/bin/anx" build -O2 bootstrap/yianc
"$HOME/.local/bin/yianc" --frontend-json desugared bootstrap/yianc/src/main.an
"$HOME/.local/bin/yianc" --analyze --json --compiler-root . lib/src tests/basic/array/access.an
"$HOME/.local/bin/yianc" --compiler-root . -O2 -o build/access lib/src tests/basic/array/access.an
```

The native CLI supports `--frontend-json {tokens,ast,desugared}`, `--analyze`, `--format`, and compilation targets `none`, `exe`, `ll`, `bc`, `obj`, and `asm`. It also supports package maps, source-root configuration, raw-pointer mode, FFI permission, logging, profiling, stage dumps and native link options. The analysis JSON uses `format`, `ok`, `stage`, `summary`, and `diagnostics`. Successful analysis does not require `main`; a present entry is validated. Compilation targets that produce native artifacts require an entry.

The native compiler locates runtime resources through `--compiler-root`, nonempty `YIAN_ROOT`, or resources associated with the installed executable. `YIAN_LIB` or `YIAN_ROOT` also selects the standard-library trust root. Standalone compilation still requires `lib/src` as a positional input; package mode receives standard-library sources through its package map. Pass `--compiler-root` for deterministic checkout-local builds. `-t none` performs semantic analysis and CFG lowering; `--analyze` stops after semantic analysis and HIR checks. Both pointer modes are target options to the compiler. The mode used to build the compiler binary is a separate configuration.

### Pipeline and module boundaries

`AnalysisSession.prepare(...)` collects and loads sources, lexes, parses and desugars them, classifies permissions, and retains the prepared units. `AnalysisSession.analyze(options)` checks restricted operations, registers primitive types and declaration identities, resolves explicit imports and injects prelude bindings, resolves definition signatures and impls, registers automatic implementations, finalizes and validates types, checks and lowers procedures, verifies HIR, checks pattern coverage, and checks definite assignment.

The middle end supports aggregate and pointer operations, generic types and procedures, trait/method lookup, closures, trait objects, FFI, patterns and compile-time branch selection. It checks root procedures and discovered concrete instances rather than limiting checks to calls reachable from `main`. An unused generic declaration is not evidence that every possible instantiation is valid.

| Directory under `bootstrap/yianc/src/` | Responsibility |
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
- Ordinary `sizeof` retains the queried `TypeId` until backend lowering. Immediate compile-time evaluation queries the selected target layout only when execution reaches the size operation. The native backend is authoritative; the recovery Python backend retains target-aware queries needed by cold bootstrap. A short-circuited operation does not query a size, and an unselected compile-time branch is not type-checked.

## Tests and checks

The `basic`, `safety`, `package`, and `bootstrap` suites share one runner and use only the installed native compiler. The legacy `--implementation native` value remains accepted; `python` and `both` fail before tool selection and point to the explicit cold-recovery entrypoint. Package and bootstrap suites use installed native `anx`, configured with the canonical native `yianc` path. The runner never installs or refreshes tools: it checks selected tools are executable ELF files before starting a suite. Missing tools require explicit setup with `scripts/bootstrap_toolchain.sh`, or `scripts/refresh_toolchain.sh` when a compatible native seed pair already exists. The following are batch/expensive command references requiring explicit user authorization; `-f call` can still select many cases, and `--no-run` still performs compilation/checks:

```bash
conda run --no-capture-output -n yian-env python scripts/run_tests.py
conda run --no-capture-output -n yian-env python scripts/run_tests.py --suite safety
conda run --no-capture-output -n yian-env python scripts/run_tests.py --all -q
conda run --no-capture-output -n yian-env python scripts/run_tests.py --suite bootstrap
conda run --no-capture-output -n yian-env python scripts/run_tests.py -f call -v
conda run --no-capture-output -n yian-env python scripts/run_tests.py -x
conda run --no-capture-output -n yian-env python scripts/run_tests.py --no-run
conda run --no-capture-output -n yian-env python scripts/run_tests.py --implementation native --prefix "$HOME/.local"
```

`basic` is the default suite. `--pointer-mode both|fat|raw` selects target modes and defaults to both; safety raw cases are N/A with a reason and do not count as passes. An all-N/A selection exits nonzero. `-q` prints the summary, `-v` includes failure details, `-f` filters names, and `-x` includes each suite's experimental sources. Confirm the exact selected cases and nested package-test effects before any filtered run; a filter alone grants no permission. Native is the only supported implementation; `python` and `both` are rejected with exit status 2. No selection refreshes tools. `--prefix` or `YIAN_INSTALL_PREFIX` selects the native toolchain prefix. `--no-run` uses `-t none` for standalone sources and `anx check` for package fixtures; the bootstrap suite checks packages instead of running their package tests.

The `basic`, `package`, and `bootstrap` suites run the selected fat/raw modes; safety checks execute in fat mode and show raw as N/A because the suite tests fat-pointer runtime checks. Package fixture `anx test` commands receive one explicit mode from the outer runner, while `graph` runs once. Direct `anx test` defaults to Both and accepts `--pointer-mode {both,fat,raw}`; `--raw-pointers` remains a raw alias and conflicts with an explicit `--pointer-mode`. Each applicable suite uses the installed native `yianc`; package and bootstrap commands use installed native `anx`, configured with the selected compiler. `tests/input/package/<fixture>.command` can select `build`, `check`, `test`, or `graph` with extra arguments such as `graph --json`. `scripts/run_safety_tests.py` is a wrapper for the safety suite and uses native `yianc`. A selected package/bootstrap fixture can invoke package tests; inspect nested execution too. The LSP protocol tests and measurements start the installed native `yian-lsp`; source freshness and authorization rules apply to `scripts/refresh_toolchain.sh`, which rebuilds it with both compiler tools and shared resources.

Sources are under `tests/<suite>/`; expected results are under `tests/output/<suite>/`. A `.err.an` source must fail compilation, and its `.ans` file contains the required diagnostic substring. For a normal test, `.ans` contains expected stdout; a first line of `Exit code <N>` specifies an expected nonzero exit code. A directory test — a multi-file standalone test or a package fixture — uses `<dir>.ans`, or `<dir>.err.ans` for an expected failure. `tests/input/<suite>/<name>.args` and `.stdin` provide process arguments and standard input.

An existing `tests/input/<suite>/<test>.compile.json` file attaches compiler variants to that test. Each variant supplies a name, a string-array `compiler_args`, and at most one compile-error substring or expected runtime exit code. The legacy top-level array remains valid. An object may instead contain `variants`, `pointer_modes` (a non-empty unique list containing `fat` and optionally `raw`), and `raw_inapplicable_reason`; excluding `raw` requires a non-empty reason. Such N/A results do not count as passes, and an all-N/A selection exits nonzero.

Test data lives under `tests/`; Python tooling under `scripts/` selects native executables and checks their results. Bootstrap package tests live under each package's `tests/` directory and run through native `anx test` with native `yianc`. `scripts/check_toolchain_parity.py` runs native-only fixed oracles; its `--full` option delegates the native compiler corpus and test suites and requires explicit authorization. It does not compare against the recovery Python compiler or refresh tools.

### Extended check references — explicit authorization required

These broad checks are not a default acceptance checklist. Prefer a target-file static check, one exact existing case, or a minimal compiler/IR/runtime probe. `git diff --check` is a low-cost local check and may be scoped to changed paths.

```bash
# Python syntax and strict type checking.
conda run --no-capture-output -n yian-env python -m compileall -q python/yianc scripts
conda run --no-capture-output -n yian-env pyright --project python/pyproject.toml

# Native compiler suites; package/bootstrap use installed native anx.
conda run --no-capture-output -n yian-env python scripts/run_tests.py --all -q

# One native formatter fixture, fixed output oracle, fat and raw modes.
conda run --no-capture-output -n yian-env python scripts/_toolchain/compiler_parity.py --native "$HOME/.local/bin/yianc" --groups format --filter fixture-comments/nested_generics.an --report build/formatter-nested-generics.json
conda run --no-capture-output -n yian-env "$HOME/.local/bin/anx" fmt --check bootstrap/yianc/src

# Pure native/C ABI check; it does not build the runtime or run self-tests.
conda run --no-capture-output -n yian-env python runtime/build.py --check-consistency-only

# Runtime build and normal/sanitized self-tests; these are extended checks.
conda run --no-capture-output -n yian-env python runtime/build.py --check
conda run --no-capture-output -n yian-env python runtime/build.py --asan
```

For compiler changes, validate native `yianc` with minimal relevant cases. Check Python recovery compatibility only when a change affects cold-bootstrap inputs. Package-manager changes target native `anx` and validate the v2 contract with native `yianc`; verify recovery compatibility only when the cold recipe consumes a changed contract. A suite run is not a default acceptance requirement. Inspect parity-script case selection and setup costs before use; batch parity runs require explicit authorization. Runtime safety-failure cases can be valid semantic inputs: do not translate an expected runtime exit code into an expected analysis rejection. Do not use `--bless` or alter expected results merely to make checks pass.

## anx package manager

`bootstrap/anx/` implements `new [--kind bin|lib|hybrid]` (`--lib` is shorthand for `--kind lib`), `build`, `run`, `check`, `test`, `fmt [--check]`, and `graph`. Projects declare metadata plus `[dependencies]` and `[dev-dependencies]` (visible only to `anx test`, and not compiled by a normal build) in `package.anx`; imports use package names. Native `anx` produces the v2 package-map contract consumed by native `yianc`; the Python recovery compiler consumes it only for cold-bootstrap compatibility. Its build/check/run/test commands share a strict compiler selector: nonempty `YIANC`, otherwise `yianc` on `PATH`; the selected canonical file must be executable ELF. It never invokes a shell or falls back to Python. `anx test` runs the project's tests under `<project>/tests/` in both fat and raw modes by default, and accepts `--pointer-mode {both,fat,raw}`; `--raw-pointers` remains a raw alias and conflicts with an explicit mode.

`bin` supplies an executable entry, `lib` exposes importable sources without an entry, and `hybrid` supplies both. `anx build`/`run` do not emit a library artifact for `lib`; `check`/`test` are the corresponding package operations, subject to verification scope and authorization. Executables are written to the root project's `build/app`. `--release` selects `-O3` unless an explicit `-O` is supplied. `anx run [project] -- ...` forwards arguments to the program. Verify native package-manager changes with minimal relevant fixtures and the daily native compiler; check recovery compatibility only if the cold recipe consumes the changed contract.

`bootstrap/anx/src/manifest/` parses TOML and package declarations; `project/` validates layout, dependencies, ownership and file indexing; `driver/test.an` runs package-local tests. The v2 package map carries FFI permission and optional LLVM shim/link requirements from `[native.llvm]` to the compiler.

## Architecture

- `python/yianc/frontend/` contains the hand-written lexer and recursive-descent parser. Expression and statement parsing is in `frontend/parse/parser_expr.py`; type syntax is in `parser_type.py`.
- `python/yianc/analysis/passes/` contains desugaring, prelude injection, restricted-operation checking, global resolution, type checking, match coverage, and definite-assignment analysis.
- `python/yianc/analysis/lowering/` lowers expressions, calls, and closure bodies into HIR through `ExprChecker`, `CallDispatcher`, `OpBuilder`, and related helpers.
- `python/yianc/analysis/ty/` contains type representations, unification, generic inference, trait implementation lookup, dereference lookup, and intrinsic type identifiers. `TypeCtx` owns the type space and its semantic queries.
- `python/yianc/analysis/state.py`, `facts/`, `resolution/`, `unit/`, and `symbol/` contain shared semantic state, recorded name facts, type resolution, callable bodies, HIR compilation units, `DefPoint`, and symbol contexts.
- `python/yianc/analysis/documents.py`, `diagnostics.py`, and `queries/` provide document snapshots, structured diagnostics and protocol-neutral completion/navigation/refactoring queries. `python/yianc/interop/positions.py` owns conversion to LSP positions; semantic passes do not depend on the JSON-RPC server.
- `python/yianc/codegen/cfg/` contains CFG IR and its builder/translator; closure environments and calls become ordinary CFG aggregates and functions during CFG lowering.
- `python/yianc/codegen/llvm/` lowers CFG nodes with llvmlite and emits target files through `Emitter`.
- `python/yianc/format/` owns YIAN formatting, fixtures and invariant checks. `python/yianc/cli.py` handles compiler argument scanning; the standard-library `std.cli` provides the argument parser used by the bootstrap CLI.
- `lib/src/core/`, `num/`, `collections/`, and `ffi/` contain the standard-library primitives, containers, numerical support, synchronous I/O/filesystem/path APIs and FFI helpers.
- `bootstrap/yianc/` contains the YIAN frontend, middle end, CFG/LLVM backend and native driver; `bootstrap/json/` provides general JSON parsing/writing and its own package tests.
- `bootstrap/anx/` is the native package manager, with manifest loading, project graph and paths, build/test/format drivers, and diagnostics. It uses the standard-library process API to invoke the selected compiler and native tools.
- `bootstrap/ffi/` and `bootstrap/llvm/` are FFI consumer packages; the LLVM package uses LLVM 22 C API bindings and declares its C shim and link components through `[native.llvm]`.
- `runtime/src/{alloc,runtime,io}.c` provide allocation, runtime failures and synchronous Linux I/O. `runtime/include/` declares the ABI; `python/yianc/runtime_lib.py` is the recovery backend's runtime-object/archive builder.
- `lsp/` is the native YIAN LSP package. It owns stdio JSON-RPC, document/workspace coordination, diagnostics, semantic tokens, formatting, completion, navigation and refactoring. It depends on `bootstrap/yianc/`, `bootstrap/anx/`, the JSON/RPC packages and shared standard-library resources. stdout belongs to the protocol; logs go to stderr or the configured file.
- `ide-support/` contains the VS Code extension. `bench/` contains benchmark sources, specifications, runners and result data. Check algorithmic equivalence before comparing C/raw/fat timings; retain the environment and commit context of recorded results.
- `docs/grammar/`, `docs/manual/`, and `docs/anx/` contain language and tooling documentation; `mkdocs_cn.yml` configures the documentation site. `licenses/` contains license texts.

## Python semantic navigation with Serena

- For Python structure and symbol lookup, prefer Serena's semantic tools such as `get_symbols_overview`, `find_symbol`, `find_referencing_symbols`, `find_implementations`, and `find_declaration` before `rg` or opening files one by one.
- Before changing a function or class, inspect its references and callers through LSP-backed tools. Use semantic rename tools for symbol renames when applicable.
- After Python changes, use target-specific Serena/Pyright diagnostics and minimal relevant checks under the Strict Local Verification Policy; do not automatically run project-wide type checks or tests. Use `rg` and direct source reading when LSP cannot answer the question.

## Language and standard-library resource contracts

The shared language supports postfix reference types such as `T&`. For a concrete non-trait `T`, the common bare representation of non-ZST `T&` has the same data-pointer ABI and size as `T*`; it does not provide borrow checking or lifetime inference. When `T` is a trait, `T&` is a trait object with dynamic method dispatch. Concrete safety metadata and runtime checking mechanisms are implementation-specific and must not be inferred from the common ABI description.

The recovery Python backend defaults to instrumented fat pointers. `--raw-pointers` selects bare pointers without runtime pointer checks. Its safety implementation lives under `python/yianc/codegen/`; daily safety behavior is owned by native `yianc` and the C runtime. Keep the shared runtime ABI consistent without requiring general Python/native feature parity.

Assignment, argument passing, and return use shallow value copies. They do not invalidate the source or perform implicit cleanup. `Move`, `.move()`, the `bitcopy` builtin, `@BitCopy`, compiler-provided `Clone`, and simple-type assignment restrictions are absent. `Clone` and `Drop` remain explicit standard-library traits. Pointers do not implement `Clone`; method lookup auto-dereferences `ptr.clone()` and clones the pointed-to value.

- Owning values must have one explicit resource owner. A shallow copy of a `String`, container, arena or session is an alias of its resources, not an independently releasable copy. Use `defer` for error-path cleanup and make ownership transfer points explicit.
- `Vec`, `HashMap`, `HashSet`, and `Arena` release their storage without recursively dropping contained resource payloads. `Vec`, `HashMap`, and `HashSet` cloning duplicates container storage with shallow element copies; inspect each type's contract before assuming a deep clone. `String` owns its buffer; `str` is a borrowed view.
- `Vec` growth and hash-table structural mutation can invalidate element references or iterators. Use arena-backed records for references that must survive append operations; use `Vec` for ID lists, queues and temporary buffers.
- Hash-table `insert_or_replace` and `remove_entry` return displaced key/value resources. `get_or_insert_with` returns an unused input key on a hit; the caller handles it. Existing `insert`/`remove` do not automatically release discarded resources. Do not structurally mutate a table during iteration.
- `Hash<T>.hash` receives `T&` so nested writes update the caller's accumulator. Equal values must hash equally; floating-point hashing canonicalizes signed zero. Composite keys explicitly implement equality and hashing over the same identity fields.
- `del` releases an allocation, not recursive field payloads. `Drop` is explicit; do not add implicit move, cleanup or deep-copy behavior to analysis or lowering.
- Buffered output requires explicit `flush()` when errors must be observed; `BufWriter.drop()` releases its buffer without flushing. Keep system-call ABI, errno and Linux behavior in the C runtime thin layer, with owned errors and UTF-8-facing APIs in YIAN.

## Conventions and gotchas

- Pyright strict checking covers the Python `python/yianc/` package via `python/pyproject.toml`; avoid `Any`, `object`, and `hasattr()`. Keep imports at the top of new files and preserve the existing handler-map dispatch style in the type checker and LLVM translator.
- Python private names use a **double** leading underscore: module-level functions and constants (`__helper`, `__CONSTANT`), methods (`self.__drain()`), nested helpers, and instance attributes. Use a **single** underscore only where the double form breaks the reference at run time: a name written as `__name` inside a class body is rewritten to `_ClassName__name`, so a *module-level* private that a class body mentions (for example `_SEP` in `python/yianc/utils/log.py`, `_is_within` in `python/yianc/analysis/source_provenance.py`) must keep one underscore. A private defined *and* used inside the same class body is `__name` like any other. Audit with `rg -n --glob '*.py' 'def _[^_]|^_[A-Za-z]|(self|cls)\._[^_]' python/yianc scripts`; review matches against the name-mangling exception.
- Organize modules by responsibility and dependency direction. Split long functions at meaningful interfaces, use guard clauses where they reduce nesting, and separate small logical blocks with blank lines. Comments explain ownership, prerequisites, non-obvious choices and algorithmic boundaries rather than restating individual statements.
- Keep user-program errors distinct from internal invariant failures. Preserve source identity and associated locations through errors; detach each location to owned path data before destroying a session whose diagnostics must outlive it. Never infer analysis error categories from message text outside the frontend compatibility boundary.
- `bak/` is ignored legacy material and `.conda/` is a local environment; do not modify them without explicit authorization. `build/` directories contain generated yianc/anx package and test output: regenerate artifacts through the relevant tools, do not hand-edit or commit them as source changes.
- User-facing language documentation is written in Chinese; this agent instruction file uses English. Keep all Git-tracked documentation synchronized during every task and verify factual claims against implementation, manifests and runnable commands. Documentation can be stale and is not evidence of an implemented feature.
- Write all code comments and docstrings in English, including those in tests, benchmarks, and examples. Keep user-visible text and test data in their intended language.
- Product and implementation documentation states the repository as it is now. Do not narrate the iteration that produced it: no history or chronology (此前/现在/改为/曾经/第一版/本轮/后续/待办), no changelogs, no plans or promises, no comparison with an earlier state, and no defect-story framing. Write the current fact in the present tense — what the code does, what the rule is, what a value means — and let it stand on its own; when a rule exists because of a hazard, describe the hazard as a property of the current design instead of recounting the defect that motivated the rule. Keep the voice neutral: 「X 是 Y」, not 「X 改成了 Y」 or 「X 以前是 Z」. This applies to `docs/`, `README` files, code comments, docstrings, and commit messages; `.agent/PLANS.md` and `.agent/plans/` are the operational exception defined above.
- Code comments and product documentation must be self-contained: never rely on a temporary document or its content — an implementation plan (such as those under `.agent/plans/`), a proposal, a TODO list, a bug log, or review notes — or on rule, section, or definition numbers that exist only there. State the fact itself instead; referring to another persistent document is fine. Protocol instructions and operational records may link `.agent/PLANS.md`, task plans, progress files, and their stable IDs, but must remain self-contained for execution and recovery. Existing violations are left in place: fix one when the surrounding code or document is next touched.
- `CLAUDE.md` must remain a relative symbolic link to `AGENTS.md`.
- There is no repository CI or installed project pre-commit hook. The daily YIAN formatter is implemented in `bootstrap/yianc/src/format/`; `python/yianc/format/` is a recovery copy. Use minimal relevant local checks under the Strict Local Verification Policy and inspect whitespace before committing.
