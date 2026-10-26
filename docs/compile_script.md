# Yian 编译器命令行手册

本文档说明日常使用的原生 `yianc` 命令行。Python 恢复编译器通过
`PYTHONPATH=python python -m yianc.main` 显式调用，只维护冷启动所需兼容性，不承诺与 native 完整功能对等。
安装后的 `yianc` 是由 `yianc/` 构建的原生程序，是日常编译与分析的权威入口；格式化由独立的 `yian-fmt` 命令完成。

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
| `ll`   | LLVM IR 文本 (.ll)       |
| `bc`   | LLVM bitcode (.bc)       |

`--check` 是独立的检查模式，不通过 `-t` 选择。

```bash
yianc -t ll lib/src tests/basic/array/assign.an
```

### 2.3 `-o` / `--output` — 输出文件路径

产物默认输出到 `build/` 目录。用 `-o` 覆盖：

```bash
yianc -o my_prog lib/src tests/basic/array/assign.an
```

默认路径：`build/a.out`（exe）、`build/<name>.ll`（ll）、`build/<name>.bc`（bc）。`<name>` 是第一个输入文件的词干。

### 2.4 `-O` — 优化等级

```bash
yianc -O2 lib/src tests/basic/array/assign.an
```

可选值：`0`, `1`, `2`, `3`。默认 0。`-O` 同时作用于三个位置：

1. **LLVM IR 级优化 pass**：对非 `-t ll` 目标（exe/bc），发射前按该等级运行 LLVM 优化 pipeline（`-O0` 不运行 IR pass）；
2. **后端 target machine**：`create_target_machine(opt=N)`，`-O0` → 0；
3. **链接 C 编译器**：按 `$YIAN_CC` → `clang` → `cc` 选择链接器，并加 `-O<N>`。

`-t ll` 输出始终为**未优化**的 IR（零优化保留，供调试）；`-t bc` 导出的是序列化后的 IR，
按同一个 `-O` 档位处理。

### 2.5 `--profile` / `--profile-output PATH` — 编译耗时报告

```bash
yianc --profile-output build/profile/compiler.json lib/src tests/basic/array/assign.an
yianc --profile --profile-output build/profile/compiler.json lib/src tests/basic/array/assign.an
```

`--profile` 保留 stderr 上的 `[profile]` 文本汇总。native `yianc` 的 `--profile-output PATH` 会额外写结构化
JSON，并在同目录写一个扩展名为 `.txt` 的可读汇总。两者可同时使用；单独使用 `--profile-output` 不输出
终端 profile 文本。报告含目标、优化级别、指针模式、成功状态、每项的 `pass_id`、父项、类型、调用次数、
累计耗时、自耗时、失败次数、不完整标记、未运行原因和统计错误。耗时为包含子项的总耗时，`self_ns` 扣除
直接子项；重复执行的 pass 累计调用次数及耗时。
`work_finished_at_ns` 记录编译工作结束、profile 序列化开始前的 monotonic 时间；anx 用它关闭子进程等待区间，
因此 JSON/文本序列化和写盘时间不计入 `build_total_ns`。

计时覆盖输入收集/读取、词法/解析/desugar、语义解析与检查、地址存储归一化、HIR 验证/覆盖/赋值、CFG
lower/验证/清理/check insertion、LLVM lowering/优化前后验证/优化/native emission 或 IR/bitcode 序列化，
以及 runtime、C shim、链接参数、最终链接和产物发布。优化等级 `-O0` 不运行 LLVM 优化；`--check` 在语义分析后结束，未执行项标为 `not_run`，不会伪造零耗时成功。原生链接阶段仅在 `exe` 目标运行。

`--profile-output` 可用于编译模式与 `--check`；编译报告的目标为 `"exe"`、`"ll"` 或 `"bc"`，检查报告的目标为 `"check"`。报告写入失败只打印警告，不改变原编译结果。`anx
build profile` 用该接口保存每次 compiler 调用的独立明细并生成命令级报告；命令选项见
[anx CLI](anx/03.cli.md)。`anx run profile` 启动产物后的执行不计入编译计时；它使用下节的运行时插桩接口，
不读取编译报告。Python 恢复编译器的 `--profile` 属于其恢复 CLI 行为；它不支持 native
`--profile-output`。

### 2.5.1 `--runtime-profile-dir PATH` — 运行时插桩构建

```bash
yianc --runtime-profile-dir /abs/run/dir -O2 -o build/app lib/src main.an
```

该 native 选项只用于生成插桩可执行文件。`PATH` 必须是非空绝对路径，目标必须是 `exe`；与 `--check`、
`-t ll`/`-t bc` 一起使用会报配置错误。`anx run profile` 自动分配本次 run 目录并传入该选项，
普通构建不传。

编译为本次构建中的每个生成 callable 分配 `function_id`，输出只读的描述符表与
`__yian_profile_config`，并在函数真实入口和每个正常 `ret` 前调用 `__yian_profile_enter(uint64_t)` /
`__yian_profile_leave(uint64_t)`；extern、头文件声明和 ABI thunk 不单独插桩。描述符包含包、模块、
规范源码路径、从 1 开始的行列号（列按 Unicode scalar value 计数）、函数/方法/闭包类别、方法所属类型、
具体泛型实参和生成来源。插桩函数加 `noinline`、`nomerge` 与 `"disable-tail-calls"="true"` 字符串属性，
profiling session 将 `MergeFunctions` 设为 `false`；优化级别仍按 `-O`/`--release` 保留，不降级到 `-O0`。

配置结构含 ABI 版本、报告目录、run id、程序绝对路径、指针模式、优化级别、描述符数量和描述符指针。
所有字符串是 UTF-8 字节切片，带显式长度，不需要 NUL 结尾。报告目录由运行环境创建；collector 在
`main` 正常返回后写 `profile.json` 与 `summary.txt`。格式、计时口径和正常返回保证见
[anx CLI 的 `run profile`](anx/03.cli.md)。report 目录缺失、时钟或分配故障属于 profiling 故障边界，
不改变普通编译或既有安全错误协议；普通编译既不定义 collector，也不引用插桩符号。

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

编译模式把 `tokens.txt`、`ast.txt`、`hir.txt`、`cfg.txt` 写到 `build/`，运行代码生成时另写 `ir.ll`。`--check --dump` 只写 `tokens.txt`、`ast.txt` 和成功分析时的 `hir.txt`。默认关闭。

### 2.8 `--compiler-root` — 指定检出根

```bash
yianc --check --compiler-root /path/to/yian lib/src main.an
```

标准库取 `<PATH>/lib/src`。查找顺序是 `--compiler-root` → `YIAN_LIB`（标准库源码根本身）
→ `YIAN_ROOT`（检出根）→ 本模块所在检出；非 editable 安装不携带 `lib/`，必须显式配置。

### 2.9 `--raw-pointers` — 裸指针表示

```bash
yianc --raw-pointers lib/src main.an
```

用裸 8 字节指针替换胖指针表示，不发射 fat 指针检查、锁槽与帧锁；固定数组边界检查仍可发射。这是诊断模式，不提供内存安全保证。
`anx build --raw-pointers` 把该开关透传给编译器。

### 2.10 `--check` — 只做前端与中端检查

```bash
yianc --check lib/src main.an
```

运行前端与中端：词法、语法、desugar、名称与类型解析、类型检查、编译期分支选择、模式覆盖与
definite-assignment 检查；不进入 CFG/LLVM，不生成编译器产物。诊断以文本写到 stderr；退出码
`0` 表示成功，`1` 表示有错误；只有警告时仍返回 `0`。不要求 `main`，存在时校验其签名。
`--check` 接受 `--packages`、`--compiler-root`、`--raw-pointers`、`--allow-ffi`、`-O`、`--dump`、
`--profile`、`--profile-output` 和日志选项；显式 `-t`、`-o`、`--link-lib`、`--link-search` 会报配置错误。
`--runtime-profile-dir` 只用于插桩 `exe` 构建，与 `--check` 组合会报配置错误。
`--dump` 只写 `tokens.txt`、`ast.txt` 和成功分析时的 `hir.txt`。

### 2.11 格式化源码 — `yian-fmt`

native `yianc` 不包含格式化 action，`--format`、`--format-check`、`-w` 和 `--write` 都是未知选项。
格式化使用独立命令：

```bash
yian-fmt main.an                  # One file: print formatted source to stdout.
yian-fmt --write src              # Multiple files: rewrite in place.
yian-fmt --check src              # Report files needing formatting; exit 1 on differences.
yian-fmt --stdin --stdin-filepath label.an < main.an
```

`yian-fmt` 只做词法与语法分析，再按规范化空白、换行与注释位置重新发射。文件模式无法格式化的文件
打印 `skip <path>: cannot be formatted` 并继续处理；退出码 2 优先于 3，差异检查为 1。stdin 模式只
输出格式化文本。`anx fmt` 与语言服务器通过子进程调用同一命令。完整契约见
[格式化器](manual/28.formatter.md)。

### 2.12 `--allow-ffi` / `--link-lib` / `--link-search` — FFI 与原生链接

```bash
yianc --allow-ffi --link-lib m --link-search /path/to/libs lib/src main.an
```

`--allow-ffi` 只授予独立编译源码 FFI 权限；包模式的权限由各包的 `ffi` 字段决定。`--link-lib NAME` 和 `--link-search DIR` 可重复，分别向生成可执行文件时的链接命令加入 `-lNAME` 和 `-LDIR`。它们只在 `-t exe` 的最终链接步骤使用；`-t ll` 与 `-t bc` 保留外部符号。编译器会拒绝非法库名。

声明 `[native.llvm]` 的包使用 LLVM 22 的 `llvm-config` 确定组件及系统库参数，要求 Linux x86-64 LP64 环境和 x86-64 Linux C 链接器。`-t exe` 编译并链接包内 C shim、YIAN 运行时、LLVM 组件及系统库。语言层的 C ABI 限制与封送操作见 [FFI 参考](grammar/17.ffi.md)。

### 2.13 前端观察与 Python recovery CLI

native `yianc` 不提供前端 JSON 观察入口；需要查看前端产物时使用 `--dump`，它会写出
`tokens.txt`、`ast.txt`、`hir.txt` 等文件。Python recovery CLI 保留
`--frontend-json {tokens,ast,desugared}`、`--analyze` 和 `--json`，其行为只属于恢复路径，
见[自举编译器](manual/30.bootstrap.md)与 [Python CLI](manual/14.cli_build.md)。

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

### 4.4 前端与中端检查（不发射 LLVM）

```bash
yianc --check lib/src tests/basic/array/assign.an
```

### 4.5 导出 LLVM IR

```bash
yianc -t ll -o output.ll lib/src tests/basic/array/assign.an
```

## 5. 运行时库与链接

`-t exe` 的产物需要运行时库（`runtime/`）：进程级对象（参数、锁槽、键计数器、帧锁影子栈）、失败路径、C 入口包装 `main`，以及堆分配器。

```text
yianc/src/codegen/abi/runtime.an 共享帧锁/块头常量与运行时消息
runtime/include/yian_rt.h        C ABI 声明与唯一尺寸类表
runtime/src/*.c                  C 源码
runtime/selftest/selftest.c      自测
runtime/build.py                 构建、native/C ABI 一致性断言、自测
build/runtime/libyian_rt.{o,a}   产物（首次使用时构建并缓存）
```

| 目标 | 处理 |
| --- | --- |
| `exe` | `clang <user>.o build/runtime/libyian_rt.a -o <out>` |
| `ll` / `bc` | 运行时不并入：运行时符号是外部声明/未定义符号，需要自行链接运行时库 |

原生 `yianc` 对模块内普通生成函数（`yian.fn.<TypeId>`，含方法、泛型实例与闭包生成函数）使用
`internal` 链接性，对 `ll`、`bc`、`exe` 一致生效；入口 `__yian_main`、`extern "C"`
声明与运行时符号保持外部名称。因此 `-t ll`/`bc` 产物只留下运行时符号与 C 声明待链接，
未被引用的普通函数可被优化器和链接器删除。`pub` 是 YIAN 级可见性，不改变该分类。

自行链接 `-t ll`/`bc` 的产物时，需要满足下表这些符号（都在 `yian_rt.h` 里声明）：

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
