# 笔记

## 目标三元组与 data layout

模块上必须**成对**写目标三元组与配套的 data layout: 类型的具体布局(TargetData)、字段偏移、
对齐填充都由 data layout 决定, 而中端优化 pass 与后端代码生成必须看到同一份。

`bootstrap/llvm/src/ir.an` 创建本机目标机，并将目标三元组和 data layout 一起写入 LLVM 模块。
`yianc/src/codegen/session.an` 使用该布局进行类型大小查询，并检查 Linux x86-64 LP64 目标。

## 目标机与代码模型

LLVM 绑定以 PIC relocation 和 small code model 创建目标机，后端代码生成级别来自优化配置。

## 指针表示（opaque pointer）

LLVM IR 使用 `ptr`。YIAN 的后端类型决定 load、store 和 GEP 所使用的值类型或元素类型。
指针模式和安全义务由 CFG 记录，表示与下降规则见 [LLVM 代码生成](manual/12.llvm_codegen.md)。

## 链接性(Linkage)

`llvm` 中, 全局变量和函数可以有不同的链接性 (linkage), 包括:

- `common`: 用于未初始化的, 可以在多个模块中定义的全局变量, 链接器会将这些定义合并为一个.
- `external`: 默认链接性, 表示该符号在模块外部可见, 可以被其他模块引用.
- `internal`: 表示该符号仅在当前模块内可见, 不能被其他模块引用, *但是其他模块仍然有可能通过间接方式(如函数指针)访问该符号*.
- `private`: 只能从当前模块直接引用，不出现在目标文件符号表；地址仍可通过指针传出。
- `weak`: 类似于 `linkonce`, 但弱符号在链接时允许被其他强符号覆盖.
- `extern_weak`: 类似于 `weak`, 但表示该符号在模块外部可见, 可以被其他模块引用, 但如果没有定义, 链接器不会报错.
- `available_externally`: 表示该符号在模块外部可见, 但不会在最终生成的目标文件中包含该符号的定义. 这种链接性通常用于跨模块的内联.
- `linkonce`: 表示该符号可以在多个模块中定义, 但链接器会选择其中一个定义进行链接.
- `appending`: 用于全局变量, 表示该变量的定义会被追加到同名变量的末尾, 适用于数组等数据结构.

`yianc/src/codegen/llvm/module.an` 将普通生成函数标记为 `internal`，将程序入口发射为
`__yian_main`。C 运行时的 `main` 调用该入口，外部 C 与运行时声明保留外部名称。

## 属性(Attribute)

`llvm` 中, 函数和指令可以有不同的属性 (attribute), 常用的整理如下:

### 函数属性(Function Attributes)

| 属性名                     | 类型     | 说明                                                                        |
| -------------------------- | -------- | --------------------------------------------------------------------------- |
| `AlwaysInline`             | EnumAttr | 强制内联函数                                                                |
| `NoInline`                 | EnumAttr | 禁止内联函数                                                                |
| `Cold`                     | EnumAttr | 函数不常用, 优化器会将其放在冷代码区                                        |
| `Hot`                      | EnumAttr | 函数常用, 优化器会将其放在热代码区                                          |
| `NoReturn`                 | EnumAttr | 函数不会返回, 如 `exit` 函数                                                |
| `NoUnwind`                 | EnumAttr | 函数不会抛出异常                                                            |
| `Naked`                    | EnumAttr | 函数没有函数前后栈帧, 适用于内联汇编                                        |
| `NoRecurse`                | EnumAttr | 函数不会递归调用自身                                                        |
| `ReturnsTwice`             | EnumAttr | 函数可能多次返回, 如 `setjmp` 函数                                          |
| `Speculatable`             | EnumAttr | 无副作用且不会引入未定义行为，可在没有执行原调用的路径上提前执行            |
| `WillReturn`               | EnumAttr | 调用会返回到调用者；不返回的执行违反该属性契约                              |
| `Convergent`               | EnumAttr | 调用依赖参与执行的线程集合，代码移动与复制必须保持相应收敛约束              |
| `SafeStack`                | EnumAttr | 启用安全栈保护机制(见[栈保护](#栈保护stack-protection))                     |
| `StackProtect`             | EnumAttr | 启用栈保护机制(见[栈保护](#栈保护stack-protection))                         |
| `StackProtectStrong`       | EnumAttr | 启用强栈保护机制(见[栈保护](#栈保护stack-protection))                       |
| `SanitizeAddress`          | EnumAttr | 启用 AddressSanitizer                                                       |
| `SanitizeThread`           | EnumAttr | 启用 ThreadSanitizer                                                        |
| `SanitizeMemory`           | EnumAttr | 启用 MemorySanitizer                                                        |
| `SpeculativeLoadHardening` | EnumAttr | 启用推测性加载硬化保护机制                                                  |
| `OptNone`                  | EnumAttr | 禁用优化                                                                    |
| `OptForSize`               | EnumAttr | 优化代码大小                                                                |
| `MinSize`                  | EnumAttr | 最小化代码大小                                                              |
| `InlineHint`               | EnumAttr | 建议内联函数                                                                |
| `NoBuiltin`                | EnumAttr | 禁止将函数视为内建函数(见[内建函数](#内建函数builtin-functions))            |
| `Builtin`                  | EnumAttr | 将函数视为内建函数(见[内建函数](#内建函数builtin-functions))                |
| `NoDuplicate`              | EnumAttr | 禁止产生同一调用的额外副本，例如循环展开；只保留一个副本的内联仍可进行       |
| `NoMerge`                  | EnumAttr | 禁止合并该调用(例如*公共子表达式消除*, *循环不变代码外提*)                  |
| `NoCfCheck`                | EnumAttr | 禁用控制流完整性检查(见[控制流完整性](#控制流完整性control-flow-integrity)) |
| `StrictFP`                 | EnumAttr | 启用严格的浮点运算语义(见[严格浮点](#严格浮点strict-floating-point))        |
| `MustProgress`             | EnumAttr | 执行必须终止、展开异常或与环境交互；不保证所有调用都返回                    |
| `AllocSize`                | IntAttr  | 函数的返回值指向的内存块大小可以通过函数的参数计算得出                      |
| `Memory`                   | IntAttr  | 通过位掩码指定函数对内存的访问行为                                          |

YIAN 的运行时声明和插桩属性由原生 LLVM 下降及绑定设置。参见 [LLVM 代码生成](manual/12.llvm_codegen.md)。

### 参数属性(Parameter Attributes)

| 属性名                  | 类型     | 说明                                                   |
| ----------------------- | -------- | ------------------------------------------------------ |
| `ByVal`                 | TypeAttr | 按值传递参数, 传递参数的副本                           |
| `ByRef`                 | TypeAttr | 声明指针参数的内存类型与可解引用大小，不要求隐式复制或栈上传递 |
| `InReg`                 | EnumAttr | 强制使用寄存器传递参数                                 |
| `NoAlias`               | EnumAttr | 调用中经该参数派生指针访问的已修改内存，不与非派生指针的访问重叠 |
| `captures(none)`        | 捕获集合 | 不捕获该指针的地址或来源；LLVM 22 使用 `captures(...)` 表达捕获约束 |
| `NonNull`               | EnumAttr | 参数指针不会为空                                       |
| `Dereferenceable`       | IntAttr  | 参数指针至少可解引用指定字节数                         |
| `DereferenceableOrNull` | IntAttr  | 参数指针为空或至少可解引用指定字节数                   |
| `Returned`              | EnumAttr | 返回值总是等于该参数                                   |
| `ImmArg`                | EnumAttr | 参数必须是一个常量                                     |
| `NoUndef`               | EnumAttr | 参数值的表示不能含 undef 或 poison 位                  |
| `SExt`                  | EnumAttr | 参数在调用前进行符号扩展                               |
| `ZExt`                  | EnumAttr | 参数在调用前进行零扩展                                 |
| `StructRet`             | TypeAttr | 参数为返回结构体的隐藏指针                             |
| `InAlloca`              | TypeAttr | 参数内存由调用者在栈上分配, 被调用函数直接使用这块内存 |
| `Preallocated`          | TypeAttr | 参数指针指向预分配的内存(和 `InAlloca` 类似)           |
| `ReadNone`              | EnumAttr | 不经该指针参数读取或写入内存                           |
| `ReadOnly`              | EnumAttr | 不经该指针参数写入内存                                 |
| `WriteOnly`             | EnumAttr | 不经该指针参数读取内存                                 |
| `ElementType`           | TypeAttr | 指定指针参数所指向的实际元素类型                       |

### 返回值属性(Return Attributes)

| 属性名                  | 类型     | 说明                                 |
| ----------------------- | -------- | ------------------------------------ |
| `NoAlias`               | EnumAttr | 返回存储与调用者可访问的其他对象存储不相交 |
| `NonNull`               | EnumAttr | 返回指针不会为空                     |
| `Dereferenceable`       | IntAttr  | 返回指针至少可解引用指定字节数       |
| `DereferenceableOrNull` | IntAttr  | 返回指针为空或至少可解引用指定字节数 |
| `NoUndef`               | EnumAttr | 返回值的表示不能含 undef 或 poison 位 |
| `SExt`                  | EnumAttr | 返回值进行符号扩展                   |
| `ZExt`                  | EnumAttr | 返回值进行零扩展                     |

### 其他属性

| 属性名      | 类型              | 说明                       |
| ----------- | ----------------- | -------------------------- |
| `Alignment` | IntAttr           | 指定参数或返回值的对齐要求 |
| `Range`     | ConstantRangeAttr | 指定参数或返回值的取值范围 |

## 基于类型的别名分析（Type-Based Alias Analysis）

`llvm` 使用类型别名分析 (TBAA) 来帮助优化器理解不同指针类型之间的别名关系. 通过为内存访问指令添加 TBAA 元数据, 可以告诉优化器哪些内存访问不会相互影响, 从而允许包括内存访问重排序在内的更激进优化. 例如:

```c
struct A {
    int x;
};
struct B {
    float y;
};
void foo(struct A* a, struct B* b) {
    a->x = 42;      // Access through struct A.
    b->y = 3.14f;   // Access through struct B.
}
```

访问标签保存 `(基类型, 访问类型, 字节偏移)`，标量节点追溯到共同的根。
同一根下的类型关系定义哪些访问可以别名；不同根的关系未知。规则来自源语言的别名契约，
与整数和浮点之间能否隐式转换无关。YIAN LLVM 发射器不添加 TBAA 元数据。
详见 [LLVM 22 TBAA 规则](https://releases.llvm.org/22.1.0/docs/LangRef.html#tbaa-metadata)。

### 构建 TBAA 元数据

TBAA 元数据通常以树状结构组织, 每个节点表示一种类型及其访问属性. 节点分为两种类型:

- 基本类型节点: 表示基本数据类型, 如 `int`, `float`, `char` 等.
- 聚合类型节点: 表示结构体或联合体等聚合类型, 包含其成员的类型信息.

例如, 下面的 c 代码对应的 TBAA 元数据结构:

```c
struct A {
    int x;
    float y;
};
```

对应的 TBAA 元数据结构如下:

```llvm
!0 = !{ !"Yian TBAA Root" }; root node
!1 = !{ !"int", !0 } ; basic type node for int
!2 = !{ !"float", !0 } ; basic type node for float
!3 = !{ !"struct A", !1, i64 0, !2, i64 4 } ; struct A node with members x and y
!4 = !{ !3, !1, i64 0 } ; TBAA info for a->x
!5 = !{ !3, !2, i64 4 } ; TBAA info for b->y
```

### 使用 TBAA 元数据

在 LLVM IR 中, 可以通过为内存访问指令添加 `!tbaa` 元数据来指定其 TBAA 信息. 例如:

- 标量类型: 设指针所指向类型的 TBAA 信息为 `!N`, 则可以添加元数据 `{!N, !N, i64 0}`.
- 聚合类型: 设基类型的 TBAA 信息为 `!M`, 子元素的 TBAA 信息为 `!N`, 则可以添加元数据 `{!M, !N, offset}`.
- 数组访问: 视为对数组元素类型的访问

## 栈上对象生命周期(Stack Object Lifetimes)

LLVM 22 的栈对象生命周期 intrinsic 接受 `alloca` 地址或 poison：

```llvm
declare void @llvm.lifetime.start(ptr captures(none))
declare void @llvm.lifetime.end(ptr captures(none))
```

这些标记供优化器推理栈对象存活范围，不执行 YIAN 的运行时撤销检查。YIAN 发射器不生成这些标记，
其帧锁机制在 `yianc/src/codegen/llvm/pointer/safety.an` 中实现。
接口见 [LLVM 22 生命周期标记](https://releases.llvm.org/22.1.0/docs/LangRef.html#llvm-lifetime-start-intrinsic)。

## 调试元数据(Debug Metadata)

YIAN LLVM 发射器不生成源码调试元数据。

## 严格浮点(Strict Floating Point)

`strictfp` 与受约束浮点 intrinsic 用于表达舍入模式和浮点状态要求，并不禁止所有常量折叠。
YIAN 发射器使用普通浮点指令，不添加 `strictfp` 或受约束浮点 intrinsic。
语义见 [LLVM 22 strictfp](https://releases.llvm.org/22.1.0/docs/LangRef.html#fnattr-strictfp)。

## 内建函数(Builtin Functions)

`llvm` 提供了一些内建函数 (builtin functions), `llvm` 理解这些函数的语义, 副作用等, 优化器可以基于这些信息进行更好的优化. 常见的内建函数包括:

- `memcpy`, `memset`, `strlen`, `strcmp` 等内存操作函数
- `malloc`, `free` 等内存分配函数
- `llvm.expect` 用于提供分支预测信息

`yianc/src/codegen/llvm/` 将内存复制、写入及尺寸查询等操作下降到 LLVM IR。
例如枚举 place 快照使用 `llvm.memmove`，具体约束见 [LLVM 代码生成](manual/12.llvm_codegen.md)。

## LLVM 保护机制与 YIAN 检查

YIAN 发射器不添加栈金丝雀、SafeStack 或 CFI 的专门属性与插桩；fat 指针安全检查由自身表示与运行时实现。
以下机制是 LLVM 提供的独立能力。

### 栈保护(Stack Protection)

`llvm` 提供了三种栈保护机制:

- 栈保护/栈金丝雀: 在函数入口处插入一个随机生成的金丝雀值, 在函数返回前检查该值是否被篡改, 以检测栈溢出攻击.
- 强栈保护: 栈保护的增强版本, 采用更激进的保护措施
- 安全栈: 通过将敏感数据存储在独立的安全栈中, 防止栈溢出攻击访问这些数据.

### 控制流完整性(Control Flow Integrity)

`llvm` 支持控制流完整性 (CFI), 通过在间接调用和跳转处插入检查, 确保程序的控制流只能按照预定义的合法路径进行, 防止控制流劫持攻击.
