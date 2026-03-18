# convert 模块

此文档介绍了 `convert` 模块, 该模块提供了一些用于类型转换的实用函数和特性.

## `From<Src>`

`From<Src>` 是一个内置的泛型 `trait`, 用于表示可以从类型 `Src` 转换而来的类型. `From<Src>` trait 定义如下:

```text
trait From<Src> {
    static Self from(Src value);
}
```

- `from` 方法用于将类型 `Src` 的值转换为调用者类型的值

## `Into<Dst>`

`Into<Dst>` 是一个内置的泛型 `trait`, 用于表示可以转换为类型 `Dst` 的类型. `Into<Dst>` trait 定义如下:

```text
trait Into<Dst> {
    Dst into();
}
```

- `into` 方法用于将调用者类型的值转换为类型 `Dst` 的值
