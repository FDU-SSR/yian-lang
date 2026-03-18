# `impl` 块

`impl` 块在 `yian` 语言中可以为某一种**类型**或者**类型模板**增加 `method` 或者 `trait`, 只能出现在顶层作用域中

impl 块分为两种:

- `impl`: 增加 `inherent method`
- `impl for`: 为类型实现 `trait`

目前对于 `impl` 可以包含的目标类型和 `trait` 的限制任在设计中, 但是已经确定以下原则:

1. 用户不能为来自标准库或者第三方库的类型增加 `inherent method`
2. 用户不能为来自标准库或者第三方库的类型实现 `trait`, 除非该 `trait` 是用户自己定义的
3. 用户不能实现来自标准库或者第三方库的 `trait`, 除非是在为用户自己定义的类型实现该 `trait`
4. 来自标准库或者第三方库的泛型类型, 如果使用了**用户自己定义的类型**进行实例化, 则实例化后的类型视为用户自己定义的类型

## 方法

方法(`method`) 是与某个类型相关联的函数, 它们只能通过 `impl` 块来定义, 有以下种类:

1. **固有方法(inherent method)**: 直接定义在某个类型上的方法
2. **特征方法(trait method)**: 定义在某个 `trait` 中, 通过 `impl for` 块为某个类型实现该 `trait` 后, 该类型就拥有了该 `trait` 中定义的所有方法

注意: 不能有重名的固有方法, 同一个 `trait` 中也不能有重名的方法, 但是**除此之外, 重名都是允许的**

根据对于 `self` 的处理方式不同, 方法还可以分为以下几种:

1. **静态方法(static method)**: 不包含 `self` 参数的方法, 可以直接通过类型名调用
2. **实例方法(instance method)**: 包含 `self` 参数的方法, 其中 `self` 参数是指向调用者实例的指针, 因此只能通过实例调用
3. **消耗性方法(consume method)**: 包含 `self` 参数的方法, 并且 `self` 参数是按值传递的, 调用该方法会消耗掉调用者实例, 使其变为不可用状态, 因此只能通过实例调用(语法暂未支持)

标准的方法调用语法例子如下:

```text
// 下面的例子中, MyStruct 是一个结构体类型, MyTrait 是一个 trait 类型, my_struct 是 MyStruct 类型的一个实例
MyStruct.static_method()                                // 调用固有的静态方法
MyStruct.MyTrait.trait_static_method()                  // 调用来自 trait 的静态方法
MyStruct.instance_method(&my_struct)                    // 调用固有的实例方法
MyStruct.MyTrait.trait_instance_method(&my_struct)      // 调用来自 trait 的实例方法
MyStruct.consume_method(my_struct)                      // 调用固有的消耗性方法
MyStruct.MyTrait.trait_consume_method(my_struct)        // 调用来自 trait 的消耗性方法
```

总结一下, 调用方法的标准格式为: `类型名 + trait名(如果有的话) + 方法名 + 传递调用者实例(如果有的话) + 其他参数`

在**不引起歧义**的情况下, 也可以使用下面的简化语法进行调用:

```text
MyStruct.trait_static_method()      // 调用来自 trait 的静态方法
my_struct.instance_method()         // 调用固有的实例方法
my_struct.trait_instance_method()   // 调用来自 trait 的实例方法
my_struct.consume_method()          // 调用固有的消耗性方法
my_struct.trait_consume_method()    // 调用来自 trait 的消耗性方法
```

编译器在遇到这种简化语法时, 将会:

1. 在**固有方法**中查找是否有匹配的方法, 如果找到了, 则调用该方法
2. 在所有 `trait` 的**特征方法**中查找是否有匹配的方法, 如果存在多个匹配的方法, 则报错(此时需要使用完整语法来指定调用哪个 `trait` 的方法), 否则调用该方法

### 方法调用与字段访问的优先级

考虑下面的代码:

```text
a.b()  // a 是变量
```

因为 `.` 与 `()` 的存在, 编译器会将其解析为方法调用 `a.b()`, 此时编译器认为 `b` **是一个方法名**

如果是想要从 `a` 中访问一个名为 `b` 的字段, 并调用之, 则需要使用括号来明确优先级:

```text
(a.b)()  // a 是变量, b 是一个字段, 并且是一个可调用的类型
```

此时由于括号的存在, 编译器会将其优先解析字段访问 `a.b`, 然后再调用该字段

```text
// 当函数指针 b 是结构体变量 a 的一个字段时, 想要调用该函数指针
a.b()  // 错误, 编译器将其解析为方法调用
(a.b)()  // 正确, 编译器将其解析为字段访问, 然后调用该字段
```

### 方法调用与字段访问的优先级

考虑下面的代码:

```text
a.b()  // a 是变量
```

因为 `.` 与 `()` 的存在, 编译器会将其解析为方法调用 `a.b()`, 此时编译器认为 `b` **是一个方法名**

如果是想要从 `a` 中访问一个名为 `b` 的字段, 并调用之, 则需要使用括号来明确优先级:

```text
(a.b)()  // a 是变量, b 是一个字段, 并且是一个可调用的类型
```

此时由于括号的存在, 编译器会将其优先解析字段访问 `a.b`, 然后再调用该字段

```
// 当函数指针 b 是结构体变量 a 的一个字段时, 想要调用该函数指针
a.b()  // 错误, 编译器将其解析为方法调用
(a.b)()  // 正确, 编译器将其解析为字段访问, 然后调用该字段
```


## `impl`

`impl` 块有两种基本格式:

```text
// 普通 impl
impl 类型 {
  [函数定义1]
  [函数定义2]
  ...
}

// 泛型 impl
impl[类型参数列表] 类型模板 {
    [函数定义1]
    [函数定义2]
    ...
}
```

- 类型: 必须是一个确定的类型, 实例化后的类型模板, 例如 `MyStruct<i32, f64>` 也算作确定的类型
- 类型模板: 指的是用到类型参数的类型, 并且必须使用 `impl` 块中的类型参数列表对其进行完全实例化, 例如 `impl<T> MyStruct<T, i32>` 是合法的, 但是 `impl<T> MyStruct<T, U>` 则是不合法的, 因为 `U` 没有在类型参数列表中声明
- `impl` 内部的方法, 仍然可以有自己的类型参数列表, 这些类型参数与 `impl` 块中的类型参数是相互独立的

看下面的例子:

```text
struct ExampleStruct<T, U> {
    T field1
    U field2
}

// 为完全实例化的类型增加方法
impl ExampleStruct<i32, f64> {
    pub fully_instantiated_method() {
        print("This method is for the fully instantiated type ExampleStruct<i32, f64>")
    }
}

// 为部分实例化的类型模板增加方法
impl<T> ExampleStruct<T, f64> {
    pub partially_instantiated_method() {
        print("This method is for the partially instantiated type ExampleStruct<T, i32>")
    }
}

// 为未实例化的类型模板增加方法
impl<T, U> ExampleStruct<T, U> {
    pub uninstantiated_method() {
        print("This method is for the uninstantiated type ExampleStruct<T, U>")
    }
}

ExampleStruct<i32, f64> ex1
ExampleStruct<u32, f64> ex2
ExampleStruct<i32, u32> ex3
```

通过 `.` 调用方法时, 编译器会根据调用者(`ex1`, `ex2`, `ex3`)来与 `impl` 块进行匹配, 选择正确的 `impl` 块中的方法进行调用, 例如:

1. `ex1`: 这个变量的类型是 `ExampleStruct<i32, f64>`, 它与三个 `impl` 块都匹配, 因此它可以调用 `fully_instantiated_method`, `partially_instantiated_method`, 和 `uninstantiated_method`
2. `ex2`: 这个变量的类型是 `ExampleStruct<u32, f64>`, 它只与后两个 `impl` 块匹配, 因此它可以调用 `partially_instantiated_method` 和 `uninstantiated_method`, 但是不能调用 `fully_instantiated_method`
3. `ex3`: 这个变量的类型是 `ExampleStruct<i32, u32>`, 它只与最后一个 `impl` 块匹配, 因此它只能调用 `uninstantiated_method`

## impl for

impl for 块的格式:

```text
impl[类型参数列表] 特征名 for 类型 {
  [函数定义1]
  [函数定义2]
  ...
}
```

- 特征名: 必须是一个已经存在的 `trait` 名, 如果是泛型 `trait`, 则必须使用 `impl for` 块中的类型参数列表对其进行完全实例化
- 类型: 必须是一个确定的类型, 例如 `i32`, `f64*`, `impl<T> SomeTrait for T` 等都是合法的
- 函数定义: 函数签名必须与 trait 中的函数签名一致, 不能包含访问限定符, 它们默认是 `pub`

### 解析规则

对于一个 `impl for` 块, 编译器首先获取以下两个信息:

1. 实例化后的 `trait`
2. 实例化后的 `target`

在块的内部, 对于一个方法签名 `SigA`, 编译器执行如下流程:

1. 在实例化后的 `trait` 中查找是否存在与该方法同名的方法声明, 如果不存在, 则报错, 否则获取其签名 `SigB`
2. 校验 `SigA` 与 `SigB` 是否匹配, 具体而言:
   1. `SigA` 与 `SigB` 的 `receiver`, 参数和返回值必须**类型相容**
   4. `SigA` 与 `SigB` 必须拥有相同长度的类型参数列表
3. 生成一条 `MethodDecl` CGIR 语句, 将其插入到当前 `impl for` 块中

在解析完所有的方法定义后, 还需要找到 `trait` 中有默认实现, 并且没有在 `impl for` 块中被重写的方法, 设其中一个方法签名为 `SigC`:

1. 依据 `SigC` 生成新的签名 `SigD`, 其中:
   1. `SigD` 的 `receiver`, 参数和返回值类型是 `SigC` 经过**类型替换**后的结果
   4. `SigD` 的类型参数列表与 `SigC` 一致
2. 生成一条 `MethodDecl` CGIR 语句, 将其插入到当前 `impl for` 块中, 其 `stmt_id` 需要新生成, `body` 则直接使用 `trait` 中的默认实现

解释:

- **类型相容**: 设 `A` 和 `B` 分别来自于 `trait` 和 `impl for` 块的方法签名, 则 `A` 与 `B` 是类型相容的, 要么 `A` 与 `B` 完全相同, 要么 `A` 和 `B` 分别是 `trait` 和 `target` 的类型
- **类型替换**: 设 `A` 来自于 `trait` 方法签名, 则对 `A` 进行类型替换的过程为: 如果 `A` 是 `trait` 的类型, 则将其替换为 `target`, 否则保持不变

完成上述流程后, 如果还存在 `trait` 中的方法没有在 `impl for` 块中被实现, 则报错

## 条件 `impl`(暂未实现)

某些情况下, 可能希望某个 `impl` 块只在特定条件下才生效, 例如下面的需求:

对于某个类型 `T`, 如果 `T` 可以被拷贝(实现了 `Clone` trait), 那么 `Vec<T>` 就应当也是可以被拷贝的(也实现 `Clone` trait), 否则 `Vec<T>` 就不应当是可以被拷贝的(不实现 `Clone` trait), 想要实现这样的功能, 就只能使用**条件 impl**:

```text
// 语法暂未确定
impl<T> Clone for Vec<T> if T implements Clone {
    Self clone() {
        ...
    }
}
```

在上面的例子中, 只有当 `T` 实现了 `Clone` trait 时, 该 `impl` 块才会生效, 否则 `Vec<T>` 将不会实现 `Clone` trait

以及下面的例子:

如果类型 `T` 可以转换成类型 `U`(为 `T` 实现了 `Into<U>` trait), 那么应该也可以从类型 `T` 创建类型 `U`(为 `U` 实现了 `From<T>` trait), 这两个 trait 应当是等价的, 因此可以使用条件 `impl` 来实现:

```text
// 语法暂未确定
trait Into<U> {
    U into()
}

impl<T, U> From<T> for U if T implements Into<U> {
    U from(T value) {
        return value.into()
    }
}

// 使用方法, 假定有类型 Meter 和类型 Foot
impl Into<Foot> for Meter {
    Foot into() {
        ...
    }
}
// 由于 Meter 实现了 Into<Foot>, 因此 Foot 也自动实现了 From<Meter>

// 将 Meter 转换为 Foot
Meter m
Foot f = m.into()

// 从 Meter 创建 Foot
Meter m2
Foot f2 = Foot.from(m2)
```

正在考虑是否将这个功能加入到语言中, 如果加入, 则需要设计一套合理的语法和语义来支持它

## 方法调用(暂未完全实现)

在有泛型 `impl` 的情况下, 解析方法调用事实上是一件极其复杂的事情, 假设我们有以下的方法调用:

```text
foo.bar(baz)
```

编译器使用以下算法来解析这个方法调用:

### 1. 确定候选项

这一步主要是为了确定**候选方法**

因为方法只可能来自于 `impl` 块, 因此编译器首先需要确定所有可能的 `impl` 块, 假设 `foo` 的类型为 `FooType`, 则有:

#### 1.1 自动解引用

如果 `FooType` 是指针类型, 或者实现了 `Deref` trait, 则编译器会持续对 `FooType` 进行解引用, 直到得到一个无法再解引用的类型为止, 由此可以得到一个**类型序列** $T_0, T_1, T_2, ..., T_n$, 其中 $T_0$ 就是 `FooType`, $T_n$ 是最终无法解引用的类型

#### 1.2 匹配 impl 块

对于**类型序列**中的每一个类型 $T_i$, 编译器会查找所有与 $T_i$ 匹配的 `impl` 块, 具体通过以下规则进行匹配, 一旦序列中某个类型 $T_i$ 找到了匹配的 `impl` 块, 则不会继续对后续的类型 $T_{i+1}, T_{i+2}, ...$ 进行匹配

##### 1.2.1 固有方法匹配

固有方法存在于两处:

1. 无泛型, 并且 `target` 与 $T_i$ 完全一致的 `impl` 块中
2. 有泛型, 并且 `target` 可以与 $T_i$ 进行**一致化**的 `impl` 块中

流程为:

1. 在第一种情况中查找所有名字与 `bar` 一致的方法
   1. 如果不存在, 转下个阶段
   2. 如果存在多个, 报错
   3. 如果存在唯一一个, 则其就是**候选方法**
2. 在第二种情况中查找所有名字与 `bar` 一致的方法
   1. 如果不存在, 转下个阶段
   2. 如果存在多个, 报错
   3. 如果存在唯一一个, 则其就是**候选方法**

##### 1.2.2 trait 方法匹配

trait 方法存在于两处:

1. 无泛型, 并且 `target` 与 $T_i$ 完全一致的 `impl for` 块中
2. 有泛型, 并且 `target` 可以与 $T_i$ 进行**一致化**的 `impl for` 块中

流程为:

1. 在第一种情况中查找所有名字与 `bar` 一致的方法
   1. 如果不存在, 转下个阶段
   2. 如果存在多个, 基于方法的泛型参数, 尝试将方法的**实参类型**与**形参类型**进行一致化
      1. 如果全部失败, 转下个阶段
      2. 如果存在多个成功的一致化结果, 报错
      3. 如果存在唯一一个成功的一致化结果, 则其就是**候选方法**
   3. 如果存在唯一一个, 则其就是**候选方法**
2. 在第二种情况中查找所有名字与 `bar` 一致的方法
   1. 如果不存在, 报错, 因为已经没有下个阶段了
   2. 如果存在多个, 基于 `impl for` 块的泛型参数与方法的泛型参数, 尝试将方法的**实参类型**与**形参类型**进行一致化
      1. 如果全部失败, 报错
      2. 如果存在多个成功的一致化结果, 报错
      3. 如果存在唯一一个成功的一致化结果, 则其就是**候选方法**
   3. 如果存在唯一一个, 则其就是**候选方法**

### 2. 确认方法调用

这一步主要是为了确认**候选方法**是否可以被调用, 执行下列检查:

1. 检查参数个数是否匹配
2. 基于 `impl` 块的泛型参数与方法的泛型参数, 尝试将方法的**实参类型**与**形参类型**进行一致化
   1. 如果失败, 报错
   2. 如果成功, 则记录下泛型参数的一致化结果(一个新的类型), 以便后续生成调用代码时使用
3. 检查访问权限, 如果不可访问, 报错

### 3. 生成调用代码

这一步主要是为了生成最终实际的调用代码, 包括以下工作:

1. 插入对 `foo` 的解引用代码
2. 插入将 `self` 参数(如果有的话)的传递从隐式改为显式的代码
3. 将 `callee` 替换为最终的包含具体类型的版本

## 方法的实例化

观察下面的例子

```text
struct SSS<T> {
    T field
}

trait TTT<T> {
    process(T value)
    handle(i32 number)
    additional<U>(U data)
}

impl<T, U> TTT<U> for SSS<T> {
    process(U value) {
        ...
    }
    handle(i32 number) {
        ...
    }
    additional<V>(V data) {
        ...
    }
}
```

上面三个函数在编译器底层表示中, 泛型参数分别为:

1. `process`: `<T, U>`, 使用来自 `impl for` 块的泛型参数列表
2. `handle`: `<T, U>`, 使用来自 `impl for` 块的泛型参数列表
3. `additional`: `<T, U, V>`, 使用来自 `impl for` 块的泛型参数列表, 以及方法自己的泛型参数

同时, 他们的签名为:

1. `process`: `SSS<T>::process(U value)`
2. `handle`: `SSS<T>::handle(i32 number)`
3. `additional`: `SSS<T>::additional<V>(V data)`

当我们调用这些方法时, 例如:

```text
SSS<i32> var = SSS<i32>{}

var.process("hello")
var.handle(42)
var.additional(3.14)
var.additional<str>("world")
```

下面是解析这些调用时, 方法实例化的过程:

1. `var.process("hello")`
   1. 编译器将 receiver 与参数的类型提取出来: `SSS<i32>` 和 `str`
   2. 构建方程组:
      1. `SSS<T> = SSS<i32>`
      2. `U = str`
   3. 求解方程组, 得到: `T = i32`, `U = str`
   4. 实例化方法, 得到: `SSS<i32>::process<str>(str value)`
   5. 在实例化后的方法中, 泛型实参为 `<i32, str>`
2. `var.handle(42)`
   1. 编译器将 receiver 与参数的类型提取出来: `SSS<i32>` 和 `i32`
   2. 构建方程组:
      1. `SSS<T> = SSS<i32>`
      2. `i32 = i32` (恒等式, 无需求解)
   3. 求解方程组, 得到: `T = i32`
   4. 实例化方法, 得到: `SSS<i32>::handle(i32 number)`
   5. 在实例化后的方法中, 泛型实参为 `<i32, U>`, 其中 `U` 维持原状
3. `var.additional(3.14)`
   1. 编译器将 receiver 与参数的类型提取出来: `SSS<i32>` 和 `f64`
   2. 构建方程组:
      1. `SSS<T> = SSS<i32>`
      2. `V = f64`
   3. 求解方程组, 得到: `T = i32`, `V = f64`
   4. 实例化方法, 得到: `SSS<i32>::additional<f64>(f64 data)`
   5. 在实例化后的方法中, 泛型实参为 `<i32, U, f64>`, 其中 `U` 维持原状
4. `var.additional<str>("world")`
   1. 编译器将 receiver 与参数的类型提取出来: `SSS<i32>` 和 `str`
   2. 构建方程组:
      1. `SSS<T> = SSS<i32>`
      2. `str = str`
      3. `V = str` (来自显式指定的泛型参数)
   3. 求解方程组, 得到: `T = i32`, `V = str`
   4. 实例化方法, 得到: `SSS<i32>::additional<str>(str data)`
   5. 在实例化后的方法中, 泛型实参为 `<i32, U, str>`, 其中 `U` 维持原状
