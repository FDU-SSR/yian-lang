# C ABI FFI 设计草案

本文描述候选机制，示例不代表现有语法。FFI 在 YIAN ABI 与 C ABI 之间建立明确的转换层：普通代码只调用
具有 YIAN 签名的函数，C 类型、原生地址和 C API 调用留在受信任的 FFI 实现内。LLVM C API 是该机制的
主要使用场景；外部内存的零拷贝接管不是使用 LLVM C API 的前提。

## 语法设计

### 函数与外部声明

- `ffi` 标注函数或方法的实现权限，不改变函数的调用约定；调用它仍使用 YIAN ABI。编译器分别生成
  raw/fat 模式下的 YIAN 值表示。
- `pub ffi fn` 的公开签名只包含普通 YIAN 类型，可供普通代码调用。私有 `ffi fn` 可以在签名中使用
  C 类型，供同一模块的 FFI 实现复用；普通函数不能调用或取得这类函数的函数值。
- `pub(ffi) ffi fn` 是仅向已获 FFI 权限的代码公开的辅助接口，可以在签名中包含 C 类型。普通 YIAN
  代码不能导入、调用或取得它的函数值。它使跨包复用 C 字符串复制和句柄表操作不必公开原生地址。
- `extern "C"` 声明外部符号及其真实 C ABI 签名。声明没有 YIAN 函数体，不使用 YIAN 名称修饰，默认
  私有，并且只能由同一模块的 `ffi fn` 直接调用。受限导出形式适用下一条规则；外部函数不能作为
  普通 YIAN 函数值传递。
- `pub(ffi) opaque type` 与 `pub(ffi) extern "C"` 只向已获 FFI 权限的模块导出 C 不完整类型及
  外部函数声明。导入方仍只能在 `ffi fn` 中直接调用外部函数；普通代码既不能导入声明，也不能取得
  外部函数值。相同 LLVM C handle 类型和目标机声明因而可以在绑定包的多个模块之间共享。

例如，LLVM 的输出参数经类型化地址传给 C API，公开函数只返回 YIAN 数值：

```yian
extern "C" {
    fn LLVMGetVersion(major: cptr<c_uint>, minor: cptr<c_uint>, patch: cptr<c_uint>);
}

pub ffi fn llvm_version() -> (u32, u32, u32) {
    let major: c_uint = c_uint(0);
    let minor: c_uint = c_uint(0);
    let patch: c_uint = c_uint(0);
    LLVMGetVersion(@ffi_addr(&major), @ffi_addr(&minor), @ffi_addr(&patch));
    return (u32(major), u32(minor), u32(patch));
}
```

`defer` 覆盖普通返回路径；运行时安全失败会终止进程，不执行栈展开或延迟清理。

### C 类型与原生指针

C ABI 类型有明确的使用范围：

1. 外部函数签名只使用 C ABI 标量、`void`、`cptr<T>` 和受支持的固定宽度 C 类型。`c_int`、`c_uint`、
   `c_size`、`c_char` 等按目标平台定义；LLVM 的 `LLVMBool` 对应 `c_int`，不对应 YIAN `bool`。
2. `opaque type` 表示 C 的不完整类型，只能经 `cptr<T>` 使用。
3. C 类型可以存在于 `ffi fn` 的局部变量、私有辅助函数签名，以及公开 YIAN 结构体的私有字段中；
   不能进入公开字段或供普通代码调用的函数签名。公开结构体可以封装私有 `cptr<T>`，由 `pub ffi fn`
   方法提供普通 YIAN 类型的接口。

`cptr<T>` 在 raw/fat 两种模式下都是一个原生指针宽度，采用相同的 C ABI。它可以为空，不携带 YIAN
边界或生命周期元数据，也不隐式转换成 `T*`、`T&`、切片、字符串或 trait object。在 `ffi fn`
内对 C 标量取址可得到相应的 `cptr<T>`；原生指针运算仅限 FFI 实现。

### 受限转换操作

FFI 内建操作提供语义明确的地址提取与复制能力，不公开胖指针的锁、键、索引或大小字段：

- `@ffi_addr(value: T&) -> cptr<T>` 在检查 YIAN 来源引用后取得对象地址。`T` 可以是 C 标量或
  `cptr<U>`，因此 `char**` 等输出参数可由局部 `cptr<U>` 的地址表达。该操作不改变对象布局。
- `@ffi_parts(value: T[]) -> (cptr<T>, u64)` 返回连续元素地址和元素个数；`str` 版本返回
  `(cptr<u8>, u64)`，长度单位是字节。两者都不添加 NUL 终止符。
- `@ffi_null<T>() -> cptr<T>` 产生 C 空指针；`cptr<T>` 支持同类型比较。C 指针不隐式转换为整数。
- `@ffi_ptr_cast<U>(ptr: cptr<T>) -> cptr<U>` 显式改变 C 指针的静态指向类型，不改变地址，也不建立
  有效内存、对齐或对象布局保证。源和目标都必须是 `cptr`，不能借此构造 YIAN 指针或引用。
- `@ffi_copy_from(dst: u8[], src: cptr<u8>, count: u64)` 和
  `@ffi_copy_to(dst: cptr<u8>, src: u8[], count: u64)` 检查 YIAN 一侧的范围，C 一侧的可访问范围由
  FFI 实现保证。`count == 0` 不访问任一指针。

`cptr<T>` 的加减只在 `ffi fn` 内可用，并要求 `T` 是有确定 C ABI 大小的完整类型；对 `cptr<u8>`
加偏移以字节计数。运算不附带 YIAN 边界检查，FFI 实现负责外部范围和整数溢出检查。`c_size`、`c_int` 等 C 标量与
YIAN 数值之间使用显式转换；变窄转换由 FFI 实现做往返检查。C 枚举值由绑定按头文件定义为私有常量，
不依赖 YIAN `bool` 或平台默认整数宽度。

这组操作有两条边界：

- `ffi` 不自动开放 `@bitcast`、`@assume_init`、`@undef` 等现有受限原语。嵌套闭包及被调用的普通
  函数也不继承调用方的 FFI 权限。
- 源代码不能通过 `@ffi_pack_fat` 一类操作自行填写胖指针字段。C 地址若要变成普通 YIAN 代码可用的
  指针或视图，运行时必须先建立有效的来源、范围和生命周期记录；拼接几个数值不能产生有效指针。

### 权限声明

1. 包在 `package.anx` 的 `[package]` 中显式声明 FFI 能力，例如 `ffi = true`；直接调用 `yianc`
   时使用对应的显式允许选项。文件名和包名不授予权限。
2. 构建工具向依赖方展示引入的 FFI 包。只有获准的包可以使用 `ffi` 标注、C 类型和 FFI 专用操作。
3. 公开的 `ffi fn` 是包作者对封装正确性的信任声明。编译器不能仅凭 YIAN 签名证明 C 函数安全；
   无法满足安全契约的原始操作保持私有。

## 使用方式

### 传入 C 的参数

FFI 实现按目标 C API 的契约准备参数：

1. C API 接收地址与长度时，对连续的 YIAN 字节数据或 `str` 使用 `@ffi_parts`，分别传递数据地址和
   字节长度。
2. C API 要求 NUL 结尾字符串时，使用 `std.ffi.c_string.c_string_bytes` 创建独立的 `Vec<u8>`。
   函数以 `Result<Vec<u8>, u64>` 返回缓冲区或第一个内部 NUL 的字节下标；FFI 实现在 C 调用结束后
   显式释放缓冲区。YIAN `str` 本身不是 C 字符串。
3. C 仅在调用期间使用 YIAN 地址时，保证对象在整个调用期间有效。C 若保存该地址，临时借用不够，
   数据必须独立存活到 C 不再使用为止。

`@ffi_addr` 只提供地址，不保证 YIAN 对象具有 C 结构体布局；C 不能据此把任意 YIAN 对象按另一种
类型解释。

### FFI 库接口

通用封送与原生资源管理分为不同层次：

1. **普通 YIAN 接口**：NUL 字符串构造、长度检查和错误类型只处理 YIAN 值，不要求 FFI 权限。
   `c_string_bytes` 是这一层的接口；返回的缓冲区服从 YIAN 的显式 `Drop` 规则。
2. **受信封送接口**：独立的 `ffi` 包通过 `pub(ffi) ffi fn` 提供
   `copy_c_bytes(cptr<u8>, u64) -> Vec<u8>` 和
   `copy_c_string(cptr<c_char>, u64) -> (Vec<u8>, bool)`。前者按给定长度复制，后者在读取上限内
   寻找 NUL，并用布尔值报告截断；空指针产生空字节序列。C 地址有效性由调用方按 C API 契约保证。
   `c_size` 等标量的可逆转换由绑定中的私有辅助函数处理。
3. **资源接口**：绑定包的公开对象可以在私有字段中保存 C 指针，并以显式方法关闭资源。需要检测
   复制句柄后的重复关闭或失效访问时，绑定包可以在私有句柄表中保存 C 指针；句柄表不是 FFI 的前置能力。

LLVM 绑定通过结构化 C API 构造类型、常量、函数、基本块和指令，不要求先序列化 LLVM 文本 IR。
`llvm.object` 向普通代码提供 `Module`、`Builder`、`Function` 和 `Global`；`llvm.ir` 提供作为方法
参数和返回值使用的 `IrType`、`IrValue`、`IrBlock` 及操作枚举。`Module` 拥有 context、module、
builder 和目标机资源；`Builder`、`Function`、`Global` 及 IR 类型、值、基本块是非拥有视图。
`Module.new` 创建模块，`module.int_type`、
`module.add_function`、`function.append_block`、`builder.position_at_end`、`builder.ret` 构造 IR，
`module.ir`、`module.bitcode`、`module.object` 返回独立字节缓冲区。调用方显式执行 `module.close()`，
并保证所有视图只在其存活期间使用。同一模块返回的 `Builder` 视图共享插入位置。
`llvm.ir` 中带 C 句柄的函数接口仅向 FFI 实现开放；普通方法的
签名不包含 C 类型。构造函数类型和调用参数时，绑定用 `Vec<cptr<...>>` 保存临时 C 指针数组，再以
`@ffi_parts` 取得连续地址；这不要求暴露胖指针字段。

### 处理 C 的返回结果

返回结果按来源与所有权选择处理方式：

1. **Opaque handle**：Context、Module 等对象的 C 指针由公开 YIAN 对象的私有字段或可选的私有
   句柄表保存。普通方法不返回 `cptr<T>`；调用者遵守对应 C API 的所有权和失效规则。
2. **C 持有的临时缓冲区**：保持为 `cptr<T>`，仅在有效期内于 `ffi fn` 中读取。需要向普通 YIAN
   代码返回内容时，复制到 YIAN 存储。
3. **转移所有权的字符串或字节块**：分配 YIAN 存储并复制数据，然后调用该 C API 指定的释放函数。
   LLVM 消息使用 `LLVMDisposeMessage`。
4. **需要零拷贝接管的 C 块**：只有运行时能够登记完整分配范围、身份和释放方式时，才建立受管理的
   YIAN 视图。零拷贝接管不是调用 LLVM C API 的必要条件。

### 句柄生命周期

跨多次调用存活的 C 对象遵守绑定包公开的所有权约定：

1. 拥有资源的对象显式关闭一次；非拥有的类型、值、基本块和 builder 视图不单独释放它们所指向的
   LLVM 对象。关闭父对象会使全部子视图失效。
2. YIAN 的赋值、传参和返回采用浅复制。复制拥有对象只会复制句柄，不会复制 C 资源；调用者负责
   避免重复关闭，并在关闭后停止使用所有副本与子视图。`Drop` 不会自动执行。
3. raw/fat 模式都不对 C 资源提供自动生命周期检查。若绑定包希望把重复关闭或失效访问变成可诊断
   错误，可以额外使用带槽号、代际号的私有句柄表；这是一层可选保障，不改变基本所有权约定。

### 数据表示约束

- 字节复制不等于构造任意 YIAN `T`。外部数据还须满足目标类型的布局和初始化规则；带私有布局的
  结构体、包含 YIAN 胖指针的对象，不能仅凭一组 C 字节成为有效值。
- C 指针可能指向静态区域、对象内部或由另一对象管理的存储。只有 C API 明确转移完整分配块所有权，
  才能考虑将其登记为外部拥有的分配。

## 底层设计

### 编译管线与类型检查

编译器在 AST、HIR、CFG 和 LLVM 阶段携带 `ffi` 权限、外部符号、C 调用约定及 ABI 类型信息。
各阶段的职责如下：

1. 类型检查拒绝 C 类型进入普通公开接口、普通函数调用外部符号或 `pub(ffi)` 接口、未经许可使用 FFI
   内建操作，以及 YIAN 指针和 `cptr<T>` 之间的隐式转换。私有和 `pub(ffi)` 辅助函数的 C 类型签名
   只能由获准的 `ffi fn` 使用。
2. 顶层常量和 `comptime if` 的编译期求值不执行外部调用。
3. LLVM lowering 将 `ffi fn` 生成为普通 YIAN 函数，将 `extern "C"` 调用生成为 C ABI 调用。fat
   元数据不跨越 C 调用边界。

编译器不验证手写外部声明是否与 C 头文件一致；绑定实现负责核对真实签名。

### 地址与复制检查

- YIAN→C 地址提取先执行适用的存活与范围检查，再传递原生数据地址。C 是否保存、释放或越界访问该
  地址，不能由调用约定或胖指针检查推断。
- `@ffi_addr(&local)` 保证被取址的局部变量在 C 调用期间保持可寻址。C 若在调用返回后保存该地址，
  FFI 实现必须为其提供独立的长期存储；局部变量地址不能直接充当持久 C handle。
- `@ffi_copy_from` 和 `@ffi_copy_to` 检查 YIAN 一侧的范围及长度算术。外部地址的有效性、对齐和访问
  权限由 FFI 实现保证。
- raw/fat 模式遵守同一 API 契约；raw 模式没有 fat 模式的运行时检查。

### 外部分配登记

fat 模式的普通堆块具有 YIAN 块头、锁表身份和专用池释放路径。任意 C 地址不具备这些条件，不能
直接拼装成堆胖指针或交给普通堆释放路径。外部分配采用独立的运行时资源记录：

1. **登记**：记录完整分配基址、字节范围、对齐、初始化状态、身份代际号和该 C API 指定的释放函数。
   未转移所有权的地址不进入这类记录。
2. **建立视图**：从登记记录生成 YIAN 视图，视图继承其身份。检查外部视图时不假定存在 YIAN 块头
   或普通堆的地址窗口布局。
3. **释放**：先使关联视图失效，再调用登记的 C 释放函数。`del` 或专用释放操作按分配种类选择路径，
   不把外部基址交给 YIAN 池分配器。
4. **扩容**：若外部分配没有可用的扩容函数，不承诺原地 `realloc`；需要扩容时分配新块、复制内容，
   再按原 C API 释放旧块。

### 链接与支持范围

- 原生库名称、搜索路径和运行时加载要求由 `yianc` 参数与 `anx` 包配置提供；非可执行目标保留外部
  符号，供最终链接。构建工具验证包声明的 FFI 权限和原生依赖字段，不把未知字段静默当作已生效的
  权限或链接配置。
- LLVM 包声明所需 LLVM 主版本和链接组件。构建工具从同一份 LLVM 安装读取版本、库路径、组件库及
  系统库参数；`llvm-config` 是可用的参数来源，但包清单不执行任意构建脚本。链接器发现的 LLVM 主
  版本与绑定目标不一致时构建失败。

  包内受控的薄 C 包装由 `[native.llvm].sources` 列出相对包根目录的 `.c` 文件。构建工具拒绝绝对
  路径、目录穿越和非 C 源码，使用所选 LLVM 安装的头文件及 C 编译器编译，再与同一安装的 LLVM
  组件链接。该字段不执行任意脚本；仅用于 `static inline`、宏等没有可链接 C 符号的 API。

  ```toml
  [package]
  ffi = true

  [native.llvm]
  major = 22
  components = ["core", "analysis", "bitwriter", "target", "nativecodegen", "passes"]
  sources = ["native/target.c"]
  ```

  `[native.llvm]` 是 LLVM 工具链依赖声明，不把 `llvm-config` 的可执行路径或任意 shell 命令写进包。
- LLVM 绑定使用与编译器一致的 LLVM 工具链头文件和链接参数，覆盖句柄创建与销毁、IR 构造、目标
  布局查询、优化和目标文件发射。C 宏、`static inline` 函数和缺少导出符号的接口由版本匹配的薄 C
  包装提供。
- 支持范围不包含可变参数、C 结构体按值传递、回调、C++ ABI、跨边界异常或 C 线程回调 YIAN。
