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

通过 `compiler.main` 模块调用编译器。若用到标准库，需把 `lib` 目录一并放入路径列表：

```bash
# 编译单个文件（含标准库）
python3 -m compiler.main lib tests/array/assign.an

# 仅输出 LLVM IR
python3 -m compiler.main -t ll lib tests/array/assign.an

# 按阶段打印日志
python3 -m compiler.main --log-spec "main=DEBUG" lib tests/array/assign.an
```

编译器的完整命令行用法见[编译脚本文档](docs/compile_script.md)。

## 运行测试

```bash
python3 scripts/run_tests.py            # 运行全部测试
python3 scripts/run_tests.py -f call    # 只运行名字匹配 "call" 的测试
```

## 文档

- [语言语法参考](docs/grammar/index.md) —— 15 章 Yian 语言手册
- [编译器开发手册](docs/manual/index.md) —— 19 章编译器内部实现
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

See [the compiler CLI guide](docs/compile_script.md) for full usage, and
[the language reference](docs/grammar/index.md) / [developer manual](docs/manual/index.md)
for documentation.
