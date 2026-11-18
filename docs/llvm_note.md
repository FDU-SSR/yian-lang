# 笔记

## 目标三元组与 data layout

模块上必须**成对**写目标三元组与配套的 data layout: 类型的具体布局(TargetData)、字段偏移、
对齐填充都由 data layout 决定, 而中端优化 pass 与后端代码生成必须看到同一份。

本编译器的目标固定为 `x86_64-unknown-linux-gnu`, 常量与落盘位置:

- `python/yianc/codegen/llvm/base/module.py` 的 `TARGET_TRIPLE` / `TARGET_DATA_LAYOUT` 与
  `apply_target(module)`;
- `python/yianc/codegen/llvm/pipeline/translator.py` 与 `python/yianc/target_layout.py` 的
  `type_size_provider`(编译期布局查询用的临时模块)都调用 `apply_target`;
- `python/yianc/codegen/llvm/pipeline/emit.py` 在跑优化管线前再兜一道: 解析出的模块若没有
  data layout, 就用目标机的 `target_data` 补上。

优化器与后端必须使用一致布局。缺少 data layout 时，优化器计算的字段偏移与目标机布局可能不一致，
因此目标信息在类型布局查询和目标文件发射两个入口都明确设置。

## 目标机与代码模型

`python/yianc/codegen/llvm/pipeline/emit.py` 由目标三元组创建目标机: `Target.from_triple(...)` 之后调用
`create_target_machine(reloc="pic", codemodel="small", opt=<O>)`。code model 必须显式给出:
llvmlite 的默认值是 `jitdefault`, 在 64 位平台上等于 `large`, 后端于是不采用 ±2 GiB 的 PC
相对寻址, 把每个函数地址与全局地址都经 `movabs` 物化进寄存器, 内部调用被编译成
`call *%reg`。本编译产物是单个可执行文件或目标文件, 代码与静态数据都远小于 2 GiB, `small`
与 clang 的默认一致: 内部调用是直接 `call`, 寻址是 RIP 相对。链接镜像若真的超过 ±2 GiB,
失败方式是链接期重定位溢出。

## 指针表示（opaque pointer）

数据指针在 LLVM 层统一是 opaque pointer `ptr`, 不带 pointee; pointee 信息只存在于 llvmlite 的
`ir` 层(`ir.PointerType(pointee)` 得到 `T*`)。本编译器只在**函数指针**上保留有型指针, 其余指针
构造都返回 `LLTypeCtx.ptr_type`(`ir.PointerType()`), 见 `codegen/llvm/base/types.py`。

`MemoryEmitter` 以 YIAN 类型 ID 决定 load/store 的内存类型，并显式指定 `load` 的 `typ`。
元素与聚合 GEP 先把地址重新定型为 opaque pointer，再指定 `source_etype`；步进由使用点的
元素类型决定，不依赖地址在 llvmlite 中残留的 pointee 信息。

`function/core.py` 的 `PrimitiveIR.bitcast` 在有型指针转 opaque pointer 时直接构造
`CastInstr`，保证 llvmlite 侧重新定型；其他情况使用 `IRBuilder.bitcast`。
函数指针保留 llvmlite 调用所需的函数签名；模块文本统一打印 `ptr`。

指针构造不为取 pointee 而物化被指类型: `__handle_pointer` / `__handle_ref` / `__handle_slice`
只看向 `ptr_type`, struct/enum 的 body 在 `__get_raw_type` 里一次填好。

发射文本由 `codegen/llvm/base/module.py` 关闭 llvmlite 的有型指针打印
(`ir.types.ir_layer_typed_pointers_enabled = False`), 因此模块里所有指针都是 `ptr`(`alloca`、
全局变量也一样)。该开关只影响类型打印: llvmlite 侧的类型判定不变, 生成的机器码也不变
。LLVM 层的类型语义使用 opaque pointer；目标布局仍由 data layout 决定。

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

本编译器的 `base/module.py::LLModule.declare` 把每个非入口函数标为 `internal`; 程序入口发射为
`__yian_main`, 保持 `external`, 由 C 运行时包装的 `main` 调用。整个程序编译进同一个 module、
交付物是单个可执行文件, 因此 YIAN 函数不构成对外 ABI。名字以 `index.` 开头的索引/检查辅助
另加 `alwaysinline`, 使其在胖指针表示下体量变大后仍能内联。

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

本编译器按事实登记这些标注, 不把它们当作优化手段:

- `base/intrinsics.py` 声明的外部函数一律加 `nounwind`, 因为 libc 的分配与随机数入口失败时
  返回错误值而不抛异常。
- `base/module.py` 声明的运行时入口 `__yian_runtime_fail` / `__yian_panic` 加 `cold`、`noreturn`、
  `nounwind`; 堆池与锁表入口 `__secl_pool_alloc` / `__secl_pool_alloc_class` /
  `__secl_pool_release` / `__secl_lock_bump_take` / `__secl_lock_release` 加 `nounwind`。
- `llvm.memcpy.p0.p0.i64` 的两个指针实参加 `noalias`, `isvolatile` 实参加 `immarg`。

据此 LLVM 的 FunctionAttrs 能为调用这些入口的 YIAN 函数推出 `nounwind`。

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
其帧锁机制在 `pointer/safety.py` 中实现。
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

本编译器(`python/yianc/codegen/llvm/base/intrinsics.py`)声明并使用 C 库的 `malloc`、`realloc`、
`free`、`strlen`、`rand`, 以及 LLVM 内建 `llvm.memcpy.p0.p0.i64`、`llvm.memset.p0.i64`。
非单字节元素的重复填充使用按元素类型特化的 `llvm.experimental.memset.pattern`；
YIAN 的 `@memcpy` 下降到对应内建操作。

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
