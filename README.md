# YIAN 编译器

YIAN 是一门自研的静态类型编程语言，编译器用 Python 实现。
完整流水线：`.an 源码 → Tokens → AST → HIR → CFG IR → LLVM IR → 可执行文件`。

## 运行环境

- Linux（如 Ubuntu）
- python3.10+
- clang

## 安装依赖库

```bash
pip install -r ./requirements.txt
```

## 编译 yian 代码

### 直接调用编译器

通过 `compiler.main` 模块调用编译器。若用到标准库，需把 `lib` 目录一并放入路径列表：

```bash
# 编译单个文件（含标准库）
python3 -m compiler.main lib tests/array/assign.an

# 仅输出 LLVM IR
python3 -m compiler.main -t ll lib tests/array/assign.an

# 按阶段打印日志
python3 -m compiler.main --log-spec "main=DEBUG" lib tests/array/assign.an
```

### 使用 anx 包管理器

`anx` 负责项目创建、包名导入解析，以及构建 / 运行 / 检查 / 测试。装好后直接用 `anx`，
不装也可以走 `python3 -m anx.main`：

```bash
scripts/install.sh          # 把 yianc 与 anx 装进 PATH（默认 editable 安装）

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

完整用法见 [anx 用户指南](docs/anx/index.md)；远程依赖与版本为什么不做，见 [远程依赖结论](docs/plan/anx-remote-deps.md)。

编译器的完整命令行用法见[编译脚本文档](docs/compile_script.md)。

## 运行测试

```bash
python3 scripts/run_tests.py            # 运行全部测试
python3 scripts/run_tests.py -f call    # 只运行名字匹配 "call" 的测试
```

## 文档

- [语言语法参考](docs/grammar/index.md) —— Yian 语言手册（语法、语义、标准库）
- [anx 用户指南](docs/anx/index.md) —— 项目、清单、依赖、命令行、项目测试、诊断
- [编译器开发手册](docs/manual/index.md) —— 编译器各阶段与内部实现
- [快速上手](docs/grammar/00.quick_start.md) —— 可编译示例

## For GitHub users

YIAN is a statically-typed programming language with a compiler written in
Python. Pipeline: `.an source → Tokens → AST → HIR → CFG IR → LLVM IR → exe`.

### Environment

- Linux (like Ubuntu)
- python3.10+
- clang

Install required packages:

```bash
pip install -r ./requirements.txt
```

### Compiling an example file

Invoke the compiler through the `compiler.main` module. Include the `lib`
directory when the program uses the standard library:

```bash
python3 -m compiler.main lib tests/array/access.an
```

See [the compiler CLI guide](docs/compile_script.md) for full usage, and the
[language reference](docs/grammar/index.md) / [anx guide](docs/anx/index.md) /
[developer manual](docs/manual/index.md) for documentation.
