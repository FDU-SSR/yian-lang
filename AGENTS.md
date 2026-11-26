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
- Complete authorized work through its user-visible boundary and required local acceptance. Follow affected callers and error paths. “Continue” resumes that feature without repeated permission; do not stop at scaffolding or a passing low-level slice.
- Explain YIAN code in terms of the behavior the user asked about. Discuss ownership or lifetimes only when the user asks or when a specific operation makes them relevant to the answer. In that case, identify the operation and its observable consequence using YIAN semantics.
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
`python/requirements.txt` and `python/pyproject.toml`. Installation, toolchain
setup, refresh and uninstall are documented in the
[bootstrap manual](yianc/docs/dev/21.bootstrap.md) and
[installation guide](docs/anx/06.install.md).

- Native verification must use binaries matching the relevant sources, environment and install prefix. Verify identity before treating results as evidence. Reuse a verified current binary; do not reinstall unnecessarily. Missing prerequisites may be reported to the user for setup; record checks that could not run.
- Native linking and package C-shim compilation select `YIAN_CC`, then `clang`, then `cc`. `runtime/build.py` independently uses `clang` and `llvm-ar` on PATH. LLVM consumer packages validate their native requirements.
- Installed tools use associated resources and need no Python/source checkout at runtime.
- `YIAN_LIB` names the standard-library source directory; `YIAN_ROOT` names a checkout root. Native runtime lookup uses `--compiler-root`, nonempty `YIAN_ROOT`, then installation resources. Native `anx` resolves standard-library resources from these environment roots or its installation and has no `--compiler-root` flag.
- Trust and package ownership use canonical roots and path-component boundaries, never a directory merely named `lib`, `std` or `tests`. Restricted-operation and FFI permissions are distinct; raw-pointer mode grants neither.

## Daily commands and recovery boundary

- Use the installed native `yianc`, `anx` and `yian-fmt`. Compiler, package-manager and formatter options are documented in the [compiler CLI](yianc/docs/compile_script.md), [anx CLI](docs/anx/03.cli.md) and [formatter contracts](docs/manual/28.formatter.md).
- Use `--compiler-root .` for checkout-local compiler resources. Standalone checks and compilation need `lib/src` as an explicit source input; package mode obtains the standard library through its package map.
- `anx` selects nonempty `YIANC`, otherwise PATH `yianc`; the canonical file must be an executable ELF. Preserve the v2 package-map input used by cold recovery.
- Use `scripts/bootstrap_toolchain.sh` for explicit cold recovery and `scripts/refresh_toolchain.sh` for normal native refresh. Both are expensive and require authorization.
- `anx build --profile` records compiler statistics. Do not persist machine-local result files.

## Source map

Native pipeline:
`.an → tokens → AST → desugared AST → HIR → address-storage normalization → CFG → LLVM IR → native output`.

| Path | Responsibility |
| --- | --- |
| `yianc/src/` | Native compiler CLI and compilation stages. |
| `anx/`, `fmt/`, `lsp/`, `ide-support/` | Package manager, formatter, language server and editor integration. |
| `lib/src/`, `runtime/`, `bootstrap/` | Standard library, C runtime and support packages. |
| `tests/`, `scripts/`, `bench/`, `cve/` | Compiler fixtures, orchestration, benchmarks and vulnerability witnesses. |
| `python/` | Explicit cold-recovery toolchain. |
| `yianc/docs/`, `docs/grammar/`, `docs/manual/`, `docs/anx/` | Compiler, language, toolchain and package-manager documentation. |

## Tests and evidence

- Do not write tests of tests or automate validation of test logic itself, including test/benchmark runners, case selection, assertions, expected-result handling and result-reporting logic. Review this logic manually; the local-verification and fixture-extension rules do not authorize meta-tests.
- If such an automated probe is indispensable, keep its code and generated files in a temporary directory outside the repository and delete them after use. Never place, stage or commit these meta-tests or their artifacts in the repository, including ignored paths, fixtures, scripts, CI configuration or embedded checks added solely to test test logic.
- `scripts/run_tests.py` runs the selected suite with installed native tools and never refreshes them. Full suites, parity runs, LSP protocol matrices and runtime self-test builds need explicit authorization.
- `-f` is a substring filter, not an exact selector. Inspect selected cases and nested package tests before running; `--no-run` still performs analysis.
- Fixture layout, expected outputs, compile variants, pointer modes and runner semantics are documented in [fixture contracts](docs/manual/25.anx_tests.md).
- For product regression tests, prefer extending an existing relevant fixture/compilation unit. Create a separate file for incompatible expectations, distinct diagnostics or setup; do not mask failures by combining cases. Product tests may accompany implementation/documentation in the same scoped commit.

## CVE witnesses and benchmarks

- Translation/equivalence review uses [docs/c-to-yian.md](docs/c-to-yian.md) as the sole translation specification. Keep general rules there and concrete mappings, differences and evidence in case READMEs. Earlier audits do not establish conformance to the current rules.
- `cve/` and `bench/` are separate from compiler self-tests. Provenance, case counts, runner semantics, benchmark sets and result-file rules are documented in [cve/README.md](cve/README.md) and [bench/README.md](bench/README.md).
- `--all` for CVE runs and benchmark measurement require explicit batch authorization. `bench/results/` is machine-local, Git-ignored output; do not force-add result files or persist source/tool/environment metadata, sidecars or logs.

## Coding, navigation and documentation

- Do not add interfaces, data structures, fields, methods, or abstraction layers for requirements that do not exist. Implement only what a current call path or confirmed contract requires, and remove declarations when their last caller disappears; do not keep unused parameters, variants, wrappers, or adapters for hypothetical future use.
- Tests and external consumers such as the LSP do not justify adding a dedicated interface to a core module; adapt within the consumer's own boundary.
- Organize modules by responsibility and dependency direction. Split functions at meaningful interfaces, use guard clauses and blank lines for logical blocks. Comments explain prerequisites and behavior that a reader cannot infer from the code.
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
