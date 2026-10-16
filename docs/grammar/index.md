# Yian 语言参考

本文档描述 Yian 编程语言的语法和语义，包含规则、示例和常见陷阱。

语言规则由规范和测试表达；编译器源码路径仅说明实现位置。日常行为以 native `yianc` 为准，Python 编译器只维护显式
冷启动恢复所需兼容性，不要求与 native 功能对等。

## 目录

| 章节                         | 标题          | 说明                                                                |
| ---------------------------- | ------------- | ------------------------------------------------------------------- |
| [00](00.quick_start.md)      | Quick Start   | 30 分钟快速上手                                                     |
| [01](01.lexicon.md)          | 词法          | 标识符、关键字、字面量、注释、运算符                                |
| [02](02.type_system.md)      | 类型系统      | 基础类型、指针、引用、trait object、数组、切片、元组、struct、enum   |
| [03](03.variables.md)        | 变量与声明    | `let`、解构声明、模块常量、类型标注与推断                            |
| [04](04.functions.md)        | 函数          | 定义、参数、返回值、泛型函数、函数指针、闭包                        |
| [05](05.methods.md)          | 方法          | `impl` 块、实例方法、静态方法、可见性                               |
| [06](06.control_flow.md)     | 控制流        | `if`/`elif`/`else`、`loop`、`while`、`for`-`in`、`break`/`continue` |
| [07](07.match.md)            | 模式匹配      | 结构化模式、守卫、解构绑定、条件模式、穷尽性与不可达检查              |
| [08](08.expressions.md)      | 表达式        | 运算、赋值、错误传播、编译期条件与内建函数                          |
| [09](09.operators.md)        | 运算符重载    | `Add`、`PartialEq`、`Index`、`Deref`、复合赋值等全部 trait          |
| [10](10.generics.md)         | 泛型          | 泛型函数/struct/enum、泛型 impl、条件约束                           |
| [11](11.structs_enums.md)    | 结构体与枚举  | 定义、构造、字段访问                                                |
| [12](12.traits_impls.md)     | Trait 与 Impl | trait 定义、静态调用、trait object、默认方法、条件 impl              |
| [13](13.modules.md)          | 模块系统      | `import`、`from...import`、可见性、Package/Standalone 模式、包名解析 |
| [14](14.standard_library.md) | 标准库        | 容器、字符串、迭代器、错误传播、同步 IO、文件系统与路径             |
| [15](15.assign_semantics.md) | 赋值与显式内存管理 | 按位复制、显式 `Clone`、`del` 与 `Drop`、容器别名                 |
| [16](16.runtime_errors.md)   | 运行时错误与安全终止 | fail-stop、安全错误编号、诊断格式与进程退出约定                 |
| [17](17.ffi.md)              | C ABI FFI      | FFI 权限、C 类型、外部声明、封送操作与资源边界                    |

## 约定

- 完整程序示例可通过 `yianc lib/src <file>` 编译；使用 FFI 的独立程序还需 `--allow-ffi`，包项目则按清单授予权限
- 示例标注 `-- 来源: tests/...` 指明出处
- 错误示例标注 `-- 期望: 编译错误`
- 语法规则使用代码块表示
