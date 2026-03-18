# `Result<T, E>` 类型

`Result<T, E>` 是一个枚举类型, 用于表示一个可能失败的操作的结果.

```an
pub enum Result<T, E> {
    Ok { T val }
    Err { E msg }
}
```

- `Ok { T val }`: 表示操作成功并返回一个值 `val`.
- `Err { E msg }`: 表示操作失败并返回一个错误信息 `msg`.

## 视图

提供以下方法来检查 `Result` 实例的状态:

- `is_ok() -> bool`: 如果 `Result` 是 `Ok`, 则返回 `true`.
- `is_err() -> bool`: 如果 `Result` 是 `Err`, 则返回 `true`.

## 操作

提供以下方法来操作 `Result` 实例:

- `unwrap_or(T default) -> T`: 如果 `Result` 是 `Ok`, 则返回包含的值 `val`; 如果 `Result` 是 `Err`, 则返回提供的 `default` 值.
- `unwrap_or_err(E default) -> E`: 如果 `Result` 是 `Err`, 则返回包含的错误 `msg`; 如果 `Result` 是 `Ok`, 则返回提供的 `default` 错误值.
- `ok() -> Option<T>`: 将 `Result` 转换为 `Option<T>`. 如果是 `Ok`, 返回包含值的 `Some`; 如果是 `Err`, 返回 `None`.
- `err() -> Option<E>`: 将 `Result` 转换为 `Option<E>`. 如果是 `Err`, 返回包含错误的 `Some`; 如果是 `Ok`, 返回 `None`.

