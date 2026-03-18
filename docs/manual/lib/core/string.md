# `String` 类型

`String` 类型表示一个可变的、可增长的 UTF-8 编码字符串. 它在堆上分配内存, 并且可以动态调整大小以容纳更多的字符.

```text
struct String {
    Vec<u8> data
}
```

- `data`: 一个动态字节数组, 用于存储字符串的 UTF-8 编码数据.

## 创建

提供以下方法来创建新的 `String` 实例:

- `String.new() -> String`: 创建一个空的 `String` 实例.
- `String.with_capacity(u64 capacity) -> String`: 创建一个具有指定初始容量的 `String` 实例.
- `String.from_str(str s) -> String`: 从一个字符串切片创建一个新的 `String` 实例.

## 修改

提供以下方法来修改 `String` 实例:

- `push(char c)`: 将单个字符追加到字符串的末尾.
- `push_str(str s)`: 将一个字符串切片追加到字符串的末尾.
- `pop() -> Option<char>`: 从字符串的末尾移除并返回最后一个字符, 如果字符串为空则返回 `None`.
- `clear()`: 清空字符串的内容, 但保留其容量.
- `reserve(u64 additional)`: 为字符串预留额外的容量, 以容纳更多的字符.
- `shrink_to_fit()`: 将字符串的容量缩小到其当前长度, 以节省内存.

## 视图

提供以下方法来获取 `String` 实例的视图:

- `len() -> u64`: 返回字符串的长度, 以字节为单位.
- `is_empty() -> bool`: 检查字符串是否为空.
- `capacity() -> u64`: 返回字符串的当前容量, 以字节为单位.
- `as_str() -> str`: 返回一个字符串切片, 代表 `String` 实例的内容.
- `as_bytes() -> u8[]`: 返回一个字节数组切片, 代表 `String` 实例的内容.

## 运算

`String` 类型实现了以下运算:

- `Clone`: 允许创建 `String` 实例的深拷贝.
- `Deref<str>`: 允许将 `String` 实例解引用为 `str` 类型.
- `Add<str, String>`: 允许将 `str` 类型追加到 `String` 实例, 返回一个新的 `String` 实例.
- `Add<String, String>`: 允许将两个 `String` 实例相加, 返回一个新的 `String` 实例.
- `PartialEq<str>`: 允许比较 `String` 实例与 `str` 类型的相等性.
- `PartialEq<String>`: 允许比较两个 `String` 实例的相等性.
