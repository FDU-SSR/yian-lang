# yian Quick Start

## 1. 先跑通一个最小程序

创建文件 `hello.an`：

```yian
from std.core.io import print
i32 main() {
    print("Hello, yian!\n")
    return 0
}
```

编译：

```bash
./scripts/yian_compiler.py hello.an
```

运行:
```bash
./tests/yian_workspace/bin/out
```

你会看到输出：

```text
Hello, yian!
```

### 1.1 语句风格

- 大多数语句行尾不用 `;`。
- `for` 头部里仍然使用 `;` 分隔三个部分。
- 代码块用 `{ ... }`。

---

## 2. 你会反复用到的基础语法

## 2.1 变量、类型、赋值

`yian` 最常见写法是“类型在前，变量名在后”：

```yian
i32 a = 10
f64 b = 3.14
bool flag = true
char ch = 'A'
```

也可以先声明后赋值：

```yian
i32 x
x = 42
```

### 2.2 常量与字面量

```yian
i32 dec = 42
i32 hex = 0x2A
i32 oct = 0o52
i32 bin = 0b101010
u64 big = 1_000_000
u8 c1 = b'A'
u8 c2 = b'\x41'
f64 pi = 3.14159
```

### 2.3 类型转换

转换采用“类型(表达式)”：

```yian
u64 ux = u64(dec)
i32 iy = i32(3.9)
```

---

## 3. 函数与返回值

## 3.1 普通函数

```yian
i8 add(i8 a, i8 b) {
    return a + b
}

i8 sub(i8 a, i8 b) {
    return a - b
}

i32 main() {
    i8 one = 1
    i8 two = 2
    i8 three = add(one, two)
    assert three == 3: "add failed"
    assert sub(three, one) == two: "sub failed"
    return 0
}
```

## 3.2 函数指针

这个例子在做两件事：

- 先把函数 `add` 的地址保存到函数指针变量 `fp`。
- 再像普通函数一样通过 `fp(...)` 发起调用。

它适合用于“把函数当参数传递”或“运行时选择不同实现”的场景。

```yian
i32 add(i32 a, i32 b) {
    return a + b
}

i32 main() {
    fn<i32(i32, i32)> fp = &add
    i32 x = fp(1, 2)
    assert x == 3: "function pointer failed"
    return 0
}
```

---

## 4. 控制流

## 4.1 if / elif / else

```yian
i32 sign(i32 x) {
    if x > 0 {
        return 1
    } elif x == 0 {
        return 0
    } else {
        return -1
    }
}
```

## 4.2 while、do-while、loop

```yian
i32 main() {
    i32 i = 0
    i32 sum = 0

    while i < 5 {
        sum += i
        i += 1
    }

    do {
        sum += 1
        i -= 1
    } while i > 0

    loop {
        if sum > 100 {
            break
        }
        sum += 10
    }

    return 0
}
```

## 4.3 for 与 for-in

```yian
i32 main() {
    i32 total = 0
    for i32 i = 0; i < 5; i += 1 {
        total += i
    }

    i32[5] arr = [1, 2, 3, 4, 5]
    for v in arr {
        total += v
    }

    assert total == 20: "loop failed"
    return 0
}
```

## 4.4 match（尤其适合 enum）

这个例子演示“返回值 + 错误分支”的经典模式：

- `safe_div` 返回 `DivResult<T>`，而不是直接 `panic`。
- 调用方用 `match` 显式处理成功和失败两种情况。

这类写法在标准库的 `Option<T>` / `Result<T, E>` 中非常常见。

```yian
enum DivResult<T> {
    Ok { T value }
    DivByZero
}

DivResult<i32> safe_div(i32 a, i32 b) {
    if b == 0 {
        return DivResult<i32>.DivByZero
    }
    return DivResult<i32>.Ok(a / b)
}

i32 main() {
    DivResult<i32> r = safe_div(10, 2)
    i32 out = 0

    match r {
        Ok as data {
            out = data.value
        }
        DivByZero {
            panic("division by zero")
        }
    }

    assert out == 5: "match failed"
    return 0
}
```

---

## 5. 结构体、枚举、方法

## 5.1 struct 与构造

```yian
struct Person {
    pub i32 age
    pub f64 height
}

i32 main() {
    Person p1 = Person(20, 175.5)
    Person p2 = Person(age = 25, height = 180.0)

    assert p1.age == 20: "p1.age"
    assert p2.height > 179.0 and p2.height < 181.0: "p2.height"
    return 0
}
```

## 5.2 impl 定义方法

这个例子展示“数据 + 行为”如何绑定在一起：

- `struct Counter` 存放状态（`value`）。
- `impl Counter` 定义操作状态的方法（`inc`、`get`）。

初学时可以把 `impl` 理解为“给类型挂函数”。

```yian
struct Counter {
    pub i32 value
}

impl Counter {
    pub inc(i32 x) {
        self.value += x
    }

    pub i32 get() {
        return self.value
    }
}

i32 main() {
    Counter c = Counter(value = 0)
    c.inc(3)
    c.inc(2)
    assert c.get() == 5: "method failed"
    return 0
}
```

## 5.3 trait + impl

这个例子展示“接口 + 实现”的关系：

- `trait Area` 声明能力（`area`）。
- `impl Area for Rect` 提供具体类型 `Rect` 的实现。

当你希望多个类型共享同一组行为时，优先考虑 trait。

```yian
trait Area {
    i32 area();
}

struct Rect {
    i32 w
    i32 h
}

impl Area for Rect {
    i32 area() {
        return self.w * self.h
    }
}

i32 main() {
    Rect r = Rect(w = 3, h = 4)
    assert r.area() == 12: "trait impl failed"
    return 0
}
```

## 5.4 self 与 Self 的使用方法

这是 yian 方法体系里最容易混淆的一组概念。

- `self`：当前对象实例（指针）。用于访问字段、调用实例方法。
- `Self`：当前 impl 对应的类型名。

可以把它们理解为：

- `self` 是“这个对象”。
- `Self` 是“这个对象所属的类型”。

```yian
struct Counter {
    pub i32 value
}

impl Counter {
    // 使用 Self：返回当前类型
    static pub Self new(i32 v) {
        return Self(value = v)
    }

    // 使用 self：访问当前实例字段
    pub inc(i32 step) {
        self.value += step
    }

    pub i32 get() {
        return self.value
    }
}

i32 main() {
    Counter c = Counter.new(10)
    c.inc(5)
    assert c.get() == 15: "self / Self basic"
    return 0
}
```

实践建议：

1. 写“构造器/工厂方法”时优先返回 `Self`。
2. 写“修改或读取对象状态”的方法时使用 `self`。
3. 在泛型 impl 中优先使用 `Self`，减少重复写长类型名。

---

## 6. 泛型

- 基础：定义泛型函数 / 结构体 / 枚举。
- 进阶：理解 `impl` 与泛型的交互，做到“一次编写，批量给类型加能力”。

## 6.1 泛型函数

通过泛型，同一份函数实现可以复用于不同类型。

- `add<i32>(10, 20)` 是显式指定类型参数。
- `add(1.5, 2.5)` 是让编译器自动推断类型参数。

```yian
T add<T>(T a, T b) {
    return a + b
}

i32 main() {
    i32 x = add<i32>(10, 20)
    f64 y = add(1.5, 2.5)
    assert x == 30: "generic int"
    assert y > 3.9 and y < 4.1: "generic float"
    return 0
}
```

## 6.2 泛型结构体

一个结构体模板可以组合不同类型。

- `Pair<T, U>` 有两个类型参数。
- 你可以写全类型参数，也可以在构造时让编译器推断。

```yian
struct Pair<T, U> {
    pub T first
    pub U second
}

i32 main() {
    Pair<i32, i64> p1 = Pair<i32, i64>(first = 1, second = 2)
    Pair<i32, i64> p2 = Pair(3, 4)
    assert p1.first == 1 and p1.second == 2: "pair p1"
    assert p2.first == 3 and p2.second == 4: "pair p2"
    return 0
}
```

## 6.3 泛型枚举

泛型枚举最典型用途是“状态 + 载荷”。

- 同一种状态机结构，载荷类型可以替换。
- 标准库 `Option<T>`、`Result<T, E>` 就是这个思路。

```yian
enum Boxed<T> {
    Empty
    Full { T value }
}

Boxed<i32> make_box(bool has_value) {
    if has_value {
        return Boxed<i32>.Full(42)
    }
    return Boxed<i32>.Empty
}

i32 main() {
    Boxed<i32> b = make_box(true)
    match b {
        Full as item {
            assert item.value == 42: "boxed value"
        }
        Empty {
            panic("unexpected empty")
        }
    }
    return 0
}
```

## 6.4 进阶：impl 与泛型的交互

先记一个核心规则：

- `impl<T> SomeType<T> { ... }`：给“某个泛型类型家族”加方法。
- `impl<T> Trait<...> for SomeType<T> { ... }`：给“某个泛型类型家族”实现 trait。

在这些模式里：

- `self` 代表当前实例（例如某个 `Pair<i32>`）。
- `Self` 代表当前具体类型（例如该方法所在的 `Pair<T>` 的实例化后类型）。

## 6.5 泛型 impl（类型内方法）

这是最常见的写法，标准库 `Vec<T>`、`Option<T>` 都是这一模式。

```yian
struct Pair<T> {
    T left
    T right
}

impl<T> Pair<T> {
    static pub Self new(T left, T right) {
        return Self(left, right)
    }
}

i32 main() {
    Pair<i32> p = Pair<i32>.new(1, 2)
    assert p.left == 1 and p.right == 2
    return 0
}
```

这段代码表达的是：`Pair<i32>`、`Pair<f64>`、`Pair<char>` 都自动拥有 `new` 方法。

再看一个同时使用 `self` 与 `Self` 的泛型例子：

```yian
struct Point<T> {
    pub T x
    pub T y
}

impl<T> Point<T> {
    static pub Self make(T x, T y) {
        return Self(x, y)
    }

    pub Self swap() {
        // self 读取当前实例；Self 构造同类型新值
        return Self(self.y, self.x)
    }
}

i32 main() {
    Point<i32> p = Point<i32>.make(1, 2)
    Point<i32> q = p.swap()
    assert q.x == 2 and q.y == 1: "generic self/Self"
    return 0
}
```

## 6.6 泛型 impl（实现 trait）

这类写法用于“给泛型类型接入运算符/接口能力”。

```yian
trait Summable<T> {
    T sum(T rhs);
}

struct Value<T> {
    pub T data
}

impl<T> Summable<Value<T>> for Value<T> {
    Value<T> sum(Value<T> rhs) {
        return Value<T>(data = self.data + rhs.data)
    }
}
```

这表示：只要 `T` 的 `+` 合法，那么 `Value<T>` 就获得了 `sum` 能力。

## 6.7 标准库泛型实例

下面这三类是最建议优先熟悉的泛型类型。

### 6.7.1 Option<T>

`Option<T>` 表示“值可能存在，也可能不存在”。

- `Some(x)`：有值。
- `None`：没值。

```yian
from std.core.option import Option

i32 main() {
    Option<i32> a = Option<i32>.Some(42)
    Option<i32> b = Option<i32>.None

    assert a.is_some(): "a should be Some"
    assert b.is_none(): "b should be None"

    assert a.unwrap_or(0) == 42
    assert b.unwrap_or(0) == 0

    Option<i32> t = a.take()
    assert t.unwrap_or(0) == 42
    assert a.is_none()

    return 0
}
```

### 6.7.2 Result<T, E>

`Result<T, E>` 表示“成功值或错误值”。

- `Ok(T)`：成功。
- `Err(E)`：失败。

```yian
from std.core.result import Result
from std.core.option import Option

typedef ErrorCode = u64

i32 main() {
    Result<i32, ErrorCode> ok_res = Result<i32, ErrorCode>.Ok(42)
    Result<i32, ErrorCode> err_res = Result<i32, ErrorCode>.Err(1)

    assert ok_res.is_ok()
    assert err_res.is_err()

    assert ok_res.unwrap_or(0) == 42
    assert err_res.unwrap_or(0) == 0

    Option<i32> v = ok_res.ok()
    Option<ErrorCode> e = err_res.err()
    assert v.is_some()
    assert e.is_some()

    return 0
}
```

### 6.7.3 Vec<T>

`Vec<T>` 是可增长数组，是最常用的泛型容器。

```yian
from std.core.vec import Vec
from std.core.option import Option

i32 main() {
    Vec<i32> v = Vec<i32>.new()
    v.push(10)
    v.push(20)
    v.push(30)

    assert v.len() == 3
    assert v[0] == 10

    Option<i32> maybe = v.get(100)
    assert maybe.is_none(): "out of bounds should be None"

    Option<i32> last = v.pop()
    assert last.unwrap_or(0) == 30

    return 0
}
```

---

## 7. 数组、元组、索引

## 7.1 数组

```yian
i32 main() {
    i32[5] arr = [10, 20, 30, 40, 50]
    assert arr[0] == 10: "arr[0]"
    arr[1] = 99
    assert arr[1] == 99: "arr[1]"
    return 0
}
```

## 7.2 多维数组访问

`yian` 支持多维索引写成 `arr[i, j, k]`。

```yian
i32 main() {
    i32[2, 2, 2] cube = [
        [[1, 2], [3, 4]],
        [[5, 6], [7, 8]]
    ]
    assert cube[1, 0, 1] == 6: "multi index"
    return 0
}
```

## 7.3 元组

```yian
i32 main() {
    (i32, bool) t = (42, true)
    assert t[0] == 42: "tuple first"
    assert t[1] == true: "tuple second"
    return 0
}
```

---

## 8. 指针、dyn、del

## 8.1 取地址与解引用

```yian
i32 main() {
    i32 x = 7
    i32* p = &x
    *p += 5
    assert x == 12: "pointer basic"
    return 0
}
```

## 8.2 动态分配

这个例子覆盖了三种 `dyn` 常见用法：

- `dyn i32`：只分配，不初始化。
- `dyn i32(100)`：分配并初始化。
- `dyn i32[3]`：分配定长数组。

最后用 `del` 释放内存。

```yian
i32 main() {
    i32* p1 = dyn i32
    *p1 = 42

    i32* p2 = dyn i32(100)

    i32* p3 = dyn i32[3]
    p3[0] = 10
    p3[1] = 20
    p3[2] = 30

    assert *p1 == 42: "dyn scalar"
    assert *p2 == 100: "dyn init"
    assert p3[2] == 30: "dyn array"

    del p1
    del p2
    del p3
    return 0
}
```

## 8.3 指针算术

这个例子演示“像迭代器一样移动指针”：

- `begin` 指向首元素。
- `end` 指向尾后位置。
- 循环里通过 `it += 1` 逐元素推进。

```yian
i32 main() {
    i32[4] arr = [1, 2, 3, 4]
    i32* begin = &arr[0]
    i32* end = begin + 4

    for i32* it = begin; it < end; it += 1 {
        *it += 10
    }

    assert arr[0] == 11 and arr[3] == 14: "pointer iter"
    return 0
}
```

---

## 9. 模块与导入

## 9.1 from import

```yian
from person import Person

i32 main() {
    Person p = Person.new(25, 175.5)
    p.walk()
    return 0
}
```

## 9.2 导入别名

```yian
from std.core.ops import Add as AddTrait
```

---

## 10. 运算符速用

常见运算：

- 算术：`+ - * / %`
- 赋值：`=` `+=` `-=` `*=` `/=` `%=`
- 位运算：`& | ^ ~ << >>`
- 比较：`== != < <= > >=`
- 逻辑：`and` `or` `not`
- 访问：`.` `[]`
- 指针：`&` `*`
- 区间：`..`

示例：

```yian
i32 main() {
    i32 a = 6
    i32 b = 4
    assert a + b == 10: "+"
    assert a - b == 2: "-"
    assert a * b == 24: "*"
    assert a / b == 1: "/"
    assert a % b == 2: "%"

    assert (a > b) and (a != b): "cmp"
    assert not (a < b): "not"

    i32 c = 0b110
    i32 d = 0b101
    assert (c & d) == 0b100: "&"
    assert (c | d) == 0b111: "|"
    assert (c ^ d) == 0b011: "^"
    return 0
}
```

---

## 11. 常见坑与建议

1. `main` 建议固定写成 `i32 main() { ... return 0 }`。
2. `for` 头部必须是 `init; cond; update` 结构。
3. 动态内存 `dyn` 创建后，按需 `del` 释放。
4. `assert 条件: "错误信息"` 非常适合作为开发期检查。

---

## 12. 一页速览

```text
程序入口
- i32 main() { ... return 0 }

声明
- i32 x = 1
- typedef Name = i64
- struct / enum / trait / impl

函数
- Ret f(T a, U b) { ... }
- fn<Ret(T, U)> fp = &f

控制流
- if / elif / else
- while / do while / loop
- for init; cond; update { ... }
- for item in arr { ... }
- match x { Case { ... } _ { ... } }

数据
- 数组: i32[5], [1,2,3]
- 元组: (i32, bool), (1, true)
- 泛型: Pair<i32, i64>

指针和内存
- i32* p = &x
- *p = 10
- i32* q = dyn i32[10]
- del q
```
