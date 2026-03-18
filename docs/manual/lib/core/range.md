# `Range<T>` 类型

`Range<T>` 是一个内置的泛型结构体, 用于表示一个范围值, 定义如下:

```text
struct Range<T> {
    pub T start
    pub T end
}
```

- `start`: 范围的起始值, 包含在范围内
- `end`: 范围的结束值, 不包含在范围内

除了使用通用的结构体构造语法创建 `Range<T>` 实例外, 还可以使用范围运算符(`..`)创建范围值. 下面三种方式是等价的:

```text
Range<i32> r1 = Range(10, 20)
Range<i32> r2 = Range(start=10, end=20)
Range<i32> r3 = 10..20
```

## 实现的 `trait`

`Range<T>` 实现了以下内置的 `trait`:

1. `Clone` (仅当 `T` 实现了 `Clone` 时)

```text
impl<T> Clone for Range<T> {
    Self clone() {
        return Self(
            start=self.start.clone(),
            end=self.end.clone()
        )
    }
}
```

1. `Copy` (仅当 `T` 实现了 `Copy` 时)

```text
impl<T> Copy for Range<T> {}
```

1. `PartialEq` (仅当 `T` 实现了 `PartialEq` 时)

```text
impl<T> PartialEq for Range<T> {
    bool eq(Range<T> other) {
        return self.start == other.start and self.end == other.end
    }
}
```

1. `Iterator` (`Range<T>` 可以直接当成迭代器使用, 前提是 `T` 支持加法和比较运算)

```text
impl<T> Iterator<T> for Range<T> {
    Option<T> next() {
        if self.start < self.end {
            T value = self.start
            self.start += 1
            return Option<T>.Some(value)
        } else {
            return Option<T>.None
        }
    }
}
```

1. `IntoIterator` (允许将 `Range<T>` 转换为迭代器)

```text
impl<T> IntoIterator<Range<T>> for Range<T> {
    Range<T> into_iter() {
        return *self
    }
}
```

## 相关机制

`Range<T>` 还有以下相关机制:

1. `Range<u64>` 可以作为数组/切片/指针类型的索引使用, 运算结果是一个新的切片, 包含原切片中位于指定范围内的元素

```text
impl<T, const u64 N> Index<Range<u64>, T[]> for T[N] {
    T[]* index(Range<u64> range) {
        // 返回一个新的切片, 包含原数组中位于指定范围内的元素
    }
}

impl<T> Index<Range<u64>, T[]> for T[] {
    T[]* index(Range<u64> range) {
        // 返回一个新的切片, 包含原切片中位于指定范围内的元素
    }
}

impl<T> Index<Range<u64>, T[]> for T* {
    T[]* index(Range<u64> range) {
        // 返回一个新的切片, 包含原指针指向的内存中位于指定范围内的元素
    }
}
```
