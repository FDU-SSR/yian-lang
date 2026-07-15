# Yian 编译器命令行手册

本文档说明如何通过命令行直接调用 `compiler/main.py` 编译 Yian 源码。

## 1. 基本用法

```bash
python3 -m compiler.main [options] <paths...>
```

- `paths`：一个或多个 `.an` 源文件路径或包含源文件的目录。编译器会递归查找 `.an` 文件。
- 如果用到了标准库，需要把 `lib` 目录也放进路径列表。
- 源码中必须包含一个 `main` 函数作为程序入口。

```bash
# 编译单个测试文件（含标准库）
python3 -m compiler.main lib tests/array/assign.an

# 编译多个文件
python3 -m compiler.main lib tests/std/core/option.an tests/std/core/result.an
```

## 2. 命令行参数

### 2.1 输入路径

```bash
python3 -m compiler.main <path> [path...]
```

位置参数，至少一个。可以是 `.an` 文件或目录。

### 2.2 `-t` / `--target` — 输出目标类型

| 值     | 说明                     |
| ------ | ------------------------ |
| `exe`  | 可执行文件（默认）       |
| `none` | 仅做语义分析，不生成代码 |
| `ll`   | LLVM IR 文本 (.ll)       |
| `bc`   | LLVM bitcode (.bc)       |
| `obj`  | 目标文件 (.o)            |
| `asm`  | 汇编文件 (.s)            |

```bash
python3 -m compiler.main -t ll lib tests/array/assign.an
```

### 2.3 `-o` / `--output` — 输出文件路径

所有产物默认输出到 `build/` 目录。用 `-o` 覆盖：

```bash
python3 -m compiler.main -o my_prog lib tests/array/assign.an
```

默认路径：`build/a.out`（exe）、`build/<name>.ll`（ll）等。

### 2.4 `-O` — 优化等级

```bash
python3 -m compiler.main -O2 lib tests/array/assign.an
```

可选值：`0`, `1`, `2`, `3`。传递给 clang 的优化等级，默认 0。

### 2.5 `--profile` — 打印各阶段耗时

```bash
python3 -m compiler.main --profile lib tests/array/assign.an
```

### 2.6 `--packages` — 包名导入解析

```bash
python3 -m compiler.main --packages pkg.json files...
```

接受一个 JSON 文件，包含包名→源文件根目录的映射：

```json
{
  "myapp": "/path/to/myapp/src",
  "stdlib": "/path/to/lib"
}
```

启用 Package 模式：import 的第一段被作为包名解析。不传 `--packages` 时为 Standalone 模式，使用相对路径导入。

通常不直接使用，由 `anx build` 自动生成并传入。

## 3. 日志与调试输出

Yian 编译器内置了结构化日志系统，替代了旧的 `--token`、`--ast`、`--hir`、`--cfg`、`--emit-llvm` 参数。

### 3.1 `--log-spec` — 配置日志级别

格式：`channel=level`，逗号分隔。支持前缀匹配（如 `type_check=DEBUG` 同时匹配 `type_check.coerce`）。

```bash
# 显示所有阶段的 DEBUG 摘要
python3 -m compiler.main --log-spec "all=DEBUG" lib tests/array/assign.an

# 仅追踪类型检查中的表达式求值
python3 -m compiler.main --log-spec "type_check.expr=TRACE" lib tests/array/assign.an

# 追踪方法调度 + CFG 逻辑展开
python3 -m compiler.main --log-spec "call_dispatch=TRACE,cfg.logical=TRACE" lib tests/array/assign.an
```

### 3.2 日志级别

| 级别    | 说明                     |
| ------- | ------------------------ |
| `OFF`   | 关闭                     |
| `ERROR` | 仅致命错误               |
| `WARN`  | 警告                     |
| `INFO`  | 关键阶段摘要（**默认**） |
| `DEBUG` | 详细过程 + 中间产物 dump |
| `TRACE` | 每条指令/每次函数调用    |

### 3.3 `--log-file` — 覆盖默认日志路径

默认日志写入 `build/compile.log`。用 `--log-file` 覆盖：

```bash
python3 -m compiler.main --log-spec "all=DEBUG" --log-file /tmp/debug.log lib tests/array/assign.an
```

### 3.4 环境变量

```bash
# 等价于 --log-spec
YIAN_LOG="all=DEBUG,type_check=TRACE" python3 -m compiler.main lib tests/array/assign.an

# 等价于 --log-file
YIAN_LOG_FILE=/tmp/debug.log python3 -m compiler.main lib tests/array/assign.an

# 完全关闭日志
YIAN_LOG="all=OFF" python3 -m compiler.main lib tests/array/assign.an
```

### 3.5 日志频道列表

| 频道              | 说明                                    |
| ----------------- | --------------------------------------- |
| `main`            | 编译管线主流程                          |
| `type_check`      | 类型检查                                |
| `type_check.expr` | 表达式求值（TRACE 级输出每个 AST 节点） |
| `call_dispatch`   | 方法/函数调度                           |
| `cfg`             | 控制流图生成                            |
| `cfg.logical`     | 短路逻辑展开                            |

## 4. 常见工作流

### 4.1 快速编译运行

```bash
python3 -m compiler.main lib tests/array/assign.an && ./build/a.out
```

### 4.2 调试类型错误

```bash
python3 -m compiler.main --log-spec "type_check=DEBUG,type_check.expr=TRACE" lib tests/my_test.an
```

### 4.3 查看各阶段摘要

```bash
python3 -m compiler.main --log-spec "main=DEBUG" lib tests/array/assign.an
```

输出示例：

```text
[ INFO][main        ]compiling 2 source file(s)
[DEBUG][main        ]lexed 17793 tokens from 34 file(s)
[DEBUG][main        ]parsed 356 top-level items
[DEBUG][main        ]desugaring complete
[DEBUG][main        ]global resolve complete — 34 units
[DEBUG][main        ]type-checked 5 definitions
[DEBUG][main        ]generated 5 CFG functions
```

### 4.4 仅做语义检查（不生成代码）

```bash
python3 -m compiler.main -t none lib tests/array/assign.an
```

### 4.5 导出 LLVM IR

```bash
python3 -m compiler.main -t ll -o output.ll lib tests/array/assign.an
```

## 5. 日志输出位置

- **默认**：写入 `build/compile.log`，同时输出到 stderr
- stderr 输出可以用 `2>/dev/null` 丢弃
- `--log-file PATH`：覆盖默认路径
- `YIAN_LOG_FILE=PATH`：环境变量方式指定额外文件
- 日志不影响 stdout，编译产物（如 `-t exe`）正常输出

## 6. 相关文件

| 文件                    | 说明             |
| ----------------------- | ---------------- |
| `compiler/main.py`      | 编译器入口       |
| `compiler/utils/log.py` | 日志系统实现     |
| `bak/quest_log.md`      | 日志系统设计文档 |
