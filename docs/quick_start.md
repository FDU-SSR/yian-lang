# yian Quick Start (实例版)

这是一份面向新手的快速上手文档。

- 目标：30-45 分钟内跑通 13 个最小示例。
- 原则：先跑通，再扩展；每个示例都可以独立复制运行。
- 约定：示例优先用 `assert` 自检，不依赖手工看输出。

---

## 0. 先跑通：编译与运行

创建 `hello.an`：

```yian
from std.core.io import print

i32 main() {
    print("Hello, yian!\n")
    return 0
}
```

说明：这个程序从标准库导入 `print`，在 `main` 中输出一行文本，然后返回 `0` 表示正常结束。

编译并运行：

```bash
$ ./scripts/yian_compiler.py hello.an
$ ./tests/yian_workspace/bin/out
```

你现在可以做什么：确认环境和编译流程是通的。

常见坑：`main` 建议固定写成 `i32 main() { ... return 0 }`。

---

## 1. 基础数据：变量与字面量

目标：掌握 yian 的“类型在前，变量名在后”。

### 示例 1：基础类型

```yian
i32 main() {
    i32 a = 42
    f64 b = 3.14
    bool ok = true
    char ch = 'A'

    assert a == 42: "a"
    assert b > 3.1 and b < 3.2: "b"
    assert ok and ch == 'A': "ok/ch"
    return 0
}
```

说明：这个程序声明了 4 个基础类型变量，并用 `assert` 验证变量值与预期一致。

### 示例 2：常见数字字面量

```yian
i32 main() {
    i32 dec = 42
    i32 hex = 0x2A
    i32 oct = 0o52
    i32 bin = 0b101010
    u64 big = 1_000_000

    assert dec == hex and hex == oct and oct == bin: "same value"
    assert big == 1000000u64: "underscore literal"
    return 0
}
```

说明：这个程序演示同一个数值的不同进制写法，以及数字下划线分隔符的可读性写法。

---

## 2. 函数与返回值

目标：把逻辑抽成函数并复用。

### 示例 3：普通函数调用

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

    assert three == 3: "add"
    assert sub(three, one) == two: "sub"
    return 0
}
```

说明：这个程序定义 `add` 和 `sub` 两个函数，在 `main` 里调用它们并检查返回值是否正确。

---

## 3. 控制流

目标：写出分支和循环。

### 示例 4：if / else

```yian
i32 main() {
    i32 a = 3

    if a > 10 {
        panic("unexpected")
    } else {
        assert a == 3: "if-else"
    }

    return 0
}
```

说明：这个程序根据条件进入 `if` 或 `else` 分支，并在 `else` 中验证当前分支结果。

### 示例 5：for-in

```yian
i32 main() {
    i32 sum = 0
    i32[5] nums = [1, 2, 3, 4, 5]

    for n in nums {
        sum += n
    }

    assert sum == 15: "for-in"
    return 0
}
```

说明：这个程序使用 `for-in` 遍历数组，把每个元素累加到 `sum`，最后验证总和。

### 示例 6：loop + break

```yian
i32 main() {
    i32 i = 0
    i32 sum = 0

    loop {
        if i == 5 {
            break
        }
        sum += i
        i += 1
    }

    assert sum == 10: "loop"
    return 0
}
```

说明：这个程序使用 `loop` 做计数循环，在满足条件时 `break` 退出，并验证累计结果。

---

## 4. 数组与元组

目标：掌握最常见的复合数据。

### 示例 7：数组读写

```yian
i32 main() {
    i32[4] arr = [10, 20, 30, 40]
    arr[1] = 99

    assert arr[0] == 10: "arr[0]"
    assert arr[1] == 99: "arr[1]"
    return 0
}
```

说明：这个程序创建数组、修改指定索引位置的元素，并验证读写是否生效。

### 示例 8：元组访问

```yian
i32 main() {
    (i32, bool) t = (42, true)

    assert t[0] == 42: "tuple 0"
    assert t[1] == true: "tuple 1"
    return 0
}
```

说明：这个程序声明了一个二元组，并通过索引访问第 0 和第 1 个成员。

---

## 5. 自定义类型：struct / enum / match

目标：定义自己的数据模型。

### 示例 9：struct + impl

```yian
struct Pair {
    i32 a
    i32 b
}

impl Pair {
    pub static Self new(i32 a, i32 b) {
        return Self(a = a, b = b)
    }

    pub i32 sum() {
        return self.a + self.b
    }
}

i32 main() {
    Pair p = Pair.new(3, 4)
    assert p.sum() == 7: "pair.sum"
    return 0
}
```

说明：这个程序定义 `Pair` 结构体及其方法，通过 `new` 构造对象并调用 `sum` 计算字段之和。

### 示例 10：enum + match

```yian
enum Level {
    Low
    Mid
    High
}

i32 score(Level v) {
    match v {
        Low { return 1 }
        Mid { return 2 }
        High { return 3 }
    }
}

i32 main() {
    assert score(Level.Low) == 1: "low"
    assert score(Level.High) == 3: "high"
    return 0
}
```

说明：这个程序用 `enum` 表示状态，并在 `score` 函数中使用 `match` 按状态返回不同分值。

---

## 6. trait 与实现

目标：把“能力”抽象成接口。

### 示例 11：最小 trait

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
    assert r.area() == 12: "trait area"
    return 0
}
```

说明：这个程序定义 `Area` trait 并为 `Rect` 实现它，最后像普通方法一样调用 `area`。

---

## 7. 泛型入门

目标：一份类型定义，服务多种具体类型。

### 示例 12：泛型结构体

```yian
struct Value<T> {
    pub T data
}

struct Pair<T, U> {
    pub Value<T> first
    pub Value<U> second
}

i32 main() {
    Pair<i32, f64> p = Pair<i32, f64>(
        first = Value<i32>(data = 10),
        second = Value<f64>(data = 20.5)
    )

    assert p.first.data == 10: "generic first"
    assert p.second.data > 20.4 and p.second.data < 20.6: "generic second"
    return 0
}
```

说明：这个程序用泛型结构体同时存放不同类型数据，并验证泛型字段的读写。

---

## 8. 指针与动态内存

目标：理解 `&`、`*`、`dyn`、`del` 的最小闭环。

### 示例 13：取址、解引用、分配与释放

```yian
i32 main() {
    i32 x = 7
    i32* p = &x
    *p += 5
    assert x == 12: "pointer"

    i8* heap = dyn i8
    *heap = 42
    assert *heap == 42: "dyn"
    del heap

    return 0
}
```

说明：这个程序先演示取地址和解引用，再演示 `dyn` 分配堆内存并用 `del` 释放。

常见坑：`dyn` 创建的对象要在合适时机 `del`。

---

## 9. 模块导入

目标：拆分文件并通过 `from ... import ...` 复用代码。

目录：

```text
demo_import/
  adder.an
  main.an
```

`adder.an`：

```yian
pub T adder<T>(T a, T b) {
    return a + b
}
```

说明：这个模块导出一个泛型加法函数，供其他文件通过 `from ... import ...` 复用。

`main.an`：

```yian
from adder import adder

i32 main() {
    i32 res = adder(10, 20)
    assert res == 30: "import function"
    return 0
}
```

说明：这个程序从 `adder.an` 导入函数，在 `main` 中调用并验证导入函数的结果。

在 `demo_import` 目录编译：

```bash
$ ../scripts/yian_compiler.py main.an
$ ../tests/yian_workspace/bin/out
```

---

## 10. 下一步学习

- 深入泛型：`docs/manual/generic.md`
- 深入 impl/trait：`docs/manual/impl.md`
- 泛型语法细节：`docs/grammar/05.generics.md`

完成本页后，你已经具备继续阅读手册和跑测试样例的基础。
