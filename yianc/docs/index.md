# yianc 文档

`yianc` 是以 YIAN 编写的原生编译器，覆盖源码准备、语义分析、HIR 到 CFG 的下降、
LLVM 代码生成和原生链接。

编译器实现文档位于 [dev/](dev/)；编码规范和命令行参考位于本目录。
编译器源码位于 `yianc/src/`，原生 C 辅助代码位于 `yianc/native/`。日常构建、测试和安装
入口见仓库根目录的 [README](../../README.md) 与 [自举编译器与原生工具链](dev/21.bootstrap.md)。

## 编译管线

| 章 | 标题 | 对应源文件 |
|----|------|-----------|
| [01](dev/01.pipeline.md) | 编译管线概览 | `yianc/src/main.an`、`yianc/src/{frontend,analysis,codegen}/driver.an` |
| [02](dev/02.frontend.md) | 前端 | `yianc/src/frontend/`（含 `driver.an`、`dump.an`） |
| [03](dev/03.desugar.md) | AST 去糖 | `yianc/src/frontend/desugar.an` |
| [04](dev/04.resolve.md) | 全局符号解析 | `yianc/src/analysis/resolution/` |

## 类型、Lowering 与 HIR

| 章 | 标题 | 对应源文件 |
|----|------|-----------|
| [05](dev/05.type_system.md) | 类型系统 | `yianc/src/analysis/ty/` |
| [06](dev/06.lower_expr.md) | 表达式 Lowering | `yianc/src/analysis/lowering/` |
| [07](dev/07.lower_call.md) | 调用 Lowering | `yianc/src/analysis/lowering/calls.an`、`methods.an` |
| [08](dev/08.hir.md) | HIR IR | `yianc/src/analysis/hir/` |
| [09](dev/09.type_check.md) | 类型检查 | `yianc/src/analysis/passes/type_check.an` |

## CFG 与 LLVM

| 章 | 标题 | 对应源文件 |
|----|------|-----------|
| [10](dev/10.cfg_ir.md) | CFG IR | `yianc/src/codegen/cfg/model.an` |
| [11](dev/11.cfg_builder.md) | CFG 构建 | `yianc/src/codegen/lowering/`、`yianc/src/codegen/cfg/passes/` |
| [12](dev/12.llvm_codegen.md) | LLVM 代码生成 | `yianc/src/codegen/llvm/` |

## 构建、语言约定与诊断

| 章 | 标题 | 对应源文件 |
|----|------|-----------|
| [13](dev/13.log.md) | 日志系统 | `yianc/src/telemetry/` |
| [14](dev/14.cli_build.md) | CLI 与构建 | `yianc/src/main.an`、`yianc/src/config.an` |
| [15](dev/15.stdlib.md) | 标准库架构 | `lib/` |
| [16](dev/16.assign_semantics.md) | 赋值语义实现 | `yianc/src/analysis/lowering/operations.an` |
| [17](dev/17.impl_registry.md) | Impl 注册与查找 | `yianc/src/analysis/ty/impl_registry.an` |
| [18](dev/18.error_handling.md) | 错误处理 | `yianc/src/diagnostics/` |

## 分析接口、FFI 与自举

| 章 | 标题 | 对应源文件 |
|----|------|-----------|
| [19](dev/19.analysis_interface.md) | 分析接口 | `yianc/src/analysis/session/`、`lsp/src/analysis/` |
| [20](dev/20.ffi.md) | FFI 编译与链接 | `yianc/src/`、`anx/src/project/package_map.an` |
| [21](dev/21.bootstrap.md) | 自举编译器与原生工具链 | `yianc/`、`anx/`、`bootstrap/` |

## 规范与参考

| 文档 | 说明 |
|------|------|
| [编码规范](coding_style.md) | 编译阶段的模块边界、会话内 ID、诊断、测试与文档同步 |
| [编译器命令行手册](compile_script.md) | 原生 `yianc` 的参数、编译目标、包图、诊断和检查模式 |

## 相关文档

- [语言参考](../../docs/grammar/index.md)
- [anx 包管理器](../../docs/anx/index.md)
- [anx、语言服务器与格式化器开发文档](../../docs/manual/index.md)
- [语言服务器](../../docs/manual/27.language_server.md)
- [格式化器](../../docs/manual/28.formatter.md)
- [依赖图与 v2/v3 包图](../../docs/manual/23.anx_graph.md)
- [内存安全机制概述](../../docs/security.md)
