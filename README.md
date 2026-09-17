# YIAN 编译器

YIAN 是一门自研的静态类型编程语言，编译器用 Python 实现。
完整流水线：`.an 源码 → Tokens → AST → HIR → CFG IR → LLVM IR → 可执行文件`。

## 运行环境

- Linux（如 Ubuntu）
- Python 3.11+（`yianc` 本身在 3.10 上也能跑，但 `anx` 用 `tomllib` 读清单，安装脚本要求 ≥ 3.11）
- clang

## 安装与卸载

一键安装，把 `yianc`（编译器）与 `anx`（包管理器）装进当前 Python 环境：

```bash
scripts/install.sh                    # 默认：editable 安装，两个命令指向本检出
scripts/install.sh --with-deps        # 让 pip 解析依赖（否则要求 llvmlite 已可导入）
scripts/install.sh --user             # 装进用户 site-packages
scripts/install.sh --python PATH      # 指定解释器
```

- **editable**（默认）：`yianc` / `anx` 直接指向这个检出，改 `compiler/`、`anx/`
  （含新增模块）后无需重装。开发时请用这种安装。
- **`--regular`**：拷贝安装。它**不包含 `lib/`**，所以要告诉工具标准库在哪：

  ```bash
  export YIAN_LIB=/path/to/yian/lib/src     # 标准库源码根
  # 或者
  export YIAN_ROOT=/path/to/yian            # 检出根
  ```

- 脚本会预检 Python ≥ 3.11 与 llvmlite，并在已安装 setuptools ≥ 68 时加上
  `--no-build-isolation`（离线环境也能装），最后校验两个入口是否在 `PATH` 上。

卸载：

```bash
scripts/uninstall.sh
```

只想装依赖、不装命令，也可以手工装 `pip install -r ./requirements.txt`。

## 编译 yian 代码

标准库不是隐式输入：用到 `std.*` 时要把源码根 `lib/src` 一并放进路径列表。

```bash
# 编译单个文件（含标准库）
yianc lib/src tests/basic/array/assign.an

# 仅输出 LLVM IR
yianc -t ll lib/src tests/basic/array/assign.an -o build/assign.ll

# 按阶段打印日志
yianc --log-spec "main=DEBUG" lib/src tests/basic/array/assign.an
```

完整选项（`-t` / `-o` / `-O` / `--dump` / `--packages` …）见
[编译脚本文档](docs/compile_script.md)。

## 使用 anx 包管理器

`anx` 负责项目创建、包名导入解析，以及构建 / 运行 / 检查 / 测试：

```bash
anx new myapp               # 可执行项目；--kind lib / hybrid 建库 / 库+CLI
cd myapp
anx run                     # 构建到 build/app 并执行
anx check                   # 只做类型检查
anx test                    # 跑 tests/ 下的项目测试
anx graph --json            # 依赖图、文件归属与诊断（结构化输出）
```

项目内使用包名导入（`from myapp.utils.math import add`）；依赖与 dev 依赖都在
`package.anx` 中声明：

```toml
[dependencies]
mathlib = { path = "vendor/mathlib" }

[dev-dependencies]
testkit = { path = "vendor/testkit" }   # 只有 `anx test` 看得见
```

完整用法见 [anx 用户指南](docs/anx/index.md)。依赖目前只支持本地路径：远程依赖、
版本约束与锁文件都未纳入开发范围。

## 运行测试

三套件（basic / safety / package）由同一个 runner 驱动，它调用**已安装的** `yianc` / `anx`，
所以先跑一次安装：

```bash
scripts/install.sh

python3 scripts/run_tests.py                  # basic 套件（默认）
python3 scripts/run_tests.py --suite package  # 只跑 package 套件
python3 scripts/run_tests.py --all            # basic + safety + package
python3 scripts/run_tests.py -f call          # 只跑名字匹配 "call" 的用例
python3 scripts/run_tests.py -q               # 只打印汇总
```

## 文档

- [语言语法参考](docs/grammar/index.md) —— Yian 语言手册（语法、语义、标准库）
- [anx 用户指南](docs/anx/index.md) —— 项目、清单、依赖、命令行、项目测试、诊断
- [开发手册](docs/manual/index.md) —— 编译器各阶段（01–19）与 anx 内部实现（20–25）
- [快速上手](docs/grammar/00.quick_start.md) —— 可编译示例

## For GitHub users

YIAN is a statically-typed programming language with a compiler written in
Python. Pipeline: `.an source → Tokens → AST → HIR → CFG IR → LLVM IR → exe`.

### Environment

- Linux (like Ubuntu)
- Python 3.11+ (`yianc` also runs on 3.10; `anx` needs `tomllib`)
- clang

### Install

```bash
scripts/install.sh              # editable install of `yianc` and `anx`
scripts/install.sh --with-deps  # let pip resolve llvmlite
scripts/uninstall.sh            # remove the `yian` distribution
```

`--regular` copies the packages instead of linking them; it does not ship
`lib/`, so point the tools at a checkout with `YIAN_LIB=/path/to/yian/lib/src`
(or `YIAN_ROOT=/path/to/yian`). Installing only the dependency by hand works
too: `pip install -r ./requirements.txt`.

### Compiling an example file

The standard library is not implicit — pass its source root (`lib/src`) as an
input when the program uses `std.*`:

```bash
yianc lib/src tests/basic/array/access.an
```

See [the compiler CLI guide](docs/compile_script.md) for full usage, and the
[language reference](docs/grammar/index.md) / [anx guide](docs/anx/index.md) /
[developer manual](docs/manual/index.md) for documentation.
