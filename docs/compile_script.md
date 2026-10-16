# Yian 编译器命令行手册

本文档说明日常使用的原生 `yianc` 命令行。Python 恢复编译器通过
`PYTHONPATH=python python -m yianc.main` 显式调用，只维护冷启动所需兼容性，不承诺与 native 完整功能对等。
安装后的 `yianc` 是由 `bootstrap/yianc/` 构建的原生程序，也是日常编译、分析和格式化的权威入口。

## 1. 基本用法

```bash
yianc [options] <paths...>
```

- `paths`：一个或多个 `.an` 源文件路径或包含源文件的目录。编译器会递归查找 `.an` 文件。
- 如果用到了标准库，需要把源码根 `lib/src` 也放进路径列表。
- 生成可执行文件需要唯一程序入口；分析模式和库包检查可不要求入口。

```bash
# Compile one test file, including the standard library.
yianc lib/src tests/basic/array/assign.an

# Compile multiple files.
yianc lib/src tests/basic/type/alias/imported_scope/types.an tests/basic/type/alias/imported_scope/main.an
```

### 1.1 工具链要求

Python 恢复编译器源码使用 Python 3.12 的泛型函数语法，冷启动因此需要 Python 3.12+。Python
安装元数据和冷启动脚本要求 Python 3.12+；LLVM 工具链安装脚本是独立的 shell 脚本，不依赖 Python。日常 native 编译器不通过 Python CLI 启动。

编译器依赖 **LLVM 22**：`llvmlite 0.49`（自带 LLVM 22）、`clang` 22，以及 LLVM 工具（`opt`、`llvm-dis`、`llvm-link`、`llvm-config`）。

```bash
python -m pip install -r python/requirements.txt      # Install the Python recovery dependency.
scripts/setup_llvm_toolchain.sh                    # Install missing LLVM 22 and native C build packages; requires sudo.
scripts/setup_llvm_toolchain.sh --check            # Report current versions without changes.
```

`python/requirements.txt` 固定 Python 恢复编译器使用的 llvmlite 版本。LLVM 工具链安装脚本本身不依赖 Python。默认运行 `setup_llvm_toolchain.sh` 会用 sudo 从系统仓库安装缺少的 C 构建工具，并从官方 `apt.llvm.org` 安装缺少的 LLVM 工具，然后切换未版本化命令到 LLVM 22；已安装的软件包保持当前版本。脚本不安装 Python 包。`--check` 只报告各 LLVM 工具的实际版本。

链接阶段使用的 C 编译器按 `$YIAN_CC` → `clang` → `cc` 选择，不隐式回落到带版本号的 `clang-18`/`clang-20`：

```bash
export YIAN_CC=/usr/lib/llvm-22/bin/clang
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
| `none` | 完成语义分析与 CFG 下降，不发射 LLVM 或原生目标 |
| `ll`   | LLVM IR 文本 (.ll)       |
| `bc`   | LLVM bitcode (.bc)       |
| `obj`  | 目标文件 (.o)            |
| `asm`  | 汇编文件 (.s)            |

```bash
yianc -t ll lib/src tests/basic/array/assign.an
```

### 2.3 `-o` / `--output` — 输出文件路径

除汇编外，产物默认输出到 `build/` 目录。用 `-o` 覆盖：

```bash
yianc -o my_prog lib/src tests/basic/array/assign.an
```

默认路径：`build/a.out`（exe）、`build/<name>.ll`（ll）、`build/<name>.bc`（bc）、
`build/<name>.o`（obj）；汇编为当前目录下的 `<name>.s`（asm）。`<name>` 是第一个输入文件的
词干。

### 2.4 `-O` — 优化等级

```bash
yianc -O2 lib/src tests/basic/array/assign.an
```

可选值：`0`, `1`, `2`, `3`。默认 0。`-O` 同时作用于三个位置：

1. **LLVM IR 级优化 pass**：对非 `-t ll` 目标（exe/bc/obj/asm），发射前按该等级运行 LLVM 优化 pipeline（`-O0` 不运行 IR pass）；
2. **后端 target machine**：`create_target_machine(opt=N)`，`-O0` → 0；
3. **链接 C 编译器**：按 `$YIAN_CC` → `clang` → `cc` 选择链接器，并加 `-O<N>`。

`-t ll` 输出始终为**未优化**的 IR（零优化保留，供调试）；`-t bc` 导出的是序列化后的 IR，
按同一个 `-O` 档位处理。

### 2.5 `--profile` — 打印各阶段耗时

```bash
yianc --profile lib/src tests/basic/array/assign.an
```

链接可执行文件时打印实际使用的链接器及其版本；末尾打印 llvmlite 绑定的 LLVM 版本。

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
      "dependencies": ["mathlib"],
      "ffi": false
    },
    "mathlib": {
      "sourceRoot": "/path/to/lib/src",
      "kind": "lib",
      "dependencies": [],
      "ffi": false
    },
    "std": {
      "sourceRoot": "/path/to/yian/lib/src",
      "kind": "lib",
      "dependencies": [],
      "ffi": false
    }
  }
}
```

- `format`：契约版本，只接受 `2`；其它取值直接报错，不回退猜测。
- `root`：本次构建的根包规范名，程序入口取 `packages[root].entry`；省略时退回 Standalone 的入口查找（非标准库文件中唯一的 `main`）。
- `packages`：包规范名 → 该包的全部信息。`sourceRoot` 是源码根；`kind` 取 `"bin"`/`"lib"`/`"hybrid"`；`entry` 只有 `"bin"`/`"hybrid"` 才有；`dependencies` 是**直接**依赖的规范名列表。`std` 必须存在。
- 每个包的 `ffi` 是布尔值，只对该包源码生效；未写时按 `false` 处理。有 `[native.llvm]` 的包另带 `nativeLLVM`，包含 LLVM 主版本 `22`、组件数组和相对包根的 C shim 路径数组。编译器会重新校验这些字段。
- 包 `P` 的可见集合 = `{P} ∪ packages[P].dependencies ∪ {"std"}`；导入第一段不在集合内报「未声明依赖」，完全不在 `packages` 中报「未知包」。
- 导入方所属的包由「文件落在哪个 `sourceRoot` 之下」确定，命中多个时取最长前缀。
- 导入路径必须解析到一个存在的 `.an` 文件（`from pkg import x` 中的 `pkg` 是目录，会报错），且任何包的 `entry` 都不可被导入。

不传 `--packages` 时为 Standalone 模式：`std` 查标准库查找表，其余各段按导入方文件目录相对解析，导入的文件必须已经出现在本次命令行里。标准库根由 `--compiler-root` / `YIAN_LIB` / `YIAN_ROOT` 配置，包模式下取 v2 的 `std.sourceRoot`；它不从输入路径推断，因此不会因为路径里出现 `lib` 就获得标准库权限。受限内建只在标准库根与仓库自身的低层测试夹具根（`tests/basic/std`、`tests/safety/std`）中可用。

通常不直接使用，由 `anx build` / `anx check` 自动生成并传入。

### 2.7 `--dump` — 写出中间产物

```bash
yianc --dump lib/src tests/basic/array/assign.an
```

把 `tokens.txt`、`ast.txt`、`hir.txt`、`cfg.txt` 写到 `build/`；运行代码生成时另写 `ir.ll`。
默认关闭。

### 2.8 `--compiler-root` — 指定检出根

```bash
yianc --compiler-root /path/to/yian lib/src main.an -t none
```

标准库取 `<PATH>/lib/src`。查找顺序是 `--compiler-root` → `YIAN_LIB`（标准库源码根本身）
→ `YIAN_ROOT`（检出根）→ 本模块所在检出；非 editable 安装不携带 `lib/`，必须显式配置。

### 2.9 `--raw-pointers` — 裸指针表示

```bash
yianc --raw-pointers lib/src main.an
```

用裸 8 字节指针替换胖指针表示，不发射 fat 指针检查、锁槽与帧锁；固定数组边界检查仍可发射。这是诊断模式，不提供内存安全保证。
`anx build --raw-pointers` 把该开关透传给编译器。

### 2.10 `--analyze` / `--json` — 只做分析

```bash
yianc --analyze lib/src main.an
yianc --analyze --json lib/src main.an
```

运行完整语义分析：词法与语法分析、名称与类型解析、类型检查、编译期分支选择、模式覆盖与 definite-assignment
检查；不运行 CFG/LLVM 代码生成，也不写 `build/`。
`--json` 把诊断作为单个 JSON 对象写到 stdout（其余输出走 stderr）；退出码 `0` 表示成功，
`1` 表示有错误；只有警告时仍返回 `0`。`--json` 必须与 `--analyze` 一起使用。

### 2.11 `--format` / `-w` / `--check` — 格式化源码

```bash
yianc --format main.an          # One file: print formatted source to stdout.
yianc --format -w src           # Multiple files: rewrite in place.
yianc --format --check src      # Report only files needing formatting.
```

只做词法与语法分析，再按规范化空白、换行与注释位置重新发射。无法格式化的文件被跳过并在
stderr 打印 `skip <path>: cannot be formatted`。`--check` 列出需要格式化的文件，有差异时
退出 `1`。`anx fmt` 是同一格式化器在项目上的入口。

### 2.12 `--allow-ffi` / `--link-lib` / `--link-search` — FFI 与原生链接

```bash
yianc --allow-ffi --link-lib m --link-search /path/to/libs lib/src main.an
```

`--allow-ffi` 只授予独立编译源码 FFI 权限；包模式的权限由各包的 `ffi` 字段决定。`--link-lib NAME` 和 `--link-search DIR` 可重复，分别向生成可执行文件时的链接命令加入 `-lNAME` 和 `-LDIR`。它们不会自动执行在 `-t ll`、`-t bc` 或 `-t asm` 输出上；`-t obj` 保留待最终链接的外部符号。编译器会拒绝非法库名。

声明 `[native.llvm]` 的包使用 LLVM 22 的 `llvm-config` 确定组件及系统库参数，要求 Linux x86-64 LP64 环境和 x86-64 Linux C 链接器。`-t exe` 编译并链接包内 C shim；`-t obj` 将 shim 和 YIAN 运行时合入可重定位目标文件，但仍需由最终链接器解析 LLVM 符号。语言层的 C ABI 限制与封送操作见 [FFI 参考](grammar/17.ffi.md)。

### 2.13 `--frontend-json` — 前端观察

```bash
yianc --frontend-json tokens tests/basic/array/access.an
yianc --frontend-json ast tests/basic/array/access.an
yianc --frontend-json desugared tests/basic/array/access.an
```

在 stdout 输出一个前端 JSON 对象，停在指定阶段，不进行名称与类型解析或代码生成，
不写构建产物。前端观察无需标准库输入；它与 `--analyze --json` 的语义诊断出口不同。
YIAN 实现的同名入口见[自举编译器](manual/30.bootstrap.md)。

## 3. 日志与调试输出

Yian 编译器内置结构化日志系统；日志开关是 `--log-spec` / `--log-file`，中间产物的完整转储
由 `--dump` 写出。`--token`、`--ast`、`--hir`、`--cfg`、`--emit-llvm` 不是命令行选项。

### 3.1 `--log-spec` — 配置日志级别

格式：`channel=level`，逗号分隔。支持前缀匹配（如 `type_check=DEBUG` 同时匹配 `type_check.coerce`）。

```bash
# DEBUG summaries for all stages.
yianc --log-spec "all=DEBUG" lib/src tests/basic/array/assign.an

# Trace only type-checking expression evaluation.
yianc --log-spec "type_check.expr=TRACE" lib/src tests/basic/array/assign.an

# Trace method dispatch and CFG logical lowering.
yianc --log-spec "call_dispatch=TRACE,cfg.logical=TRACE" lib/src tests/basic/array/assign.an
```

### 3.2 日志级别

| 级别    | 说明                     |
| ------- | ------------------------ |
| `OFF`   | 关闭                     |
| `ERROR` | 仅致命错误               |
| `WARN`  | 警告（**默认**）         |
| `INFO`  | 关键阶段摘要             |
| `DEBUG` | 详细过程 + 中间产物 dump |
| `TRACE` | 每条指令/每次函数调用    |

### 3.3 `--log-file` — 覆盖默认日志路径

日志始终写到 stderr；文件名默认 `build/compile.log`，用 `--log-file` 覆盖：

```bash
yianc --log-spec "all=DEBUG" --log-file /tmp/debug.log lib/src tests/basic/array/assign.an
```

### 3.4 环境变量

```bash
# Equivalent to --log-spec; --log-spec takes precedence.
YIAN_LOG="all=DEBUG,type_check=TRACE" yianc lib/src tests/basic/array/assign.an

# Write an additional log to PATH.
YIAN_LOG_FILE=/tmp/debug.log yianc lib/src tests/basic/array/assign.an

# Disable logging entirely.
YIAN_LOG="all=OFF" yianc lib/src tests/basic/array/assign.an
```

### 3.5 日志频道列表

| 频道                | 说明                                    |
| ------------------- | --------------------------------------- |
| `main`              | 编译管线主流程                          |
| `lex`               | 词法分析                                |
| `parse`             | 语法分析                                |
| `desugar`           | 脱糖                                    |
| `type_check`        | 类型检查                                |
| `type_check.expr`   | 表达式求值（TRACE 级输出每个 AST 节点） |
| `type_check.coerce` | 隐式类型转换                            |
| `call_dispatch`     | 方法/函数调度                           |
| `cfg.logical`       | 控制流图：短路逻辑展开                  |
| `cfg.block`         | 控制流图：基本块构造                    |
| `llvm`              | LLVM IR 生成                            |

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

### 4.4 语义分析与 CFG 检查（不发射 LLVM）

```bash
yianc -t none lib/src tests/basic/array/assign.an
```

### 4.5 导出 LLVM IR

```bash
yianc -t ll -o output.ll lib/src tests/basic/array/assign.an
```

## 5. 运行时库与链接

`-t exe` 与 `-t obj` 的产物需要运行时库（`runtime/`）：进程级对象（参数、锁槽、键计数器、帧锁影子栈）、失败路径、C 入口包装 `main`，以及堆分配器。

```text
bootstrap/yianc/src/codegen/abi/runtime.an 共享帧锁/块头常量与运行时消息
runtime/include/yian_rt.h        C ABI 声明与唯一尺寸类表
runtime/src/*.c                  C 源码
runtime/selftest/selftest.c      自测
runtime/build.py                 构建、native/C ABI 一致性断言、自测
build/runtime/libyian_rt.{o,a}   产物（首次使用时构建并缓存）
```

| 目标 | 处理 |
| --- | --- |
| `exe` | `clang <user>.o build/runtime/libyian_rt.a -o <out>` |
| `obj` | `clang -r -nostdlib <user>.o libyian_rt.o -o <out>`，产物单文件自包含 |
| `ll` / `bc` / `asm` | 运行时不并入：运行时符号是外部声明/未定义符号，需要自行链接运行时库 |

自行链接 `-t ll`/`bc`/`asm` 的产物时，需要满足下表这些符号（都在 `yian_rt.h` 里声明）：

| 类别 | 符号 |
| --- | --- |
| 进程参数 | `__yian_argc`、`__yian_argv`（由包装 `main` 校验并写入） |
| 锁表与键 | `__yian_key_stack`、`__secl_lock_table[]`、`__secl_frame_lock_depth`、`__secl_lock_bump`、`__secl_lock_free_head`、`__secl_lock_bump_take`、`__secl_lock_release` |
| 入口与失败路径 | 包装 `main`、`__yian_runtime_fail`、`__yian_panic` |
| 堆 | `__secl_pool_alloc`、`__secl_pool_alloc_class`、`__secl_pool_release` |

堆分配器是单线程尺寸类 arena：64 KiB、64 KiB 对齐的 slab，每个尺寸类一条空闲块链加一个正在填充的 slab；大于最大类的请求走独占尺寸块，并按额度缓存、按 `madvise` 归还物理页。用户程序不直接调用它：尺寸在编译期已知的分配点由编译器直接传尺寸类号（`YIAN_CLASS_BYTES` 的下标），否则传字节数。

尺寸类表是 C 分配器的唯一来源。`runtime/build.py --check-consistency-only` 将 native `runtime.an` 中的帧锁槽数、槽宽、块头字节数和 S002/R002/R003 消息与 C 头文件核对；Clang 静态断言检查整数表达式、尺寸类数量、正数和递增顺序。该入口不构建运行时库或运行自测。`--check` 仍会构建运行时库、执行 ABI 核对并运行普通自测，`--asan` 保留 ASan/UBSan 流程。

手动构建与校验：

```bash
python3 runtime/build.py            # Build unless already current.
python3 runtime/build.py --check-consistency-only  # Check native/C ABI only.
python3 runtime/build.py --check    # ABI consistency checks and self-tests.
python3 runtime/build.py --asan     # ASan/UBSan self-tests.
```

## 6. 日志输出位置

- **默认**：写入 `build/compile.log`，同时输出到 stderr
- stderr 输出可以用 `2>/dev/null` 丢弃
- `--log-file PATH`：覆盖默认路径
- `YIAN_LOG_FILE=PATH`：环境变量方式指定额外文件
- 日志不影响 stdout，编译产物（如 `-t exe`）正常输出

## 7. 相关文件

| 文件                    | 说明             |
| ----------------------- | ---------------- |
| `python/yianc/main.py`      | 冷启动恢复编译器入口 |
| `python/yianc/utils/log.py` | 日志系统实现     |
| `python/yianc/format/`      | 恢复格式化器副本 |
| `runtime/`              | 运行时库（C）    |
