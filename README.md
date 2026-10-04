# YIAN 编译器

YIAN 是一门自研的静态类型编程语言，编译器用 Python 实现。
完整流水线：`.an 源码 → Tokens → AST → 去糖 AST → HIR → CFG IR → LLVM IR → 可执行文件`。

## 运行环境

- Linux（如 Ubuntu）
- Python 3.12+（编译器使用泛型函数语法；`anx` 用 `tomllib` 读清单）
- LLVM 22 工具链：llvmlite 0.49（自带 LLVM 22）、clang 22，以及 LLVM 工具（`opt`、`llvm-dis`、`llvm-link`、`llvm-config`）

## 安装与卸载

一键安装，把 `yianc`（编译器）、`anx`（包管理器）与 `yian-lsp`（语言服务器）装进当前
Python 环境：

```bash
scripts/install.sh                    # Default: editable installation; commands use this checkout.
scripts/install.sh --with-deps        # Resolve dependencies with pip; otherwise llvmlite must already be importable.
scripts/install.sh --user             # Install into user site-packages.
scripts/install.sh --python PATH      # Select the interpreter.
```

- **editable**（默认）：`yianc` / `anx` 直接指向这个检出，改 `compiler/`、`anx/`、`lsp/`
  （含新增模块）后无需重装。开发时请用这种安装。
- **`--regular`**：拷贝安装。它**不包含 `lib/`**，所以要告诉工具标准库在哪：

  ```bash
  export YIAN_LIB=/path/to/yian/lib/src     # 标准库源码根
  # 或者
  export YIAN_ROOT=/path/to/yian            # 检出根
  ```

- 脚本会预检 Python ≥ 3.11 与 llvmlite，并在已安装 setuptools ≥ 68 时加上
  `--no-build-isolation`（离线环境也能装），最后校验 `yianc`、`anx`、`yian-lsp` 是否在
  `PATH` 上。
- `yian-lsp` 需要可选的 `pygls` 依赖；`yianc` 与 `anx` 不依赖它。

安装元数据和脚本的最低版本检查为 Python 3.11，但 `compiler/frontend/parse/stream.py` 等模块
使用 Python 3.12 的泛型函数语法；运行工具链需要 Python 3.12 或更高版本。

卸载：

```bash
scripts/uninstall.sh
```

只想装依赖、不装命令，也可以手工装 `pip install -r ./requirements.txt`。

## 编译 yian 代码

标准库不是隐式输入：用到 `std.*` 时要把源码根 `lib/src` 一并放进路径列表。

```bash
# Compile one file, including the standard library.
yianc lib/src tests/basic/array/assign.an

# Emit LLVM IR only.
yianc -t ll lib/src tests/basic/array/assign.an -o build/assign.ll

# Log compilation stages.
yianc --log-spec "main=DEBUG" lib/src tests/basic/array/assign.an
```

完整选项（`-t` / `-o` / `-O` / `--dump` / `--packages` …）见
[编译脚本文档](docs/compile_script.md)。

## 使用 anx 包管理器

`anx` 负责项目创建、包名导入解析，以及构建 / 运行 / 检查 / 测试 / 格式化：

```bash
anx new myapp               # Executable project; --kind lib/hybrid creates a library or library plus CLI.
cd myapp
anx run                     # Build build/app and execute it.
anx check                   # Type checking only.
anx test                    # Run project tests under tests/.
anx fmt --check             # Check root-package formatting; exit 1 on differences.
anx graph --json            # Structured dependency graph, file ownership, and diagnostics.
```

项目内使用包名导入（`from myapp.utils.math import add`）；依赖与 dev 依赖都在
`package.anx` 中声明：

```toml
[dependencies]
mathlib = { path = "vendor/mathlib" }

[dev-dependencies]
testkit = { path = "vendor/testkit" }   # Visible only to anx test.
```

完整用法见 [anx 用户指南](docs/anx/index.md)。依赖只支持本地路径：远程依赖、
版本约束与锁文件都不在支持范围内。

## 运行测试

三套件（basic / safety / package）由同一个 runner 驱动，它调用**已安装的** `yianc` / `anx`，
所以先跑一次安装：

```bash
scripts/install.sh

python3 scripts/run_tests.py                  # Basic suite (default).
python3 scripts/run_tests.py --suite package  # Package suite only.
python3 scripts/run_tests.py --all            # basic + safety + package
python3 scripts/run_tests.py -f call          # Only cases whose names match 'call'.
python3 scripts/run_tests.py -q               # Summary only.
```

## 文档

- [语言语法参考](docs/grammar/index.md) —— Yian 语言手册（语法、语义、标准库）
- [anx 用户指南](docs/anx/index.md) —— 项目、清单、依赖、命令行、项目测试、诊断
- [开发手册](docs/manual/index.md) —— 编译器各阶段（01–19）、anx 内部实现（20–25）、
  分析接口与语言服务器（26–27）、格式化器（28）、FFI（29）、自举前端（30）
- [快速上手](docs/grammar/00.quick_start.md) —— 可编译示例
- [设计概览](docs/proposal.md) —— 语言、工具链与内存安全边界
- [内存安全说明](docs/security.md) —— fat 模式检查与可信边界

`bootstrap/compiler` 是 YIAN 实现的词法、语法与去糖前端，提供 `--frontend-json` 观察接口，
不提供完整原生编译。包与测试入口见[自举编译器手册](docs/manual/30.bootstrap.md)。

## For GitHub users

YIAN is a statically-typed programming language with a compiler written in
Python. Pipeline: `.an source → Tokens → AST → desugared AST → HIR → CFG IR → LLVM IR → exe`.

### Environment

- Linux (like Ubuntu)
- Python 3.12+ (the compiler uses generic function syntax; `anx` reads manifests with `tomllib`)
- LLVM 22 toolchain: llvmlite 0.49 (bundles LLVM 22), clang 22, and the LLVM
  tools (`opt`, `llvm-dis`, `llvm-link`, `llvm-config`)

### Install

```bash
scripts/install.sh              # editable install of `yianc`, `anx` and `yian-lsp`
scripts/install.sh --with-deps  # let pip resolve llvmlite
scripts/uninstall.sh            # remove the `yian` distribution
```

`--regular` copies the packages instead of linking them; it does not ship
`lib/`, so point the tools at a checkout with `YIAN_LIB=/path/to/yian/lib/src`
(or `YIAN_ROOT=/path/to/yian`). Installing only the dependency by hand works
too: `pip install -r ./requirements.txt`.

Package metadata and the install script accept Python 3.11, but compiler source
uses Python 3.12 generic function syntax; run the toolchain with Python 3.12+.

### Compiling an example file

The standard library is not implicit — pass its source root (`lib/src`) as an
input when the program uses `std.*`:

```bash
yianc lib/src tests/basic/array/access.an
```

See [the compiler CLI guide](docs/compile_script.md) for full usage, and the
[language reference](docs/grammar/index.md) / [anx guide](docs/anx/index.md) /
[developer manual](docs/manual/index.md) for documentation.
