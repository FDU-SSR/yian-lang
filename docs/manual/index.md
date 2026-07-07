# Yian 编译器开发文档

本文档描述 Yian 编译器的内部架构、数据流和关键算法。按源文件模块组织，每章对应编译管线的一个阶段。

## 目录

| 章 | 标题 | 对应源文件 |
|----|------|-----------|
| [01](01.pipeline.md) | 编译管线概览 | `compiler/main.py` |
| [02](02.frontend.md) | 前端 | `compiler/frontend/lex/`、`compiler/frontend/parse/` |
| [03](03.desugar.md) | AST 去糖 | `compiler/analysis/passes/desugar.py` |
| [04](04.resolve.md) | 全局符号解析 | `compiler/analysis/passes/global_resolve.py` |
| [05](05.type_system.md) | 类型系统 | `compiler/analysis/ty/` |
| [06](06.lower_expr.md) | 表达式 Lowering | `compiler/analysis/lowering/expr_checker.py` |
| [07](07.lower_call.md) | 调用 Lowering | `compiler/analysis/lowering/call_dispatcher.py` |
| [08](08.hir.md) | HIR IR | `compiler/analysis/unit/hir.py` |
| [09](09.type_check.md) | 类型检查 | `compiler/analysis/passes/type_check.py` |
| [10](10.cfg_ir.md) | CFG IR | `compiler/codegen/cfg/ir.py` |
| [11](11.cfg_builder.md) | CFG 构建 | `compiler/codegen/cfg/builder.py` |
| [12](12.llvm_codegen.md) | LLVM 代码生成 | `compiler/codegen/llvm/` |
| [13](13.log.md) | 日志系统 | `compiler/utils/log.py` |
| [14](14.cli_build.md) | CLI 与构建 | `compiler/main.py` |
| [15](15.stdlib.md) | 标准库架构 | `lib/` |
| [16](16.assign_semantics.md) | 赋值语义实现 | `assign_check.py`、`is_simple_type` |
| [17](17.impl_registry.md) | Impl 注册与查找 | `compiler/analysis/ty/impl.py` |
| [18](18.error_handling.md) | 错误处理 | `compiler/analysis/error.py` |
