# iter 模块

此文档介绍了标准库中的 `iter` 模块, 该模块包含与迭代器相关的类型和 trait.

## `Iterator<T>`

`Iterator<T>` 是一个内置的泛型 `trait`, 用于表示一个迭代器, 可以按顺序产生类型为 `T` 的值. `Iterator<T>` trait 定义如下:

```text
trait Iterator<T> {
    Option<T> next();
}
```

- `next` 方法返回迭代器的下一个值, 如果没有更多值可供迭代, 则返回 `Option<T>.None`

## `IntoIterator<Iter>`

`IntoIterator<Iter>` 是一个内置的泛型 `trait`, 用于表示可以生成迭代器的类型. `IntoIterator<Iter>` trait 定义如下:

```text
trait IntoIterator<Iter> {
    Iter into_iter();
}
```

- `into_iter` 方法返回一个迭代器实例, 迭代器实例的类型需要实现 `Iterator` trait
