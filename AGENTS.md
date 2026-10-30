# YIAN Agent Guide

## Scope and daily workflow

YIAN is a statically typed language with a native compiler, package manager,
formatter, language server, standard library and C runtime. Native `yianc` is
the daily compiler authority. The Python compiler supports explicit cold
recovery; maintain only the compatibility that recovery needs.

- Run compiler, package, test and benchmark commands from this repository root in the `yian-env` conda environment. Python orchestrates native tools.
- Inspect tracked and untracked changes before editing; preserve unrelated work. Use `apply_patch` for local edits when practical.
- Inventory and inspect Git-tracked project documentation for every task. Investigate task-related facts and synchronize every affected document, including related modules; do not expand into unrelated business changes.
- Complete authorized work through its user-visible boundary and required local acceptance. Keep connected layers, callers, ownership and error paths together. “Continue” resumes that feature without repeated permission; do not stop at scaffolding or a passing low-level slice.
- Keep `CLAUDE.md` a relative symbolic link to `AGENTS.md`. Do not modify `bak/` or `.conda/` without explicit authorization. Regenerate `build/` artifacts through tools; do not hand-edit or commit them as source.

## Planning and execution

[.agent/PLANS.md](.agent/PLANS.md) is authoritative for planning, execution,
acceptance, recovery and templates. Its protocols take precedence over
conflicting repository engineering rules; nonconflicting rules remain in
force. User instructions and platform permissions retain their precedence.

- Use the full protocol for complex tasks. Simple, self-contained edits need no plan directory. Do not create fictional tasks or empty plan placeholders.
- A planning request authorizes investigation and plan documents, not business implementation. `DECISION_COMPLETE` describes plan maturity. Execute when requested; the protocols do not choose models or switch client modes.
- Store stable requirements, design, decisions and acceptance in `.agent/plans/<task-name>/PLAN.md`. Store dynamic status, verification evidence and recovery instructions only in its sibling `PROGRESS.md`. Do not depend on chat history or maintain competing completion trackers.
- Enumerate **every** added, changed or removed public data structure, interface and semantic contract in PLAN: real path/symbol, before/after definitions, fields/signatures/generics, ownership/lifetime/error/invariant contracts, consumers, compatibility/migration, implementation tasks and acceptance IDs. Explicitly state and justify when there are no public changes.
- Changes to confirmed requirements, semantics, core design, public contracts, compatibility or acceptance standards need the decision required by the protocol. Make ordinary engineering choices within the established contract autonomously; do not ask again about choices already authorized.

## Strict Local Verification Policy

This policy applies to investigation, planning experiments, implementation,
diagnosis, recovery and every acceptance level. It overrides conflicting plans,
templates and engineering rules unless the user explicitly authorizes the
broader operation. Command examples describe capabilities, not authorization.

- Default to a minimal probe, one or a few directly relevant existing tests, or low-cost local static checks. Before each run, identify the property, minimal input, expected result and pass condition; inspect actual selection and indirect setup, build and test effects.
- Select an exact file, function or case. A module/package selector, substring filter, `--no-run`, or wrapper can still perform broad or expensive work. Use the smallest integration probe for cross-module behavior. Reuse fixtures and valid evidence; keep probes fast, independent and proportional to risk.
- Project-wide suites, full module/package/workspace tests, full regressions, batch integration/end-to-end tests, large unrelated selections and long project-wide builds/checks need explicit user authorization. This includes equivalent scripts, CI commands and collections of independent probes.
- “Execute the plan,” “continue,” “complete acceptance,” passing/failing local checks and multiple affected modules do not themselves authorize batch work. Separate local verification from extended verification; record the user's specific authorization and scope before an extended operation.
- Follow implement → local probe/test → analyze → fix → update progress. Reduce failures and rerun affected checks; do not broaden testing for diagnosis or repeat unaffected passing checks. Reuse still-valid evidence at task, milestone and final acceptance; no automatic fresh run is required.
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

Check task acceptance before closure, milestone acceptance before dependent
milestones, and final acceptance before the final commit or report. Report
executed checks, static review, required gaps and unrun extended verification
separately. Local evidence never establishes full-regression success.

## Environment and tool identity

The supported environment is Linux, Python 3.12+ and LLVM 22: clang and LLVM
tools plus llvmlite 0.49 for cold recovery. Dependencies are declared in
`python/requirements.txt` and `python/pyproject.toml`. Installation details:
[bootstrap manual](docs/manual/30.bootstrap.md),
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
- `--dump` observes tokens/AST/HIR/CFG/IR as stages complete (through HIR for `--check`). Native `--profile-output PATH` writes JSON and a `.txt` summary. `--runtime-profile-dir` needs an absolute path and an `exe` build. Details: [compiler CLI](docs/compile_script.md).
- Formatting belongs to `yian-fmt` in `fmt/`. `anx fmt` and LSP select it through nonempty `YIAN_FMT`, their executable's directory, then PATH. Native `yianc` rejects `--format`, `--format-check`, `-w`, `--write`, `--analyze`, `--json` and `--frontend-json`. See [formatter contracts](docs/manual/28.formatter.md).
- Recovery entry: `PYTHONPATH=python python -m yianc.main`. Its `--analyze`, `--frontend-json`, formatting flags and `-t none` belong only to recovery; `-t none` enters CFG lowering, unlike analysis-only mode. Do not infer native CLI behavior or feature parity from Python. Update/check Python only when changed sources/contracts participate in cold recovery.
- `anx/` owns `package.anx`, dependency graphs and the v2 compiler package map. Build/check/run/test select nonempty `YIANC`, otherwise PATH `yianc`; the canonical file must be executable ELF. There is no shell/Python fallback. `[dev-dependencies]` is visible only to `anx test`; `lib` packages use `check`/`test` without an executable build. See [anx](docs/anx/index.md) and [the v2 package-map contract](docs/manual/23.anx_graph.md).
- `anx build profile` records compiler statistics without running the program; `anx run profile` builds an instrumented executable and records function/call paths. `--profile-dir` is relative to the project root. Keep build statistics, instrumented artifacts and runtime reports distinct; paths/options are in [the anx CLI guide](docs/anx/03.cli.md).

## Source map

Native pipeline:
`.an → tokens → AST → desugared AST → HIR → address-storage normalization → CFG → LLVM IR → native output`.

| Location | Responsibility |
| --- | --- |
| `yianc/src/{main.an,driver/}` | Frontend/middle-end/backend drivers, CLI, dumps, profiling and linking. |
| `yianc/src/frontend/` | Source/session ownership, lexer/parser, AST, desugaring and traversal. |
| `yianc/src/analysis/` | Session/output, resolution, types, symbols/definitions, HIR, constants, lowering and checking passes. |
| `yianc/src/codegen/` | CFG, LLVM lowering and pointer-check enforcement. |
| `yianc/src/{common,diagnostics,package}/` | IDs/positions, owned diagnostics, package-map validation/provenance. |
| `anx/src/{manifest,project,driver}/` | TOML manifests, layouts/dependencies/ownership, command/test drivers. |
| `fmt/`, `lsp/`, `ide-support/` | Standalone formatter, native stdio LSP, VS Code extension. |
| `lib/src/` | Core types, containers, numeric operations, I/O, paths, processes and FFI helpers. |
| `runtime/src/`, `runtime/include/` | C allocation/failure/Linux I/O and shared ABI; optional profiling under `YIAN_RUNTIME_PROFILE`. |
| `bootstrap/` | JSON/JSON-RPC/LSP/TOML and LLVM/FFI support packages; LLVM 22 bindings and C shims. |
| `python/yianc/` | Recovery frontend, analysis, CFG/llvmlite backend, runtime builder, formatter and query APIs. |
| `tests/`, `scripts/`, `bench/`, `cve/` | Compiler fixtures, orchestration, measurements and separate vulnerability witnesses. |
| `docs/grammar/`, `docs/manual/`, `docs/anx/` | Language, implementation and tooling guides; site configured by `mkdocs_cn.yml`. |

## Compiler invariants

- `FrontendDriver` owns the completed frontend session and transfers it to `MiddleEndDriver`; `BackendDriver` owns CFG/LLVM and native publication. Keep `common` independent of analysis, package loading independent of AST/HIR, and diagnostic models separate from rendering. HIR models do not depend on checkers, sessions or constant evaluation. Constant arithmetic/intervals do not depend on HIR; typed evaluation does. Reusable containers belong in `lib`.
- Sessions transition prepared → analyzing → complete/failed once. Failed sessions retain partial data for diagnostics. `AnalysisOutput` owns its lists/warnings but refers to session records; release output before session. Never persist or mix session-local IDs. Detach diagnostic locations into owned paths before destroying their session.
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

## Language and resource contracts

- Postfix `T&` has no borrow checking or lifetime inference. In the common bare representation, non-ZST references to concrete non-trait `T` have the same data-pointer ABI/size as `T*`; trait references are trait objects with dynamic dispatch. Concrete safety metadata/checks are implementation-specific; do not infer them from the bare ABI. Native `yianc` and the C runtime own daily safety behavior; recovery Python defaults to fat pointers and supports raw mode.
- Assignment, argument passing and return are shallow copies: no source invalidation or implicit cleanup. `Move`, `.move()`, `bitcopy`, `@BitCopy`, compiler-provided `Clone` and simple-type assignment restrictions are absent. `Clone`/`Drop` are explicit library traits. Pointers do not implement `Clone`; `ptr.clone()` auto-dereferences and clones the pointed-to value.
- Give resources one explicit owner; a shallow-copied `String`, container, arena or session aliases its resources. Mark transfers and use `defer` on error paths. `del` releases an allocation without recursively releasing fields. Do not add implicit move, cleanup or deep-copy behavior to lowering.
- `Vec`, `HashMap`, `HashSet` and `Arena` release storage without recursively dropping payloads. `Vec`/`HashMap`/`HashSet` clones duplicate storage with shallow element copies; inspect contracts before assuming depth. `String` owns a buffer; `str` borrows it.
- `Vec` growth and hash-table structural mutation can invalidate references/iterators. Use arena records for references surviving appends and vectors for IDs, queues and temporary buffers. Do not structurally mutate a hash table during iteration.
- `insert_or_replace`/`remove_entry` return displaced resources; `get_or_insert_with` returns an unused input key on a hit. Callers handle them. Ordinary `insert`/`remove` do not release discarded payloads.
- `Hash<T>.hash` receives `T&` so nested writes reach the caller's accumulator. Equal values must hash equally; float hashing canonicalizes signed zero. Composite equality/hash use the same identity fields.
- Flush buffered output explicitly to observe errors; `BufWriter.drop()` frees its buffer without flushing. Keep Linux syscall ABI/errno in the thin C runtime layer, with owned errors and UTF-8 APIs in YIAN. Preserve the shared runtime ABI without requiring general Python/native parity.

## Tests and evidence

- `scripts/run_tests.py` runs `basic` (default), `safety`, `package`, `bootstrap` with installed native ELF tools and never refreshes/installs them. The supported implementation is `native`; legacy `python`/`both` values fail with exit 2. `--prefix` or `YIAN_INSTALL_PREFIX` selects installation. Package/bootstrap use native `anx` with the selected canonical `yianc`.
- Target modes default to both; `--pointer-mode fat|raw|both` selects them. Safety raw cases are N/A with reasons, not passes; all-N/A exits nonzero. Direct `anx test` also defaults to both; its `--raw-pointers` alias conflicts with an explicit mode. Outer package tests pass one explicit mode; `graph` runs once.
- `-f` is a substring filter, not an exact selector. Inspect selected cases and nested package tests before running. `--no-run` uses `yianc --check`/`anx check` and still performs analysis. `-x` includes experimental sources; `run_safety_tests.py` wraps safety. Full suites, parity `full`, LSP protocol matrices and runtime self-test builds need explicit authorization.
- Sources: `tests/<suite>/`; expected output: `tests/output/<suite>/`. `.err.an` must fail compilation with its `.ans` substring. Normal `.ans` specifies stdout; leading `Exit code <N>` specifies a nonzero runtime exit. Directory fixtures use `<dir>.ans`/`<dir>.err.ans`. Arguments/stdin: `tests/input/<suite>/<name>.args`/`.stdin`; package `.command` selects build/check/test/graph plus arguments. Inspect nested fixture effects.
- `<test>.compile.json` adds named variants with string-array `compiler_args` and at most one compile-error substring or runtime exit code. Accept the legacy array or object form. Optional `pointer_modes` must be unique, nonempty and include fat; excluding raw requires `raw_inapplicable_reason`. N/A is never a pass. See [fixture contracts](docs/manual/25.anx_tests.md).
- `scripts/check_toolchain_parity.py` validates existing native tools against fixed oracles; it neither refreshes tools nor compares Python. `--list` previews commands, repeated `--case ID` selects exact cases, and `--pointer-mode fat|raw|both` defaults to both. Inspect scope before execution.
- `runtime/build.py --check-consistency-only` checks native/C ABI declarations without building/self-tests. `--check` builds/runs normal self-tests; `--asan` runs sanitized self-tests. Distinguish costs. Use target-file Python checks instead of automatically running project-wide Pyright or compileall.
- Prefer extending an existing relevant fixture/compilation unit. Create a separate file for incompatible expectations, distinct diagnostics or setup; do not mask failures by combining cases. Tests may accompany implementation/documentation in the same scoped commit.

## CVE witnesses and benchmarks

- Translation/equivalence review uses [docs/c-to-yian.md](docs/c-to-yian.md) as the sole translation specification. Keep general rules there and concrete mappings, differences and evidence in case READMEs. It is a draft: earlier audits do not establish conformance to its current version.
- `cve/` is separate from compiler self-tests: 38 vulnerable C/YIAN pairs, one README per pair, fifteen source-mapped to upstream records, no fixed variants. Packaging or a guard hit alone does not prove faithful upstream reproduction. See [provenance and runner semantics](cve/README.md).
- `python scripts/run_cve.py --case CVE-2021-33304` selects one YIAN witness; `--language c|both` adds the ASan C reference. `--list`/`--check-layout` start no tools. `--all` requires explicit batch authorization.
- [bench/README.md](bench/README.md) defines sets, scales, runners and archives; [bench/SUITES.md](bench/SUITES.md) records sources/licenses. Check benchmark count, states, scale, warmups and samples before running. Measurement requires explicit authorization. Retain source commit, binary identity, environment/fingerprint and protocol with results; historical samples do not validate changed sources.

## Coding, navigation and documentation

- Organize modules by responsibility and dependency direction. Split functions at meaningful interfaces, use guard clauses and blank lines for logical blocks. Comments explain ownership, prerequisites and non-obvious boundaries.
- Keep user errors distinct from internal invariant failures. Preserve source identity/locations; do not infer analysis error categories from message text outside the frontend compatibility boundary.
- Omit numeric suffixes when annotations/context determine the type, such as `const ID: u64 = 0;`. Retain suffixes required for widths, conversions, inference or test semantics; do not change semantics to remove one.
- For Python structure/references, prefer available Serena/LSP symbol tools; inspect callers before modifying functions/classes and use semantic rename where applicable. Fall back to `rg` and source reading when semantic tools cannot answer. Use targeted diagnostics under the local-verification policy.
- Python follows strict Pyright (`python/pyproject.toml`): avoid `Any`, `object` and `hasattr()`, keep imports at file top and preserve handler-map dispatch in type checking/LLVM translation. Private functions, constants, methods, attributes and nested helpers use `__`. A module-level private referenced inside a class uses `_` when name mangling would break lookup; names defined/used within that class still use `__`. See [coding conventions](docs/manual/19.coding_style.md).
- User-facing language documentation is Chinese; this guide is English. Code comments/docstrings are English, including tests/examples. Keep user-visible text/test data in their intended language, with this directory rule: **all text in `cve/` and `bench/` is English**, including READMEs, comments, docstrings, messages and JSON notes; do not add Chinese text there.
- Product/implementation docs, comments, docstrings and commit messages state current facts in a neutral voice. No change history, defect stories, plans, promises or comparisons with an earlier state. Explain hazards as properties of the current design. `.agent/PLANS.md` and `.agent/plans/` are operational exceptions and may contain history, future work and checkpoints.
- Product docs/comments must stand alone: no dependency on temporary plans, proposals, TODO/bug/review notes or their rule numbers. State the fact directly; links to persistent documentation are allowed. Protocol/operational documents may link plans, progress and stable IDs but must support independent execution/recovery. Fix existing violations when touching their surrounding text.
- Verify documentation against source, manifests and command semantics. Documented/planned features are not implementation evidence. There is no repository CI or installed project pre-commit hook.

## Git delivery

- Commit only when requested; push only when separately authorized. Review all tracked/untracked changes and stage only task scope. Run `git diff --check` and `git diff --cached --check` for that scope before committing.
- Use independently useful end-to-end features/fixes as review units. Multiple scoped commits are allowed; do not commit unfinished scaffolding, partially wired features or progress checkpoints. Test-only commits are not required.
- Report modified files/behavior, evidence, remaining limitations, commit hashes when applicable, push state and unrelated worktree changes. When delivering changes through the paper repository, commit this submodule first and follow the parent's instructions for its Git link, anonymity and artifact freeze.
