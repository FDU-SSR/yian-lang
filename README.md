# YIAN

YIAN is a statically typed programming language with a Python reference compiler and a native compiler and package manager written in YIAN. The compiler pipeline is `.an source → tokens → AST → HIR → CFG → LLVM IR → executable`.

## Requirements

- Linux x86-64.
- Python 3.12+ and llvmlite 0.49+ for the initial native compiler build.
- LLVM 22, clang 22, `llvm-config`, and a C toolchain for compilation and linking.

The installed `yianc`, `anx`, and `yian-lsp` commands are native executables. The language server uses the same installed toolchain resources and LLVM runtime libraries.

## Install

Build and install the compiler, package manager, standard library, and runtime:

```bash
conda run --no-capture-output -n yian-env bash scripts/install.sh \
  --prefix "$HOME/.local" --python python
```

Install the language server separately. This step requires the installed native `anx` and `yianc` commands and builds the server in raw mode:

```bash
PATH="$HOME/.local/bin:$PATH" \
  scripts/install_lsp.sh --prefix "$HOME/.local"
"$HOME/.local/bin/yian-lsp" --version
```

The server analyzes source in fat mode by default. Set `yian.languageServer.args` to `["--raw-pointers"]` in the VS Code settings to analyze raw-pointer targets.

Uninstall the language server and the shared toolchain independently:

```bash
scripts/uninstall_lsp.sh --prefix "$HOME/.local"
scripts/uninstall.sh --prefix "$HOME/.local"
```

The general installer manages `yianc`, `anx`, the standard library, and runtime resources. The language-server installer manages only `yian-lsp` and its install record; it reuses the shared resources under `share/yian`.

## Compile YIAN programs

The standard library is not an implicit input. Include `lib/src` when compiling code that imports `std.*`:

```bash
yianc lib/src tests/basic/array/assign.an
yianc -t ll lib/src tests/basic/array/assign.an -o build/assign.ll
yianc --log-spec "main=DEBUG" lib/src tests/basic/array/assign.an
```

See the [compiler CLI guide](docs/compile_script.md) for targets, package maps, analysis, formatting, and diagnostics.

## Use anx

`anx` manages projects, local dependencies, builds, tests, formatting, and package graphs:

```bash
anx new myapp
cd myapp
anx run
anx check
anx test
anx fmt --check
anx graph --json
```

Packages declare local dependencies in `package.anx` and import them by package name. See the [anx guide](docs/anx/index.md).

## Run checks

The paired test runner refreshes native compiler tools before running the selected suites:

```bash
conda run --no-capture-output -n yian-env python scripts/run_tests.py --all -q
```

Check the native language-server protocol against standalone, JSON-package, and compiler-package workspaces:

```bash
PATH="$HOME/.local/bin:$PATH" \
  conda run --no-capture-output -n yian-env python scripts/check_lsp.py --mode check
```

## Documentation

- [Language reference](docs/grammar/index.md)
- [anx guide](docs/anx/index.md)
- [Developer manual](docs/manual/index.md)
- [Language-server installation and manual verification](docs/manual/27.language_server.md)
- [Quick start](docs/grammar/00.quick_start.md)
- [Language and tooling overview](docs/proposal.md)
- [Memory-safety model](docs/security.md)
