# YIAN 语言概览

YIAN 是一个面向系统编程场景的静态类型语言，采用编译型工作流：将 `.an` 源码编译为可执行程序。

如果你第一次接触 YIAN，建议先阅读 [快速开始](00.quick_start.md)，再按顺序浏览后续章节。

## YIAN 是什么

- 目标定位：提供清晰、可预测的语义与类型系统，适合构建需要性能与类型安全的程序。
- 语言形态：类 C 风格语法，显式类型标注，函数与方法并重。
- 工具链现状：当前仓库提供编译脚本、标准库源码与覆盖较广的测试用例，适合学习和实验。

## 核心特性

- 静态类型系统：包含基本类型、数组、元组、指针、函数类型、结构体、枚举、trait 等。
- 泛型能力：支持泛型函数、泛型类型与泛型 trait，泛型在编译期实例化。
- 方法系统：支持 inherent method 与 trait method，通过 `impl` 块组织行为。
- 模块系统：以源文件为模块，使用 `from ... import ...` / `import ...` 组织多文件项目。
- 表达式与控制流：覆盖常见字面量、算术与逻辑表达式，以及 if/for/while/match 等语句结构。
- 标准库：提供 `std.core`、`std.collections`、`std.num` 等核心模块。

## 一个最小程序

```yian
from std.core.io import print

i32 main() {
	print("Hello, yian!\n")
	return 0
}
```

编译并运行：

```bash
./scripts/yian_compiler.py hello.an
./tests/yian_workspace/bin/out
```

## 文档导览

- [00.quick_start.md](00.quick_start.md)：30-45 分钟跑通最小示例。
- [01.intro.md](01.intro.md)：语法入口与程序结构。
- [02.type_system.md](02.type_system.md)：类型系统全景。
- [03.methods.md](03.methods.md)：方法、`impl` 与 trait 实现。
- [04.statements.md](04.statements.md)：语句与控制流。
- [05.generics.md](05.generics.md)：泛型机制与使用方式。
- [06.modules.md](06.modules.md)：模块、导入与可见性。
- [07.expression.md](07.expression.md)：表达式构成与规则。
- [08.standard_library.md](08.standard_library.md)：标准库模块与常见类型。

## 当前阶段说明

- YIAN 仍在持续演进，部分特性与标准库接口可能会迭代。
- 学习或验证语义时，建议结合 `tests/` 目录中的示例与用例一起阅读。
- 如果你准备编写较完整的程序，优先熟悉类型系统、模块系统和标准库章节。
