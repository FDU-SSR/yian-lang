# Yian 工具链开发文档

本目录收录原生包管理器 `anx`、语言服务器和格式化器的内部实现文档。
原生编译器 `yianc` 的开发文档位于 [yianc/docs](../../yianc/docs/index.md)。

各章保留其稳定编号，便于 anx、语言服务器和格式化器文档按章号交叉引用。

- **原生 anx 包管理器**（20–25）：YIAN 项目模型、清单与诊断、依赖图与 `build/pkg.json` 契约、CLI，
  以及项目测试驱动。
- **语言服务器**（27）：`lsp/` 中的原生协议服务、调度与分发。
- **格式化器**（28）：独立 `yian-fmt` 命令的源码排版规则、注释与空行的恢复、五条不变式与三个入口。

## 目录 · anx 包管理器

| 章 | 标题 | 对应源文件 |
|----|------|-----------|
| [20](20.anx_overview.md) | anx 概览 | `anx/src/`：模块地图、build 数据流、compiler 契约 |
| [21](21.anx_manifest.md) | 清单解析与诊断 | `anx/src/manifest/`、`diagnostics/model.an` |
| [22](22.anx_project.md) | 项目模型与文件归属 | `anx/src/project/model.an`、`project/load.an` |
| [23](23.anx_graph.md) | 依赖图与 v2/v3 包图 | `anx/src/project/`、native `yianc` 的日常 package-map consumer |
| [24](24.anx_cli.md) | CLI 与标准库定位 | `anx/src/main.an`、`project/paths.an` |
| [25](25.anx_tests.md) | anx 项目测试与仓库 fixtures | `anx/src/driver/test.an`、`scripts/run_tests.py` |

## 目录 · 语言服务器与格式化器

| 章 | 标题 | 对应源文件 |
|----|------|-----------|
| [27](27.language_server.md) | 语言服务器 | `lsp/`、`ide-support/vscode/` |
| [28](28.formatter.md) | 格式化器 | `fmt/`、`anx/src/driver/formatter.an`、`lsp/src/formatting.an` |

## 相关文档

- 编译器实现、编码规范与命令行参考：[yianc/docs](../../yianc/docs/index.md)
- 语言语法与标准库：[语言参考](../grammar/index.md)
- 用户视角的包管理用法：[anx 用户指南](../anx/index.md)
- 内存安全机制：[内存安全说明](../security.md)
