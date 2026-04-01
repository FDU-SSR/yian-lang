# yian_compiler.py 编译脚本说明

本文档详细说明 `scripts/yian_compiler.py` 的用途、参数、执行流程与常见问题，便于在命令行下稳定地完成 YIAN 工程编译。

## 1. 脚本定位

`scripts/yian_compiler.py` 是一个统一入口，负责串联两段流程：

1. 调用 `compiler/main.py` 生成 LLVM 后端产物。
2. 根据目标类型输出最终文件：
	- 目标为 ll：直接导出 LLVM IR 文本。
	- 目标为 exe：先生成目标文件，再调用 clang 链接成可执行文件。

脚本内部依赖 `scripts/compiler_api.py` 提供的编译管线实现。

## 2. 基本用法

```bash
scripts/yian_compiler.py [src_paths]
```

说明：

1. `src_paths`：若干个源代码文件路径或者包含源文件的目录路径，编译器会自动递归查找 .an 文件进行编译。
2. 源代码文件中必须包含且仅包含一个 `main` 函数作为程序入口。

## 3. 参数说明

通过命令行参数，用户可以控制编译过程的行为、输出类型与调试信息。

### 3.1 `-h` / `--help` 输出帮助信息

通过以下命令可以查看脚本的完整参数列表与说明：

```bash
scripts/yian_compiler.py -h
```

### 3.2 `-d` / `--debug` 开启调试输出

使用 `-d` 或 `--debug` 参数可以在编译过程中输出更多调试信息，帮助定位问题：

```bash
scripts/yian_compiler.py -d tests/control_flow/for.an
```

### 3.3 `-p` / `--display` 显示中间结果

YIAN 使用静态分析工具 LIAN 的部分功能作为编译器的前端, 使用 `-p` 或 `--display` 参数可以在编译完成后调用 LIAN 的脚本显示编译过程中生成的中间结果(`dataframe.html`)：

```bash
scripts/yian_compiler.py -p tests/array/init.an
```

### 3.4 `-O*` 优化等级参数

编译器支持传递优化等级参数给底层的 clang 编译器，常用的优化等级包括：

- `-O0`：无优化，适合调试。
- `-O1`：基本优化，平衡编译时间和性能。
- `-O2`：较高优化。
- `-O3`：最高优化，可能增加编译时间和二进制大小。
- `-Os`：优化代码大小。

### 3.5 `--out PATH` / `--output PATH` 指定输出路径

通过 `--out` 或 `--output` 参数可以指定编译产物的输出路径：

```bash
scripts/yian_compiler.py tests/call/func.an --output build/func
```

如果未指定输出路径，脚本会默认将产物输出到 `tests/yian_workspace/objects` 目录下，文件名根据目标类型自动命名（如 `out.ll` 或 `out`）。

### 3.6 `--target TARGET` 指定编译目标类型

通过 `--target` 参数可以指定编译产物的类型，支持以下选项：

- `ll`：输出 LLVM IR 文本文件（.ll）。
- `exe`：输出可执行文件（默认）。

## 4. 常见问题

### 4.1 提示 missing compiler arguments

原因：未提供任何编译输入（如源文件路径）。

处理：至少提供一个有效的编译参数，例如 tests/control_flow/for.an。

### 4.2 clang 相关错误

现象：target=exe 时失败。

处理建议：

1. 确认系统已安装 clang。
2. 检查传入的 -O* 参数是否拼写正确。
3. 确认编译阶段已生成对象文件（tests/yian_workspace/objects/output.o）。

### 4.3 找不到预期编译产物

现象：报错 Expected compiler artifact not found。

处理建议：

1. 优先检查 compiler/main.py 阶段是否已失败。
2. 查看 tests/yian_workspace/objects 目录下是否生成 output.ll 或 output.o。
3. 必要时加 -d 重新执行以获取更多上下文。

## 5. 相关文件

1. `scripts/yian_compiler.py`：命令行入口。
2. `scripts/compiler_api.py`：编译管线与产物处理逻辑。
3. `compiler/main.py`：核心编译命令实现。
4. `lian/scripts/dfview.py`：中间结果展示工具。
