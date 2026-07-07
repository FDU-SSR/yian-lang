# Yian 语言参考

本文档完整描述 Yian 编程语言的语法和语义。每个章节包含语法规则、来自 `tests/` 的可编译代码示例，以及常见陷阱。

## 目录

| 章节                         | 标题          | 说明                                                                |
| ---------------------------- | ------------- | ------------------------------------------------------------------- |
| [00](00.quick_start.md)      | Quick Start   | 30 分钟快速上手                                                     |
| [01](01.lexicon.md)          | 词法          | 标识符、关键字、字面量、注释、运算符                                |
| [02](02.types.md)            | 类型系统      | 基础类型、指针、数组、切片、元组、struct、enum、`@BitCopy`          |
| [03](03.variables.md)        | 变量与声明    | `let` 声明、类型标注、类型推断                                      |
| [04](04.functions.md)        | 函数          | 定义、参数、返回值、泛型函数、函数指针                              |
| [05](05.methods.md)          | 方法          | `impl` 块、实例方法、静态方法、可见性                               |
| [06](06.control_flow.md)     | 控制流        | `if`/`elif`/`else`、`loop`、`while`、`for`-`in`、`break`/`continue` |
| [07](07.match.md)            | 模式匹配      | `match` 表达式、enum pattern、payload pattern                       |
| [08](08.expressions.md)      | 表达式        | 算术、比较、逻辑、位运算、赋值、内置函数                            |
| [09](09.operators.md)        | 运算符重载    | `Add`、`PartialEq`、`Index`、`Deref`、复合赋值等全部 trait          |
| [10](10.generics.md)         | 泛型          | 泛型函数/struct/enum、泛型 impl、条件约束                           |
| [11](11.structs_enums.md)    | 结构体与枚举  | 定义、构造、字段访问、`@BitCopy`                                    |
| [12](12.traits_impls.md)     | Trait 与 Impl | trait 定义、impl 实现、默认方法、条件 impl                          |
| [13](13.modules.md)          | 模块系统      | `import`、`from...import`、可见性、`std` 命名空间                   |
| [14](14.standard_library.md) | 标准库        | `Option`、`Result`、`Vec`、`String`、`Iterator` 等                  |
| [15](15.assign_semantics.md) | 赋值语义      | 简单类型、非简单类型、`.move()`、`.clone()`、`@BitCopy`             |

## 约定

- 所有代码示例均可通过 `python3 -m compiler.main lib <file>` 编译
- 示例标注 `-- 来源: tests/...` 指明出处
- 错误示例标注 `-- 期望: 编译错误`
- 语法规则使用代码块表示
