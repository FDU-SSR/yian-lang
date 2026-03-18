# 笔记

## 链接性(Linkage)

`llvm` 中, 全局变量和函数可以有不同的链接性 (linkage), 包括:

- `common`: 用于未初始化的, 可以在多个模块中定义的全局变量, 链接器会将这些定义合并为一个.
- `external`: 默认链接性, 表示该符号在模块外部可见, 可以被其他模块引用.
- `internal`: 表示该符号仅在当前模块内可见, 不能被其他模块引用, *但是其他模块仍然有可能通过间接方式(如函数指针)访问该符号*.
- `private`: 比 `internal` 更严格, 表示该符号仅在当前模块内可见, 其他模块无法通过任何方式访问该符号.
- `weak`: 类似于 `linkonce`, 但弱符号在链接时允许被其他强符号覆盖.
- `external_weak`: 类似于 `weak`, 但表示该符号在模块外部可见, 可以被其他模块引用, 但如果没有定义, 链接器不会报错.
- `available_externally`: 表示该符号在模块外部可见, 但不会在最终生成的目标文件中包含该符号的定义. 这种链接性通常用于跨模块的内联.
- `linkonce`: 表示该符号可以在多个模块中定义, 但链接器会选择其中一个定义进行链接.
- `append`: 用于全局变量, 表示该变量的定义会被追加到同名变量的末尾, 适用于数组等数据结构.

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
| `Speculatable`             | EnumAttr | 函数可被推测执行, 即函数不存在可观测的副作用, 只要输入相同, 输出必然相同    |
| `WillReturn`               | EnumAttr | 函数一定会返回, 与 `NoReturn` 相反                                          |
| `Convergent`               | EnumAttr | 编译器不能移动对该函数的调用, 否则可能破坏多线程同步语义                    |
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
| `NoDuplicate`              | EnumAttr | 禁止复制该调用(例如*函数内联*, *循环展开*)                                  |
| `NoMerge`                  | EnumAttr | 禁止合并该调用(例如*公共子表达式消除*, *循环不变代码外提*)                  |
| `NoCfCheck`                | EnumAttr | 禁用控制流完整性检查(见[控制流完整性](#控制流完整性control-flow-integrity)) |
| `StrictFP`                 | EnumAttr | 启用严格的浮点运算语义(见[严格浮点](#严格浮点strict-floating-point))        |
| `MustProgress`             | EnumAttr | 函数必然会产生进展, 不会卡死(但不一定返回)                                  |
| `AllocSize`                | IntAttr  | 函数的返回值指向的内存块大小可以通过函数的参数计算得出                      |
| `Memory`                   | IntAttr  | 通过位掩码指定函数对内存的访问行为                                          |

### 参数属性(Parameter Attributes)

| 属性名                  | 类型     | 说明                                                   |
| ----------------------- | -------- | ------------------------------------------------------ |
| `ByVal`                 | TypeAttr | 按值传递参数, 传递参数的副本                           |
| `ByRef`                 | TypeAttr | 按引用传递参数, 传递参数的地址                         |
| `InReg`                 | EnumAttr | 强制使用寄存器传递参数                                 |
| `NoAlias`               | EnumAttr | 参数指针不会与其他指针别名                             |
| `NoCapture`             | EnumAttr | 函数不会保存指针的副本供函数返回后使用                 |
| `NonNull`               | EnumAttr | 参数指针不会为空                                       |
| `Dereferenceable`       | IntAttr  | 参数指针至少可解引用指定字节数                         |
| `DereferenceableOrNull` | IntAttr  | 参数指针为空或至少可解引用指定字节数                   |
| `Returned`              | EnumAttr | 返回值总是等于该参数                                   |
| `ImmArg`                | EnumAttr | 参数必须是一个常量                                     |
| `NoUndef`               | EnumAttr | 参数不能是未定义值                                     |
| `SExt`                  | EnumAttr | 参数在调用前进行符号扩展                               |
| `ZExt`                  | EnumAttr | 参数在调用前进行零扩展                                 |
| `StructRet`             | TypeAttr | 参数为返回结构体的隐藏指针                             |
| `InAlloca`              | TypeAttr | 参数内存由调用者在栈上分配, 被调用函数直接使用这块内存 |
| `Preallocated`          | TypeAttr | 参数指针指向预分配的内存(和 `InAlloca` 类似)           |
| `ReadNone`              | EnumAttr | 不会读取或写入任何内存                                 |
| `ReadOnly`              | EnumAttr | 只读取内存, 不写入内存                                 |
| `WriteOnly`             | EnumAttr | 只写入内存, 不读取内存                                 |
| `ElementType`           | TypeAttr | 指定指针参数所指向的实际元素类型                       |

### 返回值属性(Return Attributes)

| 属性名                  | 类型     | 说明                                 |
| ----------------------- | -------- | ------------------------------------ |
| `NoAlias`               | EnumAttr | 返回指针不会与其他指针别名           |
| `NonNull`               | EnumAttr | 返回指针不会为空                     |
| `Dereferenceable`       | IntAttr  | 返回指针至少可解引用指定字节数       |
| `DereferenceableOrNull` | IntAttr  | 返回指针为空或至少可解引用指定字节数 |
| `NoUndef`               | EnumAttr | 返回值不能是未定义值                 |
| `SExt`                  | EnumAttr | 返回值进行符号扩展                   |
| `ZExt`                  | EnumAttr | 返回值进行零扩展                     |

### 其他属性

| 属性名      | 类型              | 说明                       |
| ----------- | ----------------- | -------------------------- |
| `Alignment` | IntAttr           | 指定参数或返回值的对齐要求 |
| `Range`     | ConstantRangeAttr | 指定参数或返回值的取值范围 |

## 类型别名分析(Type Alias Analysis)

`llvm` 使用类型别名分析 (TBAA) 来帮助优化器理解不同指针类型之间的别名关系. 通过为内存访问指令添加 TBAA 元数据, 可以告诉优化器哪些内存访问不会相互影响, 从而允许包括内存访问重排序在内的更激进优化. 例如:

```c
struct A {
    int x;
};
struct B {
    float y;
};
void foo(struct A* a, struct B* b) {
    a->x = 42;      // 访问类型为 struct A
    b->y = 3.14f;   // 访问类型为 struct B
}
```

对于每次内存访问, 可以通过一个元组来描述其 TBAA 信息, 即 (根类型, 访问类型, 偏移量). 在上面的例子中, `a->x` 的 TBAA 信息为 `(struct A, int, 0)`, `b->y` 的 TBAA 信息为 `(struct B, float, 0)`. 因为 `struct A` 和 `struct B` 都是聚合类型, 因此他们的 TBAA 信息的父节点分别是 `(int, 0)` 和 `(float, 0)`, 这两个父节点没有共同的祖先(因为 c 语言不允许 `int` 和 `float` 的隐式转换), 因此优化器可以确定这两个内存访问不会相互影响, 可以安全地重排序它们.

备注: 因为 `Rust` 中本身的引用类型具有更严格的别名规则, 因此 `Rust` 不使用 TBAA.

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

`llvm` 提供了 `lifetime.start` 和 `lifetime.end` 两条指令, 用于标记栈上对象的生命周期范围. 这些指令告诉优化器在对象的生命周期之外, 该内存区域不会被使用, 从而允许优化器进行更激进的内存优化, 如寄存器分配和内存重用. 相关接口的使用方法:

| 指令                                                           | 说明                                                   |
| -------------------------------------------------------------- | ------------------------------------------------------ |
| `@llvm.lifetime.start.p0(i64 immarg size, ptr nocapture addr)` | 标记从 `addr` 开始的 `size` 字节内存区域的生命周期开始 |
| `@llvm.lifetime.end.p0(i64 immarg size, ptr nocapture addr)`   | 标记从 `addr` 开始的 `size` 字节内存区域的生命周期结束 |

## 调试元数据(Debug Metadata)

暂缺

## 严格浮点(Strict Floating Point)

`llvm` 支持严格浮点运算语义, 通过为函数或模块添加 `StrictFP` 属性, 可以确保浮点运算遵循 IEEE 标准, 禁止某些优化(如重新排序浮点运算) 以保证结果的可预测性和一致性. 例如:

```c
float a = 0.1;
float b = 0.2;
float c = a + b; // 在严格浮点模式下, c 的值不能被优化为 0.3f, 必须进行实际的浮点加法运算.
```

## 内建函数(Builtin Functions)

`llvm` 提供了一些内建函数 (builtin functions), `llvm` 理解这些函数的语义, 副作用等, 优化器可以基于这些信息进行更好的优化. 常见的内建函数包括:

- `memcpy`, `memset`, `strlen`, `strcmp` 等内存操作函数
- `malloc`, `free` 等内存分配函数
- `sin`, `cos`, `sqrt` 等数学函数
- `llvm.expect` 用于提供分支预测信息

## 保护机制(Protection Mechanism)

### 栈保护(Stack Protection)

`llvm` 提供了三种栈保护机制:

- 栈保护/栈金丝雀: 在函数入口处插入一个随机生成的金丝雀值, 在函数返回前检查该值是否被篡改, 以检测栈溢出攻击.
- 强栈保护: 栈保护的增强版本, 采用更激进的保护措施
- 安全栈: 通过将敏感数据存储在独立的安全栈中, 防止栈溢出攻击访问这些数据.

### 控制流完整性(Control Flow Integrity)

`llvm` 支持控制流完整性 (CFI), 通过在间接调用和跳转处插入检查, 确保程序的控制流只能按照预定义的合法路径进行, 防止控制流劫持攻击.
