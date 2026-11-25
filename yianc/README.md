# yianc

`yianc` 是 YIAN 语言的原生编译器，也是格式化器和语言服务器共享的前端库。编译器本身以
YIAN 编写，由原生 `anx` 构建为不依赖 Python 的 ELF 可执行文件。

编译管线为：

```text
.an → tokens → AST → desugared AST → HIR → address-storage normalization → CFG → LLVM IR → native output
```

原生 `yianc` 提供两类入口：`--check` 完成前端与中端检查，不生成产物；默认编译继续下降为
CFG 和 LLVM IR，并按 `-t` 输出可执行文件、`.ll` 或 `.bc`。可执行文件由 C 工具链链接。诊断按阶段
返回具体 `UserDiagnostic`，warning 通过同步 reporter 立即报告；用户错误终止分析并沿 `Result` 返回，内部不变量违反在发现处 panic。

## 包与依赖

| 项目 | 内容 |
|------|------|
| 包名与类型 | `yianc`，`hybrid`（原生编译器与前端库） |
| 语言实现 | YIAN，入口 `src/main.an` |
| 原生依赖 | LLVM 22；链接可执行文件时使用 C 工具链 |
| 构建依赖 | `bootstrap/json`、`bootstrap/llvm` |
| 原生声明 | `[native.llvm]`：LLVM 22 `core`，C shim `native/host.c` |
| 支持平台 | Linux x86-64 |

安装后的 `yianc` 是原生 ELF，运行时不依赖 Python。

`yianc` 的库接口供 `fmt/` 和 `lsp/` 复用词法、语法、AST、诊断和前端单元表；格式化器与语言服务器
不链接一个独立的编译器进程来获得这些能力。

## 源码结构

| 位置 | 职责 |
|------|------|
| `src/main.an` | 程序入口、阶段组合和顶层错误出口 |
| `src/config.an` | 命令行解析与编译器配置 |
| `src/preprocess.an`、`src/input_error.an` | 包图加载、源码根/信任准备、源码收集和输入诊断 |
| `src/frontend/driver.an`、`src/analysis/driver.an`、`src/codegen/driver.an` | 各阶段的组合入口：pass 顺序、CFG/LLVM 与发布 |
| `src/frontend/` | 源码/单元表、词法、语法、AST、去糖、遍历和前端转储 |
| `src/analysis/` | 权限分类、解析、类型、符号、HIR、常量求值、lowering、检查 pass 和 HIR 转储 |
| `src/codegen/` | CFG、LLVM lowering、指针安全检查、原生链接和阶段转储 |
| `src/telemetry/` | 日志、计时与 profile 记录 |
| `src/host.an`、`src/roots.an` | 主机/安装资源定位、标准库源码根与运行时资源根 |
| `src/common/` | 会话内 ID、源码位置和共享遍历控制 |
| `src/diagnostics/` | 诊断模型、渲染和 stderr 输出 |
| `src/package/` | v2/v3 包图校验与源码来源信任 |
| `native/` | LLVM C shim 与 Unicode 字符表 |
| `tests/` | 单元、集成测试与期望输出 |
| `docs/dev/` | 编译器实现文档（01–21 章） |
| `docs/coding_style.md`、`docs/compile_script.md` | 编码规范与编译器命令行参考 |

## 构建与使用

支持环境是 Linux、LLVM 22 和 clang 22；冷启动还需要 Python 3.12+ 与
`python/requirements.txt` 中的依赖。首次安装或刷新工具链见仓库根目录
[README](../README.md) 和[自举编译器与原生工具链](docs/dev/21.bootstrap.md)。

已有可用的原生 `anx` 与 `yianc` 时，可以直接构建 checkout 中的编译器包：

```bash
PATH="$HOME/.local/bin:$PATH" YIAN_ROOT="$PWD" YIAN_LIB="$PWD/lib/src" anx build yianc -O2
yianc/build/app --check --compiler-root . lib/src tests/basic/array/access.an
yianc/build/app --compiler-root . -O2 -o build/access lib/src tests/basic/array/access.an
```

安装后的 `yianc` 直接接受相同的源码参数。使用标准库时必须把 `lib/src` 作为显式输入：

```bash
yianc lib/src tests/basic/array/assign.an
yianc -t ll lib/src tests/basic/array/assign.an -o build/assign.ll
yianc --check --compiler-root . lib/src tests/basic/array/access.an
```

常用观察和配置入口：

```bash
yianc --dump --compiler-root . lib/src tests/basic/array/access.an
yianc --log --log-spec "main=DEBUG" lib/src tests/basic/array/access.an
yianc --profile --profile-output build/profile/compiler.json lib/src tests/basic/array/access.an
```

格式化统一由独立的 `yian-fmt` 完成，`yianc` 不提供格式化参数：

```bash
yian-fmt --check yianc/src/main.an
yian-fmt -w yianc/src/main.an
```

## 测试

编译器测试位于 `yianc/tests/`：`unit/` 覆盖词法、语法、AST、去糖、类型存储、HIR 验证、
常量和 codegen；`integration/` 覆盖前端单元表、源码、生命周期和端到端行为。

包测试由原生 `anx` 驱动：

```bash
YIAN_ROOT="$PWD" YIAN_LIB="$PWD/lib/src" anx test yianc
```

该命令运行整个 `yianc` 包的测试矩阵。日常修改应按仓库的局部验证规则选择最小范围的
`yianc --check`、格式检查或单个测试入口，不默认执行完整包测试。

仓库级 basic、safety、package 和 bootstrap 套件由 `scripts/run_tests.py` 驱动；suite 选择、
指针模式和授权边界见 [AGENTS.md](../AGENTS.md)。

## 文档索引

### 开发文档

完整目录见 [yianc 开发文档](docs/index.md)。

| 主题 | 文档 |
|------|------|
| 编译管线与前端 | [01 编译管线概览](docs/dev/01.pipeline.md)、[02 前端](docs/dev/02.frontend.md)、[03 AST 去糖](docs/dev/03.desugar.md)、[04 全局符号解析](docs/dev/04.resolve.md) |
| 类型、Lowering 与 HIR | [05 类型系统](docs/dev/05.type_system.md)、[06 表达式 Lowering](docs/dev/06.lower_expr.md)、[07 调用 Lowering](docs/dev/07.lower_call.md)、[08 HIR](docs/dev/08.hir.md)、[09 类型检查](docs/dev/09.type_check.md) |
| CFG 与 LLVM | [10 CFG IR](docs/dev/10.cfg_ir.md)、[11 CFG 构建](docs/dev/11.cfg_builder.md)、[12 LLVM 代码生成](docs/dev/12.llvm_codegen.md) |
| 构建、语言约定与诊断 | [13 日志系统](docs/dev/13.log.md)、[14 CLI 与构建](docs/dev/14.cli_build.md)、[15 标准库架构](docs/dev/15.stdlib.md)、[16 赋值语义实现](docs/dev/16.assign_semantics.md)、[17 Impl 注册与查找](docs/dev/17.impl_registry.md)、[18 错误处理](docs/dev/18.error_handling.md) |
| 分析接口、FFI 与自举 | [19 分析接口](docs/dev/19.analysis_interface.md)、[20 FFI 编译与链接](docs/dev/20.ffi.md)、[21 自举编译器与原生工具链](docs/dev/21.bootstrap.md) |

### 规范与参考

| 文档 | 内容 |
|------|------|
| [编码规范](docs/coding_style.md) | 模块组织、命名、类型声明、所有权、错误处理、注释、排版和测试约定 |
| [编译器命令行手册](docs/compile_script.md) | 参数、编译目标、包图、检查模式、诊断和 profiling |

### 仓库相关文档

- [语言参考](../docs/grammar/index.md)
- [anx 包管理器](../docs/anx/index.md)
- [依赖图与 v2/v3 包图](../docs/manual/23.anx_graph.md)
- [语言服务器](../docs/manual/27.language_server.md)
- [格式化器](../docs/manual/28.formatter.md)
- [内存安全机制概述](../docs/security.md)
- [项目总览与工具链安装](../README.md)
