# `Vec<T>` 类型

`Vec<T>` 类型表示一个动态数组, 可以存储任意类型的元素. 它在堆上分配内存, 并且可以动态调整大小以容纳更多的元素.

```text
struct Vec<T> {
    RawVec<T> raw_vec
    u64 len
}
```

- `raw_vec`: 一个底层的原始动态数组, 负责实际的内存分配和管理.
- `len`: 当前数组中存储的元素数量.

## 创建

提供以下方法来创建新的 `Vec` 实例:

- `Vec.new() -> Vec<T>`: 创建一个新的, 空的 `Vec` 实例.
- `Vec.with_capacity(u64 capacity) -> Vec<T>`: 创建一个具有指定初始容量的 `Vec` 实例.
- `Vec.from_slice(T[] slice) -> Vec<T>`: 从一个切片创建一个新的 `Vec` 实例, 切片中的元素将被复制.

## 访问

提供以下方法来访问 `Vec` 中的元素:

- `as_slice() -> T[]`: 返回一个切片, 代表 `Vec` 实例的内容.

## 修改

提供以下方法来修改 `Vec` 实例:

- `push(T value)`: 将一个元素追加到 `Vec` 的末尾.
- `extend(Vec<T> other)`: 将另一个 `Vec` 的所有元素移动到当前 `Vec` 中.
- `pop() -> Option<T>`: 移除并返回 `Vec` 的最后一个元素. 如果 `Vec` 为空, 则返回 `None`.
- `insert(u64 index, T value)`: 在指定索引处插入元素, 并将后面的元素向后移动. 如果索引大于当前长度, 则会 `panic`.
- `remove(u64 index) -> T`: 移除并返回指定索引处的元素, 并将后面的元素向前移动. 如果索引越界, 则会 `panic`.
- `swap_remove(u64 index) -> T`: 移除指定位置的元素, 并用最后一个元素填补该位置. 如果索引越界, 则会 `panic`.
- `extend_from_slice(T[] slice)`: 将一个切片中的元素追加到 `Vec` 的末尾.
- `truncate(u64 len)`: 将 `Vec` 的长度截断为 `len`. 如果 `len` 大于当前长度, 则不进行任何操作.
- `clear()`: 清空 `Vec` 的内容, 但保留其容量.
- `reserve(u64 additional)`: 为 `Vec` 预留额外的容量, 以容纳更多的元素.
- `shrink_to_fit()`: 将 `Vec` 的容量缩小到其当前长度, 以节省内存.

## 视图

提供以下方法来获取 `Vec` 实例的视图:

- `is_empty() -> bool`: 检查 `Vec` 是否为空.
- `capacity() -> u64`: 返回 `Vec` 的当前容量.

## 运算

`Vec` 类型实现了以下运算:

- `Clone`: 允许创建 `Vec` 实例的深拷贝.
- `Index<u64>`: 允许通过索引访问 `Vec` 中的元素.
- `PartialEq<Vec<T>>`: 允许比较两个 `Vec` 实例是否相等.
