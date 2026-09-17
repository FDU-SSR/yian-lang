# Yian 编译器命令行手册

本文档说明如何通过命令行直接调用 `compiler/main.py` 编译 Yian 源码。

## 1. 基本用法

```bash
yianc [options] <paths...>
```

- `paths`：一个或多个 `.an` 源文件路径或包含源文件的目录。编译器会递归查找 `.an` 文件。
- 如果用到了标准库，需要把 `lib` 目录也放进路径列表。
- 源码中必须包含一个 `main` 函数作为程序入口。

```bash
# 编译单个测试文件（含标准库）
yianc lib/src tests/basic/array/assign.an

# 编译多个文件
yianc lib/src tests/basic/std/core/option.an tests/basic/std/core/result.an
```

## 2. 命令行参数

### 2.1 输入路径

```bash
yianc <path> [path...]
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
yianc -t ll lib/src tests/basic/array/assign.an
```

### 2.3 `-o` / `--output` — 输出文件路径

所有产物默认输出到 `build/` 目录。用 `-o` 覆盖：

```bash
yianc -o my_prog lib/src tests/basic/array/assign.an
```

默认路径：`build/a.out`（exe）、`build/<name>.ll`（ll）等。

### 2.4 `-O` — 优化等级

```bash
yianc -O2 lib/src tests/basic/array/assign.an
```

可选值：`0`, `1`, `2`, `3`。默认 0。`-O` 同时作用于三个位置：

1. **LLVM IR 级优化 pass**：对非 `-t ll` 目标（exe/bc/obj/asm），发射前按该等级运行 LLVM 优化 pipeline（`-O0` 不运行 IR pass）；
2. **后端 target machine**：`create_target_machine(opt=N)`，`-O0` → 0；
3. **链接 clang**：`clang -O<N>`。

`-t ll` 输出始终为**未优化**的 IR（零优化保留，供调试）。注意 `-t bc` 导出的 bitcode 是优化后的 IR（非源码级）。

### 2.5 `--profile` — 打印各阶段耗时

```bash
yianc --profile lib/src tests/basic/array/assign.an
```

### 2.6 `--packages` — 包名导入解析

```bash
yianc --packages pkg.json files...
```

接受一个 **v2** 契约的 JSON 文件（由 `anx build` 生成）：

```json
{
  "format": 2,
  "root": "app",
  "packages": {
    "app": {
      "sourceRoot": "/path/to/app/src",
      "kind": "bin",
      "entry": "/path/to/app/src/main.an",
      "dependencies": ["mathlib"]
    },
    "mathlib": {
      "sourceRoot": "/path/to/lib/src",
      "kind": "lib",
      "dependencies": []
    },
    "std": {
      "sourceRoot": "/path/to/yian/lib/src",
      "kind": "lib",
      "dependencies": []
    }
  }
}
```

- `format`：契约版本，当前只接受 `2`；其它取值直接报错，不回退猜测。旧的扁平映射（v1）不再被接受。
- `root`：本次构建的根包规范名，程序入口取 `packages[root].entry`；省略时退回 Standalone 的入口查找（非标准库文件中唯一的 `main`）。
- `packages`：包规范名 → 该包的全部信息。`sourceRoot` 是源码根；`kind` 取 `"bin"`/`"lib"`/`"hybrid"`；`entry` 只有 `"bin"`/`"hybrid"` 才有；`dependencies` 是**直接**依赖的规范名列表。`std` 必须存在。
- 包 `P` 的可见集合 = `{P} ∪ packages[P].dependencies ∪ {"std"}`；导入第一段不在集合内报「未声明依赖」，完全不在 `packages` 中报「未知包」。
- 导入方所属的包由「文件落在哪个 `sourceRoot` 之下」确定，命中多个时取最长前缀。
- 导入路径必须解析到一个存在的 `.an` 文件（`from pkg import x` 中的 `pkg` 是目录，会报错），且任何包的 `entry` 都不可被导入。

不传 `--packages` 时为 Standalone 模式：`std` 查标准库查找表，其余各段按导入方文件目录相对解析，导入的文件必须已经出现在本次命令行里。安全边界（prelude、受限操作）也由 v2 的 `std.sourceRoot` 判定，因此不会因为路径里出现 `lib` 就获得标准库权限。

通常不直接使用，由 `anx build` / `anx check` 自动生成并传入。

## 3. 日志与调试输出

Yian 编译器内置了结构化日志系统，替代了旧的 `--token`、`--ast`、`--hir`、`--cfg`、`--emit-llvm` 参数。

### 3.1 `--log-spec` — 配置日志级别

格式：`channel=level`，逗号分隔。支持前缀匹配（如 `type_check=DEBUG` 同时匹配 `type_check.coerce`）。

```bash
# 显示所有阶段的 DEBUG 摘要
yianc --log-spec "all=DEBUG" lib/src tests/basic/array/assign.an

# 仅追踪类型检查中的表达式求值
yianc --log-spec "type_check.expr=TRACE" lib/src tests/basic/array/assign.an

# 追踪方法调度 + CFG 逻辑展开
yianc --log-spec "call_dispatch=TRACE,cfg.logical=TRACE" lib/src tests/basic/array/assign.an
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
yianc --log-spec "all=DEBUG" --log-file /tmp/debug.log lib/src tests/basic/array/assign.an
```

### 3.4 环境变量

```bash
# 等价于 --log-spec
YIAN_LOG="all=DEBUG,type_check=TRACE" yianc lib/src tests/basic/array/assign.an

# 等价于 --log-file
YIAN_LOG_FILE=/tmp/debug.log yianc lib/src tests/basic/array/assign.an

# 完全关闭日志
YIAN_LOG="all=OFF" yianc lib/src tests/basic/array/assign.an
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
yianc lib/src tests/basic/array/assign.an && ./build/a.out
```

### 4.2 调试类型错误

```bash
yianc --log-spec "type_check=DEBUG,type_check.expr=TRACE" lib/src tests/basic/array/assign.an
```

### 4.3 查看各阶段摘要

```bash
yianc --log-spec "main=DEBUG" lib/src tests/basic/array/assign.an
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
yianc -t none lib/src tests/basic/array/assign.an
```

### 4.5 导出 LLVM IR

```bash
yianc -t ll -o output.ll lib/src tests/basic/array/assign.an
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
