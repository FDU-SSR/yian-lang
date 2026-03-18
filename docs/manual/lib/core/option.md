# `Option<T>` 类型

`Option<T>` 是一个枚举类型, 用于表示一个值可能存在也可能不存在.

```an
pub enum Option<T> {
    None
    Some { T val }
}
```

- `None`: 表示没有值.
- `Some { T val }`: 表示存在一个类型为 `T` 的值 `val`.

## 视图

提供以下方法来检查 `Option` 实例的状态:

- `is_some() -> bool`: 如果 `Option` 是 `Some` 值, 则返回 `true`.
- `is_none() -> bool`: 如果 `Option` 是 `None` 值, 则返回 `true`.

## 操作

提供以下方法来操作 `Option` 实例:

- `unwrap_or(T default) -> T`: 如果 `Option` 是 `Some`, 则返回包含的值 `val`; 如果 `Option` 是 `None`, 则返回提供的 `default` 值.
- `take() -> Option<T>`: 从 `Option` 中取出值, 将原来的 `Option` 变为 `None`. 如果原值为 `Some`, 返回包含该值的 `Some`; 如果原值为 `None`, 返回 `None`.
