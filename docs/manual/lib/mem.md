# mem 模块

此文档介绍了标准库 `mem` 模块中与内存管理相关的类型和 trait.

## `Clone`

`Clone` 是一个内置的泛型 `trait`, 用于表示可以被克隆的类型. `Clone` trait 定义如下:

```text
trait Clone {
    Self clone();
}
```

- `clone` 方法返回调用者的一个深拷贝

## `Copy`

`Copy` 是一个内置的泛型 `trait`, 用于表示可以被按值复制的类型. `Copy` trait 定义如下:

```text
trait Copy {}
```

- 实现了 `Copy` trait 的类型在赋值或传递时会被按值复制, 而不是移动
- 一个类型可以实现 `Copy` trait, 当且仅当它的所有字段类型都可以按值复制
