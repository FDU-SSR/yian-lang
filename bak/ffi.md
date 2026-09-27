# C ABI FFI 设计草案

本文描述候选机制；示例语法和接口不代表当前编译器已支持。FFI 使 YIAN 能调用固定签名的 C 函数，
并为使用 LLVM C API 编写自举编译器提供基础。FFI 不承担 C++ ABI、自动内存管理或跨语言安全验证。

## 外部声明与类型

外部声明与 YIAN 函数定义分离。外部函数使用 C 调用约定和声明中的 C 符号名，不生成 YIAN 函数体或名称修饰；
声明遵守普通模块导入与可见性规则。

```yian
opaque type LLVMContext;

extern "C" {
    fn LLVMContextCreate() -> cptr<LLVMContext>;
    fn LLVMContextDispose(context: cptr<LLVMContext>);
}

fn use_context() {
    unsafe {
        let context = LLVMContextCreate();
        LLVMContextDispose(context);
    }
}
```

`opaque type` 只提供类型身份，不能按值构造、访问字段或计算大小。`cptr<T>` 是 C ABI 裸指针，在 raw 和 fat
模式下均占目标机器的一个指针宽度，可表示空指针。它不隐式转换为 `T*`、`T&`、切片或 trait object；这些
YIAN 类型在 fat 模式下携带额外的安全元数据。`cptr<T>` 不携带 YIAN 的边界与生命周期检查。当 `T` 是
opaque type 时，`cptr<T>` 只可作为句柄传递，不能解引用。

外部函数的参数和返回值限于 ABI 明确的标量、`void` 与 `cptr<T>`。定宽整数和浮点数按对应 C 宽度传递；
`c_int`、`c_uint`、`c_size` 等别名由目标平台定义。LLVM 的 `LLVMBool` 对应 `c_int`，不对应 YIAN
`bool`。YIAN `bool`、`char`、`str`、普通指针、引用、切片、容器和用户结构体不能直接出现在外部函数
签名中。`T**` 输出参数和指针数组使用 `cptr<cptr<T>>` 等 C 兼容存储。泛型外部函数、可变参数、C 结构体
按值传递、回调函数和 C++ ABI 不在此设计范围内。

C 的 `char*` 使用显式 NUL 结尾缓冲区。输入转换拒绝内部 NUL 字节，并保证缓冲区在调用期间有效；返回
字符串按对应 C API 的规则复制或释放。C 头文件中的宏与 `static inline` 函数没有可直接链接的符号，
绑定使用已导出的函数，或使用与目标库版本一致的薄 C 包装。

## 安全边界与资源生命周期

外部函数调用必须位于 `unsafe` 块内。对 `cptr<T>` 解引用或做指针运算，以及从 YIAN 引用提取 C 地址，
也只能在 `unsafe` 上下文中进行。提取的地址不能比来源对象存活更久；若 C 函数保存地址，调用方还须保证
对象在保存期间不释放或移动。`unsafe` 标记责任边界，不提供借用检查或自动延长生命周期。运行时 fat
指针检查不覆盖外部 C 指针指向的内存。

封装模块可检查参数与返回值，向普通 YIAN 代码暴露安全接口。YIAN 赋值、参数传递和返回仍采用浅层值
复制，`Drop` 仍需显式调用。外部资源句柄的复制只是地址复制，创建、借用、转移和销毁责任由封装接口约定。
同一资源只调用一次对应的释放函数；来自不同 C API 的内存也分别使用其规定的释放函数，例如 LLVM 返回
的消息使用 `LLVMDisposeMessage`。

## 编译和链接

编译器为外部声明记录 C 符号、调用约定及 ABI 类型，在 HIR、CFG 和 LLVM 阶段保持该信息。类型检查
拒绝不支持的签名、隐式指针转换及缺少 `unsafe` 的调用。外部调用不能用于顶层常量或 `comptime if` 的
编译期求值。编译器无法从动态库验证手写声明是否与 C 头文件一致，这一一致性由绑定维护者负责。

原生库由构建配置提供链接名称、搜索路径和运行时加载路径。`yianc` 使用可重复的 `--link-lib` 和
`--link-search` 参数接收原生库，`anx` 从包清单的原生依赖条目汇总并传递这些参数。LLVM IR、bitcode、
object 和 assembly 输出保留未解析的外部符号。构建 LLVM 绑定时，库名称、头文件与链接参数来自同一套
LLVM 22 工具链的 `llvm-config`，避免把系统路径写入 YIAN 源码。

## LLVM 绑定与自举

LLVM 绑定覆盖 Context、Module、Builder、TargetMachine 和 TargetData 句柄。绑定使用 LLVM 22 的
`LLVMBuildLoad2`、`LLVMBuildCall2`、`LLVMBuildGEP2` 等显式类型接口构造 IR，通过 TargetData 查询
大小、对齐及字段偏移，通过 pass manager 优化，并由 TargetMachine 发射 object 或 assembly。
`LLVMPrintModuleToString` 提供 LLVM IR 文本输出。目标初始化使用已导出的目标专用函数，或使用与 LLVM
版本一致的薄 C 包装。

YIAN 编写的编译器核心接收明确的源文件列表，执行词法、语法、语义分析与代码生成，通过 LLVM C API
查询目标布局、构造 IR 并发射目标文件；构建驱动链接运行时。完整的命令行工具还需要目录遍历、进程
启动、路径处理以及包图和清单解析能力。

自举编译器需要把 YIAN 类型映射成对应 LLVM 类型，保持聚合布局、调用约定与 raw/fat 指针 ABI 一致。
FFI 裸指针只用于跨 C 边界的句柄和缓冲区，不代替 YIAN 指针的安全表示。AST、HIR、CFG 和符号表适合
使用稳定的节点 ID 与集中分配的存储区，并明确字符串、容器及节点的释放责任。

## 验证条件

FFI 验证覆盖 raw/fat 两种模式的标量传参、空指针、NUL 字符串、数组和输出参数、错误返回、资源释放与
动态库链接，并确认不支持的签名或缺少库时给出明确诊断。LLVM 绑定验证覆盖模块构造与验证、聚合布局
查询、目标文件发射与链接，以及生成程序的运行。

自举闭环由 Python 编译器生成 stage1，stage1 编译同一份 YIAN 编译器源码生成 stage2，stage2 再生成
stage3。stage2 与 stage3 在固定源码、运行时和工具链条件下，对语言用例给出一致的编译结果与程序行为。
编译器规模验证同时检查多模块源码、深层泛型实例化、大量中间节点的编译时间、内存占用与错误恢复。
