# YIAN Agent Guide

## Scope and daily workflow

YIAN is a statically typed language with a native compiler, package manager,
formatter, language server, standard library and C runtime. Native `yianc` is
the daily compiler authority. The Python compiler supports explicit cold
recovery; maintain only the compatibility that recovery needs.

- Run compiler, package, test and benchmark commands from this repository root in the `yian-env` conda environment. Python orchestrates native tools.
- Inspect tracked and untracked changes before editing; preserve unrelated work. Use `apply_patch` for local edits when practical.
- Before writing or modifying `.an` files, read the [YIAN writing skill](.agents/skills/yian-writing/SKILL.md).
- Inventory and inspect Git-tracked project documentation for every task. Investigate task-related facts and synchronize every affected document, including related modules; do not expand into unrelated business changes.
- Complete authorized work through its user-visible boundary and required local acceptance. Keep connected layers, callers, ownership and error paths together. “Continue” resumes that feature without repeated permission; do not stop at scaffolding or a passing low-level slice.
- Keep `CLAUDE.md` a relative symbolic link to `AGENTS.md`. Do not modify `bak/` or `.conda/` without explicit authorization. Regenerate `build/` artifacts through tools; do not hand-edit or commit them as source.

## Planning and execution

`.agents/PLANS.md` is inactive unless the user explicitly instructs you to follow
it. Do not adopt its planning, execution, acceptance, recovery or template
protocol by default, and do not create or update `.agents/plans/` records merely
because a task is large or crosses modules. An ordinary implementation, fix,
migration or verification request is not such an instruction; a request for a
plan, investigation or design also does not activate the protocol unless the
user names it or asks to follow it. Rules stated directly in this guide,
including the local verification policy and reporting statuses, remain in force
independently of that file.

- Act directly on the user's request within this guide. Simple, self-contained edits need no plan. For larger work, track the steps in the session and proceed; do not stop at a plan unless the user asked for one.
- If the user explicitly requests the `.agents/PLANS.md` protocol, follow it for that task only. Its rules then take precedence over conflicting repository engineering rules; user instructions and platform permissions retain their precedence. Do not extend that protocol to later tasks without a new explicit request.
- For changes that add, change or remove public data structures, interfaces or semantic contracts, identify the real paths and symbols, before/after definitions, owners, consumers, compatibility effects and error behavior, then report them with the change. Keep the inventory proportional to the task; it does not require a PLAN document.
- Changes to confirmed requirements, semantics, core design, public contracts, compatibility or acceptance standards require user confirmation. Make ordinary engineering choices within the established contract autonomously; do not ask again about choices already authorized.

## Strict Local Verification Policy

This policy applies to investigation, planning experiments, implementation,
diagnosis, recovery and every acceptance level. It overrides conflicting plans,
templates and engineering rules unless the user explicitly authorizes the
broader operation. Command examples describe capabilities, not authorization.

- Default to a minimal probe, one or a few directly relevant existing tests, or low-cost local static checks. Before each run, identify the property, minimal input, expected result and pass condition; inspect actual selection and indirect setup, build and test effects.
- Select an exact file, function or case. A module/package selector, substring filter, `--no-run`, or wrapper can still perform broad or expensive work. Use the smallest integration probe for cross-module behavior. Reuse fixtures and valid evidence; keep probes fast, independent and proportional to risk.
- Project-wide suites, full module/package/workspace tests, full regressions, batch integration/end-to-end tests, large unrelated selections and long project-wide builds/checks need explicit user authorization. This includes equivalent scripts, CI commands and collections of independent probes.
- “Continue,” “complete acceptance,” passing/failing local checks and multiple affected modules do not themselves authorize batch work. Separate local verification from extended verification; record the user's specific authorization and scope before an extended operation.
- Follow implement → local probe/test → analyze → fix → record the result. Reduce failures and rerun affected checks; do not broaden testing for diagnosis or repeat unaffected passing checks. Reuse still-valid evidence when closing the task; no automatic fresh run is required.
- If extended verification is indispensable, explain the need and request authorization once. Pending authorization, record it as unexecuted, disclose the risk and continue independent work. Unrun batch checks do not fail default local acceptance unless the user explicitly requires them.
- Never weaken assertions, bless outputs or change expectations merely to pass. Expected runtime failures are not necessarily analysis rejections. A declaration that builds without instantiation is not runtime evidence for its generic instances; Python success does not verify native behavior.

Record implementation and verification separately:

| Status | Meaning |
| --- | --- |
| `IMPLEMENTED` | Implementation is complete. |
| `LOCALLY_VERIFIED` | The defined local checks passed. |
| `FULLY_VERIFIED` | Every explicitly user-required acceptance item passed within its stated scope; this does not imply exhaustive coverage. |
| `UNVERIFIED` | Necessary verification is missing or invalid. |
| `BLOCKED` | Work cannot proceed; record the cause and release condition. |

Check the user's acceptance conditions before closure and before the final
commit or report. Report executed checks, static review, required gaps and
unrun extended verification separately. Local evidence never establishes
full-regression success.

## Environment and tool identity

The supported environment is Linux, Python 3.12+ and LLVM 22: clang and LLVM
tools plus llvmlite 0.49 for cold recovery. Dependencies are declared in
`python/requirements.txt` and `python/pyproject.toml`. Installation details:
[bootstrap manual](yianc/docs/dev/21.bootstrap.md),
[installation guide](docs/anx/06.install.md).

| Entry | Purpose and effects |
| --- | --- |
| `scripts/setup_llvm_toolchain.sh` | Installs missing C/LLVM packages with sudo and selects LLVM 22 commands; installs no Python packages. `--check` only reports versions. |
| `scripts/bootstrap_toolchain.sh` | Explicit cold recovery through Python; builds/publishes raw native `yianc`, `anx`, `yian-fmt` and shared resources with source/builder/artifact identities. Does not build LSP. |
| `scripts/refresh_toolchain.sh` | Freezes compatible native seeds, rebuilds raw/O2 `yianc`, `anx`, `yian-fmt`, `yian-lsp`, checks staged formatter/LSP behavior and publishes resources transactionally. No Python or cold fallback. |
| `scripts/uninstall.sh --prefix PATH` | Removes managed commands/resources. `--lsp-only` retains other tools/resources; `--python PATH` handles recognized legacy Python launchers. |

- Native verification must use binaries matching the relevant sources, environment and install prefix. Verify identity before treating results as evidence. Reuse a verified current binary; do not reinstall unnecessarily. Refresh/cold bootstrap are expensive operations subject to authorization, including for LSP checks. Missing prerequisites may be reported to the user for setup; record checks that could not run.
- Native linking and package C-shim compilation select `YIAN_CC`, then `clang`, then `cc`. `runtime/build.py` independently uses `clang` and `llvm-ar` on PATH. LLVM consumer packages validate their native requirements.
- Installed tools use associated resources and need no Python/source checkout at runtime. Pass `--compiler-root .` for deterministic checkout-local compiler resources. Standalone semantic checks/compilation need `lib/src` as an explicit source input; root configuration alone does not add it. Package mode obtains standard-library sources through its package map.
- `YIAN_LIB` names the standard-library source directory; `YIAN_ROOT` names a checkout root. Native runtime lookup uses `--compiler-root`, nonempty `YIAN_ROOT`, then installation resources. Native `anx` resolves standard-library resources from these environment roots or its installation and has no `--compiler-root` flag.
- Trust and package ownership use canonical roots and path-component boundaries, never a directory merely named `lib`, `std` or `tests`. Restricted-operation and FFI permissions are distinct; raw-pointer mode grants neither.

## Daily commands and recovery boundary

Examples below remain subject to source identity and the local-verification policy:

```bash
conda run --no-capture-output -n yian-env yianc --check --compiler-root . lib/src tests/basic/array/access.an
conda run --no-capture-output -n yian-env yianc --compiler-root . -O2 -o build/access lib/src tests/basic/array/access.an
conda run --no-capture-output -n yian-env yian-fmt --check yianc/src/main.an
```

- Native `yianc --check` runs frontend/middle-end checks through HIR validation, coverage and definite assignment; it accepts no target/output/native-link options. Compilation targets are `exe` (default), `ll` and `bc`. Checks need no `main` but validate one when present; compilation needs an entry. Use explicit `-o` paths when names matter. `-t ll` emits unoptimized IR.
- `--dump` observes tokens/AST/HIR/CFG/IR as stages complete (through HIR for `--check`). Native `--profile --profile-output PATH` writes JSON and a `.txt` summary. `--runtime-profile-dir` needs an absolute path and an `exe` build. Details: [compiler CLI](yianc/docs/compile_script.md).
- Formatting belongs to `yian-fmt` in `fmt/`. `anx fmt` and LSP select it through nonempty `YIAN_FMT`, their executable's directory, then PATH. Native `yianc` rejects `--format`, `--format-check`, `-w`, `--write`, `--analyze`, `--json` and `--frontend-json`. See [formatter contracts](docs/manual/28.formatter.md).
- Use `scripts/bootstrap_toolchain.sh` for explicit cold recovery. Update/check the recovery implementation only when changed sources or contracts participate in that workflow.
- `anx/` owns `package.anx`, dependency graphs and the v2/v3 compiler package map. Maps with `nativeC` or `nativeRoots` use v3; other maps retain v2. Native `yianc` accepts both. Preserve the v2 package-map input used by cold recovery. Build/check/run/test select nonempty `YIANC`, otherwise PATH `yianc`; the canonical file must be executable ELF. There is no shell/Python fallback. `[dev-dependencies]` is visible only to `anx test`; `lib` packages use `check`/`test` without an executable build. See [anx](docs/anx/index.md) and [the package-map contract](docs/manual/23.anx_graph.md).
- `anx build --profile` records compiler statistics without running the program; `anx run --runtime-profile` builds an instrumented executable and records function/call paths. `--profile-dir` and `--runtime-profile-dir` resolve relative to the project root. Keep build statistics, instrumented artifacts and runtime reports distinct; paths/options are in [the anx CLI guide](docs/anx/03.cli.md).

## Source map

Native pipeline:
`.an → tokens → AST → desugared AST → HIR → address-storage normalization → CFG → LLVM IR → native output`.

| Location | Responsibility |
| --- | --- |
| `yianc/src/{main.an,config.an}` | CLI parsing, stage sequencing and the process exit path. |
| `yianc/src/{preprocess.an,input_error.an}` | Package-map loading, source-root/trust setup, source collection and their terminal diagnostics. |
| `yianc/src/telemetry/`, `yianc/src/{host.an,roots.an}` | Logging/timing/profile recording; host resource and source/runtime root resolution. |
| `yianc/src/frontend/` | Source/unit ownership, lexer/parser, AST, desugaring, traversal and stage dumps; `driver.an` composes the stage. |
| `yianc/src/analysis/` | Session/output, permission classification, resolution, types, symbols/definitions, HIR, constants, lowering, checking passes and HIR dump; `driver.an` owns the pass sequence. |
| `yianc/src/codegen/` | CFG, LLVM lowering, pointer-check enforcement, native linking and stage dumps; `driver.an` owns emission and publication. |
| `yianc/src/{common,diagnostics,package}/` | IDs/positions, owned diagnostics and stderr reporting, package-map validation/provenance. |
| `anx/src/{manifest,project,driver}/` | TOML manifests, layouts/dependencies/ownership, command/test drivers. |
| `fmt/`, `lsp/`, `ide-support/` | Standalone formatter, native stdio LSP, VS Code extension. |
| `lib/src/` | Core types, containers, numeric operations, I/O, paths, processes and FFI helpers. |
| `runtime/src/`, `runtime/include/` | C allocation/failure/Linux I/O and shared ABI; optional profiling under `YIAN_RUNTIME_PROFILE`. |
| `bootstrap/` | JSON/JSON-RPC/LSP/TOML and LLVM/FFI support packages; LLVM 22 bindings and C shims. |
| `python/` | Explicit cold recovery and its dependencies. |
| `tests/`, `scripts/`, `bench/`, `cve/` | Compiler fixtures, orchestration, measurements and separate vulnerability witnesses. |
| `yianc/docs/`, `docs/grammar/`, `docs/manual/`, `docs/anx/` | Compiler implementation, language, remaining toolchain and package-manager guides; site configured by `mkdocs_cn.yml`. |

## Compiler invariants

- `preprocess.an` loads the package map, resolves source roots/trust and collects the source files once into an immutable `SourceTable`. `FrontendDriver.new` takes that table; `FrontendDriver.run` performs lex/parse/desugar and returns the completed `UnitTable`; the CLI or LSP then allocates an `AnalysisSession` for it and creates a `TargetLayout` from the session type store before semantic analysis. `UnitTable` owns each unit's source, tokens and desugared AST by `UnitId` and implements `SourceMap` after the frontend completes. `AnalysisSession` owns the unit table, the package map, trust and source permissions, classifies sources before restricted-operation checks, and grants FFI permission only through a source's owning package. `MiddleEndDriver` borrows its options and reporter, owns the session until a successful run transfers it to `MiddleEndResult`, and leaves a failed session available for diagnostics. `TargetLayout` owns the LLVM module and type cache; `BackendDriver` is built only for compilation after analysis succeeds and owns CFG lowering and native publication. Keep `common` independent of analysis, package loading independent of AST/HIR, and diagnostic models separate from rendering. HIR models do not depend on checkers, sessions or constant evaluation. Constant arithmetic/intervals do not depend on HIR; typed evaluation does. Reusable containers belong in `lib`.
- Fatal user errors outside CLI initialization return through `Result` to `main`; command-line argument and log-configuration failures render and exit inside `parse_args` and `CompilerTrace.new`, respectively; warnings are delivered synchronously through `DiagnosticReporter` and are emitted immediately; the first user error terminates analysis and returns through `Result`; internal invariant violations panic where they are detected. Each source file owns one `UnitId`; the frozen `SourceTable` is the frontend input and the completed `UnitTable` is its output, both implement `SourceMap`, and `SourceMap.file` panics for an unassigned ID. `render(diagnostic, sources)` produces the single CLI text form.
- Sessions transition prepared → analyzing → complete/failed once. Failed sessions retain partial data for diagnostics. `AnalysisOutput` owns its lists but refers to session records; release output before session. Never persist or mix session-local IDs. CLI and LSP report diagnostics while the owning source table lives; records kept across sessions copy the message, path and span.
- Escaping references require stable storage. `Arena<T>` preserves addresses on allocation and has O(1) indexed lookup. `clear`/`drop` invalidate references without dropping payloads: owners release payloads before storage. Do not shallow-copy owning stores and release both copies.
- Structural types use `TypeStore` interning; do not mutate a value against its key. Nominal identities distinguish declared/complete definitions; reject queries requiring completion on incomplete records.
- Read-only AST traversal lives in `frontend/ast/walk.an` (`TryAstVisitor<E>`); `traversal.an` adapts infallible visitors and separates rewriting. Skip omits children and `leave`; errors omit ancestor `leave`; shared nodes are visited per incoming edge. Use `defer` for error cleanup; do not alter graph edges during read-only traversal.
- AST additions cover cleanup, allocation, JSON, traversal/rewriting and relevant frontend/lowering dispatch. HIR additions cover cleanup, traversal, verification and consuming passes.
- Native analysis checks root procedures and discovered concrete instances beyond calls reachable from `main`. Preserve the distinction between checked definitions and those selected for code generation. Compile-time branch selection lowers only the chosen branch; coverage/assignment inspect HIR.
- Normalize data-address operands and implicit storage consumers into places or local place blocks before HIR verification/coverage/assignment. CFG address lowering requires normalized storage and must not invent rvalue fallback locals. Function addresses and slice-element addresses keep dedicated paths.
- Tagged enums with tag/payload storage use `EnumInitAt`, `EnumTagAt`, `EnumFieldAt`, `EnumCopyAt` place operations; copies are shallow snapshots. ZST/niche representations keep value operations. Classify callable linkage once: module-local generated functions are internal for every LLVM target; entry/extern declarations retain external names.
- `?` desugaring evaluates its operand once through trait-qualified `Try.branch` and `FromResidual.from_residual`; preserve generated type/pattern metadata. `IS_RAW_MODE` is supplied by the active compilation configuration.
- Ordinary `sizeof` retains its queried type through HIR/CFG until backend lowering. Immediate evaluation uses the selected target's layout only when execution reaches the operation. Do not use Python host sizes or infer layouts in semantic passes. Short-circuited operations do not query sizes; unselected compile-time branches are not type-checked.
- Native `yianc` exposes strict analysis without editor facts/query APIs. LSP owns its source/semantic indexes and detached snapshots. Do not infer native editor capabilities from recovery Python APIs. LSP stdout is protocol-only; logs use stderr or a configured file. See [LSP contracts](docs/manual/27.language_server.md).
- Preserve shallow copy semantics in lowering. Introduce resource transfers, deep copies and cleanup only through explicit source operations.
- In the common bare representation, non-ZST references to concrete non-trait `T` have the same data-pointer ABI and size as `T*`. Trait references are trait objects with dynamic dispatch. Native `yianc` and the C runtime define the safety metadata and checks independently of the bare ABI.
- Keep Linux syscall ABI and `errno` handling in the thin C runtime layer, with owned errors and UTF-8 APIs in YIAN. Preserve the shared runtime ABI. The [cold recovery scope](#daily-commands-and-recovery-boundary) governs Python compatibility.

## Tests and evidence

- Do not write tests of tests or automate validation of test logic itself, including test/benchmark runners, case selection, assertions, expected-result handling and result-reporting logic. Review this logic manually; the local-verification and fixture-extension rules do not authorize meta-tests.
- If such an automated probe is indispensable, keep its code and generated files in a temporary directory outside the repository and delete them after use. Never place, stage or commit these meta-tests or their artifacts in the repository, including ignored paths, fixtures, scripts, CI configuration or embedded checks added solely to test test logic.
- `scripts/run_tests.py` runs `basic` (default), `safety`, `package`, `bootstrap` with installed native ELF tools and never refreshes/installs them. The supported implementation is `native`; legacy `python`/`both` values fail with exit 2. `--prefix` or `YIAN_INSTALL_PREFIX` selects installation. Package/bootstrap use native `anx` with the selected canonical `yianc`.
- Target modes default to both; `--pointer-mode fat|raw|both` selects them. Safety raw cases are N/A with reasons, not passes; all-N/A exits nonzero. Direct `anx test` also defaults to both; its `--raw-pointers` alias conflicts with an explicit mode. Outer package tests pass one explicit mode; `graph` runs once.
- `-f` is a substring filter, not an exact selector. Inspect selected cases and nested package tests before running. `--no-run` uses `yianc --check`/`anx check` and still performs analysis. `-x` includes experimental sources; `run_safety_tests.py` wraps safety. Full suites, parity `full`, LSP protocol matrices and runtime self-test builds need explicit authorization.
- Sources: `tests/<suite>/`; expected output: `tests/output/<suite>/`. `.err.an` must fail compilation with its `.ans` substring. Normal `.ans` specifies stdout; leading `Exit code <N>` specifies a nonzero runtime exit. Directory fixtures use `<dir>.ans`/`<dir>.err.ans`. Arguments/stdin: `tests/input/<suite>/<name>.args`/`.stdin`; package `.command` selects build/check/test/graph plus arguments. Inspect nested fixture effects.
- `<test>.compile.json` adds named variants with string-array `compiler_args` and at most one compile-error substring or runtime exit code. Accept the legacy array or object form. Optional `pointer_modes` must be unique, nonempty and include fat; excluding raw requires `raw_inapplicable_reason`. N/A is never a pass. See [fixture contracts](docs/manual/25.anx_tests.md).
- `scripts/check_toolchain_parity.py` validates existing native tools against fixed oracles; it neither refreshes tools nor compares Python. `--list` previews commands, repeated `--case ID` selects exact cases, and `--pointer-mode fat|raw|both` defaults to both. Inspect scope before execution.
- `runtime/build.py --check-consistency-only` checks native/C ABI declarations without building/self-tests. `--check` builds/runs normal self-tests; `--asan` runs sanitized self-tests. Distinguish costs. Use target-file Python checks instead of automatically running project-wide Pyright or compileall.
- For product regression tests, prefer extending an existing relevant fixture/compilation unit. Create a separate file for incompatible expectations, distinct diagnostics or setup; do not mask failures by combining cases. Product tests may accompany implementation/documentation in the same scoped commit.

## CVE witnesses and benchmarks

- Translation/equivalence review uses [docs/c-to-yian.md](docs/c-to-yian.md) as the sole translation specification. Keep general rules there and concrete mappings, differences and evidence in case READMEs. Earlier audits do not establish conformance to the current rules.
- `cve/` is separate from compiler self-tests: 40 vulnerable C/YIAN pairs, one README per pair, seventeen source-mapped to upstream records, no fixed variants. Packaging or a guard hit alone does not prove faithful upstream reproduction. See [provenance and runner semantics](cve/README.md).
- `python scripts/run_cve.py --case CVE-2021-33304` selects one YIAN witness; `--language c|both` adds the ASan C reference. `--list`/`--check-layout` start no tools. `--all` requires explicit batch authorization.
- [bench/README.md](bench/README.md) defines sets, scales, runners and result files; [bench/SUITES.md](bench/SUITES.md) records sources/licenses. Check benchmark count, states, scale, warmups and samples before running. Measurement requires explicit authorization. `bench/results/` is machine-local, Git-ignored output; do not force-add result files. Performance results contain only numeric measurements/statistics/ratios and benchmark/state labels. Do not persist source/tool/environment/parameter/validation metadata, sidecars, logs or archives. Runtime checks remain transient; historical samples do not validate changed sources.

## Coding, navigation and documentation

- Do not add interfaces, data structures, fields, methods, or abstraction layers for requirements that do not exist. Implement only what a current call path or confirmed contract requires, and remove declarations when their last caller disappears; do not keep unused parameters, variants, wrappers, or adapters for hypothetical future use.
- Tests and external consumers such as the LSP do not justify adding a dedicated interface to a core module; adapt within the consumer's own boundary.
- Organize modules by responsibility and dependency direction. Split functions at meaningful interfaces, use guard clauses and blank lines for logical blocks. Comments explain ownership, prerequisites and non-obvious boundaries.
- Keep user errors distinct from internal invariant failures. Preserve source identity/locations; do not infer analysis error categories from message text outside the frontend compatibility boundary.
- For Python structure/references, prefer available Serena/LSP symbol tools; inspect callers before modifying functions/classes and use semantic rename where applicable. Fall back to `rg` and source reading when semantic tools cannot answer. Use targeted diagnostics under the local-verification policy.
- Python follows strict Pyright (`python/pyproject.toml`): avoid `Any`, `object` and `hasattr()`, keep imports at file top and preserve handler-map dispatch in type checking/LLVM translation. Private functions, constants, methods, attributes and nested helpers use `__`. A module-level private referenced inside a class uses `_` when name mangling would break lookup; names defined/used within that class still use `__`.
- User-facing language documentation is Chinese; this guide is English. Code comments/docstrings are English, including tests/examples. Keep user-visible text/test data in their intended language, with this directory rule: **all text in `cve/` and `bench/` is English**, including READMEs, comments, docstrings, messages and JSON notes; do not add Chinese text there.
- Compiler documentation describes native `yianc` only. Omit the Python recovery compiler's CLI, APIs and implementation from product and developer documentation. Installation guides may describe the cold bootstrap script and its prerequisites.
- Product/implementation docs, comments, docstrings and commit messages state current facts in a neutral voice. No change history, defect stories, plans, promises or comparisons with an earlier state. Explain hazards as properties of the current design. When the user explicitly requests the `.agents/PLANS.md` protocol, that file and `.agents/plans/` are operational exceptions and may contain history, future work and checkpoints.
- Product docs/comments must stand alone: no dependency on temporary plans, proposals, TODO/bug/review notes or their rule numbers. State the fact directly; links to persistent documentation are allowed. Protocol/operational documents may link plans, progress and stable IDs but must support independent execution/recovery. Fix existing violations when touching their surrounding text.
- Verify documentation against source, manifests and command semantics. Documented/planned features are not implementation evidence. There is no repository CI or installed project pre-commit hook.

## Git delivery

- Commit only when requested; push only when separately authorized. Review all tracked/untracked changes and stage only task scope. Run `git diff --check` and `git diff --cached --check` for that scope before committing.
- Use independently useful end-to-end features/fixes as review units. Multiple scoped commits are allowed; do not commit unfinished scaffolding, partially wired features or progress checkpoints. Test-only commits are not required.
- Report modified files/behavior, evidence, remaining limitations, commit hashes when applicable, push state and unrelated worktree changes. When delivering changes through the paper repository, commit this submodule first and follow the parent's instructions for its Git link, anonymity and artifact freeze.
