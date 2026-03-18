# yian 编译器文档

## 基础设施

见[utils](../../cmd/compiler/utils)文件夹与[unit_data](../../cmd/compiler/unit_data.py)

此外, 在[init.py](../../cmd/compiler/utils/__init__.py)中也包含一些通用的工具函数, 如 `is_available`, `is_empty` 等.

### [错误管理](../../cmd/compiler/utils/errors)

主要包括错误的定义, 以及如何报告错误

- [yian error](../../cmd/compiler/utils/errors/yian_error.py): 定义了编译器错误的基类 `YianError` 以及各种具体的错误类型, 如语法错误, 语义错误等. 使用时只需要在发生错误的地方 **raise** 对应的错误类即可. 编译器将通过 **try-except** 机制捕获这些错误并进行报告.
- [error reporter](../../cmd/compiler/utils/errors/error_reporter.py): 定义了错误报告器 `ErrorReporter` 类, 负责收集和格式化错误信息, 并将其输出到控制台或日志文件中. 使用 **try-except** 块捕获 `YianError` 异常后, 可以调用 `ErrorReporter` 的方法来报告错误.

### [IR](../../cmd/compiler/utils/IR)

主要包括中间表示(IR)所需要的基础设施

- [gir](../../cmd/compiler/utils/IR/gir.py): 基本是 lian 输出的 GIR 的一比一映射, 但是使用 Python 类来表示各种 GIR 结构, 方便后续的处理. 最大的特点是**将 GIR 节点之间组织为了图结构**, 方便进行指令的复制, 插入, 删除等操作.
- [指令元数据](../../cmd/compiler/utils/IR/meta.py): 定义所有指令都需要包含的元数据, 包括 `stmt_id`, `parent_stmt_id`, 指令在源代码中的位置等信息.
- [运算符](../../cmd/compiler/utils/IR/operator.py): 定义了所有支持的运算符, 注意, 这里的运算符是指源代码中的运算符, 而不是实际运算, 例如乘法和解引用都是 `Star` 运算符.
- [Symbol](../../cmd/compiler/utils/IR/symbol.py): 定义了各种符号类型, 如 `Function`, `Method`, `VariableSymbol`, `CustomType` 等. 这些符号通常对应源代码中的声明, 包含 `stmt_id` 和 `type_id`.
- [TypedValue](../../cmd/compiler/utils/IR/typed_value.py): 定义了带有类型信息的数值或变量, 如 `IntegerLiteral`, `Variable` 等. 它们通常作为指令的操作数.
- [cgir](../../cmd/compiler/utils/IR/cgir.py): Checked GIR 的定义, 是比 GIR 包含更丰富语义信息的中间表示. 例如类型信息, 符号表引用等. 会裁剪掉一些不生成代码的, 仅描述元数据的指令.

#### GIR 与 CGIR 的关系

GIR 是编译器前端生成的中间表示, 主要用于描述源代码的结构和基本语义. 然而, GIR 本身并不包含足够的类型信息和符号引用, 因此将其转换为拥有更丰富语义信息, 可操作性更强的 CGIR 是编译器中端的主要任务之一.

相比于 GIR, CGIR 在内容上的区别:

- **类型信息**: CGIR 中每条语句中的类型信息使用 `TypeId` 来表示, 方便类型检查与推导.
- **符号引用**: CGIR 中的符号使用符号表中的 id 来引用, 方便符号的查找与管理.
- **专注于实际代码**: CGIR 仅保留那些实际会生成代码的指令, 移除了全局变量, import, impl 等不生成代码的指令.

在组织形式上, CGIR 与 GIR 都是基于图结构组织的, CGIR 继承了 GIR 的图结构, 但是 CGIR 只有函数/方法体内的指令, 而没有顶层的指令.

### [类型系统](../../cmd/compiler/utils/ty)

主要包括类型的定义和类型检查相关的功能

- [类型](../../cmd/compiler/utils/ty/yian_types.py): 定义了编译器支持的所有类型, 包括基本类型(如整数, 浮点数, 布尔值), 复合类型(如数组, 结构体), 函数类型等. 每个类型都有对应的类, 并实现了类型相关的方法.
- [type space](../../cmd/compiler/utils/ty/space.py): 定义了类型空间 `TypeSpace` 类, 用于创建, 存储, 查找类型. 类型空间支持类型的唯一化, 确保每个类型在空间中只有一个实例. **泛型的实例化** 也是通过类型空间来完成的.
- [impl](../../cmd/compiler/utils/ty/impl.py): 用于描述 `impl` 块的数据结构
- [utils](../../cmd/compiler/utils/ty/utils.py): 包含类型系统的工具函数, 最重要的是 `type_unification`, 用于在泛型实例化时进行类型参数的推导与统一.
- [method registry](../../cmd/compiler/utils/ty/method_registry.py): 定义了方法注册表 `MethodRegistry` 类, 用于注册 `impl` 块, 同时负责解析方法调用, 即给定一个接收者类型和方法名, 查找对应的方法定义.

#### 泛型

泛型类型包括

- struct/enum/trait
- 函数/方法

实现这些泛型类型方法可以概括为: **一份模板, 多份实例**. 以下面的 struct 为例:

```text
pub struct Pair<T, U> {
    T field1
    U field2
}
```

它在 `TypeSpace` 中将以以下形式存在:

```text
Pair(StructType)
  |-- StructDef
  |    |-- name: "Pair"
  |    |-- stmt_id: 66
  |    |-- attributes: {pub}
  |    |-- fields
  |    |    |-- private field1: T
  |    |    `-- private field2: U
  |    `-- generics
  |         |-- T
  |         `-- U
  `-- generic_args
       |-- T
       `-- U
```

如果创建了 `Pair<int, float>` 的实例, 则会在 `TypeSpace` 中创建一个新的 `StructType` 实例, 结构如下:

```text
Pair(StructType)
  |-- StructDef
  |    |-- name: "Pair"
  |    |-- stmt_id: 66
  |    |-- attributes: {pub}
  |    |-- fields
  |    |    |-- private field1: T
  |    |    `-- private field2: U
  |    `-- generics
  |         |-- T
  |         `-- U
  `-- generic_args
       |-- int
       `-- float
```

可以看到, 两个 `StructType` 实例共享同一个 `StructDef`, 实际上, 两个 `StructType` 实例的 `StructDef` 引用的是同一个对象. 这种设计使得泛型类型的实例化只需要替换掉 `generic_args` 字段即可, 而不需要重新创建或者复制整个类型定义.

其他的泛型类型(如函数/方法)也是类似的设计.

### [UnitData](../../cmd/compiler/unit_data.py)

`UnitData` 类是编译器中用于存储和管理一个编译单元(通常是一个源文件)相关数据的核心类. 它包含了以下主要功能:

- **GIR 存储**: `UnitData` 包含一个 GIR 图, 用于存储该编译单元的中间表示.
- **类型名称解析**: 给定一个类型名称字符串, `UnitData` 能够解析并返回对应的 `Symbol` 实例
- **名称解析**: 给定一个名称字符串及其所在语句, `UnitData` 能够解析并返回对应的 symbol id
- 维护其他 GIR 附带的元数据, 如语句中变量的类型信息等.

## 编译流程

每个 `yian` 源文件对应于编译过程中的一个 `unit`. `unit` 会经过前端, 中端, 后端三个主要阶段的处理, 并最终生成目标代码(LLVM IR). 编译器将 `unit` 中的指令完全遍历一次, 称之为一个**编译轮次**(compilation pass). 每个阶段可能会进行多个编译轮次, 以完成不同的任务.

### 1. 前端

负责将源代码转换为 lian 的中间表示 GIR

#### 1.1 词法 & 语法分析

将源代码传入 tree-sitter 生成的解析器 [yian_lang_linux.so](../../cmd/compiler/frontend/yian_lang_linux.so)

解析器将源代码转换为一个 tree-sitter-python 类型的 AST

[yian_parser](../../cmd/compiler/frontend/yian_parser.py) 接受这个 AST 作为输入，转换成 Lian 系统能够解析的 python dict 形式

#### 1.2 GIR 生成

Lian 接受 yian_parser 输出的 dict 并将其转换为 GIR

### 2. 中端

主要负责类型检查, 以及丰富 GIR 的语义信息. 给定 lian 的分析结果.

中端会经历多个PASS, 每个PASS的内部, 每个 `unit` 都会被处理, 处理 `unit` 的顺序无所谓, 因此PASS内部可以**并行**处理多个 `unit`. 但是不同PASS之间可能存在依赖关系, 因此需要**顺序**执行.

以下是中端的主要步骤:

#### 2.1 上下文准备

中端处理开始之前, 需要准备好上下文信息. 中端所有的上下文信息都由 `TypeAnalysisCtx` 类维护, 其职责包括:

- 维护 `TypeSpace`, 用于类型的创建与查找
- 维护 `MethodRegistry`, 用于方法的注册与查找
- 在作用域不断切换过程中, 正确维护各种符号的可见性

每个PASS开始之前, 都需要将 `TypeAnalysisCtx` 传入, 以便在处理过程中访问类型与方法信息. 下面是 `TypeAnalysisCtx` 中部分接口的说明:

- `set_unit_data`, `unset_unit_data`: 设置/清除当前正在处理的 `unit_data`, 以便在处理过程中访问 `unit_data` 中的信息.
- `cgir_init`: 表示开始生成当前 `unit_data` 的 CGIR, 需要初始化一些数据结构.
- `enter_*_scope`, `exit_scope`: 进入/退出不同类型的作用域, 以便正确维护符号的可见性.
- `process_gir_block`, `process_cgir_block`: 根据传入的 handlers 遍历并处理 GIR/CGIR 块中的每条语句.
- `gir_*`: 与 GIR 相关的访问接口.
- `cgir_*`: 与 CGIR 相关的访问接口.
- `ty_*`: 与类型相关的访问接口.
- `symbol_*`: 与符号相关的访问接口.

中端PASS的标准定义由 `TypeAnalysisPass` 类给出, 模板方法 `run` 是PASS的入口, 具体的PASS需要继承该类并实现相应的处理函数.

#### 2.2 PASS1: GIR 转化

将 lian 输出的 GIR 转化为编译器内部使用的 GIR 图结构. 具体转化步骤见[utils](../../cmd/compiler/utils/IR/utils.py)中的 `map_stmts` 函数. 转化完成后还会统计 `max_gir_id`, 用于后面生成新指令.

转化完成之后, 源代码中所有的信息都被收集在了 `UnitData` 中, 后续的处理都将在 `UnitData` 上进行.

#### 2.3 PASS2: 符号收集

由 `SymbolCollector` 完成, 负责收集一个 `unit` 中所有**暴露在顶层**的符号定义, 并将其注册到 `unit` 的符号表中. 同时会在 `TypeSpace` 中为自定义类型, 函数和方法分配类型对象.

**处理内容:**

- **自定义类型**: Struct, Enum, Trait, Type Alias
- **函数与方法**: 包括顶层函数, `impl` 块中的方法, `trait` 定义中的方法
- **变量**: 仅包含全局变量

#### 2.4 PASS3: 导出收集

由 `ExportCollector` 完成, 负责收集一个 `unit` 中所有可导出的(标注为 `pub` 的)符号, 每个 `unit` 将自己的可导出的符号名与对应的 symbol id 的映射维护在 `UnitData` 中

#### 2.5 PASS4: 导入解析

由 `ImportResolver` 完成, 负责解析使用 `import` 导入的符号, 链接到其定义所在的 `UnitData`, 并将其注册到当前 `UnitData` 的符号表中.

每个 `unit` 中可导出的符号都已经在 `PASS3` 中收集完毕, 因此导入解析只需要查找被导入的 `unit` 中的导出信息, 并将符号引用添加到当前 `unit` 的符号表中即可.

#### 2.6 PASS5: 声明解析

由 `DeclScanner` 完成, 负责解析所有声明的**类型信息**, 填充 `TypeSpace` 中已分配的类型对象, 并丰富符号表.

**主要工作:**

- **作用域管理**: 在解析过程中会正确地进入和退出各种作用域 (函数作用域, `impl` 作用域, 自定义类型作用域等), 以处理泛型参数和符号可见性
- **泛型与属性**: 解析函数, 方法, 自定义类型的泛型参数列表和属性 (`attributes`)
- **函数与方法签名**: 解析参数类型, 返回值类型. 注意: **不解析函数体/方法体内部的语句**
- **自定义类型定义**:
  - Struct: 解析字段类型
  - Enum: 解析 Variant 及其 Payload 类型
  - Trait: 解析方法签名
  - Type Alias: 解析被别名的类型
- **全局变量**: 解析全局变量的类型

#### 2.7 PASS6: trait 实现校验

由 `ImplValidator` 完成, 负责校验 `impl` 块中 trait 的实现是否正确, 即对于某个 `impl` 块及其实现的 trait, 校验以下内容:

- trait 中的所有方法, 要么有默认实现, 要么在 `impl` 块中被实现
- 对于需要继承默认实现的方法, 需要在 `impl` 块中新增指向默认实现的 `MethodDecl`
- 对于其他方法, 需要校验方法签名是否匹配

之所以不在 `PASS5` 解析 `impl` 块时进行校验, 是因为被实现的 trait 可能在 `unit` 的后面部分才被定义, 因此需要延迟到此时进行校验

#### 2.8 PASS7: 类型检查

由 `TypeChecker` 完成, 这个PASS比较特殊, 因为它是**逐过程**进行检查的. 具体而言, 从 `main` 函数开始(或者从项目暴露的所有顶层 `pub` 函数开始), 按照调用图的顺序, 逐个函数/方法地进行类型检查. 具体细节:

- 主体为 `worklist algorithm`
- 使用 `DefPoint` 来唯一标记一个调用图中的节点, 它是一个三元组: (unit_id, stmt_id, type_id)
- 每个 `DefPoint` 有一个独立的符号表, 维护自己局部变量类型信息
- 同一个模板函数的不同类型实例化, 视为不同的 `DefPoint`
- 一个函数被取地址时, 等同于被调用, 将其 `DefPoint` 加入待处理列表

对于每个 `DefPoint`, 进行以下处理:

- 对每个指令进行类型推导, 并检查类型是否匹配
- 将语法糖指令转化为更基础的指令, 涉及到使用新指令去替换旧指令
- 生成 CGIR
- 解析 `DefPoint` 内部的调用关系, 这涉及到:
  - 函数调用
  - 方法调用
  - 函数指针(取地址)
  - 泛型实例化

这个阶段之后, 程序的结构从若干个 `unit` 转化为若干个 `DefPoint`, 此后的分析都将在 `DefPoint` 级别进行.

#### 2.9 PASS8: 可见性检查

由 `VisibilityAnalyzer` 完成, 负责检查符号的可见性, 包括:

- 确保涉及到可见性的字段, 方法, 函数等符号的使用符合其可见性规则
- 对于 `pub` 符号, 允许在任何 `unit` 中访问
- 对于非 `pub` 符号, 仅允许在定义它的 `unit` 中访问

#### 2.10 PASS9: 变量有效性分析

由 `VariableAnalyzer` 完成, 负责解析移动语义相关的内容, 包括:

- 收集每个局部变量的: 作用域, 定义点, 使用点的信息
- 确保一个变量被使用时, 该变量是有效的
- 确保一个变量被赋值时, 如果被赋值的变量有效, 先调用其析构函数

对于某一类变量, 在某个语句, 有以下情况:

- 变量一定有效: 此时变量可以使用
- 变量一定无效: 此时变量使用会报错, 并且可以直接赋值
- 变量可能有效也可能无效: 此时变量使用会报错, 需要一个额外的隐藏布尔变量来记录变量的有效性, 以便在变量被赋值时检查其有效性并调用析构函数

#### 2.11 合法性校验

这部分只有一个初步的构想, 大致负责保证: CGIR 中不会出现无法翻译为 LLVM IR 的结构, 例如:

- 一个类型递归包含自身, 导致无法确定其大小
- 类型实现的 trait 中包含未实现的方法
- 类型包含未被实例化的泛型
- 等等

这部分校验是针对 `TypeAnalysisCtx` 中的类型信息进行的全局性校验

### 3. 后端

主要工作是将中端生成的指令与类型信息转化为 LLVM IR. 后端的工作由两大模块组成: `Context` 负责维护生成 LLVM IR 所需的上下文信息, 包括类型映射, 符号映射等; `Translator` 负责将 CGIR 转化为 LLVM IR.

#### 3.1 Context 初始化

在处理每个 `UnitData` 之前, 需要初始化 `Context`, 包括:

- 创建 LLVM 模块
- 引入编译器内置函数
- 将 context 与 `TypeSpace`, `MethodRegistry` 关联起来, 以便在翻译过程中访问类型与方法信息

在翻译过程中, `Context` 的职责:

- 类型映射: 维护 CGIR 类型与 LLVM 类型之间的映射关系, 确保每个类型对应正确的 LLVM 类型
- 符号映射: 维护 CGIR 符号与 LLVM 变量/函数之间的映射关系, 确保每个符号对应正确的 LLVM 变量/函数
- 函数管理: 负责创建和管理 LLVM 函数, 包括函数签名的定义, 函数体的生成等
- 类型大小与对齐: 提供类型大小和对齐信息的查询功能, 以便在生成内存布局时使用
- 全局变量管理: 管理静态生命周期的全局变量的创建与访问

#### 3.2 翻译 CGIR 为 LLVM IR

由 `Translator` 完成, 负责将 CGIR 逐句的翻译为 LLVM IR.

#### 3.3 输出 LLVM IR

将生成的 LLVM 模块输出为 LLVM IR 文件, 供后续的优化与代码生成使用.
